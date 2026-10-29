"""Five deterministic ego references, with independent verification of each solution."""
from copy import deepcopy
from . import t2,t3,t4
from .contracts import Failure


def plan(scene,save=lambda candidate,name,value:None,profile=t2.SEARCH_PROFILE):
    rows=[];accepted=[];failures=[]
    for candidate in t2.CANDIDATE_IDS:
        row=dict(candidate_id=candidate,T2='NOT_REACHED',T3='NOT_REACHED',T4='NOT_REACHED')
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
            accepted.append((row['objective'],candidate,data))
        rows.append(row)
    winner=min(accepted,key=lambda x:x[0]) if accepted else None
    ledger=dict(search_profile=profile,candidate_count=len(rows),candidates=rows,
                selected_candidate=winner[1] if winner else None,
                limitation='Finite ego-reference search; exhaustion is not proof of global infeasibility. All target occupancies, adverse scenarios, residuals and physical limits are unchanged.')
    failure=None if winner else failures[-1]
    if not winner and profile==t2.SEARCH_PROFILE_V2:
        # Feedback must describe the deepest actually attempted stage. A final
        # seed's heading failure must not hide another candidate's T3 failure.
        failed=max(rows,key=lambda row:max(i for i,name in enumerate(('T2','T3','T4')) if row[name]=='FAIL'))
        failure=deepcopy(failed['failure'])
        failure.setdefault('details',{}).update(candidate_id=failed['candidate_id'],
            candidate_failures=[dict(candidate_id=r['candidate_id'],
                stage=next(n for n in ('T2','T3','T4') if r[n]=='FAIL'),
                code=r['failure']['code']) for r in rows])
    return (winner[2] if winner else None),ledger,failure
