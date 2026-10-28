"""One-off offline evaluation of an exp2 pilot with no native response.

No model adapters/runtime tools/network imports. Outputs go to a fresh sibling
directory; sealed runtime and pre-run GT/definitions are read and hashed only.
"""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path('/data2/hzy/MAP/agentic-driving')
sys.path.insert(0,str(ROOT/'implementation'))
from agentic.contracts import need
from agentic.evidence_io import reserve_directory,write_json_new,write_text_new,open_new

TRIAL=ROOT/'implementation/results/exp2_single_day_pilot_20261028_v1'
RUNTIME=TRIAL/'runtime'
PRE=TRIAL/'evaluation_preregister'
OUT=TRIAL/'evaluation'

def read(p):return json.loads(Path(p).read_text())
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def relative(p):return str(Path(p).relative_to(ROOT)) if Path(p).is_relative_to(ROOT) else str(p)

seal_path=RUNTIME/'SHA256_RUNTIME_MANIFEST.json'
seal=read(seal_path)
need(seal.get('runtime_sealed') is True,'UNSEALED_RUNTIME','Refuse independent evaluation before runtime seal')
runtime_checks=[]
for name,expected in seal['files'].items():
    path=(RUNTIME/name).resolve()
    need(path.is_relative_to(RUNTIME.resolve()) and path.is_file(),'INVALID_RUNTIME_MEMBER','Manifest member escaped runtime')
    actual=dict(sha256=sha(path),bytes=path.stat().st_size)
    need(actual==expected,'RUNTIME_SEAL_MISMATCH','Original runtime bytes changed',path=name)
    runtime_checks.append(dict(path=name,expected=expected,actual=actual,unchanged=True))
actual_files={str(p.relative_to(RUNTIME)) for p in RUNTIME.rglob('*') if p.is_file()}
unlisted_files=sorted(actual_files-(set(seal['files'])|{seal_path.name}))
need(all(name.startswith('transport/.private_state/') or name.startswith('transport/attempt_01/.private/')
         for name in unlisted_files),'RUNTIME_FILE_SET','Unexpected public files are absent from the completed seal')
need(set(seal['files'])|{seal_path.name}<=actual_files,'RUNTIME_FILE_SET','A sealed runtime member is missing')

registration=read(TRIAL/'frozen_registration.json')
plan=read(PRE/'preregistration.json')
truth=read(PRE/'truth.json')
need(sha(PRE/'preregistration.json')==registration['g2_preregistration_sha256'],
     'G2_PLAN_CHANGED','Preregistered definitions changed after frozen registration')
pre_checks=[]
for path in sorted(PRE.glob('*.json')):
    blob=subprocess.check_output(['git','show',seal['source_commit']+':'+str(path.relative_to(ROOT))],cwd=ROOT)
    frozen_sha=hashlib.sha256(blob).hexdigest()
    need(sha(path)==frozen_sha,'G2_PREREG_CHANGED','Pre-run GT or definitions changed',path=str(path))
    pre_checks.append(dict(path=relative(path),frozen_source_commit=seal['source_commit'],
                           frozen_git_blob_sha256=frozen_sha,current_sha256=sha(path),unchanged=True))

summary=read(RUNTIME/'summary.json')
ledger=read(RUNTIME/'attempt_01/stage_ledger.json')
budget=read(RUNTIME/'budget/final.json')
events=[json.loads(x) for x in (RUNTIME/'transport/attempt_01/events.redacted.jsonl').read_text().splitlines() if x.strip()]
native_types=[]
for event in events:
    if event.get('method') in ('item/started','item/completed'):
        kind=event.get('params',{}).get('item',{}).get('type')
        native_types.append(kind)
need(set(native_types)<={'userMessage'},'NATIVE_CONTENT_PRESENT','Unexpected non-user content requires normal independent review, not no-output evaluation')
need(not list(RUNTIME.rglob('record.json')),'NATIVE_RECORD_PRESENT','Use ordinary frozen-identity G2 for actual model records')
need(summary['unique_windows']==1 and summary['sample_id']==plan['sample_id'],
     'WINDOW_MISMATCH','Do not replace the preregistered unique window')
need(summary['budget']['charged']==1 and summary['budget']['turn_start_requests']==1,
     'BUDGET_MISMATCH','This no-output report is specific to the one charged service attempt')
need(len(summary['attempts'])==2 and summary['attempts'][1]['status']=='NOT_ATTEMPTED',
     'ATTEMPT_MISMATCH','Keep the unused revision slot separate')
for stage in ('NATIVE_OUTPUT','T1','T2','T3','T4','ACCEPTOR','EXECUTION'):
    need(ledger['stages'][stage]['status']=='NOT_REACHED','STAGE_MISMATCH','Unreached stages cannot be passed',stage=stage)
failure=read(RUNTIME/'attempt_01/failure.json')
need(failure['code']=='CODEX_SERVICE_FAILURE','FAILURE_MISMATCH','Do not reclassify this trial as a T1 refusal')

inputs=[seal_path,TRIAL/'frozen_registration.json',*sorted(PRE.glob('*.json')),
        *[RUNTIME/x for x in seal['files']],
        ROOT/'implementation/agentic/g2_metric.py',ROOT/'implementation/experiments/pilot_g2.py']
input_hashes={relative(p):dict(sha256=sha(p),bytes=p.stat().st_size) for p in inputs}
before_hashes={str(p):sha(p) for p in inputs}
out=reserve_directory(OUT,readonly_inputs=[RUNTIME,PRE])
source_bytes=Path(__file__).read_bytes()
with open_new(out/'analysis_source.py','xb') as stream:stream.write(source_bytes)

reason='NO_NATIVE_MODEL_OUTPUT_AFTER_SERVICE_INTERFACE_FAILURE'
null_metric=dict(value=None,status='NOT_EVALUABLE',reason=reason,evaluable_count=0)
report=dict(
    evaluation_version='controlled_exp2_no_native_output_offline_v1',
    experiment_id=summary['experiment_id'],sample_id=plan['sample_id'],original_cohort='original_four',
    status='NO_NATIVE_OUTPUT_OFFLINE_EVALUATION_NOT_G2_PASS',
    unique_windows=1,attempted_response_slots=1,native_output_slots=0,
    optional_revision_slot=dict(attempt=2,status='NOT_ATTEMPTED',reason='Stopped after service interface incompatibility; no replacement call or transport retry'),
    attempted_slot_rows=[dict(attempt=1,revision=0,service_status='FAIL',
        service_error_notification_observed=True,model_service_returned='UNKNOWN',
        native_output_present=False,structured_output_status='NOT_REACHED',
        failure_code=failure['code'],underlying_diagnostic_code='invalid_json_schema',
        underlying_param='text.format.schema',
        diagnosis='Schema interface incompatibility was reported before any native model text. Error origin and model inference execution are not independently established. This is not evidence that the model lacked necessary metric information.',
        stages={name:ledger['stages'][name]['status'] for name in ('NATIVE_OUTPUT','T1','T2','T3','T4','ACCEPTOR','EXECUTION')})],
    budget=dict(limit=2,charged=1,turn_start_requests=1,unused=1,automatic_transport_retries=0,semantic_revisions=0,ended=True),
    fixed_focal=dict(gt_instance=plan['fixed_focal_instance'],attempted_slots=1,
        native_positions_present=0,evaluable_slots=0,unevaluated_slots=1,
        unevaluated_rate_among_attempted_slots=1.,status='1_OF_1_ATTEMPTED_FOCAL_SLOTS_UNEVALUATED',
        position_error_m=None,reason=reason,not_zero_error=True,
        unattempted_optional_revision_excluded=True),
    declared_actor_availability=dict(declared_actor_rows=0,denominator_status='NO_NATIVE_OUTPUT_NO_DECLARED_ACTOR_DENOMINATOR',
        position_available=0,position_missing=None,position_missing_rate=None,
        velocity_available=0,velocity_missing=None,velocity_missing_rate=None,
        future_occupancy_available=0,future_occupancy_missing=None,future_occupancy_missing_rate=None,
        identity_reviewed_rows=0,unmatched=None,unmatched_rate=None,
        comparable_rows=0,noncomparable_rate=None,
        reason='No actor declarations exist. Undefined actor-level rates are null; do not treat no output as zero error, zero missingness or detection recall.'),
    position_error_m=dict(null_metric),velocity_vector_error_mps=dict(null_metric),
    road_geometry_error=dict(null_metric),future_coverage=dict(null_metric),future_area_inflation=dict(null_metric),
    planning_usability=dict(status='NOT_REACHED',value=None,T1='NOT_REACHED',T2='NOT_REACHED',
        T3='NOT_REACHED',T4='NOT_REACHED',independent_acceptor='NOT_REACHED',
        simulated_execution='NOT_REACHED',released_prefix_s=None,consumed_prefix=False,
        prefix_classification='NOT_REACHED',reason=reason),
    truth_reference=dict(available_independent_gt_actor_rows=len(truth['samples'][plan['sample_id']]['objects']),
        definitions=plan['measurement_semantics'],evaluation_domain=plan['evaluation_domain'],
        reference_time_id=plan['reference_time_id'],reference_time_s=plan['reference_time_s'],
        ego_center_to_rear_axle_m=plan['ego_center_to_rear_axle_m'],
        position_reference='Annotation-center linear interpolation, not exact observed instantaneous truth',
        velocity_reference='Annotation displacement slope proxy; instantaneous-velocity accuracy remains unscored',
        future_reference='Annotation-linear circumscribed BODY surrogate; no actual continuous-time truth guarantee',
        actor_rows_used_for_scores=0,passed_to_runtime=False),
    identity_review=dict(status='NOT_REACHED_NO_ACTOR_OUTPUT',new_associations_created=0,
        historical_exposure_disclosed=True,claim_of_full_blindness=False,
        fixed_focal_replaced=False,numerical_error_based_matching=False),
    model_identity=dict(requested_model='gpt-6.1-sol',cli_resolved_alias='gpt-6.1-sol',
        server_reported_snapshot=None,snapshot_identity='NOT_EXPOSED'),
    service_software_vs_model_information=dict(classification='SERVICE_INTERFACE_INCOMPATIBILITY',
        observed='invalid_json_schema: schema must have a type key at hypotheses.coordinate_frame',
        missing_model_metric_information='NOT_ASSESSABLE_NO_NATIVE_RESPONSE',
        six_window_readiness='NOT_ESTABLISHED_SERVICE_SCHEMA_COMPATIBILITY_BLOCKED'),
    thresholds_applied=False,g2_pass_claim=False,online_success_claims=0,
    real_model_calls_this_evaluation=0,runtime_truth_writes=0,
    repeated_revisions_are_independent_windows=False,future_six_window_batch_launched=False,
    strict_g2_numeric_entry_invoked=False,
    strict_g2_numeric_entry_reason='The strict evaluator requires at least one original model record. No fixture, converted response or invented empty actor response was substituted.',
    limitations=[
        'This is one historically exposed unique window and one charged invocation, not a success-rate estimate.',
        'A schema error notification does not establish that multimodal model inference ran or identify an unexposed service snapshot.',
        'No native output means neither model measurement accuracy nor model information sufficiency can be assessed.',
        'All model-dependent scores and actor-level missing/unmatched rates are undefined, not zero.',
        'The unused revision slot is not an additional failed response and its unused budget is not transferred.',
        'GT and labels were read only after runtime seal; neither they nor this evaluation were fed back to runtime.'
    ])
write_json_new(out/'report.json',report)
write_json_new(out/'integrity_checks.json',dict(
    runtime_seal_sha256=sha(seal_path),runtime_sealed=True,
    runtime_members_verified=len(runtime_checks),runtime_checks=runtime_checks,
    public_runtime_inventory_exact=True,whole_runtime_inventory_sealed=False,
    excluded_private_paths=unlisted_files,excluded_private_contents_read=False,
    private_scope='Public runtime seal excludes explicitly private transport state/raw streams; this audit did not read them and does not claim their byte integrity',
    preregistered_plan_sha256=sha(PRE/'preregistration.json'),
    preregistration_git_blob_checks=pre_checks,
    model_record_files_found=0,original_native_output_files_found=0,
    item_notification_types=dict(Counter(native_types)),original_input_bytes_unchanged=True))
text='''# 单窗口小试的独立离线评价

**没有原生模型输出，计量评价不可进行；这不是零误差，也不是模型米制信息不足的证据。** 固定白天窗口只有 1 个唯一窗口、1 个已尝试响应槽；服务报告 `invalid_json_schema`（`hypotheses.coordinate_frame` 缺少服务要求的 `type`），已扣预算 1/2。没有传输重试或语义修订，第二槽为 `NOT_ATTEMPTED`。

已逐项核对 runtime 公共封存清单的全部 20 个文件，公共清单范围内没有额外文件或缺失。运行目录还保留 25 个未列公共清单的明确私有状态/原始流文件；本评价只登记路径、不读取内容，不宣称全 runtime 字节均已封存。预注册定义、独立 GT、投影索引和桥接表 5 份文件与实际运行源码提交 `1ef04fdda3d64bfe632f14f4fc411aa810646a11` 的 Git blob 完全一致。没有改变原图、窗口、真值、定义或运行记录。

固定焦点仍为 `afd097377c71479ea8349eeae83b8143`，**1/1 已尝试槽未评**；未尝试修订槽不增加分母。不存在 actor 声明，因此位置、速度、占用的 actor 级缺失率及未匹配率均无合法分母，记为 `null`；未伪造空输出或人工目标代替。所有位置/速度/道路误差、未来覆盖与面积膨胀均为 `null`。T1–T4、独立接受器、短前缀释放和模拟执行均 `NOT_REACHED`，不归入移动或静止通过。

独立注释 GT 仍具备 51 个登记 actor，原共同域、身体点、速度含义、采集时刻及 1.2m 偏移保持。位置 GT 是注释中心插值，速度是注释区间斜率代理量，未来包络是注释线性运动与身体外接界代理量；这些定义不被此次无输出结果改变。没有新身份关联，也没有任何数值评分。历史窗口和非盲关联已暴露，不追称全盲。

观察到的阻塞是服务/Schema 接口兼容性。是否执行过实际多模态推理、服务端模型快照及模型能否补齐新契约仍未建立。修复接口后需要新实验组和新授权；不能自动继续第二调用或展开六窗批次。本评价新增模型调用 0，GT 运行时反馈 0，没有 G2 通过或在线成功声明。

机器报告见 `report.json`，逐文件核对见 `integrity_checks.json`，来源与输入 SHA 见 `provenance.json`，本次独立分析源码见 `analysis_source.py`。这是本次无输出结果专用的可复算离线分析；源码不曾在模型调用前运行或影响模型输入。
'''
write_text_new(out/'README.md',text)
after_hashes={str(p):sha(p) for p in inputs}
need(before_hashes==after_hashes,'EVALUATION_INPUT_MUTATION','Offline evaluation changed original sources')
write_json_new(out/'provenance.json',dict(
    generated_at_utc=datetime.now(timezone.utc).isoformat(),
    analysis_source_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
    analysis_source_relationship='Post-run offline analysis source in this output; not part of frozen runtime source before inference',
    analysis_source_sha256=hashlib.sha256(source_bytes).hexdigest(),
    analysis_source_copy_sha256=sha(out/'analysis_source.py'),
    code_dirty=bool(subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True).strip()),
    input_sha256=input_hashes,inputs_unchanged_after_evaluation=True,
    initial_readonly_scope_check='Initial pre-output check found 25 private files excluded from public runtime seal. It stopped before output reservation. Final audit explicitly checks only the public seal scope and records excluded paths without reading private contents.',
    real_model_calls=0,runtime_truth_writes=0,network_requests=0))
files={str(p.relative_to(out)):dict(sha256=sha(p),bytes=p.stat().st_size)
       for p in sorted(out.rglob('*')) if p.is_file()}
write_json_new(out/'SHA256_EVALUATION_MANIFEST.json',dict(
    experiment_id=summary['experiment_id'],evaluation_sealed=True,files=files,
    runtime_seal_sha256=sha(seal_path),no_model_calls=True))
print(json.dumps(dict(output=str(out),runtime_files_verified=len(runtime_checks),
    preregistration_files_verified=len(pre_checks),attempted_slots=1,native_output_slots=0,
    fixed_focal_unevaluated='1/1',real_model_calls=0),ensure_ascii=False))
