"""Read-only audit of archived real model outputs; never reads nuScenes GT.

Counts are per response or object entry, NOT independent driving scenarios.
No output is repaired or admitted to a tool by this diagnostic.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path

ROOT=Path(__file__).resolve().parents[3]


def planes(vertices):
    vertices=[tuple(p) for p in vertices]
    area=sum(a[0]*b[1]-b[0]*a[1] for a,b in zip(vertices,vertices[1:]+vertices[:1]))/2
    if area<0:vertices.reverse()
    result=[]
    for a,b in zip(vertices,vertices[1:]+vertices[:1]):
        dx,dy=b[0]-a[0],b[1]-a[1];length=math.hypot(dx,dy)
        if length>1e-12:result.append((dy/length,-dx/length,(dy*a[0]-dx*a[1])/length))
    return result


def clip(vertices,plane):
    if not vertices:return []
    nx,ny,b=plane;result=[]
    for a,c in zip(vertices,vertices[1:]+vertices[:1]):
        va=nx*a[0]+ny*a[1]-b;vc=nx*c[0]+ny*c[1]-b
        ina=va<=1e-10;inc=vc<=1e-10
        if ina:result.append(list(a))
        if ina!=inc:
            u=va/(va-vc);result.append([a[0]+u*(c[0]-a[0]),a[1]+u*(c[1]-a[1])])
    return result


def eroded_polygon(vertices,radius):
    result=[list(v) for v in vertices]
    for nx,ny,b in planes(vertices):result=clip(result,(nx,ny,b-radius))
    return result


def polygon_area(vertices):
    if len(vertices)<3:return 0.
    return abs(sum(a[0]*b[1]-b[0]*a[1] for a,b in zip(vertices,vertices[1:]+vertices[:1])))/2


def grid_status(occupancy,horizon,step):
    if occupancy is None:return 'unknown'
    count=round(horizon/step)
    if len(occupancy)!=count:return 'wrong_interval_count'
    for k,cell in enumerate(occupancy):
        if abs(cell['start_s']-k*step)>1e-8 or abs(cell['end_s']-(k+1)*step)>1e-8:return 'wrong_interval_boundaries'
    return 'complete_grid'


def audit(results_root,out_dir):
    sources={}
    def read(path):
        raw=path.read_bytes()
        try:key=str(path.relative_to(ROOT))
        except ValueError:key=str(path)
        sources[key]={'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)}
        return json.loads(raw)
    index=read(results_root/'response_index.json');rows=[]
    captures={}
    for run in sorted({r['run'] for r in index}):
        captures[run]=[(int(p.name[:4]),p,read(p)) for p in sorted((results_root/'raw_runs'/run).glob('*capture.json'))]
    for entry in index:
        record_path=results_root/'raw_runs'/entry['run']/entry['file'];record=read(record_path)
        output=record['output'];decision=output['decision'];hypotheses=output['hypotheses'];number=int(entry['file'][:4])
        choices=[r for r in captures[entry['run']] if r[0]<number and r[2]['metadata']['scene_id']==decision['scene_id'] and r[2]['metadata']['revision']==decision['revision']]
        if not choices:raise ValueError('No preceding capture matches '+str(record_path))
        _,capture_path,capture=max(choices,key=lambda r:r[0]);m=capture['metadata'];d=capture['vehicle']['center_to_rear_axle_m']
        target=decision['target_region'];hp=planes(target);eroded=eroded_polygon(target,d)
        initial=(capture['ego']['x_m'],capture['ego']['y_m']);yaw=capture['ego']['yaw_rad'];rear=(initial[0]-d*math.cos(yaw),initial[1]-d*math.sin(yaw))
        objects=[dict(id=o['id'],occupancy_state=grid_status(o['occupancy'],m['horizon_s'],m['step_s']),supplied_intervals=None if o['occupancy'] is None else len(o['occupancy']),expected_intervals=round(m['horizon_s']/m['step_s']),uncertainty_known=o['uncertainty_m'] is not None,position_known=o['position_m'] is not None,velocity_known=o['velocity_mps'] is not None) for o in hypotheses['objects']]
        delta=decision['valid_until_s']-m['reference_time_s'];note=hypotheses['confidence_note'] or ''
        rows.append(dict(record_id=entry['run']+':'+entry['file'],sample_id=entry['sample_id'],kind=entry['kind'],agent_output_contract=entry['agent_output_contract'],capture_source=str(capture_path.relative_to(ROOT)),record_source=str(record_path.relative_to(ROOT)),revision=m['revision'],horizon_s=m['horizon_s'],step_s=m['step_s'],decision_validity_relative_s=delta,decision_covers_horizon=delta+1e-8>=m['horizon_s'],target_bounds_m=[min(v[0] for v in target),min(v[1] for v in target),max(v[0] for v in target),max(v[1] for v in target)],target_contains_initial_center=all(nx*initial[0]+ny*initial[1]<=b+1e-8 for nx,ny,b in hp),center_to_rear_axle_m=d,target_after_current_t2_erosion_area_m2=polygon_area(eroded),target_after_current_t2_erosion_empty=polygon_area(eroded)<=1e-8,eroded_target_contains_initial_rear=all(nx*rear[0]+ny*rear[1]<=b-d+1e-8 for nx,ny,b in hp),scale_basis=hypotheses['scale_basis'],scale_label_unsupported_by_current_profile=hypotheses['scale_basis']=='calibrated_ground_plane',scale_explicitly_unknown=hypotheses['scale_basis']=='unknown',road_unknown=hypotheses['road'] is None,road_uncertainty_known=hypotheses['road'] is not None and hypotheses['road']['uncertainty_m'] is not None,unbounded_unknown=hypotheses['unbounded_unknown'],confidence_note_mentions_placeholder='placeholder' in note.lower(),confidence_note=note,objects=objects))
    objects=[o for r in rows for o in r['objects']]
    counts=dict(responses=len(rows),unique_observation_windows=len({r['sample_id'] for r in rows}),original_contract_20_responses=sum(r['agent_output_contract']=='2.0.0' for r in rows),separate_contract_21_responses=sum(r['agent_output_contract']=='2.1.0' for r in rows),short_planning_validity_responses=sum(not r['decision_covers_horizon'] for r in rows),target_region_excludes_initial_center_responses=sum(not r['target_contains_initial_center'] for r in rows),target_region_empty_after_current_t2_erosion_responses=sum(r['target_after_current_t2_erosion_empty'] for r in rows),unsupported_calibrated_plane_label_responses=sum(r['scale_label_unsupported_by_current_profile'] for r in rows),explicit_unknown_scale_responses=sum(r['scale_explicitly_unknown'] for r in rows),road_unknown_responses=sum(r['road_unknown'] for r in rows),unbounded_unknown_responses=sum(r['unbounded_unknown'] for r in rows),object_entries=len(objects),nonnull_occupancy_entries=sum(o['occupancy_state']!='unknown' for o in objects),unknown_occupancy_entries=sum(o['occupancy_state']=='unknown' for o in objects),wrong_grid_nonnull_occupancy_entries=sum(o['occupancy_state'] not in ('complete_grid','unknown') for o in objects),complete_grid_occupancy_entries=sum(o['occupancy_state']=='complete_grid' for o in objects),unknown_uncertainty_entries=sum(not o['uncertainty_known'] for o in objects),known_position_entries=sum(o['position_known'] for o in objects),known_velocity_entries=sum(o['velocity_known'] for o in objects),response_notes_containing_placeholder_literal=sum(r['confidence_note_mentions_placeholder'] for r in rows))
    report=dict(status='READ_ONLY_CONTRACT_AUDIT_NO_PASS_CLAIM',scope='All completed image-scene outputs listed in the archived response index; failed/time-out requests have no output to audit and are not removed from the original experiment.',counts=counts,constraint_policy=dict(profile='convex_disk_bicycle_v2',target_region_check='Baseline target half planes offset inward by the recorded capture.vehicle.center_to_rear_axle_m; no road or obstacle geometry is inferred',scope='This audit records the archived baseline constraint rule, not a claim about all future corridor profiles'),interpretation=['These response/object counts include repeated observations and feedback revisions, not independent scenes.','The original 2.0 experiment and the single 2.1 night format probe remain separate protocols.','Wrong validity/grid/target semantics and unsupported label selection are engineering interface issues. Fixing them does not supply missing metric evidence or justify a future occupancy envelope.','The target-erosion diagnostic reproduces archived baseline T2 target half-plane offsets; it is not a new road/actor hypothesis or a claim that no physical solution exists.','A non-null occupancy is not endorsed by this audit; the original model explicitly disclaimed some as placeholders. Unknown coverage and uncertainty still require rejection.','No GT, map, future dataset state, model call or tool execution is used by this audit.'],rows=rows,input_hashes=sources)
    out_dir.mkdir(parents=True,exist_ok=True)
    (out_dir/'contract_audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    lines=['# 真实模型输出的只读流程契约审计','','**工程接口存在可修复问题；修复不等于米制 grounding 或未来覆盖已成立。** 本审计没有调用模型、四工具或读取 GT/地图。原数据不修改。','','按归档响应逐份检查；重复观测和反馈修订不当作独立场景，原 2.0 与独立 2.1 格式复核不合并成同一协议成功率。超时请求没有输出，仍保留在原始实验失败账本，不从总体分母消失。','','| 项目 | 实际数量 |','|---|---:|']
    labels=[('有效图像响应 / 不同观测窗口',f"{counts['responses']} / {counts['unique_observation_windows']}"),('原 2.0 / 独立 2.1 响应',f"{counts['original_contract_20_responses']} / {counts['separate_contract_21_responses']}"),('决策有效期短于 5s 规划时域',counts['short_planning_validity_responses']),('目标区域排除初始中心',counts['target_region_excludes_initial_center_responses']),('按原 v2 T2 内缩后目标区域为空',counts['target_region_empty_after_current_t2_erosion_responses']),('当前剖面拒绝的 calibrated_ground_plane 标签',counts['unsupported_calibrated_plane_label_responses']),('明确 scale=unknown',counts['explicit_unknown_scale_responses']),('目标条目（含重复）',counts['object_entries']),('非空占用 / 网格错误',f"{counts['nonnull_occupancy_entries']} / {counts['wrong_grid_nonnull_occupancy_entries']}"),('明确未知占用 / 完整网格占用',f"{counts['unknown_occupancy_entries']} / {counts['complete_grid_occupancy_entries']}"),('未知数值不确定范围',counts['unknown_uncertainty_entries']),('unbounded_unknown=true 响应',counts['unbounded_unknown_responses'])]
    lines += [f'| {label} | {value} |' for label,value in labels]
    lines += ['','## 可修复的流程问题','','1. `target_region` 被输出成小停车框或仅前方的终点框；工具实际把它施加于整个时域，且按中心偏移保守内缩。新提示词必须解释全时域允许 CENTER 区域，而非要求模型凭空扩大可行道路。','2. 规划有效期与 0.2s 执行前缀混淆；提示词须明确复制采集元数据的规划有效截止，不意味着旧核验可跨新观测沿用。','3. 原非空 occupancy 全部只有一段；T1 要求每个网格整区间一段。必须提供明确网格要求，或诚实返回 null；不能把未获支持的占位矩形切成 25 段就当预测成功。','4. Schema 允许的 calibrated_ground_plane 标签不等于当前 T1 支持该投影契约；新提示词须明确禁止该标签。只有原本有依据的条件米制假设才可用 model_metric_hypothesis，不能靠换标签修复缺尺度。','5. 仅返回第一个 UNBOUNDED_UNKNOWN 会遮住同时存在的有效期、目标区和网格错误。可聚合纯契约诊断，仍不得修补环境事实或静默放松硬约束。','','## 逐响应摘要','','| 来源 | 协议 | 有效期(s) | 目标内缩空 | 尺度标签 | 占用段数 / 目标 |','|---|---|---:|---|---|---|']
    for r in rows:
        cells=','.join('null' if o['supplied_intervals'] is None else str(o['supplied_intervals']) for o in r['objects'])
        lines.append(f"| `{r['record_id']}` | {r['agent_output_contract']} | {r['decision_validity_relative_s']:.3f} | {r['target_after_current_t2_erosion_empty']} | {r['scale_basis']} | {cells} |")
    lines += ['','完整字段、原 confidence_note、来源 SHA256 见 [contract_audit.json](contract_audit.json)。`placeholder` 文本计数只是原文是否含该字面词，不是自动判定支持程度的分类器。占用可支持性、道路未知与未来侵入仍须独立面对；本审计不作通过结论。','']
    (out_dir/'contract_audit.md').write_text('\n'.join(lines))
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--results-root',type=Path,default=ROOT/'implementation/results/gpt61_live');p.add_argument('--out-dir',type=Path,default=ROOT/'implementation/results/process_recovery');a=p.parse_args();r=audit(a.results_root,a.out_dir);print(json.dumps(r['counts'],ensure_ascii=False))
