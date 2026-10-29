"""Two focused authored regressions for explicit prototype-v3; no service calls."""
from copy import deepcopy
import hashlib
import inspect
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'implementation'),str(Path(__file__).resolve().parent)]
from agentic import t1
from agentic.contracts import Failure,digest
from agentic.fixtures import rect
from agentic.fixtures_v3 import metric_case
from agentic.pilot_transport import PilotSubscriptionAgent,PROMPT
from test_seed_candidates import authored_case


class PrototypePromptV3Tests(unittest.TestCase):
    def test_explicit_v3_payload_preserves_native_output_and_old_default(self):
        def no_process(*args,**kwargs):
            raise AssertionError('No RPC process or authentication may be accessed')
        prompt=(ROOT/'implementation/prompts/driving_prototype_v3.txt').read_text()
        v2=(ROOT/'implementation/prompts/driving_prototype_v2.txt').read_text()
        self.assertTrue(prompt.startswith(v2.replace('Prompt version driving_prototype_v2;',
                                                      'Prompt version driving_prototype_v3;')))
        self.assertIn('round its boundaries outward',prompt)
        self.assertIn('negative time to zero',prompt)
        with tempfile.TemporaryDirectory(prefix='prototype-v3-authored-') as directory:
            root=Path(directory)
            self.assertEqual(inspect.signature(PilotSubscriptionAgent).parameters['prompt_version'].default,PROMPT)
            legacy=PilotSubscriptionAgent(evidence_dir=root/'legacy',binary=sys.executable,process_factory=no_process)
            self.assertEqual(legacy.prompt_version,'driving_v3_exp2')
            cap,out,_=authored_case(speed=3.,half_road=10.)
            image=root/'authored.png';Image.new('RGB',(3,3),'white').save(image)
            for observation in cap['observations']:observation['path']=str(image)
            before=deepcopy(out);raw=' \n'+json.dumps(out,ensure_ascii=False)+'\n'
            agent=PilotSubscriptionAgent(evidence_dir=root/'v3',binary=sys.executable,process_factory=no_process,
                prompt_version='driving_prototype_v3',max_calls=4,experiment_profile='local_conditional_2s_prototype_v1')
            agent.preflight_ok=True;agent.version='mock_transport_no_service'
            protocol=SimpleNamespace(final_text=lambda:raw,resolved_model='mock_transport_no_service')
            with patch.object(agent,'_rpc',return_value=protocol) as rpc:
                returned,provenance=agent.decide(cap)
            self.assertEqual(rpc.call_count,1)
            text=rpc.call_args.args[1][0]['text']
            self.assertTrue(text.startswith(prompt+'\n'))
            payload=json.loads(text[len(prompt)+1:])
            self.assertEqual(payload['tool_contract']['planner_profile'],'bounded_seed_candidates_v2')
            self.assertEqual(returned,before);self.assertEqual(out,before)
            self.assertEqual(provenance['source'],'mock_transport')
            self.assertEqual(provenance['prompt_version'],'driving_prototype_v3')
            self.assertEqual(provenance['prompt_sha256'],hashlib.sha256(prompt.encode()).hexdigest())
            self.assertEqual((agent.evidence_dir/'attempt_01/final_agent_message.txt').read_text(),raw)
            self.assertEqual(agent.call_count,1)

    def test_v3_does_not_accept_undersized_body_null_or_future_only_alignment_premise(self):
        for case,expected in (('valid',None),('body','BODY_ENVELOPE_CONFLICT'),
                              ('null','OCCUPANCY_UNKNOWN'),('past_scope','PREMISE_SCOPE')):
            with self.subTest(case=case):
                if case=='past_scope':
                    cap,out,prov=metric_case(asynchronous=True,speed=3.)
                    cap['metadata'].update(horizon_s=2.,valid_until_s=102.)
                    out['decision']=deepcopy(authored_case(speed=3.,half_road=10.)[1]['decision'])
                    for premise in out['hypotheses']['premises']:premise['end_s']=2.
                    out['hypotheses']['premises'].append(dict(id='prototype_local_scope',object_ids=[],scenario_ids=[],
                        start_s=0.,end_s=2.,space_polygon_m=rect(-100,100,-100,100),statement='AUTHORED finite test scope.'))
                    out['hypotheses']['objects'][0]['occupancy'][0]['end_s']=2.
                    out['hypotheses']['premises'][0]['start_s']=0.
                else:
                    cap,out,prov=authored_case(speed=3.,half_road=10.)
                    actor=out['hypotheses']['objects'][0]
                    if case=='body':actor['occupancy'][0]['alternatives'][0]['polygon_m']=rect(17.6,18.4-1e-9,-3.5,10.)
                    elif case=='null':actor['occupancy']=None
                prov.update(prompt_version='driving_prototype_v3',experiment_profile='local_conditional_2s_prototype_v1',output_digest=digest(out))
                before=deepcopy((cap,out,prov))
                if expected:
                    with self.assertRaises(Failure) as caught:t1.construct(cap,out,prov,True)
                    self.assertEqual(caught.exception.record['code'],expected)
                else:
                    self.assertEqual(t1.construct(cap,out,prov,True)['provenance']['prompt_version'],'driving_prototype_v3')
                self.assertEqual((cap,out,prov),before)


if __name__=='__main__':unittest.main()
