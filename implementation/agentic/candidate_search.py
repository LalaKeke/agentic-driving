"""Finite deterministic ego references, with independent verification of each solution."""
from copy import deepcopy
from . import t2,t3,t4
from .contracts import Failure


def plan(scene,save=lambda candidate,name,value:None,profile=t2.SEARCH_PROFILE,task_filter=None):
    rows=[];accepted=[];failures=[]
    for candidate in (t2.CANDIDATE_IDS_V4 if profile==t2.SEARCH_PROFILE_V4 else t2.CANDIDATE_IDS):
        row=dict(candidate_id=candidate,T2='NOT_REACHED',T3='NOT_REACHED',T4='NOT_REACHED')
        if task_filter is not None:row['TURN_TASK']='NOT_REACHED'
        data={}
        for name,fn in (
            ('T2',lambda:t2.build(scene,policy=t2.YAW_BOX_PROFILE,
                                  candidate_id=None if candidate=='original' and profile==t2.SEARCH_PROFILE else candidate,
                                  search_profile=profile)),
            ('T3',lambda:t3.solve(scene,data['T2'])),
            ('T4',lambda:t4.verify(scene,data['T3']))):
            try:
                data[name]=fn();save(candidate,name,data[name]);row[name]='PASS'
                if name=='T4' and data[name]['verdict']!='PASS':
                    row[name]='FAIL';row['failure']=dict(code='VERIFICATION_REJECTED',violations=data[name]['violations'])
                    failures.append(row['failure']);break
            except Failure as exc:
                row[name]='FAIL';row['failure']=exc.record
                save(candidate,name+'-failure',exc.record);failures.append(exc.record);break
        if row['T4']=='PASS':
            row['objective']=data['T3']['solver']['objective']
            row['minimum_clearance_lower_bound_m']=data['T4']['minimum_clearance_lower_bound_m']
            if task_filter is not None:
                report=task_filter(data['T3'])
                data['TURN_TASK']=report;save(candidate,'TURN_TASK',report)
                row['TURN_TASK']='PASS' if report['verdict']=='PASS' else 'FAIL'
                if row['TURN_TASK']=='FAIL':
                    row['task_failure']=dict(code='TURN_TASK_NOT_DEMONSTRATED',category='task_not_completed',
                        details=dict(candidate_id=candidate,turn_task=deepcopy(report)),
                        repair_request='A physically verified candidate does not demonstrate the registered turning task. Preserve image-supported road, target and all safety constraints; do not induce an arbitrary swerve on a straight road.')
                    failures.append(row['task_failure'])
            if task_filter is None or row['TURN_TASK']=='PASS':
                accepted.append((row['objective'],candidate,data))
        rows.append(row)
    winner=min(accepted,key=lambda x:x[0]) if accepted else None
    ledger=dict(search_profile=profile,candidate_count=len(rows),candidates=rows,
                selected_candidate=winner[1] if winner else None,
                limitation='Finite ego-reference search; exhaustion is not proof of global infeasibility. All target occupancies, adverse scenarios, residuals and physical limits are unchanged.')
    if task_filter is not None:
        ledger.update(task_filter_active=True,task_accepted_candidate_count=sum(r['TURN_TASK']=='PASS' for r in rows))
    failure=None if winner else failures[-1]
    task_failed=[r for r in rows if r.get('TURN_TASK')=='FAIL']
    if not winner and task_failed:
        # Physical verification passed. Keep that fact separate from task
        # completion, and never feed an all-PASS row to physical-failure max().
        failed=min(task_failed,key=lambda row:row['objective'])
        failure=deepcopy(failed['task_failure'])
    elif not winner and profile in (t2.SEARCH_PROFILE_V2,t2.SEARCH_PROFILE_V3,t2.SEARCH_PROFILE_V4):
        # Feedback must describe the deepest actually attempted stage. A final
        # seed's heading failure must not hide another candidate's T3 failure.
        failed=max(rows,key=lambda row:max(i for i,name in enumerate(('T2','T3','T4')) if row[name]=='FAIL'))
        failure=deepcopy(failed['failure'])
        failure.setdefault('details',{}).update(candidate_id=failed['candidate_id'],
            candidate_failures=[dict(candidate_id=r['candidate_id'],
                stage=next(n for n in ('T2','T3','T4') if r[n]=='FAIL'),
                code=r['failure']['code']) for r in rows])
    return (winner[2] if winner else None),ledger,failure
