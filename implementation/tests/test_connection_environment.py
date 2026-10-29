"""Child environment routing only: no sockets, account reads or model calls."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agentic.codex_adapter import subscription_environment
from agentic.contracts import Failure

PROXY_KEYS=('HTTPS_PROXY','HTTP_PROXY','ALL_PROXY','https_proxy','http_proxy','all_proxy')
KEYS=('OPENAI_API_KEY','CODEX_API_KEY','DRIVING_API_KEY')


class ConnectionEnvironmentTests(unittest.TestCase):
    def test_pilot_rpc_keeps_the_preflight_route_when_parent_environment_changes(self):
        from agentic.pilot_transport import PilotSubscriptionAgent
        calls=[]
        def factory(command,**kwargs):
            calls.append(kwargs['env'])
            raise OSError('synthetic process boundary; no subprocess or network')
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ,{'DRIVING_PROXY_URL':'http://127.0.0.1:17891','NO_PROXY':'*',
                                        'DRIVING_API_KEY':'synthetic_fixture_only'},clear=True):
                agent=PilotSubscriptionAgent(evidence_dir=Path(tmp)/'evidence',process_factory=factory)
            with patch.dict(os.environ,{'HTTPS_PROXY':'http://127.0.0.1:17890','NO_PROXY':'*'},clear=True):
                with self.assertRaises(OSError):agent._rpc(Path(tmp),[],{},Path(tmp)/'attempt')
            self.assertEqual(len(calls),1)
            self.assertTrue(all(calls[0][key]=='http://127.0.0.1:17891' for key in PROXY_KEYS))
            self.assertNotIn('NO_PROXY',calls[0]);self.assertNotIn('DRIVING_API_KEY',calls[0])
            profile=agent.public_config['connection_profile']
            self.assertEqual(profile['proxy_variables']['HTTPS_PROXY']['port'],17891)
            self.assertFalse(profile['no_proxy_present'])

    def test_legacy_environment_is_preserved_except_api_credentials(self):
        original={
            'HTTPS_PROXY':'http://old-upper.invalid:17890',
            'https_proxy':'http://old-lower.invalid:17891',
            'NO_PROXY':'.chatgpt.com','no_proxy':'localhost',
            'SSL_CERT_FILE':'/legacy/trust.pem','HOME':'/unchanged/home',
            **{key:'synthetic_fixture_only' for key in KEYS}}
        with patch.dict(os.environ,original,clear=True):
            result=subscription_environment()
            self.assertEqual(result,{key:value for key,value in original.items() if key not in KEYS})
            self.assertEqual(dict(os.environ),original)

    def test_explicit_route_overrides_all_casings_and_bypass_only_in_child(self):
        original={**{key:'http://stale.invalid:17890' for key in PROXY_KEYS},
                  'NO_PROXY':'*','no_proxy':'.chatgpt.com',
                  **{key:'synthetic_fixture_only' for key in KEYS}}
        with patch.dict(os.environ,original,clear=True):
            result=subscription_environment(proxy_url='http://127.0.0.1:17891')
            self.assertTrue(all(result[key]=='http://127.0.0.1:17891' for key in PROXY_KEYS))
            self.assertNotIn('NO_PROXY',result)
            self.assertNotIn('no_proxy',result)
            self.assertTrue(all(key not in result for key in KEYS))
            self.assertEqual(dict(os.environ),original)

    def test_project_proxy_setting_and_explicit_keyword_precedence(self):
        with patch.dict(os.environ,{'DRIVING_PROXY_URL':'https://proxy.invalid:8443/'},clear=True):
            result=subscription_environment()
            self.assertTrue(all(result[key]=='https://proxy.invalid:8443/' for key in PROXY_KEYS))
            overridden=subscription_environment(proxy_url='http://[::1]:8123')
            self.assertTrue(all(overridden[key]=='http://[::1]:8123' for key in PROXY_KEYS))

    def test_bad_or_credential_proxy_is_rejected_without_echo(self):
        values=('',False,17891,'127.0.0.1:17891','socks5://localhost:1080',
                'http://localhost','http://localhost:0','http://localhost:65536',
                'http://localhost:notaport','http://:17891','http://[broken:17891',
                'http://fixture-user:fixture-secret@localhost:17891',
                'http://localhost:17891/private','http://localhost:17891?secret=fixture',
                'http://localhost:17891#fragment','http://local host:17891',
                'http://localhost:17891\n')
        with patch.dict(os.environ,{},clear=True):
            for value in values:
                with self.subTest(value_type=type(value).__name__):
                    with self.assertRaises(Failure) as caught:
                        subscription_environment(proxy_url=value)
                    record=caught.exception.record
                    self.assertEqual(record['code'],'CODEX_PROXY_CONFIGURATION')
                    self.assertNotIn('fixture-secret',str(record))
                    self.assertNotIn('fixture-user',str(record))
                    if isinstance(value,str) and value:
                        self.assertNotIn(value,str(record))

    def test_bad_project_proxy_setting_is_not_silently_ignored(self):
        with patch.dict(os.environ,{'DRIVING_PROXY_URL':''},clear=True):
            with self.assertRaises(Failure) as caught:
                subscription_environment()
            self.assertEqual(caught.exception.record['code'],'CODEX_PROXY_CONFIGURATION')

    def test_ca_bundle_selects_existing_file_without_changing_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle=Path(tmp)/'trusted-fixture.pem'
            bundle.write_text('test file only; no network or certificate validation performed\n')
            original={'DRIVING_CA_BUNDLE':str(bundle),'SSL_CERT_FILE':'/legacy/trust.pem'}
            with patch.dict(os.environ,original,clear=True):
                result=subscription_environment()
                self.assertEqual(result['SSL_CERT_FILE'],str(bundle.resolve()))
                self.assertEqual(dict(os.environ),original)
                explicit=subscription_environment(ca_bundle=bundle)
                self.assertEqual(explicit['SSL_CERT_FILE'],str(bundle.resolve()))
                self.assertNotIn('PYTHONHTTPSVERIFY',result)

    def test_missing_empty_and_directory_ca_paths_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{},clear=True):
            for value in ('',Path(tmp)/'missing.pem',tmp,False,'\0'):
                with self.subTest(value_type=type(value).__name__):
                    with self.assertRaises(Failure) as caught:
                        subscription_environment(ca_bundle=value)
                    self.assertEqual(caught.exception.record['code'],'CODEX_CA_CONFIGURATION')


if __name__=='__main__':
    unittest.main()
