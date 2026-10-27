"""Unit contracts plus opt-in checks on real prepared nuScenes windows.

The real-data checks do not substitute synthetic outputs for model responses.
"""
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import sys
import unittest
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agentic.datasets.nuscenes_capture import Acquisition,quaternion_matrix,planar_rotation
from agentic.contracts import Failure,digest,validate
from agentic.fixtures import capture,ScriptedAgent,rect
from agentic import t1
from agentic.g2 import evaluate

class DatasetContractTests(unittest.TestCase):
    def test_quaternion_wxyz_and_center_offset(self):
        q=[math.sqrt(.5),0,0,math.sqrt(.5)];R,yaw=planar_rotation(q)
        np.testing.assert_allclose(R@np.array([1.2,0]),[0,1.2],atol=1e-12)
        self.assertAlmostEqual(yaw,math.pi/2)
        np.testing.assert_allclose(quaternion_matrix(q).T@quaternion_matrix(q),np.eye(3),atol=1e-12)
    def test_runtime_reader_has_no_annotation_or_map_tables(self):
        self.assertEqual(Acquisition.ALLOWED_TABLES,{'sample','sample_data','ego_pose','sensor','calibrated_sensor'})
    def test_invalid_quaternion_rejected(self):
        with self.assertRaises(Failure):quaternion_matrix([0,0,0,0])
    def test_optional_calibration_keeps_legacy_captures_valid(self):
        validate('capture_v2.schema.json',capture())
    def calibrated(self):
        cap=capture();frame=cap['observations'][0]
        cal=dict(camera_intrinsic=np.eye(3).tolist(),camera_to_center_rotation=np.eye(3).tolist(),camera_to_center_translation_m=[0.,0.,1.5])
        cap['acquisition_metadata']=dict(dataset='nuScenes',native_ego_reference='rear_axle',center_offset_assumption_m=1.2,calibrations={'front':cal},ego_history=[dict(frame_id=frame['id'],time_id=frame['time_id'],center_xy_m=[0.,0.],yaw_rad=0.)],ego_speed_source='unit_test',vehicle_parameters_source='unit_test')
        return cap
    def test_calibration_reference_mismatch_rejected(self):
        cap=self.calibrated();out,p=ScriptedAgent().decide(cap)
        t1.construct(cap,out,p,True)
        cap['acquisition_metadata']['center_offset_assumption_m']=0.
        with self.assertRaises(Failure) as ctx:t1.construct(cap,out,p,True)
        self.assertEqual(ctx.exception.record['code'],'CALIBRATION_REFERENCE')
    def test_calibration_rotation_mismatch_rejected(self):
        cap=self.calibrated();cap['acquisition_metadata']['calibrations']['front']['camera_to_center_rotation'][0][0]=2.
        out,p=ScriptedAgent().decide(cap)
        with self.assertRaises(Failure) as ctx:t1.construct(cap,out,p,True)
        self.assertEqual(ctx.exception.record['code'],'CALIBRATION')
    def test_reference_ego_history_must_be_zero(self):
        cap=self.calibrated();cap['acquisition_metadata']['ego_history'][0]['center_xy_m']=[1.,0.]
        out,p=ScriptedAgent().decide(cap)
        with self.assertRaises(Failure) as ctx:t1.construct(cap,out,p,True)
        self.assertEqual(ctx.exception.record['code'],'EGO_HISTORY')
    def test_g2_map_holes_are_preserved(self):
        # Numerical unit test, NOT real model/G2 evidence.
        output,prov=ScriptedAgent().decide(capture());output['hypotheses']['road']['polygon_m']=rect(-5,5,-5,5)
        prov.update(source='online_model',image_sha256={'unit':'hash'},output_digest=digest(output))
        geom={'type':'Polygon','coordinates':[[[-5,-5],[5,-5],[5,5],[-5,5],[-5,-5]],[[-1,-1],[-1,1],[1,1],[1,-1],[-1,-1]]]}
        truth={'samples':{'unit':dict(image_sha256=prov['image_sha256'],reference_time_id='t100.000',coordinate_frame='ego_at_reference',road_polygon_m=None,road_geometry_geojson=geom,evaluation_roi_m=[-5,-5,5,5],scene_type='unit_test',model_object_ids={},objects=[])}}
        result=evaluate([dict(sample_id='unit',output=output,provenance=prov)],truth,dict(distance_edges_m=[10],evaluation_horizon_s=.2))
        self.assertAlmostEqual(result['scene_metrics'][0]['road_area_iou'],.96)
        self.assertFalse(result['thresholds_applied'])

@unittest.skipUnless(os.environ.get('NUSCENES_CASES_DIR'),'Opt-in real-data integration: set NUSCENES_CASES_DIR')
class RealNuScenesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root=Path(os.environ['NUSCENES_CASES_DIR']).resolve()
        cls.index=json.loads((cls.root/'runtime_index.json').read_text())
        cls.report=json.loads((cls.root/'preparation_report.json').read_text())
        cls.truth=json.loads((cls.root/'offline_evaluation/truth.json').read_text())
        cls.caps=[json.loads((cls.root/item['capture']).read_text()) for item in cls.index['cases']]
    def test_four_real_cases_have_40_acquired_images(self):
        self.assertEqual(len(self.caps),4);self.assertEqual(sum(len(c['observations']) for c in self.caps),40)
        for cap in self.caps:
            validate('capture_v2.schema.json',cap)
            for f in cap['observations']:
                self.assertTrue(Path(f['path']).is_file());self.assertFalse(f['path'].startswith('fixture:'))
    def test_no_future_frames_and_reference_is_real_timestamp(self):
        for cap in self.caps:
            m=cap['metadata']
            self.assertTrue(all(0<=m['reference_time_s']-f['capture_time_s']<=2.+1e-6 for f in cap['observations']))
            self.assertTrue(any(f['time_id']==m['reference_time_id'] and f['capture_time_s']==m['reference_time_s'] for f in cap['observations']))
    def test_no_actor_map_or_selection_labels_in_runtime(self):
        for cap in self.caps:
            self.assertNotIn('objects',cap);self.assertNotIn('road_geometry_geojson',cap);self.assertNotIn('model_object_ids',cap)
            text=json.dumps(cap)
            for row in self.report['cases']:
                self.assertNotIn(row['case_id'],text);self.assertNotIn(row['focal_instance'],text)
    def test_coordinate_roundtrips_and_25_intervals(self):
        for row in self.report['cases']:
            self.assertLess(row['coordinate_roundtrip_max_error_m'],1e-8)
            self.assertEqual(row['future_intervals_per_actor'],25)
        for gt in self.truth['samples'].values():
            self.assertTrue(gt['objects'])
            for actor in gt['objects']:
                self.assertEqual(len(actor['occupancy']),25);self.assertAlmostEqual(actor['occupancy'][-1]['end_s'],5.)
    def test_image_hash_binding(self):
        import hashlib
        for item,cap in zip(self.index['cases'],self.caps):
            expected=self.truth['samples'][item['sample_id']]['image_sha256']
            self.assertEqual(expected,{f['id']:hashlib.sha256(Path(f['path']).read_bytes()).hexdigest() for f in cap['observations']})
    def test_native_camera_translation_converted_once(self):
        for cap in self.caps:
            meta=cap['acquisition_metadata'];d=cap['vehicle']['center_to_rear_axle_m'];self.assertEqual(meta['native_ego_reference'],'rear_axle')
            self.assertAlmostEqual(d,meta['center_offset_assumption_m'])
            ref=next(x for x in meta['ego_history'] if x['time_id']==cap['metadata']['reference_time_id'])
            np.testing.assert_allclose(ref['center_xy_m'],[0,0],atol=1e-12)
            for cal in meta['calibrations'].values():
                R=np.asarray(cal['camera_to_center_rotation']);np.testing.assert_allclose(R.T@R,np.eye(3),atol=1e-10)
                self.assertGreater(cal['camera_to_center_translation_m'][2],0)
