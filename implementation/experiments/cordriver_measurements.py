"""Causal input helpers and nominal body poses used for CorDriver intervention routing."""
import json
import math
from pathlib import Path

import numpy as np
from shapely.geometry import Polygon

from cordriver_iter_road import XY, BODY_ORIGIN_SHIFT

TIMES = np.arange(7) * .5


def stream_array(path):
    """Bounded-memory cursor JSON reader; compact consumed text once per chunk."""
    decoder = json.JSONDecoder()
    with Path(path).open('r', encoding='utf8') as f:
        buf, pos, eof, started = '', 0, False, False
        while True:
            if pos > 1024 * 1024:
                buf, pos = buf[pos:], 0
            if pos >= len(buf) and not eof:
                chunk=f.read(1024 * 1024)
                if chunk: buf += chunk
                else: eof=True
            while pos < len(buf) and buf[pos].isspace(): pos += 1
            if pos >= len(buf):
                if eof: raise ValueError('TRUNCATED_JSON_ARRAY')
                continue
            if not started:
                if buf[pos] != '[': raise ValueError('TABLE_NOT_JSON_ARRAY')
                started=True;pos+=1;continue
            if buf[pos] == ',':
                pos+=1;continue
            if buf[pos] == ']':
                return
            try:
                row, end = decoder.raw_decode(buf,pos)
            except json.JSONDecodeError:
                if eof: raise
                buf, pos = buf[pos:], 0
                chunk = f.read(1024 * 1024)
                if not chunk: eof=True
                else: buf += chunk
                continue
            pos = end
            yield row


def body(center, yaw, length, width):
    x, y = center; c, s = math.cos(yaw), math.sin(yaw)
    return Polygon([(x + c * dx - s * dy, y + s * dx + c * dy)
                    for dx, dy in ((-length/2, -width/2), (length/2, -width/2),
                                   (length/2, width/2), (-length/2, width/2))])


def official_qp(row, replay):
    if not row.get('verified_qp_output') or row.get('fallback') or row.get('solver_status')!='optimal':
        return None, 'UPSTREAM_FALLBACK' if row.get('fallback') else 'UNVERIFIED_UPSTREAM_RETURN'
    if replay is None or replay['status']!='BOUND':
        return None, 'MISSING_OR_UNBOUND_NATIVE_LINEAR_STATES'
    states = np.array(replay['state_xy_yaw_speed'])[::2]
    rear_relative = states[:,:2]-states[0,:2]
    heading = states[:,2]
    centers = rear_relative + .5*np.column_stack([np.cos(heading)-1.,np.sin(heading)])
    return np.column_stack([centers,heading]), 'OFFICIAL_LINEARIZED_QP_STATE_BOUND_TO_ARCHIVED_RETURN'


def forecast_rings(visual):
    rings=[[] for _ in TIMES]
    if visual is not None:
        for obj in visual['current_objects']:
            if obj['score']<.4:
                continue
            native = np.asarray(obj['footprint_lidar_xy_m'])@XY.T + BODY_ORIGIN_SHIFT
            for mode,steps in enumerate(obj['future_step_xy_lidar_m']):
                path = np.vstack([np.zeros(2),np.cumsum(np.asarray(steps)@XY.T,axis=0)])
                if len(path)!=len(TIMES):
                    raise ValueError('Unexpected horizon for forecast mode')
                for p,temporal in zip(path,rings):
                    temporal.append((obj['rank'],mode,Polygon(native+p)))
    return rings
