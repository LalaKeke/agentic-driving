"""Bind a frozen camera map prediction to its token and gate on all preceding image timestamps."""
import io
import json
import mmap
import pickle

import numpy as np
import torch
from scipy.spatial.transform import Rotation

from cordriver_iter_sparse_road import map_record, sha


def save(path, data):
    with path.open("x") as stream:
        json.dump(data,stream,indent=2,allow_nan=False)


def detection_binding(raw, exported, pose):
    """Bind the same primary prediction record to a token-keyed detection export."""
    if not exported:
        return 0
    b=np.asarray(raw["boxes_3d"][:32])
    if not len(b):
        return 0
    centers=b[:,:3]
    for quat,translation in ((pose[0],pose[1]),(pose[2],pose[3])):
        centers=Rotation.from_quat(np.roll(quat,-1)).apply(centers)+translation
    xyz=np.asarray([d["translation"] for d in exported])
    wlh=np.asarray([d["size"] for d in exported])
    agree=(np.linalg.norm(centers[:,None]-xyz,axis=-1)<1e-4) & (np.abs(b[:,None,[4,3,5]]-wlh).max(axis=-1)<1e-4)
    return int(agree.any(axis=1).sum())


def main(selection, sparse, out):
    cases=json.loads(selection.read_bytes())["cases"]
    infos=sparse/"data/infos/nuscenes_infos_val.pkl"
    run=sparse/"work_dirs/sparsedrive_small_stage2"
    archive, export=run/"results.pkl", run/"submission_vector.json"
    with infos.open("rb") as f:
        timeline=sorted(((r["timestamp"],r["token"],r["scene_token"],r["cams"],
                          (r["lidar2ego_rotation"],r["lidar2ego_translation"],r["ego2global_rotation"],r["ego2global_translation"]))
                         for r in pickle.load(f)["infos"]),key=lambda r:r[0])
    idx={r[1]:i for i,r in enumerate(timeline)}
    # CPU remapping for the existing project prediction archive.
    torch.storage._load_from_bytes=lambda b:torch.load(io.BytesIO(b),map_location="cpu")
    with archive.open("rb") as f:
        raw=pickle.load(f)
    assert len(raw)==len(timeline)
    det_path=run/"results_nusc.json"
    detections=json.loads(det_path.read_bytes())["results"]
    out.mkdir(exist_ok=False,parents=True)
    rows=[]
    with export.open("rb") as f, mmap.mmap(f.fileno(),0,access=mmap.ACCESS_READ) as mapped:
        for c in cases:
            token=c["token"]
            i=idx[token]
            ts,_,scene,cams,pose=timeline[i]
            history=[r for r in timeline if r[2]==scene and r[0]<=ts]
            assert c["reference_time_us"]==ts and c["scene_token"]==scene and c["history"]==[r[1] for r in history[-3:-1]]
            exposures={k:dict(token=v["sample_data_token"],timestamp_us=int(v["timestamp"])) for k,v in cams.items()}
            delta=int(max(v["timestamp"] for r in history for v in r[3].values())-ts)
            raw_head=raw[i]["img_bbox"]
            exported=map_record(mapped,token)
            primary=tuple(np.asarray(raw_head[k]) for k in ("scores","labels","vectors"))
            same=len(exported)==len(primary[0]) and all(np.array_equal(x,y) for x,y in zip(primary,zip(*exported)))
            exported_boxes=detections.get(token,[])
            assert all(b["sample_token"]==token for b in exported_boxes)
            matched=detection_binding(raw_head,exported_boxes,pose)
            admitted=delta<=0 and (same or matched>0)
            features=[dict(rank=j,score=float(s),label=("ped_crossing","divider","boundary")[int(l)],
                           points=v.tolist()) for j,(s,l,v) in enumerate(zip(*primary))]
            if admitted:
                save(out/(token+".json"),dict(token=token,index=i,features=features,cameras=exposures,
                                               score_threshold=.4,coords="source current LiDAR"))
            rows.append(dict(scene=c["name"],token=token,index=i,reference_us=ts,current_cameras=exposures,
                             prior_scene_observations=len(history),latest_exposure_minus_decision_us=delta,
                             token_export_matches=bool(same),same_primary_detection_matches=matched,admitted=bool(admitted)))
    save(out/"sources.json",dict(archive_path=str(archive),primary_sha256=sha(archive),infos_sha256=sha(infos),
                                  token_export_path=str(export),export_sha256=sha(export),detection_export_sha256=sha(det_path),cases=rows))
    print("Bound by token",sum(r["token_export_matches"] for r in rows),"causal and bound",sum(r["admitted"] for r in rows),"/",len(rows))
