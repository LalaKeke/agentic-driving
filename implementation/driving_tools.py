"""Deterministic straight-road contract prototype. No model, perception or prediction calls.

Only axis-aligned metric scenes at one reference time are supported. A separate
foundation-model adapter must provide the scene hypotheses in an actual experiment.
"""
from copy import deepcopy
import json
import math
from pathlib import Path

from jsonschema import Draft7Validator, RefResolver

ROOT = Path(__file__).resolve().parent
BASE = 'https://driving-v1.local/schemas/'
STORE = {}
for path in (ROOT / 'schemas').glob('*.json'):
    schema = json.loads(path.read_text(encoding='utf-8'))
    STORE[schema['$id']] = schema
EPS = 1e-7


class ToolError(Exception):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code, self.message, self.details = code, message, details


def require(condition, code, message, **details):
    if not condition:
        raise ToolError(code, message, **details)


def validate_document(schema_name, data):
    """Resolve every schema locally; reject nonfinite JSON numbers as well."""
    def finite(value):
        if isinstance(value, float):
            require(math.isfinite(value), 'NONFINITE_NUMBER', '禁止 NaN 或无穷数。')
        elif isinstance(value, dict):
            for item in value.values(): finite(item)
        elif isinstance(value, list):
            for item in value: finite(item)
    finite(data)
    schema = STORE[BASE + schema_name]
    def no_network(uri):
        raise ToolError('SCHEMA_REFERENCE_ERROR', '禁止通过网络解析 Schema。', uri=uri)
    resolver = RefResolver(schema['$id'], schema, store=STORE,
                           handlers={'https': no_network, 'http': no_network})
    errors = list(Draft7Validator(schema, resolver=resolver).iter_errors(data))
    if errors:
        raise ToolError('SCHEMA_ERROR', '数据不符合接口 Schema。',
                        errors=[{'path': '/'.join(map(str, e.path)), 'message': e.message}
                                for e in errors[:4]])


def rect(x0, x1, y0, y1):
    return dict(zip(('xmin_m', 'xmax_m', 'ymin_m', 'ymax_m'), (x0, x1, y0, y1)))


def check_rect(r):
    require(r['xmin_m'] < r['xmax_m'] and r['ymin_m'] < r['ymax_m'],
            'INVALID_GEOMETRY', '矩形边界无效。', bounds=r)


def inside(r, x, y):
    return r['xmin_m'] - EPS <= x <= r['xmax_m'] + EPS and r['ymin_m'] - EPS <= y <= r['ymax_m'] + EPS


def contains_rect(outer, inner):
    return inside(outer, inner['xmin_m'], inner['ymin_m']) and inside(outer, inner['xmax_m'], inner['ymax_m'])


def expand(r, x, y=None):
    y = x if y is None else y
    return rect(r['xmin_m']-x, r['xmax_m']+x, r['ymin_m']-y, r['ymax_m']+y)


def intersection(a, b):
    x0, x1 = max(a['xmin_m'], b['xmin_m']), min(a['xmax_m'], b['xmax_m'])
    y0, y1 = max(a['ymin_m'], b['ymin_m']), min(a['ymax_m'], b['ymax_m'])
    return rect(x0, x1, y0, y1) if x1-x0 > EPS and y1-y0 > EPS else None


def subtract(a, b):
    cut = intersection(a, b)
    if cut is None: return [a]
    pieces = [rect(a['xmin_m'], cut['xmin_m'], a['ymin_m'], a['ymax_m']),
              rect(cut['xmax_m'], a['xmax_m'], a['ymin_m'], a['ymax_m']),
              rect(cut['xmin_m'], cut['xmax_m'], a['ymin_m'], cut['ymin_m']),
              rect(cut['xmin_m'], cut['xmax_m'], cut['ymax_m'], a['ymax_m'])]
    return [r for r in pieces if r['xmax_m']-r['xmin_m'] > EPS and r['ymax_m']-r['ymin_m'] > EPS]


def same_version(meta, other):
    # Includes provenance, clocks, grid and coordinate frame, not just scene_id.
    require(meta == other, 'SCENE_VERSION_MISMATCH', '工具输入属于不同场景版本或时空约定。')


def grid(meta):
    count = round(meta['horizon_s'] / meta['step_s'])
    require(count > 0 and abs(count*meta['step_s']-meta['horizon_s']) < EPS,
            'INVALID_TIME_GRID', '预测窗口必须是步长的整数倍。')
    return [round(k*meta['step_s'], 9) for k in range(count+1)]


def check_decision(scene, decision):
    m = scene['metadata']
    require(decision['scene_id'] == m['scene_id'] and decision['revision'] == m['revision'],
            'SCENE_VERSION_MISMATCH', '驾驶决策不属于当前场景版本。')
    require(decision['valid_until_s']+EPS >= m['reference_time_s']+m['horizon_s'],
            'DECISION_EXPIRED', '驾驶决策不能覆盖整个规划窗口。')
    require(scene['valid_until_s']+EPS >= m['reference_time_s']+m['horizon_s'],
            'SCENE_EXPIRED', '场景有效期不足。')
    require(decision['behavior'] in ('stop_yield','resume'), 'UNSUPPORTED_BEHAVIOR',
            '参考实现仅实现停车让行与直道起步；其他行为必须显式扩展。')
    if decision['behavior']=='stop_yield':
        require(0 < decision['stop_deadline_s'] <= m['horizon_s'],
                'INVALID_DEADLINE', '停车截止时刻必须位于规划窗口内。')
    require(set(decision['yield_to']) <= {o['id'] for o in scene['objects']},
            'MISSING_YIELD_TARGET', '让行对象没有对应场景假设。')


def check_scene(scene):
    times = grid(scene['metadata'])
    check_rect(scene['road_bounds'])
    for r in scene['unknown_regions']: check_rect(r)
    ids = [f['id'] for f in scene['observations']]
    require(len(ids) == len(set(ids)), 'DUPLICATE_ID', '图像编号重复。')
    known = set(ids)
    reference = scene['metadata']['reference_time_s']
    for f in scene['observations']:
        require(-EPS <= reference-f['capture_time_s'] <= 2+EPS,
                'OBSERVATION_TIME_MISMATCH', '图像不在本轮历史两秒窗口内。')
    require(any(abs(f['capture_time_s']-reference)<EPS for f in scene['observations']),
            'OBSERVATION_TIME_MISMATCH', '本轮参考时刻没有对应采集图像。')
    require(set(scene['road_evidence_frame_ids']) <= known,
            'MISSING_EVIDENCE', '道路证据引用不存在。')
    object_ids = [o['id'] for o in scene['objects']]
    require(len(object_ids) == len(set(object_ids)), 'DUPLICATE_ID', '目标编号重复。')
    for o in scene['objects']:
        require(set(o['evidence_frame_ids']) <= known,
                'MISSING_EVIDENCE', '目标证据引用不存在。', object_id=o['id'])
        intervals = o['occupancy_intervals']
        require(len(intervals) == len(times)-1, 'OCCUPANCY_COVERAGE_GAP',
                '未来占用必须逐区间完整覆盖规划窗口。', object_id=o['id'])
        for k, item in enumerate(intervals):
            check_rect(item['bounds'])
            require(abs(item['start_s']-times[k]) < EPS and abs(item['end_s']-times[k+1]) < EPS,
                    'OCCUPANCY_COVERAGE_GAP', '未来占用区间存在空缺、乱序或重叠。', object_id=o['id'], index=k)
        require(inside(expand(intervals[0]['bounds'], o['position_uncertainty_m']),
                       o['position']['x_m'], o['position']['y_m']),
                'HYPOTHESIS_INCONSISTENT', '目标当前位置与首段占用假设冲突。', object_id=o['id'])
    ego = scene['ego']
    require(abs(ego['x_m']) < EPS and abs(ego['y_m']) < EPS and abs(ego['yaw_rad']) < EPS,
            'UNSUPPORTED_INITIAL_POSE', '本直道剖面要求参考时刻自车位于原点、航向为零。')


def check_corridor(scene, corridor):
    same_version(scene['metadata'], corridor['metadata'])
    times = grid(scene['metadata'])
    require(len(corridor['cells']) == len(times)-1, 'CORRIDOR_COVERAGE_GAP', '走廊时域不完整。')
    for k, cell in enumerate(corridor['cells']):
        require(abs(cell['start_s']-times[k]) < EPS and abs(cell['end_s']-times[k+1]) < EPS,
                'CORRIDOR_COVERAGE_GAP', '走廊时间网格不一致。', index=k)
        for r in cell['allowed_rectangles']: check_rect(r)


def t1(payload):
    h, m = payload['model_hypotheses'], payload['metadata']
    require(abs(h['observed_at_s']-m['reference_time_s']) < EPS,
            'HYPOTHESIS_TIME_MISMATCH', '参考实现不对动态目标做隐式外推或时间补偿。')
    scene = {k: deepcopy(payload[k]) for k in ('metadata', 'observations', 'ego', 'vehicle')}
    scene.update({k: deepcopy(h[k]) for k in ('road_bounds', 'road_uncertainty_m',
                 'road_evidence_frame_ids', 'objects', 'unknown_regions')})
    scene['valid_until_s'] = m['reference_time_s']+m['horizon_s']
    check_scene(scene)
    check_decision(scene, payload['decision'])
    return {'scene': scene}


def t2(payload):
    scene, decision = payload['scene'], payload['decision']
    check_scene(scene); check_decision(scene, decision)
    v, road, margin = scene['vehicle'], scene['road_bounds'], payload['safety_margin_m']
    shrink = scene['road_uncertainty_m']
    # Reference-point configuration space. Vehicle size is applied exactly once.
    domain = rect(road['xmin_m']+v['rear_m']+shrink,
                  min(road['xmax_m']-v['front_m']-shrink,
                      decision['stop_before_x_m'] if decision['behavior']=='stop_yield' else math.inf),
                  max(road['ymin_m']+v['half_width_m']+shrink,
                      decision['lane_center_y_m']-decision['max_lateral_deviation_m']),
                  min(road['ymax_m']-v['half_width_m']-shrink,
                      decision['lane_center_y_m']+decision['max_lateral_deviation_m']))
    require(domain['xmin_m'] < domain['xmax_m'] and domain['ymin_m'] < domain['ymax_m'],
            'CORRIDOR_EMPTY', '道路、车身尺寸或停车要求导致空走廊。')
    times, cells = grid(scene['metadata']), []
    for k in range(len(times)-1):
        allowed = [domain]
        occupied = [(o['occupancy_intervals'][k]['bounds'], o['position_uncertainty_m']) for o in scene['objects']]
        occupied += [(r, 0.) for r in scene['unknown_regions']]
        for bounds, uncertainty in occupied:
            b = expand(bounds, uncertainty+margin)
            forbidden = rect(b['xmin_m']-v['front_m'], b['xmax_m']+v['rear_m'],
                             b['ymin_m']-v['half_width_m'], b['ymax_m']+v['half_width_m'])
            allowed = [piece for a in allowed for piece in subtract(a, forbidden)]
        require(bool(allowed), 'CORRIDOR_EMPTY', '某时段无可行空间。', start_s=times[k])
        cells.append({'start_s':times[k], 'end_s':times[k+1], 'allowed_rectangles':allowed})
    require(any(inside(r, scene['ego']['x_m'], scene['ego']['y_m']) for r in cells[0]['allowed_rectangles']),
            'INITIAL_STATE_BLOCKED', '自车初态不在可行走廊内。')
    for k in range(1, len(cells)):
        require(any(intersection(a,b) for a in cells[k-1]['allowed_rectangles'] for b in cells[k]['allowed_rectangles']),
                'CORRIDOR_DISCONNECTED', '相邻时段走廊没有连续衔接区域。', start_s=times[k])
    return {'corridor': {'metadata':deepcopy(scene['metadata']), 'safety_margin_m':margin, 'cells':cells}}


def braking_trajectory(scene, acceleration):
    """Analytical straight-line candidate. It never invents obstacle motion."""
    speed = scene['ego']['speed_mps']
    stop_time = -speed/acceleration if acceleration < 0 else math.inf
    points = []
    for t in grid(scene['metadata']):
        q = min(t, stop_time)
        points.append({'time_s':t, 'x_m':speed*q+0.5*acceleration*q*q,
                       'y_m':0., 'yaw_rad':0., 'speed_mps':max(0.,speed+acceleration*q),
                       'acceleration_mps2':acceleration if t < stop_time-EPS else 0.,
                       'curvature_per_m':0.})
    return {'metadata':deepcopy(scene['metadata']), 'points':points,
            'solver':{'name':'straight_longitudinal_candidate_search', 'candidate_count':7, 'objective':0.},
            'requested_prefix_s':min(.2, scene['metadata']['horizon_s'])}


def t3(payload):
    scene, decision, corridor = payload['scene'], payload['decision'], payload['corridor']
    check_scene(scene); check_decision(scene, decision); check_corridor(scene, corridor)
    require(abs(decision['lane_center_y_m']) < EPS, 'UNSUPPORTED_LATERAL_MOTION',
            '直道参考求解器不生成横向运动。')
    candidates = []
    accelerations=(0., -.5, -1., -1.5, -2., -2.5, -3.) if decision['behavior']=='stop_yield' else (0.,.5,1.,1.5,2.)
    for acceleration in accelerations:
        if -acceleration > scene['vehicle']['max_brake_mps2']+EPS: continue
        if acceleration > scene['vehicle']['max_accel_mps2']+EPS: continue
        if decision['behavior']=='stop_yield':
            stop_time=-scene['ego']['speed_mps']/acceleration if acceleration<0 else math.inf
            # Controls are piecewise constant on the declared grid, including stop.
            if stop_time <= scene['metadata']['horizon_s'] and abs(stop_time/scene['metadata']['step_s']-round(stop_time/scene['metadata']['step_s']))>EPS:continue
            traj = braking_trajectory(scene, acceleration)
        else:
            traj = accelerating_trajectory(scene,acceleration,decision['desired_speed_mps'])
            if traj is None:continue
        traj['solver']['candidate_count']=len(accelerations)
        pts = traj['points']
        if any(p['speed_mps'] > scene['vehicle']['max_speed_mps']+EPS for p in pts): continue
        if decision['behavior']=='stop_yield':
            stopping = -scene['ego']['speed_mps']/acceleration if acceleration < 0 else math.inf
            if stopping > decision['stop_deadline_s']+EPS: continue
            if pts[-1]['x_m'] > decision['stop_before_x_m']+EPS: continue
        feasible = True
        for i, cell in enumerate(corridor['cells']):
            sweep = rect(pts[i]['x_m'],pts[i+1]['x_m'],0.,0.)
            if not any(contains_rect(r,sweep) for r in cell['allowed_rectangles']):
                feasible = False; break
        if feasible:
            dt = scene['metadata']['step_s']
            score = sum((.05*(p['speed_mps']-decision['desired_speed_mps'])**2
                         + p['acceleration_mps2']**2)*dt for p in pts[:-1])
            traj['solver']['objective'] = score
            candidates.append(traj)
    require(bool(candidates), 'NO_FEASIBLE_TRAJECTORY',
            '纵向候选集合内无可行解；不代表一般非线性规划问题已被证明无解。')
    return {'trajectory':min(candidates,key=lambda t:t['solver']['objective'])}


def accelerating_trajectory(scene, acceleration, target_speed):
    initial_speed=scene['ego']['speed_mps']
    if target_speed < initial_speed-EPS:return None
    switch=(target_speed-initial_speed)/acceleration if acceleration>EPS else math.inf
    horizon,step=scene['metadata']['horizon_s'],scene['metadata']['step_s']
    if switch<=horizon and abs(switch/step-round(switch/step))>EPS:return None
    points=[]
    for t in grid(scene['metadata']):
        q=min(t,switch)
        x=initial_speed*q+.5*acceleration*q*q+(target_speed*(t-q) if t>switch else 0.)
        points.append({'time_s':t,'x_m':x,'y_m':0.,'yaw_rad':0.,
                       'speed_mps':min(target_speed,initial_speed+acceleration*q),
                       'acceleration_mps2':acceleration if t<switch-EPS else 0.,'curvature_per_m':0.})
    return {'metadata':deepcopy(scene['metadata']),'points':points,
            'solver':{'name':'straight_longitudinal_candidate_search','candidate_count':5,'objective':0.},
            'requested_prefix_s':min(.2,horizon)}


def t4(payload):
    scene, decision, corridor, trajectory = (payload[k] for k in ('scene','decision','corridor','trajectory'))
    check_scene(scene); check_decision(scene, decision); check_corridor(scene, corridor)
    same_version(scene['metadata'], trajectory['metadata'])
    pts, times, v = trajectory['points'], grid(scene['metadata']), scene['vehicle']
    require(len(pts) == len(times) and all(abs(p['time_s']-t)<EPS for p,t in zip(pts,times)),
            'TRAJECTORY_TIME_MISMATCH', '轨迹必须完整覆盖本轮时间网格。')
    require(trajectory['requested_prefix_s'] <= scene['metadata']['horizon_s']+EPS,
            'PREFIX_OUT_OF_RANGE', '请求执行前缀超出核验窗口。')
    violations, minimum = [], math.inf
    def fail(code, t, p, measured, required, unit, message, repair, object_id=None):
        violations.append({'code':code,'object_id':object_id,'time_s':t,
                           'position':{'x_m':p['x_m'],'y_m':p['y_m']},
                           'measured':float(measured),'required':float(required),'unit':unit,
                           'message':message,'repair_request':repair})
    for key, tol in [('x_m',1e-5),('y_m',1e-5),('yaw_rad',1e-5),('speed_mps',1e-5)]:
        if abs(pts[0][key]-scene['ego'][key])>tol:
            fail('INITIAL_STATE_MISMATCH',0.,pts[0],pts[0][key],scene['ego'][key],key,
                 '轨迹初态与自车状态不一致。','按当前自车状态重新规划。')
    for p in pts:
        if abs(p['yaw_rad'])>EPS or abs(p['curvature_per_m'])>EPS:
            fail('UNSUPPORTED_MOTION',p['time_s'],p,abs(p['yaw_rad']),0.,'rad',
                 '该核验器仅支持直道零航向轨迹。','使用直道轨迹，或扩展并验证核验器。')
        if p['speed_mps']>v['max_speed_mps']+EPS:
            fail('SPEED_LIMIT',p['time_s'],p,p['speed_mps'],v['max_speed_mps'],'m/s','速度超限。','降低目标速度。')
        if not -v['max_brake_mps2']-EPS <= p['acceleration_mps2'] <= v['max_accel_mps2']+EPS:
            fail('ACCELERATION_LIMIT',p['time_s'],p,p['acceleration_mps2'],v['max_brake_mps2'],'m/s^2',
                 '加速度超限。','重新规划加减速过程。')
        if decision['behavior']=='stop_yield' and p['x_m']>decision['stop_before_x_m']+EPS:
            fail('STOP_BOUNDARY',p['time_s'],p,p['x_m'],decision['stop_before_x_m'],'m',
                 '轨迹越过停车参考点边界。','提前停车。')
        if decision['behavior']=='stop_yield' and p['time_s']>=decision['stop_deadline_s']-EPS and p['speed_mps']>1e-5:
            fail('STOP_DEADLINE',p['time_s'],p,p['speed_mps'],0.,'m/s','停车期限后仍在移动。','在截止时刻前停车。')
    # Independent collision checks against scene occupancies, not optimizer status
    # or T2's obstacle subtraction. Occupancies describe WHOLE time intervals.
    road = scene['road_bounds']; ru = scene['road_uncertainty_m']
    for k, (a,b) in enumerate(zip(pts,pts[1:])):
        dt = b['time_s']-a['time_s']
        expected_v = a['speed_mps']+a['acceleration_mps2']*dt
        expected_x = a['x_m']+a['speed_mps']*dt+.5*a['acceleration_mps2']*dt*dt
        if decision['behavior']=='stop_yield' and a['time_s'] <= decision['stop_deadline_s'] < b['time_s']:
            speed_at_deadline=a['speed_mps']+a['acceleration_mps2']*(decision['stop_deadline_s']-a['time_s'])
            if speed_at_deadline>1e-5:
                elapsed=decision['stop_deadline_s']-a['time_s']
                at_deadline={'x_m':a['x_m']+a['speed_mps']*elapsed+.5*a['acceleration_mps2']*elapsed**2,
                             'y_m':a['y_m']}
                fail('STOP_DEADLINE',decision['stop_deadline_s'],at_deadline,speed_at_deadline,0.,'m/s',
                     '采样间的停车截止时刻仍在移动。','提前完成停车。')
        if expected_v < -1e-5 or abs(b['speed_mps']-expected_v)>1e-5 or abs(b['x_m']-expected_x)>1e-5 or abs(b['y_m']-a['y_m'])>EPS:
            fail('DYNAMICS_RESIDUAL',a['time_s'],a,abs(b['x_m']-expected_x),1e-5,'m',
                 '轨迹不满足约定的分段常加速度直线运动。','重新生成连续轨迹。')
        # With verified straight nonnegative velocity, extrema lie at endpoints.
        center_sweep = rect(min(a['x_m'],b['x_m']),max(a['x_m'],b['x_m']),
                            min(a['y_m'],b['y_m']),max(a['y_m'],b['y_m']))
        body = rect(center_sweep['xmin_m']-v['rear_m'],center_sweep['xmax_m']+v['front_m'],
                    center_sweep['ymin_m']-v['half_width_m'],center_sweep['ymax_m']+v['half_width_m'])
        road_safe = rect(road['xmin_m']+ru,road['xmax_m']-ru,road['ymin_m']+ru,road['ymax_m']-ru)
        if not contains_rect(road_safe,body):
            fail('ROAD_BOUNDARY',a['time_s'],a,0.,1.,'boolean','车身扫掠范围越出已知道路。','修改轨迹或重新检查道路假设。')
        if not any(contains_rect(r,center_sweep) for r in corridor['cells'][k]['allowed_rectangles']):
            fail('CORRIDOR_BOUNDARY',a['time_s'],a,0.,1.,'boolean','轨迹不在时空走廊内。','重新规划。')
        if max(abs(a['y_m']-decision['lane_center_y_m']),abs(b['y_m']-decision['lane_center_y_m'])) > decision['max_lateral_deviation_m']+EPS:
            fail('LANE_INTENT',a['time_s'],a,abs(a['y_m']-decision['lane_center_y_m']),decision['max_lateral_deviation_m'],'m',
                 '轨迹偏离指定车道区域。','遵循智能体车道决策。')
        occupied = [(o['id'],expand(o['occupancy_intervals'][k]['bounds'],o['position_uncertainty_m'])) for o in scene['objects']]
        occupied += [('unknown_region',r) for r in scene['unknown_regions']]
        for oid, obstacle in occupied:
            dx = max(obstacle['xmin_m']-body['xmax_m'],body['xmin_m']-obstacle['xmax_m'],0.)
            dy = max(obstacle['ymin_m']-body['ymax_m'],body['ymin_m']-obstacle['ymax_m'],0.)
            clearance = math.hypot(dx,dy)
            minimum = min(minimum,clearance)
            if clearance <= EPS or clearance+EPS < corridor['safety_margin_m']:
                fail('OBSTACLE_CLEARANCE',a['time_s'],a,clearance,corridor['safety_margin_m'],'m',
                     '区间车身扫掠与目标占用的保守间距不足。','提前减速或请智能体修订场景假设。',oid)
    passed = not violations
    return {'verification':{'metadata':deepcopy(scene['metadata']),'verdict':'PASS' if passed else 'FAIL',
            'violations':violations,'minimum_clearance_m':None if math.isinf(minimum) else minimum,
            'checked_until_s':times[-1],'released_prefix_s':trajectory['requested_prefix_s'] if passed else 0.,
            'claim':'conditional_on_supplied_scene_and_bounds'}}


def call_tool(request, *, allow_synthetic=False):
    tool = request.get('tool') if isinstance(request,dict) else None
    require(tool in ('T1','T2','T3','T4'), 'UNKNOWN_TOOL', '未知工具。')
    request_id = request.get('request_id')
    if not isinstance(request_id,str) or not request_id:
        request_id='invalid-request'
    try:
        validate_document(tool+'.schema.json',request)
        require(request['kind']=='request','INVALID_MESSAGE_KIND','工具只接收 request。')
        payload = request['payload']
        metadata = payload['metadata'] if tool=='T1' else payload['scene']['metadata']
        require(allow_synthetic or metadata['data_origin']=='foundation_model',
                'SYNTHETIC_INPUT_DISABLED','人工场景仅允许显式测试模式使用。')
        output = {'T1':t1,'T2':t2,'T3':t3,'T4':t4}[tool](payload)
        response = {'kind':'response','tool':tool,'request_id':request_id,'status':'ok','payload':output}
        validate_document(tool+'.schema.json',response)
        return response
    except ToolError as exc:
        return {'kind':'response','tool':tool,'request_id':request_id,'status':'error',
                'error':{'code':exc.code,'message':exc.message,'details':exc.details}}


def make_request(tool, payload, suffix='demo'):
    return {'kind':'request','tool':tool,'request_id':suffix+'-'+tool.lower(),'payload':deepcopy(payload)}


def pipeline(initial_request, *, allow_synthetic=False):
    """Returns all request/response messages; never executes a vehicle controller."""
    trace=[]
    def invoke(request):
        response=call_tool(request,allow_synthetic=allow_synthetic)
        trace.extend([deepcopy(request),deepcopy(response)])
        if response['status']!='ok':
            raise ToolError('PIPELINE_STOPPED','工具失败，停止本轮。',tool=request['tool'],response=response)
        return response['payload']
    scene=invoke(initial_request)['scene']; decision=initial_request['payload']['decision']
    corridor=invoke(make_request('T2',{'scene':scene,'decision':decision,'safety_margin_m':1.}))['corridor']
    trajectory=invoke(make_request('T3',{'scene':scene,'decision':decision,'corridor':corridor}))['trajectory']
    verification=invoke(make_request('T4',{'scene':scene,'decision':decision,'corridor':corridor,'trajectory':trajectory}))['verification']
    return {'trace':trace,'scene':scene,'decision':deepcopy(decision),'corridor':corridor,
            'trajectory':trajectory,'verification':verification}
