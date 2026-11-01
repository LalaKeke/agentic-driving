"""Append the existing matched BEV panel to saved planning-frame front GIFs.

Pure image composition: no model, planning, verification or execution calls.
The complete left GIF frame is reused; the right plot is cropped from the
previous four-panel result.png, including its labels and refusal indications.
"""
import argparse
import json
from pathlib import Path
import subprocess

from PIL import Image, ImageDraw

from planning_front_gif import (ROOT, CLIPS, font, read, sha,
                                reserve_directory, write_json_new, write_text_new)

BEV_CROP = (1025, 110, 2170, 755)
LEFT_SIZE = (1280, 970)
HEADER = 100
PLOT_HEIGHT = 720
GAP = 16


def compose(left, source_plot, row):
    """Keep the full left image; fit the original BEV without aspect distortion."""
    if left.size != LEFT_SIZE or source_plot.size != (2170, 1400):
        raise ValueError('Unexpected source layout; refuse to guess a crop')
    right_x = left.width + GAP
    canvas = Image.new('RGB', (right_x + left.width, left.height), '#101c2b')
    canvas.paste(left, (0, 0))
    bev = source_plot.crop(BEV_CROP).convert('RGB')
    bev.thumbnail((left.width, PLOT_HEIGHT), Image.Resampling.LANCZOS)
    canvas.paste('white', (right_x, HEADER, canvas.width, HEADER + PLOT_HEIGHT))
    canvas.paste(bev, (right_x + (left.width-bev.width)//2,
                      HEADER + (PLOT_HEIGHT-bev.height)//2))
    draw = ImageDraw.Draw(canvas)
    draw.text((right_x+22, 12), "LOCAL SCENE / BIRD'S-EYE VIEW",
              fill='white', font=font(25, True))
    draw.text((right_x+22, 53),
              f"{row['case_id']} | recorded +{row['relative_sequence_time_s']:.3f}s | matched to left view",
              fill='#d4e0ee', font=font(19))
    draw.line((right_x, HEADER-4, canvas.width, HEADER-4), fill='#59768e', width=4)
    y = HEADER + PLOT_HEIGHT + 10
    labels = [
        'Original BEV: road, object BODY, future occupancy envelopes and T2 corridor.',
        'Blue: saved 2s plan | Green: consumed 0.2s prefix | See original plot legend.',
        ('Verified local forecast; the full 2s trajectory was not executed.'
         if row['physical'].get('prefix_consumed') else
         'REFUSED: any unreleased candidate is unverified and was NOT consumed.'),
        'Local X forward / Y left (m). Model-conditional geometry; no GT or replanning.',
    ]
    for i, line in enumerate(labels):
        draw.text((right_x+22, y+i*29), line, fill='#c4d5e6', font=font(17))
    return canvas


def main(front_root, bev_root, destination):
    front_root, bev_root = front_root.resolve(), bev_root.resolve()
    out = reserve_directory(destination, readonly_inputs=(front_root, bev_root))
    top = read(front_root/'summary.json')
    summary = dict(new_model_calls=0, new_planner_calls=0, new_execution_calls=0,
                   git_head_at_render=subprocess.check_output(
                       ['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                   script_sha256=sha(__file__), front_root=str(front_root),
                   bev_root=str(bev_root), bev_crop_pixels=BEV_CROP,
                   left_frame_preservation='Full original decoded RGB frame pasted unmodified before normal GIF palette quantization',
                   frame_timing='Original per-frame GIF durations retained exactly', clips=[])
    for item in top['clips']:
        clip = item['clip_id']
        source_gif = front_root/item['gif']
        if sha(source_gif) != item['gif_sha256']:
            raise ValueError('Source GIF differs from its existing manifest')
        records = read(front_root/(clip+'_frames.json'))['records']
        frames = []; durations = []; output_records = []
        max_turn_index = max(range(len(records)),
                             key=lambda k: abs(records[k]['plan_heading_change_deg'] or 0))
        with Image.open(source_gif) as gif:
            if gif.n_frames != len(records):
                raise ValueError('Source GIF and reference list differ')
            for index, record in enumerate(records):
                gif.seek(index)
                left = gif.convert('RGB')
                duration = gif.info['duration']
                if duration != record['display_duration_ms']:
                    raise ValueError('Source GIF timing differs from its existing records')
                directory = bev_root/clip/record['case_id']
                row = read(directory/'result.json')
                for key, metadata_key in [('sample_token', 'sample_token'),
                                          ('source_group', 'selected_source_group'),
                                          ('selected_attempt', 'selected_attempt')]:
                    if row[key] != record[metadata_key]:
                        raise ValueError('Left and right views have different samples or revisions')
                if abs(row['relative_sequence_time_s']-record['relative_sequence_time_s']) > 1e-9:
                    raise ValueError('Left and right reference times differ')
                source_plot = directory/'result.png'
                with Image.open(source_plot) as plot:
                    combined = compose(left, plot, row)
                # Check the actual uncompressed composition, not a re-render.
                if combined.crop((0, 0, *LEFT_SIZE)).tobytes() != left.tobytes():
                    raise ValueError('Left GIF frame was changed during composition')
                frames.append(combined); durations.append(duration)
                output_records.append(dict(case_id=row['case_id'], sample_token=row['sample_token'],
                    source_group=row['source_group'], selected_attempt=row['selected_attempt'],
                    relative_sequence_time_s=row['relative_sequence_time_s'], duration_ms=duration,
                    source_plot=str(source_plot), source_plot_sha256=sha(source_plot),
                    source_row_sha256=sha(directory/'result.json'),
                    prefix_consumed=record['prefix_consumed'], failure_stage=record['failure_stage']))
        path = out/(clip+'.gif')
        with path.open('xb') as stream:
            frames[0].save(stream, format='GIF', save_all=True, append_images=frames[1:],
                           duration=durations, loop=0, disposal=2, optimize=False)
        frames[max_turn_index].save(out/(clip+'_preview.png'))
        with Image.open(path) as decoded:
            if decoded.n_frames != len(records):
                raise ValueError('Composed GIF dropped a planning frame')
            for index, duration in enumerate(durations):
                decoded.seek(index); decoded.load()
                if decoded.info['duration'] != duration:
                    raise ValueError('Composed GIF changed the playback cadence')
        result = dict(clip_id=clip, title=CLIPS[clip], frames=len(frames),
                      dimensions_px=frames[0].size, duration_ms=sum(durations),
                      source_gif_sha256=sha(source_gif), gif=path.name, gif_sha256=sha(path),
                      source_front_records_sha256=sha(front_root/(clip+'_frames.json')),
                      left_frames_unchanged_before_encoding=True, matched_samples_and_revisions=True,
                      verified_frames=sum(r['prefix_consumed'] for r in records),
                      refused_frames=sum(not r['prefix_consumed'] for r in records), records=output_records)
        write_json_new(out/(clip+'_frames.json'), result)
        summary['clips'].append({k:v for k,v in result.items() if k != 'records'})
        print(json.dumps(summary['clips'][-1]), flush=True)
    write_json_new(out/'summary.json', summary)
    text = ['# 规划帧 GIF：前视投影 + 右侧俯视场景', '',
            '保留上一版完整前视GIF（轨迹、近场小窗、状态、消费数值和进度条），右侧逐帧拼接原报告的俯视场景图。**新增模型、规划和执行调用均为0。**', '',
            '|场景|GIF|已核验 / 规划帧|', '|---|---|---:|']
    names = dict(following_deceleration='跟车减速', bus_road='公交路段', parking_bend='停车区弯道')
    for item in summary['clips']:
        text.append(f"|{names[item['clip_id']]}|[播放]({item['gif']})|{item['verified_frames']}/{item['frames']}|")
    text += ['', '右侧直接裁取此前同一参考帧的完整BEV面板，保留模型道路、目标BODY、全部未来占用图形、走廊、自车、轨迹、坐标轴和原图例；不重新推断几何。两侧样本、修订来源、采集参考时间逐一匹配。', '',
             '左侧青色为已核验2秒轨迹、绿色为消费前缀；右侧沿用原图的蓝色轨迹、绿色前缀。失败帧仍保留失败状态，未释放候选若存在仍按原图标注，不能计为消费。', '',
             '每段19帧，播放时长与原GIF逐帧相同。左图完整复用，正常GIF调色板编码可能改变少量颜色；拼接前逐像素核对左图未变。', '',
             '前视仍是展示性z=0投影；各时刻独立录制回放，不是连续反事实执行。原GIF和实验数据未覆盖。', '',
             '零调用复现，输出必须为新目录：', '', '```bash', 'cd /data2/hzy/MAP/agentic-driving',
             'python implementation/experiments/append_bev_to_planning_gif.py \\',
             f'  --front-root {front_root.relative_to(ROOT)} \\',
             f'  --bev-root {bev_root.relative_to(ROOT)} \\',
             '  --out /tmp/new_front_bev_gifs', '```', '']
    write_text_new(out/'README.md', '\n'.join(text))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--front-root', type=Path, default=ROOT/'implementation/results/planning_front_gifs_20261029_v2')
    parser.add_argument('--bev-root', type=Path, default=ROOT/'implementation/results/long_clips_visual_20261029_v2')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    main(args.front_root, args.bev_root, args.out)
