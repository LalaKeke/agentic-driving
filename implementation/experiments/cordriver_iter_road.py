"""Construct locally supported road strips from measured visual boundary polylines."""
from dataclasses import dataclass
from functools import lru_cache
import json
from pathlib import Path

import numpy as np
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union


XY = np.array([[.00203327, .99970406], [-.99998053, .00217566]], dtype=np.float32).astype(float)
BODY_ORIGIN_SHIFT = np.array([-.5, 0.])
STEP = .25
BODY_CORNERS = np.array([[1, 1], [-1, 1], [-1, -1], [1, -1]]) * [4.084/2, 1.85/2]


def read(path):
    return json.loads(path.read_bytes())


@dataclass
class Edge:
    rank: int
    points: np.ndarray
    offsets: np.ndarray
    line: LineString


@dataclass
class Crossing:
    rank: int
    offset: float
    lateral: float
    xy: np.ndarray


@dataclass
class Road:
    route: np.ndarray
    edges: dict
    support: object
    cuts: list
    cells: list


def route_basis(route):
    delta = np.diff(route, axis=0)
    lengths = np.linalg.norm(delta, axis=1)
    if np.any(lengths <= 1e-8):
        raise ValueError("Degenerate navigation segment")
    tangents = delta / lengths[:, None]
    offsets = np.r_[0., np.cumsum(lengths)]

    def center(station):
        i = np.clip(np.searchsorted(offsets, station, side="right") - 1, 0, len(tangents)-1)
        return route[i] + (station - offsets[i]) * tangents[i]

    # The tangent window smooths the direction of cross sections at navigation vertices.
    def frame(station):
        tangent = center(station + 1.25) - center(station - 1.25)
        tangent /= np.linalg.norm(tangent)
        return center(station), tangent, np.array([-tangent[1], tangent[0]])

    return frame, offsets[-1]


def edges_from_features(features):
    edges = {}
    for feature in features:
        if feature["label"] != "boundary" or feature["score"] < .4:
            continue
        xy = np.asarray(feature["points"]) @ XY.T + BODY_ORIGIN_SHIFT
        lengths = np.linalg.norm(np.diff(xy, axis=0), axis=1)
        xy = xy[np.r_[True, lengths > 1e-8]]
        if len(xy) < 2:
            continue
        offsets = np.r_[0., np.cumsum(np.linalg.norm(np.diff(xy, axis=0), axis=1))]
        rank = feature["rank"]
        edges[rank] = Edge(rank, xy, offsets, LineString(xy))
    return edges


def crossings(edge, center, tangent, normal):
    x = (edge.points-center) @ tangent
    x[np.abs(x)<1e-10] = 0.
    # Including shared endpoints is harmless because we retain the nearest side crossing.
    indices = np.flatnonzero((x[:-1]*x[1:] <= 0.) & (np.abs(np.diff(x)) > 1e-9))
    for i in indices:
        fraction = x[i] / (x[i]-x[i+1])
        point = edge.points[i] + fraction * (edge.points[i+1]-edge.points[i])
        offset = edge.offsets[i] + fraction * (edge.offsets[i+1]-edge.offsets[i])
        yield Crossing(edge.rank, offset, float((point-center) @ normal), point)


def subcurve(edge, a, b):
    between = edge.points[(edge.offsets > min(a.offset, b.offset)) &
                          (edge.offsets < max(a.offset, b.offset))]
    if b.offset < a.offset:
        between = between[::-1]
    return np.vstack((a.xy, between, b.xy))


def path_between(a, b, edges, cuts):
    limit = 8*STEP + .25
    if a.rank == b.rank:
        if abs(a.offset-b.offset) > limit:
            return None
        return subcurve(edges[a.rank],a,b)
    first, second = edges[a.rank], edges[b.rank]
    meet = first.line.intersection(second.line)
    choices = [meet] if meet.geom_type == "Point" else list(getattr(meet, "geoms", ()))
    paths = []
    for point in choices:
        if point.is_empty or point.geom_type != "Point":
            continue
        xy = np.asarray(point.coords[0])
        if any(direction * float((xy - center) @ tangent) < -1e-7
               for (center,tangent),direction in zip(cuts,(1,-1))):
            continue
        fa, fb = first.line.project(point), second.line.project(point)
        dist = abs(fa-a.offset) + abs(fb-b.offset)
        if dist <= limit:
            ca = Crossing(a.rank,fa,0.,xy)
            cb = Crossing(b.rank,fb,0.,xy)
            paths.append((dist,np.vstack((subcurve(first,a,ca), subcurve(second,cb,b)[1:]))))
    return min(paths,key=lambda v:v[0])[1] if paths else None


def segment_cell(previous, current, edges, cuts):
    if not all(previous) or not all(current):
        return None, "MISSING_SIDE"
    right, left = [path_between(a,b,edges,cuts) for a,b in zip(previous,current)]
    if right is None or left is None:
        return None, "DISCONNECTED_OR_AMBIGUOUS_SIDE"
    left = left[::-1]
    polygon = Polygon(np.round(np.vstack((right, left)), 9))
    if not polygon.is_valid or polygon.area < 1e-9:
        return None, "CROSS_SECTION_FOLD"
    # The nearest-ray rule only checks two sections. Also exclude any intervening
    # predicted island/boundary cutting the interior of this finite cell.
    interior = polygon.buffer(-1e-7)
    if any(interior.intersects(edge.line) for edge in edges.values()):
        return None, "INTERVENING_PREDICTED_BOUNDARY"
    return polygon, "LOCALLY_SUPPORTED"


def build_road(route, edges):
    route = np.asarray(route)
    frame, length = route_basis(route)
    stations = np.arange(-16., length + 24., STEP)
    # Locate actual boundary vertices whose normal station lies between grid cuts.
    # Cutting at the exact endpoint avoids a grid-artifact gap at the end of a line.
    grid_frames = [frame(station) for station in stations]
    positions = np.array([value[0] for value in grid_frames])
    tangents = np.array([value[1] for value in grid_frames])
    extra = []
    for edge in edges.values():
        for point in edge.points:
            side = np.sum((point - positions) * tangents, axis=1)
            for i in np.flatnonzero(side[:-1]*side[1:] < 0):
                def signed(s):
                    center, tangent, _ = frame(s)
                    return float((point-center)@tangent)
                lo, hi = stations[i], stations[i+1]
                for _ in range(42):
                    mid = (lo+hi)*.5
                    if signed(mid)*side[i] > 0:
                        lo = mid
                    else:
                        hi = mid
                extra.append((lo+hi)*.5)
    stations = sorted(set(stations).union(extra))
    cuts, cells, polygons = [], [], []
    previous = [None, None]
    previous_frame = grid_frames[0][:2]
    for station in stations:
        center, tangent, normal = frame(station)
        hits = [hit for edge in edges.values() for hit in crossings(edge, center, tangent, normal)]
        right = min((h for h in hits if h.lateral < 0.), key=lambda h: (-h.lateral,h.rank), default=None)
        left = min((h for h in hits if h.lateral >= 0.), key=lambda h: (h.lateral,h.rank), default=None)
        current = [right, left]
        poly, reason = segment_cell(previous, current, edges,(previous_frame,(center,tangent)))
        cuts.append(dict(s=float(station), right=right.rank if right else None, left=left.rank if left else None))
        cells.append(dict(s=float(station), reason=reason))
        if poly is not None:
            polygons.append(poly)
        previous = current
        previous_frame = (center,tangent)
    merged = unary_union(polygons)
    components = ([merged] if merged.geom_type == "Polygon" else
                  [p for p in getattr(merged, "geoms", []) if p.geom_type == "Polygon"])
    connected = [p for p in components if p.covers(Point(0.,0.))]
    return Road(route, edges, unary_union(connected), cuts, cells)


@lru_cache(maxsize=32)
def load_road(pilot_dir, token):
    base = Path(pilot_dir)
    packet = read(base/"packets_v2"/token/"packet.json")["model_context"]
    features = read(base/"frontend_v2"/token/"visual_map.json")["map_features"]
    return build_road(packet["navigation"]["polyline_guidance_m"], edges_from_features(features))


def check_road(road, body_xy_array, yaws_rad_array):
    centers, yaws = np.asarray(body_xy_array), np.asarray(yaws_rad_array)
    if len(centers) != len(yaws) or not len(yaws) or not np.all(np.isfinite(centers)) or not np.all(np.isfinite(yaws)):
        raise ValueError("Invalid trajectory")
    poses = []
    for i,(center,yaw) in enumerate(zip(centers,yaws)):
        c,s = np.cos(yaw),np.sin(yaw)
        body = Polygon(BODY_CORNERS @ np.array([[c,s],[-s,c]]) + center)
        intersected = [rank for rank,edge in road.edges.items() if body.intersects(edge.line)]
        outside = body.difference(road.support).area
        if intersected:
            status = "OUTSIDE_VISUAL_BAND"
        elif road.support.covers(body):
            status = "CONDITIONAL_PASS"
        else:
            status = "UNKNOWN"
        poses.append(dict(i=i,status=status,uncovered_area_m2=float(outside),intersected_boundary_ranks=intersected))
    statuses = [p["status"] for p in poses]
    status = ("OUTSIDE_VISUAL_BAND" if "OUTSIDE_VISUAL_BAND" in statuses else
              "UNKNOWN" if "UNKNOWN" in statuses else "CONDITIONAL_PASS")
    return dict(status=status, poses=poses)
