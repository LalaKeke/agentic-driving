"""Read-only, case-specific comparison of existing prototype runs; no inference.

The table separates fresh online chains from replanning saved real responses.
Each JSON run row retains all attempts, including service failures and every
candidate. A finite-search failure is never a global infeasibility conclusion.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'implementation'))
from agentic.contracts import Failure,digest,validate
from agentic.evidence_io import reserve_directory,write_json_new

BUS=ROOT/'implementation/results/moving_prototype_2s_20261028_v1'
OLD=ROOT/'implementation/results/varied_prototypes_2s_20261028_v1'
CURRENT=ROOT/'implementation/results/construction_recovery_20261029_v1'
STAGES=('SERVICE','BUSINESS_SCHEMA','T1','T2','T3','T4','ACCEPTOR','EXECUTION')


def read(path,default=None):
    return json.loads(path.read_text()) if path.is_file() else default


def relative(path):
    try:return str(path.relative_to(ROOT))
    except ValueError:return str(path)


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def physical_metrics(folder):
    trajectory=read(folder/'T3.json',{})
    verification=read(folder/'T4.json',{})
    execution=read(folder/'execution.json',{})
    acceptance=read(folder/'acceptor.json',{})
    receipt=read(folder/'execution_receipt.json',{})
    points=trajectory.get('points',[])
    controls=points[:-1]
    jerk=[]
    for a,b in zip(controls,controls[1:]):
        dt=b['time_s']-a['time_s']
        if dt>0:jerk.append(((b['acceleration_mps2']-a['acceleration_mps2'])/dt,dt))
    displacement=execution.get('center_displacement_m')
    consumed=bool(receipt.get('consumed_once') and execution)
    return dict(prefix_consumed=consumed,
        motion_class=(('MOVING_PREFIX' if math.hypot(*displacement)>1e-5 else 'STATIONARY_HOLD')
                      if consumed and displacement else 'NOT_EXECUTED'),
        displacement_xy_m=displacement,displacement_m=math.hypot(*displacement) if displacement else None,
        end_speed_mps=execution.get('speed_mps'),prefix_duration_s=execution.get('elapsed_s'),
        minimum_conditional_clearance_lower_bound_m=verification.get('minimum_clearance_lower_bound_m'),
        max_abs_acceleration_mps2=max((abs(p['acceleration_mps2']) for p in controls),default=None),
        rms_discrete_jerk_proxy_mps3=math.sqrt(sum(j*j*dt for j,dt in jerk)/sum(dt for _,dt in jerk)) if jerk else None,
        max_abs_steer_rad=max((abs(p['steer_rad']) for p in controls),default=None),
        solver_status=trajectory.get('solver',{}).get('status'),
        solver_objective=trajectory.get('solver',{}).get('objective'),
        verification_profile=trajectory.get('verification_profile','legacy_disk_v2' if trajectory else None),
        T4_verdict=verification.get('verdict','NOT_REACHED'),
        independent_acceptor_status=acceptance.get('status','NOT_REACHED'),
        independent_acceptor_software_success=acceptance.get('software_success',False))


def failure_view(failure,stages):
    if not failure:return None
    details=failure.get('details',{})
    return dict(stage=next((s for s in STAGES if stages.get(s)=='FAIL'),'SERVICE_OR_INTERFACE' if failure else None),
                code=failure.get('code'),message=failure.get('message'),object_id=details.get('object_id'),
                interval_s=details.get('interval',details.get('time_interval_s')),
                raw=failure)


def run_row(label,case,folder,source,capture_path):
    summary=read(folder/'summary.json')
    capture=read(capture_path,{})
    if summary is None:
        if (folder/'config.json').exists():
            raise SystemExit(f'Run is still incomplete: {relative(folder)}. Wait for its summary before comparison.')
        return dict(label=label,case=case,source='NOT_ATTEMPTED',path=relative(folder),
            unique_window=capture.get('metadata',{}).get('scene_id'),charged_requests=0,native_valid_count=0,
            status='NOT_ATTEMPTED',attempts=[],latest_native_attempt=None,metrics={},
            limitation='No model output or planning success is asserted.')
    config=read(folder/'config.json',{})
    attempts=[]
    for event in summary.get('attempts',[]):
        index=event['attempt'];path=folder/f'attempt_{index:02d}'
        record=read(path/'record.json')
        stages=dict(event.get('stages',{}))
        business='NOT_REACHED'
        if record is not None:
            capture=record['capture']
            try:validate('agent_output_v3_exp2.schema.json',record['output']);business='PASS'
            except Failure:business='FAIL'
        ledger=read(path/'candidate_ledger.json')
        failure=event.get('failure') or read(path/'failure.json')
        attempts.append(dict(attempt=index,record_path=relative(path/'record.json') if record else None,
            record_sha256=sha(path/'record.json') if record else None,
            provenance_source=record.get('provenance',{}).get('source') if record else None,
            native_structured_output=record is not None,business_schema_validation=business,
            service_status=stages.get('SERVICE','NOT_APPLICABLE_FROZEN_REPLAY' if source=='FROZEN_REAL_OUTPUT_OFFLINE_REPLAN' else 'UNKNOWN'),
            stages={s:stages.get(s,'NOT_REACHED') for s in STAGES},
            earliest_failure=failure_view(failure,stages),candidate_ledger=ledger,
            metrics=physical_metrics(path)))
    valid=[a for a in attempts if a['native_structured_output']]
    latest=valid[-1] if valid else (attempts[-1] if attempts else None)
    source_commit=summary.get('source_commit',config.get('source_commit'))
    return dict(label=label,case=case,source=source,path=relative(folder),
        unique_window=capture.get('metadata',{}).get('scene_id'),
        source_commit=source_commit,actual_source_sha256=config.get('actual_source_sha256'),
        planner_profile=config.get('planner_profile',summary.get('planner_profile','legacy_disk_v2')),
        charged_requests=summary.get('charged_requests',0),native_valid_count=sum(a['business_schema_validation']=='PASS' for a in attempts),
        ended=summary.get('ended'),status='CONDITIONAL_CHAIN_PASS' if summary.get('prototype_conditional_success') else 'NO_COMPLETE_CHAIN',
        fatal_service_or_interface_failure=summary.get('fatal'),
        attempts=attempts,latest_native_attempt=latest['attempt'] if valid else None,
        latest_response_failure=latest['earliest_failure'] if latest else None,
        metrics=latest['metrics'] if latest else {},
        selected_candidate=(latest.get('candidate_ledger') or {}).get('selected_candidate') if latest else None,
        candidate_ledger=(latest.get('candidate_ledger') if latest else None),
        note='Recorded Git HEAD and actual source SHA are separate observations; original provenance remains unchanged.')


def unchanged_pair(label,old,new):
    if not old.is_file() or not new.is_file():
        return dict(label=label,status='NOT_REACHED',old=relative(old),new=relative(new))
    a,b=read(old),read(new)
    return dict(label=label,old=relative(old),new=relative(new),old_file_sha256=sha(old),new_file_sha256=sha(new),
                old_scene_digest=digest(a),new_scene_digest=digest(b),scene_values_identical=a==b,
                status='UNCHANGED' if a==b else 'CHANGED')


def main(out,current):
    global CURRENT
    CURRENT=current.resolve()
    definitions=[
        ('公交旧在线','bus',BUS/'runtime','FRESH_ONLINE_CHAIN',BUS/'capture.json'),
        ('停车旧在线','parking',OLD/'parking_bend/runtime','FRESH_ONLINE_CHAIN',OLD/'parking_bend/capture.json'),
        ('施工旧在线','construction',OLD/'construction_yield/runtime','FRESH_ONLINE_CHAIN',OLD/'construction_yield/capture.json'),
        ('停车旧BOX回放','parking',OLD/'parking_bend/yaw_box_replay','FROZEN_REAL_OUTPUT_OFFLINE_REPLAN',OLD/'parking_bend/capture.json'),
        ('施工旧BOX回放r2','construction',OLD/'construction_yield/yaw_box_replay_attempt2','FROZEN_REAL_OUTPUT_OFFLINE_REPLAN',OLD/'construction_yield/capture.json'),
        ('施工旧BOX回放r3','construction',OLD/'construction_yield/yaw_box_replay','FROZEN_REAL_OUTPUT_OFFLINE_REPLAN',OLD/'construction_yield/capture.json'),
    ]
    iterations=[name for name in ('iteration_1','iteration_2','iteration_3','final') if (CURRENT/name).is_dir()]
    for iteration in iterations:
        for case in ('bus','parking','construction'):
            folder=CURRENT/iteration/case
            if not folder.is_dir():continue
            cap=BUS/'capture.json' if case=='bus' else OLD/({'parking':'parking_bend','construction':'construction_yield'}[case])/'capture.json'
            definitions.append((f'{case} {iteration}五候选回放',case,folder,'FROZEN_REAL_OUTPUT_OFFLINE_REPLAN',cap))
    for case in ('construction','redlight'):
        config=read(CURRENT/f'live_{case}/config.json',{})
        cap_path=config.get('capture_path')
        cap=(ROOT/cap_path if cap_path and not Path(cap_path).is_absolute() else Path(cap_path)) if cap_path else OLD/('construction_yield' if case=='construction' else 'redlight_crossflow')/'capture.json'
        definitions.append((f'{case} 本轮新在线',case,CURRENT/f'live_{case}','FRESH_ONLINE_CHAIN',cap))
    for name in ('bus','parking','construction','live_construction'):
        folder=CURRENT/'final_replays'/name
        if not folder.is_dir():continue
        config=read(folder/'config.json',{});cap_path=config.get('capture_path')
        cap=(ROOT/cap_path if cap_path and not Path(cap_path).is_absolute() else Path(cap_path)) if cap_path else BUS/'capture.json'
        case='construction' if name=='live_construction' else name
        definitions.append((f'{name} 冻结最终源码回放',case,folder,'FROZEN_REAL_OUTPUT_OFFLINE_REPLAN',cap))
    old_live=CURRENT/'live_output_old_planner'
    if old_live.is_dir():
        config=read(old_live/'config.json',{});cap_path=config.get('capture_path')
        cap=(ROOT/cap_path if cap_path and not Path(cap_path).is_absolute() else Path(cap_path)) if cap_path else OLD/'construction_yield/capture.json'
        definitions.append(('施工新JSON→旧BOX回放','construction',old_live,'FROZEN_REAL_OUTPUT_OFFLINE_REPLAN',cap))
    rows=[run_row(*definition) for definition in definitions]
    pairs=[]
    for iteration in iterations:
        for case,old in [('bus',BUS/'runtime/attempt_02/T1.json'),
                         ('parking',OLD/'parking_bend/runtime/attempt_02/T1.json'),
                         ('construction',OLD/'construction_yield/runtime/attempt_03/T1.json')]:
            new=CURRENT/f'{iteration}/{case}/attempt_01/T1.json'
            if new.exists():pairs.append(unchanged_pair(case+' frozen T1 vs '+iteration+' candidates',old,new))
    pairs.append(unchanged_pair('parking original T1 vs old BOX replay',OLD/'parking_bend/runtime/attempt_02/T1.json',OLD/'parking_bend/yaw_box_replay/attempt_01/T1.json'))
    for attempt,suffix in [('02','yaw_box_replay_attempt2'),('03','yaw_box_replay')]:
        pairs.append(unchanged_pair('construction '+attempt+' original T1 vs old BOX replay',OLD/f'construction_yield/runtime/attempt_{attempt}/T1.json',OLD/f'construction_yield/{suffix}/attempt_01/T1.json'))
    for case,old in [('bus',BUS/'runtime/attempt_02/T1.json'),
                     ('parking',OLD/'parking_bend/runtime/attempt_02/T1.json'),
                     ('construction',OLD/'construction_yield/runtime/attempt_03/T1.json')]:
        new=CURRENT/f'final_replays/{case}/attempt_01/T1.json'
        if new.exists():pairs.append(unchanged_pair(case+' frozen T1 vs final-source replay',old,new))
    native=next((p for p in sorted((CURRENT/'live_construction').glob('attempt_*/T1.json'))),None)
    if native:
        for label,new in [('new live JSON vs final-source replan',CURRENT/'final_replays/live_construction/attempt_01/T1.json'),
                          ('new live JSON vs old BOX planner',CURRENT/'live_output_old_planner/attempt_01/T1.json')]:
            if new.exists():pairs.append(unchanged_pair(label,native,new))
    if any(p['status']=='CHANGED' for p in pairs):raise SystemExit('Frozen-scene comparison found changed T1; refusing unchanged-scene report.')
    source=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    registered_windows=sorted({r['unique_window'] for r in rows if r.get('unique_window')})
    output_windows=sorted({r['unique_window'] for r in rows if r.get('unique_window') and r['native_valid_count']>0})
    current_online_windows=sorted({r['unique_window'] for r in rows if r.get('unique_window') and r['path'].startswith(relative(CURRENT)) and r['source']=='FRESH_ONLINE_CHAIN' and r['charged_requests']>0})
    report=dict(kind='READ_ONLY_CASE_COMPARISON_NO_MODEL_CALLS',analysis_source_commit=source,
        analysis_script_sha256=sha(Path(__file__)),new_calls_by_this_script=0,
        registered_unique_windows=registered_windows,model_output_unique_windows=output_windows,
        current_online_attempted_unique_windows=current_online_windows,
        rows=rows,frozen_T1_parity=pairs,
        caveats=['Fresh online chains and frozen real-output offline replans are separate.',
                 'The original online service failure remains visible even when an earlier native response exists.',
                 'Jerk is a discrete acceleration-difference proxy; piecewise-constant controls have jumps, not finite continuous RMS jerk.',
                 'Clearance is a conditional conservative lower bound, not GT safety.',
                 'Disk and oriented-body clearance lower bounds use different conservative enclosures; a larger lower bound is not measured real-world safety improvement.',
                 'Each window is counted once; revisions and replans are not independent samples.'])
    reserve_directory(out)
    write_json_new(out/'comparison.json',report)
    def f(x):return '-' if x is None else f'{x:.4f}'
    lines=['# 固定真实输出与新增在线结果对照','',
        '本表为零调用读后汇总；每行取该run最后一份有效原生响应，全部初答/修订/服务失败和候选逐项保留在 comparison.json。旧接受器内部ONLINE键不改变离线回放来源。','',
        '|记录|来源|有效输出/扣账|最后有效响应的结果|候选 T2/T3/T4通过数|消费/位移m/末速m/s|最小条件间距m|最大加速度/离散jerk代理/最大转角|',
        '|---|---|---|---|---|---|---|---|']
    for row in rows:
        failure=row.get('latest_response_failure');metrics=row['metrics'];ledger=row.get('candidate_ledger')
        count='/'.join(str(sum(c[s]=='PASS' for c in ledger['candidates'])) for s in ('T2','T3','T4'))+f" (共{ledger['candidate_count']})" if ledger else '-'
        fail=(failure['stage']+':'+str(failure['code'])+(str(failure['interval_s']) if failure.get('interval_s') else '')) if failure else row['status']
        fatal=row.get('fatal_service_or_interface_failure')
        if fatal:fail+='；终止服务错误 '+str(fatal.get('code'))
        source_label={'FRESH_ONLINE_CHAIN':'真实在线','FROZEN_REAL_OUTPUT_OFFLINE_REPLAN':'真实输出离线重规划','NOT_ATTEMPTED':'未调用'}[row['source']]
        motion=metrics.get('motion_class','NOT_EXECUTED')
        lines.append(f"|{row['label']}|{source_label}|{row['native_valid_count']}/{row['charged_requests']}|{fail}|{count}|{motion}/{f(metrics.get('displacement_m'))}/{f(metrics.get('end_speed_mps'))}|{f(metrics.get('minimum_conditional_clearance_lower_bound_m'))}|{f(metrics.get('max_abs_acceleration_mps2'))}/{f(metrics.get('rms_discrete_jerk_proxy_mps3'))}/{f(metrics.get('max_abs_steer_rad'))}|")
    lines+=['','同一模型输出的T1场景对照：'+('全部已到达项逐值及规范化摘要相同。' if all(p['status'] in ('UNCHANGED','NOT_REACHED') for p in pairs) else '存在差异。'),
            '位置、道路、BODY、全部情景、残差和1m安全间距均由完整T1摘要保护；源文件SHA另列。',
            '加速度单位m/s²、离散jerk代理m/s³、转角rad；无轨迹时留空。连续控制在网格处跳变，因此不能将差分代理解释成连续RMS jerk。','',
            f'登记{len(registered_windows)}个唯一窗口，有模型输出{len(output_windows)}个；本轮新增在线请求涉及{len(current_online_windows)}个窗口。重复回放及修订不增加窗口数。',
            '圆盘与连续车身核验的间距下界采用不同保守包络，数值增大不能解释成实测真实安全改善。',
            '完整独立窗口列表、每次工具/服务阶段、候选失败时间与原始失败对象均见 comparison.json；不合并计算成功率，不作G2或真车安全结论。','']
    with (out/'comparison.md').open('x') as handle:handle.write('\n'.join(lines))
    print(json.dumps(dict(out=str(out),rows=len(rows),registered_unique_windows=len(registered_windows),model_output_unique_windows=len(output_windows),unchanged_T1_pairs=sum(p['status']=='UNCHANGED' for p in pairs)),ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--current',type=Path,default=CURRENT)
    args=parser.parse_args();main(args.out,args.current)
