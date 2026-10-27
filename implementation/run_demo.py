"""Run synthetic crossing, injected failure and scripted-agent revision."""
from copy import deepcopy
import json
from pathlib import Path
from driving_tools import ROOT, braking_trajectory, call_tool, make_request, pipeline


def write(name, content):
    (ROOT/'results'/name).write_text(json.dumps(content,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')


def main():
    initial=json.loads((ROOT/'examples/pedestrian_crossing.request.json').read_text(encoding='utf-8'))
    normal=pipeline(initial,allow_synthetic=True)
    write('normal.trace.json',normal['trace'])
    write('shared_scene.example.json',normal['scene'])

    # Deliberate downstream fault injection: replace T3 output with coasting.
    # This is NOT the trajectory T3 actually returned.
    unsafe=braking_trajectory(normal['scene'],0.)
    fault_request=make_request('T4',{'scene':normal['scene'],'decision':normal['decision'],
                     'corridor':normal['corridor'],'trajectory':unsafe},'fault-injection')
    fault_response=call_tool(fault_request,allow_synthetic=True)
    assert fault_response['status']=='ok'
    rejected=fault_response['payload']['verification']
    assert rejected['verdict']=='FAIL' and rejected['released_prefix_s']==0
    write('fault_injection.trace.json',{'origin':'deliberate_test_fault_after_T3',
          'request':fault_request,'response':fault_response})

    # The test double represents the Agent's NEW decision. Tools do not edit it.
    revised=deepcopy(initial)
    revised['request_id']='agent-revision-t1'
    revised['payload']['metadata']['revision']=1
    revised['payload']['decision']['revision']=1
    revised['payload']['decision']['stop_before_x_m']=10.
    repaired=pipeline(revised,allow_synthetic=True)
    write('agent_revision.request.json',revised)
    write('agent_revision.trace.json',repaired['trace'])
    write('agent_revision.provenance.json',{
        'producer':'scripted_agent_test_double',
        'uses_live_foundation_model':False,
        'feedback_source':'fault_injection.trace.json',
        'change':'停车参考点上界由 12 米调整为 10 米；保留停车让行行为。',
        'same_observation_replanning':True})
    resume_request=json.loads((ROOT/'examples/pedestrian_clear_resume.request.json').read_text(encoding='utf-8'))
    resumed=pipeline(resume_request,allow_synthetic=True)
    write('pedestrian_clear_resume.trace.json',resumed['trace'])
    summary={
        'scenario':'synthetic pedestrian crossing; straight-road profile',
        'normal':{'verdict':normal['verification']['verdict'],
                  'stop_x_m':normal['trajectory']['points'][-1]['x_m'],
                  'released_prefix_s':normal['verification']['released_prefix_s'],
                  'minimum_clearance_m':normal['verification']['minimum_clearance_m']},
        'injected_fault':{'verdict':rejected['verdict'],
                          'codes':sorted({v['code'] for v in rejected['violations']}),
                          'released_prefix_s':rejected['released_prefix_s']},
        'scripted_agent_revision':{'verdict':repaired['verification']['verdict'],
                                   'stop_x_m':repaired['trajectory']['points'][-1]['x_m'],
                                   'revision':1},
        'next_observation_resume':{'verdict':resumed['verification']['verdict'],
                                   'reference_time_s':104.,'revision':2,
                                   'forward_distance_m':resumed['trajectory']['points'][-1]['x_m'],
                                   'final_speed_mps':resumed['trajectory']['points'][-1]['speed_mps'],
                                   'released_prefix_s':resumed['verification']['released_prefix_s'],
                                   'source':'scripted fresh observation after normal stopping branch'},
        'limitations':['No live model call, images or vehicle execution.',
                       'No G2 metric-grounding validation.',
                       'New observation and Agent decisions are synthetic test doubles, not inferred from images.',
                       'No general nonlinear solver, curved-road support or safety certification.']}
    assert summary['normal']['verdict']=='PASS'
    assert summary['scripted_agent_revision']['verdict']=='PASS'
    assert summary['next_observation_resume']['verdict']=='PASS'
    write('summary.json',summary)
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__=='__main__': main()
