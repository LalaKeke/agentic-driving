"""Pure diagnostics for model-native premise/domain binding failures.

Never changes a model field, accepts a scene, predicts an actor or reads an
image/truth file. JSON pointers refer to the native output argument unless
explicitly labelled capture_pointer. Runtime callers opt in only for v3.
"""
from copy import deepcopy
from decimal import Decimal, InvalidOperation

from shapely.geometry import Point, Polygon


def _pointer(parts):
    return ''.join('/'+str(p).replace('~','~0').replace('/','~1') for p in parts)


def _bindings(node, premise_id, parts=()):
    if isinstance(node,dict):
        for key,value in node.items():
            if key=='premise_ids' and isinstance(value,list):
                for i,item in enumerate(value):
                    if item==premise_id:
                        yield parts+(key,i)
            yield from _bindings(value,premise_id,parts+(key,))
    elif isinstance(node,list):
        for i,value in enumerate(node):
            yield from _bindings(value,premise_id,parts+(i,))


def _number(value):
    return isinstance(value,(int,float)) and not isinstance(value,bool)


def _evidence(item,capture):
    ids=item.get('frame_ids') or [];times=item.get('time_ids') or []
    result=[]
    for fid,tid in zip(ids,times):
        found=next(((i,f) for i,f in enumerate(capture.get('observations',[]))
                    if f.get('id')==fid and f.get('time_id')==tid),None)
        row=dict(frame_id=fid,time_id=tid,capture_pointer=None)
        if found:
            i,f=found
            row.update(capture_pointer=f'/observations/{i}',camera_id=f.get('camera_id'),
                       capture_time_s=f.get('capture_time_s'))
        result.append(row)
    return dict(frame_ids=deepcopy(ids),time_ids=deepcopy(times),resolved_pairs=result)


def _use(parts,output,capture):
    row=dict(json_pointer=_pointer(parts),raw_value=None,kind='other_native_binding',
             object_id=None,scenario_id=None,required_interval_s=None)
    current=output
    for part in parts:current=current[part]
    row['raw_value']=deepcopy(current)
    item={}
    if len(parts)>3 and parts[:2]==('hypotheses','objects'):
        obj=output['hypotheses']['objects'][parts[2]];row['object_id']=obj.get('id');item=obj
        section=parts[3]
        if section=='alignment':
            row['kind']='past_alignment_binding'
            row['evidence']=_evidence(obj,capture)
            offsets=[]
            for found in row['evidence']['resolved_pairs']:
                if found.get('capture_time_s') is None:continue
                try:
                    offsets.append(float(Decimal(str(found['capture_time_s']))-
                                         Decimal(str(capture['metadata']['reference_time_s']))))
                except (KeyError,InvalidOperation,ValueError):pass
            if offsets:row['required_interval_s']=[min(offsets),0]
        elif section=='scenarios':
            scenario=obj['scenarios'][parts[4]]
            row.update(kind='future_scenario_binding',scenario_id=scenario.get('id'),
                       scenario_role=scenario.get('role'))
            horizon=capture.get('metadata',{}).get('horizon_s')
            if _number(horizon):row['required_interval_s']=[0,horizon]
        elif section=='occupancy':
            item=obj['occupancy'][parts[4]]
            row.update(kind='future_source_binding',source_id=item.get('id'),
                       scenario_ids=[a.get('scenario_id') for a in item.get('alternatives',[])])
            if _number(item.get('start_s')) and _number(item.get('end_s')):
                row['required_interval_s']=[item['start_s'],item['end_s']]
    if 'evidence' not in row:row['evidence']=_evidence(item,capture)
    return row


def _premise_diagnostic(failure,output,capture):
    details=failure.get('details') or {};hyp=output['hypotheses']
    pids=[details['premise_id']] if details.get('premise_id') else details.get('premise_ids',[])
    registry=hyp.get('premises') or [];items=[]
    for pid in pids:
        found=next(((i,p) for i,p in enumerate(registry) if p.get('id')==pid),None)
        uses=[_use(parts,output,capture) for parts in _bindings(output,pid)]
        raw=deepcopy(found[1]) if found else None
        declared=[raw.get('start_s'),raw.get('end_s')] if raw else None
        past_only=bool(declared and all(_number(x) for x in declared)
                       and declared[0]<0 and declared[1]<=0)
        future=[u for u in uses if u['kind'] in ('future_scenario_binding','future_source_binding')
                and u['required_interval_s'] and u['required_interval_s'][1]>0]
        items.append(dict(premise_id=pid,json_pointer=f'/hypotheses/premises/{found[0]}' if found else None,
            raw_value=raw,declared_interval_s=declared,required_interval_s=deepcopy(details.get('required_interval_s')),
            object_id=details.get('object_id'),required_scenario_ids=deepcopy(details.get('required_scenario_ids')),
            reference_uses=uses,past_only_misbound_to_future=bool(past_only and future)))
    return dict(offending_premises=items,original_failure_details=deepcopy(details),
        model_repair_request=(
            'Inspect the exact native JSON pointers. Keep justified past-alignment support and its actual image citations. '
            'If a past-only premise was erroneously also attached to future scenarios or BODY sources, have the model '
            'correct those future bindings and provide or retain independently evidence-supported future-motion premises '
            'covering every retained scenario and source interval. Do not automatically extend the past premise, remove '
            'a reasonable adverse scenario, or change actor position/BODY, occupancy, residuals, road or physical limits '
            'to repair a binding error. Unsupported necessary future coverage must remain unknown and refused.'))


def _shape(vertices):
    if not isinstance(vertices,list) or len(vertices)<3:return None
    try:
        result=Polygon(vertices)
        return result if result.is_valid and not result.is_empty and result.area>0 else None
    except (TypeError,ValueError):return None


def _domain_diagnostic(output,capture):
    hyp=output['hypotheses'];road=hyp.get('road');premises=hyp.get('premises') or []
    domains=[]
    for i,premise in enumerate(premises):
        if premise.get('id')=='prototype_local_scope':
            domains.append(dict(json_pointer=f'/hypotheses/premises/{i}',raw_value=deepcopy(premise),
                                geometry_pointer=f'/hypotheses/premises/{i}/space_polygon_m'))
    road_vertices=road.get('polygon_m') if isinstance(road,dict) else None
    road_shape=_shape(road_vertices);comparisons=[]
    for domain in domains:
        shape=_shape(domain['raw_value'].get('space_polygon_m'))
        entry=dict(domain_pointer=domain['geometry_pointer'],geometry_status='NOT_EVALUABLE',
                   road_within_domain=None,road_outside_area_m2=None,outside_road_vertices=[])
        if road_shape is not None and shape is not None:
            entry.update(geometry_status='EVALUATED_FROM_NATIVE_GEOMETRY',road_within_domain=shape.covers(road_shape),
                road_bounds_m=list(road_shape.bounds),domain_bounds_m=list(shape.bounds),
                road_outside_area_m2=road_shape.difference(shape).area)
            for j,point in enumerate(road_vertices):
                if not shape.covers(Point(point)):
                    entry['outside_road_vertices'].append(dict(json_pointer=f'/hypotheses/road/polygon_m/{j}',
                        raw_value=deepcopy(point),distance_to_declared_domain_m=shape.distance(Point(point))))
        comparisons.append(entry)
    return dict(road=dict(json_pointer='/hypotheses/road',raw_value=deepcopy(road)),
        prototype_domains=domains,geometry_comparisons=comparisons,
        required_interval_s=[0,capture.get('metadata',{}).get('horizon_s')],
        road_evidence=_evidence(road,capture) if isinstance(road,dict) else None,
        model_repair_request=(
            'Your native road and prototype-local-domain declarations conflict or are incomplete. Re-examine the cited '
            'images and explicitly adopted local conditions to make consistent, evidence-supported declarations. '
            'Do not automatically enlarge the domain to cover the road, clip/erase road geometry, omit threats, shrink '
            'residuals or alter physical limits for acceptance. A changed finite domain changes the conditional scope '
            'and needs an explicit image-based rationale. If the required declaration is unsupported, retain unknown '
            'and the planning refusal. These geometry diagnostics are not replacement model values.'))


def augment_feedback(failure,output,capture):
    """Return extra diagnostics or None; every argument remains value-identical."""
    if not isinstance(failure,dict) or failure.get('code') not in ('PREMISE_SCOPE','PROTOTYPE_SCOPE'):
        return None
    if not isinstance(output,dict) or not isinstance(output.get('hypotheses'),dict) or not isinstance(capture,dict):
        return None
    result=dict(diagnostic_version='contract_scope_feedback_v1',source='deterministic_contract_diagnostic',
                failure_code=failure['code'],pointer_root='native_output',authorizes_planning=False,
                model_data_modified=False,units=dict(distance='m',area='m2',relative_time='s'))
    result.update(_premise_diagnostic(failure,output,capture) if failure['code']=='PREMISE_SCOPE'
                  else _domain_diagnostic(output,capture))
    return result
