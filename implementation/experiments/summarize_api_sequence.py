"""Read-only combined views of the fixed API sequence and explicit recoveries.

Keep an existing pass; otherwise show the last group with an actual attempt.
Every failed and successful attempt in every group remains in the accounting.
"""
import argparse
from copy import deepcopy
import csv
import hashlib
import html
import json
import math
import os
from pathlib import Path
import tempfile

from summarize_bend_sequence import (align_captures, frame_result, render_frame,
                                     render_overview, LIMITATION)
from agentic.evidence_io import reserve_directory, write_json_new, write_text_new


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def combine(selected, histories, group):
    """A recovery-group initial answer is not the first request overall."""
    row = deepcopy(selected)
    attempts = [dict(attempt, source_group=name) for name, history in histories
                for attempt in history['attempts']]
    row.update(selected_group_status=selected['status'], source_group=group,
               group_histories=[dict(source_group=name, result=history) for name, history in histories],
               overall_attempts=attempts,
               selected_group_charged_requests=selected['charged_requests'],
               charged_requests=sum(history['charged_requests'] for _, history in histories),
               api_post_requests=sum(history.get('api_post_requests', 0) for _, history in histories),
               native_json_count=sum(history['native_json_count'] for _, history in histories),
               first_overall_request_pass=bool(attempts and attempts[0]['complete_conditional_chain']),
               service_failure_history=[dict(source_group=name, attempt=attempt['attempt'],
                                             failure=attempt['raw_last_failure'])
                                         for name, history in histories for attempt in history['attempts']
                                         if attempt.get('service_or_interface_failure')])
    row['preflight_failure_history'] = [dict(source_group=name, failure=history['fatal_service_or_interface_failure'])
        for name, history in histories
        if not history['attempts'] and isinstance(history.get('fatal_service_or_interface_failure'), dict)
        and history['fatal_service_or_interface_failure'].get('code', '').startswith('API_PREFLIGHT_')]
    row['post_failure_count'] = sum(bool(attempt.get('service_or_interface_failure'))
                                   and attempt.get('api_post_requests', 0) > 0 for attempt in attempts)
    row['preflight_failure_count'] = len(row['preflight_failure_history'])
    selected_group_index = next(index for index, (name, _) in enumerate(histories) if name == group)
    preceding_groups = {name for name, _ in histories[:selected_group_index]}
    row['prior_service_failures'] = [item for item in row['service_failure_history'] if item['source_group'] in preceding_groups]
    row['first_attempt_pass'] = row['first_overall_request_pass'] if attempts else None
    row['first_generation_post_pass'] = row['first_overall_request_pass'] if attempts else None
    passes = [index + 1 for index, attempt in enumerate(attempts) if attempt['complete_conditional_chain']]
    row['overall_success_request_ordinal'] = passes[0] if passes else None
    if passes and any(history['charged_requests'] for _, history in histories[:selected_group_index]):
        row['status'] = 'RECOVERY_GROUP_PASS_AFTER_PRIOR_ATTEMPT'
    elif passes and any(item['source_group'] in preceding_groups for item in row['preflight_failure_history']):
        row['status'] = 'FIRST_GENERATION_PASS_AFTER_PREFLIGHT_FAILURE'
    selected_preflight = next((failure for failure in row['preflight_failure_history'] if failure['source_group'] == group), None)
    if selected_preflight and not selected['attempts']:
        row.update(status='PREFLIGHT_FAILURE_NO_GENERATION', final_failure_stage='MODELS_PREFLIGHT',
                   final_failure_reasons=[selected_preflight['failure']],
                   selected_native_failure_stage=None, selected_native_failure_reasons=[])
        row['stages']['SERVICE'] = 'NOT_REACHED'
    return row


def main(original_plan, recovery_plan, out, additional_plans=()):
    plan_paths = [original_plan, recovery_plan] + list(additional_plans)
    if len(set(plan_paths)) != len(plan_paths):
        raise ValueError('Do not count a source group twice')
    plans = [read(path) for path in plan_paths]
    if len({plan['experiment_id'] for plan in plans}) != len(plans):
        raise ValueError('Source group identifiers must be distinct')
    roots = [path.parent for path in plan_paths]
    batches = [read(root / 'batch_summary.json') for root in roots]
    if not all(batch.get('ended') is True for batch in batches):
        raise ValueError('Both source batches must have ended=true; no output created')
    cases = plans[0]['cases']
    if len(cases) != 6:
        raise ValueError('The original six recorded references are required')
    cases_by_group = [{case['case_id']: case for case in plan['cases']} for plan in plans]
    if set(cases_by_group[1]) != {case['case_id'] for case in cases[1:]}:
        raise ValueError('Recovery must contain exactly the original final five references')
    fixed_ids = {case['case_id'] for case in cases}
    if any(not set(group) <= fixed_ids for group in cases_by_group):
        raise ValueError('A source group added a different recorded reference')
    rows, results, captures = [], [], []
    for index, case in enumerate(cases):
        histories, candidates = [], []
        for plan, root, group_cases in zip(plans, roots, cases_by_group):
            if case['case_id'] not in group_cases:
                continue
            new_case = group_cases[case['case_id']]
            if any(new_case[key] != case[key] for key in ('sample_token', 'capture_sha256', 'image_sha256')):
                raise ValueError('Recovery changed fixed input identity')
            value, result, cap = frame_result(new_case, root)
            group = plan['experiment_id']
            histories.append((group, value))
            candidates.append((value, result, cap, group))
        passes = [candidate for candidate in candidates if any(
            attempt['complete_conditional_chain'] for attempt in candidate[0]['attempts'])]
        attempted = [candidate for candidate in candidates if candidate[0]['attempts']]
        selected, result, cap, selected_group = passes[0] if passes else (attempted[-1] if attempted else candidates[-1])
        rows.append(combine(selected, histories, selected_group))
        rows[-1]['selection_policy'] = 'EARLIEST_EXISTING_PASS' if passes else ('LATEST_ACTUAL_ATTEMPT_GROUP' if attempted else 'NO_ATTEMPT_IN_ANY_GROUP')
        results.append(result); captures.append(cap)
    totals = dict(charged_requests=sum(row['charged_requests'] for row in rows),
                  api_post_requests=sum(row['api_post_requests'] for row in rows))
    for key, total in totals.items():
        if total != sum(batch[key] for batch in batches):
            raise ValueError('Combined ' + key + ' differs from both source ledgers')
    transforms = align_captures(captures)
    for row, transform in zip(rows, transforms):
        row['display_transform_to_first_frame'] = transform
    out = reserve_directory(out)
    with tempfile.TemporaryDirectory(prefix='api-sequence-mpl-') as cache:
        os.environ['MPLCONFIGDIR'] = cache
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import numpy as np
        from matplotlib.patches import Polygon as Patch
        from PIL import Image, ImageOps
        for row, result, cap in zip(rows, results, captures):
            folder = reserve_directory(out / row['case_id'])
            write_json_new(folder / 'result.json', row)
            display = deepcopy(row)
            display['source'] = row['source'] + '; group=' + row['source_group']
            display['status'] = row['status'] + f"; post failures={row['post_failure_count']}/preflight failures={row['preflight_failure_count']}"
            render_frame(display, result, cap, folder / 'result.png', plt, np, Patch, Image)
        render_overview(rows, results, transforms, out / 'sequence_overview.png', plt, np)
        pages = []
        for row in rows:
            with Image.open(out / row['case_id'] / 'result.png') as picture:
                pages.append(ImageOps.pad(picture.convert('RGB'), (1200, 775), color='white'))
        sheet = Image.new('RGB', (2400, 775 * math.ceil(len(pages) / 2)), 'white')
        for index, page in enumerate(pages):
            sheet.paste(page, ((index % 2) * 1200, (index // 2) * 775))
        sheet.save(out / 'contactsheet.png')
        pages[0].save(out / 'sequence.gif', save_all=True, append_images=pages[1:], duration=1500, loop=0)
        with Image.open(out / 'sequence_overview.png') as overview:
            overview.convert('RGB').save(out / 'casebook.pdf', save_all=True, append_images=pages, resolution=110)
    report = dict(ended=True, analysis_script_sha256=sha(Path(__file__)),
                  unique_scenes=1, unique_observation_times=6, **totals,
                  native_json_count=sum(row['native_json_count'] for row in rows),
                  post_failure_count=sum(row['post_failure_count'] for row in rows),
                  preflight_failure_count=sum(row['preflight_failure_count'] for row in rows),
                  conditional_chain_passes=sum(row['overall_success_request_ordinal'] is not None for row in rows),
                  sources=[dict(plan_path=str(path), plan_sha256=sha(path),
                                batch_sha256=sha(path.parent / 'batch_summary.json'), batch=batch)
                           for path, batch in zip(plan_paths, batches)],
                  no_extra_model_calls=True, GT_used=False, claim=LIMITATION,
                  accounting_scope='Driving POSTs in all listed API groups, including all service and semantic failures; separate adapter-validation requests are not included.',
                  overall_request_ordinal_scope='Generating POST attempts only; non-generating GET preflight failures are retained and counted separately.',
                  recovery_policy='Explicit new groups, no automatic retry; keep existing passes, otherwise select the last actual attempt. A recovery-group first answer is not a first overall attempt.',
                  frames=rows)
    write_json_new(out / 'sequence_summary.json', report)
    columns = ('case_id', 'sample_token', 'relative_sequence_time_s', 'status', 'source_group',
               'charged_requests', 'api_post_requests', 'first_overall_request_pass', 'first_generation_post_pass',
               'overall_success_request_ordinal', 'final_failure_stage', 'post_failure_count', 'preflight_failure_count')
    with (out / 'sequence_summary.csv').open('x', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=columns + ('T4', 'TURN_TASK', 'prefix_consumed', 'displacement_m', 'end_speed_mps'))
        writer.writeheader()
        for row in rows:
            item = {key: row.get(key) for key in columns}
            item.update(T4=row['stages']['T4'], TURN_TASK=row['stages']['TURN_TASK'],
                        **{key: row['physical'].get(key) for key in ('prefix_consumed', 'displacement_m', 'end_speed_mps')})
            writer.writerow(item)
    cards = ''.join('<section><h2>' + html.escape(row['case_id'] + ' | ' + row['status']) + '</h2><p>'
                    + html.escape('Source group: ' + row['source_group'] + '; all-group POSTs: ' + str(row['api_post_requests']))
                    + '</p><a href="' + row['case_id'] + '/result.json">All group histories</a><img src="'
                    + row['case_id'] + '/result.png"></section>' for row in rows)
    write_text_new(out / 'index.html', '<!doctype html><meta charset="utf-8"><title>API recorded turning sequence</title>'
                   '<style>body{font:16px sans-serif;max-width:1400px;margin:auto;padding:20px}img{max-width:100%}</style>'
                   '<h1>连续转弯场景：完整请求与恢复历史</h1><p>' + html.escape(LIMITATION) + '</p><p>'
                   + html.escape(report['recovery_policy']) + '</p><p>Total driving POSTs: ' + str(totals['api_post_requests'])
                   + '</p><p><a href="casebook.pdf">PDF</a> · <a href="sequence.gif">GIF</a> · '
                   '<a href="sequence_summary.csv">CSV</a> · <a href="sequence_summary.json">JSON</a></p>'
                   '<img src="sequence_overview.png">' + cards)
    print(json.dumps(dict(output=str(out), frame_count=6, calls_added=0, **totals)))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--original-plan', required=True, type=Path)
    parser.add_argument('--recovery-plan', required=True, type=Path)
    parser.add_argument('--additional-plan', action='append', type=Path, default=[])
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    main(args.original_plan.resolve(), args.recovery_plan.resolve(), args.out,
         [path.resolve() for path in args.additional_plan])
