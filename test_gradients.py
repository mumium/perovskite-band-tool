import unittest
import numpy as np
from physics import (layer, profile, normalize, solve, alignment, validate, example,
                     shift_band, drawing_segments)

class Gradients(unittest.TestCase):
    def test_band_allocation(self):
        a=layer('graded',500,25,1.5,4)
        a.update(Eg_mode='线性',Eg_end=1.8,band_fraction=.5)
        p=profile(a,[0,.5,1])
        np.testing.assert_allclose(p['Eg'],[1.5,1.65,1.8])
        np.testing.assert_allclose(-p['chi'],[-4,-3.925,-3.85])
        np.testing.assert_allclose(-p['chi']-p['Eg'],[-5.5,-5.575,-5.65])
        r=solve([a],left=4.75,right=4.75)
        np.testing.assert_allclose(r['Ec']-r['Ev'],r['Eg'])

    def test_doping_and_reversal(self):
        a=layer('graded',500,25,1.5,4,Nd=1e14)
        a.update(Nd_mode='对数',Nd_end=1e18,Na_mode='线性',Na_end=1e16)
        p=profile(a,[0,.5,1])
        np.testing.assert_allclose(p['Nd'],[1e14,1e16,1e18])
        np.testing.assert_allclose(p['Na'],[0,5e15,1e16])
        a.update(Nd=1e18,Nd_end=1e14)
        np.testing.assert_allclose(profile(a,[0,.5,1])['Nd'],p['Nd'][::-1])
        a['Na_mode']='对数'
        with self.assertRaises(ValueError):validate([a],300,5,4)

    def test_legacy_and_uniform_limit(self):
        a=layer('old',500,25,1.5,4)
        old=solve([a],left=4.75,right=4.75)
        np.testing.assert_allclose(old['Ec'],.75,atol=1e-7)
        b=normalize(a);b['Eg_mode']='线性';b['Nd_mode']='线性'
        new=solve([b],left=4.75,right=4.75)
        np.testing.assert_allclose(old['phi'],new['phi'])

    def test_doping_changes_equilibrium_not_alignment(self):
        a=layer('n',500,25,1.5,4,Nd=1e15)
        b=dict(a,Nd_mode='对数',Nd_end=1e17)
        np.testing.assert_allclose(alignment([a])['Ec'],alignment([b])['Ec'])
        r=solve([a],left=4.2,right=4.2)
        s=solve([b],left=4.2,right=4.2)
        self.assertGreater(np.max(np.abs(r['Ec']-s['Ec'])),.01)
        fine=solve([b],left=4.2,right=4.2,points=480)
        self.assertLess(np.max(np.abs(s['phi']-np.interp(s['x'],fine['x'],fine['phi']))),.01)

    def test_heterojunction_gradient(self):
        layers=example(); layers[1].update(Eg_mode='线性',Eg_end=1.8,band_fraction=.5,
                                          Na_mode='线性',Na_end=1e16)
        r=solve(layers)
        self.assertLess(r['error'],1e-9)
        np.testing.assert_allclose(r['Ec']-r['Ev'],r['Eg'])

    def test_drag_band_and_endpoints(self):
        a=layer('graded',500,25,1.5,4,Nd=1e15,Na=1e14)
        a.update(Eg_mode='线性',Eg_end=1.8,band_fraction=.5,Nd_mode='对数',Nd_end=1e17)
        t=np.linspace(0,1,17);base=profile(a,t)
        for band in ('Ec','Ev'):
            for part in ('left','right','whole'):
                b=shift_band(a,band,part,.12);p=profile(b,t)
                expected=.12*({'left':1-t,'right':t,'whole':np.ones_like(t)}[part])
                ec_change=-p['chi']+base['chi']
                ev_change=ec_change-p['Eg']+base['Eg']
                np.testing.assert_allclose(ec_change,expected if band=='Ec' else 0,atol=1e-12)
                np.testing.assert_allclose(ev_change,expected if band=='Ev' else 0,atol=1e-12)
                np.testing.assert_array_equal(p['Nd'],base['Nd'])
                np.testing.assert_array_equal(p['Na'],base['Na'])
                self.assertEqual(b['mun'],a['mun'])
                self.assertEqual(b['taun'],a['taun'])

    def test_invalid_drag_does_not_mutate_original(self):
        a=layer('test',500,25,1.5,4);saved=dict(a)
        with self.assertRaises(ValueError):shift_band(a,'Ev','right',2)
        with self.assertRaises(ValueError):shift_band(a,'Ec','whole',5)
        self.assertEqual(a,saved)

    def test_independent_affinity_and_display_endpoints(self):
        layers=example();layers[1]=shift_band(layers[1],'Ec','right',.15)
        r=solve(layers);segs=drawing_segments(layers,r)
        self.assertEqual(segs[0]['x'][0],0)
        self.assertEqual(segs[-1]['x'][-1],550)
        self.assertAlmostEqual(segs[0]['Ec'][0],5-layers[0]['chi'])
        self.assertAlmostEqual(segs[-1]['Ec'][-1],4.1-layers[-1]['chi'])
        for a,s in zip(layers,segs):
            p=profile(a,[0,1])
            np.testing.assert_allclose((s['Ec']-s['Ev'])[[0,-1]],p['Eg'])
        # Electrostatic potential is continuous at an ideal interface.
        p0=profile(layers[0],[1]);p1=profile(layers[1],[0])
        self.assertAlmostEqual(-p0['chi'][0]-segs[0]['Ec'][-1],-p1['chi'][0]-segs[1]['Ec'][0])

if __name__=='__main__':unittest.main()
