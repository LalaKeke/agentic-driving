"""Planning-reference-only GIFs of saved verified center paths in CAM_FRONT.

Read-only visualization: no model, planner, executor, annotations or maps.
Calibration projects a display-only z=0 ground plane; it is not depth truth.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import statistics
import subprocess
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'implementation'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from agentic.evidence_io import reserve_directory, write_json_new, write_text_new
from bend_result_figure import dense
from prototype_figure import load_result

CLIPS = {'following_deceleration': 'Following / deceleration',
         'bus_road': 'Bus / roadside traffic',
         'parking_bend': 'Parking area / curve'}
CYAN = '#24e3ff'
GREEN = '#64ff76'
RED = '#ff727a'


def read(path):
    return json.loads(Path(path).read_text())


def resolve(path):
    path = Path(path)
    return path if path.is_absolute() else ROOT / path


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def font(size, bold=False):
    name = 'DejaVuSans-Bold.ttf' if bold else 'DejaVuSans.ttf'
    return ImageFont.truetype('/usr/share/fonts/truetype/dejavu/' + name, size)


def verified(row):
    return (row['physical'].get('prefix_consumed') is True
            and row['physical'].get('independent_acceptor_pass') is True
            and all(row['stages'].get(s) == 'PASS'
                    for s in ('T4', 'ACCEPTOR', 'EXECUTION')))


def project(points, calibration, scale):
    """CENTER XY is already center-referenced; never add the rear offset again."""
    xyz = np.column_stack((points[:, 1:3], np.zeros(len(points)))).T
    rotation = np.asarray(calibration['camera_to_center_rotation'])
    translation = np.asarray(calibration['camera_to_center_translation_m'])[:, None]
    camera = rotation.T @ (xyz - translation)
    uvw = np.asarray(calibration['camera_intrinsic']) @ camera
    uv = np.full((len(points), 2), np.nan)
    valid = camera[2] > .2
    uv[valid] = (uvw[:2, valid] / uvw[2:3, valid]).T * scale
    return uv, valid


def draw_path(image, uv, valid, color, width):
    # Draw on the image-sized layer so off-screen segments cannot enter the HUD.
    draw = ImageDraw.Draw(image)
    visible = 0
    for a, b, okay in zip(uv, uv[1:], valid[:-1] & valid[1:]):
        if not okay:
            continue
        xy = [tuple(a), tuple(b)]
        draw.line(xy, fill='#102b3c', width=width + 4)
        draw.line(xy, fill=color, width=width)
        visible += int((0 <= a[0] < image.width and 0 <= a[1] < image.height)
                       or (0 <= b[0] < image.width and 0 <= b[1] < image.height))
    return visible


def render_frame(row, index, rows, width):
    cap_path = resolve(row['capture_path'])
    cap = read(cap_path)
    frame = max((f for f in cap['observations'] if f['camera_id'] == 'CAM_FRONT'),
                key=lambda f: f['capture_time_s'])
    if frame['time_id'] != cap['metadata']['reference_time_id']:
        raise ValueError('Projection requires this exact reference camera time')
    image_path = resolve(frame['path'])
    with Image.open(image_path) as source:
        scale = width / source.width
        image = source.convert('RGB').resize((width, round(source.height * scale)),
                                             Image.Resampling.LANCZOS)
    passed = verified(row)
    native = {}; path_segments = prefix_segments = 0; turn = None
    inputs = {'capture': {'path': str(cap_path), 'sha256': sha(cap_path)},
              'image': {'path': str(image_path), 'sha256': sha(image_path)}}
    if passed:
        attempt = resolve(row['runtime_path']) / f"attempt_{row['selected_attempt']:02d}"
        result, _ = load_result(attempt)
        # Only display the selected, actually accepted trajectory. No new solve.
        for name in ('T3', 'T4', 'execution', 'execution_receipt', 'acceptor'):
            path = attempt / (name + '.json')
            inputs[name] = {'path': str(path), 'sha256': sha(path)}
        if result['trajectory'] != read(attempt / 'T3.json'):
            raise ValueError('Selected trajectory differs from its original T3 file')
        if result['verification']['verdict'] != 'PASS':
            raise ValueError('Summary and original T4 disagree')
        samples = np.asarray(dense(result))
        calibration = cap['acquisition_metadata']['calibrations']['CAM_FRONT']
        uv, valid = project(samples, calibration, scale)
        path_segments = draw_path(image, uv, valid, CYAN, 6)
        prefix = samples[:, 0] <= row['physical']['prefix_s'] + 1e-9
        prefix_segments = draw_path(image, uv[prefix], valid[prefix], GREEN, 9)
        # Time markers belong to this forecast, not later recorded ego poses.
        draw = ImageDraw.Draw(image)
        for seconds in (.5, 1., 1.5, 2.):
            k = int(np.argmin(abs(samples[:, 0] - seconds)))
            x, y = uv[k]
            if valid[k] and 10 <= x < width - 80 and 30 <= y < image.height - 10:
                draw.ellipse((x-4, y-4, x+4, y+4), fill=CYAN, outline='#102b3c', width=2)
                label = f'{seconds:g}s'
                box = draw.textbbox((x+9, y-20), label, font=font(15, True))
                draw.rectangle((box[0]-3, box[1]-2, box[2]+3, box[3]+2), fill='#102b3c')
                draw.text((x+9, y-20), label, font=font(15, True), fill=CYAN)
        if path_segments == 0:
            # A short low-speed plan can lie entirely below the camera view.
            # Show its actual local XY in a separately labelled inset, never
            # extend or translate the path to manufacture a front projection.
            left, top, w, h = 18, image.height-223, 310, 205
            draw.rounded_rectangle((left, top, left+w, top+h), radius=8,
                                   fill='#10202e', outline='#a8c7d8', width=2)
            draw.text((left+12, top+10), 'NEAR-FIELD PLAN / BEV', font=font(17, True), fill=CYAN)
            draw.text((left+12, top+35), '2s path outside camera view', font=font(15), fill='white')
            xlim = max(3., float(max(samples[:, 1]))+1.)
            ylim = max(1.5, float(max(abs(samples[:, 2])))+.5)
            def bev(q):
                return (left+w/2-q[2]/ylim*(w/2-18), top+h-32-q[1]/xlim*(h-95))
            local = [bev(q) for q in samples]
            draw.line(local, fill=CYAN, width=4)
            draw.line([bev(q) for q in samples[prefix]], fill=GREEN, width=7)
            x, y = local[0]
            draw.ellipse((x-4, y-4, x+4, y+4), fill=GREEN)
            draw.text((left+12, top+h-25), f'Local CENTER XY | forward range {xlim:.1f} m',
                      font=font(13), fill='#b7cbdc')
        native = result.get('agent_output', {}).get('decision', {})
        turn = math.degrees(samples[-1, 3] - samples[0, 3])
    header, footer = 100, 150
    canvas = Image.new('RGB', (width, header + image.height + footer), '#101c2b')
    canvas.paste(image, (0, header)); draw = ImageDraw.Draw(canvas)
    label = 'VERIFIED / MOVING' if passed else 'REFUSED / NO PREFIX'
    color = GREEN if passed else RED
    draw.text((22, 12), CLIPS[row['_clip']], font=font(25, True), fill='white')
    draw.text((width-340, 17), label, font=font(22, True), fill=color)
    draw.text((22, 53), f"{row['scene_name']} | {row['case_id']} | recorded +{row['relative_sequence_time_s']:.3f}s | planning {index+1}/{len(rows)}",
              font=font(19), fill='#d4e0ee')
    draw.line((0, header-4, width, header-4), fill=color, width=4)
    y = header + image.height + 10
    if passed:
        m = row['physical']
        text = (f"T1-T4 + acceptor PASS | {native.get('behavior')} | 2s plan / 0.2s simulated prefix"
                f" | v0 {row['initial_speed_mps']:.2f} m/s")
        detail = f"Consumed {m['displacement_m']:.3f} m | end speed {m['end_speed_mps']:.3f} m/s | plan heading {turn:+.1f} deg"
    else:
        codes = list(dict.fromkeys(f.get('code', 'UNKNOWN') for f in row['final_failure_reasons']))
        text = f"NO VERIFIED TRAJECTORY | {row['final_failure_stage']} | {', '.join(codes)}"
        detail = 'Original reference image retained. No rejected candidate is displayed as a verified path.'
    draw.text((22, y), text, font=font(17, True), fill=color)
    draw.text((22, y+27), detail, font=font(17), fill='white')
    draw.text((22, y+54), 'CYAN: verified 2s CENTER plan | GREEN: consumed 0.2s (near-field prefix may be outside view)',
              font=font(15), fill='#c4d5e6')
    draw.text((22, y+78), 'Display-only calibrated z=0 projection. Independent recorded states; no stitched execution or GT.',
              font=font(14), fill='#9eb3c9')
    for j, other in enumerate(rows):
        x = 22 + j * ((width-44) / len(rows))
        draw.rounded_rectangle((x, y+110, x+((width-44)/len(rows))-5, y+129), radius=3,
                               fill='#29a56c' if verified(other) else '#ad404b',
                               outline='white' if j == index else None, width=3)
    metadata = dict(case_id=row['case_id'], sample_token=row['sample_token'],
                    time_id=frame['time_id'], relative_sequence_time_s=row['relative_sequence_time_s'],
                    selected_source_group=row['source_group'], selected_attempt=row['selected_attempt'],
                    verified_path_drawn=passed, path_visible_segments=path_segments,
                    front_path_visible=path_segments > 0,
                    near_field_inset_drawn=passed and path_segments == 0,
                    prefix_visible_segments=prefix_segments,
                    prefix_consumed=row['physical'].get('prefix_consumed', False),
                    failure_stage=row['final_failure_stage'], inputs=inputs,
                    plan_heading_change_deg=turn)
    return canvas, metadata


def main(input_root, destination, width):
    input_root = input_root.resolve()
    out = reserve_directory(destination, readonly_inputs=(input_root,))
    summary = dict(new_model_calls=0, new_planner_calls=0, new_execution_calls=0,
                   source_root=str(input_root), script_sha256=sha(__file__),
                   source_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                   projection='R.T @ ([CENTER_x,CENTER_y,0] - camera_to_center_translation); uv = K @ camera / depth',
                   reference_offset_added_again=False, z_m=0, display_only=True,
                   interpolation='Original exact constant-control ego integration from bend_result_figure.dense',
                   failures='Retained reference image and explicit refusal; no rejected path drawn', clips=[])
    for clip in CLIPS:
        source = input_root / clip / 'summary.json'
        original = read(source); rows = original['frames']
        images = []; records = []
        for i, row in enumerate(rows):
            image, record = render_frame(dict(row, _clip=clip), i, rows, width)
            images.append(image); records.append(record)
        times = [r['relative_sequence_time_s'] for r in rows]
        dt = [b-a for a, b in zip(times, times[1:])]
        if not dt or min(dt) <= 0:
            raise ValueError('Planning reference timestamps must strictly increase')
        # GIF stores 10ms increments; last frame holds for the median cadence.
        durations = [max(10, round(t*100)*10) for t in dt+[statistics.median(dt)]]
        path = out / (clip + '.gif')
        with path.open('xb') as stream:
            images[0].save(stream, format='GIF', save_all=True, append_images=images[1:],
                           duration=durations, loop=0, disposal=2, optimize=False)
        with Image.open(path) as decoded:
            if decoded.n_frames != len(rows):
                raise ValueError('GIF did not retain exactly one image per planning reference')
        for record, duration in zip(records, durations):
            record['display_duration_ms'] = duration
        best = max(range(len(records)), key=lambda k: abs(records[k]['plan_heading_change_deg'] or 0))
        images[best].save(out / (clip + '_preview.png'))
        item = dict(clip_id=clip, title=CLIPS[clip], frames=len(rows),
                    verified_path_frames=sum(r['verified_path_drawn'] for r in records),
                    front_visible_path_frames=sum(r['front_path_visible'] for r in records),
                    near_field_inset_frames=sum(r['near_field_inset_drawn'] for r in records),
                    refused_frames=sum(not r['verified_path_drawn'] for r in records),
                    source_summary_sha256=sha(source), gif=path.name, gif_sha256=sha(path),
                    duration_ms=sum(durations), last_frame_hold_ms=durations[-1],
                    first_recorded_time_s=times[0], last_recorded_time_s=times[-1], records=records)
        write_json_new(out / (clip + '_frames.json'), item)
        summary['clips'].append({k:v for k,v in item.items() if k != 'records'})
    write_json_new(out / 'summary.json', summary)
    text = ['# 规划参考帧：轨迹投影前视图 GIF', '',
            '**新增模型/规划/执行调用均为0。** 从已保存结果读取原轨迹，每个GIF只含19个规划参考帧；原8个失败时刻保留，不画未核验候选。', '',
            '|场景|GIF|已核验轨迹帧 / 规划帧|', '|---|---|---:|']
    names = {'following_deceleration':'跟车减速', 'bus_road':'公交路段', 'parking_bend':'停车区弯道'}
    for clip in summary['clips']:
        text.append(f"|{names[clip['clip_id']]}|[播放]({clip['gif']})|{clip['verified_path_frames']}/{clip['frames']}|")
    text += ['', '青色为已核验的2秒车身中心轨迹，绿色为实际模拟消费的0.2秒前缀。近场前缀若不在相机视野内，不移动或延长投影，以文字显示实际消费位移和末速。', '',
             '12个低速时刻的整段2秒轨迹都在前视近场盲区内，这些帧增加标注为“NEAR-FIELD PLAN / BEV”的俯视小窗；小窗是原局部XY轨迹，不是前视投影。其余37个已核验时刻可在前视图看到轨迹。', '',
             '使用该参考时刻相机内外参和展示性z=0平面，按原控制定义精确加密自车曲线；不再次添加1.2m后轴偏移。投影不是深度/道路真值。各帧从独立录制ego状态起步，不表示执行一条连续轨迹后的新观测。', '',
             '播放间隔按原规划采集时间差，量化到GIF的10ms；末帧按该片段规划间隔中位数停留。补跑优先视图沿用原来源选择，不新增成功，不覆盖原证据。', '',
             '零调用复现（必须使用尚不存在的新目录）：', '', '```bash', 'cd /data2/hzy/MAP/agentic-driving',
             'python implementation/experiments/planning_front_gif.py \\',
             f'  --input-root {input_root.relative_to(ROOT)} \\',
             '  --out /tmp/new_planning_front_gifs', '```', '']
    write_text_new(out / 'README.md', '\n'.join(text))
    print(json.dumps({k:v for k,v in summary.items() if k == 'clips'}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-root', type=Path, default=ROOT/'implementation/results/long_clips_visual_20261029_v2')
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--width', type=int, default=1280)
    args = parser.parse_args()
    if args.width < 1000:
        parser.error('--width must be at least 1000 for the status labels')
    main(args.input_root, args.out, args.width)
