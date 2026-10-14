"""Real Tk integration: independent input copies, async runs, and exports."""
from copy import deepcopy
from pathlib import Path
import json
import tempfile
import time
import unittest
from unittest.mock import patch

from app import App


class PerformanceUI(unittest.TestCase):
    def setUp(self):
        self.app = App()
        self.app.pages.select(self.app.performance)
        self.app.update()
        self.page = self.app.performance

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


if __name__ == '__main__':
    unittest.main()
