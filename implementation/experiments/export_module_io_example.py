"""Export a saved driving round's complete module arguments and outputs.

Documentation only: reads saved files, assembles argument views and copies
original outputs. Never invokes an agent, T1-T4, optimizer, executor or acceptor.
"""
import argparse
from copy import deepcopy
import csv
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'implementation'))
from agentic.evidence_io import reserve_directory, write_json_new, write_text_new

DEFAULT_RUNTIME = ROOT/'implementation/results/long_clips_20261029_v2/following_deceleration/runtime/frame_004'
WIRE = ROOT/'implementation/results/exp2_wire_enum_day_pilot_20261028_v1/registration/wire_bundle'


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def export(runtime, attempt, destination):
    runtime = runtime.resolve(); folder = runtime/f'attempt_{attempt:02d}'
    if runtime != DEFAULT_RUNTIME.resolve() or attempt != 2:
        raise ValueError('This focused exporter documents only following_deceleration/frame_004/attempt_02; do not mix another case with its fixed reference notes.')
    record = read(folder/'record.json'); config = read(runtime/'config.json')
    cap, native, prov = (record[k] for k in ('capture', 'output', 'provenance'))
    scene, corridor, trajectory, verification = (read(folder/(name+'.json')) for name in ('T1', 'T2', 'T3', 'T4'))
    execution = read(folder/'execution.json'); receipt = read(folder/'execution_receipt.json')
    ledger = read(folder/'candidate_ledger.json'); final = read(runtime/'round_result.json')
    initial_path = Path(config['capture_path']); initial = read(initial_path)
    transport = runtime/'transport'/f'attempt_{attempt:02d}'
    manifest = read(transport/'input_manifest.json'); instructions = manifest['instructions']
    model_text = (transport/'model_input.txt').read_text()
    assert model_text.startswith(instructions+'\n')
    user = json.loads(model_text[len(instructions)+1:])
    assert user['capture']['metadata'] == cap['metadata']
    assert native == read(transport/'native_output.json') == final['agent_output']
    assert cap == final['scene']['capture'] and final['revisions'] == cap['metadata']['revision']-initial['metadata']['revision']
    assert ledger['search_profile'] == 'bounded_seed_candidates_v2'
    assert verification['verdict'] == 'PASS' and final['verification'] == verification
    assert receipt['trajectory_digest'] == digest(trajectory) and receipt['scene_digest'] == digest(scene)
    out = reserve_directory(destination, readonly_inputs=(runtime, initial_path.parent))
    sources = {}; copies = []; modules = []

    def source(path):
        path = Path(path).resolve(); key = str(path)
        sources[key] = dict(path=key, sha256=sha(path), size_bytes=path.stat().st_size)
        return sources[key]

    def copy(src, relative):
        src = Path(src); target = out/relative; target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as stream:
            stream.write(src.read_bytes())
        copies.append(dict(output=relative, **source(src)))
        return relative

    def put(relative, value):
        path = out/relative; path.parent.mkdir(parents=True, exist_ok=True)
        write_json_new(path, value)

    def call(relative, function, arguments, output_src=None, notes=None):
        put(relative+'/input.json', arguments)
        output = copy(output_src, relative+'/output.json') if output_src else None
        modules.append(dict(module=relative, function=function, input=relative+'/input.json',
                            output=output, input_status='Offline argument assembly from saved values; no function invoked',
                            output_status='Original saved bytes' if output else 'See module output files', notes=notes or []))

    source(folder/'record.json'); source(runtime/'config.json'); source(initial_path)
    clip_plan = ROOT/'implementation/results/long_clips_20261029_v1/following_deceleration/plan.json'
    plan = read(clip_plan); source(clip_plan)
    input_manifest_path = initial_path.parent/'input_manifest.json'
    export_manifest = read(input_manifest_path); source(input_manifest_path)
    export_config = deepcopy(plan['configuration'])
    call('00_capture', 'Acquisition.export', dict(sample_token=export_manifest['sample_token'], config=export_config),
         initial_path, notes=['Acquisition object additionally reads the five acquisition tables and original image files; these are referenced, not copied.'])
    put('00_capture/external_dependencies.json', dict(
        dataset='nuScenes', dataset_root='/data/nuscenes_dataset', version='v1.0-mini',
        allowed_tables=['sample', 'sample_data', 'ego_pose', 'sensor', 'calibrated_sensor'],
        source_basis='Original observation paths and exporter constructor default; no full raw dataset is duplicated.',
        image_files='See tables/images.csv; runtime uses the original files.',
        no_annotation_or_map_inputs=True))
    copy(input_manifest_path, '00_capture/original_input_manifest.json')
    call('01_model', 'OpenAIResponsesAgent.decide', dict(capture=cap, feedback=user['feedback']),
         notes=['Function returns (output, provenance). Complete HTTP request packet was not saved.',
                'Images remain original files; API key and Authorization are excluded.'])
    put('01_model/output.json', dict(output=native, provenance=prov))
    modules[-1]['output']='01_model/output.json'; modules[-1]['output_status']='Return tuple assembled from original saved output and provenance'
    for name in ('model_input.txt', 'input_manifest.json', 'final_agent_message.txt', 'native_output.json',
                 'provenance.json', 'response_metadata.json', 'attempt_summary.json'):
        copy(transport/name, '01_model/'+name)
    write_text_new(out/'01_model/instructions.txt', instructions)
    put('01_model/user_payload.json', user)
    put('01_model/feedback.json', user['feedback'])
    clip_root = runtime.parents[1]
    transport_config = read(clip_root/'transport_config.json')
    copy(clip_root/'transport_config.json', '01_model/transport_config.json')
    attachments=[]
    for index, (observed, sent) in enumerate(zip(cap['observations'], user['capture']['observations'])):
        assert observed['id'] == sent['id'] and sha(observed['path']) == prov['image_sha256'][observed['id']]
        attachments.append(dict(index=index, frame_id=observed['id'], camera_id=observed['camera_id'],
                                capture_time_s=observed['capture_time_s'], time_id=observed['time_id'],
                                attached_name=sent['path'], local_path=observed['path'],
                                sha256=prov['image_sha256'][observed['id']], detail='auto'))
    put('01_model/attachments.json', attachments)
    put('01_model/api_input_view.json', dict(
        record_kind='Offline request-field view, NOT an original HTTP packet', instructions=instructions,
        user_payload=user, attachments=attachments,
        request_options=dict(model=prov['requested_model'], reasoning=dict(effort='medium'), tools=[],
                             stream=False, store=False, response_format_type='json_schema',
                             response_format_name='driving_v3_exp2', strict=True),
        schema_file='../schemas/wire_schema.json', authentication_content='EXCLUDED',
        inline_pixels='Not copied; original image path and SHA are provided'))
    schema_names=['capture_v2', 'agent_output_v3_exp2', 'scene_v2', 'corridor_seed_candidates_v2', 'trajectory_seed_candidates_v2']
    for name in schema_names:
        copy(ROOT/'implementation/schemas'/(name+'.schema.json'), 'schemas/'+name+'.schema.json')
    copy(ROOT/'implementation/docs/module_io_parameters_zh.md', 'PARAMETERS_ZH.md')
    copy(WIRE/'wire_schema.json', 'schemas/wire_schema.json')
    copy(WIRE/'transformation_manifest.json', 'schemas/wire_transformation_manifest.json')
    assert sha(WIRE/'wire_schema.json') == prov['schema_transport']['wire_schema_sha256']
    assert sha(ROOT/'implementation/schemas/agent_output_v3_exp2.schema.json') == prov['schema_transport']['business_schema_sha256']
    call('02_T1', 't1.construct', dict(capture=cap, output=native, provenance=prov, allow_synthetic=False), folder/'T1.json')
    winner = ledger['selected_candidate']
    call('03_T2', 't2.build', dict(scene=scene, policy='yaw_bounded_box_v1', candidate_id=winner,
                                  search_profile=ledger['search_profile']), folder/'T2.json')
    call('04_T3', 't3.solve', dict(scene=scene, corridor=corridor, max_iter=700), folder/'T3.json',
         notes=['max_iter is an implicit default confirmed against the run-pinned source. IPOPT implicit defaults/internal state were not individually logged.'])
    call('05_T4', 't4.verify', dict(scene=scene, trajectory=trajectory), folder/'T4.json',
         notes=['No corridor argument; verification reuses the original scene and its native occupancy trace.'])
    call('06_executor', 'SimulatedExecutor.execute', dict(scene=scene, trajectory=trajectory,
         verification=verification, now_s=cap['metadata']['reference_time_s']), folder/'execution.json')
    put('06_executor/observe_input.json', dict(capture=cap))
    put('06_executor/observe_output.json', None)
    put('06_executor/state_explanation.json', dict(
        evidence_kind='State deduced from pinned source, NOT a recorded process-state dump',
        constructor=dict(current=None, consumed=[]), after_observe=dict(current=digest(cap), consumed=[]),
        after_execute=dict(current=digest(cap), consumed=[digest(verification)])))
    copy(folder/'execution_receipt.json', '06_executor/execution_receipt.json')
    acceptance_input = deepcopy(final)
    acceptance_input.pop('acceptance'); acceptance_input.pop('prototype_conditional_success')
    call('07_acceptor', 'assess_round', dict(result=acceptance_input, capture=initial), folder/'acceptor.json',
         notes=['Runs AFTER simulated execution and receipt creation. Second argument is ORIGINAL revision-zero capture.',
                'First argument is reconstructed by removing post-acceptance fields from the saved final result.'])
    call('08_orchestrator', 'physical', dict(cap=cap, out=native, prov=prov, initial=initial,
         revision=final['revisions'], folder=str(folder), policy=config['planner_profile'], require_turn=config['require_turn']),
         notes=['folder is an original output-path argument shown for documentation, not a destination to rerun.',
                'physical returns result, failure, stages; final result stores acceptance added after consumption.'])
    put('08_orchestrator/output.json', dict(result=final, failure=None, stages=read(folder/'stages.json')['stages']))
    modules[-1]['output']='08_orchestrator/output.json'; modules[-1]['output_status']='Return tuple assembled from saved round_result and stages'
    for name in ('config.json','summary.json','round_result.json'):
        copy(runtime/name, '08_orchestrator/'+name)
    copy(folder/'stages.json', '08_orchestrator/stages.json')
    put('candidates/input.json', dict(scene=scene, profile=ledger['search_profile'], task_filter=None))
    put('candidates/non_json_arguments.json', dict(save=dict(kind='local callback',
        implementation='physical.save(candidate, name, value)', behavior='Exclusive JSON writes under the original attempt/candidates/<id> path; no model or geometry modification')))
    put('candidates/output.json', dict(winner=dict(T2=corridor,T3=trajectory,T4=verification), ledger=ledger, failure=None))
    copy(folder/'candidate_ledger.json','candidates/ledger.json')
    for row in ledger['candidates']:
        cid=row['candidate_id']; cp=folder/'candidates'/cid; base='candidates/'+cid
        for stage in ('T2','T3','T4'):
            if row[stage]=='NOT_REACHED':
                put(base+'/'+stage+'/NOT_REACHED.json', dict(status='NOT_REACHED', no_input_or_output_was_produced=True))
                continue
            args=(dict(scene=scene,policy='yaw_bounded_box_v1',candidate_id=cid,search_profile=ledger['search_profile']) if stage=='T2'
                  else dict(scene=scene,corridor=read(cp/'T2.json'),max_iter=700) if stage=='T3'
                  else dict(scene=scene,trajectory=read(cp/'T3.json')))
            put(base+'/'+stage+'/input.json',args)
            filename=stage+'.json' if (cp/(stage+'.json')).exists() else stage+'-failure.json'
            copy(cp/filename,base+'/'+stage+('/output.json' if filename==stage+'.json' else '/failure.json'))
    for stage in ('T2','T3','T4'):
        assert read(folder/'candidates'/winner/(stage+'.json'))==read(folder/(stage+'.json'))
    if attempt>1:
        old=runtime/f'attempt_{attempt-1:02d}'
        for filename in ('record.json','candidate_ledger.json','stages.json'):
            if (old/filename).exists():copy(old/filename,'history/previous_'+filename)
    tables=out/'tables';tables.mkdir()
    def csv_file(name, rows):
        with (tables/name).open('x',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    csv_file('images.csv',[dict(a,age_at_reference_s=cap['metadata']['reference_time_s']-a['capture_time_s']) for a in attachments])
    csv_file('trajectory_points.csv',trajectory['points'])
    csv_file('controls.csv',[dict(start_s=p['time_s'],end_s=q['time_s'],acceleration_mps2=p['acceleration_mps2'],steer_rad=p['steer_rad'],curvature_per_m=p['curvature_per_m']) for p,q in zip(trajectory['points'],trajectory['points'][1:])])
    csv_file('corridor_cells.csv',[dict(index=i,start_s=c['start_s'],end_s=c['end_s'],heading_band_rad=json.dumps(c['heading_band_rad']),halfplane_count=len(c['halfplanes']),polygon_m=json.dumps(c['polygon_m']),halfplanes=json.dumps(c['halfplanes'])) for i,c in enumerate(corridor['cells'])])
    actor_rows=[]; occupancy_rows=[]
    for actor in native['hypotheses']['objects']:
        actor_rows.append(dict(id=actor['id'],position_m=json.dumps(actor['position_m']),velocity_mps=json.dumps(actor['velocity_mps']),
            velocity_missing=actor['velocity_mps'] is None,**actor['body_geometry'],total_uncertainty_m=actor['uncertainty_m'],
            alignment_method=actor['alignment']['method'],alignment_residual_m=actor['alignment']['residual_m'],
            source_count=len(actor['occupancy'] or []),scenarios=json.dumps(actor['scenarios'],ensure_ascii=False)))
        for src in actor['occupancy'] or []:
            for alt in src['alternatives']:
                occupancy_rows.append(dict(object_id=actor['id'],source_id=src['id'],start_s=src['start_s'],end_s=src['end_s'],kind=src['kind'],status=src['status'],
                    scenario_id=alt['scenario_id'],polygon_m=json.dumps(alt['polygon_m']),source_residual_m=src['residual_m'],
                    total_residual_m=actor['uncertainty_m'],premise_ids=json.dumps(src['premise_ids']),frame_ids=json.dumps(src['frame_ids']),time_ids=json.dumps(src['time_ids'])))
    csv_file('actors.csv',actor_rows);csv_file('occupancy_sources.csv',occupancy_rows)
    write_text_new(out/'tables/README.md', 'CSV以JSON原字段单位为准：米、秒、弧度、m/s、m/s²、m⁻¹。null明确保留为字符串null，不当作0。轨迹11状态，controls只列前10段；末状态附带控制值沿用末段，不是额外第11段。occupancy_sources逐源逐情景列出原模型多边形，未插值或预测。完整字段见各模块JSON。\n')
    put('parameters.json',dict(metadata=cap['metadata'],measurement_semantics=native['measurement_semantics'],vehicle=cap['vehicle'],ego=cap['ego'],
        safety=cap['safety'],navigation_intent=cap['navigation_intent'],decision=native['decision'],road=native['hypotheses']['road'],
        planner=dict(profile=config['planner_profile'],selected_candidate=winner,reference_point=corridor['reference_point'],body_envelope=corridor['body_envelope'],
                     heading_bound_rad=corridor['heading_bound_rad'],heading_bands_rad=trajectory['heading_bands_rad']),
        optimizer=dict(saved_solver_metadata=trajectory['solver'],source_confirmed_explicit_options=dict(max_iter=700,tol=1e-8,acceptable_tol=1e-7,print_level=0,sb='yes',print_time=False)),
        verification=verification,execution=execution,acceptance=read(folder/'acceptor.json'),
        transport_config=transport_config,per_window_max_requests=config['max_requests']))
    source_matches=[]
    for path, expected in config['source_sha256'].items():
        if path in ('implementation/agentic/'+name+'.py' for name in ('t1','t2','t3','t4','metric_contract','candidate_search','runtime','acceptance','geometry','datasets/nuscenes_capture')):
            source_matches.append(dict(path=path,run_sha256=expected,current_sha256=sha(ROOT/path),matches=expected==sha(ROOT/path)))
    assert source_matches and all(x['matches'] for x in source_matches)
    put('source_reference.json',dict(run_commit=config['source_commit'],documentation_git_head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        export_script_sha256=sha(__file__),source_matches=source_matches,
        limits=['API raw HTTP packet and process-state dumps were not saved.', 'Implicit IPOPT defaults are not fully recorded.',
                'Only documented fields and public transport metadata are exported; no authentication credentials.']))
    put('module_index.json',modules)
    text='''# 各模块完整输入输出与具体参数：跟车 frame_004

本目录整理你正在查看的 **scene-0103 / frame_004 / attempt_02**，样本 `747aa46b9a4641fe90db05d97db2acea`。初答因T2拒绝，第二次真实模型输出经五候选规划后通过，已模拟消费0.2秒。**本次只是离线整理，新增模型、工具求解、核验和执行调用均为0。** 原实验不改。

先读[中文参数说明](PARAMETERS_ZH.md)，查实际值用[parameters.json](parameters.json)；完整机器索引见[module_index.json](module_index.json)。这里的input.json是用已保存对象组装的完整函数参数视图，输出JSON按原始文件字节复制；不是重新运行产生的结果。

```mermaid
flowchart LR
  A["采集与自车信息"] --> B["在线模型：图像＋提示＋反馈"]
  B --> C["T1：米制场景"]
  C --> D["五个候选分别运行 T2 → T3 → T4"]
  D --> E["在核验通过者中选最小代价"]
  D -. "全部候选失败时反馈；受原调用预算约束" .-> B
  E --> F["模拟消费0.2秒前缀"]
  F --> G["执行回执"]
  G --> H["独立接受器重建核对"]
```

## 逐模块完整输入输出

|模块|实际入口与输入|完整输出|用途|
|---|---|---|---|
|采集导出|`Acquisition.export(sample_token,config)`；[输入](00_capture/input.json)、[外部数据依赖](00_capture/external_dependencies.json)|[原始capture，revision=0](00_capture/output.json)|10图引用、采集/自车/标定、车辆、安全与导航；不读取目标注释或地图|
|在线模型|`agent.decide(capture,feedback)`；[完整函数实参](01_model/input.json)、[实际完整文本](01_model/model_input.txt)、[图像清单](01_model/attachments.json)、[传输配置](01_model/transport_config.json)|[返回output＋provenance](01_model/output.json)、[原生JSON](01_model/native_output.json)、[原始模型文本](01_model/final_agent_message.txt)|语义决策与原生米制假设同时输出；本例是revision=1|
|T1|`t1.construct(capture,output,provenance,allow_synthetic=False)`；[输入](02_T1/input.json)|[完整米制场景](02_T1/output.json)|校验原生计量/时刻/证据，规范化占用到工具网格并保留source trace|
|T2，选中候选|`t2.build(scene,policy,candidate_id,search_profile)`；[输入](03_T2/input.json)|[完整走廊](03_T2/output.json)|10个区间的允许域、半平面、航向带和完整种子|
|T3，选中候选|`t3.solve(scene,corridor,max_iter=700)`；[输入](04_T3/input.json)|[完整轨迹和求解元数据](04_T3/output.json)|11状态、10段控制，硬约束＋速度/平顺性代价|
|T4，选中候选|`t4.verify(scene,trajectory)`；[输入](05_T4/input.json)|[完整核验报告](05_T4/output.json)|直接读原场景，**不以走廊作为核验输入**；PASS才释放前缀|
|模拟执行器|先`observe(capture)`再`execute(scene,trajectory,verification,now_s)`；[observe输入](06_executor/observe_input.json)、[执行输入](06_executor/input.json)|[模拟状态](06_executor/output.json)、[编排器生成的回执](06_executor/execution_receipt.json)|按原控制积分0.2s，只消费一次，无真实车辆控制|
|独立接受器|`assess_round(result,initial_capture)`；[完整输入](07_acceptor/input.json)|[完整接受结论](07_acceptor/output.json)|执行回执之后重建T1/T2、重跑独立T4、比对积分/来源/摘要；不重新求解T3|
|单轮编排器|`physical(...)`；[完整参数](08_orchestrator/input.json)|[返回元组视图](08_orchestrator/output.json)、[原round_result](08_orchestrator/round_result.json)、[阶段表](08_orchestrator/stages.json)|汇总真实调用顺序、工具结果和执行回执|

## 五候选与失败反馈

完整候选编排输入见[candidates/input.json](candidates/input.json)，保存回调见[non_json_arguments.json](candidates/non_json_arguments.json)，返回值见[output.json](candidates/output.json)，原始[ledger](candidates/ledger.json)保持。每个候选文件夹都有实际到达阶段的完整input/output或failure；未到达的阶段只写NOT_REACHED，不补造输入输出。

|候选|T2|T3|T4|结果|
|---|---|---|---|---|
|[original](candidates/original)|PASS|PASS|PASS|选中；objective=4.949915881568494|
|[brake_straight](candidates/brake_straight)|PASS|PASS|PASS|objective=4.949915901354775|
|[brake_right](candidates/brake_right)|FAIL|NOT_REACHED|NOT_REACHED|航向界，0.6–0.8s|
|[brake_left](candidates/brake_left)|FAIL|NOT_REACHED|NOT_REACHED|目标CENTER约束经参考点转换后排除初态，0–0.2s|
|[brake_right_strong](candidates/brake_right_strong)|FAIL|NOT_REACHED|NOT_REACHED|航向界，0.4–0.6s|

这里先做完五个候选再选核验通过者中代价最小的一个，不是看到T3成功就执行；有限候选失败不证明全局无解。上一个模型回答和失败记录在[history](history)，本次真实修订收到的**完整反馈**在[feedback.json](01_model/feedback.json)，没有因导出文档再次调用模型。

## 参数表与字段说明

- [PARAMETERS_ZH.md](PARAMETERS_ZH.md)：各模块参数、单位、用途、硬/软要求、源码默认值与数值容差。
- [images.csv](tables/images.csv)：10张原始图像、相机、真实采集时间、相对年龄和SHA。
- [actors.csv](tables/actors.csv)：7个声明对象的位置、速度或null、车身尺寸/航向、残差和条件情景。
- [occupancy_sources.csv](tables/occupancy_sources.csv)：每个原模型源区间、每种情景、多边形及残差/前提/证据。
- [corridor_cells.csv](tables/corridor_cells.csv)：10个区间的完整半平面、允许多边形、航向带。
- [trajectory_points.csv](tables/trajectory_points.csv)：全部11个时刻的位置、航向、速度、控制量及曲率。
- [controls.csv](tables/controls.csv)：真正的10段常控制区间；末状态中的控制字段沿用末段，不代表额外执行一段。
- [schemas](schemas)：本例使用的业务、capture、场景、走廊、轨迹和wire定义，不改变旧字段或校验要求。

所有位置单位为m，时间为s，角度为rad，速度m/s，加速度m/s²，曲率m⁻¹。坐标原点为参考采集时刻自车BODY中心；X前/Y左；目标位置为BODY中心地面XY；速度是绝对地面速度在固定参考轴的分量。后轴初态为[-1.2,0]m，轨迹同时输出后轴和中心，不能再给目标加1.2m。

`hold_lane`主要通过target_region、道路和速度偏好落到数值规划，不是独立的车道ID/中心线跟踪模块；交通/停车要求仍需对应结构化字段。所有结论依赖模型声明的局部场景和条件；不是GT准确性或真实车辆安全结论。

## 完整性的实际边界

模型实际提交的完整文字和原生回答已逐字节复制。图像保留原文件路径/SHA，不复制数据集或base64；[API输入视图](01_model/api_input_view.json)解释实际请求字段，但**原始HTTP请求封包未被保存**，不能称它是原始wire；不导出API密钥和Authorization。输出业务Schema和传输wire分开保存，服务报告模型gpt-6.1-sol，独立快照未知。

模拟器的current/consumed状态没有原始单独转储；[状态说明](06_executor/state_explanation.json)明确属于源码推导。独立接受器的第二参数是原revision=0采集；模型/T1场景是revision=1。接受器第一参数由最终round_result删除事后追加的acceptance和prototype_conditional_success恢复，不把事后字段当成当时实参。

求解器的显式参数来自已核对运行SHA一致的源码，不冒充所有IPOPT内部默认值均有运行日志。来源与复制映射见[source_reference.json](source_reference.json)、[input_sources.json](input_sources.json)。整理过程只做读取、复制、JSON组装和表格展开。

## 零调用复现

```bash
cd /data2/hzy/MAP/agentic-driving
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 .venv/bin/python implementation/experiments/export_module_io_example.py \\
  --out /tmp/new_module_io_example
```

输出目录必须尚不存在。该命令不重放规划、核验或执行，也不发送模型请求。
'''
    write_text_new(out/'README.md',text)
    put('input_sources.json',dict(sources=list(sources.values()),byte_copies=copies))
    for item in copies:
        assert sha(out/item['output'])==item['sha256']
    put('export_verification.json',dict(status='PASS',new_model_calls=0,new_tool_calls=0,new_execution_calls=0,
        modules=len(modules),candidates=len(ledger['candidates']),images=len(attachments),actors=len(actor_rows),
        occupancy_source_count=sum(len(o['occupancy'] or []) for o in native['hypotheses']['objects']),
        occupancy_alternative_rows=len(occupancy_rows),trajectory_states=len(trajectory['points']),control_intervals=len(trajectory['points'])-1,
        copied_files=len(copies),all_copied_bytes_match_sources=True,selected_outputs_match_candidate=True,
        core_source_matches_run=True,original_capture_revision=initial['metadata']['revision'],model_capture_revision=cap['metadata']['revision']))
    print(json.dumps(dict(out=str(out),modules=len(modules),copied_files=len(copies),new_model_calls=0,new_tool_calls=0),ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime',type=Path,default=DEFAULT_RUNTIME)
    parser.add_argument('--attempt',type=int,default=2)
    parser.add_argument('--out',type=Path,required=True)
    options=parser.parse_args()
    export(options.runtime,options.attempt,options.out)
