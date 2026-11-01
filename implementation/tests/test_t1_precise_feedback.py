"""Precise T1 repair evidence without changing acceptance or native output.

All cases are authored fixtures; no images, GT, network or model calls.
"""
from copy import deepcopy
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agentic import t1
from agentic.contracts import Failure, digest
from agentic.fixtures import rect
from agentic.fixtures_v3 import metric_case
from agentic.metric_contract import _premises


class PreciseT1FeedbackTests(unittest.TestCase):
    def reject_fixture(self, cap, out, prov):
        prov['output_digest'] = digest(out)
        before = deepcopy((cap, out, prov))
        with self.assertRaises(Failure) as caught:
            t1.construct(cap, out, prov, True)
        self.assertEqual((cap, out, prov), before)
        return caught.exception.record

    def test_sub_nanometre_body_miss_is_reported_and_still_rejected(self):
        cap, out, prov = metric_case()
        actor = out['hypotheses']['objects'][0]
        source = actor['occupancy'][0]
        # A residual must not turn an undersized nominal BODY into coverage.
        actor['uncertainty_m'] = 10.
        source['alternatives'][0]['polygon_m'] = rect(17.6, 18.4-1e-9, -3.5, 10.)
        failure = self.reject_fixture(cap, out, prov)
        self.assertEqual((failure['code'], failure['category']),
                         ('BODY_ENVELOPE_CONFLICT', 'tool_infeasible'))
        details = failure['details']
        self.assertEqual(details['object_id'], actor['id'])
        self.assertEqual(details['source_id'], source['id'])
        self.assertEqual(details['scenario_id'], 'walk')
        self.assertEqual(details['source_interval_s'], [0., 5.])
        self.assertEqual(len(details['computed_initial_body_corners_m']), 4)
        self.assertEqual(len(details['outside_body_corners_m']), 2)
        self.assertGreater(details['maximum_corner_excess_m'], 0.)
        self.assertLess(details['maximum_corner_excess_m'], 1e-8)
        self.assertEqual(details['distance_unit'], 'm')
        self.assertFalse(details['geometry_modified'])

    def test_rounded_pi_is_not_silently_snapped_to_a_different_heading(self):
        cap, out, prov = metric_case(asynchronous=False)
        actor = out['hypotheses']['objects'][0]
        actor['body_geometry']['yaw_rad'] = 3.141593
        actor['occupancy'][0]['alternatives'][0]['polygon_m'] = rect(17.6,18.4,-3.5,10.)
        failure = self.reject_fixture(cap, out, prov)
        self.assertEqual(failure['code'], 'BODY_ENVELOPE_CONFLICT')
        self.assertGreater(failure['details']['maximum_corner_excess_m'], 0.)
        self.assertNotEqual(actor['body_geometry']['yaw_rad'], math.pi)

    def test_alignment_reports_negative_required_time_without_extending_premise(self):
        cap, out, prov = metric_case()
        out['hypotheses']['premises'][0]['start_s'] = 0.
        failure = self.reject_fixture(cap, out, prov)
        self.assertEqual((failure['code'], failure['category']),
                         ('PREMISE_SCOPE', 'interface_error'))
        details = failure['details']
        self.assertEqual(details['premise_id'], 'walk_premise')
        self.assertEqual(details['required_interval_s'], [-.2, 0])
        self.assertEqual(details['declared_interval_s'], [0., 5.])
        self.assertEqual(details['required_scenario_ids'], ['walk'])
        self.assertEqual(details['conflict_dimensions'], ['time_start'])

    def test_all_conflicting_scope_dimensions_are_exposed(self):
        registry = {'p':dict(object_ids=['different_actor'],
                            scenario_ids=['different_mode'],start_s=1.,end_s=2.)}
        before = deepcopy(registry)
        with self.assertRaises(Failure) as caught:
            _premises(['p'],registry,'actor',['mode'],0.,3.)
        self.assertEqual(caught.exception.record['details']['conflict_dimensions'],
                         ['object','scenario','time_start','time_end'])
        self.assertEqual(registry, before)

    def test_missing_adverse_branch_premise_remains_rejected(self):
        registry = {'p':dict(object_ids=['actor'],scenario_ids=['adopted'],
                            start_s=0.,end_s=2.)}
        with self.assertRaises(Failure) as caught:
            _premises(['p'],registry,'actor',['adopted','adverse'],0.,2.)
        details = caught.exception.record['details']
        self.assertEqual(details['scenario_id'], 'adverse')
        self.assertEqual(details['premise_ids'], ['p'])
        self.assertEqual(details['conflict_dimensions'], ['scenario'])
        # Fully covered modes still pass the identical scope guard.
        _premises(['p'],registry,'actor',['adopted'],0.,2.)


if __name__ == '__main__':
    unittest.main()
