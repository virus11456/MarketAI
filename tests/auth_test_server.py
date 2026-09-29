"""Local browser-test fixture only. Never imported by the production app.

Creates a signed, one-hour fixture session to exercise UI/CSRF/storage. Separate
unit tests validate OIDC state, nonce and actual RSA-signed provider tokens.
"""
import json
import os
import secrets
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.update(AUTH_MODE='google', AUTH_BASE_URL='http://127.0.0.1:5057',
                  AUTH_GOOGLE_DOMAINS='test.invalid', OIDC_CLIENT_ID='browser-test-client',
                  OIDC_CLIENT_SECRET='browser-test-secret', FLASK_SECRET_KEY=secrets.token_urlsafe(48), VERCEL='0')
from app import app

csrf = secrets.token_urlsafe(32)
fixture = {'_permanent': True, 'identity': {'id': 'browser-test-user', 'name': '測試同事'},
           'auth_expires': time.time() + 3600,
           'auth_policy': app.extensions['company_auth_config']['policy'], 'csrf_token': csrf}
cookie = app.session_interface.get_signing_serializer(app).dumps(fixture)
print(json.dumps({'cookie': cookie, 'csrf': csrf}), flush=True)
app.run(host='127.0.0.1', port=5057, debug=False, use_reloader=False)
