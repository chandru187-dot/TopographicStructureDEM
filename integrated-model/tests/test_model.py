import json
import math
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from railmodel.maths import ipf,gravity,mode_choice,rail_loading,appraisal,interpolate,screenline_geH
from railmodel.demand import generation,capture,freight_operations
from railmodel.engine import Engine
from railmodel.store import Store

ROOT=Path(__file__).resolve().parents[1]


class PrimitiveTests(unittest.TestCase):
    def test_furness_margins_and_zeros(self):
        x,n=ipf([[0,1,1],[1,0,1],[1,1,0]],[10,20,30],[20,20,20])
        for a,b in zip(map(sum,x),[10,20,30]):self.assertAlmostEqual(a,b,places=5)
        for j in range(3):self.assertAlmostEqual(sum(r[j] for r in x),20,places=5)
        self.assertEqual(x[0][0],0)

    def test_unbalanced_margins_rejected(self):
        with self.assertRaises(ValueError):ipf([[1]],[10],[20])

    def test_infeasible_matrix_rejected(self):
        with self.assertRaises(ValueError):ipf([[0,0],[1,0]],[2,2],[2,2])

    def test_nonfinite_rejected(self):
        with self.assertRaises(ValueError):ipf([[float('nan')]],[1],[1])

    def test_logit_stability_and_conservation(self):
        p=mode_choice({'car':1000,'rail':1001});self.assertAlmostEqual(sum(p.values()),1);self.assertGreater(p['rail'],p['car'])

    def test_frequency_utility_response(self):
        choices=[{'mode':'car','minutes':40,'cost':10,'asc':0,'source':'fixture','status':'Synthetic test'},
                 {'mode':'rail','minutes':30,'cost':5,'asc':0,'source':'fixture','status':'Synthetic test'}]
        slow=capture(100,choices,-0.05,-0.1)[1]['share'];choices[1]['minutes']=25
        self.assertGreater(capture(100,choices,-0.05,-0.1)[1]['share'],slow)

    def test_line_loading_independent(self):
        r=rail_loading([[0,10,20],[5,0,7],[3,4,0]],['A','B','C'])
        self.assertEqual(r['daily_trips'],49)
        self.assertEqual(r['links'][0]['forward_passengers'],30)
        self.assertEqual(r['links'][1]['forward_passengers'],27)
        self.assertEqual(r['links'][0]['reverse_passengers'],8)
        self.assertEqual(r['balance_residual'],0)

    def test_generation_unit_guard(self):
        r={'id':'1','zone':'z','quantum':20,'quantum_unit':'unit','rate':2,'rate_denominator_unit':'unit','overlap_weight':0.5,'source':'fixture','status':'Synthetic test'}
        self.assertEqual(generation([r])[0]['trips'],20)
        r['rate_denominator_unit']='ha'
        with self.assertRaises(ValueError):generation([r])

    def test_economic_discount_independent(self):
        r=appraisal([{'year':2030,'benefit':0,'cost':100},{'year':2031,'benefit':110,'cost':0}],0.1,2030)
        self.assertAlmostEqual(r['npv'],0);self.assertAlmostEqual(r['bcr'],1)

    def test_geH_zero_and_symmetry(self):
        self.assertEqual(screenline_geH(0,0),0)
        self.assertEqual(screenline_geH(20,30),screenline_geH(30,20))

    def test_interpolation_no_extrapolation(self):
        self.assertEqual(interpolate({'2030':100,'2040':200},2035),150)
        with self.assertRaises(ValueError):interpolate({'2030':100},2040)

    def test_empty_freight_returns(self):
        r=freight_operations([{'service':'S','origin':'A','destination':'B','direction':'A','tonnes_year':100,'teu_year':600}],300,1,None)
        self.assertEqual(r[0]['paths_day'],4)


class ApiTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        from railmodel.api import app
        self.client=TestClient(app)

    def test_api_locked_without_token(self):
        with patch.dict(os.environ,{'MODEL_API_TOKEN':'','MODEL_DEV_MODE':'0'}):
            self.assertEqual(self.client.get('/api/evidence').status_code,503)

    def test_api_unauthorised(self):
        with patch.dict(os.environ,{'MODEL_API_TOKEN':'test-key'}):
            self.assertEqual(self.client.post('/api/runs',json={}).status_code,401)

    def test_dashboard_shell(self):
        r=self.client.get('/');self.assertEqual(r.status_code,200);self.assertIn('Run model',r.text)


if __name__=='__main__':unittest.main()
