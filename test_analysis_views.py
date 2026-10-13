import unittest
import numpy as np
from physics import layer, example, alignment, solve
from analysis_views import analysis_segments, interface_offsets, sample_position


class AnalysisViews(unittest.TestCase):
    def test_signed_interface_offsets_and_one_sided_hover(self):
        layers=example()
        layers[1].update(Eg_mode='线性',Eg_end=1.8,band_fraction=.5)
        segments=analysis_segments(layers,alignment(layers))
        offsets=interface_offsets(layers,segments)
        self.assertAlmostEqual(offsets[0]['Ec'],-1.9)
        self.assertAlmostEqual(offsets[0]['Ev'],-.45)
        x=layers[0]['thickness']
        a=sample_position(layers,segments,x,side='left')
        b=sample_position(layers,segments,x)
        self.assertEqual((a['index'],b['index']),(0,1))
        self.assertAlmostEqual(b['Ec']-a['Ec'],offsets[0]['Ec'])
        self.assertNotIn('field',a)
        end=sample_position(layers,segments,520,side='left')
        self.assertAlmostEqual(end['Eg'],1.8)

    def test_field_sign_units_and_dielectric_interface(self):
        layers=[layer('a',10,10,2,3),layer('b',20,20,2,4)]
        # Charge-free piecewise linear potential with continuous eps*E.
        result=dict(counts=[2,2],x=np.array([2.5,7.5,15.,25.]),
                    phi=np.array([-5.125,-5.375,-5.625,-5.875]))
        segments=analysis_segments(layers,result,left=5.,right=6.)
        np.testing.assert_allclose(segments[0]['field'],5e5)
        np.testing.assert_allclose(segments[1]['field'],2.5e5)
        self.assertAlmostEqual(segments[0]['phi'][-1],segments[1]['phi'][0])
        self.assertAlmostEqual(10*segments[0]['field'][-1],20*segments[1]['field'][0])

    def test_gradient_band_edges_do_not_create_electric_field(self):
        a=layer('graded',100,10,1.5,4)
        a.update(Eg_mode='线性',Eg_end=2,affinity_mode='独立端点',chi_end=4.5)
        result=dict(counts=[3],x=np.array([100/6,50.,500/6]),phi=np.full(3,-5.))
        s=analysis_segments([a],result,left=5.,right=5.)[0]
        np.testing.assert_allclose(s['field'],0,atol=1e-8)
        self.assertGreater(np.ptp(s['Ec']),.4)

    def test_equilibrium_readouts_match_solver_at_cell_centres(self):
        layers=example();r=solve(layers)
        segments=analysis_segments(layers,r)
        for key in ('phi','n','p'):
            actual=np.concatenate([s[key][1:-1] for s in segments])
            np.testing.assert_allclose(actual,r[key],rtol=1e-10,atol=1e-12)
        x=float(r['x'][25]);v=sample_position(layers,segments,x)
        self.assertAlmostEqual(v['Ec'],r['Ec'][25])
        self.assertAlmostEqual(v['n']/r['n'][25],1.)
        with self.assertRaises(ValueError):sample_position(layers,segments,-1)


if __name__=='__main__':unittest.main()
