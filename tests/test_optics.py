"""Optical units, reproducibility, photon budget and real TMM coupling."""
from copy import deepcopy
from pathlib import Path
import tempfile
import sys
import unittest

import numpy as np

from perovskite_studio.optics import (DATA_DIR, NK_LIBRARY, get_optics, read_csv, spectrum_table,
                    integrate, nk_table)
from perovskite_studio.performance import (DEFAULT_SETTINGS, ENGINE_DIR, import_layers, write_inputs,
                         run_simulation, validate_device, _reverse_for_illumination)
from perovskite_studio.physics import example


class OpticalInputs(unittest.TestCase):
    def setUp(self):
        self.layers = import_layers(example())
        self.settings = dict(DEFAULT_SETTINGS, vmax=1.24, optics=dict(mode='spectrum'))

    def test_am15g_reference_and_si_normalization(self):
        raw = spectrum_table(dict(kind='builtin', name='AM1.5G'))
        self.assertEqual(len(raw), 2002)
        self.assertAlmostEqual(integrate(raw[:, 1], raw[:, 0]), 1000.3706555734423)
        with tempfile.TemporaryDirectory() as folder:
            mesh = write_inputs(folder, self.layers, self.settings)
            si = np.loadtxt(Path(folder)/'spectrum.txt', skiprows=1)
            self.assertAlmostEqual(integrate(si[:, 1], si[:, 0]), 1000.)
            self.assertAlmostEqual(si[0, 0], 280e-9)
            self.assertEqual(mesh['optical']['incident_power_mW_cm2'], 100.)
            self.assertLess(mesh['optical']['modelled_band_power_mW_cm2'], 100.)
            self.assertIn('genProfile = calc', (Path(folder)/'simulation_setup.txt').read_text())
            nk = np.loadtxt(Path(folder)/'nk_layer_2.txt', skiprows=1)
            self.assertAlmostEqual(nk[0, 0], 300e-9)
            self.assertEqual(len(mesh['optical']['boundary_nk_snapshots']), 3)

    def test_custom_data_validation_and_no_silent_extrapolation(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'nk.csv'
            path.write_text('wavelength_nm,n,k\n300,2,0.1\n800,2,0\n')
            spec = read_csv(path, 3)
            self.assertEqual(nk_table(spec).shape, (2, 3))
            path.unlink()
            self.assertEqual(nk_table(spec)[0, 1], 2)
            path.write_text('wavelength_nm,n,k\n300,2,0.1\n300,2,0\n')
            with self.assertRaisesRegex(ValueError, '递增'):
                read_csv(path, 3)
            path.write_text('300,2,-0.1\n800,2,0\n')
            with self.assertRaisesRegex(ValueError, 'k'):
                read_csv(path, 3)
        self.settings['optics']['wavelength_max'] = 900
        with self.assertRaisesRegex(ValueError, '覆盖'):
            validate_device(self.layers, self.settings)

    def test_manual_legacy_settings_do_not_require_optical_files(self):
        self.layers[0]['optical_nk'] = None
        validate_device(self.layers, dict(DEFAULT_SETTINGS))
        self.assertEqual(get_optics(DEFAULT_SETTINGS)['mode'], 'manual')


@unittest.skipUnless(sys.platform == 'win32' and (ENGINE_DIR/'simss.exe').exists(), 'Windows SimSS required')
class RealTMM(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.layers = import_layers(example())
        cls.settings = dict(DEFAULT_SETTINGS, vmax=1.24, optics=dict(mode='spectrum'))
        cls.reference = run_simulation(cls.layers, cls.settings)

    def test_photon_budget_and_full_spectrum_efficiency_denominator(self):
        r = self.reference
        o = r['optics']
        spectrum = np.asarray(o['spectrum_nm_W_m2_nm'])
        lo, hi = o['settings']['wavelength_min'], o['settings']['wavelength_max']
        keep = (spectrum[:, 0] >= lo) & (spectrum[:, 0] <= hi)
        wave, irradiance = spectrum[keep].T
        photons = integrate(irradiance*wave*1e-9/(6.62607015e-34*299792458), wave)
        maximum = photons*1.602176634e-19/10
        self.assertTrue(0 < r['generated_current'] < maximum*1.001)
        self.assertLess(r['metrics']['Jsc'], r['generated_current']*1.01)
        self.assertAlmostEqual(r['metrics']['PCE'], r['metrics']['Pmax']/100*100)
        self.assertLess(r['relative_current_error'], .001)
        self.assertGreater(np.ptp(o['G_cm3_s']), 0)
        self.assertEqual(len(o['x_nm']), r['mesh']['effective_points']+2)

    def test_halving_power_halves_generation(self):
        half = run_simulation(self.layers, dict(self.settings, pin=50))
        np.testing.assert_allclose(half['optics']['G_cm3_s'], np.asarray(self.reference['optics']['G_cm3_s'])/2,
                                   rtol=1e-10, atol=1.)
        self.assertAlmostEqual(half['metrics']['Jsc']/self.reference['metrics']['Jsc'], .5, delta=.01)
        self.assertEqual(half['optics']['incident_power_mW_cm2'], 50)

    def test_zero_absorption_and_custom_spectrum(self):
        transparent = deepcopy(self.layers)
        for a in transparent:
            a['optical_nk'] = dict(kind='builtin', name='Air')
        settings = deepcopy(self.settings)
        settings['optics'].update(substrate=dict(kind='builtin', name='Air'),
                                 back=dict(kind='builtin', name='Air'), tco_thickness=0)
        settings.update(vmin=0, vmax=.1, vstep=.05)
        r = run_simulation(transparent, settings)
        self.assertEqual(r['generated_current'], 0)
        self.assertLess(abs(r['metrics']['Jsc']), 1e-6)
        self.assertIsNone(r['metrics']['PCE'])
        custom = deepcopy(self.settings)
        custom['optics']['spectrum'] = dict(kind='custom', name='reference.csv',
                                           values=spectrum_table(dict(kind='builtin', name='AM1.5G')).tolist())
        r = run_simulation(self.layers, custom)
        np.testing.assert_allclose(r['current'], self.reference['current'], rtol=1e-8, atol=1e-7)

    def test_illumination_mirroring_and_graded_stack(self):
        reversed_layers, reversed_settings = _reverse_for_illumination(self.layers, self.settings)
        reversed_settings['optics']['illumination'] = 'right'
        mirrored = run_simulation(reversed_layers, reversed_settings)
        np.testing.assert_allclose(mirrored['current'], self.reference['current'], rtol=1e-7, atol=1e-6)
        self.assertAlmostEqual(mirrored['generated_current'], self.reference['generated_current'], places=8)
        layers = deepcopy(self.layers)
        layers[1].update(Eg_mode='线性', Eg_end=1.7, affinity_mode='独立端点', chi_end=4.,
                         Nd_mode='对数', Nd=1e13, Nd_end=1e15)
        coarse = run_simulation(layers, dict(self.settings, grading_slices=8))
        fine = run_simulation(layers, dict(self.settings, grading_slices=16))
        self.assertLess(abs(fine['generated_current']/coarse['generated_current']-1), .02)
        self.assertTrue(any('电学渐变' in w for w in fine['warnings']))


if __name__ == '__main__':
    unittest.main()
