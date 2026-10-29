"""Prepare explicit feedback reruns of all sixteen failed long-clip references.

No inference, credential, annotation or map access. Existing sources are read
only; rerun results never overwrite the original phase or its budget history.
"""
import argparse
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'implementation'))
from agentic.contracts import need
from agentic.evidence_io import reserve_directory, write_json_new
from run_local_prototype import body_feedback

CLIPS = ('following_deceleration', 'bus_road', 'parking_bend')
EXPECTED_FAILURES = (7, 8, 1)
REPAIR = ('Repair the supplied object/time/constraint conflict using only the same actual images and trusted capture. '
          'Keep existing actor IDs; explain any evidence-supported new, absent or reidentified actor in confidence_note. '
          'Only evidence-supported environment estimates or physically achievable behavior/target/stop revisions are allowed. '
          'target_region is the permitted vehicle CENTER region: you may revise it within the original road to include the '
          'true initial center and an appropriate route, or revise a reasonable speed preference. Do not enlarge road to '
          'repair target_region, and do not delete genuine stop/yield requirements without image-supported justification. '
          'Do not move an obstacle, enlarge the road, shrink residuals, remove an observed threat or discard reasonable adverse '
          'modes merely to pass. A numerical or conservative-planner issue is not permission to change scene facts. '
          'Changes to actor position, dimensions, occupancy or residual need explicit actual-image evidence and a stated basis. '
          'Retain unknown or failure where support is unavailable. Use the CURRENT target capture scene, revision and time IDs; '
          'the previous response is immutable diagnostic context from an earlier trial.')


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def resolve(path):
    path = Path(path)
    return path if path.is_absolute() else ROOT / path


def relative(path):
    path = Path(path).resolve()
    return str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)


def complete(row):
    return bool(row.get('physical', {}).get('independent_acceptor_pass') and row['physical'].get('prefix_consumed'))


def collect(reports, template):
    prepared = []; snapshots = []
    for cid, expected in zip(CLIPS, EXPECTED_FAILURES):
        summary_path = reports / cid / 'summary.json'; summary = read(summary_path)
        need(summary.get('ended') is True and len(summary['frames']) == 19,
             'RERUN_SOURCE_INCOMPLETE', 'All nineteen original reference results must be complete')
        original_path = resolve(summary['source_plan']); original = read(original_path)
        need(sha(original_path) == summary['source_plan_sha256'], 'RERUN_SOURCE_CHANGED', 'Original plan changed')
        by_case = {c['case_id']: c for c in original['cases']}
        failed = [row for row in summary['frames'] if not complete(row)]
        need(len(failed) == expected, 'RERUN_SELECTION', 'The frozen failed set changed', clip_id=cid)
        entries = []
        for row in failed:
            case = deepcopy(by_case[row['case_id']]); capture_path = resolve(case['capture']); capture = read(capture_path)
            need(sha(capture_path) == case['capture_sha256'], 'RERUN_CAPTURE_CHANGED', 'Keep original capture bytes')
            selected = next(a for a in row['overall_attempts']
                            if a['source_group'] == row['source_group'] and a['attempt'] == row['selected_attempt'])
            record_path = resolve(selected['record_path']); record = read(record_path)
            need(sha(record_path) == selected['record_sha256'], 'RERUN_RECORD_CHANGED', 'Previous response record changed')
            scene = capture['metadata']['scene_id']; output = record['output']
            need(record['capture']['metadata']['scene_id'] == scene == output['decision']['scene_id']
                 and record['capture']['metadata']['reference_time_id'] == capture['metadata']['reference_time_id']
                 and record['provenance']['image_sha256'] == case['image_sha256'],
                 'RERUN_WINDOW_MISMATCH', 'Feedback must belong to the exact target scene/time/images')
            native_path = resolve(selected['native_output_path']); text_path = native_path.parent / 'final_agent_message.txt'
            text = text_path.read_text(); need(read(native_path) == output == json.loads(text), 'RERUN_NATIVE_MISMATCH', 'Native sources disagree')
            failure_path = record_path.parent / 'stages.json'; failure = read(failure_path).get('failure')
            need(bool(failure) and failure['code'] == selected['raw_last_failure']['code'],
                 'RERUN_FAILURE_MISMATCH', 'Selected failure must have original stage evidence')
            for image in capture['observations']:
                need(sha(image['path']) == case['image_sha256'][image['id']], 'RERUN_IMAGE_CHANGED', 'Original image changed')
            feedback = dict(failure=failure, previous_native_output=output,
                all_initial_body_envelope_conflicts=body_feedback(output),
                previous_actor_ids=[o['id'] for o in output['hypotheses']['objects']], concrete_repair_request=REPAIR,
                target_scene_id=scene, target_reference_time_id=capture['metadata']['reference_time_id'],
                target_revision=capture['metadata']['revision'], source_revision=record['capture']['metadata']['revision'],
                source_record_path=relative(record_path), source_record_sha256=sha(record_path),
                source_native_output_path=relative(native_path), source_native_output_sha256=sha(native_path),
                source_native_text_path=relative(text_path), source_native_text_sha256=sha(text_path),
                source_failure_stage=row['selected_native_failure_stage'], source_failure_path=relative(failure_path),
                source_failure_sha256=sha(failure_path), source_failure_json_pointer='/failure',
                source_summary_path=relative(summary_path), source_summary_sha256=sha(summary_path),
                source_reference_policy='Source paths and hashes are provenance only, not environment facts or extra sensor inputs.',
                revision_relation='New independent trial uses current original capture revision; old output is not converted or relabelled.')
            case['request_limit'] = 2; entries.append((case, feedback))
        template_path = resolve(next(c['plan'] for c in template['clips'] if c['clip_id'] == cid))
        snapshots.append(dict(clip_id=cid, summary_path=relative(summary_path), summary_sha256=sha(summary_path),
            original_references=19, original_passes=summary['conditional_chain_passes'], failed_case_ids=[r['case_id'] for r in failed],
            charged_requests=summary['charged_requests'], observed_posts=summary['api_post_requests'],
            unknown_dispatch=summary['unknown_dispatch_attempts'], preflight_failures=summary['clip_wide_preflight_failures'],
            source_groups=summary['sources']))
        prepared.append((cid, read(template_path), entries))
    return prepared, snapshots


def prepare(reports, template_path, out):
    template = read(template_path); prepared, snapshots = collect(reports, template)
    root = reserve_directory(out); now = datetime.now(timezone.utc)
    paths = [p for p in template['source_sha256'] if 'summarize_' not in p and 'make_overview' not in p]
    paths.append(relative(__file__))
    pins = {p: sha(resolve(p)) for p in dict.fromkeys(paths)}
    budget = dict(original_phase_ended=True, old_budget_inherited=0, unique_references=57,
        original_passes=sum(c['original_passes'] for c in snapshots), rerun_references=16,
        charged_requests=sum(c['charged_requests'] for c in snapshots), observed_posts=sum(c['observed_posts'] for c in snapshots),
        unknown_dispatch=sum(c['unknown_dispatch'] for c in snapshots), clips=snapshots,
        note='Original phase accounting snapshot; unknown in-flight dispatches and observer closures remain unknown, not returned service failures.')
    write_json_new(root / 'original_phase_budget.json', budget)
    common = deepcopy(template)
    common.update(source_commit=subprocess.check_output(['git','rev-parse','HEAD'], cwd=ROOT, text=True).strip(),
        source_sha256=pins, preparation_started_at_utc=now.isoformat(), deadline_utc=(now+timedelta(hours=4)).isoformat(),
        execution_authorized=True, launched=False, old_budget_inherited=0, per_window_limit=2,
        original_phase_budget_path=relative(root/'original_phase_budget.json'),
        original_phase_budget_sha256=sha(root/'original_phase_budget.json'),
        phase='EXPLICIT_FAILED_REFERENCE_FEEDBACK_RERUN', prior_plan=relative(template_path))
    clips = []
    for cid, old_plan, entries in prepared:
        folder = reserve_directory(root/cid); feedback_dir = reserve_directory(folder/'feedback'); cases = []
        for case, feedback in entries:
            path = feedback_dir/(case['case_id']+'.json'); write_json_new(path, feedback)
            case.update(initial_feedback_path=relative(path), initial_feedback_sha256=sha(path)); cases.append(case)
        plan = deepcopy(old_plan)
        plan.update({k:common[k] for k in ('source_commit','source_sha256','preparation_started_at_utc','deadline_utc','original_phase_budget_path','original_phase_budget_sha256','phase')})
        plan.update(experiment_id=root.name+'_'+cid, cases=cases, frame_count=len(cases),
            operational_call_ceiling=2*len(cases), per_window_limit=2, old_budget_inherited=0,
            launched=False, prior_plan=next(c['plan'] for c in template['clips'] if c['clip_id']==cid))
        write_json_new(folder/'plan.json',plan)
        clips.append(dict(clip_id=cid, scene_name=plan['scene_name'], scene_token=plan['scene_token'],
            plan=relative(folder/'plan.json'), plan_sha256=sha(folder/'plan.json'), reference_count=len(cases),
            raw_frame_count=plan['raw_frame_count'], full_clip_duration_s=plan['full_clip_duration_s'],
            first_planning_time_s=min(c['relative_sequence_time_s'] for c in cases)))
    common.update(experiment_id=root.name, clips=clips, operational_call_ceiling=32,
        planning_reference_count=16, original_reference_count=57, reused_prior_reference_count=41,
        max_parallel_clips=2, serial=False, serial_within_clip=True, status='PREPARED_NOT_LAUNCHED')
    write_json_new(root/'plan.json',common)
    return dict(out=str(root), failed_references=16, call_ceiling=32, old_budget_inherited=0, model_requests=0)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--out',required=True,type=Path)
    p.add_argument('--reports',type=Path,default=ROOT/'implementation/results/long_clips_visual_20261029_v1')
    p.add_argument('--template',type=Path,default=ROOT/'implementation/results/long_clips_20261029_v3/plan.json')
    args=p.parse_args(); print(json.dumps(prepare(args.reports,args.template,args.out),ensure_ascii=False))
