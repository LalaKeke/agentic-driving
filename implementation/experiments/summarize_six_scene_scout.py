"""Offline summary of the fixed six-window scout; never calls models or tools.

Reads all candidate outcomes rather than interpreting failures[-1] as the
window failure. Unselected failing candidates do not turn a successful attempt
into a blocked window. GT and behavior judgments are intentionally separate.
"""
import argparse
from collections import Counter,defaultdict
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'implementation'))
from agentic.contracts import Failure,validate
from agentic.evidence_io import reserve_directory,write_json_new,write_text_new

STAGES=('SERVICE','BUSINESS_SCHEMA','T1','T2','T3','T4','ACCEPTOR','EXECUTION')
STATUS_LABELS={
    'FIRST_ATTEMPT_PASS':'First-attempt chain pass',
    'REVISION_PASS':'Chain pass after revision',
    'TOOL_OR_SEMANTIC_FAILURE':'Tool / semantic rejection',
    'SERVICE_INTERFACE_STOP':'Service / interface stop',
    'NOT_ATTEMPTED':'Not attempted',
    'INCOMPLETE_ATTEMPT':'Incomplete evidence',
}

def read(path,default=None):return json.loads(path.read_text()) if path.is_file() else default
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def rel(path):
    try:return str(path.relative_to(ROOT))
    except ValueError:return str(path)

def turn_starts(value):
    if isinstance(value,dict):return int(value.get('method')=='turn/start')+sum(turn_starts(x) for x in value.values())
    if isinstance(value,list):return sum(map(turn_starts,value))
    return 0

def failure_class(stage,code):
    code=code or 'UNKNOWN'
    if stage in ('SERVICE','BUSINESS_SCHEMA'):return 'SERVICE_INTERFACE'
    if stage=='T1':
        if 'SCALE' in code:return 'T1_SCALE'
        if code in ('UNBOUNDED_UNKNOWN','OCCUPANCY_UNKNOWN','UNKNOWN_UNCERTAINTY','TYPED_UNKNOWN_BLOCKING','SCENARIO_INCOMPLETE','OCCUPANCY_COVERAGE','UNKNOWN_TRAFFIC_CONDITION'):
            return 'T1_INFORMATION_COVERAGE'
        if any(part in code for part in ('ALIGN','EVIDENCE','FRAME','TIME_REFERENCE')):return 'T1_ALIGNMENT_EVIDENCE'
        return 'T1_CONTRACT_GEOMETRY'
    return {'T2':'T2_CORRIDOR','T3':'T3_OPTIMIZATION','T4':'T4_VERIFICATION',
            'ACCEPTOR':'PROVENANCE_ACCEPTOR','EXECUTION':'SIMULATED_EXECUTION'}.get(stage,'INCOMPLETE_EVIDENCE')

def planner_stage(ledger):
    """Deepest reachable blocking stage; preserves all candidate details."""
    if not ledger:return None
    rows=ledger.get('candidates',[])
    if any(row.get('T4')=='PASS' for row in rows):return None
    if any(row.get('T3')=='PASS' for row in rows):return 'T4'
    if any(row.get('T2')=='PASS' for row in rows):return 'T3'
    return 'T2' if rows else None

def metrics(folder):
    trajectory=read(folder/'T3.json',{});verification=read(folder/'T4.json',{})
    execution=read(folder/'execution.json',{});receipt=read(folder/'execution_receipt.json',{})
    acceptance=read(folder/'acceptor.json',{})
    points=trajectory.get('points',[]);controls=points[:-1]
    jerk=[((b['acceleration_mps2']-a['acceleration_mps2'])/(b['time_s']-a['time_s']),b['time_s']-a['time_s'])
          for a,b in zip(controls,controls[1:]) if b['time_s']>a['time_s']]
    consumed=bool(execution and receipt.get('consumed_once'))
    displacement=execution.get('center_displacement_m')
    distance=math.hypot(*displacement) if displacement is not None else None
    motion=acceptance.get('motion_class')
    if not consumed:motion='NOT_EXECUTED'
    elif motion not in ('MOVING_PREFIX','STATIONARY_HOLD'):
        motion='MOVING_PREFIX' if (distance or 0)>1e-5 else 'STATIONARY_HOLD'
    return dict(prefix_consumed=consumed,motion_class=motion,displacement_xy_m=displacement,
        displacement_m=distance,end_speed_mps=execution.get('speed_mps'),prefix_s=execution.get('elapsed_s'),
        minimum_conditional_clearance_lower_bound_m=verification.get('minimum_clearance_lower_bound_m'),
        max_abs_acceleration_mps2=max((abs(p['acceleration_mps2']) for p in controls),default=None),
        rms_discrete_jerk_proxy_mps3=math.sqrt(sum(j*j*dt for j,dt in jerk)/sum(dt for _,dt in jerk)) if jerk else None,
        max_abs_steer_rad=max((abs(p['steer_rad']) for p in controls),default=None),
        solver_status=trajectory.get('solver',{}).get('status'),
        T4_verdict=verification.get('verdict','NOT_REACHED'),
        acceptor_status=acceptance.get('status','NOT_REACHED'),
        independent_acceptor_pass=acceptance.get('strict_live_physical_success') is True,
        prefix_receipt_path=rel(folder/'execution_receipt.json') if receipt else None,
        limitation='Jerk is an acceleration-difference proxy, not finite continuous RMS jerk of piecewise-constant controls.')

def attempt(folder,transport_folder,event,charged,expected_sample):
    record_path=folder/'record.json';record=read(record_path)
    native_path=transport_folder/'native_output.json'
    output=record.get('output') if record else read(native_path)
    output_dict=output if isinstance(output,dict) else {}
    hypotheses=output_dict.get('hypotheses')
    hypotheses=hypotheses if isinstance(hypotheses,dict) else {}
    native_present=record is not None or native_path.is_file()
    business='NOT_REACHED';schema_failure=None
    if native_present:
        try:validate('agent_output_v3_exp2.schema.json',output);business='PASS'
        except Failure as error:business='FAIL';schema_failure=error.record
    ledger=read(folder/'candidate_ledger.json')
    stage_file=read(folder/'stages.json',{})
    stages=dict(event.get('stages',stage_file.get('stages',{})))
    stages={name:stages.get(name,'NOT_REACHED') for name in STAGES}
    failure=event.get('failure') or read(folder/'failure.json') or stage_file.get('failure')
    transport=read(transport_folder/'attempt_summary.json',{})
    request_path=transport_folder/'requests.json'
    count=turn_starts(read(request_path,[]))
    physical=metrics(folder)
    source=(record or {}).get('provenance',read(transport_folder/'provenance.json',{})).get('source')
    window_identity=(not record or record.get('capture',{}).get('metadata',{}).get('scene_id')=='nusc_'+expected_sample)
    chain=(source=='online_model' and business=='PASS' and physical['T4_verdict']=='PASS'
           and physical['independent_acceptor_pass'] and physical['prefix_consumed'] and window_identity)
    planner_block=planner_stage(ledger)
    blocking=next((stage for stage in ('T1',) if stages[stage]=='FAIL'),None)
    reasons=[]
    if blocking:
        failed=read(folder/(blocking+'-failure.json')) or failure
        if failed:reasons=[failed]
    elif not window_identity:
        blocking='SERVICE';reasons=[dict(code='CROSS_WINDOW_RECORD',message='Record capture window differs from the frozen case sample.')]
    elif planner_block:
        blocking=planner_block
        reasons=[c.get('failure',{}) for c in ledger['candidates'] if c.get(blocking)=='FAIL']
    elif not chain:
        if business=='FAIL':blocking='BUSINESS_SCHEMA';reasons=[schema_failure]
        elif stages['ACCEPTOR']=='FAIL':blocking='ACCEPTOR';reasons=read(folder/'acceptor.json',{}).get('reasons',[])
        elif stages['EXECUTION']=='FAIL':blocking='EXECUTION';reasons=[failure] if failure else []
        elif failure:blocking='SERVICE';reasons=[failure]
        else:blocking='INCOMPLETE';reasons=[]
    # Raw service flags can say NOT_REACHED even after an observed error; do not
    # rewrite them, but report the notification and charged request independently.
    service_layer=transport.get('service_layer') or {}
    error_notification=service_layer.get('error_notification_observed',
                                         transport.get('error_notification_observed',False))
    service_error=bool(error_notification or (not native_present and failure))
    if source is None and not native_present and transport.get('source'):source=transport['source']
    issue_classes=set()
    if not chain and blocking:
        issue_classes.update(failure_class(blocking,r.get('code')) for r in reasons if r)
        if not issue_classes:issue_classes.add(failure_class(blocking,None))
    if service_error:issue_classes.add('SERVICE_INTERFACE')
    provenance=(record or {}).get('provenance',read(transport_folder/'provenance.json',{}))
    return dict(attempt=int(folder.name.split('_')[-1]),charged=charged,turn_start_count=count,
        api_post_requests=transport.get('api_post_requests',0),
        transport=provenance.get('transport',transport.get('transport')),
        server_reported_model=provenance.get('server_reported_model',transport.get('server_reported_model',transport.get('response_model'))),
        usage=transport.get('usage'),
        record_path=rel(record_path) if record else None,record_sha256=sha(record_path) if record else None,
        native_output_path=rel(native_path) if native_path.is_file() else None,
        native_json_present=native_present,native_business_schema=business,source=source,
        record_window_matches_frozen_sample=window_identity,
        requested_model=provenance.get('requested_model'),cli_resolved_alias=provenance.get('cli_resolved_alias'),
        actual_server_snapshot=provenance.get('server_reported_snapshot'),
        raw_stage_ledger=stages,transport_summary=transport,
        service_error_notification_observed=bool(error_notification),
        service_or_interface_failure=bool(service_error or blocking in ('SERVICE','BUSINESS_SCHEMA')),
        service_return='NATIVE_FINAL_TEXT' if (transport_folder/'final_agent_message.txt').exists() else ('ERROR_NOTIFICATION' if error_notification else ('ADAPTER_FAILURE_NO_NATIVE' if service_error else 'UNKNOWN')),
        complete_conditional_chain=chain,deepest_blocking_stage=blocking,
        blocking_reasons=reasons,raw_last_failure=failure,
        candidate_counts=({stage:sum(c.get(stage)=='PASS' for c in ledger.get('candidates',[])) for stage in ('T2','T3','T4')} if ledger else None),
        candidate_ledger=ledger,issue_classes=sorted(issue_classes),physical=physical,
        decision_evidence=dict(decision=output_dict.get('decision'),traffic_evidence=hypotheses.get('traffic_evidence')),
        behavioral_review=dict(status='NOT_EVALUATED',needs_human_review=True,
            rule='Review raw images and decision evidence separately. A stationary prefix alone does not complete a moving task; a signal label is not an applicable signal fact.'),
        offline_GT_review='NOT_EVALUATED_BY_THIS_HELPER')

def case_runtime(run_root,case_id):
    choices=(run_root/'runtime'/case_id,run_root/case_id/'runtime',run_root/case_id)
    return next((p for p in choices if (p/'summary.json').exists() or (p/'config.json').exists()),choices[0])

def case_row(case,run_root,preparation_root,selection):
    folder=case_runtime(run_root,case['case_id']);summary=read(folder/'summary.json',{})
    charge_files=sorted(folder.glob('charge_*.json'));charges={int(f.stem.split('_')[-1]) for f in charge_files}
    events={int(x['attempt']):x for x in summary.get('attempts',[])}
    indices=sorted(charges|set(events)|{int(p.name.split('_')[-1]) for p in folder.glob('attempt_*') if p.is_dir()})
    attempts=[attempt(folder/f'attempt_{i:02d}',folder/f'transport/attempt_{i:02d}',events.get(i,{}),i in charges,case['sample_token']) for i in indices]
    valid=[a for a in attempts if a['native_business_schema']=='PASS']
    passes=[a for a in attempts if a['complete_conditional_chain']]
    fatal=summary.get('fatal')
    if passes:status='FIRST_ATTEMPT_PASS' if passes[0]['attempt']==1 else 'REVISION_PASS'
    elif fatal or any(a['service_or_interface_failure'] for a in attempts):status='SERVICE_INTERFACE_STOP'
    elif not attempts:status='SERVICE_INTERFACE_STOP' if summary and fatal else 'NOT_ATTEMPTED'
    elif not summary:status='INCOMPLETE_ATTEMPT'
    else:status='TOOL_OR_SEMANTIC_FAILURE'
    latest=passes[0] if passes else (valid[-1] if valid else (attempts[-1] if attempts else None))
    issues=set().union(*(set(a['issue_classes']) for a in attempts)) if attempts else set()
    if fatal:issues.add('SERVICE_INTERFACE')
    return dict(case_id=case['case_id'],scene_name=case['scene_name'],scene_token=case['scene_token'],sample_token=case['sample_token'],
        label=selection.get(case['case_id'],{}).get('label',case['case_id']),runtime_path=rel(folder),
        initial_speed_mps=case['ego_speed_mps'],status=status,ended=summary.get('ended'),
        charged_requests=len(charge_files),reported_charged_requests=summary.get('charged_requests'),
        turn_start_count=sum(a['turn_start_count'] for a in attempts),
        api_post_requests=sum(a['api_post_requests'] for a in attempts),
        budget_count_matches_summary=(summary.get('charged_requests')==len(charge_files)) if summary else None,
        first_attempt_pass=(attempts[0]['complete_conditional_chain'] if attempts else None),
        revision_pass=(next((a['complete_conditional_chain'] for a in attempts if a['attempt']==2),None)),
        native_json_count=sum(a['native_json_present'] for a in attempts),native_schema_valid_count=len(valid),
        final_failure_stage=(latest['deepest_blocking_stage'] if latest and not passes else None),
        final_failure_reasons=(latest['blocking_reasons'] if latest and not passes else []),
        fatal_service_or_interface_failure=fatal,issue_classes_any_attempt=sorted(issues),
        attempts=attempts,selected_attempt=latest['attempt'] if latest else None,
        candidate_counts=latest['candidate_counts'] if latest else None,
        physical=latest['physical'] if latest else {},
        behavioral_review=latest['behavioral_review'] if latest else dict(status='NOT_EVALUATED'),
        offline_GT_review='NOT_EVALUATED_BY_THIS_HELPER')

def plot(report,out):
    with tempfile.TemporaryDirectory(prefix='scout-summary-mpl-') as cache:
        os.environ['MPLCONFIGDIR']=cache
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        from matplotlib.ticker import MaxNLocator
        fig,axes=plt.subplots(1,2,figsize=(15,5.4))
        labels=list(STATUS_LABELS);counts=Counter(row['status'] for row in report['rows'])
        axes[0].barh(range(len(labels)),[counts[k] for k in labels],color=['#3a9d61','#86b96b','#d98c46','#bd5b58','#a2a6ab','#8861a8'])
        axes[0].set_yticks(range(len(labels)),[STATUS_LABELS[k] for k in labels]);axes[0].invert_yaxis()
        axes[0].set_title('Final window outcomes (exclusive)');axes[0].set_xlabel('Unique windows')
        axes[0].set_xlim(0,6);axes[0].xaxis.set_major_locator(MaxNLocator(integer=True))
        for i,key in enumerate(labels):axes[0].text(counts[key]+.05,i,str(counts[key]),va='center')
        issues=report['issue_distribution_any_attempt']
        if issues:
            axes[1].barh(range(len(issues)),[x['unique_window_count'] for x in issues],color='#c77b45')
            axes[1].set_yticks(range(len(issues)),[x['problem_class'] for x in issues]);axes[1].invert_yaxis()
            for i,row in enumerate(issues):axes[1].text(row['unique_window_count']+.05,i,str(row['unique_window_count']),va='center')
        else:
            axes[1].text(.5,.5,'No observed blocking issue classes\nNo unattempted window is counted as a failure',ha='center',va='center',transform=axes[1].transAxes)
            axes[1].set_yticks([])
        axes[1].set_title('Observed blocking classes across attempts (may overlap)')
        axes[1].set_xlabel('Affected unique windows');axes[1].set_xlim(0,6);axes[1].xaxis.set_major_locator(MaxNLocator(integer=True))
        fig.suptitle('Frozen six-scene scout | actual runtime evidence | conditional prototype only',fontsize=14)
        fig.text(.5,.012,'Repeated revisions are not extra windows. Non-selected candidate failures do not turn a passed attempt into a blocked window.',ha='center',fontsize=9)
        fig.tight_layout(rect=(0,.045,1,.93));fig.savefig(out/'failure_distribution.png',dpi=180);plt.close(fig)

def main(run_root,preparation_root,out):
    run_root=run_root.resolve();preparation_root=preparation_root.resolve()
    batch=read(run_root/'batch_summary.json')
    if not batch or not batch.get('ended'):raise SystemExit('Batch not finalized: no summary artifacts written.')
    plan=read(preparation_root/'execution_plan.json');selection=read(preparation_root/plan['selection_path'])
    labels={x['case_id']:x for x in selection['main_cases']}
    if len(plan['runtime_inputs'])!=6 or len({x['scene_token'] for x in plan['runtime_inputs']})!=6:
        raise SystemExit('Expected exactly six distinct scene tokens.')
    rows=[case_row(case,run_root,preparation_root,labels) for case in plan['runtime_inputs']]
    affected=defaultdict(list)
    for row in rows:
        for category in row['issue_classes_any_attempt']:affected[category].append(row['case_id'])
    issues=[dict(problem_class=k,unique_window_count=len(v),windows=v) for k,v in sorted(affected.items(),key=lambda p:(-len(p[1]),p[0]))]
    total=sum(row['charged_requests'] for row in rows)
    report=dict(experiment_id=plan['experiment_id'],source='ACTUAL_RUNTIME_READ_ONLY_SUMMARY',new_model_calls=0,
        analysis_source_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        analysis_script_sha256=sha(Path(__file__)),batch_summary_sha256=sha(run_root/'batch_summary.json'),
        fixed_unique_windows=6,attempted_generation_windows=sum(row['charged_requests']>0 for row in rows),
        windows_with_native_valid_output=sum(row['native_schema_valid_count']>0 for row in rows),
        charged_requests=total,batch_reported_charged_requests=batch.get('charged_requests'),
        budget_count_matches_batch=(total==batch.get('charged_requests')),turn_start_count=sum(r['turn_start_count'] for r in rows),
        internal_HTTP_attempt_count=None,rows=rows,batch=batch,
        outcome_distribution=dict(Counter(row['status'] for row in rows)),issue_distribution_any_attempt=issues,
        can_select_changes_for_completed_six_window_batch=bool(batch.get('all_six_processed')),
        caveats=['Conditional model-scene verification and simulated prefixes only; not world safety, G2 pass or counterfactual multi-round execution.',
                 'Unattempted windows are neither failed nor passed; revisions add no independent scene.',
                 'All candidate failures are retained, but only a blocking stage of an unsuccessful attempt contributes to issue counts.',
                 'Service failure is separate even if an earlier valid response had a semantic failure.',
                 'No GT is read; behavior reasonableness requires independent image/decision review and remains NOT_EVALUATED.',
                 'Partial-batch rankings are descriptive only, not a six-window basis for two algorithm changes.'])
    if any(row['charged_requests']>2 for row in rows) or total>12:raise SystemExit('Observed call budget exceeds frozen cap; refusing normal summary.')
    reserve_directory(out)
    write_json_new(out/'window_results.json',report)
    def n(value):return '—' if value is None else f'{value:.3f}'
    lines=['# 六场景真实运行结果','',
        f"固定6个不同scene；已扣预算{total}/12；有原生合法输出的窗口{report['windows_with_native_valid_output']}/6。没有合并修订或把未调用写作失败。",'',
        '|窗口/scene|状态|原生有效/扣账|最深阻塞及服务|T2/T3/T4通过候选|前缀/位移m/末速m/s|条件间距m|行为合理性|',
        '|---|---|---|---|---|---|---|---|']
    for row in rows:
        p=row['physical'];c=row['candidate_counts'];counts='/'.join(str(c[k]) for k in ('T2','T3','T4')) if c else 'NOT_REACHED'
        reasons=sorted({str(x.get('code')) for x in row['final_failure_reasons']})
        failure=(row['final_failure_stage'] or '—')+(' '+','.join(reasons) if reasons else '')
        if row['fatal_service_or_interface_failure']:
            fatal=row['fatal_service_or_interface_failure']
            failure+='; SERVICE '+str(fatal.get('code') if isinstance(fatal,dict) else fatal)
        lines.append(f"|{row['label']} / {row['scene_name']}|{row['status']}|{row['native_schema_valid_count']}/{row['charged_requests']}|{failure}|{counts}|{p.get('motion_class','NOT_EXECUTED')}/{n(p.get('displacement_m'))}/{n(p.get('end_speed_mps'))}|{n(p.get('minimum_conditional_clearance_lower_bound_m'))}|待独立审阅|")
    lines+=['','![失败原因与最终状态](failure_distribution.png)','',
            '左图为互斥最终状态，右图为各次尝试中真正阻断全链的问题类，按唯一窗口计数、可以重叠。未选中候选的失败保留在JSON，但不会把已通过尝试记成受阻。',
            '服务通知、原文/业务Schema、完整5候选、首答/修订、接受器、模拟回执与积分状态均保留在window_results.json。原始SERVICE=NOT_REACHED不覆盖已经观察到的错误通知。',
            '静止前缀不能自动算移动任务完成；信号等待标签也不是适用于自车的信号事实。行为合理性与离线GT均未由本脚本评分。',
            '平顺性见JSON：最大加速度及离散jerk代理，后者不是分段常加速度控制的连续RMS jerk。','']
    if not batch.get('all_six_processed'):lines+=['**批次未完成：只报告已观察问题，不据部分窗口选择或实施两项算法修改。**','']
    write_text_new(out/'window_results.md','\n'.join(lines));plot(report,out)
    print(json.dumps(dict(out=str(out),windows=6,charged_requests=total,all_six_processed=batch.get('all_six_processed'),outcomes=report['outcome_distribution']),ensure_ascii=False))

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-root',required=True,type=Path);parser.add_argument('--preparation-root',required=True,type=Path);parser.add_argument('--out',required=True,type=Path)
    args=parser.parse_args();main(args.run_root,args.preparation_root,args.out)
