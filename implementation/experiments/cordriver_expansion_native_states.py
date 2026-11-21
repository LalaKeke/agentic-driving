"""Recover the published linearized QP poses with native CUDA operations and archived controls.

Replays the frozen upstream forward algebra. QPFunction is short-circuited to the real archived
optimal controls. No solver, image network, annotation, recorded future or map is read.
"""
import hashlib
import importlib
import json
from pathlib import Path
from cordriver_paths import TOOLS, CHECK_PYTHON, CONFIG
import sys
from unittest.mock import patch

import numpy as np
import torch
from cordriver_guided_qp import NATIVE, PROJECT, SOURCE_SHA


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(front, out):
    sys.path.insert(0,str(TOOLS/'cordriver'))
    upstream=importlib.import_module(NATIVE)
    assert sha(Path(upstream.__file__))==SOURCE_SHA
    frontend=json.loads(front.read_bytes()); output=[]
    for row in frontend['samples']:
        rec=dict(token=row['token'],scene=row['scene_name'],solver=row.get('solver_status'),fallback=row['fallback'])
        output.append(rec)
        if not row.get('verified_qp_output') or row['fallback'] or row.get('solver_status')!='optimal':
            rec['status']='NO_VERIFIED_UPSTREAM_QP_RETURN';continue
        model=upstream.QPControl().cuda().eval();model.weight.data.fill_(frontend['qp_weight'])
        original=model.ego2lidar_traj
        def capture(rear):
            # These are the upstream forward's actual final locals immediately before it discards
            # heading and speed for the public two-coordinate return (upstream SHA checked above).
            scope=sys._getframe(1).f_locals
            x=(torch.bmm(scope['BB'],scope['sol_u'])+torch.bmm(scope['AA'],scope['x0'])+scope['gg']).reshape(1,12,4)
            assert torch.equal(rear,x[:,1::2,:2])
            rec['state_xy_yaw_speed']=torch.cat((scope['x0'].reshape(1,4),x[0]),dim=0).cpu().tolist()
            return original(rear)
        def archive(*args,**kwargs):
            def use(q,*args):
                return q.new_tensor(row['qp_controls_accel_steer']).reshape(1,-1)
            return use
        with torch.no_grad(),patch.object(upstream,'QPFunction',archive),patch.object(model,'ego2lidar_traj',capture):
            result=model(np.array(row['can_bus'],dtype=np.float32),None,
                torch.tensor([row['qp_reference_steps']],dtype=torch.float32,device='cuda'),torch.tensor([row['qp_corridor']],dtype=torch.float32,device='cuda'))
        returned=result[0].cumsum(0).cpu().numpy()
        expected=np.asarray(row['final_xy_lidar_m'])
        rec['replay_max_xy_delta_m']=float(np.max(np.linalg.norm(returned-expected,axis=1)))
        rec['status']='BOUND' if rec['replay_max_xy_delta_m']<=.0005 else 'BINDING_FAILED'
    data=dict(model_calls=0,solver_calls=0,torch_version=torch.__version__,gpu=torch.cuda.get_device_name(0),
        matmul_tf32=torch.backends.cuda.matmul.allow_tf32,cudnn_tf32=torch.backends.cudnn.allow_tf32,frontend_sha256=sha(front),
        source_sha256=SOURCE_SHA,tool_sha256=sha(Path(__file__)),samples=output)
    with out.open('x') as f:json.dump(data,f,indent=2,allow_nan=False);f.write('\n')
