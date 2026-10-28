"""Bounded agent/tool revision loop and explicitly simulated short-prefix execution."""
from copy import deepcopy
from pathlib import Path
import json
import platform
import os
import time
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
    def __init__(self,agent,log,allow_synthetic=False,max_revisions=2,corridor_policy=None):
        self.agent=agent;self.log=log;self.allow_synthetic=allow_synthetic;self.max_revisions=max_revisions;self.executor=SimulatedExecutor()
        self.corridor_policy=corridor_policy or os.environ.get('DRIVING_CORRIDOR_POLICY','initial_anchor')
        log.write('loop-config',dict(max_revisions=max_revisions,allow_synthetic=allow_synthetic,agent_class=type(agent).__name__,adapter=getattr(agent,'public_config',None),corridor_policy=self.corridor_policy))
    def round(self,capture,fault=None):
        from .diagnostics import diagnose
        cap=deepcopy(capture);feedback=None
        statistics=dict(model_attempts=0,model_responses=0,failures=[],tool_calls={name:0 for name in ['T1','T2','T3','T4','Execute']})
        def finish(value):
            from .acceptance import assess_round
            value['statistics']=deepcopy(statistics)
            value['acceptance']=assess_round(value,capture)
            self.log.write('round-acceptance',value['acceptance'])
            self.log.write('round-statistics',statistics)
            return value
        for attempt in range(self.max_revisions+1):
            self.executor.observe(cap);self.log.write('capture',cap);stage='Agent';output=None
            started=time.monotonic()
            def event(state,name,**extra):
                self.log.write('stage-event',dict(stage=name,state=state,attempt=attempt,revision=cap['metadata']['revision'],capture_digest=digest(cap),**extra))
            try:
                statistics['model_attempts']+=1;event('started','Agent')
                output,provenance=self.agent.decide(cap,feedback)
                statistics['model_responses']+=1;event('completed','Agent',elapsed_s=time.monotonic()-started)
                self.log.write('agent',dict(output=output,provenance=provenance))
                artifacts={}
                for name,call in [
                    ('T1',lambda:t1.construct(cap,output,provenance,self.allow_synthetic)),
                    ('T2',lambda:t2.build(artifacts['T1'],policy=self.corridor_policy)),
                    ('T3',lambda:t3.solve(artifacts['T1'],artifacts['T2'])),
                    ('T4',lambda:t4.verify(artifacts['T1'],artifacts['T3']))]:
                    stage=name;started=time.monotonic();statistics['tool_calls'][name]+=1;event('started',name)
                    artifact=call()
                    if name=='T3' and fault and attempt==0:artifact=fault(artifacts['T1'],artifact)
                    artifacts[name]=artifact;self.log.write(name,artifact);event('completed',name,elapsed_s=time.monotonic()-started)
                scene,corridor,traj,verification=(artifacts[n] for n in ('T1','T2','T3','T4'))
                if verification['verdict']=='FAIL':
                    feedback=dict(category='verification_rejected',failed_stage='T4',violations=verification['violations'])
                    statistics['failures'].append(dict(stage='T4',attempt=attempt,code='VERIFICATION_REJECTED'))
                else:
                    stage='Execute';started=time.monotonic();statistics['tool_calls'][stage]+=1;event('started',stage)
                    execution=self.executor.execute(scene,traj,verification,cap['metadata']['reference_time_s']);self.log.write('simulation',execution)
                    receipt=dict(capture_digest=digest(scene['capture']),scene_digest=digest(scene),trajectory_digest=digest(traj),verification_digest=digest(verification),executed_at_s=cap['metadata']['reference_time_s'],consumed_once=digest(verification) in self.executor.consumed,execution_digest=digest(execution))
                    self.log.write('execution-receipt',receipt);event('completed',stage,elapsed_s=time.monotonic()-started)
                    return finish(dict(status='executed_simulated_prefix',scene=scene,corridor=corridor,agent_output=output,provenance=provenance,trajectory=traj,verification=verification,execution=execution,execution_receipt=receipt,revisions=attempt))
            except Failure as exc:
                feedback=deepcopy(exc.record);feedback['failed_stage']=stage
                statistics['failures'].append(dict(stage=stage,attempt=attempt,code=feedback['code']))
                event('failed',stage,elapsed_s=time.monotonic()-started,failure_code=feedback['code'])
                if output is not None and stage in ('T1','T2'):
                    feedback['contract_diagnostics']=diagnose(cap,output)
                # An unavailable transport/account is not a semantic scene revision.
                if stage=='Agent' and feedback['code'] in ('CODEX_TIMEOUT','CODEX_TRANSPORT','CODEX_MODEL_UNAVAILABLE','CODEX_PROCESS','CODEX_CLI_STATE','CODEX_CHATGPT_LOGIN_REQUIRED','API_TRANSPORT','API_HTTP','API_CONFIGURATION'):
                    self.log.write('feedback',feedback)
                    return finish(dict(status='blocked_no_execution',feedback=feedback,revisions=attempt))
            self.log.write('feedback',feedback)
            cap['metadata']['revision']+=1
        return finish(dict(status='blocked_no_execution',feedback=feedback,revisions=self.max_revisions))
