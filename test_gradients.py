import unittest
import numpy as np
from physics import layer, profile, normalize, solve, alignment, validate, example

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

if __name__=='__main__':unittest.main()
