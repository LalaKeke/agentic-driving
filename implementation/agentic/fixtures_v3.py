"""Authored SOFTWARE fixtures for v3-exp2. No images, model calls, or GT.

The actor observations, motion premises and future envelopes below are all
manually authored test assumptions. They never purport to be nuScenes outputs.
"""
from copy import deepcopy

from .contracts import digest
from .fixtures import ScriptedAgent, capture, rect
from .metric_contract import VERSION, COVERAGE_PROFILE, MEASUREMENT_SEMANTICS


def metric_case(asynchronous=True, speed=0.):
    """Return capture, authored base-model substitute, and honest provenance.

    The actor's BODY center at reference t=100 is (18,-3); length .8m,
    width1m, yaw0. The authored walking branch is v=(0,2.5)m/s and its
    single source polygon encloses its BODY throughout [0,5], not endpoints.
    Alignment residual.2m plus source residual.2m gives one total.4m radius,
    applied once by existing geometry/tools.
    """
    cap = capture(speed=speed)
    output, _ = ScriptedAgent().decide(cap)
    output.update(contract_version=VERSION, coverage_profile=COVERAGE_PROFILE,
                  measurement_semantics=deepcopy(MEASUREMENT_SEMANTICS))
    h = output['hypotheses']
    h['confidence_note'] = 'AUTHORED SOFTWARE FIXTURE ONLY; bounds are conditional, not calibrated.'
    h['unknowns'] = []
    h['premises'] = [dict(id='walk_premise', object_ids=['pedestrian'],
                         scenario_ids=['walk'], start_s=-.2 if asynchronous else 0., end_s=5.,
                         space_polygon_m=rect(17,19,-4,11),
                         statement='AUTHORED: center walks along +Y at 2.5m/s; no additional modes in this synthetic world.')]
    actor = h['objects'][0]
    actor['uncertainty_m'] = .4
    if asynchronous:
        frames = []
        for stamp in (99.8, 99.9):
            frames.append(dict(id=f'authored_rear_{stamp:.3f}', time_id=f't{stamp:.3f}',
                               capture_time_s=stamp, capture_utc=None, camera_id='rear',
                               path='fixture://authored-no-image'))
        cap['observations'].extend(frames)
    else:
        frames = [cap['observations'][0]]
    actor['frame_ids'] = [f['id'] for f in frames]
    actor['time_ids'] = [f['time_id'] for f in frames]
    actor['state_time_id'] = cap['metadata']['reference_time_id']
    actor['body_geometry'] = dict(length_m=.8, width_m=1., yaw_rad=0.)
    h['premises'].append(dict(
        id='scale_size_prior', object_ids=['pedestrian'], scenario_ids=['walk'],
        start_s=-.2 if asynchronous else 0., end_s=5.,
        space_polygon_m=rect(17,19,-4,11),
        statement='AUTHORED conditional body-length prior .8m. This fixture is manually specified; the value is not a real-image measurement or certified absolute scale.'))
    h['metric_scale_support'] = dict(
        method='conditional_body_dimension_prior',
        frame_ids=list(actor['frame_ids']), time_ids=list(actor['time_ids']),
        premise_ids=['scale_size_prior'],
        anchors=[dict(object_id='pedestrian', dimension='length_m', value_m=.8,
                      rationale='AUTHORED .8m body length is an explicit conditional test assumption, never a measured or calibrated scale guarantee.')],
        metric_reasoning='AUTHORED synthetic scene dimensions are manually specified under the .8m body-length prior; paired actor capture records are the only cited evidence. No actual image or monocular absolute-scale recovery is claimed.')
    actor['alignment'] = dict(
        method='model_motion_bound' if asynchronous else 'direct_reference_observation',
        premise_ids=['walk_premise'], residual_m=.2,
        motion_bound=dict(max_speed_mps=3., displacement_bound_m=.6,
                          covers_all_declared_scenarios=True) if asynchronous else None,
        evidence_positions=[dict(frame_id=f['id'], time_id=f['time_id'],
                                 position_m=[18., -3.+2.5*(f['capture_time_s']-100.)],
                                 observed_actor=True) for f in frames])
    # Walking across the lane is the explicitly retained adverse mode in this
    # authored case; it is not removed merely because the ego plans to wait.
    actor['scenarios'] = [dict(id='walk', premise_ids=['walk_premise'], role='reasonable_adverse')]
    actor['scenario_inventory_complete'] = True
    actor['occupancy'] = [dict(id='walk_source_0_5', start_s=0., end_s=5.,
                               kind='whole_interval_body_envelope', status='supported',
                               frame_ids=list(actor['frame_ids']), time_ids=list(actor['time_ids']),
                               premise_ids=['walk_premise'], residual_m=.2,
                               alternatives=[dict(scenario_id='walk', polygon_m=rect(17.6,18.4,-3.5,10.))])]
    provenance = dict(source='synthetic_fixture', model_id='authored-not-a-model-call',
                      prompt_version='fixture_v3_exp2', agent_output_contract=VERSION,
                      output_digest=digest(output), tool_calls=0)
    return cap, output, provenance


def add_adverse_scenario(output, invade=False):
    """Keep a declared adverse mode; optionally author future ego intrusion.

    These are counterexample assumptions rather than any tool extrapolation.
    Full-horizon envelope may be wide; that is permitted to remain conservative.
    """
    actor = output['hypotheses']['objects'][0]
    actor['scenarios'].append(dict(id='adverse', premise_ids=['adverse_premise'],
                                   role='reasonable_adverse'))
    actor['alignment']['premise_ids'].append('adverse_premise')
    for premise in output['hypotheses']['premises']:
        if premise['id']=='scale_size_prior':
            # Body length remains the same in both authored motion branches;
            # this is the stated prior itself, not a new inferred scale.
            premise['scenario_ids'].append('adverse')
    output['hypotheses']['premises'].append(dict(
        id='adverse_premise', object_ids=[actor['id']], scenario_ids=['adverse'],
        start_s=-.2, end_s=5., space_polygon_m=rect(-3,20,-5,11),
        statement='AUTHORED adverse branch; included in source BODY envelope, never dropped to obtain planning.'))
    actor['occupancy'][0]['premise_ids'].append('adverse_premise')
    actor['occupancy'][0]['alternatives'].append(dict(
        scenario_id='adverse', polygon_m=rect(-2,19,-4,10) if invade else rect(17.6,19.,-3.5,10.)))
    return output
