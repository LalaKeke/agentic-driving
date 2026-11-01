"""Compare fixed saved real outputs with v2/v3 using the existing replay CLI.

The 21-window ended baseline cohort and a separately supplied legacy record
cohort are mutually exclusive. No model is instantiated by this script; replay
children have Python network connects disabled and use --replay exclusively.
"""
import argparse
from collections import Counter
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'implementation'))
from agentic.evidence_io import reserve_directory, write_json_new, write_text_new

PROFILES = ('bounded_seed_candidates_v2', 'bounded_seed_candidates_v3')
STAGES = ('T1', 'T2', 'T3', 'T4', 'EXECUTION', 'ACCEPTOR')
WIRE = ROOT/'implementation/results/exp2_wire_enum_day_pilot_20261028_v1/registration/wire_bundle'
RUNNER = ROOT/'implementation/experiments/run_local_prototype.py'
DEFAULT_BASELINE = ROOT/'implementation/results/hard_clip_baseline_20261030_v1'
CLAIM = ('Saved real model outputs, offline replanning and independent simulated '
         'prefixes only. No new online success, model calls saved, GT accuracy, '
         'counterfactual observations or real vehicle safety claim.')
GUARD = """import runpy,socket,sys
def denied(*args,**kwargs):
    raise RuntimeError('OFFLINE_REPLAY_NETWORK_PROHIBITED')
socket.socket.connect=denied
socket.socket.connect_ex=denied
socket.create_connection=denied
sys.argv=sys.argv[1:]
runpy.run_path(sys.argv[0],run_name='__main__')
"""


def resolve(value, base=ROOT):
    p = Path(value)
    return p.resolve() if p.is_absolute() else (base/p).resolve()


def read(path, default=None):
    return json.loads(Path(path).read_text()) if Path(path).is_file() else default


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canonical(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def record_info(path, number=None):
    path = resolve(path)
    if number is None and path.parent.name.startswith('attempt_'):
        try:
            number = int(path.parent.name.split('_')[-1])
        except ValueError:
            pass
    value = read(path)
    if not isinstance(value, dict) or any(k not in value for k in ('capture', 'output', 'provenance')):
        raise ValueError('Missing original capture/output/provenance record: '+str(path))
    if value['provenance'].get('source') != 'online_model':
        raise ValueError('Only saved online_model records belong in this real-output cohort')
    if not isinstance(value['output'], dict):
        raise ValueError('Missing native output is not an empty successful scene')
    return dict(path=str(path), sha256=sha(path), original_attempt=number,
                window_identity=value['capture']['metadata']['scene_id'],
                capture_digest=canonical(value['capture']), output_digest=canonical(value['output']),
                original_prompt=value['provenance'].get('prompt_version'))


def baseline_windows(root):
    root = root.resolve()
    batch = read(root/'batch_summary.json')
    if not batch or batch.get('ended') is not True:
        raise ValueError('Baseline must have an actually ended batch_summary.json before comparison')
    windows = []; sources = [root/'batch_summary.json']
    for pp in sorted(root.glob('*/plan.json')):
        plan = read(pp); clip = plan.get('clip_id', pp.parent.name)
        clip_batch = read(pp.parent/'batch_summary.json')
        started = any((pp.parent/n).exists() for n in ('runtime', 'transport', 'budget', 'config.json'))
        if started and (not clip_batch or clip_batch.get('ended') is not True):
            raise ValueError('A started clip has not ended: '+clip)
        sources.append(pp)
        if clip_batch is not None:
            sources.append(pp.parent/'batch_summary.json')
        for case in plan['cases']:
            runtime = pp.parent/'runtime'/case['case_id']
            summary = read(runtime/'summary.json', {})
            records = []
            for directory in sorted(runtime.glob('attempt_*')):
                if not (directory/'record.json').is_file():
                    continue
                number = int(directory.name.split('_')[-1])
                info = record_info(directory/'record.json', number)
                if info['window_identity'] != 'nusc_'+case['sample_token']:
                    raise ValueError('A saved record belongs to another registered sample')
                records.append(info)
            if len(records) > min(2, case.get('request_limit', 2)):
                raise ValueError('More than two saved responses would change the registered comparison')
            charged = summary.get('charged_requests', len(list(runtime.glob('charge_*.json'))))
            windows.append(dict(clip_id=clip, case_id=case['case_id'],
                scene_token=case.get('scene_token', plan.get('scene_token')),
                scene_name=case.get('scene_name', plan.get('scene_name')),
                sample_token=case['sample_token'], window_identity='nusc_'+case['sample_token'],
                generation_attempted=charged > 0, original_charged_requests=charged,
                original_runtime=str(runtime), original_status=summary.get('prototype_conditional_success'),
                original_fatal=summary.get('fatal'), require_turn=bool(case.get('require_turn', False)),
                records=records))
    if len(windows) != 21 or len({w['window_identity'] for w in windows}) != 21:
        raise ValueError('The fixed baseline must retain all 21 unique registered windows')
    return windows, sources, dict(cohort_id=root.name, mode='FROZEN_21_WINDOW_BASELINE',
                                  baseline_root=str(root), planned_window_denominator=21)


def legacy_windows(path):
    path = path.resolve(); value = read(path)
    entries = value.get('records') if isinstance(value, dict) else value
    if not isinstance(entries, list) or not entries:
        raise ValueError('--records-json needs a nonempty records list')
    grouped = {}; seen = set()
    for index, entry in enumerate(entries):
        item = {'record': entry} if isinstance(entry, str) else entry
        record_path = item.get('record', item.get('path', item.get('record_path')))
        if not isinstance(record_path, str):
            raise ValueError('Each legacy entry needs a record path')
        # Project-relative is the normal format; manifest-relative is a fallback.
        candidate = resolve(record_path)
        if not candidate.exists():
            candidate = resolve(record_path, path.parent)
        info = record_info(candidate, item.get('attempt'))
        info.update(source_label=item.get('case_id'), purpose=item.get('purpose'))
        if info['path'] in seen:
            raise ValueError('Duplicate legacy response path')
        seen.add(info['path']); key = info['window_identity']
        if key not in grouped:
            grouped[key] = dict(clip_id=item.get('clip_id', 'legacy_scene_unspecified'),
                case_id=item.get('case_id', 'window_'+str(len(grouped)+1).zfill(3)),
                scene_token=item.get('scene_token'), scene_name=item.get('scene_name'),
                sample_token=key.removeprefix('nusc_'), window_identity=key,
                generation_attempted=True, original_charged_requests=None,
                original_runtime=None, original_status=None, original_fatal=None,
                require_turn=bool(item.get('require_turn', False)), records=[])
        window = grouped[key]
        if item.get('scene_token') not in (None, window['scene_token']):
            raise ValueError('Two records of one sample cite conflicting scenes')
        window['records'].append(info)
        if len(window['records']) > 2:
            raise ValueError('Legacy windows may contain at most two fixed native responses')
    return list(grouped.values()), [path], dict(
        cohort_id=value.get('cohort_id', path.stem) if isinstance(value, dict) else path.stem,
        mode='SEPARATE_LEGACY_REAL_OUTPUT_COHORT', records_json=str(path),
        planned_window_denominator=len(grouped), mixed_with_21_window_baseline=False)


def replay(window, info, profile, directory, python, timeout):
    native = read(info['path']); capture_path = directory.parent/'capture.json'
    if not capture_path.exists():
        write_json_new(capture_path, native['capture'])
    elif read(capture_path) != native['capture']:
        raise ValueError('Do not share a capture file between different response revisions')
    command = [str(python), '-c', GUARD, str(RUNNER), '--capture', str(capture_path),
               '--wire', str(WIRE), '--replay', info['path'],
               '--planner-profile', profile, '--out', str(directory)]
    if window['require_turn']:
        command.append('--require-turn')
    environment = dict(os.environ, OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1')
    for name in ('OPENAI_API_KEY', 'DRIVING_API_KEY', 'DRIVING_API_KEY_FILE'):
        environment.pop(name, None)
    console = directory.parent/(profile+'.console.txt')
    started = time.monotonic(); timed_out = False
    with console.open('x') as stream:
        try:
            process = subprocess.run(command, cwd=ROOT, env=environment, stdout=stream,
                                     stderr=subprocess.STDOUT, timeout=timeout)
            exit_code = process.returncode
        except subprocess.TimeoutExpired:
            exit_code = None; timed_out = True
    saved = read(directory/'summary.json', {})
    attempt = (saved.get('attempts') or [{}])[0]
    stages = {s: attempt.get('stages', {}).get(s, 'NOT_REACHED') for s in STAGES}
    ledger = read(directory/'attempt_01/candidate_ledger.json', {})
    result = read(directory/'round_result.json', {})
    failure = attempt.get('failure') or saved.get('fatal')
    if timed_out or exit_code not in (0, 3) or not saved:
        failure = dict(code='OFFLINE_REPLAY_TIMEOUT' if timed_out else 'OFFLINE_REPLAY_PROCESS_ERROR',
                       category='offline_replay_process', exit_code=exit_code)
        blocking_stage = 'OFFLINE_PROCESS'
    else:
        blocking_stage = next((s for s in STAGES if stages[s] == 'FAIL'), None)
    success = bool(saved.get('prototype_conditional_success'))
    if success and (exit_code != 0 or not all(stages[s] == 'PASS' for s in STAGES)):
        raise ValueError('Replay success does not have all required stages')
    executed = result.get('execution', {})
    receipt = result.get('execution_receipt', {})
    points = result.get('trajectory', {}).get('points', [])
    displacements = executed.get('center_displacement_m')
    controls = points[:-1]
    jerk = [(b['acceleration_mps2']-a['acceleration_mps2'])/(b['time_s']-a['time_s'])
            for a,b in zip(controls, controls[1:])]
    return dict(profile=profile, mode='SAVED_REAL_OUTPUT_OFFLINE_REPLANNING', new_model_calls=0,
        source_record=info['path'], source_record_sha256=info['sha256'],
        source_capture_digest=info['capture_digest'], source_output_digest=info['output_digest'],
        original_attempt=info['original_attempt'], original_prompt=info['original_prompt'],
        original_record_label=info.get('source_label'), record_purpose=info.get('purpose'),
        output_dir=str(directory), console=str(console), elapsed_s=time.monotonic()-started,
        exit_code=exit_code, timed_out=timed_out, stages=stages, conditional_chain_pass=success,
        earliest_failure_stage=blocking_stage, failure=failure,
        candidate_search_reached=bool(ledger),
        candidate_counts={s:sum(r.get(s)=='PASS' for r in ledger.get('candidates', [])) for s in ('T2','T3','T4')},
        candidate_count=ledger.get('candidate_count', 0), selected_candidate=ledger.get('selected_candidate'),
        prefix_consumed=bool(executed and receipt.get('consumed_once')),
        motion_class=result.get('acceptance', {}).get('motion_class', 'NOT_EXECUTED'),
        displacement_xy_m=displacements,
        displacement_m=math.hypot(*displacements) if displacements is not None else None,
        end_speed_mps=executed.get('speed_mps'), prefix_s=executed.get('elapsed_s'),
        minimum_conditional_clearance_lower_bound_m=result.get('verification', {}).get('minimum_clearance_lower_bound_m'),
        solver=result.get('trajectory', {}).get('solver'),
        max_abs_acceleration_mps2=max((abs(p['acceleration_mps2']) for p in controls), default=None),
        max_abs_steer_rad=max((abs(p['steer_rad']) for p in controls), default=None),
        rms_discrete_jerk_proxy_mps3=math.sqrt(sum(x*x for x in jerk)/len(jerk)) if jerk else None,
        jerk_scope='Acceleration-difference proxy, not continuous RMS jerk of piecewise-constant controls')


def aggregate_window(window, responses):
    profiles = {}
    for profile in PROFILES:
        values = [r for r in responses if r['profile']==profile]
        selected = next((r for r in values if r['conditional_chain_pass']), values[-1] if values else None)
        availability = ('AVAILABLE_SAVED_NATIVE_OUTPUT' if values else
                        'ATTEMPTED_WITHOUT_SAVED_NATIVE_OUTPUT' if window['generation_attempted'] else 'NOT_ATTEMPTED')
        profiles[profile] = dict(availability=availability,
            first_saved_native_response_pass=values[0]['conditional_chain_pass'] if values else (False if window['generation_attempted'] else None),
            any_of_at_most_two_saved_responses_pass=any(v['conditional_chain_pass'] for v in values) if values else (False if window['generation_attempted'] else None),
            saved_response_count=len(values), selected_response=selected,
            first_saved_original_attempt=values[0]['original_attempt'] if values else None)
    return dict(**window, profiles=profiles)


def main(args):
    global PROFILES
    PROFILES = tuple(args.profiles)
    if PROFILES[0] == PROFILES[1]:
        raise ValueError('Choose two distinct explicit planner profiles')
    if args.records_json:
        windows, input_paths, cohort = legacy_windows(args.records_json)
    else:
        windows, input_paths, cohort = baseline_windows(args.baseline_root)
    root = reserve_directory(args.out, readonly_inputs=tuple(input_paths))
    source_files = [Path(__file__), RUNNER]
    source_files += [ROOT/'implementation/agentic'/f'{n}.py' for n in
                     ('t1','t2','t3','t4','metric_contract','candidate_search','geometry','runtime','acceptance')]
    source_files += [ROOT/'implementation/schemas'/f'{n}_seed_candidates_v{v}.schema.json'
                     for n in ('corridor','trajectory') for v in (2,3,4)]
    provenance = dict(git_head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        code_dirty=bool(subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True)),
        source_sha256={str(p.relative_to(ROOT)):sha(p) for p in source_files},
        cohort_inputs=[dict(path=str(p),sha256=sha(p)) for p in input_paths],
        python=str(args.python.resolve()), network_connect_prohibited_in_replay_children=True,
        generation_requests=0, claim=CLAIM)
    write_json_new(root/'source.json',provenance)
    all_rows = []; window_rows = []; processes = 0
    for wi, window in enumerate(windows):
        # Numeric directories avoid changing or trusting arbitrary source case labels.
        window_dir = reserve_directory(root/f'window_{wi+1:03d}')
        rows = []
        for ri, info in enumerate(window['records']):
            response_dir = reserve_directory(window_dir/f'native_{ri+1:02d}')
            for profile in PROFILES:
                value = replay(window,info,profile,response_dir/profile,args.python,args.timeout_s)
                rows.append(value);all_rows.append(dict(clip_id=window['clip_id'],case_id=window['case_id'],window_identity=window['window_identity'],**value));processes+=1
                print(json.dumps(dict(clip=window['clip_id'],case=window['case_id'],native=ri+1,
                    profile=profile,pass_=value['conditional_chain_pass'],failure_stage=value['earliest_failure_stage']),ensure_ascii=False),flush=True)
            if sha(info['path']) != info['sha256']:
                raise ValueError('Original model record bytes changed during replay')
        combined = aggregate_window(window,rows);window_rows.append(combined)
        write_json_new(window_dir/'comparison.json',combined)
    if any(sha(ROOT/name)!=expected for name,expected in provenance['source_sha256'].items()):
        raise ValueError('Replay code changed during comparison; do not mix source versions')
    scenes = {w['scene_token'] for w in windows if w.get('scene_token')}
    scene_count = len(scenes) if all(w.get('scene_token') for w in windows) else None
    aggregate = {}
    for profile in PROFILES:
        items = [w['profiles'][profile] for w in window_rows]
        aggregate[profile] = dict(first_saved_native_pass_windows=sum(x['first_saved_native_response_pass'] is True for x in items),
            any_saved_response_pass_windows=sum(x['any_of_at_most_two_saved_responses_pass'] is True for x in items),
            registered_window_denominator=len(windows),
            attempted_windows=sum(w['generation_attempted'] for w in windows),
            windows_with_saved_native_output=sum(bool(w['records']) for w in windows),
            not_attempted_windows=sum(x['availability']=='NOT_ATTEMPTED' for x in items),
            attempted_without_saved_native_windows=sum(x['availability']=='ATTEMPTED_WITHOUT_SAVED_NATIVE_OUTPUT' for x in items),
            window_failure_stage_counts=dict(Counter((x['selected_response'] or {}).get('earliest_failure_stage') or x['availability']
                for x in items if x['any_of_at_most_two_saved_responses_pass'] is not True)),
            stage_pass_windows_any_saved_response={s:sum(any(r['window_identity']==w['window_identity']
                and r['profile']==profile and r['stages'][s]=='PASS' for r in all_rows) for w in windows) for s in STAGES})
    recovered = [w['window_identity'] for w in window_rows if w['profiles'][PROFILES[0]]['any_of_at_most_two_saved_responses_pass'] is not True and w['profiles'][PROFILES[1]]['any_of_at_most_two_saved_responses_pass'] is True]
    regressed = [w['window_identity'] for w in window_rows if w['profiles'][PROFILES[0]]['any_of_at_most_two_saved_responses_pass'] is True and w['profiles'][PROFILES[1]]['any_of_at_most_two_saved_responses_pass'] is not True]
    report = dict(ended=True,cohort=cohort,unique_scenes=scene_count,unique_windows=len({w['window_identity'] for w in windows}),
        scene_count_missing_identity=scene_count is None,saved_native_responses=sum(len(w['records']) for w in windows),
        offline_replay_processes=processes,new_model_calls=0,new_online_successes=0,model_calls_actually_saved=0,
        profiles=aggregate,recovered_on_fixed_saved_responses=recovered,regressed_on_fixed_saved_responses=regressed,
        windows=window_rows,responses=all_rows,claim=CLAIM)
    write_json_new(root/'comparison.json',report)
    columns=('clip_id','case_id','window_identity','profile','saved_response_count','first_saved_native_response_pass',
             'any_of_at_most_two_saved_responses_pass','availability','failure_stage','failure_code','T2_candidates','T3_candidates','T4_candidates',
             'prefix_consumed','motion_class','displacement_m','end_speed_mps','minimum_conditional_clearance_lower_bound_m')
    with (root/'comparison.csv').open('x',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=columns);writer.writeheader()
        for w in window_rows:
            for profile in PROFILES:
                value=w['profiles'][profile];selected=value['selected_response'] or {};failure=selected.get('failure') or {}
                row={k:w[k] for k in ('clip_id','case_id','window_identity')}
                row.update({k:value[k] for k in ('saved_response_count','first_saved_native_response_pass','any_of_at_most_two_saved_responses_pass','availability')})
                row.update(profile=profile,failure_stage=selected.get('earliest_failure_stage'),failure_code=failure.get('code'))
                row.update({s+'_candidates':selected.get('candidate_counts',{}).get(s) for s in ('T2','T3','T4')})
                row.update({k:selected.get(k) for k in ('prefix_consumed','motion_class','displacement_m','end_speed_mps','minimum_conditional_clearance_lower_bound_m')})
                writer.writerow(row)
    labels='/'.join(p.rsplit('_',1)[-1] for p in PROFILES)
    title = (f'# 困难clip固定21窗：{labels}离线比较' if cohort['mode']=='FROZEN_21_WINDOW_BASELINE'
             else f'# 独立保存记录组：{labels}离线比较（不混新21窗分母）')
    lines=[title,'',
        f"范围：{cohort['mode']}；{len(windows)}个登记窗口、{scene_count if scene_count is not None else '身份未全提供，数量未知'}个scene、{report['saved_native_responses']}份保存原生记录。",
        '**新增真实模型调用0。这里只比较相同JSON下的离线重规划，不新增在线成功，不宣称实际节省了模型调用。**','',
        '|profile|首份保存原生响应通过窗|已有至多两份任一通过窗|登记分母|未尝试|尝试但无保存原生输出|',
        '|---|---:|---:|---:|---:|---:|---:|']
    for profile,a in aggregate.items():
        lines.append(f"|{profile}|{a['first_saved_native_pass_windows']}|{a['any_saved_response_pass_windows']}|{len(windows)}|{a['not_attempted_windows']}|{a['attempted_without_saved_native_windows']}|")
    lines+=['','首份指首份可读取的原生record，不把缺失响应补成null场景。original_attempt另列；若先前请求没有可用record，它仍计入原始预算且不会被本比较改写。NOT_ATTEMPTED保持未尝试，不计成模型或规划器失败。','',
        '[完整机器表](comparison.json) / [逐窗逐profile CSV](comparison.csv) / [源码与输入SHA](source.json)。每个window/native/profile目录保留CLI完整结果和console；所有模型JSON、采集、BODY、全部情景、残差和1m间距保持原样。T1拒绝时下游保持NOT_REACHED。','',
        f'固定记录下新增恢复窗：{len(recovered)}；回退窗：{len(regressed)}。这不是在线成功率。',
        '每个通过结果单独模拟消费短前缀，不把不同原始观测拼接成执行反事实闭环。平顺性为离散加速度差分代理，不是分段常控制的连续RMS jerk。']
    write_text_new(root/'README.md','\n'.join(lines)+'\n')
    print(json.dumps(dict(out=str(root),unique_windows=report['unique_windows'],profiles=aggregate,new_model_calls=0),ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    group=parser.add_mutually_exclusive_group()
    group.add_argument('--baseline-root',type=Path,default=DEFAULT_BASELINE)
    group.add_argument('--records-json',type=Path)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--python',type=Path,default=ROOT/'.venv/bin/python')
    parser.add_argument('--timeout-s',type=float,default=180)
    parser.add_argument('--profiles',nargs=2,default=PROFILES,
                        choices=('bounded_seed_candidates_v2','bounded_seed_candidates_v3','bounded_seed_candidates_v4'),
                        help='Explicit baseline and proposed planner; default remains v2/v3')
    args=parser.parse_args()
    if args.timeout_s<=0:parser.error('--timeout-s must be positive')
    main(args)
