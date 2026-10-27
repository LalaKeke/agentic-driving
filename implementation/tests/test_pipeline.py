from copy import deepcopy
import json
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from driving_tools import (ROOT, STORE, Draft7Validator, braking_trajectory, call_tool,
                           make_request, pipeline, validate_document)


class ToolchainTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.initial=json.loads((ROOT/'examples/pedestrian_crossing.request.json').read_text(encoding='utf-8'))
        cls.normal=pipeline(cls.initial,allow_synthetic=True)

    def request(self,tool,**changes):
        r=self.normal
        payload={k:deepcopy(r[k]) for k in ('scene','decision','corridor','trajectory')}
        allowed={'T2':('scene','decision'),'T3':('scene','decision','corridor'),
                 'T4':('scene','decision','corridor','trajectory')}[tool]
        payload={k:payload[k] for k in allowed}
        if tool=='T2':payload['safety_margin_m']=1.
        payload.update(changes)
        return make_request(tool,payload,'test')

    def invoke(self,request):
        response=call_tool(request,allow_synthetic=True)
        validate_document(request['tool']+'.schema.json',response)
        return response

    def test_all_schema_definitions_are_well_formed(self):
        for schema in STORE.values():Draft7Validator.check_schema(schema)

    def test_normal_pipeline_all_messages_validate(self):
        for msg in self.normal['trace']:validate_document(msg['tool']+'.schema.json',msg)
        validate_document('shared_scene.schema.json',self.normal['scene'])
        self.assertEqual(self.normal['verification']['verdict'],'PASS')
        self.assertAlmostEqual(self.normal['trajectory']['points'][-1]['x_m'],12.)
        self.assertAlmostEqual(self.normal['trajectory']['points'][20]['speed_mps'],0.)
        self.assertEqual(self.normal['verification']['released_prefix_s'],.2)

    def test_synthetic_fixture_requires_explicit_test_mode(self):
        r=call_tool(self.initial)
        self.assertEqual(r['error']['code'],'SYNTHETIC_INPUT_DISABLED')

    def test_missing_future_occupancy_is_rejected(self):
        r=deepcopy(self.initial)
        r['payload']['model_hypotheses']['objects'][0]['occupancy_intervals'].pop(8)
        self.assertEqual(self.invoke(r)['error']['code'],'OCCUPANCY_COVERAGE_GAP')

    def test_unknown_evidence_is_rejected(self):
        r=deepcopy(self.initial)
        r['payload']['model_hypotheses']['objects'][0]['evidence_frame_ids']=['missing']
        self.assertEqual(self.invoke(r)['error']['code'],'MISSING_EVIDENCE')

    def test_reference_time_mismatch_is_rejected(self):
        r=deepcopy(self.initial)
        r['payload']['model_hypotheses']['observed_at_s']=99.
        self.assertEqual(self.invoke(r)['error']['code'],'HYPOTHESIS_TIME_MISMATCH')

    def test_nonfinite_number_is_rejected(self):
        r=deepcopy(self.initial);r['payload']['ego']['speed_mps']=math.nan
        self.assertEqual(self.invoke(r)['error']['code'],'NONFINITE_NUMBER')

    def test_unknown_fields_and_negative_uncertainty_are_rejected(self):
        for field,value in [('hallucinated_field',1),('road_uncertainty_m',-1)]:
            r=deepcopy(self.initial);r['payload']['model_hypotheses'][field]=value
            self.assertEqual(self.invoke(r)['error']['code'],'SCHEMA_ERROR')

    def test_old_decision_version_is_rejected(self):
        r=self.request('T2');r['payload']['decision']['revision']=1
        self.assertEqual(self.invoke(r)['error']['code'],'SCENE_VERSION_MISMATCH')

    def test_old_corridor_version_is_rejected(self):
        r=self.request('T3');r['payload']['corridor']['metadata']['revision']=1
        self.assertEqual(self.invoke(r)['error']['code'],'SCENE_VERSION_MISMATCH')

    def test_expired_decision_is_rejected(self):
        r=self.request('T2');r['payload']['decision']['valid_until_s']=104.
        self.assertEqual(self.invoke(r)['error']['code'],'DECISION_EXPIRED')

    def test_unknown_region_blocks_planning(self):
        r=self.request('T2');r['payload']['scene']['unknown_regions']=[{
            'xmin_m':-5.,'xmax_m':40.,'ymin_m':-2.,'ymax_m':2.}]
        self.assertEqual(self.invoke(r)['error']['code'],'CORRIDOR_EMPTY')

    def test_insufficient_braking_does_not_trigger_behavior_switch(self):
        r=self.request('T3');r['payload']['scene']['vehicle']['max_brake_mps2']=1.
        self.assertEqual(self.invoke(r)['error']['code'],'NO_FEASIBLE_TRAJECTORY')
        self.assertEqual(r['payload']['decision']['behavior'],'stop_yield')

    def test_lateral_maneuver_is_explicitly_unsupported(self):
        r=self.request('T3');r['payload']['decision']['lane_center_y_m']=.1
        self.assertEqual(self.invoke(r)['error']['code'],'UNSUPPORTED_LATERAL_MOTION')

    def test_independent_verifier_rejects_injected_coasting(self):
        r=self.request('T4',trajectory=braking_trajectory(self.normal['scene'],0.))
        result=self.invoke(r)['payload']['verification']
        self.assertEqual(result['verdict'],'FAIL')
        self.assertEqual(result['released_prefix_s'],0.)
        codes={v['code'] for v in result['violations']}
        self.assertTrue({'OBSTACLE_CLEARANCE','STOP_BOUNDARY','STOP_DEADLINE'} <= codes)

    def test_verifier_checks_raw_scene_even_if_corridor_is_tampered(self):
        r=self.request('T4',trajectory=braking_trajectory(self.normal['scene'],0.))
        for cell in r['payload']['corridor']['cells']:
            cell['allowed_rectangles']=[{'xmin_m':-2.,'xmax_m':35.,'ymin_m':-.2,'ymax_m':.2}]
        result=self.invoke(r)['payload']['verification']
        self.assertIn('OBSTACLE_CLEARANCE',{v['code'] for v in result['violations']})

    def test_t4_detects_dynamic_discontinuity(self):
        r=self.request('T4');r['payload']['trajectory']['points'][4]['x_m']+=.1
        result=self.invoke(r)['payload']['verification']
        self.assertIn('DYNAMICS_RESIDUAL',{v['code'] for v in result['violations']})

    def test_interval_occupancy_cannot_be_ignored(self):
        r=self.request('T4')
        r['payload']['scene']['objects'][0]['occupancy_intervals'][5]['bounds']={
            'xmin_m':7.7,'xmax_m':7.8,'ymin_m':-4.,'ymax_m':4.}
        result=self.invoke(r)['payload']['verification']
        self.assertIn('OBSTACLE_CLEARANCE',{v['code'] for v in result['violations']})

    def test_feedback_revision_remains_agent_owned(self):
        revised=deepcopy(self.initial)
        revised['payload']['metadata']['revision']=1
        revised['payload']['decision']['revision']=1
        revised['payload']['decision']['stop_before_x_m']=10.
        result=pipeline(revised,allow_synthetic=True)
        self.assertEqual(result['verification']['verdict'],'PASS')
        self.assertAlmostEqual(result['trajectory']['points'][-1]['x_m'],9.)
        self.assertEqual(result['decision'],revised['payload']['decision'])

    def test_deadline_between_sampling_points_is_checked(self):
        r=self.request('T4');r['payload']['decision']['stop_deadline_s']=3.9
        result=self.invoke(r)['payload']['verification']
        self.assertIn('STOP_DEADLINE',{v['code'] for v in result['violations']})

    def test_new_observation_resume_pipeline(self):
        request=json.loads((ROOT/'examples/pedestrian_clear_resume.request.json').read_text(encoding='utf-8'))
        result=pipeline(request,allow_synthetic=True)
        self.assertEqual(result['verification']['verdict'],'PASS')
        self.assertGreater(result['trajectory']['points'][-1]['speed_mps'],0.)
        self.assertEqual(result['decision']['behavior'],'resume')
        self.assertEqual(result['scene']['ego']['x_m'],0.)
        self.assertEqual(result['scene']['objects'][0]['position']['x_m'],6.)
        for message in result['trace']:validate_document(message['tool']+'.schema.json',message)

    def test_duplicate_object_identifier_is_rejected(self):
        r=deepcopy(self.initial)
        r['payload']['model_hypotheses']['objects'].append(deepcopy(r['payload']['model_hypotheses']['objects'][0]))
        self.assertEqual(self.invoke(r)['error']['code'],'DUPLICATE_ID')

    def test_invalid_rectangle_is_rejected(self):
        r=deepcopy(self.initial)
        r['payload']['model_hypotheses']['road_bounds']['xmin_m']=41.
        self.assertEqual(self.invoke(r)['error']['code'],'INVALID_GEOMETRY')

    def test_asymmetric_vehicle_configuration_space(self):
        r=self.request('T2')
        r['payload']['scene']['vehicle']['front_m']=3.
        r['payload']['scene']['vehicle']['rear_m']=1.
        r['payload']['decision']['stop_before_x_m']=30.
        result=self.invoke(r)['payload']['corridor']
        cells=result['cells'][10]['allowed_rectangles']
        self.assertEqual(len(cells),2)
        self.assertAlmostEqual(cells[0]['xmax_m'],13.4)
        self.assertAlmostEqual(cells[1]['xmin_m'],20.6)
        self.assertAlmostEqual(cells[0]['xmin_m'],-3.9)

    def test_error_response_has_valid_fallback_request_id(self):
        r=deepcopy(self.initial);r['request_id']=123
        result=self.invoke(r)
        self.assertEqual(result['status'],'error')
        self.assertEqual(result['request_id'],'invalid-request')


if __name__=='__main__':unittest.main(verbosity=2)
