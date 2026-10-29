"""Display exact ego integration from a saved-real-output curved offline replay.

No model, GT, re-planning or actor interpolation. All original source polygons
are drawn; the BEV viewport is cropped only for legibility.
"""
import argparse
import json
import math
import os
from pathlib import Path
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'implementation'))
from agentic.evidence_io import check_new_destination

def integrate(q,a,delta,t,v):
    # Same exact constant-acceleration/constant-rear-curvature display formula
    # as geometry.integrate; kept dependency-free because the plotting Python
    # has matplotlib but does not have the planner environment's Shapely.
    k=math.tan(delta)/v['wheelbase_m'];s=q[3]*t+.5*a*t*t;z=k*s/2
    sinc=math.sin(z)/z if abs(z)>1e-10 else 1-z*z/6
    return [q[0]+s*sinc*math.cos(q[2]+z),q[1]+s*sinc*math.sin(q[2]+z),q[2]+2*z,q[3]+a*t]


def center_state(q,v):
    d=v['center_to_rear_axle_m']
    return [q[0]+d*math.cos(q[2]),q[1]+d*math.sin(q[2]),q[2],q[3]]


def read_result(path):
    path=Path(path)
    return json.loads((path/'round_result.json' if path.is_dir() else path).read_text())


def dense(result):
    """Exact declared constant-control ego motion, for display only."""
    points=result['trajectory']['points'];vehicle=result['scene']['capture']['vehicle'];rows=[]
    for p,b in zip(points,points[1:]):
        q=[p[n] for n in ('rear_x_m','rear_y_m','yaw_rad','speed_mps')]
        dt=b['time_s']-p['time_s']
        for j in range(21):
            tau=dt*j/20
            state=integrate(q,p['acceleration_mps2'],p['steer_rad'],tau,vehicle)
            c=center_state(state,vehicle)
            rows.append([p['time_s']+tau,c[0],c[1],state[2],state[3]])
    return rows


def render(run,baseline,out):
    out=check_new_destination(out);out.parent.mkdir(parents=True,exist_ok=True)
    new=read_result(run);old=read_result(baseline)
    if new.get('fresh_online_model_call') is True:
        raise ValueError('This figure is specifically labelled OFFLINE REPLANNING.')
    scene=new['scene'];cap=scene['capture'];native=new['agent_output']['hypotheses']
    trajectory=new['trajectory'];verification=new['verification'];execution=new['execution']
    new_points=dense(new);old_points=dense(old)
    with tempfile.TemporaryDirectory(prefix='bend-result-mpl-') as cache:
        os.environ['MPLCONFIGDIR']=cache
        import numpy as np
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        from matplotlib.patches import Polygon as Patch
        from PIL import Image
        a=np.asarray(new_points);b=np.asarray(old_points);prefix=execution['elapsed_s']
        fig,axes=plt.subplots(2,2,figsize=(16,10.5));front,bev,speed,text=axes.flat
        frame=max((f for f in cap['observations'] if f['camera_id']=='CAM_FRONT'),key=lambda f:f['capture_time_s'])
        original_image=Image.open(frame['path'])
        front.imshow(original_image);front.axis('off')
        front.set_title('Original front image / display-only ground projection')
        cal=cap['acquisition_metadata']['calibrations']['CAM_FRONT']
        def project(arr):
            xyz=np.vstack((arr[:,1],arr[:,2],np.zeros(len(arr))))
            camera=np.asarray(cal['camera_to_center_rotation']).T@(xyz-np.asarray(cal['camera_to_center_translation_m'])[:,None])
            uv=np.asarray(cal['camera_intrinsic'])@camera
            mask=camera[2]>.3
            return uv[0]/uv[2],uv[1]/uv[2],mask
        for arr,color,label,lw in ((b,'#777777','Original straight plan',2),(a,'#1767c0','Curved replan',3),(a[a[:,0]<=prefix+1e-9],'#00a967','Consumed 0.2s prefix',5)):
            x,y,mask=project(arr);front.plot(np.where(mask,x,np.nan),np.where(mask,y,np.nan),color=color,lw=lw,label=label)
        # Projection outside the field of view must not autoscale the original
        # image away. This viewport clipping changes no model or trajectory data.
        front.set_xlim(0,original_image.width);front.set_ylim(original_image.height,0)
        front.legend(loc='upper left',fontsize=8)
        road=scene['hypotheses']['road']['polygon_m'];bev.add_patch(Patch(road,fc='#e7edf2',ec='#6d7f91',lw=1.3,label='Model road'))
        seen=set()
        for actor in native['objects']:
            for source in actor.get('occupancy') or []:
                for alt in source['alternatives']:
                    key=json.dumps(alt['polygon_m'])
                    if key not in seen:
                        bev.add_patch(Patch(alt['polygon_m'],fc='#e1baba',ec='#ba8c8c',alpha=.15,lw=.6))
                        seen.add(key)
            x,y=actor['position_m'];body=actor['body_geometry'];co,si=math.cos(body['yaw_rad']),math.sin(body['yaw_rad'])
            corners=[[x+co*dx-si*dy,y+si*dx+co*dy] for dx,dy in [(-body['length_m']/2,-body['width_m']/2),(body['length_m']/2,-body['width_m']/2),(body['length_m']/2,body['width_m']/2),(-body['length_m']/2,body['width_m']/2)]]
            bev.add_patch(Patch(corners,fc='#c17f73',ec='#8f5048',alpha=.45))
        vehicle=cap['vehicle']
        bev.add_patch(Patch([[-vehicle['rear_m'],-vehicle['half_width_m']],[vehicle['front_m'],-vehicle['half_width_m']],[vehicle['front_m'],vehicle['half_width_m']],[-vehicle['rear_m'],vehicle['half_width_m']]],fc='#24334c',alpha=.25,label='Initial full ego BODY'))
        for arr,color,label,lw in ((b,'#777777','Original straight plan',2),(a,'#1767c0','Curved replan',3),(a[a[:,0]<=prefix+1e-9],'#00a967','Consumed prefix',5)):
            bev.plot(arr[:,1],arr[:,2],color=color,lw=lw,label=label)
        bev.scatter([execution['center_displacement_m'][0]],[execution['center_displacement_m'][1]],c='#00a967',s=50,zorder=10)
        bev.set_xlim(min(-vehicle['rear_m']-.4,a[:,1].min()-.4),max(vehicle['front_m']+.4,a[:,1].max()+.6))
        bev.set_ylim(min(-vehicle['half_width_m']-.35,a[:,2].min()-.5),max(vehicle['half_width_m']+.35,a[:,2].max()+.5))
        bev.set_aspect('equal',adjustable='box');bev.set_xlabel('Fixed reference X forward (m)');bev.set_ylabel('Y left (m)')
        bev.set_title('Local BEV zoom / original polygons retained, display clipped');bev.grid(alpha=.18);bev.legend(fontsize=8,loc='upper left')
        speed.plot(b[:,0],b[:,4],c='#777777',lw=2,label='Original speed')
        speed.plot(a[:,0],a[:,4],c='#1767c0',lw=2,label='Curved replan speed');speed.axvspan(0,prefix,color='#00a967',alpha=.14)
        speed.scatter([prefix],[execution['speed_mps']],c='#00a967',s=45,label='Consumed state')
        speed.set_xlabel('Time (s)');speed.set_ylabel('Speed (m/s)');speed.grid(alpha=.2);speed.legend(fontsize=9)
        text.axis('off');p0,p1=trajectory['points'][0],trajectory['points'][-1]
        yaw=(p1['yaw_rad']-p0['yaw_rad'])*180/math.pi
        prefix_yaw=execution['yaw_delta_rad']*180/math.pi
        lines=[
            'REAL MODEL SAVED OUTPUT / OFFLINE REPLANNING',
            'Not a fresh online call or full-bend completion.',
            f"Decision: {scene['decision']['behavior']} | unchanged native scene",
            f"Planned heading change: {yaw:+.3f} deg over 2 s",
            f"Planned lateral endpoint: {p1['y_m']:+.4f} m",
            f"T4: {verification['verdict']} | violations: {len(verification['violations'])}",
            f"Conditional clearance lower bound: {verification['minimum_clearance_lower_bound_m']:.4f} m",
            '',f"SIMULATED prefix consumed: {prefix:.1f} s",
            f"Displacement XY: ({execution['center_displacement_m'][0]:.4f}, {execution['center_displacement_m'][1]:+.4f}) m",
            f"Consumed heading change: {prefix_yaw:+.3f} deg",
            f"Speed after consumption: {execution['speed_mps']:.4f} m/s",
            f"Independent recheck: {'PASS' if new.get('acceptance',{}).get('software_success') else 'see receipt'} (saved-source replay)",
            '', 'Only the green prefix was consumed; blue tail is a plan.',
            'All BODY/source occupancies and adverse modes stay unchanged.',
            'No GT, new observation or real vehicle control.'
        ]
        text.text(0,1,'\n'.join(lines),va='top',fontsize=10.5,linespacing=1.45)
        fig.suptitle('CURVED SHORT-PREFIX PROTOTYPE / SAVED REAL MODEL / OFFLINE REPLANNING',fontsize=16,fontweight='bold')
        fig.text(.5,.024,'Old v1 model note/scope contains a stale circumscribed-disk rejection instruction. This text conflict remains disclosed; no model re-approval is claimed.',ha='center',fontsize=9)
        fig.text(.5,.008,'Front-view lines use calibrated display-only z=0 projection, not GT or perception. BEV uses complete original BODY and occupancy polygons, cropped only in the viewport.',ha='center',fontsize=8.5)
        fig.tight_layout(rect=(0,.06,1,.95));fig.savefig(out,dpi=180);plt.close(fig)
    print(json.dumps(dict(out=str(out),T4=verification['verdict'],mode='SAVED_REAL_OUTPUT_OFFLINE_REPLANNING',new_model_calls=0)))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--run',required=True,type=Path)
    p.add_argument('--baseline',type=Path,default=ROOT/'implementation/results/scene_expansion_20261029_v1/runtime/neighbor_bend/round_result.json')
    p.add_argument('--out',required=True,type=Path);args=p.parse_args();render(args.run,args.baseline,args.out)
