"""Render the official collision union and bind every plotted policy to its open-loop score."""
import argparse
import csv
import json
import math
import pickle
import shutil
from collections import Counter
from functools import lru_cache
from html import escape
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from atlas_projection import STYLES, _layer, _detail_crop, project, sha
from review import score_report

STUDY = Path(__file__).resolve().parent
# Projection and line styles follow the project-wide front camera visualizer.
METHODS = {
    "official": ("qp", "发布版 CorDriver"),
    "native": ("initial", "同输入 CorDriver"),
    "fast": ("fixed", "纯快系统"),
    "dual": ("astra", "当前双系统"),
    "gt": ("recorded", "录制自车（评价）"),
}
PAPER, INK, SUB, GRID = "#fafaf6", "#182b35", "#50636a", "#dde3de"
W, H = 1520, 1292
MARGIN = 30
FRONT = (30, 135, 1014, 689)
BEV = (1040, 135, 1490, 689)


@lru_cache(maxsize=32)
def font(size, bold=False):
    file = "NotoSansCJK-Bold.ttc" if bold else "NotoSansCJK-Regular.ttc"
    return ImageFont.truetype(str(Path("/usr/share/fonts/opentype/noto") / file), size, index=2)


def label(draw, xy, text, size=18, color=INK, bold=False, anchor=None):
    draw.text(xy, text, font=font(size, bold), fill=color, anchor=anchor)


def read_json(path):
    return json.loads(path.read_bytes())


def indexed(rows):
    table = {r["token"]: r for r in rows}
    if len(table) != len(rows):
        raise ValueError("Duplicate decision token")
    return table


def bitmap_rgb(path):
    with Image.open(path) as im:
        return im.convert("RGB")


def plot_bev(draw, paths, metric):
    left, top, right, bottom = BEV
    draw.rounded_rectangle(BEV, radius=6, fill="#f0f3f1")
    label(draw, (left+16, top+10), "俯视  /  前方朝上，等比例", 21, bold=True)
    points = np.concatenate(list(paths.values()))
    lower, upper = points.min(axis=0), points.max(axis=0)
    usable_w, usable_h = right-left-70, bottom-top-122
    span = np.maximum(upper-lower, (5, 5))
    scale = min(usable_w/(span[1]+2), usable_h/(span[0]+2))
    center = (lower+upper)/2
    px, py = (left+right)/2, (top+bottom)/2+3

    def pixel(point):
        return px-(point[1]-center[1])*scale, py-(point[0]-center[0])*scale

    step = next(x for x in (0.5, 1, 2, 5, 10, 20, 50) if x*scale>44)
    grid_l, grid_r, grid_t, grid_b = left+18, right-18, top+57, bottom-51
    for coord in np.arange(math.floor((center[1]-(grid_r-px)/scale)/step)*step, center[1]+(px-grid_l)/scale+step, step):
        x,_=pixel((0,coord))
        if grid_l<=x<=grid_r:
            draw.line((x,grid_t,x,grid_b), fill=GRID)
    for coord in np.arange(math.floor((center[0]-(grid_b-py)/scale)/step)*step, center[0]+(py-grid_t)/scale+step, step):
        _,y=pixel((coord,0))
        if grid_t<=y<=grid_b:
            draw.line((grid_l,y,grid_r,y), fill=GRID)
    for method, xy in paths.items():
        key = METHODS[method][0]
        color = STYLES[key][1]
        uv = [pixel(p) for p in xy]
        for i,(a,b) in enumerate(zip(uv,uv[1:])):
            draw.line((*a,*b), fill=color, width=3)
            if i in (2,5):
                x,y=b
                draw.ellipse((x-3,y-3,x+3,y+3),fill=color,outline=INK)
    # The ring marks the plan's reference point at the first positive scoring knot.
    marks = sorted({float(metric[k]) for k in ("first_ACR_hit_s","first_CCR_hit_s") if metric[k]})
    for time in marks:
        x,y=pixel(paths["official"][int(round(2*time))])
        draw.ellipse((x-7,y-7,x+7,y+7),outline="#b12c2b",width=3)
    x,y=pixel((0,0))
    draw.ellipse((x-3,y-3,x+3,y+3),fill=INK)
    label(draw,(grid_l, bottom-41), f"每格 {step:g} m    红圈：原版首次命中时的轨迹点",15,SUB)


def figure(src, row, number, out, dataroot, gt):
    paired = src.get("paired_xy_lidar_m")
    token=src["token"]
    name=f"{row['scene']}-frame-{row['frame_idx']}_{token}"
    ctx=src["model_context"]
    rgb=dataroot / src["front_image"]
    if sha(rgb)!=src["front_sha256"] or row["scene"]!=src["scene"] or int(row["frame_idx"])!=src["frame_idx"]:
        raise ValueError(f"Source mismatch for {token}")
    layers={"official":src["published_xy_lidar_m"]}
    if paired:
        for key,source_key in (("native","qp"),("fast","fixed"),("dual","astra")):
            layers[key]=paired[source_key]
    layers["gt"]=gt
    projections, xy_ego = {}, {}
    linear=np.array(ctx["overlay_geometry"]["xy_guidance_to_reference_ego_linear"])
    for method, coords in layers.items():
        xy=np.array(coords)
        if xy.shape!=(6,2) or not np.isfinite(xy).all():
            raise ValueError(f"Invalid scoring path {method}/{token}")
        # The extra t=0 origin is display-only. L2 is checked only over the six scored knots.
        shown=np.vstack(([0.,0.],xy))
        xy_ego[method]=shown @ linear.T
        projections[method]=project(dict(time_s=[i*.5 for i in range(7)],xy=shown.tolist()),ctx)
        if method!="gt":
            errors=np.linalg.norm(xy-np.asarray(layers["gt"]),axis=1)
            for h in (1,2,3):
                if abs(float(row[f"{method}_L2_{h}s"])-errors[:2*h].mean())>3e-5:
                    raise ValueError(f"Plotted trajectory does not reproduce L2: {token} / {method} / {h}")
    image=bitmap_rgb(rgb)
    camera=ctx["cameras"]["CAM_FRONT"]
    if image.size!=(camera["width_px"],camera["height_px"]):
        raise ValueError(f"Image dimensions differ for {token}")
    dest=out/"figures"/(name+".png")
    thumb=out/"thumbs"/(name+".jpg")
    result={**row,"png":dest.name,"n":number,"layer_visibility":{k:p["visible_s"] for k,p in projections.items()}}
    canvas=Image.new("RGB",(W,H),PAPER)
    draw=ImageDraw.Draw(canvas)
    label(draw,(MARGIN,12),f"{number:03d}   {row['scene']} · 帧 {row['frame_idx']}",31,bold=True)
    parts=[f"{k} 首次 {float(row['first_'+k+'_hit_s']):g} s" for k in ("ACR","CCR") if row["first_"+k+"_hit_s"]]
    fallback = "QP 回退" if row["official_qp_fallback"].lower() in ("true","1") else "QP 返回"
    label(draw,(W-MARGIN,24),"发布版  /  "+"，".join(parts)+"  ·  "+fallback,19,"#913c32",True,"ra")
    act=row["astra_decision"]
    if paired:
        decision=("Astra "+act) if act in ("KEEP","ADJUST") else "此帧慢系统未触发"
    else:
        decision="缺少合规因果输入  /  双系统未评"
    label(draw,(MARGIN,61),f"CAM_FRONT   ·   {decision}     |     {token}",17,SUB)
    for i,(method,(style,name_cn)) in enumerate(METHODS.items()):
        x=MARGIN+i*271
        draw.rectangle((x,111,x+26,118),fill=STYLES[style][1],outline=INK)
        label(draw,(x+35,98),name_cn,18,bold=True)
    overlay=image.copy()
    for method,p in projections.items():
        _layer(overlay,METHODS[method][0],p,image.width/1600.)
    canvas.paste(overlay.resize((FRONT[2]-FRONT[0],FRONT[3]-FRONT[1]),Image.Resampling.LANCZOS),FRONT[:2])
    plot_bev(draw,xy_ego,row)
    crop=_detail_crop(image.size,projections)
    label(draw,(MARGIN,702),"同一前视区域的分层对照",20,bold=True)
    panel_w, panel_h, gap=282,158,12
    for i,(method,(style,name_cn)) in enumerate(METHODS.items()):
        x=MARGIN+i*(panel_w+gap)
        y=744
        tile=image.copy()
        if method in projections:
            _layer(tile,style,projections[method],image.width/1600.)
            status=f"画内 {projections[method]['visible_s']:.2f} s"
        else:
            status="无配对轨迹"
        canvas.paste(tile.crop(crop).resize((panel_w,panel_h),Image.Resampling.LANCZOS),(x,y))
        label(draw,(x,y+panel_h+3),name_cn,16,bold=True)
        label(draw,(x+panel_w,y+panel_h+3),status,15,SUB,anchor="ra")
    base_y=951
    columns=[MARGIN,415,680,952,1410]
    for x,col in zip(columns,["实际返回","ACR · 1 / 2 / 3 s","CCR · 1 / 2 / 3 s","L2 · 1 / 2 / 3 s（m）","平均 L2"]):
        label(draw,(x,base_y),col,17,bold=True)
    for idx,(method,(style,name_cn)) in enumerate(list(METHODS.items())[:4]):
        y=base_y+39+idx*39
        draw.line((MARGIN,y-2,W-MARGIN,y-2),fill=GRID)
        label(draw,(MARGIN,y),name_cn,19)
        values=[]
        for m in ("ACR","CCR","L2"):
            fields=[row[f"{method}_{m}_{h}s"] for h in (1,2,3)]
            values.append("  /  ".join(f"{float(v):.3f}" if m=="L2" else str(int(float(v))) for v in fields) if all(v!="" for v in fields) else "—")
        l2=row[f"{method}_L2_3s"]
        avg=(sum(float(row[f"{method}_L2_{h}s"]) for h in (1,2,3))/3) if l2!="" else None
        values.append("—" if avg is None else f"{avg:.3f}")
        for x,value in zip(columns[1:],values):
            label(draw,(x,y),value,18)
    label(draw,(MARGIN,1168),"ACR 为参与者碰撞，CCR 为路缘碰撞。1 表示该时域内至少命中一次；L2 为至该时域的计分点平均误差。",17,SUB)
    label(draw,(MARGIN,1200),"前视为几何投影，不重建遮挡；曲线按计分坐标绘制，0 秒为显示原点。俯视保留完整 3 秒。",17,SUB)
    label(draw,(MARGIN,1232),"同输入 CorDriver 与双系统共用因果前端。录制自车轨迹只用于离线评价。原版筛选和同输入对照各自计分。",17,SUB)
    canvas.save(dest,compress_level=3)
    canvas.thumbnail((360,306))
    canvas.save(thumb,quality=83)
    return result


def write_indexes(out, rows):
    counts=Counter()
    html_rows, md = [], ["# 全部原生碰撞帧", "", "点击帧号看高清前视、俯视及指标。3 秒碰撞格用 ACR／CCR 表示。", "",
                         "| 编号 | 场景与帧号 | 发布版 ACR／CCR | 同输入原生 → 双系统 ACR／CCR | 双系统平均 L2（m） |",
                         "|---:|---|---|---|---:|"]
    for r in rows:
        paired=r["dual_L2_3s"]!=""
        def hits(m):
            return "／".join(str(int(float(r[f"{m}_{k}_3s"]))) for k in ("ACR","CCR")) if paired or m=="official" else "—"
        tags=["ACR" if float(r["official_ACR_3s"]) else "CCR"]
        if paired:
            tags.append("paired")
            native=bool(max(float(r["native_ACR_3s"]),float(r["native_CCR_3s"])))
            dual=bool(max(float(r["dual_ACR_3s"]),float(r["dual_CCR_3s"])))
            if native: tags.append("native_hit")
            if native and not dual: tags.append("cleared")
            if dual: tags.append("dual_hit")
            if r["astra_decision"] in ("KEEP","ADJUST"):tags.append("astra")
        else: tags.append("missing")
        counts.update(tags)
        title=f"{r['scene']} · 帧 {r['frame_idx']}"
        label_=f"{hits('native')} → {hits('dual')}" if paired else "无配对结果"
        err=sum(float(r[f"dual_L2_{h}s"]) for h in (1,2,3))/3 if paired else None
        num="—" if err is None else f"{err:.3f}"
        href=f"figures/{r['png']}"
        md.append(f"| {r['n']:03d} | [{title}]({href}) | {hits('official')} | {label_} | {num} |")
        thumb=Path(r["png"]).with_suffix(".jpg").name
        html_rows.append(f'<tr data-k="{" ".join(tags)}" data-id="{r["scene"]} {r["frame_idx"]} {r["token"]}"><td>{r["n"]:03d}</td><td><a href="{href}"><img loading="lazy" alt="{title}" src="thumbs/{thumb}"></a></td><td><a href="{href}">{title}</a><small>{r["token"]}</small></td><td>{hits("official")}</td><td>{label_}</td><td>{num}</td></tr>')
    (out/"index.md").write_text("\n".join(md)+"\n")
    groups={"all":"全部","ACR":"发布版 ACR","CCR":"发布版 CCR","paired":"可配对","native_hit":"同输入原生仍碰撞",
            "cleared":"双系统消除碰撞","dual_hit":"双系统仍碰撞","astra":"有真实 Astra 回答","missing":"缺因果输入"}
    opt="".join(f'<option value="{k}">{escape(v)}（{len(rows) if k=="all" else counts[k]}）</option>' for k,v in groups.items())
    page='''<!DOCTYPE html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>CorDriver 碰撞帧图册</title><style>
body{font:16px system-ui,sans-serif;background:#fafaf6;color:#182b35;margin:0 auto;padding:32px 20px;max-width:1220px}h1{font-size:2rem}a{color:#085a70}input,select{padding:8px;margin-right:12px}table{border-collapse:collapse;width:100%;margin-top:20px}td,th{text-align:left;padding:7px 10px;border-bottom:1px solid #d3ddd7}th{position:sticky;top:0;background:#f1f3ef}small{display:block;color:#5b6c70;font:11px monospace}img{width:120px;max-width:100%;height:auto;display:block}p{max-width:900px;line-height:1.6}tr[hidden]{display:none}</style>
<h1>CorDriver 原生碰撞帧图册</h1><p>119 帧，41 个场景。点击缩略图可查看前视投影、完整三秒俯视轨迹及逐时域指标。发布版轨迹用于选帧；同输入原生、纯快系统和双系统用于方法对照。20 帧缺少可评价的因果输入。</p>
<p><a href="metrics/per_frame.csv">逐帧指标 CSV</a> · <a href="metrics/summary.json">汇总 JSON</a> · <a href="README.md">实验口径</a></p>
<label>筛选 <select id="sel">OPTIONS</select></label><label>搜索 <input id="q" placeholder="场景、帧号或 token"></label><output id="count"></output>
<table><thead><tr><th>序号</th><th>图</th><th>场景与帧</th><th>发布版 ACR／CCR</th><th>同输入原生 → 双系统 ACR／CCR</th><th>双系统平均 L2（m）</th></tr></thead><tbody>ROWS</tbody></table><script>
function update(){const q=document.querySelector('#q').value.toLowerCase().trim(), k=document.querySelector('#sel').value;let n=0;for(const tr of document.querySelectorAll('tbody tr')){const show=tr.dataset.id.includes(q)&&(k==='all'||tr.dataset.k.split(' ').includes(k));tr.hidden=!show;n+=Number(show)}document.querySelector('#count').textContent=n+' 帧'}document.querySelector('#sel').onchange=update;document.querySelector('#q').oninput=update;update()</script></html>'''
    (out/"index.html").write_text(page.replace("OPTIONS",opt).replace("ROWS","".join(html_rows)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataroot", type=Path, required=True, help="nuScenes root containing samples/CAM_FRONT")
    parser.add_argument("--infos", type=Path, required=True, help="Original CorDriver validation infos pickle")
    parser.add_argument("--out", type=Path, required=True, help="New directory for the standalone HTML atlas")
    args = parser.parse_args()
    frozen = read_json(STUDY / "atlas_tracks.json")
    if sha(args.infos) != frozen["validation_infos_sha256"]:
        raise ValueError("The validation infos file differs from the registered upstream file")
    with (STUDY / "per_frame.csv").open(newline="", encoding="utf-8") as stream:
        metrics = sorted(csv.DictReader(stream), key=lambda r: (r["scene"], int(r["frame_idx"])))
    worlds = indexed(frozen["cases"])
    if len(worlds) != 119 or set(indexed(metrics)) != set(worlds):
        raise ValueError("Collision cohort sizes or tokens differ")
    with args.infos.open("rb") as stream:
        infos = indexed([r for r in pickle.load(stream)["infos"] if r["token"] in worlds])
    if set(infos) != set(worlds):
        raise ValueError("Validation infos missing frozen decision tokens")
    args.out.mkdir(parents=True, exist_ok=False)
    for directory in ("figures", "thumbs", "metrics"):
        (args.out / directory).mkdir()
    output = []
    for number, row in enumerate(metrics, 1):
        token = row["token"]
        info, world = infos[token], worlds[token]
        camera = info["cams"]["CAM_FRONT"]
        if (int(info["timestamp"]) != world["reference_time_us"] or info["scene_token"] != world["scene_token"]
                or int(info["frame_idx"]) != world["frame_idx"] or camera["sample_data_token"] != world["front_sample_data_token"]
                or int(camera["timestamp"]) != world["front_exposure_us"] or not bool(info["fut_valid_flag"])
                or np.asarray(info["gt_ego_fut_masks"], dtype=bool).reshape(-1).tolist() != [True] * 6
                or bool(world.get("paired_xy_lidar_m")) != (row["causal_status"] == "AVAILABLE")):
            raise ValueError(f"Scene, frame, exposure, future mask or paired binding differs: {token}")
        steps = np.asarray(info["gt_ego_fut_trajs"], dtype=np.float32)
        if steps.shape != (6, 2) or not np.isfinite(steps).all():
            raise ValueError(f"Expected six finite XY increments for {token}")
        # Match the CPU FloatTensor.cumsum convention of the registered CorDriver evaluation.
        gt = np.cumsum(steps, axis=0, dtype=np.float64).astype(np.float32)
        output.append(figure(world, row, number, args.out, args.dataroot, gt))
    write_indexes(args.out, output)
    shutil.copyfile(STUDY / "per_frame.csv", args.out / "metrics/per_frame.csv")
    (args.out / "metrics/summary.json").write_text(json.dumps(score_report(), indent=2) + "\n")
    (args.out / "README.md").write_text(
        "# nuScenes CorDriver 碰撞帧图册\n\n119 个发布版阳性起算帧中，99 帧具备完整因果对照。"
        "车身前视投影使用曝光时刻标定和参考坐标系 z=0 显示平面。录制未来仅用于评价。"
        "\n\n[图册](index.html) · [逐帧分数](metrics/per_frame.csv) · [汇总](metrics/summary.json)\n"
    )
    with (args.out / "visibility.csv").open("x", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["scene", "frame_idx", "token", "png", *METHODS])
        for r in output:
            writer.writerow([r["scene"], r["frame_idx"], r["token"], r["png"],
                             *(r["layer_visibility"].get(k) for k in METHODS)])
    print(f"Rendered {len(output)} frames into {args.out}")


if __name__ == "__main__":
    main()
