from copy import deepcopy
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agentic.contracts import Failure,digest
from agentic.fixtures import capture,ScriptedAgent,coasting_fault,rect
from agentic import t1,t2,t3,t4
from agentic.geometry import integrate,rear_state,center_state,project_ground
from agentic.runtime import ClosedLoop,RunLog,SimulatedExecutor

class V2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cap=capture();cls.output,cls.prov=ScriptedAgent().decide(cls.cap)
        cls.scene=t1.construct(cls.cap,cls.output,cls.prov,True)
        cls.corridor=t2.build(cls.scene);cls.traj=t3.solve(cls.scene,cls.corridor);cls.ver=t4.verify(cls.scene,cls.traj)
    def construct(self,out=None,cap=None):
        out=out or deepcopy(self.output);p=deepcopy(self.prov);p['output_digest']=digest(out)
        return t1.construct(cap or deepcopy(self.cap),out,p,True)
    def reject(self,fn,code):
        with self.assertRaises(Failure) as ctx:fn()
        self.assertEqual(ctx.exception.record['code'],code)
    def test_ipopt_actual_feasible_and_verified(self):
        self.assertEqual(self.ver['verdict'],'PASS',self.ver['violations'][:3]);self.assertIn('ipopt',self.traj['solver']['name']);self.assertGreater(self.ver['released_prefix_s'],0)
    def test_legacy_schema_keeps_old_profile(self):
        import driving_tools
        request=json.loads((Path(__file__).parents[1]/'examples/pedestrian_crossing.request.json').read_text())
        self.assertEqual(driving_tools.pipeline(request,allow_synthetic=True)['verification']['verdict'],'PASS')
    def test_coordinate_and_acquisition_references(self):
        out=deepcopy(self.output);out['hypotheses']['reference_time_id']='invented'
        self.reject(lambda:self.construct(out),'DYNAMIC_ALIGNMENT')
        out=deepcopy(self.output);out['hypotheses']['objects'][0]['time_ids']=['old']
        self.reject(lambda:self.construct(out),'MISSING_EVIDENCE')
    def test_reference_pose_must_be_rebased(self):
        cap=deepcopy(self.cap);cap['ego']['x_m']=1.
        self.reject(lambda:self.construct(cap=cap),'EGO_FRAME')
    def test_unknown_never_silently_zero(self):
        out=deepcopy(self.output);out['hypotheses']['objects'][0]['velocity_mps']=None
        self.assertIsNone(self.construct(out)['hypotheses']['objects'][0]['velocity_mps'])
        out['hypotheses']['objects'][0]['uncertainty_m']=None
        self.reject(lambda:self.construct(out),'UNKNOWN_UNCERTAINTY')
    def test_occupancy_missing_and_alternatives_preserved(self):
        out=deepcopy(self.output);out['hypotheses']['objects'][0]['occupancy'].pop()
        self.reject(lambda:self.construct(out),'OCCUPANCY_GAP')
        out=deepcopy(self.output);out['hypotheses']['objects'][0]['occupancy'][0]['alternatives_m'].append(rect(25,26,3,4))
        self.assertEqual(len(self.construct(out)['hypotheses']['objects'][0]['occupancy'][0]['alternatives_m']),2)
    def test_evidence_scale_and_clock_conflict(self):
        out=deepcopy(self.output);out['hypotheses']['scale_basis']='unknown'
        self.reject(lambda:self.construct(out),'MISSING_SCALE')
        cap=deepcopy(self.cap);cap['observations'].append(dict(cap['observations'][0],id='another',capture_time_s=99.))
        self.reject(lambda:self.construct(cap=cap),'CLOCK_CONFLICT')
    def test_scene_content_and_revision_bound(self):
        scene=deepcopy(self.scene);scene['metadata']['revision']+=1
        self.reject(lambda:t4.verify(scene,self.traj),'SCENE_VERSION_MISMATCH')
        scene=deepcopy(self.scene);scene['hypotheses']['road']['uncertainty_m']+=.1
        self.reject(lambda:t3.solve(scene,self.corridor),'SCENE_VERSION_MISMATCH')
    def test_invalid_geometry_and_vehicle_size(self):
        out=deepcopy(self.output);out['hypotheses']['road']['polygon_m']=[[0,0],[1,1],[0,1],[1,0]]
        self.reject(lambda:self.construct(out),'INVALID_GEOMETRY')
        cap=deepcopy(self.cap);cap['vehicle']['half_width_m']=9
        self.reject(lambda:t2.build(self.construct(cap=cap)),'CORRIDOR_EMPTY')
    def test_synthetic_requires_explicit_opt_in(self):
        self.reject(lambda:t1.construct(self.cap,self.output,self.prov),'SOURCE')
    def test_dynamics_and_reference_points(self):
        traj=deepcopy(self.traj);traj['points'][4]['rear_x_m']+=.1
        codes={x['code'] for x in t4.verify(self.scene,traj)['violations']}
        self.assertIn('DYNAMICS_RESIDUAL',codes);self.assertIn('REFERENCE_POINT',codes)
    def test_bicycle_turn_exact_integration(self):
        q=[-1.2,0,0,4.];v=self.cap['vehicle'];whole=integrate(q,.3,.2,.2,v);half=integrate(integrate(q,.3,.2,.1,v),.3,.2,.1,v)
        for a,b in zip(whole,half):self.assertAlmostEqual(a,b,places=10)
        self.assertGreater(whole[2],0);self.assertAlmostEqual(center_state(q,v)[0],0)
    def test_nonlinear_solver_generates_curved_motion(self):
        cap=capture(speed=2.);out,prov=ScriptedAgent().decide(cap)
        out['decision']['target_region']=[[-8,-3],[45,4],[45,8],[-8,1]];out['decision']['behavior']='hold_lane';out['decision']['stop']=None;out['decision']['desired_speed_mps']=3.
        scene=self.construct(out,cap);corr=t2.build(scene);traj=t3.solve(scene,corr)
        self.assertEqual(t4.verify(scene,traj)['verdict'],'PASS');self.assertGreater(abs(traj['points'][-1]['y_m']),.1)
    def test_raw_scene_collision_with_no_corridor_input(self):
        coasting=coasting_fault(self.scene,self.traj);ver=t4.verify(self.scene,coasting)
        self.assertEqual(ver['released_prefix_s'],0);codes={x['code'] for x in ver['violations']}
        self.assertTrue({'BODY_COLLISION','STOP_BOUNDARY','STOP_DEADLINE'}<=codes)
    def test_sampling_interval_collision_is_checked(self):
        # Tiny vehicle + high speed: obstacle is outside BOTH endpoint body disks,
        # but the swept motion between them collides. This cannot pass by point checks.
        out=deepcopy(self.output);cap=capture(speed=12.)
        cap['vehicle'].update(front_m=.05,rear_m=.05,half_width_m=.02,center_to_rear_axle_m=.02,wheelbase_m=.1)
        out['decision']['behavior']='resume';out['decision']['stop']=None
        out['hypotheses']['objects'][0]['position_m']=[1.2,0.]
        for occ in out['hypotheses']['objects'][0]['occupancy']:occ['alternatives_m']=[rect(1.19,1.21,-.01,.01)]
        scene=self.construct(out,cap)
        tr=coasting_fault(scene,self.traj);ver=t4.verify(scene,tr)
        self.assertIn('BODY_COLLISION',{x['code'] for x in ver['violations']})
        from agentic.geometry import disk_radius,polygon
        from shapely.geometry import Point
        obstacle=polygon(out['hypotheses']['objects'][0]['occupancy'][0]['alternatives_m'][0])
        self.assertTrue(all(obstacle.distance(Point([p['rear_x_m'],p['rear_y_m']]))>disk_radius(cap['vehicle']) for p in tr['points'][:2]))
    def test_deadline_between_points_and_intent(self):
        out=deepcopy(self.output);out['decision']['stop']['deadline_s']=.19
        scene=self.construct(out);tr=coasting_fault(scene,self.traj);tr['metadata']=scene['metadata'];tr['scene_digest']=digest(scene)
        ver=t4.verify(scene,tr)
        self.assertIn('STOP_DEADLINE',{x['code'] for x in ver['violations']})
    def test_infeasible_optimizer_does_not_relax_stop(self):
        out=deepcopy(self.output);out['decision']['stop']['deadline_s']=.2
        scene=self.construct(out);corr=t2.build(scene)
        self.reject(lambda:t3.solve(scene,corr,max_iter=80),'OPTIMIZATION_FAILED')
        self.assertEqual(scene['decision']['behavior'],'stop_yield')
    def test_prefix_once_and_invalidate_on_new_observation(self):
        executor=SimulatedExecutor();executor.observe(self.cap)
        executor.execute(self.scene,self.traj,self.ver,100.)
        self.reject(lambda:executor.execute(self.scene,self.traj,self.ver,100.),'PREFIX_REUSED')
        executor.observe(capture(t=100.2))
        self.reject(lambda:executor.execute(self.scene,self.traj,self.ver,100.),'STALE_OBSERVATION')
    def test_feedback_revises_agent_every_round(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent=ScriptedAgent();loop=ClosedLoop(agent,RunLog(tmp),True)
            result=loop.round(self.cap,coasting_fault)
            self.assertEqual(result['status'],'executed_simulated_prefix');self.assertEqual(result['revisions'],1);self.assertEqual(agent.calls,2)
            self.assertEqual(result['scene']['decision']['stop']['center_x_max_m'],10.)
    def test_traffic_evidence_cannot_be_ignored(self):
        out=deepcopy(self.output);out['decision'].update(behavior='resume',stop=None)
        out['hypotheses']['traffic_evidence']=[dict(rule='stop_required',state='active',frame_ids=['fixture100.000'],time_ids=['t100.000'])]
        self.reject(lambda:self.construct(out),'TRAFFIC_INTENT_CONFLICT')
        out['hypotheses']['traffic_evidence'][0]['state']=None
        self.reject(lambda:self.construct(out),'UNKNOWN_TRAFFIC_CONDITION')

    def test_four_independent_interfaces_and_error_classes(self):
        from agentic.tools import call_tool
        def invoke(tool,payload):return call_tool(dict(kind='request',tool=tool,request_id='unit',payload=payload),allow_synthetic=True)
        response=invoke('T1',dict(capture=self.cap,output=self.output,provenance=self.prov))
        self.assertEqual(response['status'],'ok')
        response=invoke('T4',dict(scene=self.scene,trajectory=coasting_fault(self.scene,self.traj)))
        self.assertEqual(response['status'],'ok');self.assertEqual(response['payload']['verification']['verdict'],'FAIL')
        response=invoke('T2',dict(scene=self.scene,unexpected=True))
        self.assertEqual(response['error']['category'],'interface_error')

    def test_projection_requires_calibration(self):
        point=project_ground([0,0],[[1,0,0],[0,1,0],[0,0,1]],[[1,0,0],[0,-1,0],[0,0,-1]],[0,0,2])
        self.assertEqual(point,[0,0]);self.reject(lambda:project_ground([0,0],[[1,0,0],[0,1,0],[0,0,1]],[[1,0,0],[0,1,0],[0,0,1]],[0,0,2]),'PROJECTION')

if __name__=='__main__':unittest.main()
