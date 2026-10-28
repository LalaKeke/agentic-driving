"""Independent authored counterexamples; no real images/model or GT is read."""
from copy import deepcopy
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agentic import t1, t2, t3, t4
from agentic.contracts import Failure, digest, model_contract, resolve_prompt, validate
from agentic.fixtures import ScriptedAgent, capture, coasting_fault, rect
from agentic.fixtures_v3 import metric_case, add_adverse_scenario
from agentic.geometry import center_state, disk_radius, obstacles, rear_state
from agentic.metric_contract import VERSION, COVERAGE_PROFILE, normalize


class MetricTemporalContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cap, out, prov = metric_case()
        cls.base_scene = t1.construct(cap, out, prov, True)
        cls.base_trajectory = t3.solve(cls.base_scene, t2.build(cls.base_scene))

    def construct(self, cap, output, provenance):
        provenance = deepcopy(provenance)
        provenance['output_digest'] = digest(output)
        return t1.construct(cap, output, provenance, True)

    def rejects(self, cap, output, provenance, expected=None):
        with self.assertRaises(Failure) as ctx:
            self.construct(cap, output, provenance)
        if expected is not None:
            self.assertIn(ctx.exception.record['code'], expected)
        self.assertNotEqual(ctx.exception.record['code'], 'SOURCE')
        return ctx.exception.record

    def test_async_actual_evidence_native_reference_state_is_accepted(self):
        cap, out, prov = metric_case()
        actor = out['hypotheses']['objects'][0]
        self.assertNotIn(cap['metadata']['reference_time_id'], actor['time_ids'])
        scene = self.construct(cap, out, prov)
        self.assertEqual(scene['hypotheses']['objects'][0]['time_ids'], actor['time_ids'])
        self.assertEqual(scene['hypotheses']['objects'][0]['frame_ids'], actor['frame_ids'])
        self.assertEqual(scene['provenance']['source'], 'synthetic_fixture')
        self.assertEqual(scene['provenance']['agent_output_contract'], VERSION)
        self.assertEqual(t4.verify(scene, self.base_trajectory)['verdict'], 'PASS')

    def test_direct_reference_evidence_is_explicitly_supported(self):
        cap, out, prov = metric_case(asynchronous=False)
        self.construct(cap, out, prov)

    def test_construct_does_not_mutate_or_add_evidence(self):
        cap, out, prov = metric_case()
        before = deepcopy((cap, out, prov))
        self.construct(cap, out, prov)
        self.assertEqual((cap, out, prov), before)

    def test_stale_state_remains_rejected(self):
        cap, out, prov = metric_case()
        out['hypotheses']['objects'][0]['state_time_id'] = 't99.900'
        self.rejects(cap, out, prov)

    def test_false_frame_time_pair_is_rejected(self):
        cap, out, prov = metric_case()
        out['hypotheses']['objects'][0]['alignment']['evidence_positions'][0]['time_id'] = 't100.000'
        self.rejects(cap, out, prov)

    def test_actor_must_be_claimed_observed_in_its_cited_image(self):
        cap, out, prov = metric_case()
        out['hypotheses']['objects'][0]['alignment']['evidence_positions'][0]['observed_actor'] = False
        self.rejects(cap, out, prov)

    def test_missing_motion_bound_is_not_excused_by_small_age(self):
        cap, out, prov = metric_case()
        out['hypotheses']['objects'][0]['alignment']['motion_bound'] = None
        self.rejects(cap, out, prov)

    def test_ego_only_alignment_label_is_rejected(self):
        cap, out, prov = metric_case()
        out['hypotheses']['objects'][0]['alignment']['method'] = 'ego_compensation_only'
        self.rejects(cap, out, prov)

    def test_stated_motion_bound_must_cover_model_displacement(self):
        cap, out, prov = metric_case()
        out['hypotheses']['objects'][0]['position_m'] = [28.,-3.]
        self.rejects(cap, out, prov)

    def test_native_velocity_cannot_contradict_alignment_speed_bound(self):
        cap,out,prov=metric_case()
        out['hypotheses']['objects'][0]['velocity_mps']=[0.,100.]
        self.rejects(cap,out,prov,{'VELOCITY_BOUND_CONFLICT'})

    def test_unknown_instantaneous_velocity_does_not_invalidate_full_body_envelopes(self):
        cap,out,prov=metric_case()
        out['hypotheses']['objects'][0]['velocity_mps']=None
        scene=self.construct(cap,out,prov)
        self.assertIsNone(scene['hypotheses']['objects'][0]['velocity_mps'])

    def test_motion_bound_cannot_exclude_an_adverse_mode(self):
        cap, out, prov = metric_case()
        out['hypotheses']['objects'][0]['alignment']['motion_bound']['covers_all_declared_scenarios'] = False
        self.rejects(cap, out, prov)

    def test_wrong_velocity_semantics_is_rejected(self):
        cap, out, prov = metric_case()
        # The exact registered semantic key is read from the immutable fixture.
        semantics = out['measurement_semantics']
        candidates = [k for k in semantics if 'velocity' in k]
        self.assertTrue(candidates)
        semantics[candidates[0]] = 'relative_to_ego_velocity'
        self.rejects(cap, out, prov)

    def test_unknown_body_reference_point_is_not_reinterpreted(self):
        cap, out, prov = metric_case()
        semantics = out['measurement_semantics']
        candidates = [k for k in semantics if 'actor' in k and ('position' in k or 'reference' in k)]
        self.assertTrue(candidates)
        semantics[candidates[0]] = 'unknown_contact_point'
        self.rejects(cap, out, prov)

    def test_coarse_body_envelope_refines_without_shrinking(self):
        cap, out, prov = metric_case()
        normalized, trace = normalize(cap, out, dict(prov, output_digest=digest(out)), True)
        source = out['hypotheses']['objects'][0]['occupancy'][0]
        cells = normalized['hypotheses']['objects'][0]['occupancy']
        self.assertEqual(len(cells),25)
        for k, cell in enumerate(cells):
            self.assertAlmostEqual(cell['start_s'],k*.2)
            self.assertAlmostEqual(cell['end_s'],(k+1)*.2)
            self.assertEqual(cell['alternatives_m'],[a['polygon_m'] for a in source['alternatives']])
        self.assertIn(source['id'], str(trace))
        self.assertIn('walk_premise', str(trace))

    def test_touching_closed_source_boundary_retains_both_sets(self):
        cap, out, prov = metric_case()
        actor=out['hypotheses']['objects'][0]
        left=deepcopy(actor['occupancy'][0]);left.update(id='left',end_s=1.)
        right=deepcopy(left);right.update(id='right',start_s=1.,end_s=5.)
        right['alternatives'][0]['polygon_m']=rect(17.6,19.,-3.5,10.5)
        actor['occupancy']=[left,right]
        normalized,_=normalize(cap,out,dict(prov,output_digest=digest(out)),True)
        cells=normalized['hypotheses']['objects'][0]['occupancy']
        expected=[left['alternatives'][0]['polygon_m'],right['alternatives'][0]['polygon_m']]
        for index in (4,5):
            for poly in expected:self.assertIn(poly,cells[index]['alternatives_m'])

    def test_null_coverage_is_not_empty_occupancy(self):
        cap,out,prov=metric_case();out['hypotheses']['objects'][0]['occupancy']=None
        self.rejects(cap,out,prov)

    def test_source_time_gap_is_rejected(self):
        cap,out,prov=metric_case();out['hypotheses']['objects'][0]['occupancy'][0]['start_s']=.01
        self.rejects(cap,out,prov)

    def test_tiny_positive_time_gap_is_not_treated_as_float_tolerance(self):
        cap,out,prov=metric_case();actor=out['hypotheses']['objects'][0]
        left=deepcopy(actor['occupancy'][0]);left.update(id='left',end_s=1.)
        right=deepcopy(left);right.update(id='right',start_s=1.+1e-12,end_s=5.)
        actor['occupancy']=[left,right]
        self.rejects(cap,out,prov,{'OCCUPANCY_GAP'})

    def test_source_placeholder_is_not_supported_coverage(self):
        cap,out,prov=metric_case();out['hypotheses']['objects'][0]['occupancy'][0]['status']='placeholder'
        self.rejects(cap,out,prov)

    def test_instantaneous_polygon_is_not_interval_coverage(self):
        cap,out,prov=metric_case();out['hypotheses']['objects'][0]['occupancy'][0]['kind']='instantaneous_state'
        self.rejects(cap,out,prov)

    def test_unknown_scale_stays_rejected(self):
        cap,out,prov=metric_case();out['hypotheses']['scale_basis']='unknown'
        self.rejects(cap,out,prov,{'MISSING_SCALE'})

    def test_null_scale_support_is_explicitly_infeasible(self):
        cap,out,prov=metric_case();out['hypotheses']['metric_scale_support']=None
        err=self.rejects(cap,out,prov,{'MISSING_SCALE_SUPPORT'})
        self.assertEqual(err['category'],'tool_infeasible')

    def test_scale_support_must_cite_actual_paired_acquisition(self):
        cap,out,prov=metric_case()
        out['hypotheses']['metric_scale_support']['time_ids'][0]=cap['metadata']['reference_time_id']
        self.rejects(cap,out,prov,{'MISSING_EVIDENCE'})

    def test_scale_support_cannot_add_a_frame_not_showing_the_anchor_actor(self):
        cap,out,prov=metric_case();front=cap['observations'][0]
        support=out['hypotheses']['metric_scale_support']
        support['frame_ids']=[front['id']];support['time_ids']=[front['time_id']]
        self.rejects(cap,out,prov,{'SCALE_ANCHOR_EVIDENCE'})

    def test_scale_anchor_must_identify_an_existing_actor(self):
        cap,out,prov=metric_case()
        out['hypotheses']['metric_scale_support']['anchors'][0]['object_id']='invented_actor'
        self.rejects(cap,out,prov,{'SCALE_ANCHOR'})

    def test_scale_anchor_dimension_value_must_match_native_body(self):
        cap,out,prov=metric_case()
        out['hypotheses']['metric_scale_support']['anchors'][0]['value_m']=.81
        self.rejects(cap,out,prov,{'SCALE_ANCHOR'})

    def test_scale_anchor_dimension_is_not_silently_switched(self):
        cap,out,prov=metric_case()
        # .8 is the stated length; width is1.0. Merely changing the label
        # cannot reinterpret this prior to reduce a later comparison error.
        out['hypotheses']['metric_scale_support']['anchors'][0]['dimension']='width_m'
        self.rejects(cap,out,prov,{'SCALE_ANCHOR'})

    def test_scale_anchor_whitespace_rationale_is_not_support(self):
        cap,out,prov=metric_case()
        out['hypotheses']['metric_scale_support']['anchors'][0]['rationale']=' \n\t '
        self.rejects(cap,out,prov,{'MISSING_SCALE_SUPPORT'})

    def test_scale_support_whitespace_reasoning_is_not_support(self):
        cap,out,prov=metric_case()
        out['hypotheses']['metric_scale_support']['metric_reasoning']=' \n\t '
        self.rejects(cap,out,prov,{'MISSING_SCALE_SUPPORT'})

    def test_missing_declared_adverse_coverage_is_rejected(self):
        cap,out,prov=metric_case();add_adverse_scenario(out)
        out['hypotheses']['objects'][0]['occupancy'][0]['alternatives'].pop()
        out['hypotheses']['objects'][0]['occupancy'][0]['premise_ids'].remove('adverse_premise')
        self.rejects(cap,out,prov,{'OCCUPANCY_GAP'})

    def test_scenario_inventory_must_explicitly_remain_complete(self):
        cap,out,prov=metric_case();out['hypotheses']['objects'][0]['scenario_inventory_complete']=False
        self.rejects(cap,out,prov)

    def test_premise_must_bind_actor_scenario_and_interval(self):
        cap,out,prov=metric_case();out['hypotheses']['premises'][0]['object_ids']=['another_actor']
        self.rejects(cap,out,prov)

    def test_unresolved_typed_unknown_is_not_distance_pruned(self):
        cap,out,prov=metric_case()
        out['hypotheses']['unknowns']=[dict(id='remote_unknown',kind='no_finite_bound',
            object_ids=['pedestrian'],scenario_ids=['walk'],start_s=0.,end_s=5.,
            space_polygon_m=rect(1000,1001,1000,1001),premise_ids=['walk_premise'],
            detail='AUTHORED unknown. Present distance does not bound future intrusion.')]
        self.rejects(cap,out,prov)

    def test_all_three_unresolved_unknown_reasons_remain_blocking(self):
        for kind in ('not_provided','not_expressible','no_finite_bound'):
            with self.subTest(kind=kind):
                cap,out,prov=metric_case()
                out['hypotheses']['unknowns']=[dict(id='unresolved',kind=kind,
                    object_ids=['pedestrian'],scenario_ids=['walk'],start_s=0.,end_s=5.,
                    space_polygon_m=None,premise_ids=['walk_premise'],
                    detail='AUTHORED explicit unknown, not a permission to plan.')]
                err=self.rejects(cap,out,prov)
                self.assertEqual(err['code'],'TYPED_UNKNOWN_BLOCKING')

    def test_multiple_conditionals_can_be_expressed_when_all_modes_are_covered(self):
        cap,out,prov=metric_case();add_adverse_scenario(out)
        out['hypotheses']['unknowns']=[dict(id='modes',kind='multiple_conditionals',
            object_ids=['pedestrian'],scenario_ids=['walk','adverse'],start_s=0.,end_s=5.,
            space_polygon_m=rect(17,20,-4,11),premise_ids=['walk_premise','adverse_premise'],
            detail='Two authored scenarios; both complete envelopes remain obstacles.')]
        scene=self.construct(cap,out,prov)
        self.assertEqual(len(list(obstacles(scene,12))),2)

    def test_source_residual_cannot_exceed_the_declared_total(self):
        cap,out,prov=metric_case();out['hypotheses']['objects'][0]['occupancy'][0]['residual_m']=.3
        self.rejects(cap,out,prov,{'RESIDUAL_BUDGET'})

    def test_alignment_residual_cannot_exceed_the_declared_total(self):
        cap,out,prov=metric_case();out['hypotheses']['objects'][0]['alignment']['residual_m']=.3
        self.rejects(cap,out,prov,{'RESIDUAL_BUDGET'})

    def test_total_residual_includes_source_plus_alignment_once(self):
        cap,out,prov=metric_case();out['hypotheses']['objects'][0]['uncertainty_m']=.3
        self.rejects(cap,out,prov,{'RESIDUAL_BUDGET'})

    def test_decimal_residual_sum_is_exact_without_a_positive_shortfall_tolerance(self):
        cap,out,prov=metric_case();actor=out['hypotheses']['objects'][0]
        actor['alignment']['residual_m']=.1
        actor['occupancy'][0]['residual_m']=.2
        actor['uncertainty_m']=.3
        self.construct(cap,out,prov)
        actor['uncertainty_m']=.3-1e-12
        self.rejects(cap,out,prov,{'RESIDUAL_BUDGET'})

    def test_multiple_object_local_premises_are_not_asserted_for_other_actors(self):
        cap,out,prov=metric_case();add_adverse_scenario(out)
        original=out['hypotheses']['objects'][0];second=deepcopy(original)
        second['id']='pedestrian_2';second['position_m'][0]+=10
        sid={'walk':'walk2','adverse':'adverse2'}
        pid={'walk_premise':'walk_premise2','adverse_premise':'adverse_premise2',
             'scale_size_prior':'scale_size_prior2'}
        second['alignment']['premise_ids']=[pid[x] for x in second['alignment']['premise_ids']]
        for p in second['alignment']['evidence_positions']:p['position_m'][0]+=10
        for s in second['scenarios']:
            s['id']=sid[s['id']];s['premise_ids']=[pid[x] for x in s['premise_ids']]
        for source in second['occupancy']:
            source['id']+='2';source['premise_ids']=[pid[x] for x in source['premise_ids']]
            for alt in source['alternatives']:
                alt['scenario_id']=sid[alt['scenario_id']]
                alt['polygon_m']=[[x+10,y] for x,y in alt['polygon_m']]
        out['hypotheses']['objects'].append(second)
        for premise in deepcopy(out['hypotheses']['premises']):
            premise['id']=pid[premise['id']];premise['object_ids']=['pedestrian_2']
            premise['scenario_ids']=[sid[x] for x in premise['scenario_ids']]
            premise['space_polygon_m']=[[x+10,y] for x,y in premise['space_polygon_m']]
            out['hypotheses']['premises'].append(premise)
        out['hypotheses']['unknowns']=[dict(id='both_conditionals',kind='multiple_conditionals',
            object_ids=['pedestrian','pedestrian_2'],scenario_ids=['walk','adverse','walk2','adverse2'],
            start_s=0.,end_s=5.,space_polygon_m=rect(17,30,-4,11),
            premise_ids=['walk_premise','adverse_premise','walk_premise2','adverse_premise2'],
            detail='Each actor has two fully covered branches with its own local premises.')]
        scene=self.construct(cap,out,prov)
        self.assertEqual(len(scene['hypotheses']['objects']),2)
        self.assertEqual(len(list(obstacles(scene,10))),4)

    def test_source_must_enclose_actor_body_not_merely_its_center(self):
        cap,out,prov=metric_case()
        out['hypotheses']['objects'][0]['occupancy'][0]['alternatives'][0]['polygon_m']=rect(17.9,18.1,-3.1,-2.9)
        self.rejects(cap,out,prov,{'BODY_ENVELOPE_CONFLICT'})

    def test_total_residual_cannot_repair_a_nominal_body_envelope_conflict(self):
        cap,out,prov=metric_case();actor=out['hypotheses']['objects'][0]
        actor['uncertainty_m']=10.
        # Even this deliberately huge authored residual cannot make a tiny
        # nominal center rectangle into an ACTOR BODY envelope.
        actor['occupancy'][0]['alternatives'][0]['polygon_m']=rect(17.99,18.01,-3.01,-2.99)
        self.rejects(cap,out,prov,{'BODY_ENVELOPE_CONFLICT'})

    def test_malformed_unknown_scope_is_not_accepted_as_a_reason_label(self):
        cap,out,prov=metric_case()
        out['hypotheses']['unknowns']=[dict(id='bad_scope',kind='multiple_conditionals',
            object_ids=['not_an_actor'],scenario_ids=['walk'],start_s=0.,end_s=5.,
            space_polygon_m=None,premise_ids=['walk_premise'],detail='Bad explicit scope.')]
        self.rejects(cap,out,prov,{'SCOPE_OBJECT'})

    def test_bound_must_cover_alignment_interval_not_only_future(self):
        cap,out,prov=metric_case();out['hypotheses']['premises'][0]['start_s']=0.
        self.rejects(cap,out,prov,{'PREMISE_SCOPE'})

    def test_premise_missing_tiny_final_interval_is_rejected(self):
        cap,out,prov=metric_case();out['hypotheses']['premises'][0]['end_s']=5.-1e-12
        self.rejects(cap,out,prov,{'PREMISE_SCOPE'})

    def test_premise_missing_tiny_alignment_prefix_is_rejected(self):
        cap,out,prov=metric_case();out['hypotheses']['premises'][0]['start_s']=-.2+1e-12
        self.rejects(cap,out,prov,{'PREMISE_SCOPE'})

    def test_finite_premise_space_cannot_exclude_its_source_body(self):
        cap,out,prov=metric_case()
        out['hypotheses']['premises'][0]['space_polygon_m']=rect(17.8,18.2,-3.2,-2.8)
        self.rejects(cap,out,prov,{'PREMISE_SPACE'})

    def test_all_adverse_branches_are_retained_in_tool_geometry(self):
        cap,out,prov=metric_case();add_adverse_scenario(out)
        scene=self.construct(cap,out,prov)
        self.assertEqual(len(list(obstacles(scene,12))),2)
        self.assertEqual(scene['hypotheses']['objects'][0]['uncertainty_m'],.4)

    def test_adverse_intrusion_rejects_corridor_and_independent_t4(self):
        cap,out,prov=metric_case();add_adverse_scenario(out,invade=True)
        actor=out['hypotheses']['objects'][0]
        future=deepcopy(actor['occupancy'][0]);future.update(id='intrusion_after_2s',start_s=2.)
        before=deepcopy(future);before.update(id='safe_before_2s',start_s=0.,end_s=2.)
        before['alternatives'][1]['polygon_m']=rect(17.6,19.,-3.5,10.)
        actor['occupancy']=[before,future]
        scene=self.construct(cap,out,prov)
        with self.assertRaises(Failure) as ctx:t2.build(scene)
        self.assertIn(ctx.exception.record['code'],{'CORRIDOR_ANCHOR_BLOCKED','CORRIDOR_SEED_BLOCKED'})
        # Explicit software counterexample: stationary ego remains still while
        # a model-authored alternative can enter it. No optimizer/GT is trusted.
        zero=coasting_fault(scene,self.base_trajectory)
        verdict=t4.verify(scene,zero)
        self.assertEqual(verdict['verdict'],'FAIL')
        self.assertEqual(verdict['released_prefix_s'],0)
        collisions=[x for x in verdict['violations'] if x['code']=='BODY_COLLISION']
        self.assertTrue(collisions)
        # Closed source intervals touch at2s, so conservative refinement may
        # begin this threat in the cell ending at2s, but never at initial time.
        self.assertTrue(all(x['time_interval_s'][0]>=1.8-1e-8 for x in collisions))

    def test_t4_rejects_forged_lowering_or_source_trace_even_with_updated_digests(self):
        for variant in ('lowered_hypothesis','source_envelope'):
            with self.subTest(variant=variant):
                scene=deepcopy(self.base_scene)
                if variant=='lowered_hypothesis':
                    scene['hypotheses']['objects'][0]['uncertainty_m']+=.01
                else:
                    trace=scene['provenance']['metric_contract_trace']
                    source=trace['source_output']
                    source['hypotheses']['objects'][0]['occupancy'][0]['alternatives'][0]['polygon_m'][0][0]-=.01
                    # Recomputing supplied hashes still cannot bind this newly
                    # changed source to the unchanged physical cell geometry.
                    scene['provenance']['output_digest']=digest(source)
                    trace['source_output_digest']=digest(source)
                trajectory=deepcopy(self.base_trajectory)
                trajectory['scene_digest']=digest(scene)
                with self.assertRaises(Failure) as ctx:t4.verify(scene,trajectory)
                self.assertEqual(ctx.exception.record['code'],'METRIC_TRACE_MISMATCH')

    def test_t4_rejects_contract_downgrade_with_retained_metric_trace(self):
        scene=deepcopy(self.base_scene)
        scene['provenance']['agent_output_contract']='2.1.0'
        trajectory=deepcopy(self.base_trajectory);trajectory['scene_digest']=digest(scene)
        with self.assertRaises(Failure) as ctx:t4.verify(scene,trajectory)
        self.assertEqual(ctx.exception.record['code'],'METRIC_TRACE')

    def test_ego_center_rear_axle_offset_is_used_once(self):
        cap,out,prov=metric_case();scene=self.construct(cap,out,prov)
        q=rear_state(cap['ego'],cap['vehicle'])
        self.assertAlmostEqual(q[0],-1.2)
        self.assertAlmostEqual(center_state(q,cap['vehicle'])[0],0.)
        self.assertAlmostEqual(disk_radius(cap['vehicle']),math.hypot(3.5,.9))
        self.assertEqual(scene['hypotheses']['objects'][0]['position_m'],[18.,-3.])
        # Existing T2/T4 apply this residual once, in addition to ego body/margin.
        self.assertEqual({u for _,_,u in obstacles(scene,0)},{.4})

    def test_synthetic_source_needs_explicit_opt_in(self):
        cap,out,prov=metric_case()
        with self.assertRaises(Failure) as ctx:t1.construct(cap,out,prov)
        self.assertIn(ctx.exception.record['code'],{'SOURCE','PROMPT_CONTRACT'})

    def test_new_profile_requires_explicit_provenance_version(self):
        cap,out,prov=metric_case();prov.pop('agent_output_contract')
        self.rejects(cap,out,prov)

    def test_new_output_is_not_silently_accepted_by_old_schemas(self):
        cap,out,prov=metric_case()
        for old in ('2.0.0','2.1.0'):
            self.rejects(cap,out,dict(prov,agent_output_contract=old),{'SCHEMA_ERROR'})

    def test_old_outputs_are_not_silently_upgraded(self):
        cap=capture(speed=0.);out,prov=ScriptedAgent().decide(cap)
        self.rejects(cap,out,dict(prov,agent_output_contract=VERSION),{'SCHEMA_ERROR'})

    def test_legacy_defaults_and_old_reference_guard_stay_compatible(self):
        cap=capture(speed=0.);out,prov=ScriptedAgent().decide(cap)
        baseline=t1.construct(cap,out,prov,True)
        same21=t1.construct(cap,out,dict(prov,agent_output_contract='2.1.0'),True)
        self.assertEqual(baseline['hypotheses'],same21['hypotheses'])
        self.assertNotIn('contract_trace',baseline['provenance'])
        cap['observations'].append(dict(id='old_rear',time_id='t99.900',capture_time_s=99.9,
            capture_utc=None,camera_id='rear',path='fixture://no-image'))
        out['hypotheses']['objects'][0].update(frame_ids=['old_rear'],time_ids=['t99.900'])
        self.rejects(cap,out,prov,{'DYNAMIC_ALIGNMENT'})

    def test_prompt_and_output_contract_versions_are_separate(self):
        schema,prompt=resolve_prompt(VERSION)
        self.assertEqual(schema,'agent_output_v3_exp2.schema.json')
        self.assertEqual(prompt,'driving_v3_exp2')
        with self.assertRaises(Failure):resolve_prompt('2.1.0','driving_v3_exp2')
        with self.assertRaises(Failure):resolve_prompt(VERSION,'driving_v2_3')

    def test_frozen_exp1_is_not_implicitly_reinterpreted_as_exp2(self):
        cap,out,prov=metric_case();prov['agent_output_contract']='3.0.0-exp1'
        self.rejects(cap,out,prov,{'MODEL_CONTRACT'})


if __name__=='__main__':unittest.main()
