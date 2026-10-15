"""Failure evidence must belong to the failed native attempt, survive cleanup,
and identify the EQE wavelength rather than mislabel a runtime crash as inputs.
"""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import zipfile

from performance import (DEFAULT_SETTINGS, _run_engine, run_eqe, import_layers,
                         _engine_executable, EngineFailure, run_simulation)
from physics import example


class EngineDiagnostics(unittest.TestCase):
    def test_crash_discards_stale_outputs_and_preserves_inputs_after_cleanup(self):
        with tempfile.TemporaryDirectory() as saved_root:
            saved_root = Path(saved_root)
            with tempfile.TemporaryDirectory() as run_folder:
                folder = Path(run_folder)
                for name in ('log.txt', 'JV.dat', 'OpticalProfile.dat'):
                    (folder/name).write_text('PREVIOUS SUCCESS', encoding='utf-8')
                (folder/'simulation_setup.txt').write_text('current inputs', encoding='ascii')
                (folder/'spectrum.txt').write_text('current probe spectrum', encoding='ascii')
                process = Mock(returncode=226)
                process.communicate.return_value = (b'', b'Runtime error 226 at $123456\n')
                process.poll.return_value = 226
                def launch(*args, **kwargs):
                    for name in ('log.txt', 'JV.dat', 'OpticalProfile.dat'):
                        self.assertFalse((folder/name).exists())
                    return process
                with patch('performance.subprocess.Popen', side_effect=launch), \
                     patch('performance._diagnostics_directories', return_value=(saved_root,)):
                    with self.assertRaises(RuntimeError) as raised:
                        _run_engine(folder, DEFAULT_SETTINGS, context='EQE 第 3/6 个波长（500 nm）')
                message = str(raised.exception)
                self.assertIn('500 nm', message)
                self.assertIn('226', message)
                self.assertIn('$123456', message)
                self.assertNotIn('PREVIOUS SUCCESS', message)
            archives = list(saved_root.glob('*.zip'))
            self.assertEqual(len(archives), 3)  # bounded retries, every failure retained
            with zipfile.ZipFile(archives[0]) as archive:
                self.assertEqual(archive.read('simulation_setup.txt'), b'current inputs')
                self.assertEqual(archive.read('spectrum.txt'), b'current probe spectrum')
                self.assertIn(b'$123456', archive.read('stderr.txt'))
                self.assertNotIn('JV.dat', archive.namelist())
                metadata = json.loads(archive.read('diagnostics.json'))
                self.assertEqual(metadata['returncode'], 226)
                self.assertIn('500 nm', metadata['operation'])

    def test_current_log_stdout_and_stderr_are_all_kept(self):
        with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryDirectory() as saved_root:
            process = Mock(returncode=94)
            def communicate(**kwargs):
                (Path(folder)/'log.txt').write_text('current solver log', encoding='utf-8')
                return b'current console', b'current error'
            process.communicate.side_effect = communicate
            process.poll.return_value = 94
            with patch('performance.subprocess.Popen', return_value=process), \
                 patch('performance._diagnostics_directories', return_value=(Path(saved_root),)):
                with self.assertRaises(RuntimeError) as raised:
                    _run_engine(folder, DEFAULT_SETTINGS)
            message = str(raised.exception)
            for text in ('current solver log', 'current console', 'current error'):
                self.assertIn(text, message)
            self.assertNotIn('226 是', message)
            process.communicate.assert_called_once()  # ordinary convergence failure is not retried

    def test_old_jv_cannot_validate_an_empty_successful_attempt(self):
        with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryDirectory() as saved_root:
            (Path(folder)/'JV.dat').write_text('Vext Jext errJ\n0 1 0\n', encoding='ascii')
            process = Mock(returncode=0)
            process.communicate.return_value = (b'', b'')
            process.poll.return_value = 0
            with patch('performance.subprocess.Popen', return_value=process), \
                 patch('performance._diagnostics_directories', return_value=(Path(saved_root),)):
                with self.assertRaisesRegex(RuntimeError, '未生成本次 J–V'):
                    _run_engine(folder, DEFAULT_SETTINGS)

    def test_eqe_background_and_wavelength_failures_have_correct_context(self):
        layers = import_layers(example())
        settings = dict(DEFAULT_SETTINGS, optics=dict(mode='spectrum'), eqe=dict(step_nm=100))
        with patch('performance._run_engine', side_effect=RuntimeError('baseline crash')) as engine:
            with self.assertRaisesRegex(RuntimeError, 'baseline crash'):
                run_eqe(layers, settings)
            self.assertEqual(engine.call_args.kwargs['context'], 'EQE 背景计算')
        def fake_engine(folder, settings, cancel, started, context):
            if context == 'EQE 背景计算':
                import numpy as np
                points = settings['points']+2
                np.savetxt(Path(folder)/'OpticalProfile.dat',
                           np.column_stack([np.linspace(0, 1, points), np.zeros(points)]),
                           header='x G_ehp', comments='')
                return np.array([(0., 0., 0.)], dtype=[('Vext', float), ('Jext', float), ('errJ', float)]), ''
            raise RuntimeError(context)
        with patch('performance._run_engine', side_effect=fake_engine):
            with self.assertRaisesRegex(RuntimeError, 'EQE 第 1/6 个波长（300 nm）'):
                run_eqe(layers, settings)

    def test_226_recovers_to_real_jv_with_identical_inputs_and_visible_warning(self):
        import subprocess
        import numpy as np
        layers = import_layers(example())
        settings = dict(DEFAULT_SETTINGS, optics=dict(mode='manual'), vmax=1.24)
        reference = run_simulation(layers, settings)
        native_popen = subprocess.Popen
        calls, inputs = [], []
        crashed = Mock(returncode=226)
        crashed.communicate.return_value = (b'', b'Runtime error 226 at $111\n')
        crashed.poll.return_value = 226
        def launch(*args, **kwargs):
            calls.append(args)
            inputs.append((Path(kwargs['cwd'])/'simulation_setup.txt').read_bytes())
            return crashed if len(calls) == 1 else native_popen(*args, **kwargs)
        with tempfile.TemporaryDirectory() as saved_root:
            with patch('performance.subprocess.Popen', side_effect=launch), \
                 patch('performance._diagnostics_directories', return_value=(Path(saved_root),)):
                result = run_simulation(layers, settings)
            self.assertEqual(len(calls), 2)
            self.assertEqual(inputs[0], inputs[1])
            np.testing.assert_allclose(result['current'], reference['current'], rtol=1e-12)
            self.assertEqual(len(result['runtime_recoveries']), 1)
            self.assertTrue(any('226' in warning and '重新启动' in warning for warning in result['warnings']))
            self.assertTrue(Path(result['runtime_recoveries'][0]['diagnostic_path']).is_file())

    def test_cancel_during_retry_does_not_launch_another_attempt(self):
        cancelled = threading.Event()
        def fail(*args, **kwargs):
            cancelled.set()
            raise EngineFailure('test 226', returncode=226)
        from performance import SimulationCancelled
        with patch('performance._run_engine_once', side_effect=fail) as engine:
            with self.assertRaises(SimulationCancelled):
                _run_engine('.', DEFAULT_SETTINGS, cancel=cancelled)
            engine.assert_called_once()

    def test_frozen_engine_cache_repairs_corruption_and_separates_versions(self):
        import sys
        import os
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            source = root/'source'
            source.mkdir()
            filename = 'simss.exe' if sys.platform == 'win32' else 'simss'
            (source/filename).write_bytes(b'engine version one')
            with patch('performance.ENGINE_DIR', source), \
                 patch.object(sys, 'frozen', True, create=True), \
                 patch.dict(os.environ, LOCALAPPDATA=str(root/'cache')):
                first = _engine_executable()
                self.assertEqual(first.read_bytes(), b'engine version one')
                if sys.platform == 'win32':
                    self.assertNotEqual(first, source/filename)
                    first.write_bytes(b'corrupted')
                    self.assertEqual(_engine_executable().read_bytes(), b'engine version one')
                    (source/filename).write_bytes(b'engine version two')
                    second = _engine_executable()
                    self.assertNotEqual(first, second)
                    self.assertEqual(second.read_bytes(), b'engine version two')


if __name__ == '__main__':
    unittest.main()
