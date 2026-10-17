"""Real Tk integration: independent input copies, async runs, and exports."""
from copy import deepcopy
from pathlib import Path
import json
import tempfile
import time
import unittest
from unittest.mock import patch
import numpy as np
from tkinter import ttk

from perovskite_studio.app import App


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
            path = Path(folder)/'perovskite_studio.performance.json'
            with patch('perovskite_studio.performance_page.filedialog.asksaveasfilename', return_value=str(path)):
                p.save()
            p.vars['mun'].set('9')
            with patch('perovskite_studio.performance_page.filedialog.askopenfilename', return_value=str(path)):
                p.load()
            self.assertEqual(float(p.vars['mun'].get()), 7)
        p.import_from_band()
        self.assertEqual(p.layers[0]['mun'], original[0]['mun'])

    def test_real_background_run_exports_and_retained_result(self):
        p = self.page
        self.assertFalse(p.settings_visible)
        p.toggle_settings(True)
        p.settings['vmax'].set('1.25')
        p.run()
        self.assertFalse(p.settings_visible)
        self.assertTrue(p.running)
        self.assertEqual(str(p.run_button['state']), 'disabled')
        self.wait_for_run()
        self.assertIsNotNone(p.result)
        self.assertGreater(p.result['metrics']['PCE'], 0)
        self.assertFalse(p.settings_visible)
        self.assertEqual(p.settings_tabs.winfo_manager(), '')
        with tempfile.TemporaryDirectory() as folder:
            csv = Path(folder)/'JV.csv'
            report = Path(folder)/'results.json'
            image = Path(folder)/'JV.png'
            for method, path in ((p.export_csv, csv), (p.export_result, report), (p.export_image, image)):
                with patch('perovskite_studio.performance_page.filedialog.asksaveasfilename', return_value=str(path)):
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
        with patch('perovskite_studio.performance_page.run_simulation', side_effect=ValueError('test failure')), patch('perovskite_studio.performance_page.messagebox.showerror'):
            p.run()
            self.wait_for_run()
        self.assertIs(p.result, latest)
        self.assertFalse(p.settings_visible)
        p.run()
        p.cancel()
        self.wait_for_run()
        self.assertIs(p.result, latest)
        self.assertFalse(p.settings_visible)

    def test_small_screen_and_gradient_roundtrip(self):
        p = self.page
        from types import SimpleNamespace
        self.assertFalse(p.settings_visible)
        for height in (700, 400, 700):
            p.resize_right(SimpleNamespace(width=900, height=height))
            self.assertFalse(p.settings_visible)
        self.app.geometry('1024x650')
        self.app.update()
        self.assertGreater(p.canvas.get_tk_widget().winfo_height(), 100)
        self.assertLessEqual(p.footer.winfo_y()+p.footer.winfo_height(), p.winfo_height())
        self.assertGreaterEqual(p.run_button.winfo_height(), 45)
        self.assertEqual(float(p.settings['vstep'].get()), .02)
        self.assertEqual(float(p.settings['rs'].get()), .3)
        self.assertEqual(float(p.settings['rsh'].get()), 4e4)
        p.toggle_settings(True)
        self.assertTrue(p.settings_visible)
        p.resize_right(SimpleNamespace(width=900, height=700))
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
            with patch('perovskite_studio.performance_page.filedialog.asksaveasfilename', return_value=str(path)):
                p.save()
            p.vars['Eg_mode'].set('均匀')
            with patch('perovskite_studio.performance_page.filedialog.askopenfilename', return_value=str(path)):
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
        with patch('perovskite_studio.performance_page.messagebox.showerror') as error:
            p.run()
        error.assert_called_once()
        self.assertFalse(p.running)

    def test_first_tab_display_preserves_combobox_values_without_dirtying(self):
        p = self.page
        def combos(widget):
            found = []
            for child in widget.winfo_children():
                if isinstance(child, ttk.Combobox):
                    found.append(child)
                found.extend(combos(child))
            return found
        expected = {w: w.get() for w in combos(self.app) if w.cget('textvariable')}
        writes = []
        for w in expected:
            self.app.tk.call('trace', 'add', 'variable', w.cget('textvariable'), 'write',
                             self.app.register(lambda *_: writes.append(True)))
        p.dirty = False
        status = p.status.get()
        # These tabs are mapped for the first time in this fresh App.
        for notebook in (p.layer_tabs, p.settings_tabs):
            if notebook is p.settings_tabs:
                p.toggle_settings(True)
            for tab in notebook.tabs():
                notebook.select(tab)
                self.app.update()
                for w in combos(self.app.nametowidget(tab)):
                    self.assertTrue(w.winfo_ismapped())
                    self.assertTrue(w.get())
                    self.assertEqual(w.get(), expected[w])
                    self.assertEqual(w.xview()[0], 0)
        self.assertEqual(writes, [])
        self.assertFalse(p.dirty)
        self.assertEqual(p.status.get(), status)

    def test_rejected_project_keeps_all_current_inputs_and_results(self):
        p = self.page
        layers, settings = p.collect()
        original = deepcopy(p.layers)
        original_vars = {k: v.get() for k, v in p.vars.items()}
        for malformed in ('eqe', 'optics', 'last_interface'):
            data = dict(schema=1, model='simss', engine='5.36',
                        layers=deepcopy(layers), settings=deepcopy(settings))
            data['layers'][0]['name'] = 'Must not replace current layer'
            if malformed == 'eqe':
                data['settings']['eqe']['step_nm'] = 'invalid'
            elif malformed == 'optics':
                data['settings']['optics']['tco_thickness'] = 'invalid'
            else:
                data['layers'][-1]['interface']['trap_type'] = 99
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder)/'bad.json'
                path.write_text(json.dumps(data), encoding='utf-8')
                with patch('perovskite_studio.performance_page.filedialog.askopenfilename', return_value=str(path)), patch('perovskite_studio.performance_page.messagebox.showerror') as error:
                    p.load()
                error.assert_called_once()
            self.assertEqual(p.layers, original)
            self.assertEqual({k: v.get() for k, v in p.vars.items()}, original_vars)
            self.assertFalse(p.loading)

    def test_grading_inputs_follow_modes_and_ignore_inactive_text(self):
        from perovskite_studio.performance import discretize_layers
        p = self.page
        before = discretize_layers(p.collect()[0])
        for key in ('Eg_end', 'Nd_end', 'Na_end', 'chi_end', 'band_fraction'):
            self.assertEqual(str(p.grading_entries[key]['state']), 'disabled')
            p.vars[key].set('unused text')
        after = discretize_layers(p.collect()[0])
        self.assertEqual(before, after)
        p.vars['Eg_mode'].set('线性')
        self.assertEqual(str(p.grading_entries['Eg_end']['state']), 'normal')
        self.assertEqual(str(p.grading_entries['band_fraction']['state']), 'normal')
        with self.assertRaises(ValueError):
            p.commit_layer()
        p.vars['Eg_end'].set('3.2')
        p.vars['band_fraction'].set('.2')
        p.vars['Nd_mode'].set('对数')
        p.vars['Nd_end'].set('1e18')
        p.vars['Na_mode'].set('线性')
        p.vars['Na_end'].set('1e16')
        p.vars['affinity_mode'].set('独立端点')
        self.assertEqual(str(p.grading_entries['chi_end']['state']), 'normal')
        self.assertEqual(str(p.grading_entries['band_fraction']['state']), 'disabled')
        p.vars['band_fraction'].set('unused text')
        p.vars['chi_end'].set('2.1')
        p.commit_layer()
        self.assertEqual(p.layers[0]['chi_end'], 2.1)
        p.set_running(True)
        self.assertTrue(all(str(w['state']) == 'disabled' for w in p.grading_entries.values()))
        p.set_running(False)
        for key in ('Eg_end', 'Nd_end', 'Na_end', 'chi_end'):
            self.assertEqual(str(p.grading_entries[key]['state']), 'normal')
        self.assertEqual(str(p.grading_entries['band_fraction']['state']), 'disabled')
        p.vars['Eg_mode'].set('均匀')
        p.vars['Nd_mode'].set('均匀')
        p.vars['Na_mode'].set('均匀')
        p.vars['affinity_mode'].set('随带隙分配')
        p.commit_layer()
        p.index = 1
        p.show_fields()
        self.assertTrue(all(str(w['state']) == 'disabled' for w in p.grading_entries.values()))
        p.index = 0
        p.show_fields()
        self.assertTrue(all(str(w['state']) == 'disabled' for w in p.grading_entries.values()))
        p.vars['Eg_mode'].set('线性')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'gradient.json'
            with patch('perovskite_studio.performance_page.filedialog.asksaveasfilename', return_value=str(path)):
                p.save()
            p.vars['Eg_mode'].set('均匀')
            with patch('perovskite_studio.performance_page.filedialog.askopenfilename', return_value=str(path)):
                p.load()
            self.assertEqual(str(p.grading_entries['Eg_end']['state']), 'normal')
            self.assertEqual(str(p.grading_entries['Nd_end']['state']), 'disabled')

    def test_spectral_mode_profiles_and_portable_custom_data(self):
        p = self.page
        p.optical_mode.set('光谱 · TMM')
        p.settings['vmax'].set('1.24')
        self.assertEqual(str(p.G_entry['state']), 'disabled')
        with tempfile.TemporaryDirectory() as folder:
            nk_path = Path(folder)/'nk.csv'
            nk_path.write_text('wavelength_nm,n,k\n300,1.7,0.01\n800,1.7,0.01\n')
            with patch('perovskite_studio.performance_page.filedialog.askopenfilename', return_value=str(nk_path)):
                p.import_layer_nk()
            self.assertEqual(p.layers[0]['optical_nk']['kind'], 'custom')
            project = Path(folder)/'optical.json'
            with patch('perovskite_studio.performance_page.filedialog.asksaveasfilename', return_value=str(project)):
                p.save()
            nk_path.unlink()
            with patch('perovskite_studio.performance_page.filedialog.askopenfilename', return_value=str(project)):
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
                with patch('perovskite_studio.performance_page.filedialog.asksaveasfilename', return_value=str(path)):
                    p.export_csv()
                self.assertIn(header, path.read_text())
            report = Path(folder)/'result.json'
            with patch('perovskite_studio.performance_page.filedialog.asksaveasfilename', return_value=str(report)):
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
            with patch('perovskite_studio.performance_page.filedialog.asksaveasfilename', return_value=str(project)):
                p.save()
            with patch('perovskite_studio.performance_page.filedialog.askopenfilename', return_value=str(project)):
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
            with patch('perovskite_studio.performance_page.filedialog.asksaveasfilename', return_value=str(path)):
                p.export_csv()
            self.assertIn('interface_1_mA_cm2', path.read_text())
            p.run_eqe()
            self.assertFalse(p.settings_visible)
            self.assertEqual(str(p.run_button['state']), 'disabled')
            self.wait_for_run()
            self.assertIs(p.result, jv)
            self.assertEqual(p.metric_vars['PCE'].get(), metric)
            self.assertEqual(p.plot_view.get(), 'EQE')
            self.assertFalse(p.settings_visible)
            e = p.eqe_result
            path = Path(folder)/'eqe.csv'
            with patch('perovskite_studio.performance_page.filedialog.asksaveasfilename', return_value=str(path)):
                p.export_csv()
            self.assertIn('EQE_fraction', path.read_text())
            report = Path(folder)/'eqe.json'
            with patch('perovskite_studio.performance_page.filedialog.asksaveasfilename', return_value=str(report)):
                p.export_result()
            saved = json.loads(report.read_text(encoding='utf-8'))
            self.assertEqual(saved['kind'], 'eqe')
            self.assertEqual(saved['layers'][1]['Eg'], 1.7)
            with patch('perovskite_studio.performance_page.run_eqe', side_effect=ValueError('failure')), patch('perovskite_studio.performance_page.messagebox.showerror'):
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

    def test_single_illumination_line_survives_compact_layout_and_direction_change(self):
        a=self.app;p=self.page
        self.assertFalse(hasattr(a,'structure_direction'))
        for compact in (False,True):
            a.structure.set_compact(compact)
            self.assertEqual(a.structure.light_text.get(),'→  入射光：左 → 右')
            for direction,expected in (('右侧入射','←  入射光：右 → 左'),('左侧入射','→  入射光：左 → 右')):
                p.illumination.set(direction);a.update()
                p.structure.set_compact(compact)
                self.assertEqual(p.structure.light_text.get(),expected)
                self.assertTrue(p.structure.light_label.winfo_viewable())
                self.assertTrue(a.structure.light_label.winfo_manager())


if __name__ == '__main__':
    unittest.main()
