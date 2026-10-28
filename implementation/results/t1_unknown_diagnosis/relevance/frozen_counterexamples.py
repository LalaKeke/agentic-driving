"""Independent synthetic audit; NO model calls, NO core/evidence mutations."""
from copy import deepcopy
from pathlib import Path
import hashlib,json,sys,unittest
ROOT=Path('/data2/hzy/MAP/agentic-driving')
sys.path.insert(0,str(ROOT/'implementation'))
from agentic.fixtures import capture,ScriptedAgent,rect
from agentic.contracts import digest,Failure,validate
from agentic.t1 import construct
from agentic.geometry import obstacles
from agentic.t2 import build as build_corridor
from shapely.geometry import Point,Polygon

FIXTURES={}; RESULTS=[]
def base():
    c=capture(speed=0)
    o,p=ScriptedAgent().decide(c)
    p['agent_output_contract']='2.1.0'
    return c,o,p

def run(name,c,o,p):
    p=deepcopy(p);p['output_digest']=digest(o)
    FIXTURES[name]=dict(capture=c,output=o,provenance=p,source='synthetic_fixture',online_success=False)
    try:
        s=construct(c,o,p,True)
        r=dict(case=name,accepted=True,object_count=len(s['hypotheses']['objects']),scene_digest=digest(s))
    except Failure as e:r=dict(case=name,accepted=False,**e.record)
    RESULTS.append(r)
    return r

class IndependentCounterexamples(unittest.TestCase):
    def test_complete_supported_bounded_occupancy_passes(self):
        c,o,p=base();self.assertTrue(run('complete_supported_bounded_occupancy',c,o,p)['accepted'])
    def test_unknown_velocity_with_full_occupancy_is_not_a_blocker(self):
        c,o,p=base();o['hypotheses']['objects'][0]['velocity_mps']=None
        self.assertTrue(run('unknown_velocity_full_occupancy',c,o,p)['accepted'])
    def test_multiple_adverse_alternatives_are_retained(self):
        c,o,p=base()
        for z in o['hypotheses']['objects'][0]['occupancy']:z['alternatives_m'].append(rect(15,19,-1,2))
        r=run('multiple_adverse_alternatives',c,o,p);self.assertTrue(r['accepted'])
        p['output_digest']=digest(o);s=construct(c,o,p,True)
        self.assertEqual(s['hypotheses'],o['hypotheses']);self.assertEqual(len(list(obstacles(s,0))),2)
    def test_adverse_future_branch_is_not_ignored(self):
        c,o,p=base()
        for z in o['hypotheses']['objects'][0]['occupancy']:
            if z['start_s']>=1.:z['alternatives_m'].append(rect(-2,2,-2,2))
        r=run('adverse_future_branch',c,o,p);self.assertTrue(r['accepted'])
        p['output_digest']=digest(o);scene=construct(c,o,p,True)
        with self.assertRaises(Failure) as e:build_corridor(scene)
        self.assertEqual(e.exception.record['code'],'CORRIDOR_ANCHOR_BLOCKED')
        RESULTS.append(dict(case='adverse_future_branch_T2',accepted=False,**e.exception.record))
    def test_distance_alone_does_not_exclude_future_intrusion(self):
        # Authored counter-world, not a claim about any real actor or a tool forecast.
        self.assertGreater(60,45)  # Outside a finite current plan patch.
        self.assertAlmostEqual(60-15*4,0)  # A plausible numerical mode enters in H=5.
        RESULTS.append(dict(case='distance_counter_world',source='synthetic_fixture',start_m=[60,0],authored_velocity_mps=[-15,0],time_s=4,end_m=[0,0],meaning='Distance alone cannot prove irrelevance; no threshold adopted.'))
    def test_occluded_source_polygon_is_not_future_body_bound(self):
        source=Polygon(rect(20,22,-1,1));origin=Point(0,0)
        self.assertFalse(source.covers(origin));self.assertTrue(Polygon(rect(-1,1,-1,1)).covers(origin))
        RESULTS.append(dict(case='source_region_counter_world',source='synthetic_fixture',source_polygon_m=rect(20,22,-1,1),possible_future_body_at4s_m=rect(-1,1,-1,1),meaning='Source region separated now is insufficient unless model also bounds future intrusion.'))
    def test_null_occupancy_is_not_empty(self):
        c,o,p=base();o['hypotheses']['objects'][0]['occupancy']=None
        self.assertEqual(run('null_occupancy',c,o,p)['code'],'OCCUPANCY_UNKNOWN')
    def test_unbounded_unknown_has_no_object_or_time_link(self):
        c,o,p=base();o['hypotheses']['unbounded_unknown']=True
        r=run('global_unbounded_unknown',c,o,p)
        self.assertEqual(r['code'],'UNBOUNDED_UNKNOWN');self.assertEqual(r['details'],{})
    def test_remote_null_occupancy_still_rejects_without_distance_rule(self):
        c,o,p=base();a=o['hypotheses']['objects'][0];a['position_m']=[1000,1000];a['occupancy']=None
        self.assertEqual(run('remote_null_no_arbitrary_distance_rule',c,o,p)['code'],'OCCUPANCY_UNKNOWN')
    def test_asynchronous_actor_already_model_aligned_rejects(self):
        c,o,p=base()
        # Authored hypothetical model observed actor at y=-3.25 at t=99.9.
        # Model's aligned state at trusted reference100 is y=-3, v_y=2.5;
        # source motion is model-authored, not ego-only compensation or T1 forecast.
        for tt in [99.8,99.9]:
            c['observations'].append(dict(id=f'async_rear_{tt}',time_id=f't{tt:.3f}',capture_time_s=tt,capture_utc=None,camera_id='rear',path='fixture://synthetic-side-history'))
        a=o['hypotheses']['objects'][0];a['frame_ids']=['async_rear_99.8','async_rear_99.9'];a['time_ids']=['t99.800','t99.900']
        o['hypotheses']['confidence_note']='SYNTHETIC: model-authored constant lateral motion2.5m/s from99.8/99.9 source evidence; state and body envelopes are aligned to trusted t100.000;0.2m residual bounds adopted phase and alignment uncertainty. No actor appears in front frame. Conditional motion premise, no calibrated guarantee.'
        r=run('async_model_aligned_with_truthful_evidence',c,o,p)
        self.assertEqual(r['code'],'DYNAMIC_ALIGNMENT')
    def test_wrong_evidence_pair_is_independently_rejected(self):
        c,o,p=base();c['observations'].append(dict(id='oldrear',time_id='t99.900',capture_time_s=99.9,capture_utc=None,camera_id='rear',path='fixture://synthetic'))
        a=o['hypotheses']['objects'][0];a['frame_ids']=['oldrear'];a['time_ids']=['t100.000']
        self.assertEqual(run('wrong_evidence_pair',c,o,p)['code'],'MISSING_EVIDENCE')
    def test_wrong_state_reference_is_not_accepted(self):
        c,o,p=base();o['hypotheses']['reference_time_id']='t99.900'
        self.assertEqual(run('wrong_state_reference',c,o,p)['code'],'DYNAMIC_ALIGNMENT')
    def test_supported_coarse_envelope_rejected_as_grid_representation(self):
        c,o,p=base();a=o['hypotheses']['objects'][0]
        coarse=dict(start_s=0.,end_s=5.,kind='whole_interval_envelope',alternatives_m=[rect(17.6,18.4,-3.5,10.)])
        a['occupancy']=[coarse];o['hypotheses']['confidence_note']='Synthetic genuinely adopted whole-horizon body union, not unsupported placeholder.'
        validate('agent_output_v2_1.schema.json',o)
        self.assertEqual(run('coarse_whole_horizon_envelope',c,o,p)['code'],'OCCUPANCY_GAP')
        # Independent lossless comparison, NOT a runtime repair proposal.
        expanded=[dict(start_s=k*.2,end_s=(k+1)*.2,kind=coarse['kind'],alternatives_m=deepcopy(coarse['alternatives_m'])) for k in range(25)]
        a['occupancy']=expanded
        self.assertTrue(all(x['alternatives_m']==coarse['alternatives_m'] for x in expanded))
        self.assertTrue(run('equivalent_supported_repeated_envelope',c,o,p)['accepted'])
    def test_missing_interval_still_rejects(self):
        c,o,p=base();o['hypotheses']['objects'][0]['occupancy'].pop(11)
        self.assertEqual(run('missing_interval_not_null',c,o,p)['code'],'OCCUPANCY_GAP')
    def test_schema_valid_unknown_scale_classification(self):
        c,o,p=base();o['hypotheses']['scale_basis']='unknown'
        validate('agent_output_v2_1.schema.json',o)
        r=run('unknown_scale_schema_valid',c,o,p)
        self.assertEqual(r['code'],'MISSING_SCALE');self.assertEqual(r['category'],'interface_error')

if __name__=='__main__':
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(IndependentCounterexamples)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    payload=dict(source='synthetic_fixture',online_success_claims=0,core_mutations=0,cases=FIXTURES,results=RESULTS,test_count=result.testsRun,failures=len(result.failures),errors=len(result.errors),source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),t1_sha256=hashlib.sha256((ROOT/'implementation/agentic/t1.py').read_bytes()).hexdigest())
    Path('/tmp/t1_relevance_counterexamples.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2))
    sys.exit(not result.wasSuccessful())
