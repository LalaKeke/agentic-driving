"""Four independently callable JSON interfaces with explicit failure categories."""
from .contracts import Failure,need
from . import t1,t2,t3,t4

def call_tool(request,allow_synthetic=False):
    if not isinstance(request,dict):request={}
    tool=request.get('tool');rid=request.get('request_id','invalid-request')
    try:
        need(request.get('kind')=='request' and tool in ('T1','T2','T3','T4'),'INTERFACE','Expected versioned tool request')
        need(set(request)=={'kind','tool','request_id','payload'} and isinstance(rid,str) and bool(rid),'INTERFACE','Invalid request fields or identifier')
        p=request['payload']
        keys={'T1':{'capture','output','provenance'},'T2':{'scene'},'T3':{'scene','corridor'},'T4':{'scene','trajectory'}}
        need(isinstance(p,dict) and set(p)==keys[tool],'INTERFACE','Unexpected or missing payload fields')
        if tool=='T1':out={'scene':t1.construct(p['capture'],p['output'],p['provenance'],allow_synthetic)}
        elif tool=='T2':out={'corridor':t2.build(p['scene'])}
        elif tool=='T3':out={'trajectory':t3.solve(p['scene'],p['corridor'])}
        else:out={'verification':t4.verify(p['scene'],p['trajectory'])}
        return dict(kind='response',tool=tool,request_id=rid,status='ok',payload=out)
    except (KeyError,TypeError):
        error=Failure('INTERFACE','Missing or invalid tool fields').record
    except Failure as e:error=e.record
    return dict(kind='response',tool=tool,request_id=rid,status='error',error=error)
