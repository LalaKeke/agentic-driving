"""Protocol mocks only. These tests are never real model/G2 measurements."""
from copy import deepcopy
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch,Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image
from agentic.codex_adapter import CodexSubscriptionAgent,parse_events,subscription_environment,DISABLED_FEATURES
from agentic.contracts import Failure,digest
from agentic.fixtures import capture,ScriptedAgent

class CodexAdapterTests(unittest.TestCase):
    def agent(self):
        def run(cmd,**kwargs):
            return subprocess.CompletedProcess(cmd,0,'Logged in using ChatGPT' if cmd[1:]==['login','status'] else 'codex-cli unit-test','')
        with patch('agentic.codex_adapter.shutil.which',return_value='/unit/codex'),patch('agentic.codex_adapter.subprocess.run',side_effect=run):
            return CodexSubscriptionAgent(model='gpt-6.1-sol',timeout_s=2)
    def test_api_keys_removed_without_modifying_process_environment(self):
        with patch.dict(os.environ,dict(OPENAI_API_KEY='UNIT_KEY',CODEX_API_KEY='UNIT_KEY',DRIVING_API_KEY='UNIT_KEY')):
            env=subscription_environment()
            self.assertTrue(all(k not in env for k in ('OPENAI_API_KEY','CODEX_API_KEY','DRIVING_API_KEY')))
            self.assertEqual(os.environ['OPENAI_API_KEY'],'UNIT_KEY')
    def test_api_key_login_is_rejected(self):
        with patch('agentic.codex_adapter.shutil.which',return_value='/unit/codex'),patch('agentic.codex_adapter.subprocess.run',return_value=subprocess.CompletedProcess([],0,'Logged in using API key','')):
            with self.assertRaises(Failure) as ctx:CodexSubscriptionAgent()
        self.assertEqual(ctx.exception.record['code'],'CODEX_CHATGPT_LOGIN_REQUIRED')
    def test_command_pins_requested_model_and_disables_tools(self):
        a=self.agent();cmd=a.command(Path('/tmp/unit'),[Path('/tmp/unit/frame.jpg')])
        self.assertEqual(cmd[cmd.index('--model')+1],'gpt-6.1-sol')
        self.assertIn('forced_login_method="chatgpt"',cmd)
        self.assertIn('--ignore-user-config',cmd)
        for name in DISABLED_FEATURES:self.assertTrue(any(cmd[i:i+2]==['--disable',name] for i in range(len(cmd)-1)))
    def test_events_reject_tool_access(self):
        text=json.dumps(dict(type='item.started',item=dict(type='command_execution',command='cat truth.json')))
        with self.assertRaises(Failure) as ctx:parse_events(text,'gpt-6.1-sol')
        self.assertEqual(ctx.exception.record['code'],'CODEX_TOOL_USE')
    def test_events_reject_substitution_and_missing_completion(self):
        with self.assertRaises(Failure):parse_events(json.dumps(dict(type='thread.started',model='other-model')),'gpt-6.1-sol')
        with self.assertRaises(Failure):parse_events(json.dumps(dict(type='thread.started')),'gpt-6.1-sol')
    def test_disabled_code_mode_notice_is_not_a_tool_call(self):
        text='\n'.join(json.dumps(e) for e in [dict(type='item.completed',item=dict(type='error',message='Code Mode is unavailable because code-mode host is disabled. Code mode will fail closed.')),dict(type='item.completed',item=dict(type='agent_message',text='{"ok":true}')),dict(type='turn.completed',usage={})])
        out,events=parse_events(text,'gpt-6.1-sol')
        self.assertEqual(out,{'ok':True});self.assertEqual(events['tool_calls'],0)
        self.assertEqual(events['disabled_capability_notices'],['code_mode_host_disabled'])
    def test_other_error_items_remain_fail_closed(self):
        text=json.dumps(dict(type='item.completed',item=dict(type='error',message='An input image could not be loaded')))
        with self.assertRaises(Failure) as ctx:parse_events(text,'gpt-6.1-sol')
        self.assertEqual(ctx.exception.record['code'],'CODEX_RUNTIME_DIAGNOSTIC')
    def test_real_image_attachment_protocol_with_mock_response(self):
        a=self.agent()
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'real-pixels-unit.png';Image.new('RGB',(8,8),'red').save(path)
            cap=capture();cap['observations'][0]['path']=str(path);out,_=ScriptedAgent().decide(cap)
            events='\n'.join(json.dumps(e) for e in [dict(type='item.completed',item=dict(type='agent_message',text=json.dumps(out))),dict(type='turn.completed',usage=dict(input_tokens=1,output_tokens=1))])
            process=Mock();process.returncode=0;process.communicate.return_value=(events,'')
            def popen(cmd,**kwargs):
                self.assertNotIn('OPENAI_API_KEY',kwargs['env']);self.assertNotIn(str(path),cmd)
                self.assertNotIn('agentic-driving',str(kwargs['cwd']))
                attached=Path(cmd[cmd.index('--image')+1]);self.assertEqual(attached.read_bytes(),path.read_bytes())
                return process
            with patch('agentic.codex_adapter.subprocess.Popen',side_effect=popen):output,prov=a.decide(cap)
            self.assertEqual(prov['output_digest'],digest(output));self.assertEqual(prov['requested_model'],'gpt-6.1-sol');self.assertEqual(prov['tool_calls'],0)
            sent=process.communicate.call_args.args[0]
            self.assertNotIn(str(path),sent);self.assertIn('attached:frame_000.png',sent)
    def test_timeout_kills_process_group_and_returns_no_output(self):
        a=self.agent()
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'unit.png';Image.new('RGB',(8,8)).save(path);cap=capture();cap['observations'][0]['path']=str(path)
            process=Mock();process.pid=424242;process.communicate.side_effect=[subprocess.TimeoutExpired('codex',2),('','')]
            with patch('agentic.codex_adapter.subprocess.Popen',return_value=process),patch('agentic.codex_adapter.os.killpg') as kill:
                with self.assertRaises(Failure) as ctx:a.decide(cap)
            kill.assert_called_once_with(424242,signal.SIGKILL);self.assertEqual(ctx.exception.record['code'],'CODEX_TIMEOUT')
    def test_failed_cli_does_not_publish_raw_stderr(self):
        a=self.agent()
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'unit.png';Image.new('RGB',(8,8)).save(path);cap=capture();cap['observations'][0]['path']=str(path)
            process=Mock();process.returncode=1;process.communicate.return_value=('','connection timed out UNIT_SECRET')
            with patch('agentic.codex_adapter.subprocess.Popen',return_value=process):
                with self.assertRaises(Failure) as ctx:a.decide(cap)
            self.assertNotIn('UNIT_SECRET',str(ctx.exception.record));self.assertEqual(ctx.exception.record['code'],'CODEX_TRANSPORT')
