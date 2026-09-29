import io
import os
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from docx import Document
import app as marketai

PUBLIC_DNS = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', 443))]


class SecurityTests(unittest.TestCase):
    def setUp(self):
        self.client = marketai.app.test_client()

    def test_all_exports_return_readable_docx_without_disk(self):
        cases = [
            ('meeting', {'notes': '# 驗證會議\n- 測試事項'}, '驗證會議'),
            ('ad-report', {'cover': {'title': '驗證月報'}, 'sections': [{'title': '成果', 'content': '花費 100'}]}, '驗證月報'),
            ('work-dispatch', {'project_name': '驗證派工', 'work_packages': [{'id': 'WP-001', 'name': '素材'}]}, '驗證派工'),
        ]
        with patch.object(Path, 'open', side_effect=AssertionError('no filesystem output')):
            for route, payload, expected in cases:
                with self.subTest(route=route):
                    response = self.client.post(f'/api/{route}/export', json=payload)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.headers['Cache-Control'], 'no-store')
                    self.assertIn('attachment;', response.headers['Content-Disposition'])
                    doc = Document(io.BytesIO(response.data))
                    self.assertIn(expected, '\n'.join(p.text for p in doc.paragraphs))
        self.assertEqual(self.client.get('/api/download/anything.docx').status_code, 404)

    def test_upload_cleanup_success_and_failure(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(marketai, 'UPLOAD_FOLDER', Path(directory)):
            response = self.client.post('/api/upload', data={'file': (io.BytesIO('公司資料'.encode()), 'data.txt')})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json['text'], '公司資料')
            self.assertEqual(list(Path(directory).iterdir()), [])
            response = self.client.post('/api/upload', data={'file': (io.BytesIO(b'broken'), 'broken.docx')})
            self.assertEqual(response.status_code, 500)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_colab_disabled_and_unapproved_urls_never_make_requests(self):
        with patch.dict(os.environ, {'COLAB_API_URL': ''}), patch.object(marketai.http_requests, 'get') as request:
            self.assertEqual(self.client.post('/api/colab-health', json={'url': 'https://evil.example'}).status_code, 400)
            request.assert_not_called()
        with patch.dict(os.environ, {'COLAB_API_URL': 'https://trusted.example'}), patch.object(marketai.http_requests, 'get') as request:
            for url in ['http://127.0.0.1', 'https://trusted.example.evil.example', 'https://trusted.example@evil.example', 'https://trusted.example/path']:
                self.assertEqual(self.client.post('/api/colab-health', json={'url': url}).status_code, 400)
            request.assert_not_called()

    def test_colab_rejects_private_or_mixed_dns(self):
        with patch.dict(os.environ, {'COLAB_API_URL': 'https://trusted.example'}):
            for address in ['127.0.0.1', '10.0.0.1', '169.254.169.254', '::1', '::ffff:127.0.0.1']:
                dns = PUBLIC_DNS + [(socket.AF_INET6, socket.SOCK_STREAM, 6, '', (address, 443))]
                with patch.object(socket, 'getaddrinfo', return_value=dns), self.assertRaises(ValueError):
                    marketai.colab_endpoint('https://trusted.example', 'health')

    def test_colab_rejects_unsafe_admin_configuration(self):
        for url in ['http://trusted.example', 'https://trusted.example/path', 'https://user:pass@trusted.example', 'https://trusted.example:444', 'https://trusted.example?query=1', 'https://trusted.example#fragment']:
            with patch.dict(os.environ, {'COLAB_API_URL': url}), self.assertRaises(ValueError):
                marketai.colab_endpoint(url, 'health')

    def test_approved_colab_health_disables_redirects(self):
        with patch.dict(os.environ, {'COLAB_API_URL': 'https://trusted.example'}), patch.object(socket, 'getaddrinfo', return_value=PUBLIC_DNS), patch.object(marketai.http_requests, 'get', return_value=Mock(status_code=200, json=lambda: {'status': 'ok'})) as request:
            response = self.client.post('/api/colab-health', json={'url': 'https://trusted.example/'})
            self.assertEqual(response.status_code, 200)
            request.assert_called_once_with('https://trusted.example/health', timeout=10, allow_redirects=False)
            request.return_value.status_code = 302
            self.assertEqual(self.client.post('/api/colab-health', json={'url': 'https://trusted.example'}).status_code, 400)

    def test_transcription_cannot_follow_redirects_or_change_destination(self):
        with tempfile.NamedTemporaryFile() as audio, patch.dict(os.environ, {'COLAB_API_URL': 'https://trusted.example'}), patch.object(socket, 'getaddrinfo', return_value=PUBLIC_DNS), patch.object(marketai.http_requests, 'post', return_value=Mock(status_code=200, json=lambda: {'text': '完成'})) as request:
            self.assertEqual(marketai.transcribe_audio_remote(audio.name, 'https://trusted.example')['text'], '完成')
            self.assertFalse(request.call_args.kwargs['allow_redirects'])
            request.reset_mock()
            with self.assertRaises(ValueError):
                marketai.transcribe_audio_remote(audio.name, 'https://evil.example')
            request.assert_not_called()
            request.return_value.status_code = 302
            with self.assertRaises(RuntimeError):
                marketai.transcribe_audio_remote(audio.name, 'https://trusted.example')


if __name__ == '__main__':
    unittest.main()
