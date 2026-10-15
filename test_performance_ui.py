"""Real Tk integration: independent input copies, async runs, and exports."""
from copy import deepcopy
from pathlib import Path
import json
import tempfile
import time
import unittest
from unittest.mock import patch
import numpy as np

from app import App


class PerformanceUI(unittest.TestCase):
    def setUp(self):
        self.app = App()
        self.app.pages.select(self.app.performance)
        self.app.update()
        self.page = self.app.performance
        self.page.optical_mode.set('手动光生率 G')
        self.app.update()

    def tearDown(self):
        self.app.update()
        self.app.destroy()

    def wait_for_run(self):
        deadline = time.monotonic()+15
        while self.page.running and time.monotonic() < deadline:
            self.app.update()
            time.sleep(.01)
        self.assertFalse(self.page.running)

    def test_parameter_independence_and_project_roundtrip(self):
        p = self.page
        original = deepcopy(self.app.layers)
        p.vars['mun'].set('7')
        p.collect()
        self.assertEqual(self.app.layers, original)
        self.assertEqual(p.layers[0]['mun'], 7)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'performance.json'
            with patch('performance_page.filedialog.asksaveasfilename', return_value=str(path)):
                p.save()
            p.vars['mun'].set('9')
            with patch('performance_page.filedialog.askopenfilename', return_value=str(path)):
                p.load()
            self.assertEqual(float(p.vars['mun'].get()), 7)
        p.import_from_band()
        self.assertEqual(p.layers[0]['mun'], original[0]['mun'])

    def test_real_background_run_exports_and_retained_result(self):
        p = self.page
        p.settings['vmax'].set('1.25')
        p.run()
        self.assertTrue(p.running)
        self.assertEqual(str(p.run_button['state']), 'disabled')
        self.wait_for_run()
        self.assertIsNotNone(p.result)
        self.assertGreater(p.result['metrics']['PCE'], 0)
        with tempfile.TemporaryDirectory() as folder:
            csv = Path(folder)/'JV.csv'
            report = Path(folder)/'results.json'
            image = Path(folder)/'JV.png'
            for method, path in ((p.export_csv, csv), (p.export_result, report), (p.export_image, image)):
                with patch('performance_page.filedialog.asksaveasfilename', return_value=str(path)):
                    method()
                self.assertTrue(path.is_file())
            data = json.loads(report.read_text(encoding='utf-8'))
            self.assertIn('settings', data)
            self.assertIn('layers', data)
            self.assertIn('log', data)
            self.assertEqual(len(data['voltage']), len(data['current']))
            self.assertIn('J_output_mA_cm2', csv.read_text())
            table = np.genfromtxt(csv, delimiter=',', names=True)
            for key, column in [('Jsc', 'Jsc_mA_cm2'), ('Voc', 'Voc_V'), ('FF', 'FF_percent'), ('PCE', 'PCE_percent')]:
                np.testing.assert_allclose(table[column], p.result['metrics'][key])
        p.settings['G'].set('2e21')
        previous = p.result
        value = p.metric_vars['PCE'].get()
        self.assertIs(p.result, previous)
        self.assertTrue(p.dirty)
        self.assertTrue(all(str(b['state']) == 'normal' for b in p.export_buttons))
        p.run()
        self.assertIs(p.result, previous)
        self.assertEqual(p.metric_vars['PCE'].get(), value)
        self.wait_for_run()
        self.assertIs(p.previous_result, previous)
        self.assertFalse(p.dirty)
        self.assertTrue(any(line.get_label() == '上次结果' for line in p.ax.lines))
        self.assertIn('ΔPCE', p.comparison_text.get())
        p.settings['G'].set('1e21')
        latest = p.result
        with patch('performance_page.run_simulation', side_effect=ValueError('test failure')), patch('performance_page.messagebox.showerror'):
            p.run()
            self.wait_for_run()
        self.assertIs(p.result, latest)
        p.run()
        p.cancel()
        self.wait_for_run()
        self.assertIs(p.result, latest)

    def test_small_screen_and_gradient_roundtrip(self):
        p = self.page
        self.app.geometry('1024x650')
        self.app.update()
        self.assertGreater(p.canvas.get_tk_widget().winfo_height(), 100)
        self.assertLessEqual(p.footer.winfo_y()+p.footer.winfo_height(), p.winfo_height())
        self.assertGreaterEqual(p.run_button.winfo_height(), 45)
        self.assertEqual(float(p.settings['vstep'].get()), .02)
        p.toggle_settings(True)
        self.assertTrue(p.settings_visible)
        p.vars['Eg_mode'].set('线性')
        p.vars['Eg_end'].set('3.1')
        p.vars['affinity_mode'].set('独立端点')
        p.vars['chi_end'].set('2.05')
        p.collect()
        self.assertEqual(p.layers[0]['Eg_mode'], '线性')
        self.assertEqual(p.layers[0]['chi_end'], 2.05)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'gradient.json'
            with patch('performance_page.filedialog.asksaveasfilename', return_value=str(path)):
                p.save()
            p.vars['Eg_mode'].set('均匀')
            with patch('performance_page.filedialog.askopenfilename', return_value=str(path)):
                p.load()
            self.assertEqual(p.vars['Eg_mode'].get(), '线性')
            self.assertEqual(float(p.vars['chi_end'].get()), 2.05)

    def test_cancel_and_invalid_input(self):
        p = self.page
        p.run()
        p.cancel()
        self.wait_for_run()
        self.assertIsNone(p.result)
        self.assertIn('取消', p.status.get())
        p.settings['vstep'].set('0')
        with patch('performance_page.messagebox.showerror') as error:
            p.run()
        error.assert_called_once()
        self.assertFalse(p.running)

    def test_spectral_mode_profiles_and_portable_custom_data(self):
        p = self.page
        p.optical_mode.set('光谱 · TMM')
        p.settings['vmax'].set('1.24')
        self.assertEqual(str(p.G_entry['state']), 'disabled')
        with tempfile.TemporaryDirectory() as folder:
            nk_path = Path(folder)/'nk.csv'
            nk_path.write_text('wavelength_nm,n,k\n300,1.7,0.01\n800,1.7,0.01\n')
            with patch('performance_page.filedialog.askopenfilename', return_value=str(nk_path)):
                p.import_layer_nk()
            self.assertEqual(p.layers[0]['optical_nk']['kind'], 'custom')
            project = Path(folder)/'optical.json'
            with patch('performance_page.filedialog.asksaveasfilename', return_value=str(project)):
                p.save()
            nk_path.unlink()
            with patch('performance_page.filedialog.askopenfilename', return_value=str(project)):
                p.load()
            self.assertEqual(p.optical_mode.get(), '光谱 · TMM')
            p.run()
            self.wait_for_run()
            self.assertIsNotNone(p.result)
            self.assertEqual(p.result['optics']['mode'], 'spectrum')
            for view, header in [('入射光谱', 'wavelength_nm'), ('光生分布', 'G_cm3_s')]:
                p.plot_view.set(view)
                p.plot()
                path = Path(folder)/(view+'.csv')
                with patch('performance_page.filedialog.asksaveasfilename', return_value=str(path)):
                    p.export_csv()
                self.assertIn(header, path.read_text())
            report = Path(folder)/'result.json'
            with patch('performance_page.filedialog.asksaveasfilename', return_value=str(report)):
                p.export_result()
            data = json.loads(report.read_text(encoding='utf-8'))
            self.assertIn('spectrum_sha256', data['optics'])
            self.assertEqual(data['optics']['incident_power_mW_cm2'], 100)

    def test_interface_edge_eqe_loss_exports_and_retained_snapshots(self):
        p = self.page
        p.optical_mode.set('光谱 · TMM')
        p.settings['vmax'].set('1.24')
        p.interface_vars['Nt'].set('1e11')
        p.interface_type.set('受主')
        p.commit_layer()
        p.index = 1
        p.show_fields()
        self.assertEqual(p.edge_mode.get(), '吸收边联动（近似）')
        p.vars['Eg'].set('1.7')
        p.eqe_vars['step_nm'].set('100')
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)/'project.json'
            with patch('performance_page.filedialog.asksaveasfilename', return_value=str(project)):
                p.save()
            with patch('performance_page.filedialog.askopenfilename', return_value=str(project)):
                p.load()
            self.assertEqual(p.layers[0]['interface']['Nt'], 1e11)
            self.assertEqual(p.layers[0]['interface']['trap_type'], -1)
            self.assertEqual(p.layers[1]['optical_model']['reference_gap'], 1.55)
            self.assertEqual(float(p.eqe_vars['step_nm'].get()), 100)
            p.run()
            self.wait_for_run()
            jv = p.result
            metric = p.metric_vars['PCE'].get()
            p.plot_view.set('损失分析')
            p.plot()
            self.assertIn('电流预算', p.analysis_note.get())
            path = Path(folder)/'loss.csv'
            with patch('performance_page.filedialog.asksaveasfilename', return_value=str(path)):
                p.export_csv()
            self.assertIn('interface_1_mA_cm2', path.read_text())
            p.run_eqe()
            self.assertEqual(str(p.run_button['state']), 'disabled')
            self.wait_for_run()
            self.assertIs(p.result, jv)
            self.assertEqual(p.metric_vars['PCE'].get(), metric)
            self.assertEqual(p.plot_view.get(), 'EQE')
            e = p.eqe_result
            path = Path(folder)/'eqe.csv'
            with patch('performance_page.filedialog.asksaveasfilename', return_value=str(path)):
                p.export_csv()
            self.assertIn('EQE_fraction', path.read_text())
            report = Path(folder)/'eqe.json'
            with patch('performance_page.filedialog.asksaveasfilename', return_value=str(report)):
                p.export_result()
            saved = json.loads(report.read_text(encoding='utf-8'))
            self.assertEqual(saved['kind'], 'eqe')
            self.assertEqual(saved['layers'][1]['Eg'], 1.7)
            with patch('performance_page.run_eqe', side_effect=ValueError('failure')), patch('performance_page.messagebox.showerror'):
                p.run_eqe()
                self.wait_for_run()
            self.assertIs(p.eqe_result, e)
            self.assertIs(p.result, jv)
            p.run_eqe()
            p.cancel()
            self.wait_for_run()
            self.assertIs(p.eqe_result, e)

    def test_controls_keep_geometry_when_selected_or_pressed(self):
        nb = self.app.pages
        def tab_spans():
            return [str(nb.tk.call(nb._w, 'identify', 'tab', x, 25)) for x in range(480)]
        self.app.pages.select(0)
        self.app.update()
        before = tab_spans()
        self.app.pages.select(self.page)
        self.app.update()
        self.assertEqual(before, tab_spans())
        b = self.page.run_button
        size = b.winfo_reqwidth(), b.winfo_reqheight()
        for state in ('pressed', 'focus', 'disabled'):
            b.state((state,))
            self.app.update_idletasks()
            self.assertEqual(size, (b.winfo_reqwidth(), b.winfo_reqheight()))
            b.state(('!'+state,))


if __name__ == '__main__':
    unittest.main()
