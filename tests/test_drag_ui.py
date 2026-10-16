"""Integration tests exercising real Matplotlib events in a Tk window."""
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
import numpy as np
from matplotlib.backend_bases import MouseEvent
from perovskite_studio.app import App

class DragUI(unittest.TestCase):
    def setUp(self):
        self.app=App();self.app.update()
        self.app.mode.set('真空参考能级排列');self.app.calculate();self.app.update()

    def tearDown(self):
        self.app.update()
        self.app.destroy()

    def emit(self,kind,x,y):
        a=self.app;px,py=a.ax.transData.transform((x,y))
        event=MouseEvent(kind,a.canvas,px,py,button=1)
        a.canvas.callbacks.process(kind,event);a.update()

    def drag(self,band,part,delta,release=True):
        a=self.app;s=a.segments[1]
        j={'left':0,'right':-1,'whole':len(s['x'])//2}[part]
        x,y=s['x'][j],s[band][j]
        self.emit('button_press_event',x,y)
        self.assertIsNotNone(a.drag)
        self.assertEqual(a.drag['part'],part)
        self.emit('motion_notify_event',x,y+delta)
        if release:self.emit('button_release_event',x,y+delta)

    def test_whole_and_endpoint_undo(self):
        a=self.app;old=deepcopy(a.layers)
        self.drag('Ec','whole',.2)
        self.assertAlmostEqual(a.layers[1]['chi'],3.7,places=6)
        self.assertAlmostEqual(a.layers[1]['Eg'],1.75,places=6)
        self.assertEqual(a.vars['affinity_mode'].get(),'独立端点')
        self.assertEqual(a.vars['Eg_mode'].get(),'均匀')
        first=deepcopy(a.layers)
        self.drag('Ev','right',-.15)
        self.assertEqual(a.vars['Eg_mode'].get(),'线性')
        self.assertAlmostEqual(float(a.vars['Eg_end'].get()),1.9,places=6)
        self.assertAlmostEqual(a.layers[1]['Eg'],1.75,places=6)
        a.undo_drag();self.assertEqual(a.layers,first)
        a.undo_drag();self.assertEqual(a.layers,old)

    def test_cancel_and_toolbar(self):
        a=self.app;old=deepcopy(a.layers)
        self.drag('Ec','left',.1,release=False)
        self.assertNotEqual(a.layers,old)
        a.cancel_drag();self.assertEqual(a.layers,old)
        self.assertIsNotNone(a.result)
        a.toolbar.pan()
        s=a.segments[1];self.emit('button_press_event',s['x'][60],s['Ec'][60])
        self.assertIsNone(a.drag)
        a.toolbar.pan()

    def test_equilibrium_recompute_and_failure_rollback(self):
        a=self.app;a.mode.set('热平衡弯曲能带');a.calculate();a.update()
        self.drag('Ec','right',.1)
        self.assertLess(a.result['error'],1e-9)
        np.testing.assert_allclose(a.result['Ec']-a.result['Ev'],a.result['Eg'])
        old=deepcopy(a.layers)
        self.drag('Ev','left',-.1,release=False)
        original_solve=__import__('perovskite_studio.app',fromlist=['solve']).solve
        calls=[0]
        def fail_once(*args,**kwargs):
            calls[0]+=1
            if calls[0]==1:raise ValueError('test convergence failure')
            return original_solve(*args,**kwargs)
        with patch('perovskite_studio.app.solve',side_effect=fail_once):a.drag_release(None)
        self.assertEqual(a.layers,old)
        self.assertIsNotNone(a.result)
        self.assertIn('恢复',a.status.get())

    def test_project_and_exports_after_drag(self):
        a=self.app;self.drag('Ec','right',.2)
        expected=deepcopy(a.layers)
        with tempfile.TemporaryDirectory() as folder:
            project=Path(folder)/'project.json'
            with patch('perovskite_studio.app.filedialog.asksaveasfilename',return_value=str(project)):a.save()
            saved=json.loads(project.read_text(encoding='utf-8'))
            self.assertEqual(saved['schema'],3)
            self.assertNotIn('structure_illumination',saved)
            # Old purely illustrative direction settings remain load-compatible.
            saved['structure_illumination']='right'
            project.write_text(json.dumps(saved),encoding='utf-8')
            with patch('perovskite_studio.app.filedialog.askopenfilename',return_value=str(project)):a.load()
            self.assertEqual(a.layers,expected)
            self.assertEqual(a.structure.direction,'left')
            for method,name in ((a.export_params,'parameters.csv'),(a.export_curve,'curve.csv')):
                path=Path(folder)/name
                with patch('perovskite_studio.app.filedialog.asksaveasfilename',return_value=str(path)):method()
                self.assertIn('χ' if name=='parameters.csv' else 'chi_eV',path.read_text(encoding='utf-8-sig'))

    def test_hover_and_interface_annotations(self):
        a=self.app
        self.assertEqual(len(a.offset_artists),4)
        self.emit('motion_notify_event',250,-4)
        self.assertIn('Ec',a.hover_text.get())
        self.assertIn('Eg',a.hover_text.get())
        self.assertIn('钙钛矿',a.hover_text.get())
        self.assertTrue(a.hover_line.get_visible())
        a.show_offsets.set(False);a.change_view();a.update()
        self.assertEqual(a.offset_artists,[])
        a.clear_hover();self.assertFalse(a.hover_line.get_visible())

    def test_restored_layout_with_right_hand_readings_and_stable_dragging(self):
        a=self.app
        self.assertEqual([a.band_settings_tabs.tab(t,'text') for t in a.band_settings_tabs.tabs()],['基本参数','渐变设置'])
        for geometry,minimum_height in (('1024x650',100),('900x560',65),('1320x880',220)):
            a.geometry(geometry);a.update();a.canvas.draw();a.update()
            self.assertGreater(a.ax.bbox.height,minimum_height)
            self.assertTrue(a.structure.winfo_viewable())
            self.assertEqual(int(a.listbox['height']),5)
            chart=a.canvas.get_tk_widget()
            self.assertGreaterEqual(a.band_readings.winfo_rootx(),chart.winfo_rootx()+chart.winfo_width())
        a.geometry('1024x650');a.update();a.canvas.draw();a.update()
        a.index=1;a.refresh_list();a.show_fields();a.canvas.draw();a.update()
        before=a.ax.bbox.bounds
        self.emit('motion_notify_event',250,-4)
        self.assertIn('Ec',a.hover_text.get())
        np.testing.assert_allclose(a.ax.bbox.bounds,before,atol=.5)
        last=a.hover_text.get()
        from types import SimpleNamespace
        a.clear_hover(SimpleNamespace())
        self.assertEqual(a.hover_text.get(),last)
        self.drag('Ec','right',.15,release=False)
        np.testing.assert_allclose(a.ax.bbox.bounds,before,atol=.5)
        a.cancel_drag();a.update()
        self.assertIsNotNone(a.fig.get_layout_engine())

    def test_diagnostic_views_and_vacuum_mode(self):
        a=self.app;a.mode.set('热平衡弯曲能带');a.calculate();a.update()
        a.compare()
        for view in ('电势','电场','载流子浓度'):
            a.view.set(view);a.change_view();a.update()
            self.assertEqual(a.band_artists,{})
            self.assertEqual(a.ax.get_yscale(),'log' if view=='载流子浓度' else 'linear')
            low,high=a.ax.get_ylim()
            y=np.sqrt(low*high) if view=='载流子浓度' else (low+high)/2
            self.emit('motion_notify_event',250,y)
            self.assertIn('V/cm',a.hover_text.get())
            self.assertIn('cm⁻³',a.hover_text.get())
            self.emit('button_press_event',250,y)
            self.assertIsNone(a.drag)
            self.assertTrue(any('对照' in line.get_label() for line in a.ax.lines))
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'curve.csv'
            with patch('perovskite_studio.app.filedialog.asksaveasfilename',return_value=str(path)):a.export_curve()
            data=np.genfromtxt(path,delimiter=',',names=True)
            self.assertIn('E_V_per_cm',data.dtype.names)
            expected=np.concatenate([s['field'][1:-1] for s in a.segments])
            np.testing.assert_allclose(data['E_V_per_cm'],expected)
        a.mode.set('真空参考能级排列');a.calculate();a.update()
        self.assertEqual(a.view.get(),'能带')
        self.assertEqual(tuple(a.view_combo['values']),('能带',))
        self.assertEqual(a.ax.get_yscale(),'linear')
        a.mode.set('热平衡弯曲能带');a.calculate();a.update()
        self.drag('Ec','right',.05)
        self.assertLess(a.result['error'],1e-9)

if __name__=='__main__':unittest.main()
