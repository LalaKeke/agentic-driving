from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import httpx
from agentic.adapter import OnlineAgent
from agentic.contracts import Failure,digest
from agentic.fixtures import ScriptedAgent,capture,rect
from agentic.g2 import evaluate

class AdapterTests(unittest.TestCase):
    def run_adapter(self,handler,mode='json_schema',retries='0'):
        env=dict(DRIVING_API_BASE='https://example.invalid/v1',DRIVING_MODEL='configured-service-model',DRIVING_API_KEY='UNITTEST_SECRET_NEVER_LOG',DRIVING_OUTPUT_MODE=mode,DRIVING_RETRIES=retries)
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,env):
            cap=capture();p=Path(tmp)/'frame.png';p.write_bytes(b'\x89PNG\r\n\x1a\nTEST_TRANSPORT_ONLY_NOT_REAL_IMAGE');cap['observations'][0]['path']=str(p)
            agent=OnlineAgent(httpx.Client(transport=httpx.MockTransport(handler)))
            return agent.decide(cap)
    def test_image_structured_output_and_safe_provenance(self):
        def handle(req):
            body=json.loads(req.content);self.assertEqual(body['response_format']['type'],'json_schema')
            self.assertTrue(body['messages'][1]['content'][-1]['image_url']['url'].startswith('data:image/png;base64,'))
            output,_=ScriptedAgent().decide(capture())
            return httpx.Response(200,json=dict(model='returned-model',choices=[dict(finish_reason='stop',message=dict(content=json.dumps(output)))]))
        output,prov=self.run_adapter(handle)
        self.assertEqual(prov['source'],'mock_transport');self.assertEqual(prov['model_id'],'returned-model');self.assertNotIn('UNITTEST_SECRET',json.dumps(prov));self.assertEqual(prov['output_digest'],digest(output))
        # Mock transport verifies protocol only, never counts as a real model/G2 experiment.
    def test_capability_error_has_no_silent_fallback(self):
        calls=[]
        def handle(req):calls.append(1);return httpx.Response(400,text='UNITTEST_SECRET_NEVER_LOG')
        with self.assertRaises(Failure) as ctx:self.run_adapter(handle)
        self.assertEqual(len(calls),1);self.assertNotIn('UNITTEST_SECRET',str(ctx.exception.record));self.assertEqual(ctx.exception.record['code'],'API_HTTP')
    def test_limited_retry_and_timeout(self):
        calls=[]
        def handle(req):calls.append(1);raise httpx.ReadTimeout('UNITTEST_SECRET_NEVER_LOG')
        with patch('agentic.adapter.time.sleep'),self.assertRaises(Failure) as ctx:self.run_adapter(handle,retries='2')
        self.assertEqual(len(calls),3);self.assertEqual(ctx.exception.record['code'],'API_TRANSPORT');self.assertNotIn('UNITTEST_SECRET',str(ctx.exception.record))
    def test_invalid_model_output_rejected(self):
        def handle(req):return httpx.Response(200,json=dict(choices=[dict(finish_reason='stop',message=dict(content='{}'))]))
        with self.assertRaises(Failure) as ctx:self.run_adapter(handle)
        self.assertEqual(ctx.exception.record['code'],'SCHEMA_ERROR')

class G2Tests(unittest.TestCase):
    def test_synthetic_cannot_count_as_g2(self):
        output,prov=ScriptedAgent().decide(capture())
        with self.assertRaises(Failure):evaluate([dict(sample_id='x',output=output,provenance=prov)],{},dict(distance_edges_m=[10,20],evaluation_horizon_s=.2))
    def test_metric_computation_on_unit_test_values_only(self):
        output,prov=ScriptedAgent().decide(capture())
        # Explicit numerical UNIT TEST. This is never saved as real experiment evidence.
        prov.update(source='online_model',image_sha256={'fixture':'unit-test-hash'})
        gt=dict(samples={'unit':dict(image_sha256=prov['image_sha256'],reference_time_id='t100.000',coordinate_frame='ego_at_reference',road_polygon_m=rect(-10,50,-6,6),scene_type='unit_test',model_object_ids={'truth_actor':'pedestrian'},objects=[dict(id='truth_actor',position_m=[18.,-3.],velocity_mps=[0.,2.5],occlusion='unit_test',occupancy=[dict(start_s=0.,end_s=.2,polygon_m=rect(17.6,18.4,-3.5,-2.))])])})
        result=evaluate([dict(sample_id='unit',output=output,provenance=prov)]*2,gt,dict(distance_edges_m=[10,20],evaluation_horizon_s=.2))
        self.assertEqual(result['metrics']['position_error_m']['mean'],0);self.assertAlmostEqual(result['metrics']['minimum_interval_area_coverage']['mean'],1);self.assertFalse(result['thresholds_applied']);self.assertEqual(result['repeated_call_stability'][0]['calls'],2)

    def test_truth_must_cover_evaluation_horizon(self):
        output,prov=ScriptedAgent().decide(capture())
        prov.update(source='online_model',image_sha256={'fixture':'unit-test-hash'})
        gt=dict(samples={'unit':dict(image_sha256=prov['image_sha256'],reference_time_id='t100.000',coordinate_frame='ego_at_reference',road_polygon_m=rect(-10,50,-6,6),scene_type='unit_test',model_object_ids={},objects=[dict(id='unmatched',position_m=[18.,-3.],velocity_mps=None,occlusion='unit_test',occupancy=[])])})
        with self.assertRaises(Failure) as ctx:evaluate([dict(sample_id='unit',output=output,provenance=prov)],gt,dict(distance_edges_m=[10,20],evaluation_horizon_s=5.))
        self.assertEqual(ctx.exception.record['code'],'G2_TRUTH_COVERAGE')
