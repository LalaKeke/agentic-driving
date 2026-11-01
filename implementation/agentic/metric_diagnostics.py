"""Offline, non-authorizing diagnostics for native 3.0.0-exp2 output.

Inspect independent guards against the *unchanged* input.  This module never
calls T1, a model, an image reader or a transport, never substitutes values, and
never interprets explanatory prose as evidence.  NOT_EVALUABLE is distinct from
PASS: a missing prerequisite does not establish the downstream predicate.
"""
import ast
from collections import Counter
from copy import deepcopy
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path

from jsonschema import Draft7Validator
from shapely.errors import ShapelyError
from shapely.geometry import Point, Polygon

from .contracts import ROOT, digest
from .metric_contract import VERSION, COVERAGE_PROFILE


_MISSING = object()
_CORE = Path(__file__).with_name('metric_contract.py')
_GEOMETRY = Path(__file__).with_name('geometry.py')
_CONTRACTS = Path(__file__).with_name('contracts.py')
_SCHEMA = ROOT / 'schemas' / 'agent_output_v3_exp2.schema.json'


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _pointer(parts):
    return ''.join('/' + str(x).replace('~', '~0').replace('/', '~1') for x in parts)


def _at(value, pointer):
    if not pointer:
        return value
    try:
        for part in pointer[1:].split('/'):
            part = part.replace('~1', '/').replace('~0', '~')
            value = value[int(part)] if isinstance(value, list) else value[part]
        return value
    except (KeyError, IndexError, TypeError, ValueError):
        return _MISSING


def _unavailable():
    raise ValueError('Required semantic prerequisite is unavailable; no replacement value was supplied.')


def _strict_digest(value):
    try:
        return digest(value)
    except (ValueError, TypeError):
        return None


def _list(value):
    """Traversal only: a malformed container is already a schema failure."""
    return value if isinstance(value,list) else []


def _guard_catalog():
    """Read exact current guard locations, rather than maintaining stale lines."""
    records = []
    for path in (_CORE, _GEOMETRY, _CONTRACTS):
        tree = ast.parse(path.read_text())
        for function in tree.body:
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(function):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id == 'need' and len(node.args) >= 3
                        and isinstance(node.args[1], ast.Constant)):
                    records.append(dict(
                        code=node.args[1].value, file=str(path.relative_to(ROOT)),
                        function=function.name, line=node.lineno,
                        end_line=node.end_lineno, source_sha256=_sha(path),
                        predicate=ast.unparse(node.args[0]),
                        message=ast.literal_eval(node.args[2]) if isinstance(node.args[2], ast.Constant) else ast.unparse(node.args[2])))
    return records


def diagnose_metric_output(capture, output, provenance, first_failure=None,
                           record_path=None, allow_synthetic=False):
    """Return exhaustive independent, feasible-to-evaluate predicate checks.

    ``first_failure`` is the actual T1 Failure.record supplied by the caller, or
    None when T1 was not reached. It is preserved verbatim. Matching its code
    does not assert that every row with that code was executed by runtime T1.
    All pointers resolve against the named document (output/capture/provenance).
    No inputs, including provenance digests, are modified by this function.
    """
    audit = _Audit(capture, output, provenance, first_failure, record_path,
                   allow_synthetic)
    audit.run()
    return audit.report()


class _Audit:
    def __init__(self, capture, output, provenance, first_failure, record_path,
                 allow_synthetic):
        self.docs = dict(capture=capture, output=output, provenance=provenance)
        self.first_failure = deepcopy(first_failure)
        self.record_path = record_path
        self.allow_synthetic = allow_synthetic
        self.rows = []
        self.guards = _guard_catalog()
        self.m = capture.get('metadata', {}) if isinstance(capture, dict) else {}
        if not isinstance(self.m,dict):self.m={}
        self.h = output.get('hypotheses', {}) if isinstance(output, dict) else {}
        self.d = output.get('decision', {}) if isinstance(output, dict) else {}
        self.frames = {f['id']: f for f in capture.get('observations', [])
                       if isinstance(f, dict) and 'id' in f} if isinstance(capture, dict) else {}
        self.objects = self.h.get('objects', []) if isinstance(self.h, dict) else []
        self.objects = self.objects if isinstance(self.objects, list) else []
        self.actors = {o.get('id'): o for o in self.objects if isinstance(o, dict) and isinstance(o.get('id'), str)}
        self.premises = self.h.get('premises', []) if isinstance(self.h, dict) else []
        self.premises = self.premises if isinstance(self.premises, list) else []
        self.registry = {p.get('id'): p for p in self.premises if isinstance(p, dict) and isinstance(p.get('id'), str)}
        self.scenario_ids = [s.get('id') for o in self.objects if isinstance(o, dict)
                             for s in _list(o.get('scenarios')) if isinstance(s, dict)]

    def check(self, code, path, evaluate, *, doc='output', function=None,
              message_contains=None, dependencies=(), object_id=None,
              interval=None, evidence=None, note=None, schema_pointer=None):
        refs = [g for g in self.guards if g['code'] == code
                and (function is None or g['function'] == function)
                and (message_contains is None or message_contains in g['message'])]
        raw = _at(self.docs[doc], path)
        row = dict(document=doc, record_path=self.record_path, json_pointer=path,
                   raw_present=raw is not _MISSING,
                   raw_value=deepcopy(raw) if raw is not _MISSING else None,
                   code=code, guard_refs=deepcopy(refs), object_id=object_id,
                   time_interval_s=deepcopy(interval), evidence=deepcopy(evidence),
                   contract_clause_ref=dict(schema_file=str(_SCHEMA.relative_to(ROOT)),
                                            schema_sha256=_sha(_SCHEMA),
                                            schema_pointer=schema_pointer,
                                            document='docs/计量与时空覆盖契约3_0_exp2.md'),
                   relevance_basis='No relevance pruning: exp2 requires all declared actor scenarios and bound scopes to satisfy their complete conditional coverage obligations.',
                   note=note)
        if code in ('SCHEMA_ERROR','NONFINITE','INVALID_GEOMETRY','UNSUPPORTED_GEOMETRY','UNSUPPORTED_TRAFFIC_RULE'):
            row['diagnostic_class']='structure_or_contract_expression'
        elif code in ('MISSING_SCALE','MISSING_SCALE_SUPPORT','SCALE_ANCHOR','SCALE_ANCHOR_EVIDENCE'):
            row['diagnostic_class']='metric_scale_support'
        elif code.startswith('OCCUPANCY_') or code in ('PLACEHOLDER_OCCUPANCY','SCENARIO_INCOMPLETE','SCENARIO_REFERENCE','BODY_ENVELOPE_CONFLICT'):
            row['diagnostic_class']='future_body_coverage_or_scenario_inventory'
        elif code in ('TYPED_UNKNOWN_BLOCKING','UNBOUNDED_UNKNOWN'):
            row['diagnostic_class']='explicit_unresolved_unknown'
        elif code.startswith('ALIGNMENT_') or code in ('STATE_TIME','DYNAMIC_ALIGNMENT','MOTION_BOUND_MISSING','ACTOR_NOT_OBSERVED','VELOCITY_BOUND_CONFLICT','MISSING_EVIDENCE'):
            row['diagnostic_class']='acquisition_evidence_or_native_state_alignment'
        elif code in ('UNKNOWN_UNCERTAINTY','RESIDUAL_BUDGET'):
            row['diagnostic_class']='finite_conditional_residual'
        elif code.startswith('PREMISE_') or code.startswith('SCOPE_') or code=='CONDITIONAL_SCOPE':
            row['diagnostic_class']='conditional_premise_binding'
        else:
            row['diagnostic_class']='capture_identity_or_driving_contract'
        missing = [(d, p) for d, p in dependencies if _at(self.docs[d], p) is _MISSING]
        if missing:
            row.update(status='NOT_EVALUABLE', prerequisite_failure=dict(missing=missing))
        else:
            try:
                answer = evaluate()
                row['status'] = 'PASS' if bool(answer) else 'FAIL'
            except (KeyError, IndexError, TypeError, ValueError, ArithmeticError,
                    AttributeError, OverflowError, ShapelyError) as exc:
                row.update(status='NOT_EVALUABLE', prerequisite_failure=dict(
                    exception=type(exc).__name__, detail=str(exc)))
        if row['status'] == 'FAIL':
            # A code can identify multiple guards/fields. Preserve this ambiguity.
            row['historical_relation'] = ('matches_actual_first_rejection_code_only'
                if self.first_failure and code == self.first_failure.get('code')
                else 'independently_detected_not_claimed_as_executed_runtime_guard')
        else:
            row['historical_relation'] = 'independent_diagnostic'
        self.rows.append(row)
        return row['status']

    def schema(self, doc, name):
        data = self.docs[doc]
        path = ROOT / 'schemas' / name
        schema = json.loads(path.read_text())
        errors = sorted(Draft7Validator(schema).iter_errors(data),
                        key=lambda e: (str(list(e.absolute_path)), str(list(e.absolute_schema_path))))
        if not errors:
            self.check('SCHEMA_ERROR', '', lambda: True, doc=doc, function='validate',
                       note='All Draft7 schema predicates passed; semantic guards are checked separately.')
            self.rows[-1]['contract_clause_ref'].update(
                schema_file=str(path.relative_to(ROOT)), schema_sha256=_sha(path))
        for error in errors:
            self.check('SCHEMA_ERROR', _pointer(error.absolute_path), lambda: False,
                       doc=doc, function='validate', schema_pointer=_pointer(error.absolute_schema_path),
                       note=error.message)
            self.rows[-1]['contract_clause_ref'].update(
                schema_file=str(path.relative_to(ROOT)), schema_sha256=_sha(path))

    def geometry(self, value, path, *, convex=False, **context):
        def valid():
            p = Polygon(value)
            return p.is_valid and p.area > 1e-8
        status = self.check('INVALID_GEOMETRY', path, valid, function='polygon', **context)
        if convex:
            self.check('UNSUPPORTED_GEOMETRY', path,
                       lambda: abs(Polygon(value).convex_hull.area - Polygon(value).area) < 1e-7,
                       function='polygon', note='Independent convexity check; invalid polygon remains a separate failure.', **context)
        return Polygon(value) if status == 'PASS' else None

    def evidence(self, item, path, *, object_id=None, interval=None):
        context = dict(object_id=object_id, interval=interval, evidence={
            'frame_ids': item.get('frame_ids'), 'time_ids': item.get('time_ids')})
        self.check('MISSING_EVIDENCE', path, lambda: len(item['frame_ids']) == len(item['time_ids'])
                   and len(set(item['frame_ids'])) == len(item['frame_ids']),
                   function='_evidence', message_contains='index-paired', **context)
        fs = item.get('frame_ids'); ts = item.get('time_ids')
        if not isinstance(fs, list) or not isinstance(ts, list):
            return None
        for i, (fid, tid) in enumerate(zip(fs, ts)):
            self.check('MISSING_EVIDENCE', path + '/frame_ids/' + str(i),
                       lambda fid=fid, tid=tid: fid in self.frames and self.frames[fid]['time_id'] == tid,
                       function='_evidence', message_contains='actual acquisition', **context)
        try:
            return set(zip(fs, ts))
        except TypeError:
            return None

    def scope(self, item, path):
        self.check('SCOPE_TIME', path, lambda: -self.m['history_s'] <= item['start_s'] <= item['end_s'] <= self.m['horizon_s'], function='_scope', interval=[item.get('start_s'), item.get('end_s')])
        self.check('SCOPE_OBJECT', path + '/object_ids', lambda: set(item['object_ids']) <= set(self.actors), function='_scope')
        self.check('SCOPE_SCENARIO', path + '/scenario_ids', lambda: set(item['scenario_ids']) <= set(self.scenario_ids), function='_scope')
        if item.get('space_polygon_m') is not None:
            self.geometry(item['space_polygon_m'], path + '/space_polygon_m')

    def premises_for(self, ids, path, oid, sids, left, right, geometry=None):
        context = dict(object_id=oid, interval=[left, right])
        self.check('PREMISE_MISSING', path, lambda: bool(ids), function='_premises', **context)
        if not isinstance(ids, list):
            return
        active = []
        for i, pid in enumerate(ids):
            self.check('PREMISE_REFERENCE', path + '/' + str(i), lambda pid=pid: pid in self.registry,
                       function='_premises', **context)
            if pid not in self.registry:
                continue
            p = self.registry[pid]
            self.check('PREMISE_SCOPE', path + '/' + str(i),
                       lambda p=p: (not p['object_ids'] or oid in p['object_ids'])
                       and (not p['scenario_ids'] or bool(set(sids) & set(p['scenario_ids'])))
                       and p['start_s'] <= left and p['end_s'] >= right,
                       function='_premises', message_contains='whole time interval', **context)
            active.append(p)
            if geometry is not None:
                for sid in sids:
                    if (not p.get('object_ids') or oid in p.get('object_ids', [])) and (not p.get('scenario_ids') or sid in p.get('scenario_ids', [])):
                        self.check('PREMISE_SPACE', path + '/' + str(i),
                                   lambda p=p: p['space_polygon_m'] is None or Polygon(p['space_polygon_m']).covers(geometry),
                                   function='_premise_space', note='scenario_id=' + str(sid), **context)
        for sid in sids:
            self.check('PREMISE_SCOPE', path,
                       lambda sid=sid: any(not p['scenario_ids'] or sid in p['scenario_ids'] for p in active) if all(pid in self.registry for pid in ids) else _unavailable(),
                       function='_premises', message_contains='Each retained scenario',
                       note='scenario_id=' + str(sid), **context)

    def premise_space_for(self, ids, path, oid, sid, geometry, interval):
        """Only spatial applicability, without asserting other branch premises."""
        if not isinstance(ids,list):
            self.check('PREMISE_SPACE',path,_unavailable,function='_premise_space',object_id=oid,interval=interval)
            return
        for i,pid in enumerate(ids):
            p=self.registry.get(pid)
            if p is None:
                self.check('PREMISE_SPACE',path+'/'+str(i),_unavailable,function='_premise_space',object_id=oid,interval=interval)
            elif (not p.get('object_ids') or oid in p.get('object_ids',[])) and (not p.get('scenario_ids') or sid in p.get('scenario_ids',[])):
                self.check('PREMISE_SPACE',path+'/'+str(i),lambda p=p:p['space_polygon_m'] is None or Polygon(p['space_polygon_m']).covers(geometry),function='_premise_space',object_id=oid,interval=interval,note='scenario_id='+str(sid))

    @staticmethod
    def body(o):
        b = o['body_geometry']; x, y = o['position_m']; c = math.cos(b['yaw_rad']); s = math.sin(b['yaw_rad'])
        return Polygon([(x + c*dx - s*dy, y + s*dx + c*dy)
                        for dx,dy in [(-b['length_m']/2,-b['width_m']/2), (b['length_m']/2,-b['width_m']/2),
                                      (b['length_m']/2,b['width_m']/2), (-b['length_m']/2,b['width_m']/2)]])

    def age(self, fid):
        return float(Decimal(str(self.m['reference_time_s'])) - Decimal(str(self.frames[fid]['capture_time_s'])))

    def run(self):
        self.schema('capture', 'capture_v2.schema.json')
        self.schema('output', 'agent_output_v3_exp2.schema.json')
        # A malicious nonfinite JSON extension must not be hidden by Draft7.
        def finite_walk(value, parts=()):
            if isinstance(value, float) and not math.isfinite(value):
                self.check('NONFINITE', _pointer(parts), lambda: False, function='finite')
            elif isinstance(value, dict):
                for k,v in value.items(): finite_walk(v, parts+(k,))
            elif isinstance(value, list):
                for k,v in enumerate(value): finite_walk(v, parts+(k,))
        finite_walk(self.docs['output'])
        self.capture_checks()
        h = self.h; d = self.d; m = self.m
        top = [
            ('DYNAMIC_ALIGNMENT', '/hypotheses/reference_time_id', lambda: h['reference_time_id'] == m['reference_time_id']),
            ('COORDINATES', '/hypotheses/coordinate_frame', lambda: h['coordinate_frame'] == m['coordinate_frame']),
            ('MISSING_SCALE', '/hypotheses/scale_basis', lambda: h['scale_basis'] == 'model_metric_hypothesis'),
            ('MISSING_SCALE_SUPPORT', '/hypotheses/metric_scale_support', lambda: h['metric_scale_support'] is not None),
            ('UNBOUNDED_UNKNOWN', '/hypotheses/road', lambda: h['road'] is not None),
            ('UNBOUNDED_UNKNOWN', '/hypotheses/unbounded_unknown', lambda: not h['unbounded_unknown']),
            ('SCENE_VERSION_MISMATCH', '/decision', lambda: d['scene_id'] == m['scene_id'] and d['revision'] == m['revision']),
            ('DECISION_EXPIRED', '/decision/valid_until_s', lambda: d['valid_until_s'] >= m['reference_time_s'] + m['horizon_s']),
            ('SEMANTIC_CONTRACT', '/decision/hard_requirements', lambda: {'target_region','vehicle_limits','collision_free','road'} <= set(d['hard_requirements'])),
            ('STOP_INTENT', '/decision/stop', lambda: (d['behavior'] == 'stop_yield') == (d['stop'] is not None)),
            ('DUPLICATE_ID', '/hypotheses/objects', lambda: len(h['objects']) == len(self.actors)),
            ('YIELD_TARGET', '/decision/yield_to', lambda: set(d['yield_to']) <= set(self.actors)),
            ('DUPLICATE_SCENARIO', '/hypotheses/objects', lambda: len(self.scenario_ids) == len(set(self.scenario_ids))),
            ('DUPLICATE_PREMISE', '/hypotheses/premises', lambda: len(h['premises']) == len(self.registry))]
        for code,path,fn in top:
            self.check(code, path, fn, function='normalize',
                       message_contains='inconsistent with behavior' if code=='STOP_INTENT' else None)
        if isinstance(d, dict) and d.get('stop'):
            self.check('STOP_INTENT', '/decision/stop', lambda: {'stop','yield'} <= set(d['hard_requirements']) and 0 <= d['stop']['deadline_s'] <= m['horizon_s'], function='normalize', message_contains='deadline')
        self.geometry(d.get('target_region') if isinstance(d, dict) else None, '/decision/target_region', convex=True)
        if isinstance(h, dict):
            regions = [('/hypotheses/road', h.get('road'))] + [('/hypotheses/unknown_regions/'+str(i), r) for i,r in enumerate(_list(h.get('unknown_regions')))]
            for path,r in regions:
                if isinstance(r, dict):
                    self.evidence(r,path)
                    self.geometry(r.get('polygon_m'),path+'/polygon_m',convex=path.endswith('/road'))
                self.check('UNKNOWN_UNCERTAINTY', path+'/uncertainty_m', lambda r=r: r['uncertainty_m'] is not None,
                           function='normalize', dependencies=[('output',path)], note='A null road is not assigned a zero uncertainty.')
            for i,e in enumerate(_list(h.get('traffic_evidence'))):
                path='/hypotheses/traffic_evidence/'+str(i)
                if not isinstance(e, dict): continue
                self.evidence(e,path)
                self.check('UNSUPPORTED_TRAFFIC_RULE',path+'/rule',lambda e=e:e['rule'] in ('stop_required','yield_required'),function='normalize')
                self.check('UNKNOWN_TRAFFIC_CONDITION',path+'/state',lambda e=e:e['state'] in ('active','inactive'),function='normalize')
                if e.get('state')=='active':self.check('TRAFFIC_INTENT_CONFLICT',path,lambda e=e:d['behavior']=='stop_yield' and (e['rule']!='yield_required' or bool(d['yield_to'])),function='normalize')
        for i,p in enumerate(self.premises):
            if isinstance(p,dict):self.scope(p,'/hypotheses/premises/'+str(i))
        self.scale_checks()
        self.unknown_checks()
        for i,o in enumerate(self.objects):
            if isinstance(o,dict):self.actor_checks(o,'/hypotheses/objects/'+str(i))

    def capture_checks(self):
        cap=self.docs['capture']; prov=self.docs['provenance']; m=self.m
        for code,path,fn in [
            ('MODEL_CONTRACT','/agent_output_contract',lambda:prov.get('agent_output_contract')==VERSION),
            ('SOURCE','/source',lambda:prov.get('source')=='online_model' or (self.allow_synthetic and prov.get('source')=='synthetic_fixture')),
            ('PROMPT_CONTRACT','/prompt_version',lambda:prov.get('prompt_version')=='driving_v3_exp2' or (self.allow_synthetic and prov.get('source')=='synthetic_fixture')),
            ('SOURCE','/output_digest',lambda:prov.get('output_digest')==digest(self.docs['output']))]:
            self.check(code,path,fn,doc='provenance',function='_check_capture',
                       message_contains=('Synthetic input' if path=='/source' else 'Output does not match') if code=='SOURCE' else None)
        observations=_list(cap.get('observations')) if isinstance(cap,dict) else []
        self.check('DUPLICATE_ID','/observations',lambda:len(self.frames)==len(observations),doc='capture',function='_check_capture')
        clock={}
        for i,f in enumerate(observations):
            if not isinstance(f,dict):continue
            self.check('CLOCK_CONFLICT','/observations/'+str(i),lambda f=f:f['time_id'] not in clock or clock[f['time_id']]==f['capture_time_s'],doc='capture',function='_check_capture')
            if 'time_id' in f and 'capture_time_s' in f:clock[f['time_id']]=f['capture_time_s']
            self.check('OBSERVATION_TIME','/observations/'+str(i)+'/capture_time_s',lambda f=f:0<=float(Decimal(str(m['reference_time_s']))-Decimal(str(f['capture_time_s'])))<=m['history_s'],doc='capture',function='_check_capture')
        for code,path,fn in [
            ('REFERENCE_TIME','/metadata/reference_time_id',lambda:clock.get(m['reference_time_id'])==m['reference_time_s']),
            ('TIME_COVERAGE','/metadata/valid_until_s',lambda:m['valid_until_s']>=m['reference_time_s']+m['horizon_s']),
            ('TIME_GRID','/metadata',lambda:1<=m['horizon_s']/m['step_s']<=500 and abs(m['horizon_s']/m['step_s']-round(m['horizon_s']/m['step_s']))<1e-8),
            ('PREFIX','/safety/prefix_s',lambda:cap['safety']['prefix_s']<=min(.5,m['step_s'])),
            ('EGO_FRAME','/ego',lambda:all(abs(cap['ego'][k])<1e-8 for k in ('x_m','y_m','yaw_rad'))),
            ('VEHICLE','/vehicle/center_to_rear_axle_m',lambda:cap['vehicle']['center_to_rear_axle_m']<=cap['vehicle']['rear_m'])]:
            self.check(code,path,fn,doc='capture')
        if isinstance(cap,dict) and 'acquisition_metadata' in cap:
            import numpy as np
            a=cap['acquisition_metadata']
            if not isinstance(a,dict):return
            pairs={(f['id'],f['time_id']) for f in observations}
            for code,path,fn in [
                ('CALIBRATION_REFERENCE','/acquisition_metadata/center_offset_assumption_m',lambda:abs(a['center_offset_assumption_m']-cap['vehicle']['center_to_rear_axle_m'])<1e-8),
                ('EGO_HISTORY','/acquisition_metadata/ego_history',lambda:len(a['ego_history'])==len(observations) and {(x['frame_id'],x['time_id']) for x in a['ego_history']}==pairs),
                ('EGO_HISTORY','/acquisition_metadata/ego_history',lambda:all(max(abs(x['center_xy_m'][0]),abs(x['center_xy_m'][1]),abs(x['yaw_rad']))<1e-8 for x in a['ego_history'] if x['time_id']==m['reference_time_id'])),
                ('CALIBRATION_MISSING','/acquisition_metadata/calibrations',lambda:all(f['camera_id'] in a['calibrations'] for f in observations))]:
                self.check(code,path,fn,doc='capture',function='_check_capture')
            for cid,cal in a.get('calibrations',{}).items():
                def ok(cal=cal):
                    R=np.asarray(cal['camera_to_center_rotation']);K=np.asarray(cal['camera_intrinsic'])
                    return np.allclose(R.T@R,np.eye(3),atol=1e-6) and abs(np.linalg.det(R)-1)<1e-6 and abs(np.linalg.det(K))>1e-9
                self.check('CALIBRATION','/acquisition_metadata/calibrations/'+str(cid),ok,doc='capture',function='_check_capture')

    def scale_checks(self):
        support=self.h.get('metric_scale_support') if isinstance(self.h,dict) else None
        path='/hypotheses/metric_scale_support'
        if not isinstance(support,dict):
            self.check('MISSING_SCALE_SUPPORT',path+'/metric_reasoning',_unavailable,function='_scale_support',message_contains='reasoning',dependencies=[('output',path+'/metric_reasoning')],note='No scale argument is invented when support is null.')
            return
        self.check('MISSING_SCALE_SUPPORT',path+'/metric_reasoning',lambda:bool(support['metric_reasoning'].strip()),function='_scale_support',message_contains='Metric-scale reasoning')
        pairs=self.evidence(support,path)
        self.check('PREMISE_REFERENCE',path+'/premise_ids',lambda:set(support['premise_ids'])<=set(self.registry),function='_scale_support')
        seen=set();covered=set()
        for i,anchor in enumerate(_list(support.get('anchors'))):
            ap=path+'/anchors/'+str(i)
            if not isinstance(anchor,dict):continue
            oid=anchor.get('object_id');o=self.actors.get(oid);key=(oid,anchor.get('dimension'))
            self.check('SCALE_ANCHOR',ap,lambda key=key:key not in seen,function='_scale_support',message_contains='Duplicate',object_id=oid);seen.add(key)
            self.check('SCALE_ANCHOR',ap+'/object_id',lambda o=o:o is not None and o['body_geometry'] is not None and o['position_m'] is not None,function='_scale_support',message_contains='existing native',object_id=oid)
            self.check('SCALE_ANCHOR',ap+'/value_m',lambda o=o,anchor=anchor:Decimal(str(anchor['value_m']))==Decimal(str(o['body_geometry'][anchor['dimension']])),function='_scale_support',message_contains='conflicts',object_id=oid)
            self.check('MISSING_SCALE_SUPPORT',ap+'/rationale',lambda anchor=anchor:bool(anchor['rationale'].strip()),function='_scale_support',message_contains='rationale',object_id=oid)
            if not isinstance(o,dict):continue
            try:
                opairs=set(zip(o['frame_ids'],o['time_ids']));overlap=opairs & pairs
                observed={(x['frame_id'],x['time_id']) for x in o['alignment']['evidence_positions'] if x['observed_actor']}
                self.check('SCALE_ANCHOR_EVIDENCE',ap,lambda:bool(overlap) and overlap<=observed,function='_scale_support',message_contains='must be seen',object_id=oid,evidence={'overlap':sorted(overlap)})
                covered|=overlap
                sids=[s['id'] for s in o['scenarios']]
                applicable=[pid for pid in support['premise_ids'] if pid in self.registry and (not self.registry[pid]['object_ids'] or oid in self.registry[pid]['object_ids']) and (not self.registry[pid]['scenario_ids'] or bool(set(sids)&set(self.registry[pid]['scenario_ids'])))]
                try:body=self.body(o)
                except (KeyError,TypeError,ValueError):body=None
                self.premises_for(applicable,path+'/premise_ids',oid,sids,0,0,body)
            except (KeyError,TypeError,ValueError):
                self.check('SCALE_ANCHOR_EVIDENCE',ap,_unavailable,function='_scale_support',message_contains='must be seen',object_id=oid,note='Actor native body/evidence prerequisites unavailable.')
        self.check('SCALE_ANCHOR_EVIDENCE',path,lambda:covered==pairs,function='_scale_support',message_contains='All scale-support')

    def unknown_checks(self):
        unknowns=self.h.get('unknowns',[]) if isinstance(self.h,dict) else []
        self.check('DUPLICATE_UNKNOWN','/hypotheses/unknowns',lambda:len(unknowns)==len({u['id'] for u in unknowns}),function='normalize')
        for i,u in enumerate(_list(unknowns)):
            path='/hypotheses/unknowns/'+str(i)
            if not isinstance(u,dict):continue
            self.scope(u,path)
            context=dict(interval=[u.get('start_s'),u.get('end_s')])
            self.check('PREMISE_REFERENCE',path+'/premise_ids',lambda u=u:set(u['premise_ids'])<=set(self.registry),function='normalize',**context)
            self.check('TYPED_UNKNOWN_BLOCKING',path+'/kind',lambda u=u:u['kind']=='multiple_conditionals',function='normalize',note='Free text and distant current positions cannot authorize relevance pruning.',**context)
            self.check('CONDITIONAL_SCOPE',path,lambda u=u:bool(u['object_ids']) and bool(u['scenario_ids']),function='normalize',message_contains='identify',**context)
            self.check('CONDITIONAL_SCOPE',path+'/scenario_ids',lambda u=u:set(u['scenario_ids'])<={s['id'] for o in self.objects if o['id'] in u['object_ids'] for s in o['scenarios']},function='normalize',message_contains='another object',**context)
            for oid in _list(u.get('object_ids')):
                o=self.actors.get(oid)
                if not o:continue
                local={s['id'] for s in _list(o.get('scenarios'))}&set(_list(u.get('scenario_ids')))
                self.check('CONDITIONAL_SCOPE',path,lambda local=local:bool(local),function='normalize',message_contains='Each object',object_id=oid,**context)
                relevant=[pid for pid in u.get('premise_ids',[]) if pid in self.registry and (not self.registry[pid]['object_ids'] or oid in self.registry[pid]['object_ids']) and (not self.registry[pid]['scenario_ids'] or bool(local&set(self.registry[pid]['scenario_ids'])))]
                self.premises_for(relevant,path+'/premise_ids',oid,sorted(local),u.get('start_s'),u.get('end_s'))

    def actor_checks(self,o,path):
        oid=o.get('id');ctx=dict(object_id=oid);H=self.m.get('horizon_s')
        pairs=self.evidence(o,path,**ctx)
        for code,field,fn in [
            ('STATE_TIME','/state_time_id',lambda:o['state_time_id']==self.m['reference_time_id']),
            ('BODY_STATE_UNKNOWN','/position_m',lambda:o['position_m'] is not None),
            ('BODY_STATE_UNKNOWN','/body_geometry',lambda:o['body_geometry'] is not None),
            ('UNKNOWN_UNCERTAINTY','/uncertainty_m',lambda:o['uncertainty_m'] is not None),
            ('RESIDUAL_BUDGET','/uncertainty_m',lambda:o['alignment']['residual_m']<=o['uncertainty_m']),
            ('SCENARIO_INCOMPLETE','/scenarios',lambda:bool(o['scenarios']) and o['scenario_inventory_complete'] and any(s['role']=='reasonable_adverse' for s in o['scenarios'])),
            ('OCCUPANCY_UNKNOWN','/occupancy',lambda:o['occupancy'] is not None)]:
            self.check(code,path+field,fn,function='normalize',
                       message_contains='include alignment residual' if code=='RESIDUAL_BUDGET' else None,**ctx)
        a=o.get('alignment')
        sids=[s['id'] for s in _list(o.get('scenarios')) if isinstance(s,dict) and 'id' in s]
        for j,s in enumerate(_list(o.get('scenarios'))):
            if isinstance(s,dict):self.premises_for(s.get('premise_ids'),path+'/scenarios/'+str(j)+'/premise_ids',oid,[s.get('id')],0,H)
        if isinstance(a,dict):self.alignment_checks(o,a,path,pairs,sids)
        try:body=self.body(o)
        except (KeyError,TypeError,ValueError):body=None
        sources=o.get('occupancy')
        if not isinstance(sources,list):
            for sid in sids:
                self.check('OCCUPANCY_GAP',path+'/occupancy',_unavailable,function='normalize',object_id=oid,interval=[0,H],note='scenario_id='+str(sid)+'; null is not empty occupancy or a computed coverage gap.')
            return
        self.check('DUPLICATE_SOURCE',path+'/occupancy',lambda:len(sources)==len({s['id'] for s in sources}),function='normalize',**ctx)
        per_scenario={sid:[] for sid in sids}
        for j,src in enumerate(sources):
            if not isinstance(src,dict):continue
            sp=path+'/occupancy/'+str(j);interval=[src.get('start_s'),src.get('end_s')]
            self.check('PLACEHOLDER_OCCUPANCY',sp+'/status',lambda src=src:src['status']=='supported',function='normalize',interval=interval,**ctx)
            self.check('OCCUPANCY_SOURCE_TIME',sp,lambda src=src:0<=src['start_s']<src['end_s']<=H,function='normalize',interval=interval,**ctx)
            source_pairs=self.evidence(src,sp,interval=interval,**ctx)
            self.check('OCCUPANCY_EVIDENCE',sp,lambda:source_pairs<=pairs,function='normalize',interval=interval,**ctx)
            self.check('RESIDUAL_BUDGET',sp+'/residual_m',lambda src=src:Decimal(str(a['residual_m']))+Decimal(str(src['residual_m']))<=Decimal(str(o['uncertainty_m'])),function='normalize',message_contains='sum of alignment',interval=interval,**ctx)
            present=[x.get('scenario_id') for x in _list(src.get('alternatives')) if isinstance(x,dict)]
            self.check('SCENARIO_REFERENCE',sp+'/alternatives',lambda:len(present)==len(set(present)) and set(present)<=set(sids),function='normalize',interval=interval,**ctx)
            self.premises_for(src.get('premise_ids'),sp+'/premise_ids',oid,present,src.get('start_s'),src.get('end_s'))
            for k,alt in enumerate(_list(src.get('alternatives'))):
                if not isinstance(alt,dict):continue
                ap=sp+'/alternatives/'+str(k);sid=alt.get('scenario_id')
                poly=self.geometry(alt.get('polygon_m'),ap+'/polygon_m',object_id=oid,interval=interval)
                if sid in per_scenario:per_scenario[sid].append(src)
                if poly is not None:self.premise_space_for(src.get('premise_ids'),sp+'/premise_ids',oid,sid,poly,interval)
                if src.get('start_s')==0:
                    self.check('BODY_ENVELOPE_CONFLICT',ap+'/polygon_m',lambda:poly.covers(body) if body is not None and poly is not None else _unavailable(),function='normalize',object_id=oid,interval=interval,note='scenario_id='+str(sid)+'; nominal BODY before residual inflation.')
        for sid,seq in per_scenario.items():
            try:seq=sorted(seq,key=lambda x:(x['start_s'],x['end_s']))
            except (KeyError,TypeError):seq=None
            if seq is None:
                self.check('OCCUPANCY_GAP',path+'/occupancy',_unavailable,function='normalize',object_id=oid,interval=[0,H],note='Unusable source times prevent union coverage evaluation; no times repaired.')
                continue
            end=0.
            for src in seq:
                self.check('OCCUPANCY_GAP',path+'/occupancy',lambda src=src,end=end:src['start_s']<=end,function='normalize',message_contains='future coverage gap',object_id=oid,interval=[end,src.get('start_s')],note='scenario_id='+str(sid))
                try:end=max(end,src['end_s'])
                except TypeError:break
            self.check('OCCUPANCY_GAP',path+'/occupancy',lambda:end>=H,function='normalize',message_contains='complete horizon',object_id=oid,interval=[end,H],note='scenario_id='+str(sid))

    def alignment_checks(self,o,a,path,pairs,sids):
        oid=o.get('id');ap=path+'/alignment';positions=_list(a.get('evidence_positions'))
        ep={}
        for i,x in enumerate(positions):
            if not isinstance(x,dict):continue
            if 'frame_id' in x and 'time_id' in x:ep[(x['frame_id'],x['time_id'])]=x
            self.check('ACTOR_NOT_OBSERVED',ap+'/evidence_positions/'+str(i)+'/observed_actor',lambda x=x:x['observed_actor'],function='normalize',object_id=oid,evidence={'frame_id':x.get('frame_id'),'time_id':x.get('time_id')})
        self.check('ALIGNMENT_EVIDENCE',ap+'/evidence_positions',lambda:len(ep)==len(positions) and set(ep)==pairs,function='normalize',object_id=oid)
        try:ages=[self.age(f) for f,t in pairs]
        except (KeyError,TypeError,ValueError):ages=None
        if a.get('method')=='direct_reference_observation':
            self.check('ALIGNMENT_REQUIRED',ap,lambda:all(age==0 for age in ages) and a['motion_bound'] is None,function='normalize',object_id=oid)
            self.premises_for(a.get('premise_ids'),ap+'/premise_ids',oid,sids,0,0)
            for i,x in enumerate(positions):
                self.check('ALIGNMENT_CONFLICT',ap+'/evidence_positions/'+str(i)+'/position_m',lambda x=x:math.dist(x['position_m'],o['position_m'])<=a['residual_m']+1e-8,function='normalize',message_contains='direct reference',object_id=oid)
        else:
            b=a.get('motion_bound')
            self.check('MOTION_BOUND_MISSING',ap+'/motion_bound',lambda:b is not None,function='normalize',object_id=oid)
            self.check('ALIGNMENT_SCENARIO_GAP',ap+'/motion_bound/covers_all_declared_scenarios',lambda:b['covers_all_declared_scenarios'],function='normalize',object_id=oid)
            if o.get('velocity_mps') is not None:
                self.check('VELOCITY_BOUND_CONFLICT',path+'/velocity_mps',lambda:math.hypot(*o['velocity_mps'])<=b['max_speed_mps'],function='normalize',object_id=oid)
            if ages:
                self.premises_for(a.get('premise_ids'),ap+'/premise_ids',oid,sids,-max(ages),0)
            else:
                self.check('PREMISE_SCOPE',ap+'/premise_ids',_unavailable,function='_premises',object_id=oid,note='Cannot invent an acquisition age from invalid/unpaired evidence.')
            for i,x in enumerate(positions):
                self.check('ALIGNMENT_CONFLICT',ap+'/evidence_positions/'+str(i)+'/position_m',lambda x=x:math.dist(x['position_m'],o['position_m'])<=min(b['displacement_bound_m'],b['max_speed_mps']*self.age(x['frame_id']))+a['residual_m']+1e-8,function='normalize',message_contains='displacement exceeds',object_id=oid,evidence={'frame_id':x.get('frame_id'),'time_id':x.get('time_id')})
        for x in list(positions)+[{'position_m':o.get('position_m')}]:
            try:point=Point(x['position_m'])
            except (TypeError,ValueError,KeyError):continue
            self.premises_for(a.get('premise_ids'),ap+'/premise_ids',oid,sids,0,0,point)

    def report(self):
        statuses=Counter(r['status'] for r in self.rows)
        codes=Counter(r['code'] for r in self.rows if r['status']=='FAIL')
        inventory=[]
        for i,o in enumerate(self.objects):
            if not isinstance(o,dict):continue
            for field in ('position_m','velocity_mps','body_geometry','uncertainty_m','occupancy','state_time_id'):
                pointer='/hypotheses/objects/'+str(i)+'/'+field
                raw=_at(self.docs['output'],pointer)
                inventory.append(dict(json_pointer=pointer,raw_present=raw is not _MISSING,
                    raw_value=deepcopy(raw) if raw is not _MISSING else None,
                    object_id=o.get('id'),state_time_id=o.get('state_time_id'),
                    evidence=dict(frame_ids=deepcopy(o.get('frame_ids')),time_ids=deepcopy(o.get('time_ids'))),
                    interpretation='A null velocity alone is not a T1 rejection; no velocity or occupancy is inferred.' if field=='velocity_mps' else 'Original native field only; no substitution.'))
        return dict(diagnostic_version='exp2-independent-predicates-1',
                    source='offline_diagnostics', authorizes_planning=False,
                    new_model_calls=0, additional_T1_acceptance_runs=0,
                    required_contract=VERSION,
                    input_contract=self.docs['output'].get('contract_version') if isinstance(self.docs['output'],dict) else None,
                    required_coverage_profile=COVERAGE_PROFILE,
                    coverage_profile=self.docs['output'].get('coverage_profile') if isinstance(self.docs['output'],dict) else None,
                    original_T1_first_failure=self.first_failure,
                    first_failure_attribution='Code matches do not identify which duplicate code guard actually executed; the supplied actual T1 record remains authoritative.',
                    input_sha256={k:_strict_digest(v) for k,v in self.docs.items()},
                    diagnostic_source_sha256=_sha(Path(__file__)),
                    source_guard_catalog=self.guards,
                    summary=dict(total=len(self.rows),statuses=dict(statuses),failed_codes=dict(codes)),
                    native_field_inventory=inventory,
                    checks=self.rows,
                    limitations=['No pixel evidence or physical truth is validated.',
                                 'No prose-derived facts or new behavior predictions are supplied.',
                                 'A PASS applies only to its predicate; it is not T1/T4 acceptance or calibrated safety.',
                                 'Dependent guards may be NOT_EVALUABLE while independent guards are still reported.'])
