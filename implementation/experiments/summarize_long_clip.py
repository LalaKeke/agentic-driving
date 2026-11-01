"""Read-only full-clip views; sparse inference and raw observations stay distinct.

The source batch must have ended. This module never calls a model, planner or
executor, and never reads annotations/maps. Every original front keyframe gets
a display result; only exact registered planning references get a model plan.
"""
import argparse
from collections import Counter
import csv
import hashlib
import html
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'implementation'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from agentic.evidence_io import reserve_directory, write_json_new, write_text_new
from summarize_bend_sequence import (align_captures, frame_result, transform_points,
                                     signed_turn, resolve)
from summarize_api_sequence import combine
from prototype_figure import body_corners, occupancy_polygons
from bend_result_figure import dense

STAGES = ('SERVICE', 'BUSINESS_SCHEMA', 'T1', 'T2', 'T3', 'T4',
          'TURN_TASK', 'ACCEPTOR', 'EXECUTION')
LIMITATION = ('Recorded observations; every 2s forecast and consumed 0.2s prefix starts '
              'independently. Unqueried frames have NO_NEW_PLAN. No stitched execution, '
              'counterfactual observations, real vehicle control or GT evaluation.')


def read(path, default=None):
    return json.loads(Path(path).read_text()) if Path(path).is_file() else default


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def normalized_result(case, run_root):
    """Use existing evidence parsing, without carrying the turn-only gate."""
    row, result, cap = frame_result(case, run_root)
    required = case.get('require_turn', False)
    row['turn_task_required'] = required
    if not required:
        row['stages']['TURN_TASK'] = 'NOT_APPLICABLE'
        row['task'] = None
    runtime = resolve(row['runtime_path'])
    attempt = row.get('selected_attempt')
    if attempt:
        transport = runtime / 'transport' / f'attempt_{attempt:02d}'
        meta = read(transport / 'attempt_summary.json', {})
        # A final text/JSON can exist before business validation succeeds.
        if not result.get('agent_output'):
            native = read(transport / 'native_output.json')
            if isinstance(native, dict):
                result['agent_output'] = native
                result['provenance'] = read(transport / 'provenance.json', {})
                row['source'] = result['provenance'].get('source', meta.get('source', 'UNKNOWN'))
        if meta.get('model_service_returned') is True:
            row['stages']['SERVICE'] = 'PASS'
        if meta.get('native_json_returned') and row['stages']['BUSINESS_SCHEMA'] == 'FAIL':
            row['final_failure_stage'] = 'BUSINESS_SCHEMA'
    unknown=[]
    for event in row['attempts']:
        transport=event.get('transport_summary') or {}
        if transport.get('status')=='OBSERVER_CLOSED_PROCESS_INTERRUPTION':
            event.update(dispatch_or_response_unknown=True,unknown_dispatch_attempts=1,
                service_or_interface_failure=False,service_error_notification_observed=False,
                service_return='DISPATCH_OR_RESPONSE_UNKNOWN',deepest_blocking_stage='DISPATCH_OR_RESPONSE_UNKNOWN')
            event['raw_stage_ledger']['SERVICE']='UNKNOWN'
            event['issue_classes']=sorted((set(event.get('issue_classes',[]))-{'SERVICE_INTERFACE'})|{'PROCESS_INTERRUPTION_UNKNOWN'})
            unknown.append(dict(attempt=event['attempt'],observation=transport.get('observation'),
                charged=event['charged'],observed_api_post_requests=event['api_post_requests']))
            if event['attempt']==attempt:row['stages']['SERVICE']='UNKNOWN'
    row['unknown_dispatch_attempts']=len(unknown);row['unknown_dispatch_history']=unknown
    if unknown and not any(e['complete_conditional_chain'] for e in row['attempts']):
        row['status']='PROCESS_INTERRUPTED_OUTCOME_UNKNOWN'
        row['final_failure_stage']='DISPATCH_OR_RESPONSE_UNKNOWN'
        row['issue_classes_any_attempt']=sorted(set().union(*(set(e['issue_classes']) for e in row['attempts'])))
    fatal = row.get('fatal_service_or_interface_failure') or {}
    if str(fatal.get('code', '')).startswith('API_PREFLIGHT_'):
        row['final_failure_stage'] = 'MODELS_PREFLIGHT'
    decision = result.get('agent_output', {}).get('decision') or {}
    consumed = row['physical'].get('prefix_consumed', False)
    motion = row['physical'].get('motion_class', 'NOT_EXECUTED')
    stationary = consumed and motion == 'STATIONARY_HOLD'
    has_stop_intent = decision.get('behavior') == 'stop_yield' or decision.get('stop') is not None
    row['motion_review'] = dict(
        moving_prefix_observed=bool(consumed and motion == 'MOVING_PREFIX'),
        stationary_prefix_observed=bool(stationary),
        stationary_has_declared_stop_intent=bool(stationary and has_stop_intent),
        unexplained_hold_review_required=bool(stationary and not has_stop_intent),
        moving_task_completed_by_stationary_prefix=False,
        image_and_intent_review='NOT_INDEPENDENTLY_EVALUATED')
    row['claim'] = LIMITATION
    return row, result, cap


def collect_groups(plan_path, additional_plans=()):
    """Merge ended recovery groups without duplicating prior consumed prefixes.

    A whole-clip preflight failure belongs to its source group, not to every
    reference. A prepared, never-started original clip may be used as the fixed
    input roster when a recovery group is supplied.
    """
    paths=[Path(plan_path).resolve()]+[Path(p).resolve() for p in additional_plans]
    if len(set(paths))!=len(paths):raise ValueError('A source group was listed twice')
    plans=[read(p) for p in paths];base=plans[0];cases=base['cases']
    fixed={c['case_id']:c for c in cases}
    if len(fixed)!=len(cases):raise ValueError('Duplicate fixed reference identifiers')
    names=[p.get('experiment_id',str(path)) for p,path in zip(plans,paths)]
    if len(set(names))!=len(names):raise ValueError('Source group identifiers must be distinct')
    groups=[];sources=[]
    raw_identity=lambda plan:[(r['raw_index'],r['sample_token'],r['frame_id'],r['image_sha256']) for r in plan['raw_frames']]
    for path,plan,name in zip(paths,plans,names):
        root=path.parent;batch=read(root/'batch_summary.json')
        started=any((root/name).exists() for name in ('runtime','transport','budget','config.json','transport_config.json'))
        if batch is None:
            if not additional_plans or started:
                raise ValueError('Every started source clip must have an ended batch summary')
        elif batch.get('ended') is not True:
            raise ValueError('Every started source clip must have ended=true')
        indexed={c['case_id']:c for c in plan['cases']}
        if len(indexed)!=len(plan['cases']) or not set(indexed)<=set(fixed):
            raise ValueError('Recovery changed the fixed reference roster')
        for cid,case in indexed.items():
            if any(case[k]!=fixed[cid][k] for k in ('sample_token','capture_sha256','image_sha256')):
                raise ValueError('Recovery changed fixed capture/image/sample identity')
        if raw_identity(plan)!=raw_identity(base):raise ValueError('Recovery changed the raw clip identity')
        network=read(root/'transport/network_preflight.json',{})
        fatal=(batch or {}).get('fatal') or {}
        preflight_failed=bool(network.get('failure_code')
                              or str(fatal.get('code','')).startswith('API_PREFLIGHT_'))
        sources.append(dict(source_group=name,plan_path=str(path),plan_sha256=sha(path),
            batch_path=str(root/'batch_summary.json') if batch is not None else None,
            batch_sha256=sha(root/'batch_summary.json') if batch is not None else None,
            status='ENDED' if batch is not None else 'PREPARED_NOT_STARTED',
            charged_requests=(batch or {}).get('charged_requests',0),
            api_post_requests=(batch or {}).get('api_post_requests',0),
            unknown_dispatch_attempts=(batch or {}).get('unknown_dispatch_attempts',0),
            observer_closed=(batch or {}).get('observer_closed',False),
            completion_authority=(batch or {}).get('completion_authority'),
            parent_exec_exit_code=(batch or {}).get('parent_exec_exit_code'),
            stop_reason=(batch or {}).get('stop_reason'),fatal=fatal or None,
            clip_wide_preflight_failure=preflight_failed,network_preflight=network or None,
            preflight_attribution='SOURCE_GROUP_ONLY; not repeated on each reference'))
        groups.append((root,indexed,name,batch))
    triples=[]
    for case in cases:
        histories=[];candidates=[]
        for root,indexed,name,_ in groups:
            if case['case_id'] not in indexed:continue
            row,result,cap=normalized_result(indexed[case['case_id']],root)
            histories.append((name,row));candidates.append((row,result,cap,name))
        passed=[v for v in candidates if any(a['complete_conditional_chain'] for a in v[0]['attempts'])]
        attempted=[v for v in candidates if v[0]['attempts']]
        selected,result,cap,name=passed[0] if passed else (attempted[-1] if attempted else candidates[-1])
        if additional_plans:
            row=combine(selected,histories,name)
            row['unknown_dispatch_attempts']=sum(h.get('unknown_dispatch_attempts',0) for _,h in histories)
            row['unknown_dispatch_history']=[dict(item,source_group=group) for group,h in histories for item in h.get('unknown_dispatch_history',[])]
            if row['unknown_dispatch_attempts']:
                row['first_generation_post_pass']=None
                row['overall_request_ordinal_scope']='Charged attempts including unconfirmed dispatch; observed POSTs are counted separately.'
            row['selection_policy']='EARLIEST_EXISTING_PASS' if passed else ('LATEST_ACTUAL_ATTEMPT_GROUP' if attempted else 'NO_ATTEMPT_IN_ANY_GROUP')
            row['source']=selected['source']
        else:row=selected
        latest_name,latest_history=histories[-1]
        row.update(latest_registered_group=latest_name,
            latest_registered_group_status=latest_history['status'],
            latest_registered_group_charged_requests=latest_history['charged_requests'],
            latest_registered_group_api_post_requests=latest_history['api_post_requests'],
            latest_registered_group_chain_pass=any(a['complete_conditional_chain'] for a in latest_history['attempts']))
        triples.append((row,result,cap))
    for key in ('charged_requests','api_post_requests','unknown_dispatch_attempts'):
        if sum(t[0][key] for t in triples)!=sum(source[key] for source in sources):
            raise ValueError('Combined '+key+' differs from source batch ledgers')
    latest=next((batch for _,_,_,batch in reversed(groups) if batch is not None),{})
    return base,latest,triples,sources


def display_transforms(cases, raw_frames, captures):
    """Recorded ego poses are display-only; no future route reaches planning."""
    by_sample = {r['sample_token']: r for r in raw_frames}
    poses = [by_sample[c['sample_token']].get('display_only_ego_pose') for c in cases]
    if all(isinstance(p, dict) and p.get('center_xy_world_m') is not None
           and p.get('yaw_world_rad') is not None for p in poses):
        first = poses[0]
        origin = first['center_xy_world_m']; heading = first['yaw_world_rad']
        cs, sn = math.cos(heading), math.sin(heading)
        transforms = []
        for pose, cap in zip(poses, captures):
            offset = pose.get('center_offset_m')
            if offset is not None and offset != cap['vehicle']['center_to_rear_axle_m']:
                raise ValueError('Display ego-center offset differs from runtime convention')
            dx, dy = [pose['center_xy_world_m'][i] - origin[i] for i in (0, 1)]
            transforms.append(dict(status='ALIGNED', translation_m=[cs*dx+sn*dy, -sn*dx+cs*dy],
                yaw_rad=pose['yaw_world_rad']-heading, display_only=True,
                source='Raw acquisition ego_pose; center offset already applied',
                center_offset_added_again=False))
        return transforms
    return align_captures(captures)


def draw_frame(row, result, cap, target, plt, np, Patch, Image):
    fig, axes = plt.subplots(2, 2, figsize=(15.5, 10))
    front, bev, speed, info = axes.flat
    image = max((o for o in cap['observations'] if o['camera_id'] == 'CAM_FRONT'),
                key=lambda o: o['capture_time_s'])
    with Image.open(image['path']) as original:
        front.imshow(original); width, height = original.size
    front.set_title('Original image; model dots use display-only z=0 projection'); front.axis('off')
    calibration=cap.get('acquisition_metadata',{}).get('calibrations',{}).get('CAM_FRONT')
    def project(point):
        if not calibration or image['time_id']!=cap['metadata']['reference_time_id']:return None
        camera=np.asarray(calibration['camera_to_center_rotation']).T @ (
            np.asarray([*point,0.])-np.asarray(calibration['camera_to_center_translation_m']))
        if camera[2]<=.3:return None
        uv=np.asarray(calibration['camera_intrinsic']) @ camera;uv=uv[:2]/uv[2]
        return uv if 0<=uv[0]<width and 0<=uv[1]<height else None
    native = result.get('agent_output', {}).get('hypotheses') or {}
    native = native if isinstance(native, dict) else {}
    hypothesis = result.get('scene', {}).get('hypotheses', native)
    road = hypothesis.get('road') if isinstance(hypothesis, dict) else None
    if isinstance(road, dict) and road.get('polygon_m'):
        bev.add_patch(Patch(road['polygon_m'], fc='#e8edf1', ec='#64748b', label='Model local road'))
    actors = native.get('objects') or []
    for index, actor in enumerate(actors):
        if not isinstance(actor, dict):
            continue
        color = plt.get_cmap('tab10')(index % 10)
        try:
            seen = set()
            for polygon in occupancy_polygons(actor):
                key = json.dumps(polygon)
                if key not in seen:
                    bev.add_patch(Patch(polygon, fc=color, ec=color, alpha=.12)); seen.add(key)
            body = body_corners(actor)
            if body:
                bev.add_patch(Patch(body, fc=color, ec=color, alpha=.7))
            if actor.get('position_m') is not None:
                x, y = actor['position_m']; bev.scatter(x, y, c=[color], s=20)
                bev.annotate(str(index+1)+' '+actor.get('id', '?'), (x, y), fontsize=7, color=color)
                uv=project([x,y])
                if uv is not None:
                    front.scatter(*uv,c=[color],s=42,edgecolors='white')
                    front.annotate(str(index+1),uv,color='white',fontsize=9,weight='bold')
        except (KeyError, ValueError, TypeError):
            # An invalid native field remains invalid; only its drawing is omitted.
            bev.text(.02, .98-index*.04, 'Invalid native geometry: not drawn',
                     color='#a33', va='top', transform=bev.transAxes, fontsize=7)
    for cell in result.get('corridor', {}).get('cells', []):
        bev.add_patch(Patch(cell['polygon_m'], fc='#60c6d2', ec='#60c6d2', alpha=.07))
    vehicle = cap['vehicle']
    bev.add_patch(Patch([[-vehicle['rear_m'], -vehicle['half_width_m']],
                        [vehicle['front_m'], -vehicle['half_width_m']],
                        [vehicle['front_m'], vehicle['half_width_m']],
                        [-vehicle['rear_m'], vehicle['half_width_m']]], fc='#23334a', label='Initial ego'))
    unreleased = bool(row.get('display_only_unreleased_candidate'))
    if result.get('trajectory'):
        points = np.asarray(dense(result)); color = '#8b5a2b' if unreleased else '#1755cf'
        label = 'Unreleased candidate / not consumed' if unreleased else 'Independent 2s plan'
        bev.plot(points[:, 1], points[:, 2], c=color, lw=2.5, label=label)
        speed.plot(points[:, 0], points[:, 4], c=color, label=label)
        if row['physical'].get('prefix_consumed'):
            prefix = points[:, 0] <= row['physical']['prefix_s']+1e-9
            bev.plot(points[prefix, 1], points[prefix, 2], c='#17a66b', lw=6, label='Consumed 0.2s prefix')
            speed.axvspan(0, row['physical']['prefix_s'], fc='#17a66b', alpha=.2)
    else:
        bev.text(.5, .95, 'NO PLAN / NO PREFIX', ha='center', color='#a33', transform=bev.transAxes)
        speed.text(.5, .5, 'NO TRAJECTORY\nUnknown, not zero', ha='center', transform=speed.transAxes)
    if not native:
        bev.text(.5, .5, 'NO NATIVE SCENE\nUnknown, not empty', ha='center', color='#a33', transform=bev.transAxes)
    bev.set(xlabel='Local X forward (m)', ylabel='Local Y left (m)', title='Native model geometry / corridor / trajectory')
    bev.set_aspect('equal', adjustable='datalim'); bev.autoscale_view(); bev.grid(alpha=.2)
    if bev.get_legend_handles_labels()[0]: bev.legend(fontsize=7)
    speed.set(xlabel='Time from this observation (s)', ylabel='Speed (m/s)', title='This forecast only; long tail is not executed')
    speed.grid(alpha=.2)
    if speed.get_legend_handles_labels()[0]: speed.legend(fontsize=7)
    decision = result.get('agent_output', {}).get('decision') or {}
    m = row['physical']; turn = row['signed_turn']; info.axis('off')
    lines = [f"{row['case_id']} | recorded t=+{row['relative_sequence_time_s']:.3f}s",
             f"Status: {row['status']} | block: {row['final_failure_stage'] or 'NONE'}",
             f"Generating POST attempts: {row.get('api_post_requests',0)} | source: {row['source']}",
             f"Charged attempts: {row['charged_requests']} | dispatch/response unknown: {row.get('unknown_dispatch_attempts',0)}",
             f"Decision: {decision.get('behavior','NOT_REACHED')} | desired v: {decision.get('desired_speed_mps','UNKNOWN')}",
             'Stages: '+' | '.join(f"{s}={row['stages'][s]}" for s in STAGES),
             f"Declared actors: {len(actors) if native else 'UNKNOWN'} | T4: {m.get('T4_verdict','NOT_REACHED')}",
             f"Prefix: {m.get('motion_class','NOT_EXECUTED')} | consumed: {m.get('prefix_consumed',False)}",
             f"Displacement: {m.get('displacement_m')} m | end speed: {m.get('end_speed_mps')} m/s",
             f"Minimum conditional clearance: {m.get('minimum_conditional_clearance_lower_bound_m')} m",
             f"Plan yaw change: {turn['plan_end_yaw_change_deg']} deg | lateral: {turn['plan_end_lateral_m']} m",
             f"Consumed prefix yaw change: {turn['prefix_yaw_change_deg']} deg",
             'Turn amplitude task: '+('required by this reference' if row['turn_task_required'] else 'NOT_APPLICABLE; no forced turn'),
             f"Unexplained stationary hold review required: {row['motion_review']['unexplained_hold_review_required']}",
             'Behavior / road agreement: independent image review pending.',
             'Image markers are model z=0 projections, not detections or GT.',
             'Model uncertainty is uncalibrated; GT not evaluated.',
             'REJECTED CANDIDATE / NOT CONSUMED' if unreleased else '', '', LIMITATION]
    if row.get('source_group'):
        lines.insert(3,'Selected source group: '+row['source_group']+'; all group attempts counted above.')
    if row.get('latest_registered_group'):
        lines.insert(4,'Latest registered source: '+row['latest_registered_group']+' | '+row['latest_registered_group_status']+
            f" | charged={row['latest_registered_group_charged_requests']}, observed POST={row['latest_registered_group_api_post_requests']}")
    info.text(0, 1, '\n'.join(part for line in lines for part in (textwrap.wrap(line, 88) or [''])),
              va='top', fontsize=8)
    fig.suptitle(f"{row['case_id']} | +{row['relative_sequence_time_s']:.2f}s | {row['status']}", fontsize=15)
    fig.tight_layout(rect=(0,.02,1,.95)); fig.savefig(target, dpi=140); plt.close(fig)


def draw_overview(rows, results, transforms, target, plt, np, source_note=''):
    fig, axes = plt.subplots(2, 2, figsize=(16, 10.5)); timeline, bev, speed, yaw = axes.flat
    states = {'NOT_REACHED': 0, 'NOT_APPLICABLE': 1, 'PASS': 2, 'FAIL': 3, 'UNKNOWN': 4}
    from matplotlib.colors import ListedColormap
    values = np.asarray([[states.get(r['stages'][s], 0) for r in rows] for s in STAGES])
    timeline.imshow(values, aspect='auto', interpolation='nearest', vmin=0, vmax=4,
                    cmap=ListedColormap(['#ddd','#a6b8cf','#2b9b65','#ca4b4b','#d6a83b']))
    timeline.set(xticks=range(len(rows)), xticklabels=[f"{r['relative_sequence_time_s']:.1f}" for r in rows],
                 yticks=range(len(STAGES)), yticklabels=STAGES, xlabel='Recorded scene time (s)',
                 title='Selected answer: green PASS / red FAIL / blue N.A. / yellow UNKNOWN / gray not reached')
    colors = plt.get_cmap('viridis'); refs=[]
    for index, (row, result, transform) in enumerate(zip(rows, results, transforms)):
        color = colors(index/max(1,len(rows)-1))
        if transform['status']=='ALIGNED':
            refs.append(transform['translation_m']); bev.scatter(*transform['translation_m'], c=[color], s=15)
        if not result.get('trajectory'): continue
        points=np.asarray(dense(result))
        if transform['status']=='ALIGNED':
            xy=np.asarray(transform_points(points[:,1:3], transform))
            bev.plot(xy[:,0],xy[:,1],c=color,ls='--' if row['display_only_unreleased_candidate'] else '-',alpha=.8)
            if row['physical'].get('prefix_consumed'):
                mask=points[:,0]<=row['physical']['prefix_s']+1e-9
                bev.plot(xy[mask,0],xy[mask,1],c='#17a66b',lw=4)
        speed.plot(points[:,0]+row['relative_sequence_time_s'],points[:,4],c=color,alpha=.8)
        yaw.plot(points[:,0],(points[:,3]-points[0,3])*180/math.pi,c=color,alpha=.8)
    if refs:
        refs=np.asarray(refs);bev.plot(refs[:,0],refs[:,1],'--',c='#777',label='Recorded ego reference, not executed')
    bev.set(xlabel='First planned reference X (m)',ylabel='First planned reference Y (m)',
            title='DISPLAY ONLY alignment; independent forecasts')
    bev.set_aspect('equal',adjustable='datalim');bev.grid(alpha=.2)
    if bev.get_legend_handles_labels()[0]:bev.legend(fontsize=7)
    speed.scatter([r['relative_sequence_time_s'] for r in rows],[r['initial_speed_mps'] for r in rows],
                  c='black',s=12,label='Recorded starting speed')
    speed.set(xlabel='Recorded scene time + forecast time (s)',ylabel='Speed (m/s)',
              title='Overlapping speed forecasts; not one executed speed trace');speed.grid(alpha=.2);speed.legend(fontsize=7)
    yaw.set(xlabel='Time from each observation (s)',ylabel='Signed plan yaw change (deg)',
            title='Descriptive heading, no forced-turn threshold');yaw.grid(alpha=.2)
    count=sum(r['physical'].get('independent_acceptor_pass',False) and r['physical'].get('prefix_consumed',False) for r in rows)
    fig.suptitle(f"Full recorded clip | {len(rows)} planning references | {count} conditional chains | sparse inference",fontsize=16)
    fig.text(.02,.014,LIMITATION+('\n'+source_note if source_note else ''),fontsize=8);fig.tight_layout(rect=(0,.05,1,.95));fig.savefig(target,dpi=145);plt.close(fig)


def raw_display(raw, row, reference_picture, target, Image, ImageOps, ImageDraw, ImageFont):
    """Never reuse a previous plan on a new/unqueried image."""
    canvas=Image.new('RGB',(1280,866),'white');draw=ImageDraw.Draw(canvas)
    font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',22)
    small=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',17)
    title=f"Raw frame {raw['raw_index']:03d} | +{raw['relative_sequence_time_s']:.3f}s | {raw['role']}"
    draw.text((18,10),title,font=font,fill='black')
    if row is not None:
        with Image.open(reference_picture) as picture:
            tile=ImageOps.contain(picture.convert('RGB'),(1280,800));canvas.paste(tile,((1280-tile.width)//2,50))
        draw.text((18,840),'Registered reference: only this image has the displayed native plan/result.',font=small,fill='#223')
    else:
        with Image.open(resolve(raw['image_path'])) as image:
            tile=ImageOps.contain(image.convert('RGB'),(1250,680));canvas.paste(tile,((1280-tile.width)//2,60))
        label='WARMUP: insufficient full 2s history; no model call.' if raw['role']=='WARMUP' else 'OBSERVATION ONLY / NO_NEW_PLAN: no model call at this timestamp.'
        draw.text((18,765),label,font=font,fill='#a33')
        draw.text((18,807),'Previous verification is not reused; geometry and motion plan are unknown for this image.',font=small,fill='#333')
    canvas.save(target,quality=88)
    return canvas


def dense_front_view(index_path,plan,rows,out,Image,ImageOps,ImageDraw,ImageFont,video_only=False):
    """Optional full available CAM_FRONT stream; no stale plans on sweep frames."""
    index=read(index_path);matches=[c for c in index['clips'] if c['scene_token']==plan['scene_token']]
    if len(matches)!=1:raise ValueError('Dense front index does not uniquely match the scene')
    clip=matches[0]
    if clip['base_plan_sha256']!=sha(resolve(clip['base_plan'])):
        raise ValueError('Dense front source plan changed')
    by_case={r['case_id']:r for r in rows};by_capture={c['case_id']:read(resolve(c['capture'])) for c in plan['cases']}
    matched=[]
    for frame in clip['rows']:
        if sha(resolve(frame['path']))!=frame['sha256']:raise ValueError('Dense raw image SHA changed')
        cid=frame.get('planning_case_id')
        if cid is not None:
            if cid not in by_case:raise ValueError('Dense index added an unregistered inference reference')
            cap=by_capture[cid]
            observed=[o for o in cap['observations'] if o['id']==frame['id'] and o['time_id']==cap['metadata']['reference_time_id']]
            if len(observed)!=1:raise ValueError('Dense frame does not exactly match the native planning image')
            matched.append(cid)
    if len(matched)!=len(by_case) or set(matched)!=set(by_case):
        raise ValueError('Dense index must contain every registered reference exactly once')
    folder=None if video_only else reserve_directory(out/'dense_front_frames')
    font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',22)
    small=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',17)
    animation=[];durations=[];timeline=[]
    for number,frame in enumerate(clip['rows']):
        cid=frame.get('planning_case_id');row=by_case.get(cid);posted=bool(row and row['api_post_requests'])
        warm=frame['relative_time_s']<clip['first_planning_reference_s']-1e-8
        role='PLANNING_REFERENCE' if cid else ('WARMUP' if warm else 'OBSERVATION_ONLY')
        canvas=Image.new('RGB',(1280,866),'white');draw=ImageDraw.Draw(canvas)
        draw.text((16,10),f"CAM_FRONT {number:03d} | +{frame['relative_time_s']:.3f}s | {role}",font=font,fill='black')
        with Image.open(resolve(frame['path'])) as original:
            tile=ImageOps.contain(original.convert('RGB'),(620,350));canvas.paste(tile,(16+(620-tile.width)//2,65+(350-tile.height)//2))
        draw.text((16,430),'Original frame; exact acquisition timestamp retained.',font=small,fill='#333')
        if row is not None and posted:
            with Image.open(out/cid/'result.png') as picture:
                w,h=picture.size
                bev=ImageOps.contain(picture.crop((w//2,65,w,h//2)).convert('RGB'),(610,400));canvas.paste(bev,(660,60))
                speed=ImageOps.contain(picture.crop((0,h//2,w//2,h)).convert('RGB'),(620,380));canvas.paste(speed,(16,470))
            details=[cid+' | '+row['status'],'This exact frame only; no carried prior plan.',
                'Stages: '+' / '.join(s+'='+row['stages'][s] for s in ('T1','T2','T3','T4')),
                'Prefix: '+row['physical'].get('motion_class','NOT_EXECUTED'),
                'Distance: '+str(row['physical'].get('displacement_m'))+' m',
                'End speed: '+str(row['physical'].get('end_speed_mps'))+' m/s',
                'All conclusions conditional on model local assumptions.']
            y=485
            for line in details:
                for text in textwrap.wrap(line,59):draw.text((665,y),text,font=small,fill='#223');y+=25
        else:
            uncertain=bool(row and row.get('unknown_dispatch_attempts'))
            label=('REGISTERED REFERENCE / DISPATCH UNKNOWN' if uncertain else 'REGISTERED REFERENCE / NOT_ATTEMPTED') if cid else ('WARMUP / NO_NEW_PLAN' if warm else 'OBSERVATION_ONLY / NO_NEW_PLAN')
            for i,line in enumerate(textwrap.wrap(label,38)):draw.text((665,110+i*35),line,font=font,fill='#a33')
            note=[('A charged attempt exists; dispatch or response is unconfirmed.' if uncertain else 'No generating request at this image timestamp.'),'No previous geometry, verification or execution is reused.','Registered model references are about 1 second apart.','Detailed reference pages and the 2Hz analysis video are separate.']
            y=235
            for line in note:
                for text in textwrap.wrap(line,55):draw.text((665,y),text,font=small,fill='#333');y+=29
        draw.text((16,845),'Recorded camera stream, not observations resulting from simulated controls.',font=small,fill='#333')
        target=folder/f'frame_{number:04d}.jpg' if folder else None
        if target:canvas.save(target,quality=88)
        animation.append(ImageOps.contain(canvas,(960,650)).convert('P',palette=Image.ADAPTIVE,colors=256))
        dt=((clip['rows'][number+1]['timestamp_us']-frame['timestamp_us'])/1e6) if number+1<len(clip['rows']) else .1
        durations.append(max(10,round(dt*1000/10)*10))
        timeline.append(dict(frame_id=frame['id'],timestamp_us=frame['timestamp_us'],relative_time_s=frame['relative_time_s'],
            image_path=frame['path'],image_sha256=frame['sha256'],is_key_frame=frame['is_key_frame'],role=role,
            planning_case_id=cid,generating_post_attempted_at_this_frame=posted,
            dispatch_unknown_attempts=row.get('unknown_dispatch_attempts',0) if row else 0,
            result_status=row['status'] if row else 'NO_NEW_PLAN',prior_verification_reused=False,
            display_image=str(target.relative_to(out)) if target else None))
    video=dict(status='NOT_AVAILABLE');encoder=shutil.which('ffmpeg')
    scratch=tempfile.TemporaryDirectory(prefix='dense-front-encode-') if video_only else None
    gif_path=Path(scratch.name)/'dense_front.gif' if scratch else out/'dense_front.gif'
    try:
        animation[0].save(gif_path,save_all=True,append_images=animation[1:],duration=durations,loop=0,optimize=False)
        if encoder:
            process=subprocess.run([encoder,'-nostdin','-hide_banner','-loglevel','error','-n','-i',str(gif_path),
                '-vsync','vfr','-pix_fmt','yuv420p','-c:v','libx264','-crf','23','-threads','2','-video_track_timescale','10000',
                '-movflags','+faststart',str(out/'dense_front.mp4')],capture_output=True,text=True)
            video.update(status='PASS' if process.returncode==0 else 'ENCODE_FAILED',exit_code=process.returncode,
                         error=process.stderr[:1000] if process.returncode else None)
            probe=shutil.which('ffprobe')
            if process.returncode==0 and probe:
                inspected=subprocess.run([probe,'-v','error','-select_streams','v:0','-show_entries','stream=nb_frames,avg_frame_rate,duration','-of','json',str(out/'dense_front.mp4')],capture_output=True,text=True)
                if inspected.returncode==0:video['stream']=json.loads(inspected.stdout)['streams'][0]
    finally:
        if scratch:scratch.cleanup()
    write_json_new(out/'dense_front_timeline.json',timeline)
    with (out/'dense_front_timeline.csv').open('x',newline='') as stream:
        keys=('frame_id','timestamp_us','relative_time_s','image_path','image_sha256','is_key_frame','role','planning_case_id','generating_post_attempted_at_this_frame','dispatch_unknown_attempts','result_status','prior_verification_reused','display_image')
        writer=csv.DictWriter(stream,fieldnames=keys);writer.writeheader();writer.writerows({k:r.get(k) for k in keys} for r in timeline)
    return dict(index_path=str(index_path),index_sha256=sha(index_path),frame_count=len(timeline),
        keyframe_count=clip['keyframe_count'],sweep_count=clip['sweep_count'],source_mean_acquisition_rate_hz=clip['mean_acquisition_rate_hz'],
        source_timestamp_interval_s=clip['interval_s'],planning_references=len(matched),generating_reference_images=sum(r['generating_post_attempted_at_this_frame'] for r in timeline),
        new_model_requests=0,mp4_encoding=video,gif_duration_resolution_ms=10,last_frame_hold_ms=100,
        dense_video_only=video_only,gif_retained=not video_only,dense_frame_jpegs_retained=not video_only,
        claim='All available CAM_FRONT images in the fixed scene time domain; extra display frames never enter runtime or inherit prior plans.')


def main(plan_path,out,additional_plans=(),dense_front_index=None,dense_video_only=False):
    if dense_video_only and not dense_front_index:raise ValueError('Dense-video-only needs a display index')
    plan_path=plan_path.resolve();plan,batch,triples,sources=collect_groups(plan_path,additional_plans)
    cases=plan['cases'];raw_frames=plan['raw_frames']
    if not cases or not raw_frames:raise ValueError('Clip needs raw frames and registered planning references')
    for raw in raw_frames:
        if sha(resolve(raw['image_path']))!=raw['image_sha256']:raise ValueError('Raw image SHA changed')
    rows=[x[0] for x in triples];results=[x[1] for x in triples];captures=[x[2] for x in triples]
    transforms=display_transforms(cases,raw_frames,captures)
    for row,transform in zip(rows,transforms):row['display_transform_to_first_reference']=transform
    out=reserve_directory(out)
    with tempfile.TemporaryDirectory(prefix='long-clip-mpl-') as cache:
        os.environ['MPLCONFIGDIR']=cache
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import numpy as np
        from matplotlib.patches import Polygon as Patch
        from PIL import Image,ImageOps,ImageDraw,ImageFont
        pages=[]
        for row,result,cap in triples:
            folder=reserve_directory(out/row['case_id']);write_json_new(folder/'result.json',row)
            draw_frame(row,result,cap,folder/'result.png',plt,np,Patch,Image)
            with Image.open(folder/'result.png') as picture:
                pages.append(ImageOps.pad(picture.convert('RGB'),(1200,775),color='white'))
        draw_overview(rows,results,transforms,out/'clip_overview.png',plt,np,
            source_note=f"Source groups: {len(sources)}; clip-wide preflight failures: {sum(s['clip_wide_preflight_failure'] for s in sources)} (counted once, not once per reference).")
        raw_out=reserve_directory(out/'raw_frames');by_case={r['case_id']:r for r in rows};raw_rows=[];animation=[];durations=[]
        for index,raw in enumerate(raw_frames):
            case_id=raw.get('planning_case_id');row=by_case.get(case_id)
            if raw['role']=='PLANNING_REFERENCE' and row is None:raise ValueError('Raw reference lacks registered case')
            if row is not None and raw['sample_token']!=row['sample_token']:raise ValueError('Raw/model reference mismatch')
            target=raw_out/f"frame_{raw['raw_index']:03d}.jpg"
            picture=out/row['case_id']/'result.png' if row else None
            animation.append(raw_display(raw,row,picture,target,Image,ImageOps,ImageDraw,ImageFont))
            dt=(raw_frames[index+1]['reference_time_s']-raw['reference_time_s']) if index+1<len(raw_frames) else .5
            durations.append(max(20,round(dt*1000)))
            called=True if row and row['api_post_requests'] else (None if row and row.get('unknown_dispatch_attempts') else False)
            raw_rows.append(dict(raw,model_called_at_this_frame=called,
                generating_post_attempts=row['api_post_requests'] if row else 0,
                unknown_dispatch_attempts=row.get('unknown_dispatch_attempts',0) if row else 0,
                planned_reference_result=row['case_id']+'/result.json' if row else None,
                result_status=row['status'] if row else 'NO_NEW_PLAN',
                prior_verification_reused=False,display_image=str(target.relative_to(out))))
        animation[0].save(out/'full_clip.gif',save_all=True,append_images=animation[1:],duration=durations,loop=0,optimize=False)
        encoder=shutil.which('ffmpeg');video=dict(status='NOT_AVAILABLE',same_frames_as_gif=True)
        if encoder:
            process=subprocess.run([encoder,'-nostdin','-hide_banner','-loglevel','error','-n',
                '-i',str(out/'full_clip.gif'),'-vf','fps=12,format=yuv420p','-c:v','libx264',
                '-crf','23','-threads','2','-movflags','+faststart',str(out/'full_clip.mp4')],capture_output=True,text=True)
            video.update(status='PASS' if process.returncode==0 else 'ENCODE_FAILED',exit_code=process.returncode,
                         error=process.stderr[:1000] if process.returncode else None)
        with Image.open(out/'clip_overview.png') as overview:
            overview.convert('RGB').save(out/'casebook.pdf',save_all=True,append_images=pages,resolution=110.)
        sheet=Image.new('RGB',(2400,517*math.ceil(len(pages)/3)),'white')
        for index,page in enumerate(pages):sheet.paste(ImageOps.contain(page,(800,517)),((index%3)*800,(index//3)*517))
        sheet.save(out/'contactsheet.jpg',quality=88)
        dense_display=dense_front_view(dense_front_index,plan,rows,out,Image,ImageOps,ImageDraw,ImageFont,video_only=dense_video_only) if dense_front_index else None
    motion=Counter(r['physical'].get('motion_class','NOT_EXECUTED') for r in rows)
    summary=dict(ended=True,experiment_id=plan.get('experiment_id'),scene_name=plan.get('scene_name',cases[0]['scene_name']),
        unique_scenes=1,raw_frames=len(raw_frames),raw_clip_span_s=raw_frames[-1]['reference_time_s']-raw_frames[0]['reference_time_s'],
        registered_planning_references=len(rows),attempted_references=sum(r['charged_requests']>0 for r in rows),
        charged_requests=sum(r['charged_requests'] for r in rows),api_post_requests=sum(r['api_post_requests'] for r in rows),
        unknown_dispatch_attempts=sum(r.get('unknown_dispatch_attempts',0) for r in rows),
        observed_post_references=sum(r['api_post_requests']>0 for r in rows),
        native_json_count=sum(r['native_json_count'] for r in rows),
        conditional_chain_passes=sum(r['physical'].get('independent_acceptor_pass',False) and r['physical'].get('prefix_consumed',False) for r in rows),
        motion_classes=dict(motion),unexplained_hold_review_required=sum(r['motion_review']['unexplained_hold_review_required'] for r in rows),
        outcome_distribution=dict(Counter(r['status'] for r in rows)),stop_reason=batch.get('stop_reason'),fatal=batch.get('fatal'),
        source_plan=str(plan_path),source_plan_sha256=sha(plan_path),source_batch_sha256=sources[0]['batch_sha256'],
        sources=sources,clip_wide_preflight_failures=sum(s['clip_wide_preflight_failure'] for s in sources),
        preflight_failure_attribution='Clip-wide preflight failures are counted once per source, not as one failure per reference.',
        analysis_script_sha256=sha(__file__),new_model_requests=0,GT_used=False,claim=LIMITATION,frames=rows)
    summary['mp4_encoding']=video
    summary['dense_front_display']=dense_display
    write_json_new(out/'summary.json',summary);write_json_new(out/'raw_timeline.json',raw_rows)
    with (out/'source_groups.csv').open('x',newline='') as stream:
        keys=('source_group','status','charged_requests','api_post_requests','unknown_dispatch_attempts','clip_wide_preflight_failure','observer_closed','completion_authority','parent_exec_exit_code','stop_reason','fatal_code')
        writer=csv.DictWriter(stream,fieldnames=keys);writer.writeheader()
        for source in sources:
            writer.writerow(dict({k:source.get(k) for k in keys},fatal_code=(source.get('fatal') or {}).get('code')))
    columns=('case_id','sample_token','relative_sequence_time_s','status','charged_requests','api_post_requests','final_failure_stage',
             'latest_registered_group','latest_registered_group_status','latest_registered_group_charged_requests','latest_registered_group_api_post_requests')
    with (out/'planning_results.csv').open('x',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=columns+('motion_class','prefix_consumed','displacement_m','end_speed_mps','minimum_conditional_clearance_lower_bound_m','plan_yaw_deg','prefix_yaw_deg','unexplained_hold_review_required'))
        writer.writeheader()
        for row in rows:
            item={k:row.get(k) for k in columns};item.update({k:row['physical'].get(k) for k in ('motion_class','prefix_consumed','displacement_m','end_speed_mps','minimum_conditional_clearance_lower_bound_m')})
            item.update(plan_yaw_deg=row['signed_turn']['plan_end_yaw_change_deg'],prefix_yaw_deg=row['signed_turn']['prefix_yaw_change_deg'],unexplained_hold_review_required=row['motion_review']['unexplained_hold_review_required']);writer.writerow(item)
    with (out/'raw_timeline.csv').open('x',newline='') as stream:
        keys=('raw_index','sample_token','relative_sequence_time_s','role','planning_case_id','model_called_at_this_frame','generating_post_attempts','unknown_dispatch_attempts','result_status','prior_verification_reused')
        writer=csv.DictWriter(stream,fieldnames=keys);writer.writeheader();writer.writerows({k:r.get(k) for k in keys} for r in raw_rows)
    cards=''.join(f'<section><h2>+{r["relative_sequence_time_s"]:.2f}s {html.escape(r["case_id"])} · {html.escape(r["status"])}</h2><a href="{r["case_id"]}/result.json">Result JSON</a><img src="{r["case_id"]}/result.png"></section>' for r in rows)
    document=('<!doctype html><meta charset="utf-8"><title>Full recorded clip</title><style>body{font:16px sans-serif;max-width:1400px;margin:auto;padding:20px}img{max-width:100%}section{margin:30px 0}</style>'
        '<h1>'+html.escape(summary['scene_name'])+' · full recorded clip</h1><p>'+html.escape(LIMITATION)+'</p>'
        f'<p>Raw frames {len(raw_frames)}; planned references {len(rows)}; conditional chains {summary["conditional_chain_passes"]}. Warmup and observation-only frames have no new plan. Earlier/current clip-wide preflight failures: {summary["clip_wide_preflight_failures"]} (counted once per source group).</p>'
        '<p><a href="full_clip.gif">Full raw-timeline animation</a> · '+('<a href="full_clip.mp4">MP4</a> · ' if video['status']=='PASS' else '')+'<a href="casebook.pdf">PDF</a> · <a href="planning_results.csv">Planning CSV</a> · <a href="raw_timeline.csv">All raw frames</a> · <a href="source_groups.csv">Source groups and preflight history</a> · <a href="summary.json">Full JSON</a></p>'
        +('<p>Dense CAM_FRONT display: <a href="dense_front.mp4">MP4</a> · '+('<a href="dense_front.gif">GIF</a> · ' if dense_display['gif_retained'] else '')+'<a href="dense_front_timeline.csv">Every available frame</a>. Display only; no extra model calls.</p>' if dense_display else '')
        +'<img src="clip_overview.png">'+cards)
    write_text_new(out/'index.html',document)
    print(json.dumps(dict(out=str(out),raw_frames=len(raw_frames),planning_references=len(rows),conditional_chains=summary['conditional_chain_passes'],new_model_requests=0)))
    return summary


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--plan',required=True,type=Path);parser.add_argument('--out',required=True,type=Path)
    parser.add_argument('--additional-plan',action='append',type=Path,default=[],help='Ended recovery source; keep existing passes and include all attempts')
    parser.add_argument('--dense-front-index',type=Path,help='Optional display-only full CAM_FRONT index; never supplied to runtime')
    parser.add_argument('--dense-video-only',action='store_true',help='Retain dense MP4/timeline only; delete private temporary GIF and do not save duplicate dense JPEGs')
    args=parser.parse_args();main(args.plan,args.out,args.additional_plan,args.dense_front_index,args.dense_video_only)
