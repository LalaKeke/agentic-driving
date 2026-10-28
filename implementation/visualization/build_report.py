"""Build an offline-only visual review from archived results; never calls a model.

Requires Pillow and matplotlib in the reporting interpreter. All input hashes are
recorded. Existing experiment records are read-only. No server or CDN required.
"""
import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LIVE = ROOT / 'implementation/results/gpt61_live'
CASES = [
 ('ac452a60e8b34a7080c938c904b23057', '白天行人横穿', 'day_pedestrian_crossing', 'UNBOUNDED_UNKNOWN ×3', '原协议 4 份响应；含两次反馈修订'),
 ('88449a5cb1644a199c1c11f6ac034867', '施工障碍与左弯', 'construction_obstacle', 'UNBOUNDED_UNKNOWN ×2', '原协议 3 份响应；另有首次 300s 超时'),
 ('c537fef0b4b241df92f44daa976fe5b3', '路口横向车流', 'intersection_cross_traffic', 'MISSING_SCALE ×1', '原协议 2 份响应；反馈请求 600s 超时'),
 ('b4c279e61abf49bd871ff2ec4b13e115', '夜间行人', 'night_pedestrian_crossing', '原协议超时 / 2.1 离线复核 MISSING_SCALE', '原协议 600s 超时；独立 2.1 格式复核 1 份响应'),
]


def build(out, synthetic):
    out.mkdir(parents=True, exist_ok=True)
    # Keep matplotlib/font caches within this project's ignored run directory.
    os.environ['MPLCONFIGDIR'] = str(ROOT / 'runs/visualization/mplconfig')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    from matplotlib.backends.backend_pdf import PdfPages
    from PIL import Image, __version__ as pillow_version
    font = Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc')
    if font.exists():
        font_manager.fontManager.addfont(str(font))
        plt.rcParams['font.family'] = font_manager.FontProperties(fname=font).get_name()
    plt.rcParams.update({'axes.unicode_minus': False, 'font.size': 10, 'axes.spines.top': False, 'axes.spines.right': False, 'svg.fonttype': 'path'})
    sources = {}

    def read(path):
        path = Path(path)
        raw = path.read_bytes()
        sources[str(path.relative_to(ROOT))] = {'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}
        return json.loads(raw)

    def jpeg(path, width=960):
        path = Path(path)
        raw = path.read_bytes()
        try:
            key = str(path.relative_to(ROOT))
        except ValueError:
            key = str(path)
        sources[key] = {'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}
        im = Image.open(io.BytesIO(raw)).convert('RGB')
        im.thumbnail((width, width))
        buf = io.BytesIO(); im.save(buf, format='JPEG', quality=78)
        return 'data:image/jpeg;base64,' + base64.b64encode(buf.getvalue()).decode()

    summary = read(LIVE / 'summary.json')
    response_index = read(LIVE / 'response_index.json')
    feedback = read(LIVE / 'feedback_index.json')
    history = read(LIVE / 'case_results_with_history.json')
    captures = {}
    for p in sorted((LIVE / 'raw_runs').glob('*/*capture.json')):
        c = read(p)
        captures.setdefault(c['metadata']['scene_id'].removeprefix('nusc_'), c)
    cases = []
    for sid, title, slug, reason, note in CASES:
        c = captures[sid]
        responses = []
        for idx in response_index:
            if idx['sample_id'] != sid:
                continue
            record = read(LIVE / 'raw_runs' / idx['run'] / idx['file'])
            responses.append({'index': idx, 'output': record['output'], 'provenance': record['provenance'],
                              'source': '../gpt61_live/raw_runs/' + idx['run'] + '/' + idx['file']})
        cases.append({'id': sid, 'title': title, 'slug': slug, 'reason': reason, 'note': note,
                      'ego': c['ego'], 'responses': responses,
                      'frames': [{'camera': f['camera_id'], 'relative_s': f['capture_time_s']-c['metadata']['reference_time_s'],
                                  'id': f['id'], 'image': jpeg(f['path'])} for f in c['observations']],
                      'gt_image': jpeg(LIVE / 'offline_review' / (slug + '.jpg'), 1280)})
    g2 = read(LIVE / 'g2/g2_original_exploratory_auxiliary.json')
    # Only the explicitly reviewed exploratory associations are displayed.
    review = read(LIVE / 'offline_review/auxiliary_association_review.json')
    truth = read(LIVE / 'g2/g2_auxiliary_reviewed_truth.json')
    sr = ROOT / synthetic
    selected = {name: read(sr / name) for name in ['0007-T3.json','0008-T4.json','0012-T1.json','0013-T2.json','0014-T3.json','0015-T4.json','0192-summary.json']}
    scenes, trajectory = {}, None
    base_time=selected['0012-T1.json']['metadata']['reference_time_s']
    rounds = []
    for p in sorted(sr.glob('*.json')):
        if p.name.endswith('-T1.json'):
            d = read(p); scenes[d['metadata']['revision']] = d
        elif p.name.endswith('-T3.json'):
            trajectory = (p, read(p))
        elif p.name.endswith('-T4.json'):
            d = read(p)
            if d['verdict'] == 'PASS':
                tp, tr = trajectory
                assert tr['metadata'] == d['metadata']
                scene = scenes[d['metadata']['revision']]
                rounds.append({'time_s': scene['metadata']['reference_time_s']-base_time,
                               'speed_mps': tr['points'][0]['speed_mps'],
                               'behavior': scene['decision']['behavior'], 'prefix_s': d['released_prefix_s'],
                               'source': str(p.relative_to(ROOT))})
        elif p.name.endswith('-simulation.json'):
            d = read(p)
            rounds[-1]['end_speed_mps'] = d['speed_mps']
    syn = {'scene': selected['0012-T1.json'], 'corridor': selected['0013-T2.json'],
           'accepted': selected['0014-T3.json'], 'accepted_t4': selected['0015-T4.json'],
           'rejected': selected['0007-T3.json'], 'rejected_t4': selected['0008-T4.json'],
           'rounds': rounds, 'summary': selected['0192-summary.json'], 'run': synthetic}
    payload = {'cases': cases, 'summary': summary, 'feedback': feedback, 'history': history,
               'g2': g2, 'review': review, 'synthetic': syn}
    figures = []

    def save(fig, name):
        fig.savefig(out / (name+'.png'), dpi=170, bbox_inches='tight', facecolor='white')
        fig.savefig(out / (name+'.svg'), bbox_inches='tight', facecolor='white')
        figures.append(fig)

    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    for ax, c in zip(axes.flat, cases):
        im = Image.open(io.BytesIO(base64.b64decode(c['frames'][-1]['image'].split(',')[1])))
        ax.imshow(im);ax.axis('off')
        ax.set_title(c['title']+' | 真实采集图像\n'+c['reason'], fontsize=11)
        ax.text(0, -.035, c['note']+'\n无真实模型执行轨迹', transform=ax.transAxes, fontsize=9, va='top')
    fig.suptitle('真实 GPT-6.1 × nuScenes：图像调用成功，物理规划入口受阻', fontsize=17)
    fig.subplots_adjust(top=.86,bottom=.09,hspace=.48,wspace=.12);save(fig,'real_cases')

    fig, axes = plt.subplots(1, 2, figsize=(13,5))
    av = summary['original_protocol']['unconditional_field_availability']
    names=['有点位','有米制速度','有数值不确定界限']; vals=[av['nonnull_positions'],av['nonnull_velocities'],av['nonnull_uncertainties']]
    axes[0].barh(names,[39]*3,color='#e9eef5'); axes[0].barh(names,vals,color=['#2764db','#e18b19','#e18b19'])
    for y, v in enumerate(vals):axes[0].text(v+.6,y,f'{v}/39',va='center')
    axes[0].set_xlim(0,45); axes[0].set_xlabel('目标条目数（同一目标会因重复调用再次计数）');axes[0].set_title('原契约 6 次无反馈响应：字段可用性')
    axes[1].bar(['有效响应','T1 拒绝','T2 调用','T3 调用','T4 调用','执行前缀'],[9,6,0,0,0,0],color=['#2764db','#e18b19','#a4adba','#a4adba','#a4adba','#a4adba'])
    axes[1].set_ylim(0,11);axes[1].set_ylabel('次数');axes[1].set_title('原契约运行链路（不同阶段计数口径不同）')
    axes[1].text(.02,.94,'9 响应 = 6 无反馈 + 3 修订\n只有规划调用进入 T1；无反馈评价采样不进入工具链',transform=axes[1].transAxes,va='top',fontsize=9)
    fig.suptitle('未提供的数据保留为未知；没有“默认安全”',fontsize=16);fig.tight_layout();save(fig,'availability')

    fig, axes = plt.subplots(1, 2, figsize=(13,5))
    rows=g2['rows']; vals=[r['position_error_m'] for r in rows]
    axes[0].bar(['行人·调用1','行人·调用2','MPV·调用1','MPV·调用2'],vals,color=['#2764db']*2+['#bd6a16']*2)
    for i,v in enumerate(vals):axes[0].text(i,v+.06,f'{v:.4f}',ha='center')
    axes[0].set_ylabel('点位误差 (m)');axes[0].set_ylim(0,4.7);axes[0].set_title('仅两个事后选择的探索性目标；不是总体精度')
    axes[1].axis('off'); axes[1].text(.02,.95,'评价缺口\n\n速度：无可用数值，误差记 null\n有效完整未来覆盖：无可用得分\n自报置信/范围：未校准\n\n巨大占位包络诊断\n原始面积重合 1.0；面积约 GT 的 98.39 倍\n模型已声明不支持，不能计为有效预测\n\n未设置或套用“通过”阈值；G2 不判通过',va='top',fontsize=12,linespacing=1.5)
    fig.suptitle('G2 离线诊断：真值从未进入运行时输入',fontsize=16);fig.tight_layout();save(fig,'g2_diagnostics')

    fig, axes = plt.subplots(1, 2, figsize=(13,5))
    for ax, case in zip(axes, cases[:2]):
        sid=case['id']; actor_id=review['auxiliary_targets'][sid][0]
        gt=next(a for a in truth['samples'][sid]['objects'] if a['id']==actor_id)['position_m']
        ax.scatter(*gt,marker='*',s=170,color='#182b43',label='离线 GT 框中心投影',zorder=4)
        predictions=[]
        for row in [r for r in g2['rows'] if r['sample_id']==sid]:
            run,file=row['record_id'].split(':')
            model_id=review['record_associations'][row['record_id']][actor_id]
            rec=next(r for r in case['responses'] if r['index']['run']==run and r['index']['file']==file)
            predictions.append(next(a for a in rec['output']['hypotheses']['objects'] if a['id']==model_id)['position_m'])
        for i,pt in enumerate(predictions):
            color=['#2864dc','#bd6a16'][i]
            ax.plot([gt[0],pt[0]],[gt[1],pt[1]],color=color,ls='--',alpha=.65)
            ax.scatter(*pt,color=color,s=60,label=f'模型调用 {i+1}',zorder=3)
            ax.annotate(f'调用 {i+1}',pt,xytext=(8,14 if i==0 else -18),textcoords='offset points',color=color)
        xs=[gt[0]]+[p[0] for p in predictions];ys=[gt[1]]+[p[1] for p in predictions]
        pad=max(.6,(max(xs)-min(xs))*.3)
        ax.set_xlim(min(xs)-pad,max(xs)+pad);ax.set_ylim(min(ys)-pad,max(ys)+pad)
        ax.set_aspect('equal',adjustable='datalim');ax.grid(alpha=.18)
        ax.set(xlabel='x 向前 (m)',ylabel='y 向左 (m)',title=case['title']+'：局部点位对比')
        ax.legend(fontsize=8,loc='best')
    fig.suptitle('探索性目标局部放大 · 相同 ego_at_reference 坐标系 · GT 仅离线读取',fontsize=15)
    fig.tight_layout();save(fig,'g2_positions')

    fig, axes = plt.subplots(1, 3, figsize=(16,4.8))
    for key,color,label in [('rejected','#d24949','故障注入：匀速候选（拒绝）'),('accepted','#218964','修订后：制动候选（通过）')]:
        ps=syn[key]['points'];ts=[p['time_s'] for p in ps]
        axes[0].plot(ts,[p['x_m'] for p in ps],color=color,label=label)
        axes[1].plot(ts,[p['speed_mps'] for p in ps],color=color,label=label)
    stop=syn['scene']['decision']['stop'];axes[0].axhline(stop['center_x_max_m'],color='#218964',ls=':',label='修订后停车上限（中心）')
    axes[0].set(xlabel='规划相对时间 (s)',ylabel='自车中心纵向位置 (m)',title='同一观测：故障候选与修订规划')
    axes[1].axvspan(0,.2,color='#218964',alpha=.14,label='仅放行前 0.2s')
    axes[1].set(xlabel='规划相对时间 (s)',ylabel='速度 (m/s)',title='5s 规划不等于执行 5s')
    for ax in axes[:2]:ax.legend(fontsize=8);ax.grid(alpha=.18)
    ts=[r['time_s'] for r in rounds]+[rounds[-1]['time_s']+rounds[-1]['prefix_s']]
    vs=[r['speed_mps'] for r in rounds]+[rounds[-1]['end_speed_mps']]
    axes[2].plot(ts,vs,'o-',ms=3,color='#2764db');axes[2].grid(alpha=.18)
    axes[2].set(xlabel='累计模拟时间 (s)',ylabel='已执行前缀后的速度 (m/s)',title='26 次前缀模拟：减速后恢复')
    fig.suptitle(f'人工测试数据 / 脚本智能体 / 模拟执行：最低记录速度 {min(vs):.3f}m/s（该运行未完全停稳）',fontsize=14)
    fig.tight_layout();save(fig,'synthetic_trajectory')
    with PdfPages(out / 'figures.pdf') as pdf:
        for fig in figures:pdf.savefig(fig,bbox_inches='tight');plt.close(fig)
    template=(Path(__file__).with_name('report.html')).read_text()
    for name in ['real_cases','availability','g2_diagnostics','g2_positions','synthetic_trajectory']:
        encoded=base64.b64encode((out/(name+'.png')).read_bytes()).decode()
        template=template.replace('src="'+name+'.png"', 'src="data:image/png;base64,'+encoded+'"')
    raw=json.dumps(payload,ensure_ascii=False,separators=(',',':')).replace('<','\\u003c').replace('\u2028','\\u2028').replace('\u2029','\\u2029')
    (out/'index.html').write_text(template.replace('__REPORT_DATA__',raw))
    manifest={'purpose':'Offline visualization only; no new model calls, no changes to original results',
              'synthetic_run':synthetic,'libraries':{'matplotlib':matplotlib.__version__,'Pillow':pillow_version},
              'inputs':sources}
    for p in [Path(__file__),Path(__file__).with_name('report.html')]:
        manifest['inputs'][str(p.relative_to(ROOT))]={'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'bytes':p.stat().st_size}
    manifest['outputs']={p.name:{'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'bytes':p.stat().st_size} for p in sorted(out.iterdir()) if p.is_file() and p.name!='manifest.json' and p.suffix in ('.html','.png','.svg','.pdf')}
    (out/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'report':str(out/'index.html'),'real_cases':len(cases),'real_images':sum(len(c['frames']) for c in cases),'scene_responses':sum(len(c['responses']) for c in cases),'synthetic_passed_prefixes':len(rounds),'source_files_hashed':len(sources)},ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,default=ROOT/'implementation/results/visual_report')
    parser.add_argument('--synthetic-run',default='runs/20261027T105505Z-8a812146')
    args=parser.parse_args();build(args.out,args.synthetic_run)
