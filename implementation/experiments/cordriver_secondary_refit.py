"""Secondary predicted-edge refit and native progress veto from the frozen 99-frame dispatch."""
from dataclasses import replace
from pathlib import Path
import numpy as np
from shapely.geometry import mapping
import cordriver_clearance_refit as v1
import cordriver_reachable_options as menu
import cordriver_expansion_plan as legacy
from cordriver_edge_gate import native_body_knots,edge_clearance
read,sha,save=menu.load,menu.sha,menu.save
core=v1.core

def refit(out, c, study, data):
    token=c['token']
    folder=out/'refits'/token; folder.mkdir(exist_ok=False,parents=True)
    source=out/'menus'/token/'portfolio.json'; prior=read(source)
    floor_path=study/'planning/diagnostic'/token/'result.json'; floor_r=read(floor_path)
    assert prior['token']==token==floor_r['token']
    ver=dict(token=token,scene=c['scene'],status='RETAIN_NATIVE',selected_path=None,selected_sha256=None,attempts=[],
             floor= floor_r['fixed_clearance_knot_dense_m'],menu_path=str(source),menu_sha256=sha(source),implementation_sha256=sha(Path(__file__)))
    def finish(reason):
        ver['reason']=reason
        save(folder/'result.json',ver)
        return {k:ver[k] for k in ('token','scene','status','selected_path','selected_sha256','reason','menu_path','menu_sha256')}
    if not prior['checks']:
        return finish('NO_NUMERIC_SEED: '+','.join(prior['reasons']))
    sf=study/'frontend'/token/'sample.json'; vf=sf.with_name('visual_map.json'); pf=study/'packets'/token/'packet.json'
    ap=(study/'frontend').resolve().parent/'native_states.json'; front=study/'frontend/frontend.json'
    af=study/'secondary_primary'/(token+'.json'); can=(data/'can_bus')/(c['scene']+'_pose.json')
    for f in (sf,vf,pf,af,front,ap,floor_path,can):
        if sha(f)!=prior['source_sha256'][str(f.resolve())]:
            raise ValueError(f'source changed {f}')
    if sha(af)!=c['secondary_sha256']:
        raise ValueError('Frozen SparseDrive record mismatch')
    sample,visual,ctx,aux=read(sf),read(vf),read(pf)['model_context'],read(af)
    binding,=[r for r in read(ap)['samples'] if r['token']==token]
    poses,_=native_body_knots(sample,binding)
    if poses is None:
        return finish('NATIVE_POSES_UNAVAILABLE')
    old = [v1.receipt_entry(chk,poses) for chk in prior['checks']]
    first=min(old,key=lambda r:(not r['eligible'],r['dev'],r['method']))
    other=min(old,key=lambda r:(not r['positive_road'],-r['clearance'],r['dev'],r['method']))
    seeds=[first] if first['method']==other['method'] else [first,other]
    ver['seeds']=seeds
    geometry,_=legacy.derive(can,sample)
    road=menu.road_for(ctx,visual['map_features'],legacy); secondary=menu.road_for(ctx,aux['features'],legacy)
    search=replace(road,edges={**{('primary',k):e for k,e in road.edges.items()},
                               **{('secondary',k):e for k,e in secondary.edges.items()}})
    native_edge=edge_clearance(poses,aux['features']); primary_before=edge_clearance(poses,visual['map_features'])
    if native_edge != c['clearance'] or not native_edge['triggered'] or primary_before['triggered']:
        raise ValueError('SECONDARY_TRIGGER_CHANGED')
    ver.update(primary_before=primary_before,secondary_before=native_edge)
    p,mat=core.compose_native(sample,read(front)['qp_weight'],'cpu'); tr=core.Trial(p,mat,sample,visual)
    legacy.bind_initial_assumption(tr,geometry); raw=tr.m['rect'].copy()
    road_json=dict(support=mapping(road.support),edges=[mapping(v.line) for v in road.edges.values()],
                   area_m2=road.support.area,edge_ranks=list(road.edges))
    save(folder/'road.json',road_json); assert read(folder/'road.json')==read(source.with_name('road.json'))
    minimum=max([x for x in ver['floor'] if x is not None] or [0.])
    for i,seed in enumerate(seeds):
        prep=read(seed['source_path']); rect=np.asarray(prep['rectangles_ego_body_xy_yaw_l_w']); u0=np.asarray(prep['controls_accel_steer']).ravel()
        for m in v1.MARGINS:
            method=f'seed{i+1}_curb{round(100*m):02d}'
            u,stats=v1.refit(tr,u0,rect,search,poses,minimum,m)
            plan,rec,checks,nodes,margin=menu.certify(tr,u,method,ctx,folder,raw,rect,road,secondary,geometry,ver['floor'],vf,
                dict(search=stats,seed_sha256=seed['sha256'],seed_source=seed['source_path']))
            pose=np.column_stack((np.asarray(plan['body_xy_m'])[::2],np.asarray(plan['state_xy_yaw_v'])[::2,2]))
            pri=edge_clearance(pose,visual['map_features']); sec=edge_clearance(pose,aux['features'])
            receipt=folder/method/'receipt.json'
            ver['attempts'].append(dict(method=method,eligible=all(checks.values()),checks=checks,search=stats,primary=pri,secondary=sec,
                smooth_margin=margin,nodes=nodes,dev=v1.deviation(plan,poses),candidate_path=rec['candidate_path'],candidate_sha256=rec['sha256'],
                receipt_path=str(receipt),receipt_sha256=sha(receipt)))
    allowed=[a for a in ver['attempts'] if a['eligible'] and not a['primary']['triggered'] and
             a['secondary']['min_clearance_lower_bound_m']>=native_edge['min_clearance_lower_bound_m']+.10]
    if not allowed:
        return finish('NO_VERIFIED_SECONDARY_IMPROVEMENT_WITH_PRIMARY_NONTRIGGER')
    sel=min(allowed,key=lambda a:(a['secondary']['triggered'],a['dev'] if not a['secondary']['triggered'] else -a['secondary']['min_clearance_lower_bound_m'],a['dev'],a['method']))
    ver.update(status='RELEASED',selected_path=sel['candidate_path'],selected_sha256=sel['candidate_sha256'],selected=sel)
    return finish('VERIFIED_SECONDARY_IMPROVEMENT_WITH_PRIMARY_NONTRIGGER')
