"""Numerical unit tests only; no real model results are constructed here."""
from copy import deepcopy
import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agentic.fixtures import capture,ScriptedAgent,rect
from agentic.contracts import digest
from agentic.g2 import evaluate

class G2AvailabilityTests(unittest.TestCase):
    def inputs(self):
        out,prov=ScriptedAgent().decide(capture())
        prov.update(source='online_model',image_sha256={'unit':'numeric-only'},output_digest=digest(out))
        actor=dict(id='truth_actor',position_m=[18.,-3.],velocity_mps=[0.,2.5],occlusion='unit',occupancy=[dict(start_s=k*.2,end_s=(k+1)*.2,polygon_m=rect(17.6,18.4,-3.5,-2)) for k in range(2)])
        gt=dict(image_sha256=prov['image_sha256'],reference_time_id='t100.000',coordinate_frame='ego_at_reference',road_polygon_m=rect(-10,50,-6,6),scene_type='unit',model_object_ids={'truth_actor':'pedestrian'},objects=[actor])
        return dict(sample_id='unit',record_id='r1',output=out,provenance=prov),dict(samples={'unit':gt}),dict(distance_edges_m=[10,20],evaluation_horizon_s=.4)
    def test_unknown_uncertainty_is_unavailable_not_zero_accuracy(self):
        record,truth,config=self.inputs();record['output']['hypotheses']['objects'][0]['uncertainty_m']=None
        record['provenance']['output_digest']=digest(record['output'])
        row=evaluate([record],truth,config)['rows'][0]
        self.assertIsNone(row['minimum_interval_area_coverage']);self.assertEqual(row['occupancy_status'],'unknown_uncertainty');self.assertEqual(row['evaluable_intervals'],0)
    def test_partial_time_coverage_is_reported_separately(self):
        record,truth,config=self.inputs();record['output']['hypotheses']['objects'][0]['occupancy']=record['output']['hypotheses']['objects'][0]['occupancy'][:1]
        record['provenance']['output_digest']=digest(record['output'])
        row=evaluate([record],truth,config)['rows'][0]
        self.assertIsNone(row['minimum_interval_area_coverage']);self.assertEqual(row['expected_intervals'],2);self.assertEqual(row['supplied_time_intervals'],1);self.assertEqual(row['evaluable_intervals'],1)
        self.assertEqual(row['occupancy_status'],'partial_or_missing_time_coverage')
    def test_reviewed_per_record_association_supports_changing_model_ids(self):
        record,truth,config=self.inputs();truth['samples']['unit']['model_object_ids']={};truth['record_associations']={'r1':{'truth_actor':'pedestrian'}}
        row=evaluate([record],truth,config)['rows'][0]
        self.assertTrue(row['matched']);self.assertEqual(row['position_error_m'],0)

    def test_explicit_unknown_occupancy_has_no_numeric_coverage(self):
        record,truth,config=self.inputs();record['output']['hypotheses']['objects'][0]['occupancy']=None
        record['provenance'].update(agent_output_contract='2.1.0',output_digest=digest(record['output']))
        row=evaluate([record],truth,config)['rows'][0]
        self.assertEqual(row['occupancy_status'],'unknown_occupancy');self.assertIsNone(row['minimum_interval_area_coverage']);self.assertEqual(row['supplied_time_intervals'],0)

    def test_protocol_versions_cannot_be_silently_pooled(self):
        record,truth,config=self.inputs();other=deepcopy(record);other['record_id']='r2';other['provenance']['agent_output_contract']='2.1.0'
        from agentic.contracts import Failure
        with self.assertRaises(Failure) as ctx:evaluate([record,other],truth,config)
        self.assertEqual(ctx.exception.record['code'],'G2_MIXED_CONTRACT')

    def test_disclaimed_placeholder_keeps_raw_overlap_but_not_forecast_score(self):
        record,truth,config=self.inputs();truth['unsupported_occupancy']={'r1':['truth_actor']}
        row=evaluate([record],truth,config)['rows'][0]
        self.assertIsNone(row['minimum_interval_area_coverage']);self.assertEqual(row['evaluable_intervals'],0)
        self.assertEqual(row['occupancy_status'],'model_disclaimed_placeholder');self.assertAlmostEqual(row['raw_minimum_interval_area_overlap'],.8)
        self.assertGreater(row['mean_raw_envelope_area_ratio'],0)
    def test_reported_position_extent_is_checked_without_probability_claim(self):
        record,truth,config=self.inputs();truth['samples']['unit']['objects'][0]['position_m']=[17.,-3.]
        row=evaluate([record],truth,config)['rows'][0]
        self.assertFalse(row['position_within_reported_extent'])
