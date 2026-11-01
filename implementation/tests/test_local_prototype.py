"""Minimal prototype isolation and actual short-prefix physics regression."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agentic import t1,t2,t3,t4
from agentic.contracts import Failure,digest
from agentic.fixtures import rect
from agentic.fixtures_v3 import metric_case
from agentic.runtime import SimulatedExecutor


def case():
    cap,out,prov=metric_case(asynchronous=False,speed=6.)
    cap['metadata']['horizon_s']=2.;cap['metadata']['valid_until_s']=102.
    d=out['decision'];d.update(behavior='hold_lane',stop=None,yield_to=[],desired_speed_mps=6.)
    d['hard_requirements']=['target_region','vehicle_limits','collision_free','road']
    for p in out['hypotheses']['premises']:p['end_s']=2.
    for actor in out['hypotheses']['objects']:
        for src in actor['occupancy']:src['end_s']=2.
    out['hypotheses']['premises'].append(dict(id='prototype_local_scope',object_ids=[],scenario_ids=[],
        start_s=0.,end_s=2.,space_polygon_m=rect(-100,100,-100,100),statement='AUTHORED local conditional software fixture only.'))
    prov.update(prompt_version='driving_prototype_v1',experiment_profile='local_conditional_2s_prototype_v1',output_digest=digest(out))
    return cap,out,prov


class LocalPrototypeTests(unittest.TestCase):
    def test_explicit_prompt_profile_horizon_and_scope_are_required(self):
        for changed in ('profile','horizon','scope'):
            cap,out,prov=case()
            if changed=='profile':prov.pop('experiment_profile')
            elif changed=='horizon':cap['metadata']['horizon_s']=5.
            else:out['hypotheses']['premises'][-1]['space_polygon_m']=None;prov['output_digest']=digest(out)
            with self.assertRaises(Failure):t1.construct(cap,out,prov,True)

    def test_authored_two_second_moving_tools_and_prefix(self):
        cap,out,prov=case();scene=t1.construct(cap,out,prov,True)
        corridor=t2.build(scene,policy='moving_seed');traj=t3.solve(scene,corridor);verified=t4.verify(scene,traj)
        self.assertEqual(verified['verdict'],'PASS')
        executor=SimulatedExecutor();executor.observe(cap)
        execution=executor.execute(scene,traj,verified,cap['metadata']['reference_time_s'])
        self.assertGreater(execution['center_displacement_m'][0],1.)
        self.assertGreater(execution['speed_mps'],0.)


if __name__=='__main__':unittest.main()
