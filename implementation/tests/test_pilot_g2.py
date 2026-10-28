"""Authored offline privacy and identity-review controls; no model calls."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agentic.contracts import Failure, digest
from agentic.fixtures_v3 import metric_case

path=Path(__file__).resolve().parents[1]/'experiments/pilot_g2.py'
spec=importlib.util.spec_from_file_location('pilot_g2',path)
pilot=importlib.util.module_from_spec(spec);spec.loader.exec_module(pilot)


class PilotG2ReviewTests(unittest.TestCase):
    def record(self):
        cap,out,prov=metric_case()
        out['hypotheses']['objects'][0]['id']='blue_pedestrian_at_19.737m'
        out['hypotheses']['premises'][0]['object_ids']=['blue_pedestrian_at_19.737m']
        out['hypotheses']['premises'][0]['statement']='Blue shirt pedestrian is 19.737 m away, velocity 2.654 m/s.'
        return dict(record_id='unit_r0',sample_id='unit',capture=cap,output=out,provenance=prov)

    def test_review_is_allowlisted_and_has_no_model_metric_values_or_prose(self):
        record=self.record();before=digest(record)
        view,mapping=pilot.semantic_view(record)
        actor=view['objects'][0]
        self.assertEqual(actor['review_alias'],'actor_A')
        self.assertEqual(actor['visual_keyword_hints'],['blue','pedestrian','shirt'])
        self.assertEqual(set(actor),{'review_alias','visual_keyword_hints','evidence'})
        self.assertEqual(mapping['aliases'],{'actor_A':'blue_pedestrian_at_19.737m'})
        text=str(view)
        for forbidden in ('19.737','2.654','position_m','velocity_mps','occupancy','body_geometry','statement'):
            self.assertNotIn(forbidden,text)
        self.assertEqual(digest(record),before)

    def test_invalid_evidence_cannot_smuggle_numeric_hypotheses_into_review(self):
        record=self.record();o=record['output']['hypotheses']['objects'][0]
        o['frame_ids']=['distance_19.737_m'];o['time_ids']=['velocity_2.654_mps']
        view,_=pilot.semantic_view(record)
        evidence=view['objects'][0]['evidence'][0]
        self.assertFalse(evidence['valid_original_pair'])
        self.assertEqual(evidence['frame_id'],'<INVALID_FRAME_ID>')
        self.assertNotIn('19.737',str(view));self.assertNotIn('2.654',str(view))

    def review(self,view):
        evidence=view['objects'][0]['evidence'][0]
        return dict(record_id='unit_r0',semantic_view_digest=digest(view),
            frozen_before_metric_unsealing=True,
            associations={'actor_A':dict(gt_id='gt_actor',status='unique_reviewed',
                reason='Distinct blue shirt in the cited original image',
                evidence=['OFFLINE_AUTHORED_IDENTITY_TEST'],
                review_frame_ids=[evidence['frame_id']],single_actor_not_group=True)})

    def test_review_binding_requires_original_cited_image_and_exact_view(self):
        view,mapping=pilot.semantic_view(self.record());review=self.review(view)
        result=pilot.bind_associations(view,mapping,review)
        self.assertEqual(result['unit_r0']['blue_pedestrian_at_19.737m']['gt_id'],'gt_actor')
        for change in ('digest','frame','group','freeze'):
            altered=deepcopy(review)
            if change=='digest':altered['semantic_view_digest']='WRONG'
            elif change=='frame':altered['associations']['actor_A']['review_frame_ids']=['NOT_CITED']
            elif change=='group':altered['associations']['actor_A']['single_actor_not_group']=False
            else:altered['frozen_before_metric_unsealing']=False
            with self.assertRaises(Failure):pilot.bind_associations(view,mapping,altered)

    def test_unmatched_and_incomplete_reviews_are_not_filled(self):
        view,mapping=pilot.semantic_view(self.record());review=self.review(view)
        review['associations']['actor_A']=dict(status='ambiguous',gt_id=None,reason='No unique appearance evidence')
        result=pilot.bind_associations(view,mapping,review)
        self.assertIsNone(next(iter(result['unit_r0'].values()))['gt_id'])
        review['associations']={}
        with self.assertRaises(Failure):pilot.bind_associations(view,mapping,review)

    def test_alias_mapping_cannot_be_rebound_after_identity_review(self):
        view,mapping=pilot.semantic_view(self.record());review=self.review(view)
        mapping['aliases']['actor_A']='another_actor'
        with self.assertRaises(Failure):pilot.bind_associations(view,mapping,review)

    def test_aliases_are_alphabetic_beyond_twenty_six(self):
        self.assertEqual([pilot.alias(k) for k in (0,25,26,27)],['actor_A','actor_Z','actor_AA','actor_AB'])

    def sealed_record(self, directory):
        root=Path(directory)/'runtime';path=root/'attempt_01/record.json'
        path.parent.mkdir(parents=True);path.write_text('{"synthetic_test":true}\n')
        seal=root/'SHA256_RUNTIME_MANIFEST.json'
        value=dict(experiment_id='AUTHORED_UNIT_TEST',source_commit='unit',runtime_sealed=True,
            files={'attempt_01/record.json':dict(sha256=pilot.sha(path),bytes=path.stat().st_size)},
            online_turns_sent=0,model_budget_charged=0)
        seal.write_text(json.dumps(value))
        return path,seal,value

    def test_completed_seal_unlocks_only_exact_record_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path,seal,_=self.sealed_record(tmp)
            result=pilot.verify_runtime_record(path,seal)
            self.assertTrue(result['runtime_sealed'])
            self.assertEqual(result['record_relative_path'],'attempt_01/record.json')

    def test_empty_or_unfinished_seal_does_not_unlock_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            path,seal,value=self.sealed_record(tmp)
            for invalid in ({},dict(value,runtime_sealed=False)):
                seal.write_text(json.dumps(invalid))
                with self.assertRaises(Failure) as exc:pilot.verify_runtime_record(path,seal)
                self.assertEqual(exc.exception.record['code'],'PILOT_RUNTIME_UNSEALED')

    def test_edited_or_unlisted_record_does_not_unlock_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            path,seal,value=self.sealed_record(tmp)
            original=path.read_bytes();path.write_bytes(original+b' ')
            with self.assertRaises(Failure) as exc:pilot.verify_runtime_record(path,seal)
            self.assertEqual(exc.exception.record['code'],'PILOT_RECORD_SEAL_MISMATCH')
            path.write_bytes(original);value['files']={};seal.write_text(json.dumps(value))
            with self.assertRaises(Failure) as exc:pilot.verify_runtime_record(path,seal)
            self.assertEqual(exc.exception.record['code'],'PILOT_RECORD_NOT_SEALED')

    def test_symlink_to_unsealed_external_record_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path,seal,_=self.sealed_record(tmp)
            outside=Path(tmp)/'outside.json';outside.write_bytes(path.read_bytes())
            link=path.parent/'alias.json';link.symlink_to(outside)
            with self.assertRaises(Failure) as exc:pilot.verify_runtime_record(link,seal)
            self.assertEqual(exc.exception.record['code'],'PILOT_RECORD_OUTSIDE_SEAL')


if __name__=='__main__':unittest.main()
