"""Read-only aggregation after all three long-clip reports have completed."""
import argparse, hashlib, html, io, json, os, shlex, tempfile
from pathlib import Path

CLIPS = ('following_deceleration', 'bus_road', 'parking_bend')
DENSE_COUNTS = (229, 237, 240)
LIMIT = ('57 planning references in 3 recorded scenes; full camera videos include unqueried images. '
         'Each 2s plan / consumed 0.2s prefix starts independently from recorded ego state. '
         'No stitched execution, counterfactual closed loop, GT evaluation or real-vehicle safety claim.')


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def passed(row):
    return bool(row.get('physical', {}).get('independent_acceptor_pass') and row['physical'].get('prefix_consumed'))


def collect(root):
    values = []
    for clip, count in zip(CLIPS, DENSE_COUNTS):
        source = root / clip / 'summary.json'
        if not source.is_file():
            raise ValueError('Report not complete; no overview written: ' + str(source))
        summary = read(source)
        if not (summary.get('ended') is True and summary['registered_planning_references'] == 19 and len(summary['frames']) == 19):
            raise ValueError('Require ended=true and all nineteen reference results: ' + clip)
        plan_path = Path(summary['source_plan']); plan = read(plan_path)
        if sha(plan_path) != summary['source_plan_sha256']:
            raise ValueError('Original plan differs from its report: ' + clip)
        if summary['dense_front_display']['frame_count'] != count:
            raise ValueError('Unexpected dense display frame count: ' + clip)
        n = sum(passed(row) for row in summary['frames'])
        if n != summary['conditional_chain_passes']:
            raise ValueError('Conditional pass count disagrees with accepted-prefix evidence: ' + clip)
        image = next(r['image_path'] for r in plan['raw_frames'] if r['planning_case_id'] == summary['frames'][0]['case_id'])
        compact = dict(clip_id=clip, scene_name=summary['scene_name'], duration_s=plan['full_clip_duration_s'],
            planning_references=19, conditional_passes=n, not_completed=19-n, dense_frames=count,
            moving=summary['motion_classes'].get('MOVING_PREFIX', 0), stationary=summary['motion_classes'].get('STATIONARY_HOLD', 0),
            charged=summary['charged_requests'], observed_posts=summary['api_post_requests'],
            unknown_dispatch=summary['unknown_dispatch_attempts'], prior_preflight_failures=summary['clip_wide_preflight_failures'],
            unexplained_hold_review_required=summary['unexplained_hold_review_required'], summary_path=str(source), summary_sha256=sha(source),
            source_groups=[{k: s.get(k) for k in ('source_group','status','plan_path','plan_sha256','observer_closed','completion_authority','parent_exec_exit_code','charged_requests','api_post_requests','unknown_dispatch_attempts','clip_wide_preflight_failure','fatal')} for s in summary['sources']])
        values.append((compact, summary, plan, image))
    if len({s['frames'][0]['scene_token'] for _, s, _, _ in values}) != 3:
        raise ValueError('Three distinct recorded scenes are required')
    return values


def main(root, out, check_only=False):
    values = collect(root)
    totals = {k: sum(c[k] for c, _, _, _ in values) for k in ('planning_references','conditional_passes','not_completed','dense_frames','moving','stationary','charged','observed_posts','unknown_dispatch','prior_preflight_failures')}
    if check_only:
        print(json.dumps(dict(ready=True, **totals))); return
    targets = [out / n for n in ('overview.png','index.html','summary_all.json','README.md')]
    if any(p.exists() or p.is_symlink() for p in targets):
        raise ValueError('Use new overview destinations; an existing artifact would be overwritten')
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='long-clip-overview-mpl-') as cache:
        os.environ['MPLCONFIGDIR'] = cache
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        from PIL import Image
        fig, axes = plt.subplots(3, 2, figsize=(17, 11), gridspec_kw={'width_ratios':[1, 1.35]})
        for (compact, summary, plan, image), (left, right) in zip(values, axes):
            with Image.open(image) as original: left.imshow(original.convert('RGB'))
            left.axis('off'); left.set_title(compact['scene_name'] + ' | first planning-reference original image')
            raw = plan['raw_frames']; right.axvspan(0, plan['first_planning_time_s'], color='#bbb', alpha=.25)
            right.scatter([r['relative_sequence_time_s'] for r in raw], [0]*len(raw), c='#bbb', s=24, label='Input only / no new plan')
            for row in summary['frames']:
                color = '#279963' if passed(row) else ('#ce4646' if row.get('final_failure_stage') in ('T1','T2','T3','T4') or row['status']=='TOOL_OR_SEMANTIC_FAILURE' else '#d79934')
                right.scatter(row['relative_sequence_time_s'], 0, c=color, s=130, zorder=3)
                right.text(row['relative_sequence_time_s'], -.12, row['case_id'].split('_')[-1], ha='center', fontsize=7)
            right.set(xlim=(-.2, compact['duration_s']+.3), ylim=(-.45, .65), yticks=[], xlabel='Recorded clip time (s)',
                title=f"{compact['conditional_passes']}/19 conditional chains | {compact['not_completed']} not completed | {compact['dense_frames']} video inputs")
            right.text(.01, .89, f"MOVING={compact['moving']} HOLD={compact['stationary']} | charged={compact['charged']} observed POST={compact['observed_posts']} unknown dispatch={compact['unknown_dispatch']}", transform=right.transAxes, fontsize=9)
            right.text(.01, .73, 'Green: accepted simulated prefix; red: tool refusal; orange: unresolved/unreached; gray: input only', transform=right.transAxes, fontsize=8)
            right.grid(axis='x', alpha=.2)
        fig.suptitle(f"Three complete recorded camera clips | {totals['conditional_passes']}/57 conditional chains | all failures retained", fontsize=16)
        fig.text(.02, .016, LIMIT, fontsize=8); fig.tight_layout(rect=(0,.045,1,.955))
        buffer=io.BytesIO(); fig.savefig(buffer, format='png', dpi=150); plt.close(fig)
    with targets[0].open('xb') as stream: stream.write(buffer.getvalue())
    combined=dict(ended=True, unique_scenes=3, **totals, analysis_script_sha256=sha(Path(__file__)), new_model_requests=0, GT_used=False, claim=LIMIT, clips=[c for c, _, _, _ in values])
    with targets[2].open('x') as stream: json.dump(combined, stream, ensure_ascii=False, indent=2); stream.write('\n')
    lines=['# 三场景完整相机clip：结果总览', '', f"3个scene、57个规划参考时刻，条件式链路通过{totals['conditional_passes']}个；未完成{totals['not_completed']}个，失败全部保留。", '', '![三场景总览](overview.png)', '', '|场景|原片/稠密图像|条件通过/19|未完成|移动/静止|扣账/已观察POST/派发未知|', '|---|---|---:|---:|---|---|']
    cards=[]
    for c, s, _, _ in values:
        cid=c['clip_id']; relative=os.path.relpath(root/cid, out)
        lines.append(f"|[{c['scene_name']}]({relative}/index.html)|{c['duration_s']:.2f}s / {c['dense_frames']}|{c['conditional_passes']}|{c['not_completed']}|{c['moving']}/{c['stationary']}|{c['charged']}/{c['observed_posts']}/{c['unknown_dispatch']}|")
        links=' · '.join(f'<a href="{html.escape(relative)}/{name}">{label}</a>' for name,label in [('dense_front.mp4','完整相机MP4'),('full_clip.gif','规划分析GIF'),('casebook.pdf','PDF'),('index.html','逐帧结果')])
        cards.append(f'<section><h2>{html.escape(c["scene_name"])} · {c["conditional_passes"]}/19</h2><p>{links}</p><video controls preload="metadata" style="width:100%" src="{html.escape(relative)}/dense_front.mp4"></video></section>')
    lines += ['', f"累计扣账{totals['charged']}、已观察POST {totals['observed_posts']}、派发未知{totals['unknown_dispatch']}；先前非生成预检失败{totals['prior_preflight_failures']}次。未知在途请求及observer closure保留，不能据此说模型返回失败。", '', '完整相机视频包含706张原始前向图，模型只在57个选定参考时刻决策；每次规划2秒、模拟消费0.2秒，未查询图像没有新计划。每时刻从录制ego独立回放，不是反事实闭环、GT或真车安全证明。', '', '零模型调用复现（使用全新输出目录）：', '', '```bash', 'cd /data2/hzy/MAP/agentic-driving', 'OUT="$(mktemp -d /tmp/long-clips-report.XXXXXX)"']
    for c, s, _, _ in values:
        command='python implementation/experiments/summarize_long_clip.py --plan '+shlex.quote(s['sources'][0]['plan_path'])
        command+=''.join(' --additional-plan '+shlex.quote(x['plan_path']) for x in s['sources'][1:])
        command+=' --dense-front-index '+shlex.quote(s['dense_front_display']['index_path'])+' --dense-video-only --out "$OUT/'+c['clip_id']+'"'
        lines.append(command)
    lines += ['python '+shlex.quote(str(Path(__file__).resolve()))+' --root "$OUT"', '```', '']
    with targets[3].open('x') as stream: stream.write('\n'.join(lines))
    document='<!doctype html><meta charset="utf-8"><title>Three recorded clips</title><style>body{font:16px sans-serif;max-width:1450px;margin:auto;padding:24px}img{max-width:100%}section{margin:35px 0}</style><h1>三场景完整相机clip</h1><p>'+html.escape(LIMIT)+'</p><p>扣账/已观察POST/派发未知分别为 '+str(totals['charged'])+'/'+str(totals['observed_posts'])+'/'+str(totals['unknown_dispatch'])+'；未知不等于服务失败。</p><img src="overview.png">'+''.join(cards)+'<p><a href="summary_all.json">机器总表</a> · <a href="README.md">说明及复现命令</a></p>'
    with targets[1].open('x') as stream: stream.write(document)
    print(json.dumps(dict(out=str(out), **totals, new_model_requests=0)))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parent); parser.add_argument('--out', type=Path); parser.add_argument('--check-only', action='store_true')
    args=parser.parse_args(); main(args.root.resolve(), (args.out or args.root).resolve(), args.check_only)
