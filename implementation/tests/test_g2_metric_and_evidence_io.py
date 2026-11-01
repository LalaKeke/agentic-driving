"""Authored arithmetic/IO regressions only. No real model calls or G2 results."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agentic.contracts import Failure,digest
from agentic.fixtures import capture,ScriptedAgent,rect
from agentic.fixtures_v3 import metric_case,add_adverse_scenario
from agentic.g2_metric import evaluate,SEMANTICS
from agentic.evidence_io import check_new_destination,reserve_directory,write_json_new,RESULTS


class StrictG2MetricTests(unittest.TestCase):
    def inputs(self):
        cap,out,prov=metric_case();prov['image_sha256']={'fixture':'AUTHORED_NUMERICAL_TEST_NOT_REAL_IMAGE'}
        actor=dict(id='gt_actor',state_time_id=cap['metadata']['reference_time_id'],position_m=[18.,-3.],velocity_mps=[0.,2.5],
                   occupancy=[dict(start_s=0.,end_s=.2,kind='whole_interval_body_envelope',polygon_m=rect(17.6,18.4,-3.5,-2.)),
                              dict(start_s=.2,end_s=.4,kind='whole_interval_body_envelope',polygon_m=rect(17.6,18.4,-3.,-1.5))])
        domain=dict(id='authored_review_domain',polygon_m=rect(-50,80,-50,80),purpose='current_actor_and_future_body')
        gt=dict(image_sha256=prov['image_sha256'],reference_time_id=cap['metadata']['reference_time_id'],reference_time_s=100.,
                coordinate_frame='ego_at_reference',units='m_s_rad',ego_center_to_rear_axle_m=1.2,
                measurement_semantics=deepcopy(SEMANTICS),evaluation_domain=domain,
                velocity_time_semantics='instantaneous_at_state_time',future_body_semantics='synthetic_authored_body',
                scene_type='AUTHORED_UNIT_TEST',objects=[actor])
        record=dict(sample_id='unit',record_id='unit_r0',capture=cap,output=out,provenance=prov)
        truth=dict(samples={'unit':gt},associations={'unit_r0':{'pedestrian':dict(gt_id='gt_actor',status='unique_reviewed',evidence=['AUTHORED_IDENTITY'])}})
        config=dict(evaluation_horizon_s=.4,evaluation_domain=domain)
        return record,truth,config

    def run_case(self,record,truth,config):
        record['provenance']['output_digest']=digest(record['output'])
        return evaluate([record],truth,config,allow_synthetic=True)

    def test_matching_semantics_score_only_authored_arithmetic(self):
        r,t,c=self.inputs();result=self.run_case(r,t,c);row=result['rows'][0]
        self.assertEqual(result['status'],'SYNTHETIC_NUMERIC_TEST_NOT_G2')
        self.assertEqual(row['position_error_m'],0.);self.assertEqual(row['velocity_vector_error_mps'],0.)
        self.assertEqual(row['future']['minimum_interval_coverage'],1.)
        self.assertGreater(row['future']['mean_envelope_area_ratio'],1.)
        self.assertEqual(row['planning_usability']['status'],'NOT_EVALUATED')
        self.assertIsNone(result['scene_rows'][0]['road_accuracy_score'])
        self.assertEqual(result['online_success_claims'],0)

    def test_synthetic_mode_must_be_explicit(self):
        r,t,c=self.inputs()
        with self.assertRaises(Failure) as exc:evaluate([r],t,c)
        self.assertEqual(exc.exception.record['code'],'G2_REAL_ONLY')

    def test_legacy_semantics_are_not_backfilled(self):
        r,t,c=self.inputs();out,prov=ScriptedAgent().decide(capture());prov.update(agent_output_contract='2.1.0',image_sha256=r['provenance']['image_sha256'])
        r.update(output=out,provenance=prov);result=self.run_case(r,t,c);row=result['rows'][0]
        self.assertIsNone(row['position_error_m']);self.assertIsNone(row['velocity_vector_error_mps'])
        self.assertIn('LEGACY_OR_UNDECLARED_MEASUREMENT_CONTRACT',row['comparability_reasons'])
        self.assertEqual(result['availability']['declared_object_rows'],1)

    def test_wrong_velocity_definition_rejects_instead_of_reinterpreting(self):
        r,t,c=self.inputs();r['output']['measurement_semantics']['velocity_semantics']='ego_relative_velocity'
        row=self.run_case(r,t,c)['rows'][0]
        self.assertIsNone(row['velocity_vector_error_mps'])
        self.assertIn('MODEL_MEASUREMENT_SEMANTICS_MISSING_OR_INCOMPATIBLE',row['comparability_reasons'])

    def test_annotation_velocity_proxy_is_not_instantaneous_accuracy(self):
        r,t,c=self.inputs();t['samples']['unit']['velocity_time_semantics']='bracketing_annotation_displacement_slope'
        row=self.run_case(r,t,c)['rows'][0]
        self.assertEqual(row['position_error_m'],0.);self.assertIsNone(row['velocity_vector_error_mps'])
        self.assertEqual(row['velocity_status'],'GT_VELOCITY_IS_PROXY_OR_TIME_SEMANTICS_UNDECLARED')

    def test_domain_or_reference_offset_mismatch_refuses_scores(self):
        for kind in ('domain','offset'):
            r,t,c=self.inputs()
            if kind=='domain':t['samples']['unit']['evaluation_domain']=dict(id='different',polygon_m=rect(-50,80,-50,80))
            else:t['samples']['unit']['ego_center_to_rear_axle_m']=0.
            row=self.run_case(r,t,c)['rows'][0]
            self.assertIsNone(row['position_error_m']);self.assertIsNone(row['future_minimum_interval_coverage'])

    def test_duplicate_associations_all_refuse_scoring(self):
        r,t,c=self.inputs();other=deepcopy(r['output']['hypotheses']['objects'][0]);other['id']='other'
        r['output']['hypotheses']['objects'].append(other)
        t['associations']['unit_r0']['other']=deepcopy(t['associations']['unit_r0']['pedestrian'])
        result=self.run_case(r,t,c)
        self.assertEqual(result['availability']['unmatched'],2)
        for row in result['rows']:
            self.assertIsNone(row['position_error_m']);self.assertIn('DUPLICATE_GT_IDENTITY_NO_SCORE',row['comparability_reasons'])

    def test_missing_and_unmatched_rows_stay_in_denominators(self):
        r,t,c=self.inputs();o=r['output']['hypotheses']['objects'][0];o['position_m']=None;o['velocity_mps']=None;o['occupancy']=None
        del t['associations']['unit_r0']['pedestrian']
        result=self.run_case(r,t,c);a=result['availability']
        self.assertEqual((a['declared_object_rows'],a['position_missing'],a['velocity_missing'],a['occupancy_missing'],a['unmatched']),(1,1,1,1,1))
        self.assertEqual(result['unique_windows'],1);self.assertEqual(result['truth_object_rows'],1)

    def test_current_missing_does_not_suppress_independent_future_comparison(self):
        r,t,c=self.inputs();t['samples']['unit']['objects'][0]['position_m']=None
        row=self.run_case(r,t,c)['rows'][0]
        self.assertIsNone(row['position_error_m']);self.assertEqual(row['future']['minimum_interval_coverage'],1.)
        self.assertIsNone(row['velocity_vector_error_mps'])
        self.assertEqual(row['velocity_status'],'GT_VELOCITY_DOMAIN_LOCATION_UNAVAILABLE')

    def test_null_and_missing_scenarios_do_not_get_future_scores(self):
        for change in ('null','scenario'):
            r,t,c=self.inputs();o=r['output']['hypotheses']['objects'][0]
            if change=='null':o['occupancy']=None
            else:o['scenarios'].append(dict(id='uncovered_adverse',premise_ids=['walk_premise'],role='reasonable_adverse'))
            row=self.run_case(r,t,c)['rows'][0]
            self.assertIsNone(row['future_minimum_interval_coverage'])

    def test_coarse_sources_preserve_closed_boundary_links(self):
        r,t,c=self.inputs();o=r['output']['hypotheses']['objects'][0];a=o['occupancy'][0];a['end_s']=.2
        b=deepcopy(a);b.update(id='source_after_boundary',start_s=.2,end_s=5.);o['occupancy'].append(b)
        row=self.run_case(r,t,c)['rows'][0]
        self.assertEqual(set(row['future']['intervals'][0]['source_ids']),{a['id'],b['id']})

    def test_model_future_gap_keeps_coverage_null(self):
        r,t,c=self.inputs();r['output']['hypotheses']['objects'][0]['occupancy'][0]['start_s']=.1
        row=self.run_case(r,t,c)['rows'][0]
        self.assertIsNone(row['future_minimum_interval_coverage']);self.assertEqual(row['future']['status'],'MODEL_FUTURE_TIME_GAP')

    def test_evaluation_never_mutates_inputs(self):
        r,t,c=self.inputs();before=[digest(x) for x in (r,t,c)]
        evaluate([r],t,c,allow_synthetic=True)
        self.assertEqual([digest(x) for x in (r,t,c)],before)

    def test_legacy_truth_entity_format_is_explicitly_unscored(self):
        r,t,c=self.inputs();t['samples']['unit']['objects'][0]['instance_token']='gt_actor'
        del t['samples']['unit']['objects'][0]['id']
        result=self.run_case(r,t,c)
        self.assertEqual(result['truth_object_rows'],1)
        self.assertIn('GT_ENTITY_IDS_MISSING_DUPLICATED_OR_LEGACY_FORMAT',result['rows'][0]['comparability_reasons'])
        self.assertIsNone(result['rows'][0]['position_error_m'])

    def independent_scenario_sources(self):
        r,t,c=self.inputs();add_adverse_scenario(r['output']);o=r['output']['hypotheses']['objects'][0]
        first=o['occupancy'][0];first['premise_ids']=['walk_premise']
        second=deepcopy(first);second['id']='adverse_source_0_5';second['premise_ids']=['adverse_premise']
        first['alternatives']=[x for x in first['alternatives'] if x['scenario_id']=='walk']
        second['alternatives']=[dict(scenario_id='adverse',polygon_m=rect(17.6,19.,-3.5,10.))]
        o['occupancy']=[first,second]
        return r,t,c

    def test_independent_sources_for_each_scenario_are_supported(self):
        r,t,c=self.independent_scenario_sources()
        # Verify that this represents a native-runtime-legal source structure.
        from agentic import t1
        r['provenance']['output_digest']=digest(r['output'])
        scene=t1.construct(r['capture'],r['output'],r['provenance'],allow_synthetic=True)
        self.assertEqual(len(scene['hypotheses']['objects'][0]['occupancy']),25)
        row=self.run_case(r,t,c)['rows'][0]
        self.assertEqual(row['future']['minimum_interval_coverage'],1.)
        self.assertEqual(set(row['future']['scenario_source_ids']),{'walk','adverse'})

    def test_one_scenario_gap_cannot_be_hidden_by_another_scenario(self):
        for gap in (.1,1e-12):
            r,t,c=self.independent_scenario_sources();o=r['output']['hypotheses']['objects'][0]
            second=o['occupancy'][1];second['end_s']=.2
            after=deepcopy(second);after.update(id='adverse_after_gap',start_s=.2+gap,end_s=5.)
            o['occupancy'].append(after)
            row=self.run_case(r,t,c)['rows'][0]
            self.assertIsNone(row['future_minimum_interval_coverage'])
            self.assertEqual(row['future']['status'],'MODEL_FUTURE_TIME_GAP')
            self.assertEqual(row['future']['missing_scenarios'],['adverse'])

    def test_gt_positive_microgap_and_short_endpoint_are_rejected(self):
        for gap_type in ('middle','end'):
            r,t,c=self.inputs();last=t['samples']['unit']['objects'][0]['occupancy'][1]
            if gap_type=='middle':last['start_s']+=1e-12
            else:last['end_s']-=1e-12
            row=self.run_case(r,t,c)['rows'][0]
            self.assertIsNone(row['future_minimum_interval_coverage'])
            self.assertIn(row['future']['status'],('GT_FUTURE_TIME_GAP_OR_INVALID','GT_FUTURE_INCOMPLETE'))

    def test_velocity_truth_location_must_be_known_inside_domain(self):
        for point in (None,[1000.,1000.]):
            r,t,c=self.inputs();t['samples']['unit']['objects'][0]['position_m']=point
            row=self.run_case(r,t,c)['rows'][0]
            self.assertIsNone(row['velocity_vector_error_mps'])
            self.assertIn(row['velocity_status'],('GT_VELOCITY_DOMAIN_LOCATION_UNAVAILABLE','GT_VELOCITY_OUTSIDE_EVALUATION_DOMAIN'))

    def test_residual_decimal_sum_matches_runtime_without_an_epsilon(self):
        for total in (.3,.3-1e-12):
            r,t,c=self.inputs();o=r['output']['hypotheses']['objects'][0]
            o['alignment']['residual_m']=.1;o['occupancy'][0]['residual_m']=.2;o['uncertainty_m']=total
            row=self.run_case(r,t,c)['rows'][0]
            if total==.3:
                self.assertEqual(row['future']['minimum_interval_coverage'],1.)
                from agentic import t1
                self.assertIsNotNone(t1.construct(r['capture'],r['output'],r['provenance'],allow_synthetic=True))
            else:
                self.assertIsNone(row['future_minimum_interval_coverage'])
                self.assertEqual(row['future']['status'],'MODEL_RESIDUAL_NOT_INCLUDED_IN_TOTAL')


class EvidenceWriteProtectionTests(unittest.TestCase):
    def test_existing_file_and_directory_are_never_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'already.json';path.write_bytes(b'ORIGINAL')
            with self.assertRaises(Failure):write_json_new(path,{'replace':True})
            self.assertEqual(path.read_bytes(),b'ORIGINAL')
            with self.assertRaises(Failure):reserve_directory(tmp)

    def test_new_output_and_exclusive_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=reserve_directory(Path(tmp)/'new');write_json_new(out/'x.json',{'x':1})
            self.assertEqual(json.loads((out/'x.json').read_text()),{'x':1})

    def test_seal_ancestor_blocks_new_child_and_symlink_alias(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'sealed';root.mkdir();(root/'DELIVERY_SHA256_MANIFEST.json').write_text('{}')
            alias=Path(tmp)/'alias';alias.symlink_to(root,target_is_directory=True)
            for parent in (root,alias):
                with self.assertRaises(Failure) as exc:reserve_directory(parent/'new')
                self.assertEqual(exc.exception.record['code'],'SEALED_EVIDENCE')
            self.assertFalse((root/'new').exists())

    def test_lowercase_sha_manifest_also_seals_ancestor(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp)/'sha256_manifest.json').write_text('{}')
            with self.assertRaises(Failure) as exc:reserve_directory(Path(tmp)/'new')
            self.assertEqual(exc.exception.record['code'],'SEALED_EVIDENCE')

    def test_known_historical_roots_protected_without_marker(self):
        with self.assertRaises(Failure) as exc:check_new_destination(RESULTS/'process_recovery'/'must_not_be_created')
        self.assertEqual(exc.exception.record['code'],'SEALED_EVIDENCE')

    def test_input_output_overlap_is_not_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'input';source.mkdir()
            with self.assertRaises(Failure) as exc:reserve_directory(source/'nested_output',readonly_inputs=[source])
            self.assertEqual(exc.exception.record['code'],'INPUT_OUTPUT_OVERLAP')

    def test_old_cli_checks_existing_output_before_reading_inputs(self):
        from agentic.cli import main
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'already.json';path.write_bytes(b'PRESERVED')
            with patch.object(sys,'argv',['agentic.cli','g2','--records','does_not_exist.json','--truth','missing.json','--config','missing.json','--out',str(path)]):
                with self.assertRaises(Failure) as exc:main()
            self.assertEqual(exc.exception.record['code'],'OUTPUT_EXISTS');self.assertEqual(path.read_bytes(),b'PRESERVED')


class LegacyAnalysisEntryProtectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root=Path(__file__).resolve().parents[2]
        cls.scripts=cls.root/'implementation/experiments/nuscenes'
        cls.modules={}
        for name in ('t1_field_diagnosis','replay_frozen_t1_counterexamples','finalize_t1_diagnosis'):
            path=cls.scripts/(name+'.py');spec=importlib.util.spec_from_file_location('_guard_test_'+name,path)
            value=importlib.util.module_from_spec(spec)
            exec(compile(path.read_bytes(),str(path),'exec'),value.__dict__)
            cls.modules[name]=value

    def test_old_default_entries_stop_before_writing_archived_evidence(self):
        evidence=self.root/'implementation/results/t1_unknown_diagnosis'
        before={str(p.relative_to(evidence)):p.read_bytes() for p in evidence.rglob('*') if p.is_file()}
        for name in self.modules:
            result=subprocess.run([str(self.root/'.venv/bin/python'),'-B',str(self.scripts/(name+'.py'))],cwd=self.root,capture_output=True,text=True)
            self.assertEqual(result.returncode,2,result.stderr)
            code=json.loads(result.stdout)['code']
            self.assertEqual(code,'ARCHIVED_FINALIZER' if name=='finalize_t1_diagnosis' else 'OUTPUT_EXISTS')
        after={str(p.relative_to(evidence)):p.read_bytes() for p in evidence.rglob('*') if p.is_file()}
        self.assertEqual(before,after)

    def test_new_child_of_sealed_directory_is_rejected_without_reading_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'DELIVERY_SHA256_MANIFEST.json').write_text('{}')
            for name,call in (('t1_field_diagnosis','build'),('replay_frozen_t1_counterexamples','main')):
                out=root/(name+'_new')
                with self.assertRaises(Failure) as exc:getattr(self.modules[name],call)(out)
                self.assertEqual(exc.exception.record['code'],'SEALED_EVIDENCE')
                self.assertFalse(out.exists())
            # The archived finalizer is disabled even for a fresh path.
            with patch.object(self.modules['finalize_t1_diagnosis'],'write') as writer:
                with self.assertRaises(Failure) as exc:self.modules['finalize_t1_diagnosis'].finalize(root/'fresh')
                self.assertEqual(exc.exception.record['code'],'ARCHIVED_FINALIZER');writer.assert_not_called()

    def test_existing_empty_directory_is_not_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)/'existing_empty';out.mkdir()
            for name,call in (('t1_field_diagnosis','build'),('replay_frozen_t1_counterexamples','main')):
                with self.assertRaises(Failure) as exc:getattr(self.modules[name],call)(out)
                self.assertEqual(exc.exception.record['code'],'OUTPUT_EXISTS')
                self.assertEqual(list(out.iterdir()),[])


if __name__=='__main__':unittest.main()
