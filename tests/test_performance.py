"""Adapter units, physical invariants, and real bundled SimSS integration."""
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import threading
import subprocess
import unittest
from unittest.mock import patch

import numpy as np

from perovskite_studio.physics import example
from perovskite_studio.performance import (DEFAULT_SETTINGS, ENGINE_DIR, SimulationCancelled,
                         calculate_metrics, import_layers, run_simulation,
                         validate_device, write_inputs, discretize_layers, simulation_mesh)


def values(path):
    result = {}
    for line in Path(path).read_text(encoding='ascii').splitlines():
        if '=' in line and not line.startswith('*'):
            key, value = line.split('*', 1)[0].split('=', 1)
            result[key.strip()] = value.strip()
    return result


class PerformanceAdapter(unittest.TestCase):
    def setUp(self):
        self.layers = import_layers(example())
        self.settings = dict(DEFAULT_SETTINGS)

    def test_si_conversion_and_srh_lifetimes(self):
        with tempfile.TemporaryDirectory() as folder:
            write_inputs(folder, self.layers, self.settings)
            a = self.layers[1]
            p = values(Path(folder)/'layer_2.txt')
            self.assertAlmostEqual(float(p['L']), 500e-9)
            self.assertEqual(float(p['N_c']), a['Nc']*1e6)
            self.assertAlmostEqual(float(p['mu_n']), a['mun']*1e-4)
            self.assertAlmostEqual(float(p['E_c']), a['chi'])
            self.assertAlmostEqual(float(p['E_v']), a['chi']+a['Eg'])
            self.assertAlmostEqual(1/(float(p['C_n_bulk'])*float(p['N_t_bulk'])), a['taun'])
            self.assertAlmostEqual(1/(float(p['C_p_bulk'])*float(p['N_t_bulk'])), a['taup'])
            self.assertEqual(float(p['G_ehp']), self.settings['G']*1e6)
            self.assertEqual(float(p['N_t_int']), 0)
            self.assertEqual(float(p['N_anion']), 0)
            self.assertEqual(float(p['bulkTrapType']), 0)
            setup = values(Path(folder)/'simulation_setup.txt')
            self.assertEqual(setup['genProfile'], 'none')
            self.assertEqual(setup['failureMode'], '0')
            self.assertEqual(setup['leftElec'], '1')

    def test_no_silent_parameter_approximation(self):
        self.layers[1]['Eg_mode'] = '线性'
        self.layers[1]['Eg_end'] = 1.8
        validate_device(self.layers, self.settings)
        parts = discretize_layers(self.layers, 16)
        absorber = [a for a in parts if a['parent_index'] == 1]
        self.assertEqual(len(absorber), 16)
        self.assertAlmostEqual(absorber[0]['Eg'], 1.55)
        self.assertAlmostEqual(absorber[-1]['Eg'], 1.8)
        self.assertAlmostEqual(sum(a['thickness'] for a in absorber), 500)
        self.layers = import_layers(example())
        self.layers[1]['Nv'] *= 2
        with self.assertRaisesRegex(ValueError, 'Nc = Nv'):
            validate_device(self.layers, self.settings)

    def test_gradient_sampling_contacts_and_virtual_interface_mobility(self):
        a = self.layers[1]
        a.update(Eg_mode='线性', Eg_end=1.7, Nd_mode='对数', Nd=1e13, Nd_end=1e16,
                 Na_mode='线性', Na_end=1e14, affinity_mode='独立端点', chi_end=4.)
        pieces, points, spacing = simulation_mesh(self.layers, self.settings)
        graded = [p for p in pieces if p['parent_index'] == 1]
        self.assertAlmostEqual(graded[0]['Nd']/a['Nd'], 1.)
        self.assertAlmostEqual(graded[-1]['Nd']/a['Nd_end'], 1.)
        self.assertEqual(graded[-1]['chi'], 4.)
        self.assertEqual(graded[-1]['Na'], 1e14)
        self.assertAlmostEqual(sum(p['thickness'] for p in pieces), 550)
        with tempfile.TemporaryDirectory() as folder:
            write_inputs(folder, self.layers, self.settings)
            for i, p in enumerate(pieces[:-1]):
                params = values(Path(folder)/f'layer_{i+1}.txt')
                if p['virtual_right']:
                    vt = 1.3807e-23*self.settings['T']/1.6022e-19
                    mu = spacing[i]*float(params['nu_int_n'])/vt
                    self.assertAlmostEqual(mu, p['mun']*1e-4)
                else:
                    self.assertEqual(float(params['nu_int_n']), p['transfer_n']*.01)
        self.layers[-1].update(affinity_mode='独立端点', chi_end=4.5)
        with self.assertRaisesRegex(ValueError, '右电极'):
            validate_device(self.layers, self.settings)

    def test_all_graded_layers_fit_mesh_and_uniform_limit_does_not_split(self):
        for a in self.layers:
            a.update(Nd_mode='线性', Nd_end=a['Nd']+1e14)
        pieces, points, _ = simulation_mesh(self.layers, self.settings)
        self.assertEqual(len(pieces), 48)
        self.assertLessEqual(points, 2000)
        a = import_layers(example())[1]
        a.update(Eg_mode='线性', Eg_end=a['Eg'], Nd_mode='线性', Nd_end=a['Nd'])
        self.assertEqual(len(discretize_layers([a], 16)), 1)

    def test_arbitrary_layer_count_and_trap_off(self):
        self.layers = self.layers[1:2]
        self.layers[0]['Nt'] = 0
        self.settings.update(left=5.1, right=4.1, rs=3, rsh=2e4)
        with tempfile.TemporaryDirectory() as folder:
            write_inputs(folder, self.layers, self.settings)
            setup = values(Path(folder)/'simulation_setup.txt')
            self.assertNotIn('l2', setup)
            self.assertAlmostEqual(float(setup['R_series']), 3e-4)
            self.assertEqual(float(setup['R_shunt']), 2)
            self.assertEqual(float(values(Path(folder)/'layer_1.txt')['C_n_bulk']), 0)

    def test_validation_of_ranges_and_nonfinite(self):
        for key, value in [('G', -1), ('pin', 0), ('vstep', 0), ('vmin', .1),
                           ('points', 20.5), ('timeout', float('nan')), ('rsh', 0)]:
            s = dict(self.settings, **{key: value})
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_device(self.layers, s)

    def test_metrics_use_exact_piecewise_power_and_do_not_extrapolate(self):
        _, _, m, warnings = calculate_metrics([-.1, .2, .7, 1.2], [22, 16, 6, -4], 100)
        self.assertAlmostEqual(m['Jsc'], 20)
        self.assertAlmostEqual(m['Voc'], 1)
        self.assertAlmostEqual(m['Vmp'], .5)
        self.assertAlmostEqual(m['PCE'], 5)
        self.assertAlmostEqual(m['FF'], 25)
        self.assertFalse(warnings)
        _, _, m, warnings = calculate_metrics([0, .2, .4], [20, 19, 18], 100)
        self.assertIsNone(m['Voc'])
        self.assertIsNone(m['PCE'])
        self.assertIn('开路点', warnings[0])
        _, _, m, _ = calculate_metrics([0, .5, 1], [0, -10, -20], 100)
        self.assertIsNone(m['PCE'])


@unittest.skipUnless(sys.platform == 'win32' and (ENGINE_DIR/'simss.exe').exists(), 'Windows SimSS required')
class RealSimSS(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.layers = import_layers(example())
        cls.settings = dict(DEFAULT_SETTINGS, vmax=1.25, vstep=.025, points=200)
        cls.reference = run_simulation(cls.layers, cls.settings)

    def test_photocurrent_bound_and_current_conservation(self):
        r = self.reference
        self.assertGreater(r['metrics']['Jsc'], 0)
        self.assertLessEqual(r['metrics']['Jsc'], 1.01*r['generated_current'])
        self.assertGreater(r['metrics']['Voc'], 0)
        self.assertLess(r['metrics']['Voc'], self.layers[1]['Eg'])
        self.assertTrue(0 < r['metrics']['FF'] < 100)
        self.assertLess(r['relative_current_error'], .001)

    def test_mesh_stability(self):
        refined = run_simulation(self.layers, dict(self.settings, points=400))
        for key in ('Jsc', 'Voc', 'PCE'):
            self.assertLess(abs(refined['metrics'][key]/self.reference['metrics'][key]-1), .02, key)

    def test_all_layer_grading_refinement_and_bulk_mobility(self):
        layers = deepcopy(self.layers)
        for a in layers:
            a.update(Nd_mode='线性', Nd_end=a['Nd']+1e14)
        layers[1].update(Eg_mode='线性', Eg_end=1.7, affinity_mode='独立端点', chi_end=4.)
        coarse = run_simulation(layers, dict(self.settings, grading_slices=8))
        fine = run_simulation(layers, dict(self.settings, grading_slices=16))
        for key in ('Jsc', 'Voc', 'PCE'):
            self.assertLess(abs(fine['metrics'][key]/coarse['metrics'][key]-1), .02, key)
        self.assertLess(fine['relative_current_error'], .001)
        # Inspect native solver output, independently of the Python mesh helper.
        with tempfile.TemporaryDirectory() as folder:
            write_inputs(folder, layers, dict(self.settings, vmin=0, vmax=.04, vstep=.02))
            setup = Path(folder)/'simulation_setup.txt'
            setup.write_text(setup.read_text().replace('varFile = none', 'varFile = Var.dat'))
            process = subprocess.run([str(ENGINE_DIR/'simss.exe'), 'simulation_setup.txt'], cwd=folder,
                                     capture_output=True, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(process.returncode, 0, process.stdout.decode(errors='replace'))
            data = np.genfromtxt(Path(folder)/'Var.dat', names=True)
            # Only the two real material interfaces may override bulk mobility.
            self.assertEqual(np.count_nonzero(~np.isclose(data['mun'], 1e-3, rtol=1e-9, atol=0)), 6)

    def test_forced_homogeneous_sublayers_do_not_add_transport_barriers(self):
        with patch('perovskite_studio.performance.is_graded', return_value=True):
            split = run_simulation(self.layers, dict(self.settings, grading_slices=8))
        for key in ('Jsc', 'Voc', 'PCE'):
            self.assertLess(abs(split['metrics'][key]/self.reference['metrics'][key]-1), .01, key)

    def test_dark_equilibrium_and_lifetime_sensitivity(self):
        dark = run_simulation(self.layers, dict(self.settings, G=0, vmin=0, vmax=.1, vstep=.05))
        self.assertLess(abs(dark['metrics']['Jsc']), 1e-6)
        self.assertIsNone(dark['metrics']['PCE'])
        shorter = deepcopy(self.layers)
        shorter[1].update(taun=1e-8, taup=1e-8)
        r = run_simulation(shorter, self.settings)
        self.assertLess(r['metrics']['Voc'], self.reference['metrics']['Voc'])
        self.assertLess(r['metrics']['PCE'], self.reference['metrics']['PCE'])

    def test_cancel_before_launch(self):
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(SimulationCancelled):
            run_simulation(self.layers, self.settings, cancel)


if __name__ == '__main__':
    unittest.main()
