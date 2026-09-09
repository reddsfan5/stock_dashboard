import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import serve
from scripts.services.system_status import public_status
from scripts.services.static_reports import ASSETS, ReportHandler
from scripts.services.ui_shell import mobile_redirect


class PublicStatusTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'status.json'

    def write(self, value):
        self.path.write_text(json.dumps(value))
        return public_status(self.path)

    def test_missing_and_corrupt_file(self):
        self.assertEqual(public_status(self.path)['state'], 'missing')
        self.path.write_text('{')
        self.assertEqual(public_status(self.path)['state'], 'error')
        self.assertEqual(self.write([])['state'], 'error')

    def test_stage_failure_overrides_stale_success_and_omits_private_details(self):
        result = self.write(dict(state='success', ok=True, target_date='2026-09-04',
            stages=[dict(name='stocks', ok=False, duration_seconds=2,
                message='Traceback /private/secret.py', details={'password': 'secret'})]))
        self.assertEqual(result['state'], 'failed')
        self.assertFalse(result['ok'])
        self.assertNotIn('secret', json.dumps(result))
        self.assertEqual(result['stages'][0]['duration_seconds'], 2)

    def test_partial_status_and_nonfinite_duration(self):
        result = self.write(dict(state='running', stages=[None, dict(name='reports', duration_seconds=float('nan'))]))
        self.assertEqual(result['state'], 'running')
        self.assertEqual(len(result['stages']), 1)
        self.assertIsNone(result['stages'][0]['duration_seconds'])
        self.assertEqual(self.write(dict(stages=42))['stages'], [])


class ServiceCompatibilityTest(unittest.TestCase):
    def test_lan_defaults_on(self):
        self.assertTrue(serve.build_parser().parse_args([]).lan)
        self.assertTrue(serve.build_parser().parse_args(['restart', 'web']).lan)
        self.assertTrue(serve.build_parser().parse_args(['restart', 'web', '--lan']).lan)
        self.assertFalse(serve.build_parser().parse_args(['restart', 'web', '--no-lan']).lan)

    def test_listening_mode_mismatch_requires_restart(self):
        with patch.object(serve, 'web_is_healthy', return_value=True), patch.object(
            serve, '_http_json', return_value=(200, {'listen_host': '127.0.0.1'})
        ), patch.object(serve, '_spawn') as spawn:
            self.assertFalse(serve.start_web(lan=True))
            spawn.assert_not_called()

    def test_start_passes_requested_host(self):
        for lan, host in [(False, '127.0.0.1'), (True, '0.0.0.0')]:
            with self.subTest(lan=lan), patch.object(serve, 'web_is_healthy', return_value=False), patch.object(
                serve, '_listening_pids', return_value=[]
            ), patch.object(serve, '_spawn', return_value=(1, '/tmp/qa.log')) as spawn, patch.object(
                serve, '_wait_until', return_value=True
            ), patch.object(serve.socket, 'getaddrinfo', return_value=[]), patch.object(serve.subprocess, 'run'):
                # Avoid address probing in this command construction test.
                with patch('builtins.print'):
                    if lan:
                        with patch.object(serve.subprocess, 'run', side_effect=OSError):
                            self.assertTrue(serve.start_web(lan=lan))
                    else:
                        self.assertTrue(serve.start_web(lan=lan))
                command = spawn.call_args.args[1]
                self.assertEqual(command[command.index('--host') + 1], host)

    def test_static_asset_mapping_cannot_escape_asset_root(self):
        handler = object.__new__(ReportHandler)
        self.assertEqual(handler.translate_path('/assets/workbench.css?v=1'), str(ASSETS / 'workbench.css'))
        self.assertEqual(handler.translate_path('/assets/../../serve.py'), str(ASSETS / '__not_found__'))

    def test_mobile_redirect_retains_context(self):
        html = mobile_redirect('dashboard.html')
        self.assertIn('location.search', html)
        self.assertIn('location.hash', html)
        self.assertIn('dashboard.html', html)

    def test_dashboard_workbench_exposes_filtered_txt_export(self):
        source = (ASSETS / 'workbench.js').read_text(encoding='utf-8')
        self.assertIn('导出当前命中 TXT', source)
        self.assertIn('exportScreeningTxt', source)
        self.assertIn('screenExportStatus', source)


if __name__ == '__main__':
    unittest.main()
