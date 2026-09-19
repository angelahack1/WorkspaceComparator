"""Offline and failing-service regressions; never requires an Ollama install."""
import json
import os
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from comparator.services import llm_comparator as llm
from comparator.services.correspondence import _LLMGate, find_correspondences
from comparator.services.file_scanner import FileInfo


def response(data=None, status=200, text=''):
    result = Mock(status_code=status, text=text)
    result.json.return_value = data
    if status >= 400:
        result.raise_for_status.side_effect = requests.HTTPError(str(status))
    return result


class OllamaClientTests(unittest.TestCase):
    def setUp(self):
        self.think = patch.object(llm, '_send_think_param', True)
        self.think.start()
        self.addCleanup(self.think.stop)

    def test_missing_unreachable_or_unusable_model(self):
        cases = [requests.ConnectionError(), requests.Timeout(),
                 response({}, 503), response({'models': []}),
                 response({'models': [{'name': 'other:latest'}]}),
                 response(None), response({'models': None}),
                 response({'models': [None, 3]}), response([])]
        for case in cases:
            with self.subTest(case=case), patch.object(llm.requests, 'get') as get:
                if isinstance(case, Exception):
                    get.side_effect = case
                else:
                    get.return_value = case
                self.assertFalse(llm.is_ollama_available())
                self.assertEqual(get.call_args.kwargs['timeout'], (1, 3))

    def test_configured_model_available(self):
        for key in ('name', 'model'):
            with self.subTest(key=key), patch.object(llm.requests, 'get', return_value=response(
                    {'models': [{key: llm.MODEL_NAME}]})):
                self.assertTrue(llm.is_ollama_available())

    def test_transport_and_http_failure_preserve_public_sentinel(self):
        for error in (requests.ConnectionError(), requests.Timeout(), requests.HTTPError()):
            with self.subTest(error=error), patch.object(llm.requests, 'post', side_effect=error):
                self.assertEqual(llm.compare_with_llm('a', 'text', 'b', 'other'), -1)
                with self.assertRaises(llm.OllamaUnavailable):
                    llm.compare_with_llm('a', 'text', 'b', 'other', raise_unavailable=True)

    def test_malformed_generation_does_not_escape(self):
        for data in (None, [], {'response': 12}, {'response': 'no score'}, {'error': 'failed'}):
            with self.subTest(data=data), patch.object(llm.requests, 'post', return_value=response(data)):
                self.assertEqual(llm.compare_with_llm('a', 'text', 'b', 'other'), -1)

    def test_only_think_errors_get_compatibility_retry(self):
        with patch.object(llm.requests, 'post', side_effect=[
                response({}, 400, 'think is not supported'), response({'response': '85'})]) as post:
            self.assertEqual(llm.compare_with_llm('a', 'text', 'b', 'other'), 85)
            self.assertEqual(post.call_count, 2)
            self.assertNotIn('think', post.call_args.kwargs['json'])
            self.assertEqual(post.call_args.kwargs['timeout'], (2, 15))

    def test_generic_bad_request_is_not_retried(self):
        with patch.object(llm.requests, 'post', return_value=response({}, 400, 'invalid model')) as post:
            self.assertEqual(llm.compare_with_llm('a', 'text', 'b', 'other'), -1)
            self.assertEqual(post.call_count, 1)
            self.assertTrue(llm._send_think_param)


class OfflineComparisonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.left = Path(self.temp.name) / 'left'
        self.right = Path(self.temp.name) / 'right'
        self.left.mkdir()
        self.right.mkdir()

    def put(self, root, name, content):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if isinstance(content, bytes) else content.encode())

    def ambiguous(self, count=1):
        for i in range(count):
            self.put(self.left, f'old/data{i}.txt', 'alpha beta gamma delta epsilon')
            self.put(self.right, f'new/data{i}.txt', 'alpha beta gamma zeta theta')

    def compare(self, **settings):
        return find_correspondences(str(self.left), str(self.right), settings=settings)

    def test_disabled_mode_makes_no_requests_and_keeps_fallback(self):
        self.ambiguous()
        with patch.object(llm.requests, 'get') as get, patch.object(llm.requests, 'post') as post:
            result = self.compare(max_llm_per_file=0, content_sim_threshold=100)
            self.assertEqual(len(result.matched), 1)
            self.assertEqual(result.matched[0].match_type, 'deterministic')
            self.assertEqual(result.stats['llm_calls'], 0)
            get.assert_not_called()
            post.assert_not_called()

    def test_missing_service_matches_like_disabled_mode(self):
        self.ambiguous(4)
        with patch.object(llm.requests, 'get', side_effect=requests.ConnectionError()) as get, \
                patch.object(llm.requests, 'post') as post:
            result = self.compare(content_sim_threshold=100)
            self.assertEqual(len(result.matched), 4)
            self.assertEqual(result.stats['llm_calls'], 0)
            self.assertEqual(get.call_count, 1)
            post.assert_not_called()

    def test_no_probe_for_empty_exact_identity_or_binary_comparisons(self):
        with patch.object(llm.requests, 'get') as get, patch.object(llm.requests, 'post') as post:
            self.assertFalse(self.compare().matched)
            self.put(self.left, 'same.txt', 'same')
            self.put(self.right, 'same.txt', 'same')
            self.put(self.left, 'old/A.java', 'class A {}')
            self.put(self.right, 'new/A.java', 'class A { int n = 4; }')
            self.put(self.left, 'old/data.bin', b'\x00\x01\x02')
            self.put(self.right, 'new/data.bin', b'\x00\x01\x03')
            self.assertEqual(len(self.compare().matched), 3)
            get.assert_not_called()
            post.assert_not_called()

    def test_generation_failure_disables_service_for_run(self):
        self.ambiguous(4)
        cases = [requests.Timeout(), requests.ConnectionError()] + [
            response({}, code) for code in (401, 403, 404, 429, 500, 503)]
        for case in cases:
            with self.subTest(case=case), patch.object(llm.requests, 'get', return_value=response(
                    {'models': [{'name': llm.MODEL_NAME}]})), patch.object(llm.requests, 'post') as post:
                if isinstance(case, Exception):
                    post.side_effect = case
                else:
                    post.return_value = case
                result = self.compare(content_sim_threshold=100)
                self.assertEqual(len(result.matched), 4)
                self.assertEqual(post.call_count, 1)
                self.assertEqual(result.stats['llm_calls'], 1)

    def test_malformed_answers_trip_configured_breaker(self):
        self.ambiguous(5)
        with patch.object(llm.requests, 'get', return_value=response(
                {'models': [{'name': llm.MODEL_NAME}]})), \
                patch.object(llm.requests, 'post', return_value=response({'response': 'unknown'})) as post:
            result = self.compare(llm_failure_limit=2)
            self.assertEqual(len(result.matched), 5)
            self.assertEqual(post.call_count, 2)

    def test_compare_endpoint_survives_missing_ollama(self):
        os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'workspace_comparator.settings')
        import django
        django.setup()
        from django.test import Client
        self.ambiguous()
        self.put(self.left, 'excluded.tmp', 'ignored')
        with patch.object(llm.requests, 'get', side_effect=requests.Timeout()), \
                patch.object(llm.requests, 'post') as post:
            result = Client().post('/api/compare/', data=json.dumps({
                'left_dir': str(self.left), 'right_dir': str(self.right),
                'exclusions': {'files': ['*.tmp']},
            }), content_type='application/json')
            self.assertEqual(result.status_code, 200)
            data = result.json()
            self.assertEqual(len(data['matched']), 1)
            self.assertTrue(any(f['name'] == 'excluded.tmp' for f in data['ignored_left']))
            post.assert_not_called()

    def test_available_service_can_still_match_and_recovers_on_next_run(self):
        self.ambiguous()
        with patch.object(llm.requests, 'get', side_effect=requests.ConnectionError()):
            self.assertEqual(self.compare().stats['llm_calls'], 0)
        with patch.object(llm.requests, 'get', return_value=response(
                {'models': [{'name': llm.MODEL_NAME}]})), \
                patch.object(llm.requests, 'post', return_value=response({'response': '92'})):
            result = self.compare()
            self.assertEqual(result.stats['llm_matches'], 1)
            self.assertEqual(result.matched[0].similarity, 92)

    def test_failure_in_renamed_phases_preserves_complete_accounting(self):
        self.put(self.left, 'old/customer.txt', 'alpha beta gamma delta epsilon')
        self.put(self.right, 'new/customers.txt', 'alpha beta gamma zeta theta')
        self.put(self.left, 'old/one.txt', 'red green blue orange black')
        self.put(self.right, 'new/xyz.txt', 'red green blue yellow white')
        with patch.object(llm.requests, 'get', return_value=response(
                {'models': [{'name': llm.MODEL_NAME}]})), \
                patch.object(llm.requests, 'post', side_effect=requests.Timeout()) as post:
            result = self.compare(content_sim_threshold=90)
            self.assertEqual(post.call_count, 1)
            self.assertEqual(len(result.matched) + len(result.unmatched_left), 2)
            self.assertEqual(len(result.matched) + len(result.unmatched_right), 2)

    def test_real_stalled_http_server_falls_back(self):
        class StalledOllama(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                body = json.dumps({'models': [{'name': llm.MODEL_NAME}]}).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                time.sleep(.3)  # Deliberately send no response.

        self.ambiguous(3)
        server = ThreadingHTTPServer(('127.0.0.1', 0), StalledOllama)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        base = f'http://127.0.0.1:{server.server_port}'
        try:
            with patch.object(llm, 'OLLAMA_BASE', base), \
                    patch.object(llm, 'OLLAMA_GENERATE', base + '/api/generate'), \
                    patch.object(llm, 'GENERATE_TIMEOUT', (.2, .05)):
                result = self.compare(content_sim_threshold=100)
                self.assertEqual(len(result.matched), 3)
                self.assertEqual(result.stats['llm_calls'], 1)
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)


class GateTests(unittest.TestCase):
    def test_success_resets_consecutive_failures(self):
        stats = {'llm_calls': 0}
        file = FileInfo('a.txt', '', 'unused', '.txt')
        with patch('comparator.services.correspondence.is_ollama_available', return_value=True) as probe, \
                patch('comparator.services.correspondence.compare_with_llm', side_effect=[-1, 80, -1, -1]) as score:
            gate = _LLMGate(stats, read=lambda _: 'text', failure_limit=2)
            self.assertEqual([gate.score(file, file) for _ in range(5)], [-1, 80, -1, -1, -1])
            self.assertEqual(score.call_count, 4)
            self.assertEqual(probe.call_count, 1)
            self.assertFalse(gate.enabled)

    def test_binary_and_changed_to_nul_content_never_probe_or_generate(self):
        with patch('comparator.services.correspondence.is_ollama_available') as probe, \
                patch('comparator.services.correspondence.compare_with_llm') as score:
            gate = _LLMGate({'llm_calls': 0}, read=lambda _: '\x00binary')
            file = FileInfo('a', '', 'unused', '', is_binary=True)
            self.assertEqual(gate.score(file, file), -1)
            file.is_binary = False
            self.assertEqual(gate.score(file, file), -1)
            probe.assert_not_called()
            score.assert_not_called()


if __name__ == '__main__':
    unittest.main()
