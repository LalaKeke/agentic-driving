import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'experiments'))
import cordriver_reachable_options as menu


class ReachableOptionTests(unittest.TestCase):
    def test_causal_easing_preserves_origin_and_moves_along_curved_reference(self):
        x=np.arange(7,dtype=float)
        poses=np.column_stack((x,.07*x*x,np.arctan(.14*x)))
        xy, guide=menu.target_curve(poses,.7,.8)
        np.testing.assert_allclose(xy[0],poses[0,:2])
        follow,_=menu.target_curve(poses,.7,0.)
        self.assertAlmostEqual(float(np.linalg.norm(xy[-1]-follow[-1])),.8)
        self.assertLess(float(follow[-1,0]),float(x[-1]))
        self.assertEqual(guide['at_3_0s']['center_xy_m'],xy[-1].tolist())
        self.assertEqual(guide['at_3_0s']['region_type'],'lateral')

    def test_stopped_reference_has_finite_curve_and_witness_windows(self):
        xy,g=menu.target_curve(np.zeros((7,3)),.7,0.)
        np.testing.assert_array_equal(xy,np.zeros((7,2)))
        self.assertEqual(g['at_3_0s']['speed_range_mps'],[0.,1.])
        self.assertEqual(menu.node((0,0),np.pi,0,'near_stop',.25)['speed_range_mps'],[0,.25])

    def test_lookup_checks_frozen_offer_candidate_certificate_and_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            candidate=root/'candidate.json';receipt=root/'receipt.json';proof=root/'cert.json'
            menu.save(candidate,{'controls_accel_steer':[[0,0]]*12})
            menu.save(receipt,{'eligible_before_fixed_clearance':True})
            offer={'option_id':'P01'}
            menu.save(proof,{'token':'tok','option_id':'P01','checks':{'physical_and_environment':True,
                              'clearance_floor':True,'smooth':True,'exact_both_nodes':True},
                             'public_sha256':menu.digest(offer),'audit_receipt':str(receipt),
                             'audit_sha256':menu.sha(receipt),'candidate_sha256':menu.sha(candidate)})
            item=dict(candidate_path=str(candidate),candidate_sha256=menu.sha(candidate),certificate_path=str(proof),
                      certificate_sha256=menu.sha(proof),method='test')
            portfolio=root/'portfolio.json'
            menu.save(portfolio,dict(version='witnessed_body_menu_v1',token='tok',
                                     public_options=[offer],options={'P01':item}))
            frozen=menu.sha(portfolio)
            self.assertEqual(menu.select_option(portfolio,'P01',expected_sha256=frozen),item)
            with self.assertRaisesRegex(ValueError,'OPTION_NOT_OFFERED'):
                menu.select_option(portfolio,'P99',expected_sha256=frozen)
            with self.assertRaisesRegex(ValueError,'PORTFOLIO_DIFFERS'):
                menu.select_option(portfolio,'P01',expected_sha256='unbound')
            candidate.write_text(json.dumps({'other':True}))
            with self.assertRaisesRegex(ValueError,'DIGEST_MISMATCH'):
                menu.select_option(portfolio,'P01',expected_sha256=frozen)
