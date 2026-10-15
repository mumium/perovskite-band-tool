"""Native interface SRH, optical-edge coupling, loss accounting and EQE."""
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

import numpy as np

from physics import example
from optics import effective_nk, nk_table, optical_model, HC_EV_NM, get_optics
from performance import (DEFAULT_SETTINGS, ENGINE_DIR, SimulationCancelled, import_layers,
                         write_inputs, validate_device, run_simulation, run_eqe, loss_at_voltage,
                         _reverse_for_illumination, _rewrite_setup, _run_engine, integrate)
from performance import _eqe_short_circuit
from test_performance import values


class AnalysisInputs(unittest.TestCase):
    def setUp(self):
        self.layers = import_layers(example())
        self.settings = dict(DEFAULT_SETTINGS, vmax=1.24, optics=dict(mode='spectrum'))

    def test_interface_units_real_boundaries_and_energy_validation(self):
        self.layers[0]['interface'].update(Nt=2e11, Cn=3e-8, Cp=7e-9, trap_type=-1)
        self.layers[1].update(Eg_mode='线性', Eg_end=1.7)
        self.layers[1]['interface'].update(Nt=1e12)
        with tempfile.TemporaryDirectory() as folder:
            mesh = write_inputs(folder, self.layers, self.settings)
            for i, piece in enumerate(mesh['pieces']):
                p = values(Path(folder)/f'layer_{i+1}.txt')
                if piece['virtual_right'] or i == mesh['sublayers']-1:
                    self.assertEqual(float(p['N_t_int']), 0)
            first = values(Path(folder)/'layer_1.txt')
            self.assertEqual(float(first['N_t_int']), 2e15)
            self.assertAlmostEqual(float(first['C_n_int']), 3e-14)
            self.assertEqual(float(first['intTrapType']), -1)
            self.assertAlmostEqual(float(first['E_t_int']), 4.45)
        self.layers[0]['interface'].update(auto_energy=False, Et=5.2)
        with self.assertRaisesRegex(ValueError, '共同禁带'):
            validate_device(self.layers, self.settings)
        self.layers[0]['interface'].update(Nt=0)
        validate_device(self.layers, self.settings)  # disabled traps ignore unused depth

    def test_optical_edge_fixed_compatibility_and_local_gradients(self):
        a = deepcopy(self.layers[1])
        raw = nk_table(a['optical_nk'])
        a.pop('optical_model')
        self.assertEqual(optical_model(a)['mode'], 'fixed')
        np.testing.assert_allclose(effective_nk(a, raw[:, 0]), raw)
        a['optical_model'] = dict(mode='edge', reference_gap=1.55)
        a['Eg'] = 1.8
        effective = effective_nk(a, np.array([300., 500., 700., 800.]))
        self.assertGreater(effective[1, 2], 0)
        np.testing.assert_array_equal(effective[2:, 2], 0)
        self.assertAlmostEqual(effective[0, 2], raw[0, 2])  # UV anchor
        self.layers[1].update(Eg_mode='线性', Eg_end=1.8)
        with tempfile.TemporaryDirectory() as folder:
            mesh = write_inputs(folder, self.layers, self.settings)
            snapshots = [a for a in mesh['optical']['nk_snapshots'] if a['parent_index'] == 1]
            self.assertEqual(snapshots[0]['local_gap_eV'], 1.55)
            self.assertEqual(snapshots[-1]['local_gap_eV'], 1.8)
            self.assertFalse(np.allclose(snapshots[0]['effective_values'], snapshots[-1]['effective_values']))

    def test_requested_defaults_and_old_interface_fallback(self):
        o = get_optics({})
        self.assertEqual(o['substrate']['name'], 'ITO')
        self.assertEqual(o['back']['name'], 'Ag')
        self.assertEqual(o['tco_thickness'], 200)
        for a in self.layers:
            a.pop('interface')
            a.pop('optical_model')
        validate_device(self.layers, self.settings)

    def test_eqe_external_short_circuit_with_nonzero_series_resistance(self):
        # An independent linear device has Jext = J0 + conductance * Vint.
        # Its terminal short circuit is analytic, and differs from Vint = 0.
        settings = dict(self.settings, rs=.3, rsh=4e4)
        rs_si, j0, conductance = settings['rs']*1e-4, -200., 2e4
        def linear_device(folder, settings, cancel, started, context):
            voltage = float(values(Path(folder)/'simulation_setup.txt')['Vmin'])
            current = j0+conductance*voltage
            data = np.array([(voltage+rs_si*current, current, 0.)],
                            dtype=[('Vext', float), ('Jext', float), ('errJ', float)])
            return data, ''
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder)/'simulation_setup.txt').write_text('Vmin = 0\nVmax = 0\n', encoding='ascii')
            with patch('performance._run_engine', side_effect=linear_device):
                data, internal = _eqe_short_circuit(folder, settings, None, 0., 'test EQE')
            self.assertAlmostEqual(internal, -rs_si*j0/(1+rs_si*conductance), places=10)
            self.assertAlmostEqual(float(data['Jext'][0]), j0/(1+rs_si*conductance), places=8)
            self.assertLess(abs(float(data['Vext'][0])), 1e-9)
            self.assertEqual(settings['rs'], .3)
            self.assertEqual(settings['rsh'], 4e4)


@unittest.skipUnless(sys.platform == 'win32' and (ENGINE_DIR/'simss.exe').exists(), 'Windows SimSS required')
class NativeAnalysis(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.layers = import_layers(example())
        cls.settings = dict(DEFAULT_SETTINGS, vmax=1.24, optics=dict(mode='spectrum'))
        cls.reference = run_simulation(cls.layers, cls.settings)

    def test_interface_recombination_voltage_and_mirrored_graded_device(self):
        layers = deepcopy(self.layers)
        layers[0]['interface'].update(Nt=1e12, trap_type=0)
        layers[1].update(Eg_mode='线性', Eg_end=1.6)
        r = run_simulation(layers, self.settings)
        self.assertLess(r['metrics']['Voc'], self.reference['metrics']['Voc']-.1)
        self.assertGreater(loss_at_voltage(r, 0)['interface'], .01)
        self.assertEqual(len(r['losses']['interfaces']), 2)
        mirror_layers, mirror_settings = _reverse_for_illumination(layers, self.settings)
        mirror_settings['optics']['illumination'] = 'right'
        mirrored = run_simulation(mirror_layers, mirror_settings)
        np.testing.assert_allclose(mirrored['current'], r['current'], rtol=1e-6, atol=1e-5)
        np.testing.assert_allclose(mirrored['losses']['interface'], r['losses']['interface'], rtol=1e-6, atol=1e-5)
        charged_layers = deepcopy(self.layers)
        charged_layers[0]['interface'].update(Nt=1e9, trap_type=1)
        charged = run_simulation(charged_layers, dict(self.settings, timeout=10))
        self.assertTrue(np.isfinite(charged['metrics']['Voc']))

    def test_bandgap_changes_generation_only_when_linked(self):
        fixed = deepcopy(self.layers)
        fixed[1]['optical_model']['mode'] = 'fixed'
        before = run_simulation(fixed, self.settings)
        fixed[1]['Eg'] = 1.8
        after = run_simulation(fixed, self.settings)
        np.testing.assert_allclose(before['optics']['G_cm3_s'], after['optics']['G_cm3_s'], rtol=1e-12)
        fixed[1]['optical_model']['mode'] = 'edge'
        linked = run_simulation(fixed, self.settings)
        self.assertLess(linked['generated_current'], before['generated_current']*.85)
        self.assertLess(linked['metrics']['Jsc'], before['metrics']['Jsc']*.85)

    def test_loss_budget_short_circuit_open_circuit_and_series_resistance(self):
        r = self.reference
        sc = loss_at_voltage(r, 0)
        self.assertGreater(sc['photo'], sc['collected'])
        self.assertLess(abs(sc['residual'])/sc['photo'], .02)
        oc = loss_at_voltage(r, r['metrics']['Voc'])
        self.assertAlmostEqual(oc['collected'], 0., places=6)
        self.assertGreater(oc['bulk']+oc['direct'], sc['bulk']+sc['direct'])
        resistive = run_simulation(self.layers, dict(self.settings, rs=2))
        mpp = loss_at_voltage(resistive, resistive['metrics']['Vmp'])
        self.assertGreater(mpp['series_power'], 0)
        self.assertLess(resistive['metrics']['PCE'], r['metrics']['PCE'])
        with self.assertRaisesRegex(ValueError, '外推'):
            loss_at_voltage(r, 20)

    def test_eqe_wavelength_response_probe_linearity_and_cutoff(self):
        settings = dict(self.settings, eqe=dict(step_nm=100))
        e = run_eqe(self.layers, settings)
        self.assertTrue(all(v > .1 for v in e['EQE'][:-1]))
        np.testing.assert_allclose(e['EQE'], e['active_absorptance'], rtol=.002, atol=1e-4)
        half = run_eqe(self.layers, dict(settings, eqe=dict(step_nm=100, photon_flux_cm2_s=5e13)))
        np.testing.assert_allclose(e['EQE'], half['EQE'], rtol=.002, atol=1e-4)
        layers = deepcopy(self.layers)
        layers[1]['Eg'] = 1.8
        high_gap = run_eqe(layers, settings)
        self.assertGreater(high_gap['EQE'][2], .1)
        self.assertAlmostEqual(high_gap['EQE'][-2], 0., places=10)  # 700 nm below bandgap; root-solve roundoff
        self.assertLess(max(e['numerical_error_estimate']), .01)

    def test_dark_eqe_matches_independent_single_wavelength_native_run(self):
        settings = dict(self.settings, rs=0., eqe=dict(step_nm=100, background_scale=0))
        e = run_eqe(self.layers, settings)
        lam = 500e-9
        flux = e['eqe_settings']['photon_flux_cm2_s']*1e4
        with tempfile.TemporaryDirectory() as folder:
            write_inputs(folder, self.layers, settings)
            irradiance = flux*6.62607015e-34*299792458/lam/1e-9
            np.savetxt(Path(folder)/'spectrum.txt', [[300e-9, irradiance], [800e-9, irradiance]],
                       header='lambda I', comments='')
            _rewrite_setup(folder, dict(Vmin=0, Vmax=0, lambda_min=lam, lambda_max=lam))
            data, _ = _run_engine(folder, settings)
        independent = (-data['Jext'][0]/10-e['baseline_Jsc'])/(1.602176634e-19*flux/10)
        self.assertAlmostEqual(e['EQE'][2], independent, places=5)

    def test_eqe_transparency_and_mid_run_cancellation(self):
        layers = deepcopy(self.layers)
        for a in layers:
            a['optical_nk'] = dict(kind='builtin', name='Air')
        settings = dict(self.settings, eqe=dict(step_nm=100))
        e = run_eqe(layers, settings)
        np.testing.assert_array_equal(e['EQE'], np.zeros(6))
        cancel = threading.Event()
        def stop_after_first(i, n):
            if i == 1:
                cancel.set()
        with self.assertRaises(SimulationCancelled):
            run_eqe(self.layers, settings, cancel, stop_after_first)


if __name__ == '__main__':
    unittest.main()
