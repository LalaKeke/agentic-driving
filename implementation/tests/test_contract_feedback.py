"""Pure scope repair diagnostics; authored controls, no online model or GT."""
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'implementation'),str(ROOT/'implementation/experiments'),str(Path(__file__).resolve().parent)]
from agentic import t1
from agentic.contracts import Failure,digest
from agentic.contract_feedback import augment_feedback
from test_seed_candidates import authored_case
import test_openai_clip as clips


def resolve(value,pointer):
    for key in pointer.lstrip('/').split('/'):
        key=key.replace('~1','/').replace('~0','~')
        value=value[int(key)] if isinstance(value,list) else value[key]
    return value


class ContractFeedbackTests(unittest.TestCase):
    def premise_case(self):
        pid='rear_alignment_motion'
        premise=dict(id=pid,object_ids=['rear_car'],scenario_ids=['rear_follow','rear_adverse'],
                     start_s=-.025,end_s=0,space_polygon_m=None,statement='AUTHORED past actor bound')
        evidence=dict(frame_ids=['back'],time_ids=['t99.975'])
        output=dict(hypotheses=dict(premises=[premise],objects=[dict(id='rear_car',**evidence,
            alignment=dict(premise_ids=[pid]),
            scenarios=[dict(id='rear_follow',role='adopted',premise_ids=[pid]),
                       dict(id='rear_adverse',role='reasonable_adverse',premise_ids=[pid])],
            occupancy=[dict(start_s=0,end_s=2,premise_ids=[pid],id='source',**evidence,
                            alternatives=[dict(scenario_id='rear_follow'),dict(scenario_id='rear_adverse')])])]))
        capture=dict(metadata=dict(reference_time_s=100.,horizon_s=2.),observations=[
            dict(id='back',time_id='t99.975',capture_time_s=99.975,camera_id='CAM_BACK')])
        failure=dict(code='PREMISE_SCOPE',details=dict(premise_id=pid,object_id='rear_car',
            required_scenario_ids=['rear_follow'],required_interval_s=[0,2.],declared_interval_s=[-.025,0]))
        return failure,output,capture

    def test_actual_reference_paths_intervals_evidence_and_adverse_branch_retained(self):
        failure,output,capture=self.premise_case();before=deepcopy((failure,output,capture))
        with patch('builtins.open',side_effect=AssertionError('Pure diagnostic may not read any image/truth file')):
            result=augment_feedback(failure,output,capture)
        p=result['offending_premises'][0]
        self.assertTrue(p['past_only_misbound_to_future'])
        self.assertEqual(p['json_pointer'],'/hypotheses/premises/0')
        self.assertEqual(p['declared_interval_s'],[-.025,0]);self.assertEqual(p['required_interval_s'],[0,2.])
        self.assertEqual(resolve(output,p['json_pointer']),p['raw_value'])
        uses={r['json_pointer']:r for r in p['reference_uses']}
        self.assertEqual(len(uses),4)
        self.assertIn('/hypotheses/objects/0/alignment/premise_ids/0',uses)
        self.assertIn('/hypotheses/objects/0/scenarios/1/premise_ids/0',uses)
        self.assertIn('/hypotheses/objects/0/occupancy/0/premise_ids/0',uses)
        for pointer,item in uses.items():self.assertEqual(resolve(output,pointer),item['raw_value'])
        alignment=uses['/hypotheses/objects/0/alignment/premise_ids/0']
        self.assertEqual(alignment['required_interval_s'],[-.025,0])
        self.assertEqual(alignment['evidence']['resolved_pairs'][0]['capture_pointer'],'/observations/0')
        adverse=uses['/hypotheses/objects/0/scenarios/1/premise_ids/0']
        self.assertEqual(adverse['scenario_role'],'reasonable_adverse')
        self.assertEqual(adverse['required_interval_s'],[0,2.])
        self.assertFalse(result['authorizes_planning']);self.assertFalse(result['model_data_modified'])
        self.assertEqual((failure,output,capture),before)

    def test_prototype_scope_reports_original_polygons_and_outside_vertices(self):
        output=dict(hypotheses=dict(road=dict(polygon_m=[[-4,-4],[14,-7],[14,6],[-4,2.7]],
            frame_ids=['front'],time_ids=['t100']),premises=[dict(id='prototype_local_scope',
            start_s=0,end_s=2,space_polygon_m=[[-4,-4],[14,-4],[14,4],[-4,4]],statement='AUTHORED domain')]))
        capture=dict(metadata=dict(horizon_s=2),observations=[dict(id='front',time_id='t100',capture_time_s=100.,camera_id='CAM_FRONT')])
        before=deepcopy(output);result=augment_feedback(dict(code='PROTOTYPE_SCOPE'),output,capture)
        compared=result['geometry_comparisons'][0]
        self.assertFalse(compared['road_within_domain'])
        self.assertEqual(compared['road_bounds_m'],[-4.,-7.,14.,6.])
        self.assertEqual(compared['domain_bounds_m'],[-4.,-4.,14.,4.])
        self.assertAlmostEqual(compared['road_outside_area_m2'],37.90909090909091)
        vertices=compared['outside_road_vertices']
        self.assertEqual([r['json_pointer'] for r in vertices],['/hypotheses/road/polygon_m/1','/hypotheses/road/polygon_m/2'])
        self.assertEqual([r['distance_to_declared_domain_m'] for r in vertices],[3.,2.])
        for item in vertices:self.assertEqual(resolve(output,item['json_pointer']),item['raw_value'])
        self.assertEqual(output,before)

    def test_null_and_missing_domain_are_diagnosed_without_replacement_geometry(self):
        output=dict(hypotheses=dict(road=None,premises=[]))
        result=augment_feedback(dict(code='PROTOTYPE_SCOPE'),output,dict(metadata=dict(horizon_s=2)))
        self.assertIsNone(result['road']['raw_value'])
        self.assertEqual(result['prototype_domains'],[]);self.assertEqual(result['geometry_comparisons'],[])
        self.assertEqual(output,dict(hypotheses=dict(road=None,premises=[])))
        self.assertFalse(result['authorizes_planning'])

    def test_other_errors_and_unavailable_structure_do_not_fill_native_data(self):
        output=dict(hypotheses=dict(road=None,objects=[dict(occupancy=None)],unbounded_unknown=True))
        before=deepcopy(output)
        for code in ('OCCUPANCY_UNKNOWN','UNBOUNDED_UNKNOWN','MISSING_SCALE','OPTIMIZATION_FAILED'):
            self.assertIsNone(augment_feedback(dict(code=code),output,{}))
        self.assertIsNone(augment_feedback(dict(code='PREMISE_SCOPE'),None,{}))
        self.assertEqual(output,before)

    def test_existing_t1_guards_reject_the_same_authored_inputs_after_diagnosis(self):
        for kind,expected in [('road','PROTOTYPE_SCOPE'),('premise','PREMISE_SCOPE')]:
            cap,out,prov=authored_case(speed=3.,half_road=10.)
            if kind=='road':
                next(p for p in out['hypotheses']['premises'] if p['id']=='prototype_local_scope')['space_polygon_m']=[[-1,-1],[1,-1],[1,1],[-1,1]]
            else:
                obj=out['hypotheses']['objects'][0];sid=obj['scenarios'][0]['id']
                out['hypotheses']['premises'].append(dict(id='past_only',object_ids=[obj['id']],scenario_ids=[sid],
                    start_s=-.025,end_s=0,space_polygon_m=None,statement='AUTHORED past-only hypothesis'))
                obj['scenarios'][0]['premise_ids'].append('past_only')
            prov['output_digest']=digest(out);before=deepcopy((cap,out,prov))
            with self.assertRaises(Failure) as first:t1.construct(cap,out,prov,True)
            self.assertEqual(first.exception.record['code'],expected)
            augment_feedback(first.exception.record,out,cap)
            with self.assertRaises(Failure) as second:t1.construct(cap,out,prov,True)
            self.assertEqual(second.exception.record,first.exception.record)
            self.assertEqual((cap,out,prov),before)

    def test_clip_adds_diagnostic_only_for_v3_semantic_feedback(self):
        for profile in ('bounded_seed_candidates_v2','bounded_seed_candidates_v3'):
            with self.subTest(profile=profile),tempfile.TemporaryDirectory(prefix='mock-contract-feedback-') as temp:
                root=Path(temp);case=clips.ClipTests();path=case.prepare(root)
                plan=json.loads(path.read_text());plan['planner_profile']=profile;path.write_text(json.dumps(plan))
                fake,instances=case.fake();calls=[]
                def physical(*args,**kwargs):
                    calls.append(1)
                    return (None,dict(code='PROTOTYPE_SCOPE'),dict(T1='FAIL')) if len(calls)==1 else (dict(prototype_conditional_success=True),None,dict(T1='PASS',EXECUTION='PASS'))
                with patch.dict(os.environ,DRIVING_MODEL='authored-mock-model'),patch.object(clips.runner,'OpenAIResponsesAgent',fake),patch.object(clips.runner,'physical',physical),patch.object(clips.runner,'augment_feedback',return_value={'authored_diagnostic':True}) as diagnostic:
                    self.assertEqual(clips.runner.run(path),0)
                feedback=instances[0].calls[1][1]
                if profile.endswith('_v3'):
                    diagnostic.assert_called_once();self.assertEqual(feedback['contract_repair_diagnostics'],{'authored_diagnostic':True})
                else:
                    diagnostic.assert_not_called()
                    self.assertEqual(set(feedback),{'failure','previous_native_output','all_initial_body_envelope_conflicts','concrete_repair_request'})
                self.assertEqual(feedback['previous_native_output'],{'authored':True})


if __name__=='__main__':unittest.main()
