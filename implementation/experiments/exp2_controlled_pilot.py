"""One fixed real-image pilot, maximum two charged model turns, no retries.

This runtime imports no GT or evaluation module. It cannot choose a window or
change its output path: those are frozen in the committed configuration.
"""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'implementation'))
from agentic import t1,t2,t3,t4
from agentic.acceptance import assess_round
from agentic.contracts import Failure,digest,need,validate
from agentic.evidence_io import reserve_directory,write_json_new
from agentic.metric_diagnostics import diagnose_metric_output
from agentic.pilot_transport import PilotSubscriptionAgent
from agentic.runtime import SimulatedExecutor
from agentic.wire_schema_v2 import load_versioned_wire_bundle as load_wire_bundle, WireSchemaConflict


def read(path):return json.loads(Path(path).read_text())
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def utc():return datetime.now(timezone.utc).isoformat()


class CallBudget:
    """Exclusive, append-only debits. Failures consume budget; no resume mode."""
    def __init__(self,path):
        self.path=reserve_directory(path);self.charges=[];self.outcomes=[]
    def claim(self,capture,reason):
        need(len(self.charges)<2,'PILOT_BUDGET','Two-call hard budget exhausted')
        n=len(self.charges)+1
        row=dict(attempt=n,phase='initial' if n==1 else 'semantic_revision',
                 charged_before_invocation=True,failed_attempt_consumes_budget=True,
                 timestamp_utc=utc(),capture_digest=digest(capture),reason=reason)
        write_json_new(self.path/f'{n:02d}-charge.json',row);self.charges.append(row)
        return n
    def finish(self,n,wire,summary):
        row=dict(attempt=n,turn_start_requests=wire.get('turn_start_requests',0),
                 requested_model=wire.get('requested_model'),cli_resolved_alias=wire.get('cli_resolved_alias'),
                 server_reported_snapshot=None,transport_summary=summary,charged=1)
        write_json_new(self.path/f'{n:02d}-outcome.json',row);self.outcomes.append(row)
    def seal(self):
        sent=sum(x['turn_start_requests'] for x in self.outcomes)
        need(sent<=len(self.charges)<=2,'PILOT_BUDGET','Observed request count violates hard budget')
        value=dict(limit=2,charged=len(self.charges),turn_start_requests=sent,
                   unused=2-len(self.charges),automatic_transport_retries=0,
                   request_max_retries=0,stream_max_retries=0,charges=self.charges,outcomes=self.outcomes,
                   snapshot_identity='NOT_EXPOSED',ended=True,no_automatic_batch=True)
        write_json_new(self.path/'final.json',value);return value


def verify_inputs(config,config_path):
    need(config['output_contract']=='3.0.0-exp2' and config['prompt_version']=='driving_v3_exp2'
         and config['coverage_profile']=='whole_interval_sources_v3_exp2','PILOT_CONFIG','Frozen experimental identifiers changed')
    need(config['max_model_invocations']==2 and config['max_semantic_revisions']==1
         and config['automatic_transport_retries']==0,'PILOT_CONFIG','No hidden or expanded budget')
    need(config['sample_id']=='ac452a60e8b34a7080c938c904b23057','PILOT_WINDOW','Preregistered window cannot be replaced')
    if 'wire_schema_bundle' in config:
        pin=config['wire_schema_bundle']
        try:
            _,manifest=load_wire_bundle(ROOT/pin['path'],ROOT/'implementation/schemas/agent_output_v3_exp2.schema.json')
        except WireSchemaConflict as exc:
            raise Failure('WIRE_SCHEMA_INVALID','Frozen wire bundle integrity failed',conflicts=exc.conflicts) from None
        need(all(pin.get(k)==manifest[k] for k in ('source_schema_sha256','wire_schema_sha256','wire_schema_version','generator_version')),
             'WIRE_SCHEMA_PIN','Wire schema identifiers or SHA differ from registration')
        need(pin.get('transformation_manifest_sha256')==sha(ROOT/pin['path']/'transformation_manifest.json'),
             'WIRE_SCHEMA_PIN','Transformation manifest bytes differ from registration')
    commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    for name,expected in config['source_sha256'].items():
        p=ROOT/name
        need(sha(p)==expected,'PILOT_SOURCE_CHANGED','Frozen source changed',path=name)
        need(p.read_bytes()==subprocess.check_output(['git','show',commit+':'+name],cwd=ROOT),
             'PILOT_SOURCE_UNCOMMITTED','Runtime source must match the committed version',path=name)
    rel=str(config_path.resolve().relative_to(ROOT))
    need(config_path.read_bytes()==subprocess.check_output(['git','show',commit+':'+rel],cwd=ROOT),
         'PILOT_CONFIG_UNCOMMITTED','Commit the concrete pilot before inference')
    cap_path=ROOT/config['capture_path'];need(sha(cap_path)==config['capture_sha256'],'PILOT_CAPTURE','Capture bytes changed')
    cap=read(cap_path);validate('capture_v2.schema.json',cap)
    need(cap['metadata']['horizon_s']==5 and cap['metadata']['step_s']==.2,'PILOT_GRID','Frozen 5s/.2s grid changed')
    need(digest(cap['vehicle'])==config['vehicle_digest'] and digest(cap['safety'])==config['safety_digest'],
         'PILOT_PHYSICS','Original vehicle or safety configuration changed')
    need({f['id']:sha(f['path']) for f in cap['observations']}==config['image_sha256'],'PILOT_IMAGES','Actual image bytes changed')
    return cap,commit


def stages():
    return {name:dict(status='NOT_REACHED') for name in
            ('SERVICE','NATIVE_OUTPUT','T1','T2','T3','T4','EXECUTION','ACCEPTOR')}


def not_reached_acceptance(reason):
    return dict(status='NOT_REACHED',called=False,strict_live_physical_success=False,
                software_success=False,motion_class='NOT_REACHED',reasons=[reason])


def run_tools(cap,initial,output,prov,revision,folder,ledger,config,check):
    artifacts={};failure=None
    for name,fn in [
        ('T1',lambda:t1.construct(cap,output,prov)),
        ('T2',lambda:t2.build(artifacts['T1'],policy=config['corridor_policy'])),
        ('T3',lambda:t3.solve(artifacts['T1'],artifacts['T2'])),
        ('T4',lambda:t4.verify(artifacts['T1'],artifacts['T3']))]:
        check();started=time.monotonic()
        ledger[name]=dict(status='STARTED',started_at_utc=utc())
        write_json_new(folder/(name+'-started.json'),ledger[name])
        try:
            value=fn();artifacts[name]=value
            write_json_new(folder/(name+'.json'),value)
            ledger[name].update(status='PASS',elapsed_s=time.monotonic()-started)
            if name=='T4' and value['verdict']!='PASS':
                ledger[name]['status']='FAIL'
                failure=dict(category='verification_rejected',failed_stage='T4',violations=value['violations'])
        except Failure as exc:
            failure=dict(exc.record,failed_stage=name)
            ledger[name].update(status='FAIL',elapsed_s=time.monotonic()-started,failure=failure)
            write_json_new(folder/(name+'-failure.json'),failure)
        if failure:break
    diag=diagnose_metric_output(cap,output,prov,
        first_failure=failure if failure and failure.get('failed_stage')=='T1' else None,
        record_path='record.json')
    write_json_new(folder/'all_contract_diagnostics.json',diag)
    if failure:
        write_json_new(folder/'acceptor.json',not_reached_acceptance(failure))
        # A trusted-input/provenance failure is not repaired by another model.
        terminal=failure.get('code','').startswith('PILOT_') or failure.get('code') in ('SOURCE','REFERENCE_TIME','CALIBRATION_REFERENCE','EGO_HISTORY')
        return None,dict(failure=failure,contract_diagnostics=diag,previous_native_output=output,
            repair_request='Revise only evidence-supported decisions/hypotheses. Never fill, narrow or remove unresolved or adverse modes to pass.'),terminal
    scene,corridor,traj,verified=(artifacts[n] for n in ('T1','T2','T3','T4'))
    check();executor=SimulatedExecutor();executor.observe(cap)
    execution=executor.execute(scene,traj,verified,cap['metadata']['reference_time_s'])
    receipt=dict(capture_digest=digest(scene['capture']),scene_digest=digest(scene),trajectory_digest=digest(traj),
                 verification_digest=digest(verified),executed_at_s=cap['metadata']['reference_time_s'],
                 consumed_once=digest(verified) in executor.consumed,execution_digest=digest(execution))
    ledger['EXECUTION']=dict(status='PASS',interface='SIMULATED_EXECUTION_NO_VEHICLE_CONTROL',consumed_prefix_s=verified['released_prefix_s'])
    write_json_new(folder/'execution.json',execution);write_json_new(folder/'execution_receipt.json',receipt)
    result=dict(status='executed_simulated_prefix',scene=scene,corridor=corridor,trajectory=traj,verification=verified,
                agent_output=output,provenance=prov,execution=execution,execution_receipt=receipt,revisions=revision)
    acceptance=assess_round(result,initial)
    result['acceptance']=acceptance
    ledger['ACCEPTOR']=dict(status='PASS' if acceptance['strict_live_physical_success'] else 'FAIL',result=acceptance)
    write_json_new(folder/'acceptor.json',acceptance);write_json_new(folder/'round_result.json',result)
    # Once any prefix has been consumed, no second call may reuse this image window.
    return result,None,True


def run(config_path):
    config_path=Path(config_path);config=read(config_path)
    # Offline prepared registrations are deliberately non-executable. A future
    # authorization must freeze a new concrete config; no old unused allowance
    # is inherited by adding a wire bundle to an ended trial.
    if 'wire_schema_bundle' in config or 'execution_authorized' in config:
        need(config.get('execution_authorized') is True,'PILOT_NOT_AUTHORIZED',
             'Prepared trial is launched=false and has zero authorized calls; no preflight or model request is allowed')
    if 'wire_schema_bundle' in config:
        relation=config.get('old_pilot_relation',{})
        need(config.get('authorized_model_invocations')==2 and relation.get('approved_limit')==2
             and relation.get('inherited_unused_calls')==0 and relation.get('new_independent_budget') is True,
             'PILOT_NOT_AUTHORIZED','A wire trial requires its own explicitly frozen two-call authorization; old unused calls cannot be inherited')
    initial,commit=verify_inputs(config,config_path)
    root=reserve_directory(ROOT/config['runtime_path'])
    write_json_new(root/'frozen_runtime_config.json',dict(config=config,config_sha256=sha(config_path),
        source_commit=commit,baseline_commit=config['baseline_commit'],started_at_utc=utc(),execution='SIMULATION_ONLY'))
    budget=CallBudget(root/'budget');attempts=[];feedback=None;agent=None;fatal=None
    started=time.monotonic();consumed=False
    try:
        transport_options={}
        if 'wire_schema_bundle' in config:
            transport_options['wire_schema_bundle']=ROOT/config['wire_schema_bundle']['path']
        agent=PilotSubscriptionAgent(evidence_dir=root/'transport',model=config['model'],timeout_s=config['timeout_s'],**transport_options)
        need(agent.source=='online_model' and agent.process_factory is subprocess.Popen,'PILOT_REAL_ONLY','Mock/process substitution prohibited')
        agent.preflight()
        write_json_new(root/'transport_preflight.json',dict(status='PASS',actual_model_turns=0,config=agent.public_config))
        for revision in range(2):
            need(time.monotonic()-started+config['timeout_s']<=config['total_wall_limit_s'],
                 'PILOT_WALL','Not enough remaining budget for unchanged per-call timeout')
            cap=deepcopy(initial);cap['metadata']['revision']+=revision
            verify_inputs(config,config_path)
            n=budget.claim(cap,'Initial response' if revision==0 else feedback)
            folder=reserve_directory(root/f'attempt_{n:02d}')
            write_json_new(folder/'capture.json',cap)
            ledger=stages();record=None;result=None;terminal=False;failure=None
            try:
                output,prov=agent.decide(cap,feedback)
                record=dict(sample_id=config['sample_id'],record_id=config['experiment_id']+f'/attempt_{n:02d}',
                            capture=cap,output=output,provenance=prov)
                write_json_new(folder/'record.json',record)
                ledger['SERVICE']=dict(status='PASS',returned=True,cli_resolved_alias=prov['cli_resolved_alias'],
                    server_reported_snapshot=None,identity_basis=prov['model_identity_basis'])
                ledger['NATIVE_OUTPUT']=dict(status='PASS',original_json_unchanged=True,output_digest=digest(output))
                result,feedback,terminal=run_tools(cap,initial,output,prov,revision,folder,ledger,config,
                    lambda:verify_inputs(config,config_path))
                consumed=bool(result)
            except Failure as exc:
                failure=exc.record
                if ledger['SERVICE']['status']=='PASS':
                    # This is an execution/evidence failure after a real native
                    # response, not a provider failure or another model task.
                    phase=next((key for key,value in ledger.items() if value['status']=='STARTED'),'EXECUTION')
                    ledger[phase]=dict(status='FAIL',failure=failure)
                    write_json_new(folder/'runtime_failure.json',failure)
                    if not (folder/'acceptor.json').exists():
                        write_json_new(folder/'acceptor.json',not_reached_acceptance(failure))
                    feedback=dict(failure=failure,repair_request='Stop: trusted execution or evidence failure is not a semantic revision.')
                    terminal=True
                else:
                    terminal,feedback=_transport_failure(agent,root,folder,n,cap,config,ledger,failure)
            finally:
                transport_folder=root/'transport'/f'attempt_{n:02d}'
                wire=read(transport_folder/'wire_manifest.json') if (transport_folder/'wire_manifest.json').exists() else {}
                budget.finish(n,wire,agent.last_attempt_summary)
                row=dict(attempt=n,revision=revision,stages=ledger,consumed_prefix=consumed,
                         stop_after_attempt=terminal,feedback=feedback)
                write_json_new(folder/'stage_ledger.json',row);attempts.append(row)
            if terminal or revision==1:break
    except Failure as exc:
        fatal=exc.record;write_json_new(root/'pilot_stop.json',fatal)
    except Exception as exc:
        fatal=dict(code='PILOT_SOFTWARE_EXCEPTION',exception_type=type(exc).__name__)
        write_json_new(root/'pilot_stop.json',fatal)
    final_budget=budget.seal()
    for index in range(len(attempts)+1,3):
        attempts.append(dict(attempt=index,status='NOT_ATTEMPTED',reason='Trial ended; no transport retry or replacement call',stages=stages()))
    success=any(a.get('stages',{}).get('ACCEPTOR',{}).get('status')=='PASS' for a in attempts)
    value=dict(experiment_id=config['experiment_id'],source_commit=commit,sample_id=config['sample_id'],unique_windows=1,
        attempts=attempts,budget=final_budget,fatal_stop=fatal,elapsed_s=time.monotonic()-started,
        conditional_single_window_success=success,simulated_prefix_consumed=consumed,
        model_snapshot='NOT_EXPOSED',g2_status='NOT_EVALUATED_RUNTIME_TRUTH_SEPARATION',
        statement='真实图像/真实模型的条件式单窗口链路通过，并完成模拟短前缀消费。' if success else '未获得完整条件式单窗口链路通过。',
        no_real_vehicle_control=True,no_counterfactual_closed_loop_claim=True,no_g2_pass_claim=True,
        future_six_window_batch_launched=False,ended=True)
    write_json_new(root/'summary.json',value)
    files={str(p.relative_to(root)):dict(sha256=sha(p),bytes=p.stat().st_size) for p in sorted(root.rglob('*'))
           if p.is_file() and not any(part.startswith('.private') for part in p.relative_to(root).parts)}
    write_json_new(root/'SHA256_RUNTIME_MANIFEST.json',dict(experiment_id=config['experiment_id'],source_commit=commit,
        runtime_sealed=True,files=files,online_turns_sent=final_budget['turn_start_requests'],
        model_budget_charged=final_budget['charged'],private_wire_in_public_git=False))
    print(json.dumps(dict(runtime=str(root),charged=final_budget['charged'],turn_start_requests=final_budget['turn_start_requests'],
                         success=success,stop=fatal,ended=True),ensure_ascii=False))
    return 0 if success else 3


def _transport_failure(agent,root,folder,n,cap,config,ledger,failure):
    transport_folder=root/'transport'/f'attempt_{n:02d}'
    summary=agent.last_attempt_summary or {}
    wire=read(transport_folder/'wire_manifest.json') if (transport_folder/'wire_manifest.json').exists() else {}
    layer=summary.get('service_layer',{})
    ledger['SERVICE']=dict(status='PASS' if layer.get('completed_turn_observed') else 'FAIL',returned=summary.get('model_service_returned','UNKNOWN'),
        failure=failure,service_layer=layer,turn_start_requests=wire.get('turn_start_requests',0))
    native_path=transport_folder/'native_output.json';prov_path=transport_folder/'provenance.json'
    if native_path.exists() and prov_path.exists():
        output=read(native_path);prov=read(prov_path)
        record=dict(sample_id=config['sample_id'],record_id=config['experiment_id']+f'/attempt_{n:02d}',capture=cap,output=output,provenance=prov)
        write_json_new(folder/'record.json',record)
        diag=diagnose_metric_output(cap,output,prov,record_path='record.json')
        write_json_new(folder/'all_contract_diagnostics.json',diag)
        ledger['NATIVE_OUTPUT']=dict(status='FAIL',failure=failure,raw_json_preserved=True)
        feedback=dict(failure=failure,contract_diagnostics=diag,previous_native_output=output,
            repair_request='Only evidence-supported native corrections; unknown stays unknown and adverse scenarios stay included.')
    else:
        ledger['NATIVE_OUTPUT']=dict(status='NOT_REACHED' if not layer.get('completed_turn_observed') else 'FAIL',failure=failure)
        feedback=dict(failure=failure,repair_request='Return native JSON exactly per frozen schema. Do not invent missing information.')
    write_json_new(folder/'failure.json',failure)
    write_json_new(folder/'acceptor.json',not_reached_acceptance(failure))
    # Only a completed native-generation JSON/schema problem is a
    # semantic repair candidate. Auth/service/protocol failures stop.
    terminal=not (failure['code'] in ('SCHEMA_ERROR','CODEX_RESPONSE')
        and layer.get('completed_turn_observed') and wire.get('cli_resolved_alias')==config['model'])
    return terminal,feedback


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',required=True,type=Path)
    try:raise SystemExit(run(p.parse_args().config))
    except Failure as exc:
        print(json.dumps(exc.record,ensure_ascii=False));raise SystemExit(2)
