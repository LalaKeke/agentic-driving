"""Offline casebook: official nuScenes rendering, archived tool I/O, synthetic plans.

Never imports an online adapter or calls T1-T4. GT stays in this reporting process.
"""
import argparse
import base64
from bisect import bisect_right
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
LIVE=ROOT/'implementation/results/gpt61_live'
SYNTHETIC=ROOT/'runs/20261027T110115Z-3ac5ecb5'
SPECS=[
 ('day_pedestrian_crossing','白天行人横穿','20261027T153851Z-7d14c064', [('初次规划',8,9,10),('反馈修订 1',11,12,13),('反馈修订 2',14,15,16)]),
 ('construction_obstacle','施工障碍与左弯','20261027T154925Z-b9869784', [('初次规划',8,9,10),('反馈修订 1',11,12,13)]),
 ('intersection_cross_traffic','路口横向车流','20261027T154925Z-b9869784', [('初次规划',20,21,22)]),
 ('night_pedestrian_crossing','夜间行人','20261027T154925Z-b9869784', []),
]
SIGNATURES={
 'Agent':'decide(capture, feedback) → (decision + hypotheses, provenance)',
 'T1':'construct(capture, agent_output, provenance, allow_synthetic) → metric_scene / Failure',
 'T2':'build(metric_scene) → time_space_corridor / Failure',
 'T3':'solve(metric_scene, corridor) → trajectory / Failure',
 'T4':'verify(metric_scene, trajectory) → verdict + violations + released_prefix',
 'Execute':'execute(metric_scene, trajectory, verification, now_s) → simulated_prefix_state',
}


def build(dataset,out):
    out.mkdir(parents=True,exist_ok=True)
    os.environ['MPLCONFIGDIR']=str(ROOT/'runs/visualization/nuscenes-mplconfig')
    os.environ['XDG_CACHE_HOME']=str(ROOT/'runs/visualization/nuscenes-cache')
    Path(os.environ['XDG_CACHE_HOME']).mkdir(parents=True,exist_ok=True)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    from matplotlib.patches import Polygon as Patch
    from matplotlib.backends.backend_pdf import PdfPages
    import numpy as np
    from nuscenes.nuscenes import NuScenes
    from nuscenes.utils.geometry_utils import view_points,transform_matrix
    from pyquaternion import Quaternion
    font=Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc')
    if font.exists():
        font_manager.fontManager.addfont(str(font));plt.rcParams['font.family']=font_manager.FontProperties(fname=font).get_name()
    plt.rcParams.update({'axes.unicode_minus':False,'font.size':10,'svg.fonttype':'path'})
    sources={};checks=[];figures=[]

    def track(path):
        path=Path(path);raw=path.read_bytes()
        try:key=str(path.relative_to(ROOT))
        except ValueError:key=str(path)
        sources[key]={'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)}
        return raw
    def read(path):return json.loads(track(path))
    def write(path,value):
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    def record(run,n,kind):return LIVE/'raw_runs'/run/f'{n:04d}-{kind}.json'
    def stage(name,inputs,outputs,status='recorded',source=None,note=''):
        return {'name':name,'signature':SIGNATURES[name],'input':inputs,'output':outputs,'status':status,'source':source,'note':note}
    def stopped(reason):return [stage(x,None,None,'not_called',note=reason) for x in ['T2','T3','T4','Execute']]
    def source(path):return str(Path(path).relative_to(ROOT))
    def real_phase(run,label,ci,ai,fi,feedback_input=None):
        cp,ap,fp=record(run,ci,'capture'),record(run,ai,'agent'),record(run,fi,'feedback')
        cap,agent,failure=read(cp),read(ap),read(fp)
        return {'label':label,'stages':[
            stage('Agent',{'capture':cap,'feedback':feedback_input},agent,source=source(ap)),
            stage('T1',{'capture':cap,'agent_output':agent['output'],'provenance':agent['provenance'],'allow_synthetic':False},failure,'raised_failure',source(fp))
        ]+stopped('T1 失败，原运行没有调用下游工具；没有对应输入或输出。')}

    config=read(ROOT/'implementation/experiments/nuscenes/config.json')
    review=read(LIVE/'offline_review/auxiliary_association_review.json')
    g2=read(LIVE/'g2/g2_original_exploratory_auxiliary.json')
    index=read(LIVE/'response_index.json')
    captures={}
    for p in sorted((LIVE/'raw_runs').glob('*/*capture.json')):
        cap=read(p);captures.setdefault(cap['metadata']['scene_id'].removeprefix('nusc_'),cap)
    # This object is OFFLINE ONLY; it may load annotations/maps. Never supplied to tools.
    nusc=NuScenes(version='v1.0-mini',dataroot=str(dataset),verbose=False)
    for name in nusc.table_names:
        p=dataset/'v1.0-mini'/(name+'.json')
        if p.exists():track(p)
    import nuscenes.nuscenes as sdk_module
    import nuscenes.utils.geometry_utils as sdk_geometry
    import nuscenes.utils.data_classes as sdk_classes
    sdk_hashes={m.__name__:{'file':str(Path(m.__file__)),'sha256':hashlib.sha256(Path(m.__file__).read_bytes()).hexdigest()} for m in [sdk_module,sdk_geometry,sdk_classes]}

    def transform_context(camera_token,offset):
        sd=nusc.get('sample_data',camera_token);ep=nusc.get('ego_pose',sd['ego_pose_token']);cal=nusc.get('calibrated_sensor',sd['calibrated_sensor_token'])
        poseR=Quaternion(ep['rotation']).rotation_matrix;yaw=math.atan2(poseR[1,0],poseR[0,0]);R=np.array([[math.cos(yaw),-math.sin(yaw)],[math.sin(yaw),math.cos(yaw)]])
        center=np.asarray(ep['translation'][:2])+R@np.array([offset,0])
        T=transform_matrix(cal['translation'],Quaternion(cal['rotation']),inverse=True)@transform_matrix(ep['translation'],Quaternion(ep['rotation']),inverse=True)
        return sd,ep,cal,T,center,R
    def project(points,context):
        sd,ep,cal,T,center,R=context
        points=np.asarray(points,float);camera=(T@np.vstack([points.T,np.ones(len(points))]))[:3]
        independent=((points-np.asarray(ep['translation']))@Quaternion(ep['rotation']).rotation_matrix-np.asarray(cal['translation']))@Quaternion(cal['rotation']).rotation_matrix
        err=float(np.max(np.abs(camera.T-independent)))
        assert err<1e-8
        uv=view_points(camera,np.array(cal['camera_intrinsic']),normalize=True)[:2].T
        # Independently check pinhole projection with positive depth, before viewport clipping.
        direct=(np.asarray(cal['camera_intrinsic'])@camera).T;direct=direct[:,:2]/direct[:,2:3]
        finite=np.isfinite(uv).all(axis=1)&np.isfinite(direct).all(axis=1)
        if finite.any():assert np.max(np.abs(uv[finite]-direct[finite]))<1e-7
        visible=(camera[2]>.5)&np.isfinite(uv).all(axis=1)&(uv[:,0]>=0)&(uv[:,0]<sd['width'])&(uv[:,1]>=0)&(uv[:,1]<sd['height'])
        local=(points[:,:2]-center)@R
        return {'world_xyz':points.tolist(),'ego_at_reference_xy':local.tolist(),'camera_xyz':camera.T.tolist(),'pixel_uv':uv.tolist(),'visible':visible.tolist(),'matrix_crosscheck_max_error_m':err}
    def actor_track(instance,t0):
        inst=nusc.get('instance',instance);ann=nusc.get('sample_annotation',inst['first_annotation_token']);anns=[]
        while True:
            stamp=nusc.get('sample',ann['sample_token'])['timestamp']*1e-6
            # Bottom center uses annotated height, not monocular or assumed ground recovery.
            p=list(ann['translation']);p[2]-=ann['size'][2]/2
            anns.append((stamp,p,ann['token']))
            if not ann['next']:break
            ann=nusc.get('sample_annotation',ann['next'])
        times=[a[0] for a in anns];xyz=[];ts=[];pairs=[]
        for k in range(26):
            t=t0+k*.2;i=min(max(bisect_right(times,t)-1,0),len(times)-2)
            if i<0 or not times[i]<=t<=times[i+1] or times[i+1]-times[i]>.75:continue
            u=(t-times[i])/(times[i+1]-times[i]);xyz.append(((1-u)*np.asarray(anns[i][1])+u*np.asarray(anns[i+1][1])).tolist());ts.append(k*.2);pairs.append([anns[i][2],anns[i+1][2]])
        assert len(xyz)==26,'Focal/auxiliary track must bracket the full configured window'
        return xyz,ts,pairs
    def save(fig,name):
        fig.savefig(out/(name+'.png'),dpi=150,bbox_inches='tight',facecolor='white')
        fig.savefig(out/(name+'.svg'),bbox_inches='tight',facecolor='white');fig._artifact_name=name;figures.append(fig)

    cases=[];projections={}
    for slug,title,run,phase_specs in SPECS:
        case=next(c for c in config['cases'] if c['case_id']==slug);sid=case['sample_token'];cap=captures[sid]
        frame=next(f for f in cap['observations'] if f['camera_id']=='CAM_FRONT' and f['capture_time_s']==cap['metadata']['reference_time_s'])
        actual_image=Path(nusc.get_sample_data_path(frame['id']));image_hash=hashlib.sha256(track(actual_image)).hexdigest()
        expected=[]
        for row in index:
            if row['sample_id']==sid:
                rec=read(LIVE/'raw_runs'/row['run']/row['file']);expected.append(rec['provenance']['image_sha256'][frame['id']])
        assert expected and all(h==image_hash for h in expected),'Rendered image differs from actual online input'
        ctx=transform_context(frame['id'],cap['vehicle']['center_to_rear_axle_m']);sd,ep,cal,T,center,R=ctx;t0=sd['timestamp']*1e-6
        assert abs(t0-cap['metadata']['reference_time_s'])<1e-6
        assert cal['camera_intrinsic']==cap['acquisition_metadata']['calibrations']['CAM_FRONT']['camera_intrinsic']
        focal=nusc.get('sample_annotation',case['focal_annotation'])['instance_token'];ids=[focal]+review['auxiliary_targets'].get(sid,[]);ids=list(dict.fromkeys(ids))
        fig,axes=plt.subplots(1,2,figsize=(16,6.6),gridspec_kw={'width_ratios':[1.45,1]})
        # Official renderer supplies the calibrated camera image; custom trajectories are an overlay.
        nusc.render_sample_data(frame['id'],with_anns=False,ax=axes[0],verbose=False)
        traces=[]
        colors=['#ffb53b','#28d4d7']
        for j,actor in enumerate(ids):
            xyz,ts,pairs=actor_track(actor,t0);pr=project(xyz,ctx);pr.update(instance=actor,relative_s=ts,annotation_pairs=pairs,kind='OFFLINE_GT_ANNOTATION_BOTTOM_CENTER_INTERPOLATION');traces.append(pr)
            uv=np.asarray(pr['pixel_uv']);vis=pr['visible'];color=colors[j];label='焦点目标 GT 未来' if j==0 else '辅助目标 GT 未来'
            labelled=False
            for k in range(len(uv)-1):
                if vis[k] and vis[k+1]:axes[0].plot(uv[k:k+2,0],uv[k:k+2,1],color=color,lw=2.6,label=label if not labelled else None);labelled=True
            visible_uv=uv[np.asarray(vis,bool)]
            if len(visible_uv) and np.max(np.ptp(visible_uv,axis=0))<24:
                q=visible_uv[0];axes[0].scatter(*q,color=color,s=20);axes[0].annotate('GT 0–5s',q,xytext=(5,-12),textcoords='offset points',color=color,fontsize=8)
            else:
                last_label=None
                for k in [0,5,10,15,20,25]:
                    if vis[k] and (last_label is None or np.linalg.norm(uv[k]-last_label)>28):
                        axes[0].scatter(*uv[k],color=color,s=15);axes[0].annotate(f'{ts[k]:.0f}s',uv[k],xytext=(3,-9),textcoords='offset points',color=color,fontsize=8);last_label=uv[k]
            loc=np.asarray(pr['ego_at_reference_xy']);axes[1].plot(loc[:,0],loc[:,1],color=color,lw=2.6,label=label)
            axes[1].scatter(*loc[0],marker='*',color=color,s=120)
        # Recorded future localization is reference-only, not the counterfactual effect of any plan.
        ego=[];ets=[];cur=sd
        while cur and cur['timestamp']<=sd['timestamp']+5e6:
            pose=nusc.get('ego_pose',cur['ego_pose_token']);Q=Quaternion(pose['rotation']).rotation_matrix;yaw=math.atan2(Q[1,0],Q[0,0]);off=cap['vehicle']['center_to_rear_axle_m'];pt=list(pose['translation']);pt[0]+=off*math.cos(yaw);pt[1]+=off*math.sin(yaw);ego.append(pt);ets.append((cur['timestamp']-sd['timestamp'])*1e-6);cur=nusc.get('sample_data',cur['next']) if cur['next'] else None
        epr=project(ego,ctx);epr.update(relative_s=ets,kind='OFFLINE_RECORDED_EGO_POSES_PLUS_RESEARCH_CENTER_OFFSET_1_2M')
        uv=np.asarray(epr['pixel_uv']);vis=epr['visible'];labelled=False
        for k in range(len(uv)-1):
            if vis[k] and vis[k+1]:axes[0].plot(uv[k:k+2,0],uv[k:k+2,1],color='#ef81f2',lw=2,ls='--',label='离线自车定位参考' if not labelled else None);labelled=True
        loc=np.asarray(epr['ego_at_reference_xy']);axes[1].plot(loc[:,0],loc[:,1],color='#b755bb',lw=2,ls='--',label='自车定位参考（1.2m 偏移）')
        plotted=[]
        for row in g2['rows']:
            if row['sample_id']!=sid:continue
            rr,rf=row['record_id'].split(':');rec=read(LIVE/'raw_runs'/rr/rf);mid=review['record_associations'][row['record_id']][row['actor_id']];obj=next(o for o in rec['output']['hypotheses']['objects'] if o['id']==mid);p=obj['position_m']
            plotted.append({'record_id':row['record_id'],'position_m':p,'model_object_id':mid,'position_error_m':row['position_error_m']})
            n=len(plotted);axes[1].scatter(*p,marker='x',color=['#2266de','#c04646'][(n-1)%2],s=100,lw=2,label=f'模型点位·调用{n}（未核验）');axes[1].annotate(f'调用{n}',p,xytext=(5,8 if n==1 else -13),textcoords='offset points',fontsize=8)
        axes[1].add_patch(Patch([[-2.3,-.9],[2.3,-.9],[2.3,.9],[-2.3,.9]],facecolor='#bcc8d6',alpha=.55));axes[1].scatter(0,0,color='#172b45',s=25,label='采集时刻自车中心参考')
        axes[1].set(xlim=(-5,35),ylim=(-12,16),xlabel='x 向前 (m)',ylabel='y 向左 (m)',title='局部俯视图：模型点位与离线参考轨迹（轴窗裁剪）')
        axes[1].set_aspect('equal',adjustable='box');axes[1].grid(alpha=.2);axes[1].legend(fontsize=8,loc='lower right')
        axes[0].set_title('官方 render_sample_data + view_points 叠加\n所有曲线均为离线 GT / 定位参考，不是模型规划',fontsize=11)
        handles,labels=axes[0].get_legend_handles_labels()
        if handles:axes[0].legend(fontsize=8,loc='upper right')
        fig.suptitle(f'{title} · 真实图像 / GPT-6.1 · 模型轨迹未生成',fontsize=17)
        fig.text(.02,.025,'橙/青：目标底部中心 GT 插值；紫：未来自车定位参考。模型仅提供 XY，无可信高度/地面平面，故点位只画在 BEV，不强行投影至图像。',fontsize=10)
        fig.tight_layout(rect=(0,.08,1,.92));save(fig,slug)
        projections[slug]={'camera_token':frame['id'],'rendered_image_sha256':image_hash,'online_input_image_sha256_verified':True,'reference_time_s':t0,'intrinsics':cal['camera_intrinsic'],'world_to_camera':T.tolist(),'target_tracks':traces,'ego_reference':epr,'model_points_bev_only':plotted,'runtime_use':False,'near_depth_clip_m':0.5,'window_s':5.0,'target_interpolation_step_s':0.2,'visibility_mask':'Depth and viewport only, no occlusion visibility inferred','future_ego_reference':'Recorded localization; not the consequence of executing a planned counterfactual prefix'}
        phases=[];prior=None
        for label,ci,ai,fi in phase_specs:
            phases.append(real_phase(run,label,ci,ai,fi,prior));prior=phases[-1]['stages'][1]['output']
        if not phases:
            cap0=read(record(run,28,'capture'));case_result=read(record(run,29,'case-result'))
            phases.append({'label':'原协议：线上超时','stages':[stage('Agent',{'capture':cap0,'feedback':None},case_result['reason'],'raised_failure',source(record(run,29,'case-result'))),stage('T1',None,None,'not_called',note='模型未返回，本轮 T1 未调用。')]+stopped('模型超时，未调用工具。')})
            nr='20261027T161613Z-a58e5a49';probe_path=LIVE/'raw_runs'/nr/'0005-model-record.json';rec=read(probe_path);nc=read(LIVE/'raw_runs'/nr/'0004-capture.json');fail=read(LIVE/'g2/contract21_night_t1_result.json')
            phases.append({'label':'独立 2.1：格式复核与离线 T1','stages':[stage('Agent',{'capture':nc,'feedback':None},rec,source=source(probe_path)),stage('T1',{'capture':nc,'agent_output':rec['output'],'provenance':rec['provenance'],'allow_synthetic':False},fail,'offline_replay',source(LIVE/'g2/contract21_night_t1_result.json'),note='独立契约的离线复核，未进行在线 T2–T4。')]+stopped('独立 2.1 离线 T1 复核失败。')})
        cases.append({'id':slug,'title':title,'source_kind':'online_model','sample_id':sid,'figure':slug+'.png','phases':phases,'note':'真实模型均未生成或放行轨迹。图像未来曲线来自离线真值与定位记录，不进入工具输入。'})

    # Extract full tool calls from the later corrected artificial run. Original run untouched.
    packets=[];packet=None
    for p in sorted(SYNTHETIC.glob('*.json')):
        name=p.stem.split('-',1)[1];obj=read(p)
        if name=='capture':packet={'capture':obj,'paths':{'capture':source(p)}}
        elif name in ['agent','T1','T2','T3','T4'] and packet is not None:
            packet[name]=obj;packet['paths'][name]=source(p)
            if name=='T4':packets.append(packet)
        elif name=='simulation':packets[-1]['simulation']=obj;packets[-1]['paths']['simulation']=source(p)
        elif name=='feedback' and packets:packets[-1]['feedback']=obj
    rejected=next(p for p in packets if p['T4']['verdict']=='FAIL');brake=next(p for p in packets if p['T4']['verdict']=='PASS');resume=next(p for p in packets if p['T1']['decision']['behavior']=='resume')
    def synthetic_phase(p,label,feedback):
        scene=p['T1'];tr=p['T3'];stages=[stage('Agent',{'capture':p['capture'],'feedback':feedback},p['agent'],source=p['paths']['agent']),stage('T1',{'capture':p['capture'],'agent_output':p['agent']['output'],'provenance':p['agent']['provenance'],'allow_synthetic':True},scene,source=p['paths']['T1']),stage('T2',{'metric_scene':scene},p['T2'],source=p['paths']['T2']),stage('T3',{'metric_scene':scene,'corridor':p['T2']},tr,source=p['paths']['T3'],note='注入后匀速轨迹；solver 状态/目标值来自注入前求解，不能解释为当前候选已可行。' if 'fault_injection' in tr['solver'] else ''),stage('T4',{'metric_scene':scene,'trajectory':tr},p['T4'],source=p['paths']['T4'])]
        if 'simulation' in p:stages.append(stage('Execute',{'metric_scene':scene,'trajectory':tr,'verification':p['T4'],'now_s':scene['metadata']['reference_time_s']},p['simulation'],source=p['paths']['simulation']))
        else:stages.append(stage('Execute',None,None,'not_called',note='核验失败；释放 0s，不执行。'))
        return {'label':label,'stages':stages}
    for slug,title,p,phases in [('synthetic_stop','人工案例：拒绝、修订与停车',brake,[synthetic_phase(rejected,'故障注入：T4 FAIL',None),synthetic_phase(brake,'修订后：T4 PASS',rejected['feedback'])]),('synthetic_resume','人工案例：新观测后恢复行驶',resume,[synthetic_phase(resume,'恢复行驶：T4 PASS',None)])]:
        fig,axes=plt.subplots(1,2,figsize=(15,6.2),gridspec_kw={'width_ratios':[1.4,1]});scene=p['T1'];corr=p['T2'];tr=p['T3'];road=scene['hypotheses']['road']['polygon_m'];axes[0].add_patch(Patch(road,color='#e0e7ef'))
        axes[0].add_patch(Patch(corr['cells'][0]['polygon_m'],facecolor='#c8eff1',edgecolor='#4095a4',alpha=.8,label='首区间后轴参考点走廊'))
        actor=scene['hypotheses']['objects'][0]
        for k in [0,5,10,15,20,24]:
            for poly in actor['occupancy'][k]['alternatives_m']:axes[0].add_patch(Patch(poly,facecolor='#f2b452',edgecolor='#b3741b',alpha=.4))
        for pp,color,label in ([(rejected,'#ca424c','被拒匀速候选')] if slug=='synthetic_stop' else [])+[(p,'#218964','已核验规划（5s）')]:
            pts=pp['T3']['points'];axes[0].plot([v['x_m'] for v in pts],[v['y_m'] for v in pts],color=color,lw=2,label=label,ls='--' if pp is rejected else '-')
        if scene['decision']['stop']:
            axes[0].axvline(scene['decision']['stop']['center_x_max_m'],color='#218964',ls=':',label='修订后停车中心上限')
        pts=tr['points'];axes[0].plot([q['x_m'] for q in pts[:2]],[q['y_m'] for q in pts[:2]],color='#218964',lw=7,label='仅释放 0.2s 前缀')
        veh=scene['capture']['vehicle'];axes[0].add_patch(Patch([[-veh['rear_m'],-veh['half_width_m']],[veh['front_m'],-veh['half_width_m']],[veh['front_m'],veh['half_width_m']],[-veh['rear_m'],veh['half_width_m']]],color='#253b58',alpha=.7))
        axes[0].set(xlim=(-8,32),ylim=(-8,12),xlabel='x 向前 (m)',ylabel='y 向左 (m)',title='人工米制场景：规划直接叠加在场景图上');axes[0].set_aspect('equal');axes[0].grid(alpha=.2);axes[0].legend(fontsize=8,loc='upper right')
        for pp,color,label in [(p,'#218964','修订后速度' if slug=='synthetic_stop' else '恢复规划速度')]+([(rejected,'#ca424c','被拒匀速速度')] if slug=='synthetic_stop' else []):
            ps=pp['T3']['points'];axes[1].plot([x['time_s'] for x in ps],[x['speed_mps'] for x in ps],color=color,label=label)
        axes[1].axvspan(0,.2,color='#218964',alpha=.15);axes[1].set(xlabel='规划相对时间 (s)',ylabel='速度 (m/s)',title=f"T4 {p['T4']['verdict']} · 仅释放 {p['T4']['released_prefix_s']}s");axes[1].legend(fontsize=9);axes[1].grid(alpha=.2)
        fig.suptitle(title+' · 人工测试 / 脚本智能体 / 模拟执行',fontsize=16);fig.text(.02,.02,'橙色多边形为脚本提供的整区间占用包络；浅青为后轴配置空间。该人工场景无真实相机，不将其轨迹叠加到不对应的 nuScenes 图像。',fontsize=10);fig.tight_layout(rect=(0,.07,1,.92));save(fig,slug)
        cases.append({'id':slug,'title':title,'source_kind':'synthetic_fixture','sample_id':None,'figure':slug+'.png','phases':phases,'note':'使用后续修正了相对停车截止时间的已验收运行 20261027T110115Z-3ac5ecb5；不是你此前打开的近停未停稳运行，不覆盖历史记录。'})

    passed=[p for p in packets if 'simulation' in p];t0=passed[0]['T1']['metadata']['reference_time_s'];times=[p['T1']['metadata']['reference_time_s']-t0 for p in passed];speeds=[p['capture']['ego']['speed_mps'] for p in passed];times+=[times[-1]+passed[-1]['simulation']['elapsed_s']];speeds+=[passed[-1]['simulation']['speed_mps']]
    fig,ax=plt.subplots(figsize=(12,4.6));ax.plot(times,speeds,'o-',color='#2864dc',ms=4);ax.axvline(4.2,color='#a65e11',ls='--',label='脚本新观测：行人已清空，恢复行驶');ax.set(xlabel='累计模拟时间 (s)',ylabel='已执行前缀后的速度 (m/s)',title='完整人工闭环：26 次 0.2s 前缀，停稳后恢复');ax.grid(alpha=.2);ax.legend();fig.tight_layout();save(fig,'synthetic_closed_loop')
    summary={'real_cases':4,'synthetic_case_views':2,'synthetic_run':str(SYNTHETIC.relative_to(ROOT)),'synthetic_run_recorded_dirty':read(SYNTHETIC/'0002-manifest.json')['code_dirty'],'synthetic_executed_prefixes':len(passed),'synthetic_min_speed_mps':min(speeds),'synthetic_final_speed_mps':speeds[-1],'real_released_prefixes':0,'official_devkit_version':importlib.metadata.version('nuscenes-devkit'),'official_functions_used':['NuScenes.render_sample_data','geometry_utils.view_points','geometry_utils.transform_matrix'],'no_new_model_calls':True,'no_tool_calls_during_render':True,'gt_only_offline':True,'input_bundle_scope':'Tool arguments reconstructed from unmodified saved capture/agent/upstream outputs; not a new function-level trace','original_synthetic_source_scope':'Historical run recorded code_dirty=true; source-hash ledger retained, not relabelled as a clean-code experiment'}
    for c in cases:
        for j,phase in enumerate(c['phases']):
            for st in phase['stages']:
                relative=f"io/{c['id']}/{j:02d}-{st['name']}.json";write(out/relative,st);st['download']=relative
    write(out/'projection_audit.json',projections);write(out/'casebook_data.json',{'summary':summary,'cases':cases})
    # Add a readable I/O summary page for each case, in addition to exact JSON.
    def short_io(st):
        i,o=st['input'],st['output'];name=st['name']
        if st['status']=='not_called':return '未调用；输入不存在','未调用；输出不存在'
        if name=='Agent':
            feedback=i.get('feedback');inp=f"{len(i['capture']['observations'])} 帧；自车 {i['capture']['ego']['speed_mps']:.2f}m/s；反馈 "+(feedback.get('code',feedback.get('category','有')) if feedback else '无')
            if st['status']=='raised_failure':return inp,o['code']+'（没有有效响应）'
            h=o['output']['hypotheses'];d=o['output']['decision'];return inp,f"{d['behavior']}；期望 {d['desired_speed_mps']}m/s；无界未知={h['unbounded_unknown']}"
        if name=='T1':
            inp=f"采集修订 {i['capture']['metadata']['revision']}；模型假设 {len(i['agent_output']['hypotheses']['objects'])} 个目标；来源 {i['provenance']['source']}"
            if st['status'] in ['raised_failure','offline_replay']:
                f=o.get('failure',o);return inp,f['code']+'；'+('独立离线复核' if st['status']=='offline_replay' else f['category'])
            return inp,f"米制 scene {o['metadata']['schema_version']}；道路 {len(o['hypotheses']['road']['polygon_m'])} 顶点；未来 25 区间"
        if name=='T2':return '米制场景 + 车辆外形 + 安全间距',f"{len(o['cells'])} 个整区间单元；参考 rear_axle；车身圆半径 {o['body_radius_m']:.3f}m"
        if name=='T3':return f"米制场景 + {len(i['corridor']['cells'])} 个走廊单元；自行车模型",f"{len(o['points'])} 点 / 5s；"+('注入后匀速候选（非原求解解）' if o['solver'].get('fault_injection') else o['solver']['status'])
        if name=='T4':return f"原始米制场景 + {len(i['trajectory']['points'])} 点轨迹；独立扫掠检查",o['verdict']+f"；释放 {o['released_prefix_s']}s；"+(','.join(sorted({v['code'] for v in o['violations']})) if o['violations'] else f"最小净空下界 {o['minimum_clearance_lower_bound_m']:.3f}m")
        return '已核验场景、轨迹、T4、原观测时刻',f"模拟 {o['elapsed_s']}s；中心位移 {o['center_displacement_m'][0]:.3f}m；速度 {o['speed_mps']:.3f}m/s"
    def wrap(text,width=28):
        # Fixed-width Chinese/ASCII splitting makes PDF summaries legible.
        lines=[];line='';units=0
        for char in str(text):
            size=2 if ord(char)>255 else 1
            if units+size>width:lines.append(line);line='';units=0
            line+=char;units+=size
        if line:lines.append(line)
        return '\n'.join(lines)
    for c in cases:
        rows=[]
        for phase in c['phases']:
            for st in phase['stages']:
                inp,op=short_io(st);rows.append([wrap(phase['label']+' / '+st['name'],25),wrap(inp,48),wrap(op,54),Path(st['source']).name if st['source'] else 'NOT_CALLED'])
        fig,ax=plt.subplots(figsize=(16, max(7,len(rows)*.52+1.8)));ax.axis('off')
        table=ax.table(cellText=rows,colLabels=['调用环节 / 轮次','输入摘要（完整 JSON 在案例集）','输出 / 失败反馈摘要','来源记录'],colWidths=[.2,.3,.34,.16],cellLoc='left',loc='center',bbox=[0,.05,1,.88]);table.auto_set_font_size(False);table.set_fontsize(8.5)
        for (r,col),cell in table.get_celld().items():
            cell.set_edgecolor('#dce4ef');cell.set_facecolor('#e7eff9' if r==0 else ('#ffffff' if r%2 else '#f5f8fc'))
        fig.suptitle(c['title']+' · 实际调用与工具 I/O 摘要',fontsize=16,y=.98)
        fig.text(.03,.025,'未调用的数据保留为空；输入集合由原记录组成。真实模型不经过失败的 T1 继续调用下游；人工 PASS 仅对其场景假设成立。',fontsize=10)
        save(fig,c['id']+'_calls')
    by_name={fig._artifact_name:fig for fig in figures}
    ordered=[by_name[name] for c in cases for name in [c['id'],c['id']+'_calls']]+[by_name['synthetic_closed_loop']]
    with PdfPages(out/'casebook.pdf') as pdf:
        for fig in ordered:pdf.savefig(fig,bbox_inches='tight');plt.close(fig)
    payload={'summary':summary,'cases':cases,'signatures':SIGNATURES}
    for c in cases:c['image']='data:image/png;base64,'+base64.b64encode((out/c['figure']).read_bytes()).decode()
    payload['closed_loop_image']='data:image/png;base64,'+base64.b64encode((out/'synthetic_closed_loop.png').read_bytes()).decode()
    template=Path(__file__).with_name('casebook.html').read_text();encoded=json.dumps(payload,ensure_ascii=False,separators=(',',':')).replace('<','\\u003c').replace('\u2028','\\u2028').replace('\u2029','\\u2029');(out/'index.html').write_text(template.replace('__CASEBOOK_DATA__',encoded))
    for p in [Path(__file__),Path(__file__).with_name('casebook.html')]:track(p)
    write(out/'manifest.json',{'summary':summary,'inputs':sources,'official_sdk_source_hashes':sdk_hashes,'projection_checks':[{'case':slug,'max_error_m':max([t['matrix_crosscheck_max_error_m'] for t in p['target_tracks']]+[p['ego_reference']['matrix_crosscheck_max_error_m']])} for slug,p in projections.items()],'libraries':{name:importlib.metadata.version(name) for name in ['nuscenes-devkit','matplotlib','numpy','Pillow','pyquaternion']}})
    print(json.dumps(summary,ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--dataset',type=Path,default=Path('/data/nuscenes_dataset'));parser.add_argument('--out',type=Path,default=ROOT/'implementation/results/casebook');args=parser.parse_args();build(args.dataset,args.out)
