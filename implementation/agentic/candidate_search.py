"""Five deterministic ego references, with independent verification of each solution."""
from . import t2,t3,t4
from .contracts import Failure


def plan(scene,save=lambda candidate,name,value:None):
    rows=[];accepted=[];failures=[]
    for candidate in t2.CANDIDATE_IDS:
        row=dict(candidate_id=candidate,T2='NOT_REACHED',T3='NOT_REACHED',T4='NOT_REACHED')
        data={}
        for name,fn in (
            ('T2',lambda:t2.build(scene,policy=t2.YAW_BOX_PROFILE,
                                  candidate_id=None if candidate=='original' else candidate)),
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
    ledger=dict(search_profile=t2.SEARCH_PROFILE,candidate_count=len(rows),candidates=rows,
                selected_candidate=winner[1] if winner else None,
                limitation='Finite ego-reference search; exhaustion is not proof of global infeasibility. All target occupancies, adverse scenarios, residuals and physical limits are unchanged.')
    return (winner[2] if winner else None),ledger,(None if winner else failures[-1])
