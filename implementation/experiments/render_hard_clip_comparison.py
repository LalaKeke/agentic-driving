"""Planning-frame front+BEV GIFs from a completed fixed-output comparison.

Only reads saved comparison/replay data. No model, scene constructor, planner,
verifier, executor, annotations or maps are invoked. The visual style reuses
the existing front projection, near-field inset and four-panel BEV renderer.
"""
import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import shlex
import statistics
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'implementation'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from agentic.evidence_io import reserve_directory, write_json_new, write_text_new
import planning_front_gif as front_style
from append_bev_to_planning_gif import compose
from prototype_figure import load_result
from summarize_bend_sequence import signed_turn
from summarize_long_clip import draw_frame
from summarize_six_scene_scout import metrics

V2 = 'bounded_seed_candidates_v2'
V3 = 'bounded_seed_candidates_v3'
BASE_LABEL='v2'
NEW_LABEL='v3'
DISPLAY_NAMES = {'construction_turn':'Construction / intersection',
                 'night_pedestrian':'Rainy night / pedestrians',
                 'urban_interaction':'Urban interaction / following'}


def read(path, default=None):
    return json.loads(Path(path).read_text()) if Path(path).is_file() else default


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def resolve(value):
    path = Path(value)
    return path.resolve() if path.is_absolute() else (ROOT/path).resolve()


def selected(window, profile=None):
    return window['profiles'][profile or V3].get('selected_response')


def original_capture(window, report):
    value = selected(window)
    if value:
        config = read(resolve(value['output_dir'])/'config.json')
        path = resolve(config['capture_path'])
        return path, read(path)
    baseline = report['cohort'].get('baseline_root')
    if baseline:
        plan = read(resolve(baseline)/window['clip_id']/'plan.json')
        case = next(c for c in plan['cases'] if c['sample_token']==window['sample_token'])
        path = resolve(case['capture'])
        return path, read(path)
    raise ValueError('A registered reference lacks its acquired capture; cannot invent an image')


def clip_origin(clip, windows, report):
    baseline = report['cohort'].get('baseline_root')
    if baseline:
        plan = read(resolve(baseline)/clip/'plan.json')
        if plan and plan.get('raw_frames'):
            return plan['raw_frames'][0]['reference_time_s'], 'Original full recorded clip first frame'
    return min(c['metadata']['reference_time_s'] for _,_,c in windows), 'First shown saved observation, not full-clip origin'


def prepare_row(window, capture_path, cap, relative_time):
    value = selected(window)
    if value:
        replay = resolve(value['output_dir']); attempt = replay/'attempt_01'
        result, _ = load_result(attempt)
        source = resolve(value['source_record'])
        if sha(source) != value['source_record_sha256']:
            raise ValueError('Original native record changed after comparison')
        record = read(source)
        if result.get('agent_output') != record['output'] or cap != record['capture']:
            raise ValueError('Replay and original native/capture values differ')
        physical = metrics(attempt)
        if physical['prefix_consumed'] != value['prefix_consumed']:
            raise ValueError('Replay receipt differs from the recorded comparison')
        stages = dict(value['stages']); failure = value.get('failure')
        availability = window['profiles'][V3]['availability']
        candidate = None
        if not result.get('trajectory'):
            ledger = read(attempt/'candidate_ledger.json', {})
            candidates = [r for r in ledger.get('candidates', []) if r.get('T3')=='PASS']
            if candidates:
                candidate = min(candidates, key=lambda r:r.get('objective', float('inf')))['candidate_id']
                for name, key in [('T2','corridor'),('T3','trajectory'),('T4','verification')]:
                    saved = read(attempt/'candidates'/candidate/(name+'.json'))
                    if saved is not None:result[key]=saved
        result.setdefault('capture', cap)
        status = 'OFFLINE_'+NEW_LABEL.upper()+('_CHAIN_PASS' if value['conditional_chain_pass'] else '_REFUSED')
        stage = value.get('earliest_failure_stage')
    else:
        replay = None; attempt = None; source = None; candidate = None
        result = dict(capture=cap, agent_output={}, provenance={})
        physical = dict(prefix_consumed=False, independent_acceptor_pass=False,
            motion_class='NOT_EXECUTED', prefix_s=None, displacement_m=None,
            end_speed_mps=None, minimum_conditional_clearance_lower_bound_m=None,
            T4_verdict='NOT_REACHED')
        stages = {s:'NOT_REACHED' for s in ('T1','T2','T3','T4','EXECUTION','ACCEPTOR')}
        failure = window.get('original_fatal')
        availability = window['profiles'][V3]['availability']
        status = availability; stage = availability
    stages.update(SERVICE='NOT_CALLED_OFFLINE',BUSINESS_SCHEMA='SAVED_RECORD' if value else 'NOT_REACHED',
                  TURN_TASK='NOT_APPLICABLE' if not window.get('require_turn') else 'NOT_REACHED')
    decision = result.get('agent_output', {}).get('decision') or {}
    stationary = physical.get('motion_class')=='STATIONARY_HOLD'
    return dict(case_id=window['case_id'],sample_token=window['sample_token'],
        scene_name=window.get('scene_name') or window['clip_id'],_clip=window['clip_id'],
        capture_path=str(capture_path),runtime_path=str(replay) if replay else '',selected_attempt=1 if value else None,
        initial_speed_mps=cap['ego']['speed_mps'],status=status,final_failure_stage=stage,
        final_failure_reasons=[failure] if failure else [],physical=physical,stages=stages,
        relative_sequence_time_s=relative_time,source='SAVED_REAL_OUTPUT_OFFLINE_'+NEW_LABEL.upper(),
        api_post_requests=0,charged_requests=0,source_group='Saved model response / offline planner '+NEW_LABEL,
        display_only_unreleased_candidate=candidate,signed_turn=signed_turn(result),
        turn_task_required=bool(window.get('require_turn')),motion_review=dict(
            unexplained_hold_review_required=bool(stationary and decision.get('stop') is None and decision.get('behavior')!='stop_yield')),
        availability=availability,source_record=str(source) if source else None,
        original_prompt=(value or {}).get('original_prompt'),selected_comparison_response=value),result


class FigureCapture:
    """Keep the new figure open only to annotate native unknowns before export."""
    def __init__(self, plt):self.plt=plt;self.figure=None
    def __getattr__(self, name):return getattr(self.plt,name)
    def subplots(self,*args,**kwargs):
        self.figure,axes=self.plt.subplots(*args,**kwargs)
        return self.figure,axes
    def close(self,figure):pass


def four_panel(row, result, cap, path, plt, np, Patch, Image):
    holder=FigureCapture(plt)
    draw_frame(row,result,cap,path,holder,np,Patch,Image)
    fig=holder.figure;bev=fig.axes[1]
    native=result.get('agent_output',{}).get('hypotheses') or {}
    unknown_scopes=[]
    for item in native.get('unknown_regions',[]):
        if isinstance(item,dict) and item.get('polygon_m'):
            unknown_scopes.append((item['polygon_m'],'Unknown region (not free space)'))
    for item in native.get('unknowns',[]):
        if isinstance(item,dict) and item.get('space_polygon_m'):
            unknown_scopes.append((item['space_polygon_m'],'Unknown scope (not predicted occupancy)'))
    seen=set()
    for polygon,label in unknown_scopes:
        key=json.dumps(polygon)
        if key in seen:continue
        seen.add(key)
        bev.add_patch(Patch(polygon,fill=False,edgecolor='#b63759',hatch='///',linewidth=1.2,label=label))
    if row['stages']['T1']=='FAIL':
        count=sum(o.get('occupancy') is None for o in native.get('objects',[]) if isinstance(o,dict))
        note=f"T1 REFUSED / native assumptions only | null occupancy={count}"
        if native.get('road') is None:note+=' | road unknown'
        if native.get('unbounded_unknown'):note+=' | unbounded unknown'
        bev.text(.02,.02,note,transform=bev.transAxes,color='#a22',fontsize=7,
                 bbox=dict(fc='white',ec='#c99',alpha=.92))
    if unknown_scopes:
        bev.autoscale_view();bev.legend(fontsize=7)
    fig.savefig(path,dpi=140);plt.close(fig)


def offline_banner(left, row, rows, index, Image, ImageDraw):
    # An unattempted reference must not inherit the old helper's red refusal badge.
    if row['availability'] in ('NOT_ATTEMPTED','ATTEMPTED_WITHOUT_SAVED_NATIVE_OUTPUT'):
        draw=ImageDraw.Draw(left)
        draw.rectangle((left.width-350,0,left.width,48),fill='#101c2b')
        draw.text((left.width-340,17),'NOT ATTEMPTED' if row['availability']=='NOT_ATTEMPTED' else 'NO NATIVE OUTPUT',
                  font=front_style.font(19,True),fill='#b8c5d3')
        draw.line((0,96,left.width,96),fill='#8293a5',width=4)
    elif front_style.verified(row) and row['physical'].get('motion_class')=='STATIONARY_HOLD':
        draw=ImageDraw.Draw(left)
        draw.rectangle((left.width-350,0,left.width,48),fill='#101c2b')
        draw.text((left.width-340,17),'VERIFIED / HOLD',font=front_style.font(22,True),fill=front_style.GREEN)
    draw=ImageDraw.Draw(left)
    for j,other in enumerate(rows):
        x=22+j*((left.width-44)/len(rows));y=left.height-30
        fill=('#8293a5' if not other['selected_comparison_response'] else
              '#29a56c' if front_style.verified(other) else '#ad404b')
        draw.rounded_rectangle((x,y,x+((left.width-44)/len(rows))-5,y+19),radius=3,
                               fill=fill,outline='white' if j==index else None,width=3)
    return left


def outcome(window,profile):
    value=window['profiles'][profile]
    if value['any_of_at_most_two_saved_responses_pass'] is True:return 'PASS'
    if value['availability']=='NOT_ATTEMPTED':return 'NOT_ATTEMPTED'
    if not value.get('selected_response'):return 'NO_NATIVE_OUTPUT'
    return value['selected_response'].get('earliest_failure_stage') or 'INCOMPLETE'


def distribution(windows,path,plt,np):
    clips=list(dict.fromkeys(w['clip_id']for w in windows))
    fig,(left,right)=plt.subplots(1,2,figsize=(15,5.4));x=np.arange(len(clips));width=.36
    for i,profile in enumerate((V2,V3)):
        counts=[sum(w['clip_id']==c and outcome(w,profile)=='PASS'for w in windows)for c in clips]
        bars=left.bar(x+(i-.5)*width,counts,width,label=BASE_LABEL if i==0 else NEW_LABEL,color='#7186aa' if i==0 else '#26a077')
        for bar,count,clip in zip(bars,counts,clips):
            denominator=sum(w['clip_id']==clip for w in windows)
            left.text(bar.get_x()+bar.get_width()/2,count+.08,f'{count}/{denominator}',ha='center',fontsize=9)
    left.set_xticks(x,clips,rotation=18,ha='right');left.set_ylabel('Unique windows with accepted simulated prefix')
    left.set_title('Same saved JSON: up to two responses per window');left.legend();left.grid(axis='y',alpha=.2)
    categories=['PASS','T1','T2','T3','T4','EXECUTION','ACCEPTOR','OFFLINE_PROCESS','NOT_ATTEMPTED','NO_NATIVE_OUTPUT','INCOMPLETE']
    palette=['#26a077','#be6c9b','#d7944b','#d45252','#9566bc','#6f969e','#6777a0','#6b4e3d','#b6bec8','#8396a9','#cccccc']
    totals=[0,0]
    for category,color in zip(categories,palette):
        counts=[sum(outcome(w,p)==category for w in windows)for p in (V2,V3)]
        if not any(counts):continue
        right.bar([BASE_LABEL,NEW_LABEL],counts,bottom=totals,color=color,label=category)
        for i,n in enumerate(counts):
            if n:right.text(i,totals[i]+n/2,str(n),ha='center',va='center',fontsize=10)
        totals=[a+b for a,b in zip(totals,counts)]
    right.set_title('Disjoint final outcomes / all registered windows');right.set_ylabel('Unique windows')
    right.legend(fontsize=8,loc='upper left',bbox_to_anchor=(1.01,1))
    fig.suptitle('SAVED REAL OUTPUT / OFFLINE REPLANNING COMPARISON / NO NEW ONLINE SUCCESS',fontsize=13)
    fig.tight_layout();fig.savefig(path,dpi=160);plt.close(fig)


def main(args):
    global V2,V3,BASE_LABEL,NEW_LABEL
    comparison=args.comparison.resolve();report=read(comparison)
    if not report or report.get('ended') is not True:
        raise ValueError('Read only a completed comparison.json')
    profiles=tuple(report['profiles'])
    if len(profiles)!=2 or any(p not in ('bounded_seed_candidates_v2','bounded_seed_candidates_v3','bounded_seed_candidates_v4') for p in profiles):
        raise ValueError('Two explicit supported planner profiles required')
    V2,V3=profiles
    BASE_LABEL,NEW_LABEL=(p.rsplit('_',1)[-1] for p in profiles)
    mixed_casebook=report.get('cohort',{}).get('mode')=='CURRENT_CASEBOOK_MIXED_INPUT_GROUPS'
    windows=report['windows']
    if args.preview_window:
        render_windows=[w for w in windows if args.preview_window in (w['case_id'],w['window_identity'])]
        if len(render_windows)!=1:raise ValueError('--preview-window must identify exactly one registered window')
    else:render_windows=windows
    root=reserve_directory(args.out,readonly_inputs=(comparison.parent,))
    source=dict(comparison=str(comparison),comparison_sha256=sha(comparison),script_sha256=sha(__file__),
        git_head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        new_model_calls=0,new_planner_calls=0,new_verifier_calls=0,new_execution_calls=0,
        preview_only=bool(args.preview_window),selection=NEW_LABEL+' earliest saved response that passes; otherwise latest saved response',
        rendering_only=True,GT_used=False,mixed_input_casebook=mixed_casebook)
    write_json_new(root/'source.json',source)
    groups=defaultdict(list)
    for window in render_windows:
        path,cap=original_capture(window,report)
        groups[window['clip_id']].append((window,path,cap))
    with tempfile.TemporaryDirectory(prefix='hard-clip-mpl-')as cache:
        os.environ['MPLCONFIGDIR']=cache
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        from matplotlib.patches import Polygon as Patch
        import numpy as np
        from PIL import Image,ImageDraw
        plt.rcParams.update({'font.size':10})
        distribution(windows,root/'before_after_distribution.png',plt,np)
        clips=[]
        for clip,items in groups.items():
            origin,basis=clip_origin(clip,items,report)
            items.sort(key=lambda x:x[2]['metadata']['reference_time_s'])
            folder=reserve_directory(root/f'clip_{len(clips)+1:02d}');prepared=[]
            title=DISPLAY_NAMES.get(clip,front_style.CLIPS.get(clip,clip.replace('_',' ')))
            front_style.CLIPS[clip]=title
            for window,path,cap in items:
                row,result=prepare_row(window,path,cap,cap['metadata']['reference_time_s']-origin)
                prepared.append((window,row,result,cap))
            rows=[item[1]for item in prepared];frames=[];metadata=[]
            for index,(window,row,result,cap)in enumerate(prepared):
                frame_dir=reserve_directory(folder/f'frame_{index+1:03d}')
                four_panel(row,result,cap,frame_dir/'four_panel.png',plt,np,Patch,Image)
                left,projection_metadata=front_style.render_frame(row,index,rows,1280)
                left=offline_banner(left,row,rows,index,Image,ImageDraw)
                with Image.open(frame_dir/'four_panel.png')as bev:
                    combined=compose(left,bev,row)
                draw=ImageDraw.Draw(combined)
                draw.rectangle((1296,905,combined.width,combined.height),fill='#101c2b')
                draw.text((1318,912),f'Saved {NEW_LABEL} offline replan; this drawing adds no model or tool calls.',font=front_style.font(17),fill='#c4d5e6')
                banner=Image.new('RGB',(combined.width,combined.height+44),'#15283b')
                banner.paste(combined,(0,44));draw=ImageDraw.Draw(banner)
                banner_text=(f'CURRENT CASEBOOK / {NEW_LABEL} / {len(cap["observations"])} causal images / '
                             f'{window.get("current_casebook_input_group", "MIXED_GROUPS")} / simulated prefix only'
                             if mixed_casebook else
                             f'SAVED REAL MODEL OUTPUT / OFFLINE PLANNER {NEW_LABEL} REPLAN / independent recorded states / no new online success')
                draw.text((20,9),banner_text,font=front_style.font(22,True),fill='white')
                banner.save(frame_dir/'front_bev.png');frames.append(banner)
                value=row['selected_comparison_response']
                meta=dict(case_id=row['case_id'],sample_token=row['sample_token'],relative_sequence_time_s=row['relative_sequence_time_s'],
                    original_prompt=row['original_prompt'],source_record=row['source_record'],
                    source_record_sha256=sha(row['source_record'])if row['source_record']else None,
                    replay_output_dir=(value or {}).get('output_dir'),stages=row['stages'],physical=row['physical'],
                    status=row['status'],failure_stage=row['final_failure_stage'],failure_reasons=row['final_failure_reasons'],
                    original_vs_improved={p:outcome(window,p)for p in (V2,V3)},projection=projection_metadata,
                    drawing_mode='SAVED_REAL_OUTPUT_OFFLINE_'+NEW_LABEL.upper()+'_REPLANNING',new_model_calls=0,
                    casebook_input_group=window.get('current_casebook_input_group'),causal_image_count=len(cap['observations']))
                write_json_new(frame_dir/'result.json',meta);metadata.append(meta)
            times=[r['relative_sequence_time_s']for r in rows]
            gaps=[b-a for a,b in zip(times,times[1:])]
            if any(x<=0 for x in gaps):raise ValueError('Distinct windows of one clip need increasing acquisition times')
            durations=[max(10,round(x*100)*10)for x in gaps+[statistics.median(gaps)if gaps else 1.]]
            with (folder/'planning_front_bev.gif').open('xb')as stream:
                frames[0].save(stream,format='GIF',save_all=True,append_images=frames[1:],duration=durations,loop=0,disposal=2,optimize=False)
            with Image.open(folder/'planning_front_bev.gif')as decoded:
                if decoded.n_frames!=len(frames):raise ValueError('GIF dropped a reference')
                for i,d in enumerate(durations):
                    decoded.seek(i);decoded.load()
                    if decoded.info['duration']!=d:raise ValueError('GIF cadence changed')
            best=max(range(len(rows)),key=lambda i:abs(rows[i]['signed_turn'].get('plan_end_yaw_change_deg')or 0))
            frames[best].save(folder/'preview.png')
            clip_summary=dict(clip_id=clip,title=title,frames=len(frames),gif=str((folder/'planning_front_bev.gif').relative_to(root)),
                accepted_frames=sum(r['selected_comparison_response'] is not None and r['selected_comparison_response']['conditional_chain_pass']for r in rows),
                time_origin=basis,frame_durations_ms=durations,source_records=metadata)
            write_json_new(folder/'summary.json',clip_summary)
            clips.append({k:v for k,v in clip_summary.items()if k!='source_records'})
            print(json.dumps(clips[-1],ensure_ascii=False),flush=True)
    physical_columns=['motion_class','displacement_m','end_speed_mps','minimum_conditional_clearance_lower_bound_m',
                      'max_abs_acceleration_mps2','max_abs_steer_rad','rms_discrete_jerk_proxy_mps3']
    columns=['clip_id','case_id','sample_token',BASE_LABEL+'_first_saved_pass',BASE_LABEL+'_any_saved_pass',NEW_LABEL+'_first_saved_pass',NEW_LABEL+'_any_saved_pass',BASE_LABEL+'_outcome',NEW_LABEL+'_outcome','source_record',
             'selected_candidate','T2_pass_candidates','T3_pass_candidates','T4_pass_candidates','solver_status']+physical_columns
    with (root/'comparison_table.csv').open('x',newline='')as stream:
        writer=csv.DictWriter(stream,fieldnames=columns);writer.writeheader()
        for window in windows:
            value=selected(window)or {}
            row={k:window[k]for k in ('clip_id','case_id','sample_token')}
            for p,label in ((V2,BASE_LABEL),(V3,NEW_LABEL)):
                row[label+'_first_saved_pass']=window['profiles'][p]['first_saved_native_response_pass']
                row[label+'_any_saved_pass']=window['profiles'][p]['any_of_at_most_two_saved_responses_pass']
                row[label+'_outcome']=outcome(window,p)
            row.update({k:value.get(k)for k in ('source_record','selected_candidate',*physical_columns)})
            row.update({stage+'_pass_candidates':value.get('candidate_counts',{}).get(stage)for stage in ('T2','T3','T4')})
            row['solver_status']=(value.get('solver')or {}).get('status');writer.writerow(row)
    summary=dict(source,registered_windows=len(windows),rendered_windows=len(render_windows),unique_scene_count=report.get('unique_scenes'),clips=clips,
                 claim=f'Saved real-output offline {NEW_LABEL} results; original assumptions retained. No new online success or continuous counterfactual execution.')
    write_json_new(root/'summary.json',summary)
    lines=['# 困难clip：前视投影＋俯视场景与前后对照','',
        f'**保存真实模型输出／离线{NEW_LABEL}重规划。新增模型、规划、核验与执行调用均为0。** 本脚本只绘图，原结果不改。',
        f"登记窗口{len(windows)}，本次实际绘图{len(render_windows)}；{'这是单帧开发预览，不是完整clip展示。'if args.preview_window else f'每窗选择{NEW_LABEL}最早通过的已存响应，否则选择最后一份已存响应。'}",'',
        '![前后通过与失败阶段分布](before_after_distribution.png)','',
        ('**本图为当前案例视图：混合原10图、此前绑定修订和新12图组，不是原冻结21窗实验通过率。**' if mixed_casebook else ''),'',
        f'|clip|规划帧GIF|{NEW_LABEL}已接受帧/展示帧|','|---|---|---:|']
    for c in clips:lines.append(f"|{c['clip_id']}|[播放]({c['gif']})|{c['accepted_frames']}/{c['frames']}|")
    lines+=['','[全部登记窗口CSV](comparison_table.csv) / [完整汇总](summary.json) / [源比较与脚本SHA](source.json)。分布图、CSV始终按完整输入comparison的登记分母，预览GIF另有明确子集标记。',
        '左侧沿用前视轨迹、近场俯视小窗、决策/模拟消费数值和进度条；右侧为模型局部道路、原生BODY和所有声明占用图形、走廊及轨迹。未知作用域用斜线和文字表示，不当作空占用；T1拒绝时仍显示可用原生对象，并保留后续NOT_REACHED。',
        '青色前视路径与蓝色BEV路径为已保存轨迹，绿色为已模拟消费前缀；未释放候选仅在BEV明确标注。前视投影采用展示性z=0假设。',
        '各参考帧独立；帧间未查询时间没有新计划，不沿用旧核验。GIF按参考采集间隔播放，末帧按间隔中位数停留；只有一个时刻时停留1s。旧记录组可能只覆盖稀疏窗口，其时间原点明确标为首个展示观测，不冒称完整20秒执行。',
        '这是同份JSON的离线规划对比，不是新在线成功率、G2通过或真实车辆安全证明。','',
        '零调用复现（输出必须为新目录）：','', '```bash','cd /data2/hzy/MAP/agentic-driving',
        'OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python implementation/experiments/render_hard_clip_comparison.py --comparison '+shlex.quote(str(comparison))+
        (' --preview-window '+shlex.quote(args.preview_window)if args.preview_window else '')+' --out /tmp/new_hard_clip_visuals','```','']
    write_text_new(root/'README.md','\n'.join(lines))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--comparison',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--preview-window',help='Render exactly one uniquely matching case_id/window identity for development; summary chart/CSV retain the full denominator')
    main(parser.parse_args())
