"""Read-only four-scene summary; no models, tools, image inference or GT reads."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

from summarize_six_scene_scout import ROOT, STATUS_LABELS, case_row
from agentic.evidence_io import reserve_directory, write_json_new, write_text_new


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def figure(report, destination):
    with tempfile.TemporaryDirectory(prefix='expansion-summary-mpl-') as cache:
        os.environ['MPLCONFIGDIR'] = cache
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        from matplotlib.ticker import MaxNLocator
        fig, axes = plt.subplots(1, 2, figsize=(14, 5.2))
        counts = report['outcome_distribution']; labels = list(STATUS_LABELS)
        axes[0].barh(range(len(labels)), [counts.get(k, 0) for k in labels], color='#688d9e')
        axes[0].set_yticks(range(len(labels)), [STATUS_LABELS[k] for k in labels])
        axes[0].set_title('Final outcomes (one per unique window)')
        issues = report['issue_distribution_any_attempt']
        axes[1].barh(range(len(issues)), [x['unique_window_count'] for x in issues], color='#c77b45')
        axes[1].set_yticks(range(len(issues)), [x['problem_class'] for x in issues])
        axes[1].set_title('Blocking classes across attempts (may overlap)')
        for ax in axes:
            ax.invert_yaxis(); ax.set_xlim(0, report['unique_windows'])
            ax.xaxis.set_major_locator(MaxNLocator(integer=True)); ax.set_xlabel('Unique windows')
        fig.suptitle('Scene expansion / conditional 2s prototype / actual evidence')
        fig.text(.5, .015, 'Unattempted is neither failure nor success. Revisions do not add windows. No GT scoring.', ha='center', fontsize=9)
        fig.tight_layout(rect=(0, .045, 1, .94)); fig.savefig(destination, dpi=180); plt.close(fig)


def main(root, out):
    root = root.resolve(); plan = read(root/'plan.json'); batch = read(root/'batch_summary.json')
    if batch.get('ended') is not True:
        raise SystemExit('Batch has not ended; no summary outputs written.')
    inputs = read(root/'preparation/inputs.json'); cases = inputs['cases']
    if len(cases) != 4 or len({x['scene_token'] for x in cases}) != 4:
        raise SystemExit('Expected four fixed distinct scenes.')
    identity = lambda rows: {x['case_id']: (x['scene_token'], x['sample_token']) for x in rows}
    if identity(cases) != identity(plan['cases']):
        raise SystemExit('Prepared scene identities differ from the fixed execution plan.')
    labels = {x['case_id']: x for x in cases}
    rows = [case_row(case, root, root/'preparation', labels) for case in cases]
    for row in rows:
        if row['charged_requests'] and row.get('ended') is not True:
            row.update(status='INCOMPLETE_ATTEMPT', first_attempt_pass=False, revision_pass=False)
            for attempt in row['attempts']:
                attempt['artifact_checks_pass_without_finalized_window'] = attempt['complete_conditional_chain']
                attempt['complete_conditional_chain'] = False
    charges = sum(x['charged_requests'] for x in rows); turns = sum(x['turn_start_count'] for x in rows)
    if (plan.get('authorized_calls') != 8 or plan.get('old_budget_inherited') != 0
            or batch.get('authorized_calls') != 8 or batch.get('charged_requests') != charges
            or charges > 8 or turns > charges or any(x['charged_requests'] > 2 for x in rows)
            or any(x['budget_count_matches_summary'] is False for x in rows)):
        raise SystemExit('Budget/evidence inconsistency; no normal summary outputs written.')
    affected = defaultdict(list)
    for row in rows:
        for issue in row['issue_classes_any_attempt']: affected[issue].append(row['case_id'])
    distribution = [dict(problem_class=k, unique_window_count=len(v), windows=v)
                    for k, v in sorted(affected.items(), key=lambda p: (-len(p[1]), p[0]))]
    report = dict(experiment_id=plan['experiment_id'], ended=True, new_model_calls=0, gt_read=False,
        analysis_source_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        analysis_script_sha256=sha(Path(__file__)), helper_sha256=sha(Path(__file__).with_name('summarize_six_scene_scout.py')),
        plan_sha256=sha(root/'plan.json'), inputs_sha256=sha(root/'preparation/inputs.json'),
        batch_summary_sha256=sha(root/'batch_summary.json'), unique_windows=4,
        attempted_windows=sum(x['charged_requests'] > 0 for x in rows), authorized_calls=8,
        charged_requests=charges, turn_start_count=turns, remaining_unused=8-charges,
        budget_counts_equal=charges == turns,
        budget_audit_status='MATCH' if charges == turns else 'CHARGED_BEFORE_DISPATCH_OR_MISSING_EVIDENCE',
        budget_note='Failed attempts consume charges; fewer visible turn/start events are not invented as requests.',
        rows=rows, outcome_distribution=dict(Counter(x['status'] for x in rows)),
        issue_distribution_any_attempt=distribution, batch=batch,
        caveats=['Conditional single-window scene assumptions and simulated prefixes only.',
                 'Not a success-rate estimate, G2 pass, real-vehicle safety proof or counterfactual multi-round loop.',
                 'Behavior remains independently unreviewed; unsupported stationary holding is not moving-task completion.',
                 'Scene labels are selection metadata, not runtime signal/interaction facts.'])
    reserve_directory(out)
    write_json_new(out/'window_results.json', report)
    fmt = lambda value: '—' if value is None else f'{value:.3f}'
    lines = ['# 新四场景实际结果', '', f'固定4个不同scene；扣账{charges}/8，观察到{turns}个turn/start。新增模型调用0（本脚本仅统计）。', '',
             '|窗口|结果|合法原生/扣账|失败阶段/原因|T2/T3/T4候选|前缀/位移m/末速m/s|条件间距m|',
             '|---|---|---|---|---|---|---|']
    for row in rows:
        p = row['physical']; c = row['candidate_counts']
        counts = '/'.join(str(c[k]) for k in ('T2', 'T3', 'T4')) if c else 'NOT_REACHED'
        reasons = ','.join(sorted({str(x.get('code')) for x in row['final_failure_reasons']}))
        failure = (row['final_failure_stage'] or '—') + (' '+reasons if reasons else '')
        if row['fatal_service_or_interface_failure']: failure += '; SERVICE/INTERFACE'
        lines.append(f"|{row['case_id']} / {row['scene_name']}|{row['status']}|{row['native_schema_valid_count']}/{row['charged_requests']}|{failure}|{counts}|{p.get('motion_class','NOT_EXECUTED')}/{fmt(p.get('displacement_m'))}/{fmt(p.get('end_speed_mps'))}|{fmt(p.get('minimum_conditional_clearance_lower_bound_m'))}|")
    lines += ['', '![实际状态与失败类](failure_distribution.png)', '',
              '修订不增加唯一窗口；未调用不算失败。全部候选、服务通知、原生Schema、T1–T4、独立接受器、前缀回执/积分及平顺性代理见JSON。',
              '行为合理性待独立图像/决策审阅，GT未读取。静止前缀不自动代表移动任务完成。',
              f"预算核对：{report['budget_audit_status']}；若扣账多于可见turn/start，应保留服务前失败/缺失证据差异，不补造调用。", '']
    write_text_new(out/'window_results.md', '\n'.join(lines)); figure(report, out/'failure_distribution.png')
    print(json.dumps(dict(out=str(out), charged_requests=charges, turn_start_count=turns, outcomes=report['outcome_distribution']), ensure_ascii=False))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('--root', required=True, type=Path)
    p.add_argument('--out', type=Path); args = p.parse_args()
    main(args.root, args.out or args.root/'summary')
