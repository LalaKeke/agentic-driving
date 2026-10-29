"""Native source timing regressions; all inputs are authored software fixtures."""
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agentic import t1
from agentic.contracts import Failure, digest, grid
from agentic.fixtures import rect
from agentic.fixtures_v3 import metric_case, add_adverse_scenario
from agentic.geometry import obstacles, obstacles_native_cells, polygon


def native_case(split=True):
    cap, out, prov = metric_case(asynchronous=False)
    cap['metadata'].update(horizon_s=1., valid_until_s=101.)
    out['decision'].update(behavior='hold_lane', stop=None, yield_to=[])
    add_adverse_scenario(out)
    for p in out['hypotheses']['premises']:
        p['end_s'] = 1.
    actor = out['hypotheses']['objects'][0]
    template = actor['occupancy'][0]
    if split:
        actor['occupancy'] = []
        for i in range(5):
            source = deepcopy(template)
            source.update(id=f'authored_source_{i}',
                          start_s=float(Decimal(i)/5),
                          end_s=float(Decimal(i+1)/5))
            source['alternatives'][0]['polygon_m'] = rect(17.6, 18.4+.05*i, -3.5, 10.)
            source['alternatives'][1]['polygon_m'] = rect(17.6, 18.7+.05*i, -3.5, 10.)
            actor['occupancy'].append(source)
    else:
        template['end_s'] = 1.
    prov['output_digest'] = digest(out)
    return cap, out, prov


def construct(cap, out, prov):
    prov = deepcopy(prov)
    prov['output_digest'] = digest(out)
    return t1.construct(cap, out, prov, True)


class NativeCellCoverageTests(unittest.TestCase):
    def test_returns_original_source_sets_and_one_total_residual_without_mutation(self):
        cap, out, prov = native_case()
        scene = construct(cap, out, prov)
        before = deepcopy(scene)
        actor = out['hypotheses']['objects'][0]
        selected = list(obstacles_native_cells(scene, 1))
        self.assertEqual(len(selected), 2)
        for (oid, shape, residual), alt in zip(selected, actor['occupancy'][1]['alternatives']):
            self.assertEqual(oid, actor['id'])
            self.assertTrue(shape.equals_exact(polygon(alt['polygon_m']), 0.))
            self.assertEqual(residual, actor['uncertainty_m'])
        self.assertEqual(scene, before)

    def test_decimal_native_boundary_is_not_positive_overlap_from_grid_roundoff(self):
        cap, out, prov = native_case()
        scene = construct(cap, out, prov)
        self.assertGreater(grid(scene['metadata'])[3], .6)
        # [.4,.6] must retain source 2, not source 3 beginning exactly .6.
        current = list(obstacles_native_cells(scene, 2))
        expected = out['hypotheses']['objects'][0]['occupancy'][2]['alternatives']
        self.assertEqual(len(current), len(expected))
        for (_, got, _), alt in zip(current, expected):
            self.assertTrue(got.equals_exact(polygon(alt['polygon_m']), 0.))
        # The original lowering remains available and deliberately conservative.
        self.assertGreater(len(list(obstacles(scene, 2))), len(current))

    def test_both_closed_cells_retain_all_scenarios_at_shared_boundary(self):
        cap, out, prov = native_case()
        scene = construct(cap, out, prov)
        actor = out['hypotheses']['objects'][0]
        sids = {s['id'] for s in actor['scenarios']}
        boundary = Decimal('.6')
        for k in (2, 3):
            source = actor['occupancy'][k]
            self.assertLessEqual(Decimal(str(source['start_s'])), boundary)
            self.assertGreaterEqual(Decimal(str(source['end_s'])), boundary)
            self.assertEqual({a['scenario_id'] for a in source['alternatives']}, sids)
            selected = list(obstacles_native_cells(scene, k))
            self.assertEqual(len(selected), len(sids))
            for (_, shape, _), alt in zip(selected, source['alternatives']):
                self.assertTrue(shape.equals_exact(polygon(alt['polygon_m']), 0.))

    def test_whole_interval_coarse_source_is_kept_intact_in_every_cell(self):
        cap, out, prov = native_case(split=False)
        scene = construct(cap, out, prov)
        alternatives = out['hypotheses']['objects'][0]['occupancy'][0]['alternatives']
        for k in range(5):
            selected = list(obstacles_native_cells(scene, k))
            self.assertEqual(len(selected), len(alternatives))
            for (_, shape, _), alt in zip(selected, alternatives):
                self.assertTrue(shape.equals_exact(polygon(alt['polygon_m']), 0.))

    def test_unknown_region_is_retained_as_declared(self):
        cap, out, prov = native_case()
        region = deepcopy(out['hypotheses']['road'])
        region['polygon_m'] = rect(30, 31, 5, 6)
        region['uncertainty_m'] = .73
        out['hypotheses']['unknown_regions'] = [region]
        scene = construct(cap, out, prov)
        selected = list(obstacles_native_cells(scene, 0))
        oid, shape, residual = selected[-1]
        self.assertEqual(oid, 'unknown_region')
        self.assertTrue(shape.equals_exact(polygon(region['polygon_m']), 0.))
        self.assertEqual(residual, .73)

    def test_t1_still_rejects_null_positive_gap_and_placeholder(self):
        for kind, expected in [('null', 'OCCUPANCY_UNKNOWN'),
                               ('gap', 'OCCUPANCY_GAP'),
                               ('placeholder', 'PLACEHOLDER_OCCUPANCY'),
                               ('adverse_missing', 'OCCUPANCY_GAP')]:
            with self.subTest(kind=kind):
                cap, out, prov = native_case()
                actor = out['hypotheses']['objects'][0]
                if kind == 'null':
                    actor['occupancy'] = None
                elif kind == 'gap':
                    actor['occupancy'][1]['start_s'] += 1e-12
                elif kind == 'placeholder':
                    actor['occupancy'][0]['status'] = 'placeholder'
                else:
                    for source in actor['occupancy']:
                        source['alternatives'] = [a for a in source['alternatives'] if a['scenario_id'] != 'adverse']
                        source['premise_ids'].remove('adverse_premise')
                with self.assertRaises(Failure) as ctx:
                    construct(cap, out, prov)
                self.assertEqual(ctx.exception.record['code'], expected)


if __name__ == '__main__':
    unittest.main()
