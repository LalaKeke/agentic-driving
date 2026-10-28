"""Strict v2 contracts; v1 fields retain their original meaning."""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
from jsonschema import Draft7Validator

ROOT=Path(__file__).resolve().parents[1]
PROFILE='convex_disk_bicycle_v2'
class Failure(Exception):
    def __init__(self, code, message, category='interface_error', **details):
        super().__init__(message)
        self.record=dict(code=code,message=message,category=category,details=details,
                         repair_request='Revise decision or evidence-backed hypotheses; never relax safety silently.')
def need(ok,code,message,category='interface_error',**kw):
    if not ok: raise Failure(code,message,category,**kw)
def finite(x):
    if isinstance(x,float):need(math.isfinite(x),'NONFINITE','Nonfinite number')
    elif isinstance(x,dict):
        for v in x.values():finite(v)
    elif isinstance(x,list):
        for v in x:finite(v)
def validate(name,data):
    finite(data)
    schema=json.loads((ROOT/'schemas'/name).read_text())
    errors=list(Draft7Validator(schema).iter_errors(data))
    need(not errors,'SCHEMA_ERROR','Invalid '+name,paths=['/'.join(map(str,e.path)) for e in errors[:5]])
def digest(data):return hashlib.sha256(json.dumps(data,sort_keys=True,allow_nan=False).encode()).hexdigest()
def identity(scene,artifact):
    need(artifact['metadata']==scene['metadata'] and artifact['scene_digest']==digest(scene),
         'SCENE_VERSION_MISMATCH','Scene, revision, coordinate frame or content changed')
def artifact(scene,**kw):return dict(metadata=deepcopy(scene['metadata']),scene_digest=digest(scene),**kw)
def grid(meta):
    q=meta['horizon_s']/meta['step_s']
    need(abs(q-round(q))<1e-8 and 1<=q<=500,'TIME_GRID','Horizon must be an integer number of steps (1..500)')
    return [i*meta['step_s'] for i in range(round(q)+1)]


def model_contract(version='2.0.0'):
    need(version in ('2.0.0','2.1.0'),'MODEL_CONTRACT','Unknown explicit model output contract')
    return ('agent_output_v2.schema.json','driving_v2') if version=='2.0.0' else ('agent_output_v2_1.schema.json','driving_v2_1')


def resolve_prompt(version='2.0.0',requested=None):
    """Explicit prompt-only revision; the selected output schema stays unchanged."""
    schema,default=model_contract(version)
    allowed={'2.0.0':('driving_v2',),'2.1.0':('driving_v2_1','driving_v2_2')}
    prompt=requested or default
    need(prompt in allowed[version],'PROMPT_CONTRACT','Prompt and output contract are incompatible')
    return schema,prompt


def planning_contract(capture):
    """Deterministic tool requirements, never additional environment facts."""
    m=capture['metadata'];times=grid(m)
    return dict(target_region_semantics='Allowed vehicle CENTER region for the entire planning horizon, including initial center; not a terminal goal box',
                initial_center_m=[capture['ego']['x_m'],capture['ego']['y_m']],
                decision_valid_until_min_s=m['reference_time_s']+m['horizon_s'],
                supplied_valid_until_s=m['valid_until_s'],execution_prefix_s=capture['safety']['prefix_s'],
                occupancy_grid=[dict(start_s=a,end_s=b,kind='whole_interval_envelope') for a,b in zip(times,times[1:])],
                supported_native_metric_scale_labels=['model_metric_hypothesis','unknown'],
                calibration_limitation='Intrinsics/extrinsics alone do not verify a ground plane or pixel contact. Do not relabel unsupported scale as a supported metric hypothesis.',
                coverage_requirement='Each non-null actor occupancy must support every whole interval. Use null for unsupported coverage; never fill placeholders.')
