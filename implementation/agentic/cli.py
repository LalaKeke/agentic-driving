import argparse
from copy import deepcopy
import json
from pathlib import Path
from .contracts import Failure,need
from .runtime import ClosedLoop,RunLog
from .fixtures import ScriptedAgent,capture,coasting_fault

def load(p):return json.loads(Path(p).read_text())
def main():
    parser=argparse.ArgumentParser(description='Research pipeline; SIMULATION ONLY')
    sub=parser.add_subparsers(dest='command',required=True)
    demo=sub.add_parser('demo');demo.add_argument('--runs',default='runs');demo.add_argument('--rounds',type=int,default=26)
    online=sub.add_parser('online');online.add_argument('--capture',required=True);online.add_argument('--runs',default='runs');online.add_argument('--sample-id',required=True);online.add_argument('--calls',type=int,default=1);online.add_argument('--plan',action='store_true')
    replay=sub.add_parser('replay');replay.add_argument('--captures',nargs='+',required=True);replay.add_argument('--outputs',nargs='+',required=True);replay.add_argument('--runs',default='runs');replay.add_argument('--allow-synthetic',action='store_true')
    g=sub.add_parser('g2');g.add_argument('--records',nargs='+',required=True);g.add_argument('--truth',required=True);g.add_argument('--config',required=True);g.add_argument('--out',required=True)
    args=parser.parse_args()
    if args.command=='g2':
        from .g2 import evaluate
        result=evaluate([load(p) for p in args.records],load(args.truth),load(args.config));Path(args.out).write_text(json.dumps(result,ensure_ascii=False,indent=2));print(args.out);return
    log=RunLog(args.runs)
    if args.command=='demo':
        agent=ScriptedAgent();loop=ClosedLoop(agent,log,True);speed=6.;results=[];t=100.
        for i in range(args.rounds):
            result=loop.round(capture(t,speed,i*4),coasting_fault if i==0 else None)
            results.append(dict(round=i,status=result['status'],revisions=result.get('revisions'),speed_mps=result.get('execution',{}).get('speed_mps')))
            if result['status']=='blocked_no_execution':break
            speed=result['execution']['speed_mps'];t=round(t+result['execution']['elapsed_s'],6)
            agent.ego_x+=result['execution']['center_displacement_m'][0]
        log.write('summary',dict(source='synthetic_fixture',agent_calls=agent.calls,rounds=results))
        need(len(results)==args.rounds and all(x['status']=='executed_simulated_prefix' for x in results),'DEMO_FAILED','Inspect feedback in run log')
        if args.rounds>=24:
            need(min(x['speed_mps'] for x in results)<1e-5 and speed>1.,'DEMO_STOP_RESUME','Default demo must fully stop then resume')
        print(json.dumps(dict(run=str(log.path),source='synthetic_fixture',agent_calls=agent.calls,rounds=len(results),first_round_revisions=results[0]['revisions'],minimum_speed_mps=min(x['speed_mps'] for x in results),final_speed_mps=speed)))
    elif args.command=='online':
        from .adapter import OnlineAgent
        agent=OnlineAgent();log.write('adapter-config',agent.public_config);cap=load(args.capture);need(1<=args.calls<=20,'CALL_COUNT','calls must be 1..20')
        if args.plan:
            result=ClosedLoop(agent,log).round(cap);log.write('summary',dict(status=result['status']))
        else:
            log.write('capture',cap)
            for _ in range(args.calls):
                output,provenance=agent.decide(cap)
                log.write('model-record',dict(sample_id=args.sample_id,output=output,provenance=provenance))
        print(log.path)
    elif args.command=='replay':
        need(len(args.captures)==len(args.outputs),'REPLAY','Need paired capture/output records')
        from . import t1,t2,t3,t4
        from .runtime import SimulatedExecutor
        for c,o in zip(args.captures,args.outputs):
            cap=load(c);record=load(o);log.write('capture',cap);log.write('record',record)
            scene=t1.construct(cap,record['output'],record['provenance'],args.allow_synthetic);log.write('T1',scene)
            corridor=t2.build(scene);log.write('T2',corridor);traj=t3.solve(scene,corridor);log.write('T3',traj)
            verified=t4.verify(scene,traj);log.write('T4',verified)
            if verified['verdict']=='PASS':
                exe=SimulatedExecutor();exe.observe(cap);log.write('simulation',exe.execute(scene,traj,verified,cap['metadata']['reference_time_s']))
        print(log.path)
if __name__=='__main__':
    try:main()
    except Failure as e:
        print(json.dumps(e.record,ensure_ascii=False));raise SystemExit(2)
