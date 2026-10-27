"""OFFLINE ONLY: truth never imported by adapter, tools, or runtime."""
from collections import defaultdict
import math
import statistics
from shapely.ops import unary_union
from shapely.geometry import shape,box
from .contracts import need,validate,digest,model_contract
from .geometry import polygon

def evaluate(records,truth,config):
    output_contracts={r['provenance'].get('agent_output_contract','2.0.0') for r in records}
    need(len(output_contracts)<=1,'G2_MIXED_CONTRACT','Evaluate model output contracts separately; do not pool protocols')
    need('distance_edges_m' in config and sorted(config['distance_edges_m'])==config['distance_edges_m'],'G2_CONFIG','Provide sorted distance bin edges; no built-in acceptance thresholds')
    need(config.get('evaluation_horizon_s',0)>0,'G2_CONFIG','Provide explicit positive evaluation_horizon_s')
    rows=[];scene_rows=[];repeats=defaultdict(list)
    for record in records:
        need(record['provenance']['source']=='online_model' and bool(record['provenance'].get('image_sha256')),'G2_REAL_ONLY','G2 requires actual model output with image hashes')
        validate(model_contract(record['provenance'].get('agent_output_contract','2.0.0'))[0],record['output'])
        need(record['provenance']['output_digest']==digest(record['output']),'G2_OUTPUT_MISMATCH','Output and provenance differ')
        key=record['sample_id'];gt=truth['samples'][key]
        need(record['provenance']['image_sha256']==gt['image_sha256'],'G2_IMAGE_MISMATCH','Truth and model observations differ')
        h=record['output']['hypotheses'];need(h['reference_time_id']==gt['reference_time_id'],'G2_TIME','Truth reference mismatch')
        need(h['coordinate_frame']==gt['coordinate_frame']=='ego_at_reference','G2_FRAME','Truth coordinate mismatch')
        road_error=None
        road_iou=None
        if h['road'] is not None:
            actual_road=shape(gt['road_geometry_geojson']) if gt.get('road_geometry_geojson') is not None else polygon(gt['road_polygon_m']) if gt.get('road_polygon_m') is not None else None
            if actual_road is not None:
                predicted_road=polygon(h['road']['polygon_m'])
                if gt.get('evaluation_roi_m') is not None:
                    roi=box(*gt['evaluation_roi_m']);actual_road=actual_road.intersection(roi);predicted_road=predicted_road.intersection(roi)
                if not actual_road.is_empty and not predicted_road.is_empty:road_error=predicted_road.boundary.hausdorff_distance(actual_road.boundary)
                union_area=actual_road.union(predicted_road).area
                road_iou=actual_road.intersection(predicted_road).area/union_area if union_area else None
        actors={o['id']:o for o in h['objects']}
        # Ground-truth actor IDs must use the explicitly reviewed offline association.
        association=truth.get('record_associations',{}).get(record.get('record_id'),gt['model_object_ids'])
        scene_rows.append(dict(sample_id=key,record_id=record.get('record_id'),road_hausdorff_m=road_error,road_area_iou=road_iou,unassociated_model_object_count=len(set(actors)-set(association.values()))))
        for actor in gt['objects']:
            covered_until=0.
            for interval in actor['occupancy']:
                need(abs(interval['start_s']-covered_until)<1e-8 and interval['end_s']>covered_until,'G2_TRUTH_COVERAGE','Truth occupancy must be ordered and continuous')
                covered_until=interval['end_s']
            need(covered_until>=config['evaluation_horizon_s']-1e-8,'G2_TRUTH_COVERAGE','Truth does not cover evaluation horizon')
            o=actors.get(association.get(actor['id']));dist=math.hypot(*actor['position_m']);edges=config['distance_edges_m'];bucket=sum(dist>=x for x in edges)
            position_error=speed_error=coverage=None
            expected_intervals=sum(x['start_s']<config['evaluation_horizon_s'] for x in actor['occupancy'])
            covered_intervals=0;available_intervals=0;coverage_status='unassociated';cover=[]
            if o:
                if o['position_m'] is not None:
                    position_error=math.dist(o['position_m'],actor['position_m']);repeats[(key,actor['id'])].append(o['position_m'])
                if o['velocity_mps'] is not None and actor['velocity_mps'] is not None:speed_error=math.dist(o['velocity_mps'],actor['velocity_mps'])
                cover=[]
                for interval in actor['occupancy']:
                    if interval['start_s']>=config['evaluation_horizon_s']:break
                    pred=[x for x in (o['occupancy'] or []) if x['start_s']<=interval['start_s']+1e-9 and x['end_s']>=interval['end_s']-1e-9]
                    if not pred:continue
                    covered_intervals+=1
                    if o['uncertainty_m'] is None:continue
                    available_intervals+=1
                    pred_shape=unary_union([polygon(p) for x in pred for p in x['alternatives_m']]).buffer(o['uncertainty_m'])
                    actual=polygon(interval['polygon_m']);cover.append(pred_shape.intersection(actual).area/actual.area)
                coverage=min(cover) if cover and available_intervals==expected_intervals else None
                coverage_status='unknown_occupancy' if o['occupancy'] is None else 'complete' if available_intervals==expected_intervals else 'unknown_uncertainty' if o['uncertainty_m'] is None else 'partial_or_missing_time_coverage'
            rows.append(dict(sample_id=key,record_id=record.get('record_id'),actor_id=actor['id'],distance_m=dist,distance_bucket=bucket,occlusion=actor['occlusion'],scene_type=gt['scene_type'],matched=o is not None,position_error_m=position_error,velocity_error_mps=speed_error,road_hausdorff_m=road_error,minimum_interval_area_coverage=coverage,occupancy_status=coverage_status,expected_intervals=expected_intervals,supplied_time_intervals=covered_intervals,evaluable_intervals=available_intervals,minimum_available_interval_area_coverage=min(cover) if o and cover else None))
    def summary(items):
        result={'count':len(items),'matched_count':sum(x['matched'] for x in items)}
        for metric in ['position_error_m','velocity_error_mps','road_hausdorff_m','minimum_interval_area_coverage']:
            values=[x[metric] for x in items if x[metric] is not None]
            result[metric]={'available':len(values),'mean':statistics.mean(values) if values else None,'min':min(values) if values else None,'max':max(values) if values else None}
        return result
    groups=defaultdict(list)
    for row in rows:groups[str((row['distance_bucket'],row['occlusion'],row['scene_type']))].append(row)
    stability=[]
    for key,positions in repeats.items():
        stability.append(dict(sample_id=key[0],actor_id=key[1],calls=len(positions),position_std_m=[statistics.pstdev(p[j] for p in positions) if len(positions)>1 else None for j in (0,1)]))
    return dict(metric_version='g2_availability_v2',agent_output_contract=next(iter(output_contracts),None),status='measured_no_acceptance_claim',scene_metrics=scene_rows,metrics=summary(rows),strata={k:summary(v) for k,v in groups.items()},rows=rows,repeated_call_stability=stability,experiment_config=config,thresholds_applied=False,note='Raw hypothesis coverage; not calibrated safety probability. Thresholds require reviewed experiment criteria.')
