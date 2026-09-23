"""Integration tests exercising real Matplotlib events in a Tk window."""
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
import numpy as np
from matplotlib.backend_bases import MouseEvent
from app import App

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
        original_solve=__import__('app').solve
        calls=[0]
        def fail_once(*args,**kwargs):
            calls[0]+=1
            if calls[0]==1:raise ValueError('test convergence failure')
            return original_solve(*args,**kwargs)
        with patch('app.solve',side_effect=fail_once):a.drag_release(None)
        self.assertEqual(a.layers,old)
        self.assertIsNotNone(a.result)
        self.assertIn('恢复',a.status.get())

    def test_project_and_exports_after_drag(self):
        a=self.app;self.drag('Ec','right',.2)
        expected=deepcopy(a.layers)
        with tempfile.TemporaryDirectory() as folder:
            project=Path(folder)/'project.json'
            with patch('app.filedialog.asksaveasfilename',return_value=str(project)):a.save()
            self.assertEqual(json.loads(project.read_text(encoding='utf-8'))['schema'],3)
            with patch('app.filedialog.askopenfilename',return_value=str(project)):a.load()
            self.assertEqual(a.layers,expected)
            for method,name in ((a.export_params,'parameters.csv'),(a.export_curve,'curve.csv')):
                path=Path(folder)/name
                with patch('app.filedialog.asksaveasfilename',return_value=str(path)):method()
                self.assertIn('χ' if name=='parameters.csv' else 'chi_eV',path.read_text(encoding='utf-8-sig'))

if __name__=='__main__':unittest.main()
