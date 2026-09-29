import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from flask import Flask, jsonify, render_template
from joserfc import jwt
from joserfc.jwk import RSAKey

from company_auth import init_company_auth, settings_from_env, authorized_identity

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = 'https://marketai.example'
TENANT = '11111111-2222-4333-8444-555555555555'
OBJECT = 'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee'
ENV = {'AUTH_MODE': 'google', 'AUTH_BASE_URL': ORIGIN, 'FLASK_SECRET_KEY': 'test-only-secret-0123456789-ABCDEFGHIJKLMNOPQRSTUVWXYZ',
       'OIDC_CLIENT_ID': 'test-client', 'OIDC_CLIENT_SECRET': 'test-client-secret', 'AUTH_GOOGLE_DOMAINS': 'company.example'}


def make_app(env=None):
    app = Flask(__name__, template_folder=str(ROOT / 'templates'), static_folder=str(ROOT / 'static'))
    app.secret_key = 'disabled-mode-test-key'
    app.testing = True
    init_company_auth(app, ENV if env is None else env)
    app.add_url_rule('/', 'home', lambda: render_template('home.html'))
    app.add_url_rule('/meeting', 'meeting', lambda: 'meeting')
    app.add_url_rule('/api/protected', 'protected', lambda: jsonify(ok=True), methods=['GET', 'POST'])
    return app


class ConfigurationTests(unittest.TestCase):
    def test_disabled_mode_preserves_existing_access(self):
        client = make_app({'AUTH_MODE': 'disabled'}).test_client()
        self.assertEqual(client.get('/').status_code, 200)
        self.assertEqual(client.post('/api/protected', json={}).status_code, 200)

    def test_invalid_or_partial_settings_fail_closed(self):
        cases = [
            {'AUTH_MODE': 'typo'}, {'AUTH_MODE': 'google'},
            {**ENV, 'FLASK_SECRET_KEY': 'dev-secret-key'}, {**ENV, 'OIDC_CLIENT_SECRET': ''},
            {**ENV, 'AUTH_GOOGLE_DOMAINS': '*'}, {**ENV, 'AUTH_GOOGLE_DOMAINS': ''},
            {**ENV, 'AUTH_BASE_URL': 'http://marketai.example'},
            {**ENV, 'AUTH_BASE_URL': 'https://marketai.example/path'},
            {**ENV, 'AUTH_BASE_URL': 'https://user:pass@marketai.example'},
            {**ENV, 'AUTH_BASE_URL': 'http://localhost:5055', 'VERCEL': '1'},
            {**ENV, 'AUTH_MODE': 'microsoft', 'AUTH_MICROSOFT_TENANT_ID': 'common'},
        ]
        for env in cases:
            with self.subTest(env=str({k: v for k, v in env.items() if 'SECRET' not in k})):
                client = make_app(env).test_client()
                self.assertEqual(client.get('/').status_code, 503)
                response = client.post('/api/protected', json={})
                self.assertEqual(response.status_code, 503)
                self.assertNotIn('test-client-secret', response.get_data(as_text=True))
                with client.get('/static/js/common.js') as static_response:
                    self.assertEqual(static_response.status_code, 200)

    def test_allowlist_uses_google_hd_not_email_suffix(self):
        config = settings_from_env(ENV)
        base = {'iss': config['issuer'], 'sub': 'subject', 'email_verified': True, 'email': 'person@company.example'}
        for claims in [base, {**base, 'hd': 'evil.example'}, {**base, 'hd': 'company.example.evil.example'}, {**base, 'hd': 'company.example', 'email_verified': False}]:
            with self.assertRaises(ValueError):
                authorized_identity(claims, config)
        self.assertTrue(authorized_identity({**base, 'hd': 'company.example'}, config)['id'])

    def test_microsoft_requires_tenant_object_and_assigned_role(self):
        config = settings_from_env({**ENV, 'AUTH_MODE': 'microsoft', 'AUTH_MICROSOFT_TENANT_ID': TENANT})
        claims = {'iss': config['issuer'], 'sub': 'subject', 'tid': TENANT, 'oid': OBJECT, 'roles': ['MarketAI.User'], 'email': 'any-display-only@example.com'}
        self.assertTrue(authorized_identity(claims, config)['id'])
        for invalid in [{**claims, 'tid': OBJECT}, {**claims, 'oid': ''}, {**claims, 'roles': []}, {**claims, 'roles': 'MarketAI.User'}, {**claims, 'iss': 'https://evil.example'}]:
            with self.assertRaises(ValueError):
                authorized_identity(invalid, config)


class LoginTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = RSAKey.generate_key(2048, parameters={'kid': 'test-key'})
        cls.wrong_key = RSAKey.generate_key(2048, parameters={'kid': 'test-key'})

    def setUp(self):
        self.app = make_app()
        self.client = self.app.test_client()
        self.remote = self.app.extensions['company_oidc']
        self.config = self.app.extensions['company_auth_config']
        self.metadata = {'issuer': self.config['issuer'], 'authorization_endpoint': 'https://accounts.google.com/o/oauth2/v2/auth',
                         'token_endpoint': 'https://oauth2.googleapis.com/token', 'jwks_uri': 'https://www.googleapis.com/oauth2/v3/certs',
                         'id_token_signing_alg_values_supported': ['RS256']}
        self.patchers = [patch.object(self.remote, 'load_server_metadata', return_value=self.metadata),
                         patch.object(self.remote, 'fetch_jwk_set', return_value={'keys': [self.key.as_dict(private=False)]})]
        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def get(self, path, **kwargs):
        return self.client.get(path, base_url=ORIGIN, **kwargs)

    def post(self, path, **kwargs):
        return self.client.post(path, base_url=ORIGIN, **kwargs)

    def start(self, next_path='/meeting'):
        response = self.get('/auth/start', query_string={'next': next_path}, headers={'Host': 'marketai.example'})
        self.assertEqual(response.status_code, 302)
        query = parse_qs(urlsplit(response.location).query)
        self.assertEqual(query['redirect_uri'], [ORIGIN + '/auth/callback'])
        self.assertEqual(query['code_challenge_method'], ['S256'])
        self.assertIn('nonce', query)
        return query

    def finish(self, query, changes=None, key=None):
        now = int(time.time())
        claims = {'iss': self.config['issuer'], 'aud': 'test-client', 'sub': 'stable-subject', 'iat': now, 'exp': now + 1800,
                  'nonce': query['nonce'][0], 'hd': 'company.example', 'email_verified': True, 'name': '公司同事',
                  **(changes or {})}
        encoded = jwt.encode({'alg': 'RS256', 'kid': 'test-key'}, claims, key or self.key)
        token = {'access_token': 'must-not-persist-access-token', 'id_token': encoded, 'token_type': 'Bearer'}
        with patch.object(self.remote, 'fetch_access_token', return_value=token) as fetch:
            response = self.get('/auth/callback', query_string={'state': query['state'][0], 'code': 'test-code'})
            if fetch.called:
                self.assertTrue(fetch.call_args.kwargs['code_verifier'])
        return response

    def login(self):
        response = self.finish(self.start())
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, '/meeting')
        with self.client.session_transaction(base_url=ORIGIN) as sess:
            return sess['csrf_token']

    def test_unauthenticated_pages_redirect_and_apis_never_execute(self):
        self.assertEqual(self.get('/').location, '/login?next=/')
        for method in (self.get, self.post):
            response = method('/api/protected')
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.json['login_url'], '/login')
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertEqual(self.get('/login').status_code, 200)

    def test_real_signed_token_creates_bounded_session_without_tokens(self):
        token = self.login()
        self.assertEqual(self.get('/meeting').status_code, 200)
        response = self.post('/api/protected', json={}, headers={'X-CSRF-Token': token, 'Origin': ORIGIN})
        self.assertEqual(response.status_code, 200)
        with self.client.session_transaction(base_url=ORIGIN) as sess:
            self.assertLessEqual(sess['auth_expires'], time.time() + 1800)
            self.assertNotIn('access_token', sess)
            self.assertNotIn('id_token', sess)
            self.assertNotIn('oidc_pending', sess)
        self.assertTrue(self.app.config['SESSION_COOKIE_HTTPONLY'])
        self.assertTrue(self.app.config['SESSION_COOKIE_SECURE'])
        self.assertEqual(self.app.config['SESSION_COOKIE_SAMESITE'], 'Lax')

    def test_oidc_rejects_wrong_signature_audience_issuer_nonce_and_expiry(self):
        for changes, key in [({}, self.wrong_key), ({'aud': 'other-client'}, None), ({'iss': 'https://evil.example'}, None),
                             ({'nonce': 'wrong'}, None), ({'exp': int(time.time()) - 10}, None),
                             ({'nonce': 'wrong', 'nonce_supported': False}, None), ({'hd': 'other.example'}, None)]:
            with self.subTest(changes=changes, wrong_key=key is not None):
                self.client = self.app.test_client()
                response = self.finish(self.start(), changes, key)
                self.assertEqual(response.status_code, 403)
                self.assertEqual(self.get('/api/protected').status_code, 401)

    def test_state_and_callback_replay_rejected_without_token_exchange(self):
        query = self.start()
        with patch.object(self.remote, 'fetch_access_token') as fetch:
            self.assertEqual(self.get('/auth/callback?state=forged&code=x').status_code, 400)
            fetch.assert_not_called()
        self.assertEqual(self.finish(query).status_code, 302)
        with patch.object(self.remote, 'fetch_access_token') as fetch:
            self.assertEqual(self.get('/auth/callback', query_string={'state': query['state'][0], 'code': 'test-code'}).status_code, 400)
            fetch.assert_not_called()

    def test_expired_login_attempt_rejected(self):
        query = self.start()
        with self.client.session_transaction(base_url=ORIGIN) as sess:
            pending = dict(sess['oidc_pending'])
            pending['started'] = time.time() - 601
            sess['oidc_pending'] = pending
        with patch.object(self.remote, 'fetch_access_token') as fetch:
            self.assertEqual(self.finish(query).status_code, 400)
            fetch.assert_not_called()

    def test_no_open_redirect(self):
        for target in ['https://evil.example', '//evil.example', '/auth/start', '/\\evil.example']:
            self.client = self.app.test_client()
            self.assertEqual(self.finish(self.start(target)).location, '/')

    def test_csrf_required_for_json_multipart_and_wrong_origin(self):
        token = self.login()
        self.assertEqual(self.post('/api/protected', json={}).status_code, 403)
        self.assertEqual(self.post('/api/protected', data={'x': 'y'}).status_code, 403)
        self.assertEqual(self.post('/api/protected', json={}, headers={'X-CSRF-Token': token, 'Origin': 'https://evil.example'}).status_code, 403)
        self.assertEqual(self.post('/api/protected', data={'x': 'y'}, headers={'X-CSRF-Token': token}).status_code, 200)

    def test_logout_requires_post_and_csrf_then_revokes_browser_session(self):
        token = self.login()
        self.assertNotEqual(self.get('/auth/logout').status_code, 200)
        self.assertEqual(self.post('/auth/logout').status_code, 403)
        self.assertEqual(self.get('/api/protected').status_code, 200)
        response = self.post('/auth/logout', data={'csrf_token': token}, headers={'Origin': ORIGIN})
        self.assertEqual(response.status_code, 200)
        self.assertIn('logout.js', response.get_data(as_text=True))
        self.assertEqual(self.get('/api/protected').status_code, 401)

    def test_expired_or_old_policy_session_denied(self):
        self.login()
        with self.client.session_transaction(base_url=ORIGIN) as sess:
            sess['auth_expires'] = time.time() - 1
        self.assertEqual(self.get('/api/protected').status_code, 401)
        self.login()
        with self.client.session_transaction(base_url=ORIGIN) as sess:
            sess['auth_policy'] = 'old-policy'
        self.assertEqual(self.get('/api/protected').status_code, 401)

    def test_display_name_escaped_and_provider_errors_not_exposed(self):
        response = self.finish(self.start(), {'name': '<img src=x onerror=alert(1)>'})
        self.assertEqual(response.status_code, 302)
        html = self.get('/').get_data(as_text=True)
        self.assertIn('&lt;img', html)
        self.assertNotIn('<img src=x', html)
        query = self.start()
        with patch.object(self.remote, 'fetch_access_token', side_effect=RuntimeError('test-client-secret')):
            response = self.get('/auth/callback', query_string={'state': query['state'][0], 'code': 'x'})
        self.assertNotIn('test-client-secret', response.get_data(as_text=True))


if __name__ == '__main__':
    unittest.main()
