"""Optional company OIDC login. Provider configuration is controlled by deployment.

AUTH_MODE=disabled preserves the existing rollout until an administrator configures
SSO. Any other invalid or incomplete configuration fails closed.
"""
import hashlib
import json
import os
import re
import secrets
import time
import uuid
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

from authlib.integrations.flask_client import OAuth
from flask import Blueprint, g, jsonify, redirect, render_template, request, session

PUBLIC_ENDPOINTS = {'static', 'company_auth.login', 'company_auth.start', 'company_auth.callback', 'company_auth.logout'}
PAGE_PATHS = {'/', '/meeting', '/ad-report', '/work-dispatch'}


def settings_from_env(environ):
    mode = environ.get('AUTH_MODE', 'disabled').strip().lower()
    config = {'mode': mode, 'enabled': mode != 'disabled', 'error': None}
    if not config['enabled']:
        return config
    try:
        if mode not in ('google', 'microsoft'):
            raise ValueError('AUTH_MODE must be disabled, google or microsoft')
        secret = environ.get('FLASK_SECRET_KEY', '')
        if len(secret) < 32 or secret in ('your_secret_key_here', 'dev-secret-key'):
            raise ValueError('A strong FLASK_SECRET_KEY of at least 32 characters is required')
        base = environ.get('AUTH_BASE_URL', '').strip().rstrip('/')
        url = urlsplit(base)
        local_http = url.scheme == 'http' and url.hostname in ('localhost', '127.0.0.1') and environ.get('VERCEL') != '1'
        if (not url.hostname or (url.scheme != 'https' and not local_http)
                or url.username or url.password or url.path or url.query or url.fragment):
            raise ValueError('AUTH_BASE_URL must be a fixed HTTPS origin')
        client_id = environ.get('OIDC_CLIENT_ID', '').strip()
        client_secret = environ.get('OIDC_CLIENT_SECRET', '').strip()
        if not client_id or not client_secret:
            raise ValueError('OIDC client credentials are required')
        config.update(base_url=base, client_id=client_id, client_secret=client_secret,
                      secure=not local_http, secret=secret)
        if mode == 'google':
            domains = sorted(set(d.strip().lower() for d in environ.get('AUTH_GOOGLE_DOMAINS', '').split(',') if d.strip()))
            if not domains or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+', d) for d in domains):
                raise ValueError('Explicit Google Workspace hosted domains are required')
            config.update(domains=domains, issuer='https://accounts.google.com', label='Google Workspace')
            config['metadata_url'] = 'https://accounts.google.com/.well-known/openid-configuration'
        else:
            tenant = str(uuid.UUID(environ.get('AUTH_MICROSOFT_TENANT_ID', '')))
            role = environ.get('AUTH_MICROSOFT_ROLE', 'MarketAI.User').strip()
            if not role or len(role) > 100:
                raise ValueError('An assigned Microsoft application role is required')
            config.update(tenant=tenant, role=role, issuer=f'https://login.microsoftonline.com/{tenant}/v2.0', label='Microsoft 365')
            config['metadata_url'] = config['issuer'] + '/.well-known/openid-configuration'
        policy = {k: config.get(k) for k in ('mode', 'base_url', 'client_id', 'domains', 'tenant', 'role')}
        config['policy'] = hashlib.sha256(json.dumps(policy, sort_keys=True).encode()).hexdigest()
    except (ValueError, TypeError) as exc:
        config['error'] = str(exc)
    return config


def authorized_identity(claims, config):
    """Called only after Authlib's cryptographic OIDC validation."""
    if not isinstance(claims, dict) or claims.get('iss') != config['issuer']:
        raise ValueError('Issuer mismatch')
    subject = claims.get('sub')
    if not isinstance(subject, str) or not subject or len(subject) > 255:
        raise ValueError('Missing subject')
    if config['mode'] == 'google':
        if claims.get('email_verified') is not True or claims.get('hd') not in config['domains']:
            raise ValueError('Workspace membership not allowed')
        principal = config['issuer'] + ':' + subject
    else:
        if claims.get('tid') != config['tenant']:
            raise ValueError('Tenant mismatch')
        oid = str(uuid.UUID(claims.get('oid', '')))
        roles = claims.get('roles', [])
        if not isinstance(roles, list) or config['role'] not in roles:
            raise ValueError('Application role not assigned')
        principal = config['tenant'] + ':' + oid
    # Email/name are display-only, never Microsoft authorization keys.
    name = claims.get('name') or claims.get('email') or '公司使用者'
    return {'id': hashlib.sha256(principal.encode()).hexdigest(), 'name': str(name)[:150]}


def init_company_auth(app, environ=None):
    config = settings_from_env(os.environ if environ is None else environ)
    app.extensions['company_auth_config'] = config
    auth = Blueprint('company_auth', __name__)
    remote = None
    if config['enabled'] and not config['error']:
        app.secret_key = config['secret']
        app.config.update(SESSION_COOKIE_NAME='marketai_session', SESSION_COOKIE_SECURE=config['secure'],
                          SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax',
                          SESSION_REFRESH_EACH_REQUEST=False, PERMANENT_SESSION_LIFETIME=timedelta(hours=1))
        oauth = OAuth(app)
        remote = oauth.register('company', client_id=config['client_id'], client_secret=config['client_secret'],
                                server_metadata_url=config['metadata_url'],
                                client_kwargs={'scope': 'openid profile email', 'code_challenge_method': 'S256', 'timeout': 15})
    app.extensions['company_oidc'] = remote

    def error_response(message, status):
        if request.path.startswith('/api/'):
            return jsonify(error=message, login_url='/login'), status
        return render_template('login.html', message=message, provider=None), status

    def current_identity():
        identity = session.get('identity')
        expires = session.get('auth_expires', 0)
        if (not isinstance(identity, dict) or not identity.get('id')
                or not isinstance(expires, (int, float)) or expires <= time.time()
                or session.get('auth_policy') != config.get('policy')):
            return None
        return identity

    @app.before_request
    def require_company_login():
        g.company_auth_enabled = config['enabled']
        g.company_user = None
        if not config['enabled'] or request.endpoint == 'static':
            return None
        if config['error']:
            return error_response('公司登入設定尚未完成，請聯絡管理員。', 503)
        g.company_user = current_identity()
        if request.endpoint in PUBLIC_ENDPOINTS:
            return None
        if g.company_user is None:
            if request.path.startswith('/api/'):
                return error_response('登入已過期或尚未登入，請重新登入後再試。', 401)
            path = request.path if request.path in PAGE_PATHS else '/'
            return redirect('/login?next=' + path)
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            supplied = request.headers.get('X-CSRF-Token', '')
            expected = session.get('csrf_token', '')
            origin = request.headers.get('Origin')
            if (not expected or not secrets.compare_digest(supplied, expected)
                    or (origin is not None and origin != config['base_url'])):
                return error_response('登入狀態已變更，請重新整理頁面後再試。', 403)

    @app.after_request
    def auth_response_headers(response):
        if config['enabled'] and request.endpoint != 'static':
            response.headers['Cache-Control'] = 'no-store'
            # Keep same-origin form POST Origin intact; never leak callback codes.
            response.headers['Referrer-Policy'] = 'no-referrer' if request.endpoint == 'company_auth.callback' else 'same-origin'
            response.headers['X-Content-Type-Options'] = 'nosniff'
            response.headers['X-Frame-Options'] = 'DENY'
        return response

    @app.context_processor
    def auth_context():
        return {'company_auth_enabled': config['enabled'], 'company_user': getattr(g, 'company_user', None),
                'csrf_token': session.get('csrf_token', '') if config['enabled'] and not config['error'] else ''}

    @auth.get('/login')
    def login():
        if not config['enabled']:
            return redirect('/')
        next_path = request.args.get('next', '/')
        next_path = next_path if next_path in PAGE_PATHS else '/'
        if current_identity():
            return redirect(next_path)
        return render_template('login.html', provider=config['label'], next_path=next_path, message=None)

    @auth.get('/auth/start')
    def start():
        if not config['enabled']:
            return redirect('/')
        next_path = request.args.get('next', '/')
        nonce = secrets.token_urlsafe(32)
        previous = session.pop('oidc_pending', {})
        if isinstance(previous, dict) and previous.get('state'):
            remote.framework.clear_state_data(session, previous['state'])
        try:
            response = remote.authorize_redirect(config['base_url'] + '/auth/callback', nonce=nonce, prompt='select_account')
            state = parse_qs(urlsplit(response.location).query)['state'][0]
            session['oidc_pending'] = {'state': state, 'nonce': nonce, 'started': time.time(),
                                       'next': next_path if next_path in PAGE_PATHS else '/'}
            session.permanent = True
            return response
        except Exception:
            app.logger.warning('Company login provider could not start authorization')
            return error_response('目前無法連線到公司登入服務，請稍後再試。', 502)

    @auth.get('/auth/callback')
    def callback():
        if not config['enabled']:
            return redirect('/')
        pending = session.get('oidc_pending', {})
        state = request.args.get('state', '')
        if (not isinstance(pending, dict) or not state or not pending.get('state')
                or not secrets.compare_digest(state, pending['state'])
                or time.time() - pending.get('started', 0) > 600):
            return error_response('登入驗證已失效，請重新開始登入。', 400)
        session.pop('oidc_pending', None)
        try:
            token = remote.authorize_access_token(leeway=0)
            claims = token.get('userinfo')
            if not token.get('id_token') or not claims or claims.get('nonce') != pending['nonce']:
                raise ValueError('Missing validated ID token or nonce')
            identity = authorized_identity(claims, config)
            expiry = min(time.time() + 3600, float(claims['exp']))
            if expiry <= time.time():
                raise ValueError('Expired ID token')
        except Exception:
            remote.framework.clear_state_data(session, state)
            # Never echo provider errors, tokens, authorization codes or secrets.
            app.logger.warning('Company login validation or authorization failed')
            return error_response('登入未通過驗證或帳號未獲授權，請聯絡管理員或重新登入。', 403)
        session.clear()
        session.permanent = True
        session.update(identity=identity, auth_expires=expiry, auth_policy=config['policy'], csrf_token=secrets.token_urlsafe(32))
        return redirect(pending['next'])

    @auth.post('/auth/logout')
    def logout():
        if not config['enabled']:
            return redirect('/')
        supplied = request.form.get('csrf_token', '')
        expected = session.get('csrf_token', '')
        origin = request.headers.get('Origin')
        if (not expected or not secrets.compare_digest(supplied, expected)
                or (origin is not None and origin != config['base_url'])):
            return error_response('無法驗證登出請求，請重新整理後再試。', 403)
        session.clear()
        return render_template('logged_out.html')

    app.register_blueprint(auth)
