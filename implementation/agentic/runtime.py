"""Bounded agent/tool revision loop and explicitly simulated short-prefix execution."""
from copy import deepcopy
from pathlib import Path
import json
import platform
import subprocess
import uuid
from datetime import datetime,timezone
from .contracts import Failure,need,digest,identity
from . import t1,t2,t3,t4
from .geometry import rear_state,integrate,center_state

class RunLog:
    def __init__(self,root='runs'):
        self.path=Path(root)/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8]);self.path.mkdir(parents=True,exist_ok=False)
        self.count=0
        def git(*args):
            p=subprocess.run(['git',*args],capture_output=True,text=True,cwd=Path(__file__).resolve().parents[2]);return p.stdout.strip() if p.returncode==0 else 'unavailable'
        source=Path(__file__).resolve().parents[1]
        source_hashes={str(p.relative_to(source)):__import__('hashlib').sha256(p.read_bytes()).hexdigest() for folder in ['agentic','schemas','prompts'] for p in (source/folder).rglob('*') if p.is_file() and '__pycache__' not in str(p)}
        self.write('source-hashes',source_hashes)
        self.write('manifest',dict(code_revision=git('rev-parse','HEAD'),code_dirty=bool(git('status','--porcelain')),python=platform.python_version(),execution='SIMULATION_ONLY',schema='2.0.0'))
    def write(self,stage,data):
        self.count+=1
        (self.path/f'{self.count:04d}-{stage}.json').write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False)+'\n')

class SimulatedExecutor:
    """Consumes verified prefixes once. No hidden world, perception or true actor state."""
    def __init__(self):self.current=None;self.consumed=set()
    def observe(self,capture):
        self.current=digest(capture)
    def execute(self,scene,trajectory,verification,now_s):
        identity(scene,verification);identity(scene,trajectory)
        token=digest(verification);cap=scene['capture'];m=scene['metadata']
        need(self.current==digest(cap),'STALE_OBSERVATION','New observation invalidated old execution permission')
        need(verification['verdict']=='PASS' and verification['released_prefix_s']>0,'EXECUTION_REJECTED','No verified prefix')
        need(verification['trajectory_digest']==digest(trajectory),'TRAJECTORY_CHANGED','Trajectory changed after verification')
        need(token not in self.consumed,'PREFIX_REUSED','Prefix already consumed')
        need(abs(now_s-m['reference_time_s'])<1e-7 and now_s+verification['released_prefix_s']<=m['valid_until_s'],'EXECUTION_TIME','Observation clock or validity mismatch')
        dt=verification['released_prefix_s'];p=trajectory['points'][0]
        q=integrate(rear_state(cap['ego'],cap['vehicle']),p['acceleration_mps2'],p['steer_rad'],dt,cap['vehicle']);c=center_state(q,cap['vehicle'])
        self.consumed.add(token)
        return dict(interface='SIMULATED_EXECUTION_NO_VEHICLE_CONTROL',elapsed_s=dt,center_displacement_m=[c[0]-cap['ego']['x_m'],c[1]-cap['ego']['y_m']],yaw_delta_rad=c[2]-cap['ego']['yaw_rad'],speed_mps=max(0.,c[3]))

class ClosedLoop:
    def __init__(self,agent,log,allow_synthetic=False,max_revisions=2):
        self.agent=agent;self.log=log;self.allow_synthetic=allow_synthetic;self.max_revisions=max_revisions;self.executor=SimulatedExecutor()
        log.write('loop-config',dict(max_revisions=max_revisions,allow_synthetic=allow_synthetic,agent_class=type(agent).__name__,adapter=getattr(agent,'public_config',None)))
    def round(self,capture,fault=None):
        cap=deepcopy(capture);feedback=None
        for attempt in range(self.max_revisions+1):
            self.executor.observe(cap);self.log.write('capture',cap)
            try:
                output,provenance=self.agent.decide(cap,feedback)
                self.log.write('agent',dict(output=output,provenance=provenance))
                scene=t1.construct(cap,output,provenance,self.allow_synthetic);self.log.write('T1',scene)
                corridor=t2.build(scene);self.log.write('T2',corridor)
                traj=t3.solve(scene,corridor)
                if fault and attempt==0:traj=fault(scene,traj)
                self.log.write('T3',traj)
                verification=t4.verify(scene,traj);self.log.write('T4',verification)
                if verification['verdict']=='FAIL':
                    feedback=dict(category='verification_rejected',violations=verification['violations'])
                else:
                    execution=self.executor.execute(scene,traj,verification,cap['metadata']['reference_time_s']);self.log.write('simulation',execution)
                    return dict(status='executed_simulated_prefix',scene=scene,trajectory=traj,verification=verification,execution=execution,revisions=attempt)
            except Failure as exc:feedback=exc.record
            self.log.write('feedback',feedback)
            cap['metadata']['revision']+=1
        return dict(status='blocked_no_execution',feedback=feedback)
