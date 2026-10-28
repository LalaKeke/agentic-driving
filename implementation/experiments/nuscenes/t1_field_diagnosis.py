#!/usr/bin/env python3
"""Offline field audit of the 27 frozen process-recovery responses.

This program never calls a model, T1, T2, or an optimizer. It evaluates independent
guard predicates on original values, using the exact guards in each run's source
ZIP as the rule registry. A failed earlier guard is never repaired to reach a
later one. NOT_EVALUABLE means a predicate lacks prerequisites, not that it passes.
The original runtime feedback and all frozen files remain authoritative history.
"""
import argparse
import ast
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import zipfile

from jsonschema import Draft7Validator
from shapely.geometry import Point, Polygon
import numpy as np


REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0,str(REPO/'implementation'))
from agentic.contracts import Failure
from agentic.evidence_io import reserve_directory, open_new, write_text_new
EVIDENCE = REPO / 'implementation/results/process_recovery'
DEFAULT_OUT = REPO / 'implementation/results/t1_unknown_diagnosis/fields'
RUNS = (
    ('v22_original_four', 'v22_trials/original_four/20261028T041442Z-27241009'),
    ('v22_exploratory_two', 'v22_trials/exploratory_two/20261028T041734Z-7dce6642'),
    ('v23_original_four', 'conditional_trials/original_four/20261028T042504Z-4cb96ed8'),
    ('v23_exploratory_two', 'conditional_trials/exploratory_two/20261028T042507Z-96fbbf1b'),
    ('v23_temporal_two', 'temporal_trials/two_windows/20261028T042955Z-c7627b95'),
)
WINDOW_NAMES = {
    'ac452a60e8b34a7080c938c904b23057': ('day', 'original_four'),
    '88449a5cb1644a199c1c11f6ac034867': ('construction', 'original_four'),
    'c537fef0b4b241df92f44daa976fe5b3': ('intersection', 'original_four'),
    'b4c279e61abf49bd871ff2ec4b13e115': ('night', 'original_four'),
    '6af01b48aec7475eac1f8e9add0276ff': ('clear_straight', 'exploratory_two'),
    'ac8b4d49731d43289579f014cb7c97ef': ('parking', 'exploratory_two'),
}
NUMBERED = re.compile(r'^\d{4}-(.+)\.json$')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical_digest(data):
    return sha(json.dumps(data, sort_keys=True, allow_nan=False).encode())


def relative(path):
    try:
        return str(Path(path).resolve().relative_to(REPO))
    except ValueError:
        return str(path)


def pointer(parts):
    return ''.join('/' + str(p).replace('~', '~0').replace('/', '~1') for p in parts)


def source_inventory(path):
    raw = Path(path).read_bytes()
    return dict(path=relative(path), sha256=sha(raw), bytes=len(raw))


def lookup(data, path):
    for item in path:
        data = data[item]
    return data


def finite_number(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def note_excerpts(note):
    """Literal source spans, not a model, causal attribution or actor association.

    These flags make explicit text searchable. They do not infer which actor a
    sentence describes and cannot establish that a conditional mode is credible.
    """
    if not isinstance(note,str): return {}
    patterns={
        'future_behavior_ambiguity': r'(?i)(start|creep|turn|approach|depart|revers|running|future|motion|stationary)',
        'coverage_missing_or_unbounded': r'(?i)(occupanc|unbound|unresolved|unsupported|cannot|not covered|occlu|intrusion)',
        'conditional_premises_or_limitations': r'(?i)(assum|condition|adopt|exclud|not covered|up to|at most|prior)',
        'scale_or_road_support': r'(?i)(metric|scale|ground|road|calibrat|triangulat)',
        'asynchronous_alignment': r'(?i)(asynchron|offset|alignment|time alignment|reference time|reference-time)',
    }
    sentences=[]
    for match in re.finditer(r'.+?(?:[.!?](?=\s|$)|$)',note,re.DOTALL):
        start,end=match.span()
        while start<end and note[start].isspace(): start+=1
        if start<end: sentences.append(dict(start_character=start,end_character=end,text=note[start:end]))
    return {name:[s for s in sentences if re.search(pattern,s['text'])] for name,pattern in patterns.items()}


class FrozenRules:
    """Extract source locations, not executable historical modules."""
    def __init__(self, run):
        self.run = run
        self.manifest = json.loads((run / '0002-manifest.json').read_text())
        self.commit = self.manifest['code_revision']
        self.zip_path = run / 'source_snapshot.zip'
        with zipfile.ZipFile(self.zip_path) as archive:
            self.blobs = {name: archive.read(name) for name in archive.namelist() if not name.endswith('/')}
        recorded = json.loads((run / '0001-source-hashes.json').read_text())
        self.source_hash_validation = []
        for key, expected in recorded.items():
            name = 'implementation/' + key
            assert name in self.blobs, name
            actual = sha(self.blobs[name])
            assert actual == expected, (name, actual, expected)
            self.source_hash_validation.append(dict(path=name, sha256=actual))
        self.guards = []
        for name in ('t1.py', 'contracts.py', 'geometry.py'):
            file = 'implementation/agentic/' + name
            source = self.blobs[file].decode()
            for node in ast.walk(ast.parse(source)):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name) or node.func.id != 'need' or len(node.args) < 3:
                    continue
                if not isinstance(node.args[1], ast.Constant):
                    continue
                self.guards.append(dict(file=file, line=node.lineno, end_line=node.end_lineno,
                    sha256=sha(self.blobs[file]), code=node.args[1].value,
                    message=node.args[2].value if isinstance(node.args[2], ast.Constant) else ast.get_source_segment(source,node.args[2]),
                    category=node.args[3].value if len(node.args)>3 and isinstance(node.args[3], ast.Constant) else 'interface_error',
                    predicate=ast.get_source_segment(source, node.args[0]),
                    source=ast.get_source_segment(source, node), frozen_commit=self.commit,
                    source_archive=relative(self.zip_path)))
        self.docs = {}
        for name in ('V2_接口与迁移.md', '模型输出契约2_1.md'):
            path = 'implementation/docs/' + name
            # Documents are tracked in Git but intentionally outside the runtime source ZIP.
            result = subprocess.run(['git', 'show', self.commit + ':' + path], cwd=REPO, check=True, capture_output=True)
            self.docs[path] = result.stdout

    def ref(self, code, message=None, file='t1.py'):
        candidates = [x for x in self.guards if x['code'] == code and x['file'].endswith('/'+file)
                      and (message is None or message in x['message'])]
        if not candidates:
            raise ValueError(('Guard not found', code, message, file))
        assert len(candidates) == 1, candidates
        return candidates[0]

    def context(self, token, file='t1.py'):
        path = 'implementation/agentic/' + file
        raw = self.blobs[path]
        matches = [(i, x) for i, x in enumerate(raw.decode().splitlines(), 1) if token in x]
        assert matches, (path, token)
        return dict(file=path, line=matches[0][0], sha256=sha(raw), source=matches[0][1],
                    frozen_commit=self.commit, source_archive=relative(self.zip_path), no_rejection_guard=True)

    def schema_ref(self, path, keyword=None):
        raw = self.blobs[path]
        text = raw.decode()
        line = next((i for i, x in enumerate(text.splitlines(), 1) if keyword and '"'+keyword+'"' in x), 1)
        return dict(file=path, line=line, sha256=sha(raw), frozen_commit=self.commit,
                    source_archive=relative(self.zip_path), note='Schema line is a property anchor; exact schema JSON pointer is recorded separately.')

    def clause(self, code, prompt=None):
        v2 = 'implementation/docs/V2_接口与迁移.md'
        v21 = 'implementation/docs/模型输出契约2_1.md'
        if code in ('OCCUPANCY_UNKNOWN', 'OCCUPANCY_GAP'):
            path, token = v21, '唯一新增表达能力'
        elif code in ('MISSING_SCALE', 'CALIBRATED_PROJECTION_UNVERIFIED'):
            path, token = v2, '当前仅接受模型给出的原生米制场景'
        elif code in ('UNBOUNDED_UNKNOWN', 'UNKNOWN_UNCERTAINTY', 'POSITION_NULL_ALLOWED', 'VELOCITY_NULL_ALLOWED', 'CONFIDENCE_UNPARSED', 'conditional_note_not_machine_interpreted', 'instantaneous_position_null_allowed', 'instantaneous_velocity_null_allowed'):
            path, token = v2, '未知位置/速度为 null'
        elif code in ('TRAFFIC_INTENT_CONFLICT', 'UNKNOWN_TRAFFIC_CONDITION', 'UNSUPPORTED_TRAFFIC_RULE', 'STOP_INTENT', 'SEMANTIC_CONTRACT', 'YIELD_TARGET'):
            path, token = v2, 'decision 包含行为'
        elif code in ('SOURCE', 'SOURCE_LABEL'):
            path, token = v2, '在线适配器填写'
        elif code in ('COORDINATES', 'EGO_FRAME', 'DYNAMIC_ALIGNMENT'):
            path, token = v2, '坐标 x 向前'
        elif code in ('INVALID_GEOMETRY','UNSUPPORTED_GEOMETRY','HYPOTHESIS_CONFLICT'):
            path, token = v2, 'T1 保留每个备选占用'
        else:
            path, token = v2, '采集输入由可信调用端提供'
        raw = self.docs[path]
        matches = [(i, x) for i, x in enumerate(raw.decode().splitlines(), 1) if token in x]
        assert matches, (path, token)
        result=dict(file=path, frozen_commit=self.commit, sha256=sha(raw), line=matches[0][0], clause=matches[0][1])
        prompt_token={
            'OCCUPANCY_UNKNOWN':'occupancy MUST', 'OCCUPANCY_GAP':'Defaults are exactly 25',
            'MISSING_SCALE':'native-metric', 'CALIBRATED_PROJECTION_UNVERIFIED':'native-metric',
            'UNBOUNDED_UNKNOWN':'Set unbounded_unknown=true', 'UNKNOWN_UNCERTAINTY':'uncertainty_m',
            'INVALID_GEOMETRY':'Road and target polygons', 'UNSUPPORTED_GEOMETRY':'Road and target polygons',
            'UNKNOWN_TRAFFIC_CONDITION':'Traffic evidence supports', 'UNSUPPORTED_TRAFFIC_RULE':'Traffic evidence supports',
            'TRAFFIC_INTENT_CONFLICT':'Traffic evidence supports', 'SEMANTIC_CONTRACT':'stop_yield requires',
            'STOP_INTENT':'stop_yield requires', 'DECISION_EXPIRED':'decision.valid_until_s',
            'MISSING_EVIDENCE':'frame_ids', 'DYNAMIC_ALIGNMENT':'ego motion compensation',
            'conditional_note_not_machine_interpreted':'confidence_note',
        }.get(code)
        if prompt and prompt_token:
            pp='implementation/prompts/'+prompt+'.txt';pr=self.blobs[pp]
            pm=[(i,x) for i,x in enumerate(pr.decode().splitlines(),1) if prompt_token.lower() in x.lower()]
            assert pm,(prompt,prompt_token)
            result['prompt_clause']=dict(file=pp,line=pm[0][0],sha256=sha(pr),clause=pm[0][1],frozen_commit=self.commit)
        return result


class Audit:
    def __init__(self, rules, record_path, capture_path, feedback_path, group):
        self.rules = rules
        self.record_path = record_path
        self.record = json.loads(record_path.read_text())
        self.capture = json.loads(capture_path.read_text())
        self.feedback = json.loads(feedback_path.read_text())
        self.output = self.record['output']
        self.m = self.capture['metadata']
        self.sid = self.m['scene_id'].removeprefix('nusc_')
        self.response_id = group + '/' + record_path.name
        self.first_guard = rules.ref(self.feedback['code'], self.feedback['message'])
        self.base = dict(response_id=self.response_id, group=group, sample_id=self.sid,
            scene_type=WINDOW_NAMES[self.sid][0], cohort=WINDOW_NAMES[self.sid][1],
            revision=self.m['revision'], prompt=self.record['provenance']['prompt_version'],
            model_id=self.record['provenance']['model_id'], frozen_source_commit=rules.commit,
            record=source_inventory(record_path), capture=source_inventory(capture_path),
            feedback=source_inventory(feedback_path), recorded_first_code=self.feedback['code'],
            recorded_first_source_line=self.first_guard['line'])
        self.rows = []
        self.frames = self.capture['observations']
        self.frame_map = {f['id']: f for f in self.frames}
        self.time_map = defaultdict(list)
        for f in self.frames:
            self.time_map[f['time_id']].append(f)
        self.horizon = self.m['horizon_s']
        self.n = round(self.horizon / self.m['step_s'])
        self.times = [i*self.m['step_s'] for i in range(self.n+1)]

    def evidence_refs(self, item):
        if not item:
            return dict(frame_ids=[], time_ids=[], acquired_frames=[])
        return dict(frame_ids=item.get('frame_ids', []), time_ids=item.get('time_ids', []),
            acquired_frames=[dict(frame_id=fid, time_id=self.frame_map[fid]['time_id'],
                capture_time_s=self.frame_map[fid]['capture_time_s'],
                relative_time_s=self.frame_map[fid]['capture_time_s']-self.m['reference_time_s'])
                for fid in item.get('frame_ids', []) if fid in self.frame_map])

    def add(self, name, path, status, code=None, message=None, source_file='t1.py',
            input_name='record', item=None, object_id=None, interval=None, unknown_type=None,
            classification=None, details=None, source=None, relevance=None, stage='T1', raw_override=None,
            use_override=False, schema_pointer=None):
        data = self.record if input_name == 'record' else self.capture
        raw_value = raw_override if use_override else lookup(data, path)
        guard = self.rules.ref(code, message, source_file) if code and source is None else source
        if stage == 'T2_DIAGNOSTIC':
            execution = 'downstream_potential_only_T2_never_called'
        elif not guard or guard.get('no_rejection_guard'):
            execution = 'no_independent_T1_rejection_guard'
        elif status == 'FAIL' and guard.get('file', '').endswith('/t1.py') and guard.get('line') == self.first_guard['line']:
            execution = 'recorded_first_rejection'
        elif status == 'FAIL':
            execution = 'independent_failed_predicate_masked_in_recorded_run'
        elif status == 'NOT_EVALUABLE':
            execution = 'prerequisite_missing_not_a_separate_proven_failure'
        elif guard.get('file', '').endswith('/t1.py') and guard.get('line', 0) > self.first_guard['line']:
            execution = 'independent_audit_only_not_reached_in_recorded_run'
        else:
            execution = 'not_rejected_by_this_predicate'
        if relevance is None:
            if object_id:
                relevance = dict(basis='software_default_all_declared_objects', model_declared_object=True,
                    explicitly_yielded_to=object_id in self.output['decision']['yield_to'],
                    geometric_relevance_proof=False,
                    explanation='T1 iterates every declared object; it does not test distance, reachable sets or a candidate corridor. Relevance is not geometrically established by this guard.')
            else:
                relevance = dict(basis='profile_global_contract', geometric_relevance_proof=False,
                    explanation='A profile-wide precondition, not a proven collision or an object-specific task relevance decision.')
        row = dict(**self.base, check=name, input=input_name, json_pointer=pointer(path), raw_value=raw_value,
            status=status, error_code=code, classification=classification or 'interface_contract',
            object_id=object_id, time_interval_s=interval, unknown_type=unknown_type,
            evidence=self.evidence_refs(item), relevance=relevance, stage=stage, actual_execution=execution,
            source_ref=guard, contract_ref=self.rules.clause(code or name,self.base['prompt']), details=details or {}, schema_json_pointer=schema_pointer)
        self.rows.append(row)
        return row

    def guard(self, name, path, ok, code, message=None, **kwargs):
        return self.add(name, path, 'PASS' if ok else 'FAIL', code, message, **kwargs)

    def geometry(self, path, vertices, convex=False, **kwargs):
        try:
            p = Polygon(vertices)
            valid = p.is_valid and p.area > 1e-8
        except (TypeError, ValueError):
            p = None
            valid = False
        self.guard('polygon_validity', path, valid, 'INVALID_GEOMETRY', source_file='geometry.py',
                   classification='structure_geometry', details=dict(area_m2=p.area if p is not None else None,
                   is_valid=p.is_valid if p is not None else False), **kwargs)
        if convex:
            self.add('profile_convexity', path, 'PASS' if valid and abs(p.convex_hull.area-p.area)<1e-7 else 'FAIL' if valid else 'NOT_EVALUABLE',
                'UNSUPPORTED_GEOMETRY', source_file='geometry.py', classification='profile_geometry',
                details=dict(prerequisite='valid nonzero-area polygon', required_convex=True), **kwargs)
        return p if valid else None

    def evidence(self, item, path, object_id=None, interval=None):
        fs, ts = item['frame_ids'], item['time_ids']
        valid_refs = set(fs)<=set(self.frame_map) and set(ts)<=set(self.time_map)
        self.guard('evidence_reference_membership', path+['frame_ids'], valid_refs, 'MISSING_EVIDENCE', 'Unknown evidence reference',
            item=item, object_id=object_id, interval=interval, classification='evidence_reference',
            details=dict(time_ids=ts, unknown_frame_ids=sorted(set(fs)-set(self.frame_map)), unknown_time_ids=sorted(set(ts)-set(self.time_map))))
        consistent = all(any(f['id'] in fs and f['time_id']==t for f in self.frames) for t in ts)
        self.guard('evidence_frame_time_pairing', path+['time_ids'], consistent, 'MISSING_EVIDENCE', 'Evidence times do not reference cited frames',
            item=item, object_id=object_id, interval=interval, classification='evidence_reference',
            details=dict(missing_paired_time_ids=[t for t in ts if not any(f['id'] in fs and f['time_id']==t for f in self.frames)]))

    def schema(self):
        for input_name, data, schema_path in (
            ('capture', self.capture, 'implementation/schemas/capture_v2.schema.json'),
            ('record', self.output, 'implementation/schemas/agent_output_v2_1.schema.json')):
            schema = json.loads(self.rules.blobs[schema_path])
            errors = sorted(Draft7Validator(schema).iter_errors(data), key=lambda e: str(list(e.path)))
            prefix = ['output'] if input_name=='record' else []
            self.add('whole_document_schema', prefix, 'PASS' if not errors else 'FAIL', 'SCHEMA_ERROR',
                source_file='contracts.py', input_name=input_name, classification='structure_schema',
                details=dict(schema_path=schema_path, schema_sha256=sha(self.rules.blobs[schema_path]), errors=len(errors)))
            def leaves(error):
                if error.context:
                    for child in error.context:
                        yield from leaves(child)
                else:
                    yield error
            for top in errors:
                for error in leaves(top):
                    self.add('schema_leaf_violation', prefix+list(error.absolute_path), 'FAIL', 'SCHEMA_ERROR',
                        source_file='contracts.py', input_name=input_name, classification='structure_schema',
                        details=dict(message=error.message, validator=error.validator),
                        schema_pointer=pointer(list(error.absolute_schema_path)))
            def nonfinite(x, at):
                if isinstance(x, float) and not math.isfinite(x):
                    self.add('nonfinite_value', at, 'FAIL', 'NONFINITE', source_file='contracts.py', input_name=input_name, classification='structure_numeric')
                elif isinstance(x, dict):
                    for k, v in x.items(): nonfinite(v, at+[k])
                elif isinstance(x, list):
                    for k, v in enumerate(x): nonfinite(v, at+[k])
            nonfinite(data, prefix)

    def run(self):
        self.schema()
        c, m, h, d = self.capture, self.m, self.output['hypotheses'], self.output['decision']
        H, D = ['output', 'hypotheses'], ['output', 'decision']
        p = self.record['provenance']
        self.guard('explicit_model_output_contract',['provenance','agent_output_contract'],p.get('agent_output_contract','2.0.0') in ('2.0.0','2.1.0'),'MODEL_CONTRACT',source_file='contracts.py')
        self.guard('configured_time_grid',['metadata','horizon_s'],abs(m['horizon_s']/m['step_s']-round(m['horizon_s']/m['step_s']))<1e-8 and 1<=m['horizon_s']/m['step_s']<=500,'TIME_GRID',source_file='contracts.py',input_name='capture',details=dict(step_s=m['step_s'],interval_count=m['horizon_s']/m['step_s']))
        self.guard('online_source', ['provenance','source'], p['source']=='online_model', 'SOURCE', 'Synthetic input requires explicit test mode')
        self.guard('output_digest', ['provenance','output_digest'], p.get('output_digest')==canonical_digest(self.output), 'SOURCE', 'Output does not match provenance')
        self.guard('capture_horizon_validity', ['metadata','valid_until_s'], m['valid_until_s']>=m['reference_time_s']+self.horizon, 'TIME_COVERAGE', input_name='capture')
        self.guard('short_prefix', ['safety','prefix_s'], c['safety']['prefix_s']<=min(.5,m['step_s']), 'PREFIX', input_name='capture')
        self.guard('unique_frame_ids', ['observations'], len(self.frame_map)==len(self.frames), 'DUPLICATE_ID', 'Duplicate frame id', input_name='capture')
        clock={}
        for i, f in enumerate(self.frames):
            path=['observations',i]
            self.guard('capture_clock_consistency', path+['time_id'], f['time_id'] not in clock or clock[f['time_id']]==f['capture_time_s'], 'CLOCK_CONFLICT', input_name='capture')
            clock[f['time_id']]=f['capture_time_s']
            self.guard('acquired_history_coverage', path+['capture_time_s'], 0<=m['reference_time_s']-f['capture_time_s']<=m['history_s']+1e-8, 'OBSERVATION_TIME', input_name='capture')
        self.guard('acquired_reference_timestamp', ['metadata','reference_time_id'], clock.get(m['reference_time_id'])==m['reference_time_s'], 'REFERENCE_TIME', input_name='capture')
        self.guard('global_state_reference_time', H+['reference_time_id'], h['reference_time_id']==m['reference_time_id'], 'DYNAMIC_ALIGNMENT', 'Dynamic states must be model-aligned')
        self.guard('coordinate_frame', H+['coordinate_frame'], h['coordinate_frame']==m['coordinate_frame'], 'COORDINATES')
        self.add('units_schema_constant', H+['units'], 'PASS' if h['units']=='m_s_rad' else 'FAIL', 'SCHEMA_ERROR', source_file='contracts.py', details=dict(expected='m_s_rad'))
        self.guard('metric_scale_present', H+['scale_basis'], h['scale_basis']!='unknown', 'MISSING_SCALE', classification='scale_missing', unknown_type='absolute_metric_scale' if h['scale_basis']=='unknown' else None)
        self.guard('verified_projection_profile', H+['scale_basis'], h['scale_basis']!='calibrated_ground_plane', 'CALIBRATED_PROJECTION_UNVERIFIED', classification='scale_support')
        self.guard('road_available_component_of_combined_guard', H+['road'], h['road'] is not None, 'UNBOUNDED_UNKNOWN', classification='road_missing', unknown_type='road_extent' if h['road'] is None else None,
            interval=[0,self.horizon], details=dict(combined_guard_other_value=h['unbounded_unknown'], combined_guard='road is not None and not unbounded_unknown', separate_runtime_error=False))
        self.guard('global_unbounded_unknown_component_of_combined_guard', H+['unbounded_unknown'], not h['unbounded_unknown'], 'UNBOUNDED_UNKNOWN', classification='unbounded_unknown', unknown_type='untyped_global_unbounded_unknown' if h['unbounded_unknown'] else None,
            interval=[0,self.horizon], details=dict(combined_guard_road_is_null=h['road'] is None, combined_guard='road is not None and not unbounded_unknown', scope_not_machine_typed=True, separate_runtime_error=False),
            relevance=dict(basis='model_declared_global_relevance_without_typed_scope', model_declared_relevant_unknown=h['unbounded_unknown'], geometric_relevance_proof=False,
                explanation='The prompt assigns the model responsibility for relevant unknowns. T1 reads one boolean without object/space/time scope or independent relevance computation. The original confidence_note remains available separately.'))
        for name in ('scene_id','revision'):
            self.guard('decision_round_identity_'+name, D+[name], d[name]==m[name], 'SCENE_VERSION_MISMATCH', details=dict(expected=m[name]))
        self.guard('decision_horizon_validity', D+['valid_until_s'], d['valid_until_s']>=m['reference_time_s']+self.horizon, 'DECISION_EXPIRED', interval=[0,self.horizon])
        self.guard('mandatory_hard_requirements', D+['hard_requirements'], {'target_region','vehicle_limits','collision_free','road'}<=set(d['hard_requirements']), 'SEMANTIC_CONTRACT')
        self.guard('behavior_stop_consistency', D+['stop'], (d['behavior']=='stop_yield')==(d['stop'] is not None), 'STOP_INTENT', 'Stop requirements inconsistent with behavior')
        if d['stop'] is not None:
            self.guard('stop_deadline_and_hard_requirements', D+['stop'], {'stop','yield'}<=set(d['hard_requirements']) and 0<=d['stop']['deadline_s']<=self.horizon, 'STOP_INTENT', 'Stop deadline/hard requirements invalid', interval=[0,self.horizon])
        for key in ('behavior','desired_speed_mps','priority','preferences'):
            self.add('decision_field_schema_only', D+[key], 'INFORMATIONAL', source=self.rules.context("m=capture['metadata']"),
                classification='schema_validated_no_additional_T1_guard', details=dict(note='Validated structurally; this field alone is not an unknown-information rejection. Semantic consequences are checked by the explicit stop/traffic guards.'))
        target = self.geometry(D+['target_region'], d['target_region'], convex=True, interval=[0,self.horizon])
        if h['road'] is not None:
            self.geometry(H+['road','polygon_m'], h['road']['polygon_m'], convex=True, item=h['road'], interval=[0,self.horizon])
        else:
            self.add('road_geometry_unavailable', H+['road'], 'NOT_EVALUABLE', 'INVALID_GEOMETRY', source_file='geometry.py', classification='road_missing', details=dict(prerequisite='road must not be null'))
        regions = ([('road',None,h['road'])] if h['road'] is not None else []) + [('unknown_regions',i,r) for i,r in enumerate(h['unknown_regions'])]
        for kind, i, r in regions:
            path=H+[kind]+([] if i is None else [i])
            self.evidence(r,path,interval=[0,self.horizon])
            if kind != 'road': self.geometry(path+['polygon_m'],r['polygon_m'],item=r,interval=[0,self.horizon])
            self.guard('region_uncertainty_extent', path+['uncertainty_m'], r['uncertainty_m'] is not None, 'UNKNOWN_UNCERTAINTY', 'No usable geometric uncertainty extent', item=r, interval=[0,self.horizon], classification='uncertainty_missing', unknown_type='finite_geometric_error_extent' if r['uncertainty_m'] is None else None)
        ids=[o['id'] for o in h['objects']]
        self.guard('unique_object_ids', H+['objects'],len(ids)==len(set(ids)),'DUPLICATE_ID','Duplicate object')
        self.guard('yield_object_references',D+['yield_to'],set(d['yield_to'])<=set(ids),'YIELD_TARGET')
        for i,e in enumerate(h['traffic_evidence']):
            path=H+['traffic_evidence',i]
            self.evidence(e,path,interval=[0,self.horizon])
            self.guard('supported_traffic_rule',path+['rule'],e['rule'] in ('stop_required','yield_required'),'UNSUPPORTED_TRAFFIC_RULE',item=e,interval=[0,self.horizon],classification='traffic_semantic')
            self.guard('known_traffic_condition',path+['state'],e['state'] in ('active','inactive'),'UNKNOWN_TRAFFIC_CONDITION',item=e,interval=[0,self.horizon],classification='traffic_unknown',unknown_type='traffic_requirement_state' if e['state'] not in ('active','inactive') else None)
            if e['state']=='active':
                self.guard('traffic_behavior_consistency',path+['state'],d['behavior']=='stop_yield' and (e['rule']!='yield_required' or bool(d['yield_to'])),'TRAFFIC_INTENT_CONFLICT',item=e,interval=[0,self.horizon],classification='traffic_semantic')
        for i,o in enumerate(h['objects']):
            path=H+['objects',i]
            kw=dict(item=o,object_id=o['id'])
            self.add('object_id_schema',path+['id'],'INFORMATIONAL',source=self.rules.context("for o in h['objects']"),classification='model_declared_object',**kw)
            self.evidence(o,path,object_id=o['id'],interval=[0,self.horizon])
            self.guard('actor_reference_time_citation',path+['time_ids'],m['reference_time_id'] in o['time_ids'],'DYNAMIC_ALIGNMENT','Dynamic state lacks reference-time evidence',interval=[0,0],classification='state_time_alignment',details=dict(expected_time_id=m['reference_time_id'],note='This guard requires literal evidence time-ID membership; it does not inspect a typed state-alignment method.'),**kw)
            self.guard('actor_uncertainty_extent',path+['uncertainty_m'],o['uncertainty_m'] is not None,'UNKNOWN_UNCERTAINTY','Unknown uncertainty extent blocks planning',interval=[0,self.horizon],classification='uncertainty_missing',unknown_type='finite_geometric_error_extent' if o['uncertainty_m'] is None else None,**kw)
            self.add('instantaneous_position_null_allowed',path+['position_m'],'NOT_APPLICABLE' if o['position_m'] is None else 'PASS',source=self.rules.context("if o['position_m'] is not None"),classification='current_state_availability',unknown_type='instantaneous_position_missing' if o['position_m'] is None else None,interval=[0,0],details=dict(null_is_independent_rejection=False,nonnull_has_separate_consistency_check=True),**kw)
            self.add('instantaneous_velocity_null_allowed',path+['velocity_mps'],'INFORMATIONAL',source=self.rules.context("for o in h['objects']"),classification='current_state_availability',unknown_type='instantaneous_velocity_missing' if o['velocity_mps'] is None else None,interval=[0,0],details=dict(null_is_independent_rejection=False,T1_never_reads_velocity=True,not_used_to_predict_occupancy=True),**kw)
            occ=o['occupancy']
            self.guard('whole_interval_occupancy_available',path+['occupancy'],occ is not None,'OCCUPANCY_UNKNOWN',interval=[0,self.horizon],classification='occupancy_coverage_missing',unknown_type='whole_interval_coverage_not_supplied' if occ is None else None,
                details=dict(required_intervals=self.n,supplied_intervals=0 if occ is None else len(occ),null_is_not_empty_occupancy=True,behavior_unknown_vs_measurement_absent_not_machine_typed=True),**kw)
            if occ is None:
                self.add('occupancy_grid_unavailable',path+['occupancy'],'NOT_EVALUABLE','OCCUPANCY_GAP','Missing whole-interval occupancy',interval=[0,self.horizon],classification='occupancy_coverage_missing',details=dict(prerequisite='nonnull occupancy array',missing_interval_grid=[[a,b] for a,b in zip(self.times,self.times[1:])]),**kw)
            else:
                self.guard('whole_interval_occupancy_count',path+['occupancy'],len(occ)==self.n,'OCCUPANCY_GAP','Missing whole-interval occupancy',interval=[0,self.horizon],classification='occupancy_time_coverage',details=dict(expected=self.n,actual=len(occ)),**kw)
                for k,cell in enumerate(occ):
                    cp=path+['occupancy',k]
                    interval=[cell['start_s'],cell['end_s']]
                    if k<self.n:
                        self.guard('whole_interval_time_grid',cp,abs(cell['start_s']-self.times[k])<1e-8 and abs(cell['end_s']-self.times[k+1])<1e-8,'OCCUPANCY_GAP','Occupancy times do not cover grid',interval=interval,classification='occupancy_time_coverage',details=dict(expected_start_s=self.times[k],expected_end_s=self.times[k+1]),**kw)
                    else:
                        self.add('whole_interval_time_grid',cp,'NOT_EVALUABLE','OCCUPANCY_GAP','Occupancy times do not cover grid',interval=interval,classification='occupancy_time_coverage',details=dict(prerequisite='cell index within declared grid; count guard already fails'),**kw)
                    self.add('whole_interval_kind_schema',cp+['kind'],'PASS' if cell['kind']=='whole_interval_envelope' else 'FAIL','SCHEMA_ERROR',source_file='contracts.py',interval=interval,classification='occupancy_representation',details=dict(declaration_is_not_empirical_coverage_proof=True),**kw)
                    for j,alt in enumerate(cell['alternatives_m']):
                        self.geometry(cp+['alternatives_m',j],alt,interval=interval,**kw)
                        self.add('alternative_preservation',cp+['alternatives_m',j],'INFORMATIONAL',source=self.rules.context("for p in c['alternatives_m']:polygon(p)"),interval=interval,classification='conditional_scenario_expression',details=dict(alternative_index=j,alternative_count=len(cell['alternatives_m']),scenario_identity_across_intervals_not_typed=True,premise_reference_not_typed=True,every_provided_alternative_preserved=True,reasonable_omitted_alternatives_not_testable_from_geometry=True),**kw)
            if o['position_m'] is not None:
                prereq=occ is not None and len(occ)>0 and o['uncertainty_m'] is not None
                polys=[]
                if prereq:
                    try:
                        polys=[Polygon(p) for p in occ[0]['alternatives_m']]
                        prereq=bool(polys) and all(p.is_valid and p.area>1e-8 for p in polys)
                    except (TypeError,ValueError): prereq=False
                ok=prereq and any(p.distance(Point(o['position_m']))<=o['uncertainty_m']+1e-8 for p in polys)
                self.add('position_initial_envelope_consistency',path+['position_m'],'PASS' if ok else 'FAIL' if prereq else 'NOT_EVALUABLE','HYPOTHESIS_CONFLICT',interval=[0,self.times[1]],classification='hypothesis_conflict',details=dict(prerequisite='nonnull uncertainty and valid initial whole-interval occupancy',prerequisites_satisfied=prereq),**kw)
        self.guard('ego_reference_origin',['ego'],all(abs(c['ego'][k])<1e-8 for k in ('x_m','y_m','yaw_rad')),'EGO_FRAME',input_name='capture')
        if 'acquisition_metadata' in c:
            a=c['acquisition_metadata'];path=['acquisition_metadata']
            self.guard('camera_center_offset',path+['center_offset_assumption_m'],abs(a['center_offset_assumption_m']-c['vehicle']['center_to_rear_axle_m'])<1e-8,'CALIBRATION_REFERENCE',input_name='capture')
            pairs={(f['id'],f['time_id']) for f in self.frames}
            self.guard('ego_history_matches_frames',path+['ego_history'],len(a['ego_history'])==len(self.frames) and {(x['frame_id'],x['time_id']) for x in a['ego_history']}==pairs,'EGO_HISTORY','Ego history does not match acquisition records',input_name='capture')
            self.guard('ego_history_reference_origin',path+['ego_history'],all(max(abs(x['center_xy_m'][0]),abs(x['center_xy_m'][1]),abs(x['yaw_rad']))<1e-8 for x in a['ego_history'] if x['time_id']==m['reference_time_id']),'EGO_HISTORY','Reference-time ego history must be rebased to center zero',input_name='capture')
            self.guard('camera_calibration_present',path+['calibrations'],all(f['camera_id'] in a['calibrations'] for f in self.frames),'CALIBRATION_MISSING',input_name='capture')
            for key,cal in a['calibrations'].items():
                R=np.asarray(cal['camera_to_center_rotation']);K=np.asarray(cal['camera_intrinsic'])
                self.guard('camera_calibration_matrix',path+['calibrations',key],np.allclose(R.T@R,np.eye(3),atol=1e-6) and abs(np.linalg.det(R)-1)<1e-6 and abs(np.linalg.det(K))>1e-9,'CALIBRATION',input_name='capture')
        self.guard('rear_axle_inside_vehicle',['vehicle','center_to_rear_axle_m'],c['vehicle']['center_to_rear_axle_m']<=c['vehicle']['rear_m'],'VEHICLE',input_name='capture')
        note_source=self.rules.context("h=output['hypotheses']")
        self.add('conditional_note_not_machine_interpreted',H+['confidence_note'],'INFORMATIONAL',source=note_source,interval=[0,self.horizon],classification='conditional_scenario_expression',details=dict(field_never_read_by_T1=True,free_text_retained_verbatim=True,no_probability_calibration=True,no_machine_typed_premises=True,no_machine_typed_coverage_completeness=True,literal_excerpts=note_excerpts(h['confidence_note']),excerpt_limit='Keyword spans are textual evidence only; they do not prove actor-specific relevance, missing causes or credible bounds.'))
        # This reproduces a documented downstream diagnostic algebra only. It is
        # not a T1 fault and never grants feasibility, a corridor or acceptance.
        if target is not None:
            pts=list(target.exterior.coords)
            if not target.exterior.is_ccw: pts=list(reversed(pts))
            planes=[]
            for a,b in zip(pts,pts[1:]):
                dx,dy=b[0]-a[0],b[1]-a[1];length=math.hypot(dx,dy)
                if length>1e-10: planes.append((dy/length,-dx/length,(dy*a[0]-dx*a[1])/length))
            offset=c['vehicle']['center_to_rear_axle_m'];ego=c['ego']
            rear=[ego['x_m']-offset*math.cos(ego['yaw_rad']),ego['y_m']-offset*math.sin(ego['yaw_rad'])]
            safe=all(nx*rear[0]+ny*rear[1]<=b-offset+1e-8 for nx,ny,b in planes)
            self.add('conservative_center_to_rear_initial_membership',D+['target_region'],'PASS' if safe else 'FAIL',source=self.rules.context("if any(nx*q[0]",file='diagnostics.py'),stage='T2_DIAGNOSTIC',classification='downstream_configuration_conservatism',interval=[0,0],details=dict(initial_center_inside_declared_polygon=target.covers(Point(ego['x_m'],ego['y_m'])),initial_rear_state=rear,center_to_rear_offset_m=offset,configuration_inset_m=offset,not_a_T1_guard=True,not_a_live_failure_cause=True))
        return self.rows


def summary_for(audit):
    rows=audit.rows
    failures=[r for r in rows if r['status']=='FAIL' and r['stage']=='T1']
    return dict(**audit.base, first_feedback=audit.feedback,
        counts=dict(checks=len(rows), statuses=dict(Counter(r['status'] for r in rows)),
                    failed_T1_predicates=len(failures), failed_T1_codes=dict(Counter(r['error_code'] for r in failures)),
                    failed_T1_classes=dict(Counter(r['classification'] for r in failures)),
                    downstream_diagnostic_failures=sum(r['status']=='FAIL' and r['stage']=='T2_DIAGNOSTIC' for r in rows)),
        issue_ids=[r['issue_id'] for r in rows if r['status']=='FAIL'],
        object_availability=[dict(object_id=o['id'], position_available=o['position_m'] is not None,
            velocity_available=o['velocity_mps'] is not None, uncertainty_available=o['uncertainty_m'] is not None,
            occupancy_available=o['occupancy'] is not None, intervals=0 if o['occupancy'] is None else len(o['occupancy']),
            max_alternatives=0 if o['occupancy'] is None else max(len(c['alternatives_m']) for c in o['occupancy']),
            representation_state='coverage_not_supplied' if o['occupancy'] is None else 'finite_declared_interval_regions_with_unknown_residual_extent' if o['uncertainty_m'] is None else 'finite_declared_whole_interval_occupancy',
            future_behavior_uncertainty_not_a_machine_typed_per_object_field=True,
            evidence= audit.evidence_refs(o), explicitly_yielded_to=o['id'] in audit.output['decision']['yield_to'])
            for o in audit.output['hypotheses']['objects']],
        original_confidence_note=audit.output['hypotheses']['confidence_note'],
        confidence_note_literal_excerpts=note_excerpts(audit.output['hypotheses']['confidence_note']),
        interpretation='Independent offline predicates on original fields. No later guard was reached by substituting values; latent failures are not recorded historical tool calls.')


def write_csv(path, rows):
    if not rows:
        write_text_new(path,'')
        return
    fields=list(rows[0])
    with open_new(path,'x',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
        for row in rows:
            w.writerow({k:json.dumps(v,ensure_ascii=False,sort_keys=True,allow_nan=False) if isinstance(v,(dict,list)) or v is None else v for k,v in row.items()})


def compact_tables(rows):
    """Deduplicate long source/clause quotations without losing original values."""
    rules={};clauses={};sources={};result=[]
    def register(catalog,value):
        if value is None: return None
        key=canonical_digest(value)
        catalog.setdefault(key,value)
        return key
    for original in rows:
        row=dict(original)
        source=row.pop('source_ref')
        clause=row.pop('contract_ref')
        row['source_rule_ref']=register(rules,source)
        row['contract_clause_ref']=register(clauses,clause)
        row['guard_file']=source['file'] if source else None
        row['guard_line']=source['line'] if source else None
        row['guard_source_sha256']=source['sha256'] if source else None
        row['guard_error_category']=source.get('category') if source else None
        # The original response path+SHA remains explicit in every row. Paired
        # capture/feedback hashes remain losslessly available in source_catalog.
        row['capture_source_ref']=register(sources,row.pop('capture'))
        row['feedback_source_ref']=register(sources,row.pop('feedback'))
        result.append(row)
    return result,dict(rules=rules,clauses=clauses,sources=sources)


def build(out=DEFAULT_OUT):
    out=reserve_directory(out,readonly_inputs=[EVIDENCE])
    before={str(p):sha(p.read_bytes()) for p in EVIDENCE.rglob('*') if p.is_file()}
    all_rows=[];summaries=[];inputs={};run_reports=[]
    for group,subdir in RUNS:
        run=EVIDENCE/subdir;rules=FrozenRules(run);capture_path=None;agents=[]
        numbered=sorted(p for p in run.glob('*.json') if NUMBERED.match(p.name))
        for index,path in enumerate(numbered):
            kind=NUMBERED.match(path.name).group(1)
            if kind=='capture': capture_path=path
            elif kind=='agent':
                assert capture_path is not None
                after=numbered[index+1:]
                feedback_path=next(p for p in after if p.name.endswith('-feedback.json'))
                assert not any(p.name.endswith(('-capture.json','-agent.json')) for p in after[:after.index(feedback_path)])
                audit=Audit(rules,path,capture_path,feedback_path,group)
                audit.run()
                for j,row in enumerate(audit.rows): row['issue_id']=audit.response_id+f'/check-{j:05d}'
                all_rows.extend(audit.rows);summaries.append(summary_for(audit));agents.append(audit.response_id)
                for p in (path,capture_path,feedback_path): inputs[relative(p)]=source_inventory(p)
        for p in (run/'0001-source-hashes.json',run/'0002-manifest.json',run/'source_snapshot.zip'):
            inputs[relative(p)]=source_inventory(p)
        run_reports.append(dict(group=group,run=relative(run),manifest=rules.manifest,
            source_archive=source_inventory(rules.zip_path),verified_frozen_sources=rules.source_hash_validation,
            responses=agents,T1_sha256=sha(rules.blobs['implementation/agentic/t1.py'])))
    assert len(summaries)==27, len(summaries)
    assert len({r['sample_id'] for r in summaries})==6
    bywindow=[]
    for sid in sorted({r['sample_id'] for r in summaries}):
        response_set=[r for r in summaries if r['sample_id']==sid]
        groups=[]
        for group,_ in RUNS:
            rs=[r for r in response_set if r['group']==group]
            if not rs: continue
            rs.sort(key=lambda r:r['revision'])
            revisions=[]
            for previous,current in zip(rs,rs[1:]):
                def keys(r):
                    return {(x['json_pointer'],x['error_code'],x['classification']) for x in all_rows if x['response_id']==r['response_id'] and x['status']=='FAIL' and x['stage']=='T1'}
                old,new=keys(previous),keys(current)
                revisions.append(dict(from_response=previous['response_id'],to_response=current['response_id'],
                    resolved_original_path_code_class=[list(x) for x in sorted(old-new)],
                    newly_failed_path_code_class=[list(x) for x in sorted(new-old)],
                    persisted_path_code_class=[list(x) for x in sorted(old&new)],
                    object_identity_warning='Comparison by JSON pointer is structural only; object IDs/order may change. Original object summaries remain available; no GT association is inferred.'))
            groups.append(dict(group=group,prompt=rs[0]['prompt'],responses=[dict(response_id=r['response_id'],revision=r['revision'],
                first_code=r['recorded_first_code'],failed_codes=r['counts']['failed_T1_codes'],failed_classes=r['counts']['failed_T1_classes'],
                objects=len(r['object_availability']),position_available=sum(o['position_available'] for o in r['object_availability']),
                velocity_available=sum(o['velocity_available'] for o in r['object_availability']),
                occupancy_available=sum(o['occupancy_available'] for o in r['object_availability']),
                missing_occupancy=sum(not o['occupancy_available'] for o in r['object_availability'])) for r in rs],revision_changes=revisions))
        bywindow.append(dict(sample_id=sid,scene_type=WINDOW_NAMES[sid][0],original_cohort=WINDOW_NAMES[sid][1],unique_window_count=1,groups=groups))
    issues=[r for r in all_rows if r['status']=='FAIL']
    compact,catalogs=compact_tables(all_rows)
    compact_issues=[r for r in compact if r['status']=='FAIL']
    for name,rows in (('checks',compact),('issues',compact_issues)):
        if name=='checks':
            with open_new(out/'checks.jsonl.gz','xb') as stream:
                with gzip.GzipFile(filename='',mode='wb',fileobj=stream,mtime=0) as f:
                    for row in rows: f.write((json.dumps(row,ensure_ascii=False,sort_keys=True,allow_nan=False)+'\n').encode())
        else:
            with open_new(out/(name+'.jsonl'),'x') as f:
                for row in rows: f.write(json.dumps(row,ensure_ascii=False,sort_keys=True,allow_nan=False)+'\n')
        if name=='issues': write_csv(out/(name+'.csv'),rows)
    write_text_new(out/'reference_catalog.json',json.dumps(catalogs,ensure_ascii=False,indent=2)+'\n')
    write_text_new(out/'format.json',json.dumps(dict(version='t1_field_table_v1',
        table_format='checks.jsonl.gz is deterministic gzip-compressed JSON Lines; one independent check per line. issues.jsonl and issues.csv contain failures only.',
        original_value='raw_value is the exact original JSON value at input/json_pointer. Null is JSON null, never empty occupancy.',
        references=dict(source_rule_ref='reference_catalog.json /rules/{id}: frozen source archive, commit, SHA, source line, original predicate, message and category',
            contract_clause_ref='reference_catalog.json /clauses/{id}: frozen Git document, SHA, line and exact clause',
            capture_source_ref='reference_catalog.json /sources/{id}: capture file path, SHA and bytes',
            feedback_source_ref='reference_catalog.json /sources/{id}: original feedback file path, SHA and bytes'),
        statuses=dict(PASS='Predicate holds on original values; not acceptance or calibrated coverage',
            FAIL='Independent predicate fails; actual_execution distinguishes first refusal from masked or downstream-only issues',
            NOT_EVALUABLE='A required value/branch prerequisite is absent; not a proven extra violation',
            NOT_APPLICABLE='The explicit guard does not apply, e.g. position=null skips the conflict test',
            INFORMATIONAL='Original field preserved; no independent rejection or no machine-parsed semantics'),
        coverage='All source guard families, all declared nonnull interval cells, every supplied alternative polygon and every object state/evidence field; no missing fields are filled.'),ensure_ascii=False,indent=2)+'\n')
    write_text_new(out/'README.md','''# 逐响应、逐字段离线诊断表

`issues.csv` / `issues.jsonl` 保留失败谓词；`checks.jsonl.gz` 包含全部通过、失败、不可评及信息项。每行的 `record.path`、`record.sha256`、`json_pointer`、`raw_value` 可直接定位原始值。配对 capture/feedback、冻结代码、条款原文及提示词位置通过 `reference_catalog.json` 无损解引用；格式见 `format.json`。

`responses.json` 为27份有效响应分别保存实录首拒、独立潜在错误、对象可用性及原始条件说明；`windows.json` 按六个唯一窗口、实验组/提示词和修订分开，修订字段差异不自动建立对象身份对应。它们是诊断分布，不是新增在线成功率。

表内 `recorded_first_rejection` 才是当时实际首拒。`independent_failed_predicate_masked_in_recorded_run` 只表示原值可独立检出的后续问题；`NOT_EVALUABLE` 表示缺少依赖项，不能算额外已证实错误。`T2_DIAGNOSTIC` 单列潜在下游保守性，不是这些在线运行的实际失败原因。未知瞬时位置/速度的 null 单独不触发 T1 拒绝；occupancy=null 不等于空占用。

长表可离线读取：

```python
import gzip, json
from pathlib import Path
root = Path('implementation/results/t1_unknown_diagnosis/fields')
catalog = json.loads((root / 'reference_catalog.json').read_text())
with gzip.open(root / 'checks.jsonl.gz', 'rt') as stream:
    for line in stream:
        row = json.loads(line)
        rule = catalog['rules'].get(row['source_rule_ref'])
        # row['raw_value'] 是原字段值；rule 是冻结源码中的位置与谓词。
```

所有模型、工具和执行调用均为0。没有向缺失值填数，没有用真值，没有改变旧接受器结论。完整输入SHA、冻结源码ZIP复核及旧证据起止字节保护见 `inputs.json`、`frozen_rule_sources.json`、`summary.json`。源码提交和实际脚本SHA记录在 summary；未经确认不启动新模型实验。
''')
    write_text_new(out/'responses.json',json.dumps(summaries,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    write_text_new(out/'windows.json',json.dumps(bywindow,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    write_text_new(out/'frozen_rule_sources.json',json.dumps(run_reports,ensure_ascii=False,indent=2)+'\n')
    write_text_new(out/'inputs.json',json.dumps(list(inputs.values()),ensure_ascii=False,indent=2)+'\n')
    after={str(p):sha(p.read_bytes()) for p in EVIDENCE.rglob('*') if p.is_file()}
    assert before==after, 'Frozen evidence changed during analysis'
    report=dict(analysis='offline_t1_field_diagnosis_v1',created_at_utc=datetime.now(timezone.utc).isoformat(),
        source_commit=subprocess.run(['git','rev-parse','HEAD'],cwd=REPO,check=True,capture_output=True,text=True).stdout.strip(),
        analysis_source=source_inventory(Path(__file__)),source_tree_dirty=subprocess.run(['git','status','--porcelain'],cwd=REPO,check=True,capture_output=True,text=True).stdout.strip()!='',
        valid_responses=len(summaries),unique_windows=len(bywindow),original_four_unique_windows=4,
        recorded_first_codes=dict(Counter(r['recorded_first_code'] for r in summaries)),
        field_checks=len(all_rows),failed_T1_predicates=sum(r['stage']=='T1' for r in issues),
        latent_and_first_failed_T1_codes=dict(Counter(r['error_code'] for r in issues if r['stage']=='T1')),
        failure_response_counts={code:len({r['response_id'] for r in issues if r['stage']=='T1' and r['error_code']==code}) for code in sorted({r['error_code'] for r in issues if r['stage']=='T1'})},
        downstream_potential_failures=sum(r['stage']=='T2_DIAGNOSTIC' for r in issues),
        frozen_files_checked=len(before),frozen_byte_preservation=True,
        online_model_calls=0,tool_calls=0,acceptance_recomputed=False,original_acceptance_changed=False,
        category_notes={
            'first_vs_latent':'Recorded feedback is the actual first failure. Independent additional failed predicates are latent diagnostics, never extra recorded tool calls.',
            'combined_guard':'Road-null and global unbounded-unknown are separate field components of one historical UNBOUNDED_UNKNOWN guard, not two historical failures.',
            'nulls':'Null instantaneous position or velocity alone does not reject T1. Null occupancy and uncertainty have separate hard guards.',
            'bounds':'A whole_interval_envelope label, finite polygon and self-reported uncertainty do not establish calibrated probability or empirical coverage.',
            'relevance':'All declared objects are universally required by T1; no candidate corridor or geometric irrelevance proof is computed here.',
            'free_text':'confidence_note is preserved verbatim but never parsed by T1. Missing-vs-unbounded and scenario premises are not typed per object.',
            'causality':'No T3 code is run or used to explain these historical T1 failures. No artificial examples enter any online numerator or denominator.'})
    write_text_new(out/'summary.json',json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,default=DEFAULT_OUT)
    args=parser.parse_args()
    print(json.dumps(build(args.out),ensure_ascii=False,indent=2))


if __name__=='__main__':
    try:main()
    except Failure as exc:
        print(json.dumps(exc.record,ensure_ascii=False));raise SystemExit(2)
