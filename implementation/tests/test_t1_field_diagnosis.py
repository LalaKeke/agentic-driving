"""Audit correctness on immutable failed responses; no model/tool execution."""
import importlib.util
import json
from pathlib import Path
import unittest


SCRIPT=Path(__file__).resolve().parents[1]/'experiments/nuscenes/t1_field_diagnosis.py'
spec=importlib.util.spec_from_file_location('t1_field_diagnosis',SCRIPT)
audit_module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit_module)


class T1FieldDiagnosisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.audits={}
        for group,subdir in (audit_module.RUNS[0],audit_module.RUNS[2]):
            run=audit_module.EVIDENCE/subdir
            rules=audit_module.FrozenRules(run)
            a=audit_module.Audit(rules,run/'0011-agent.json',run/'0008-capture.json',run/'0014-feedback.json',group)
            a.run()
            cls.audits[group]=a

    def test_standard_json_pointer_escaping(self):
        self.assertEqual(audit_module.pointer(['output','a/b','x~y',2]),'/output/a~1b/x~0y/2')

    def test_legal_null_state_is_not_mislabeled_as_rejection(self):
        a=self.audits['v22_original_four']
        self.assertTrue(all(o['position_m'] is None and o['velocity_mps'] is None for o in a.output['hypotheses']['objects']))
        for row in a.rows:
            if row['check'] in ('instantaneous_position_null_allowed','instantaneous_velocity_null_allowed'):
                self.assertNotEqual(row['status'],'FAIL')
                self.assertTrue(row['unknown_type'])
                self.assertIsNone(row['error_code'])

    def test_missing_prerequisite_is_not_invented_conflict(self):
        a=self.audits['v23_original_four']
        rows=[r for r in a.rows if r['check']=='position_initial_envelope_consistency' and r['object_id']=='rear_pickup']
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['status'],'NOT_EVALUABLE')
        self.assertEqual(rows[0]['actual_execution'],'prerequisite_missing_not_a_separate_proven_failure')

    def test_actual_first_failure_and_latent_errors_are_separate(self):
        a=self.audits['v22_original_four']
        first=[r for r in a.rows if r['actual_execution']=='recorded_first_rejection']
        self.assertEqual([r['error_code'] for r in first],['MISSING_SCALE'])
        later=[r for r in a.rows if r['status']=='FAIL' and r['error_code']=='OCCUPANCY_UNKNOWN']
        self.assertEqual(len(later),10)
        self.assertTrue(all(r['actual_execution']=='independent_failed_predicate_masked_in_recorded_run' for r in later))
        # Historical category must come from the frozen source, even after a
        # category-only correction is implemented in current working T1.
        self.assertEqual(first[0]['source_ref']['category'],'interface_error')

    def test_each_original_raw_value_resolves_exactly(self):
        for a in self.audits.values():
            for row in a.rows:
                data=a.record if row['input']=='record' else a.capture
                parts=[p.replace('~1','/').replace('~0','~') for p in row['json_pointer'].split('/')[1:]]
                for part in parts:
                    data=data[int(part)] if isinstance(data,list) else data[part]
                self.assertEqual(data,row['raw_value'])

    def test_finite_envelope_does_not_turn_missing_future_into_empty(self):
        a=self.audits['v23_original_four']
        supplied=[r for r in a.rows if r['check']=='whole_interval_time_grid' and r['object_id']=='crossing_pedestrians']
        self.assertEqual(len(supplied),25)
        self.assertTrue(all(r['status']=='PASS' for r in supplied))
        missing=[r for r in a.rows if r['check']=='whole_interval_occupancy_available' and r['object_id']=='rear_pickup']
        self.assertEqual(missing[0]['status'],'FAIL')
        self.assertIsNone(missing[0]['raw_value'])
        self.assertEqual(missing[0]['error_code'],'OCCUPANCY_UNKNOWN')

    def test_text_excerpts_are_exact_without_splitting_decimal_bounds(self):
        note='Assume walking up to 1.8 m/s. Starting or turning remains unresolved.'
        excerpts=audit_module.note_excerpts(note)
        self.assertIn('Assume walking up to 1.8 m/s.',[x['text'] for x in excerpts['conditional_premises_or_limitations']])
        for spans in excerpts.values():
            for item in spans:
                self.assertEqual(note[item['start_character']:item['end_character']],item['text'])


if __name__=='__main__': unittest.main()
