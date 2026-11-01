"""Offline diagnostic counterexamples; these never count as online trials."""
from copy import deepcopy
from pathlib import Path
import unittest
from unittest.mock import patch

from agentic.contracts import Failure, digest
from agentic.fixtures_v3 import metric_case, add_adverse_scenario
from agentic.metric_contract import construct
from agentic.metric_diagnostics import diagnose_metric_output


class MetricDiagnosticsTests(unittest.TestCase):
    def audit(self, mutate=None, **kwargs):
        cap,out,prov=metric_case()
        if mutate:mutate(cap,out,prov)
        prov['output_digest']=digest(out)
        return cap,out,prov,diagnose_metric_output(cap,out,prov,allow_synthetic=True,**kwargs)

    @staticmethod
    def failed(report):
        return [r for r in report['checks'] if r['status']=='FAIL']

    def test_complete_async_fixture_has_no_false_failures_or_network(self):
        with patch('socket.socket.connect',side_effect=AssertionError('network forbidden')):
            cap,out,prov,report=self.audit()
        self.assertFalse(self.failed(report))
        self.assertNotIn('NOT_EVALUABLE',report['summary']['statuses'])
        self.assertFalse(report['authorizes_planning'])
        self.assertEqual(report['new_model_calls'],0)
        self.assertEqual(report['additional_T1_acceptance_runs'],0)

    def test_reasonable_adverse_branch_is_not_false_scope_failure(self):
        _,_,_,report=self.audit(lambda c,o,p:add_adverse_scenario(o,invade=True))
        # T1 may accept a collision-causing envelope; T2/T4 remain responsible
        # for collision rejection. Diagnostics must not invent a T1 failure.
        self.assertFalse(self.failed(report))

    def test_first_scale_rejection_does_not_mask_other_independent_fields(self):
        def mutate(c,o,p):
            h=o['hypotheses'];h['scale_basis']='unknown';h['metric_scale_support']=None
            h['road']=None;h['unbounded_unknown']=True
            actor=h['objects'][0];actor['occupancy']=None;actor['uncertainty_m']=None
        first=dict(code='MISSING_SCALE',message='actual original rejection',category='tool_infeasible',details={})
        _,out,_,report=self.audit(mutate,first_failure=first)
        self.assertEqual(report['original_T1_first_failure'],first)
        codes={r['code'] for r in self.failed(report)}
        self.assertTrue({'MISSING_SCALE','MISSING_SCALE_SUPPORT','UNBOUNDED_UNKNOWN',
                         'OCCUPANCY_UNKNOWN','UNKNOWN_UNCERTAINTY'}<=codes)
        occ=next(r for r in self.failed(report) if r['code']=='OCCUPANCY_UNKNOWN')
        self.assertEqual(occ['json_pointer'],'/hypotheses/objects/0/occupancy')
        self.assertIsNone(occ['raw_value'])
        self.assertTrue(occ['raw_present'])
        self.assertEqual(occ['object_id'],out['hypotheses']['objects'][0]['id'])
        self.assertTrue(any(r['status']=='NOT_EVALUABLE' and r['code']=='OCCUPANCY_GAP'
                            for r in report['checks']))

    def test_original_documents_and_failure_record_unchanged(self):
        cap,out,prov=metric_case()
        old=deepcopy((cap,out,prov))
        failure=dict(code='X',details={'nested':[1]})
        report=diagnose_metric_output(cap,out,prov,first_failure=failure,allow_synthetic=True)
        report['checks'][0]['raw_value'].clear()
        report['original_T1_first_failure']['details']['nested'].append(2)
        self.assertEqual((cap,out,prov),old)
        self.assertEqual(failure['details']['nested'],[1])

    def test_schema_reports_all_errors_and_wrong_velocity_definition(self):
        def mutate(c,o,p):
            o['measurement_semantics']['velocity_semantics']='relative_to_ego'
            o['measurement_semantics']['actor_reference_point']='rear_axle'
            o['coverage_profile']='archived_exp1'
        _,_,_,report=self.audit(mutate)
        schema=[r for r in self.failed(report) if r['code']=='SCHEMA_ERROR']
        self.assertEqual(len(schema),3)
        self.assertTrue(all(r['contract_clause_ref']['schema_pointer'] for r in schema))

    def test_false_actor_observation_stale_state_and_missing_motion_bound(self):
        def mutate(c,o,p):
            a=o['hypotheses']['objects'][0]
            a['state_time_id']='old_state'
            a['alignment']['motion_bound']=None
            a['alignment']['evidence_positions'][0]['observed_actor']=False
        _,_,_,report=self.audit(mutate)
        codes={r['code'] for r in self.failed(report)}
        self.assertTrue({'STATE_TIME','MOTION_BOUND_MISSING','ACTOR_NOT_OBSERVED'}<=codes)
        self.assertTrue(any(r['status']=='NOT_EVALUABLE' and r['code']=='ALIGNMENT_CONFLICT'
                            for r in report['checks']))

    def test_coarse_whole_interval_valid_but_positive_gap_detected(self):
        _,_,_,base=self.audit()
        self.assertNotIn('OCCUPANCY_GAP',{r['code'] for r in self.failed(base)})
        def mutate(c,o,p):
            src=o['hypotheses']['objects'][0]['occupancy'][0]
            a=deepcopy(src);b=deepcopy(src)
            a.update(id='left',end_s=2.5);b.update(id='right',start_s=2.5+1e-12)
            o['hypotheses']['objects'][0]['occupancy']=[a,b]
        _,_,_,report=self.audit(mutate)
        gaps=[r for r in self.failed(report) if r['code']=='OCCUPANCY_GAP']
        self.assertEqual(len(gaps),1)
        self.assertEqual(gaps[0]['time_interval_s'],[2.5,2.5+1e-12])

    def test_missing_adverse_coverage_and_placeholder_are_both_retained(self):
        def mutate(c,o,p):
            add_adverse_scenario(o)
            src=o['hypotheses']['objects'][0]['occupancy'][0]
            src['status']='placeholder';src['alternatives']=src['alternatives'][:1]
        _,_,_,report=self.audit(mutate)
        codes={r['code'] for r in self.failed(report)}
        self.assertTrue({'PLACEHOLDER_OCCUPANCY','OCCUPANCY_GAP'}<=codes)
        self.assertTrue(any('adverse' in (r.get('note') or '') for r in self.failed(report)))

    def test_guard_locations_are_current_and_refer_to_runtime_need(self):
        def mutate(c,o,p):o['hypotheses']['objects'][0]['occupancy']=None
        _,_,_,report=self.audit(mutate)
        occ=next(r for r in self.failed(report) if r['code']=='OCCUPANCY_UNKNOWN')
        ref=occ['guard_refs'][0]
        root=Path(__file__).resolve().parents[1]
        line=(root/ref['file']).read_text().splitlines()[ref['line']-1]
        self.assertIn('OCCUPANCY_UNKNOWN',line)
        self.assertEqual(len(ref['source_sha256']),64)

    def test_typed_unbounded_unknown_not_ignored_for_far_scope(self):
        def mutate(c,o,p):
            o['hypotheses']['unknowns']=[dict(id='hidden',kind='no_finite_bound',
                object_ids=['pedestrian'],scenario_ids=[],start_s=0,end_s=5,
                space_polygon_m=[[1000,1000],[1001,1000],[1001,1001],[1000,1001]],
                premise_ids=[],detail='Far now; can move later. This prose cannot authorize removal.')]
        _,_,_,report=self.audit(mutate)
        self.assertIn('TYPED_UNKNOWN_BLOCKING',{r['code'] for r in self.failed(report)})

    def test_t1_actual_failure_and_diagnostic_relation_are_separate(self):
        cap,out,prov=metric_case()
        out['hypotheses']['unbounded_unknown']=True
        out['hypotheses']['objects'][0]['occupancy']=None
        prov['output_digest']=digest(out)
        with self.assertRaises(Failure) as caught:construct(cap,out,prov,allow_synthetic=True)
        actual=deepcopy(caught.exception.record)
        report=diagnose_metric_output(cap,out,prov,first_failure=actual,allow_synthetic=True)
        self.assertEqual(report['original_T1_first_failure'],actual)
        self.assertEqual(actual['code'],'UNBOUNDED_UNKNOWN')
        self.assertTrue(any(r['code']=='OCCUPANCY_UNKNOWN' and r['status']=='FAIL'
                            and r['historical_relation'].startswith('independently_') for r in report['checks']))

    def test_null_body_does_not_create_fake_body_envelope_failure(self):
        def mutate(c,o,p):o['hypotheses']['objects'][0]['body_geometry']=None
        _,_,_,report=self.audit(mutate)
        self.assertIn('BODY_STATE_UNKNOWN',{r['code'] for r in self.failed(report)})
        body=[r for r in report['checks'] if r['code']=='BODY_ENVELOPE_CONFLICT']
        self.assertTrue(body)
        self.assertTrue(all(r['status']=='NOT_EVALUABLE' for r in body))


if __name__=='__main__':unittest.main()
