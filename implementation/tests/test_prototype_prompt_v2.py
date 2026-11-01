"""Prompt-only correction tests; all RPC/native values are authored mocks."""
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
sys.path.insert(0,str(ROOT/'implementation'))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from agentic import t1
from agentic.contracts import Failure,digest
from agentic.pilot_transport import PilotSubscriptionAgent,PROMPT
from test_seed_candidates import authored_case


def sha(value):
    return hashlib.sha256(value).hexdigest()


class PrototypePromptV2Tests(unittest.TestCase):
    def test_old_prompt_bytes_and_business_requirements_are_preserved(self):
        old=(ROOT/'implementation/prompts/driving_prototype_v1.txt').read_bytes()
        new=(ROOT/'implementation/prompts/driving_prototype_v2.txt').read_bytes()
        self.assertEqual(sha(old),'a2cead8da3cc140aea5cc46d2780b143a45dad57d7a7a4cc95de0536082e45c2')
        a,b=old.decode().splitlines(),new.decode().splitlines()
        self.assertEqual(len(a),len(b))
        self.assertEqual([i for i,(x,y) in enumerate(zip(a,b)) if x!=y],[0,11])
        self.assertEqual(a[0].replace('Prompt version driving_prototype_v1;',
                                     'Prompt version driving_prototype_v2;'),b[0])
        self.assertIn('bounded_seed_candidates_v2',b[11])
        self.assertIn('full oriented ego BODY',b[11])
        self.assertIn('independently checks continuous full-body',b[11])
        # Unknown/null, native geometry and whole-interval coverage requirements
        # are in the unchanged lines, not weakened by the new tool description.
        self.assertIn('Null velocity is permitted when occupancy is supported.',new.decode())

    def test_v2_requires_explicit_profile_horizon_scope_and_complete_native_coverage(self):
        cap,out,prov=authored_case(speed=3.,half_road=10.)
        prov['prompt_version']='driving_prototype_v2'
        scene=t1.construct(cap,out,prov,True)
        self.assertEqual(scene['provenance']['prompt_version'],'driving_prototype_v2')
        for change,code in (('profile','PROMPT_CONTRACT'),('horizon','PROMPT_CONTRACT'),
                            ('scope','PROTOTYPE_SCOPE'),('occupancy','OCCUPANCY_UNKNOWN')):
            with self.subTest(change=change):
                c,o,p=deepcopy((cap,out,prov))
                if change=='profile':p.pop('experiment_profile')
                elif change=='horizon':c['metadata']['horizon_s']=5.
                elif change=='scope':
                    next(x for x in o['hypotheses']['premises'] if x['id']=='prototype_local_scope')['space_polygon_m']=None
                else:o['hypotheses']['objects'][0]['occupancy']=None
                p['output_digest']=digest(o)
                with self.assertRaises(Failure) as failure:t1.construct(c,o,p,True)
                self.assertEqual(failure.exception.record['code'],code)

    def test_explicit_v2_mock_transport_sends_actual_prompt_without_changing_native_output(self):
        def no_process(*args,**kwargs):
            raise AssertionError('This test must never launch an RPC process')
        with tempfile.TemporaryDirectory(prefix='prototype-prompt-v2-test-') as directory:
            root=Path(directory)
            self.assertEqual(inspect.signature(PilotSubscriptionAgent).parameters['prompt_version'].default,PROMPT)
            legacy=PilotSubscriptionAgent(evidence_dir=root/'legacy',binary=sys.executable,process_factory=no_process)
            self.assertEqual(legacy.prompt_version,'driving_v3_exp2')
            self.assertEqual(legacy.max_calls,2)
            v1=PilotSubscriptionAgent(evidence_dir=root/'v1',binary=sys.executable,process_factory=no_process,
                prompt_version='driving_prototype_v1',max_calls=4,experiment_profile='local_conditional_2s_prototype_v1')
            self.assertEqual(v1.prompt_version,'driving_prototype_v1')
            for profile,limit in ((None,4),('local_conditional_2s_prototype_v1',2)):
                with self.assertRaises(Failure) as failure:
                    PilotSubscriptionAgent(evidence_dir=root/f'prohibited-{limit}',binary=sys.executable,
                        process_factory=no_process,prompt_version='driving_prototype_v2',max_calls=limit,
                        experiment_profile=profile)
                self.assertEqual(failure.exception.record['code'],'PILOT_CONFIGURATION')
                self.assertFalse((root/f'prohibited-{limit}').exists())
            cap,out,_=authored_case(speed=3.,half_road=10.)
            image=root/'authored.png';Image.new('RGB',(3,3),'white').save(image)
            for frame in cap['observations']:frame['path']=str(image)
            before=deepcopy(out)
            raw=' \n'+json.dumps(out,ensure_ascii=False,indent=1)+'\n'
            agent=PilotSubscriptionAgent(evidence_dir=root/'v2',binary=sys.executable,process_factory=no_process,
                prompt_version='driving_prototype_v2',max_calls=4,experiment_profile='local_conditional_2s_prototype_v1')
            agent.preflight_ok=True;agent.version='mock_transport_no_service'
            protocol=SimpleNamespace(final_text=lambda:raw,resolved_model='mock_transport_no_service')
            with patch.object(agent,'_rpc',return_value=protocol) as rpc:
                returned,prov=agent.decide(cap)
            self.assertEqual(rpc.call_count,1)
            prompt=(ROOT/'implementation/prompts/driving_prototype_v2.txt').read_text()
            model_input=rpc.call_args.args[1][0]['text']
            self.assertTrue(model_input.startswith(prompt+'\n'))
            payload=json.loads(model_input[len(prompt)+1:])
            self.assertEqual(payload['tool_contract']['planner_profile'],'bounded_seed_candidates_v2')
            self.assertEqual(payload['tool_contract']['experiment_profile'],'local_conditional_2s_prototype_v1')
            self.assertEqual(payload['capture']['metadata']['horizon_s'],2.)
            self.assertEqual(returned,before)
            self.assertEqual(out,before)
            self.assertEqual(prov['source'],'mock_transport')
            self.assertEqual(prov['prompt_version'],'driving_prototype_v2')
            self.assertEqual(prov['prompt_sha256'],sha(prompt.encode()))
            folder=agent.evidence_dir/'attempt_01'
            self.assertEqual((folder/'final_agent_message.txt').read_text(),raw)
            self.assertEqual(json.loads((folder/'native_output.json').read_text()),before)
            manifest=json.loads((folder/'input_manifest.json').read_text())
            self.assertEqual(manifest['prompt_sha256'],prov['prompt_sha256'])
            self.assertEqual(manifest['output_contract'],'3.0.0-exp2')
            self.assertEqual(agent.call_count,1)


if __name__=='__main__':unittest.main()
