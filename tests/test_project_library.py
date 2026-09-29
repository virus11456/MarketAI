import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from flask import Flask
from sqlalchemy import select
from project_library import init_project_library, metadata, versions
from company_auth import init_company_auth
from test_company_auth import ENV, ROOT


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database_url = os.environ.get('TEST_DATABASE_URL') or 'sqlite:///' + str(Path(self.temp.name) / 'test.sqlite3')
        self.app = self.make_app()
        self.engine = self.app.extensions['project_library_engine']
        metadata.create_all(self.engine)
        self.client = self.login('owner-a')
        self.other = self.login('owner-b')
        self.headers = {'X-CSRF-Token': 'fixture-csrf'}

    def tearDown(self):
        metadata.drop_all(self.engine)  # Dedicated TEST_DATABASE_URL only; never production DATABASE_URL.
        self.engine.dispose()
        self.temp.cleanup()

    def make_app(self, env=None):
        app = Flask(__name__, template_folder=str(ROOT / 'templates'), static_folder=str(ROOT / 'static'))
        app.testing = True
        app.secret_key = 'test-only'
        config = {**ENV, 'DATABASE_URL': self.database_url} if env is None else env
        init_company_auth(app, config)
        init_project_library(app, config)
        return app

    def login(self, identity, app=None):
        app = app or self.app
        client = app.test_client()
        with client.session_transaction() as session:
            session.update(identity={'id': identity, 'name': identity}, auth_expires=time.time()+3600,
                           auth_policy=app.extensions['company_auth_config'].get('policy'), csrf_token='fixture-csrf')
        return client

    def post(self, path, data, client=None):
        return (client or self.client).post('/api/library/' + path, json=data, headers=self.headers)

    def setup_document(self, kind='meeting', snapshot=None):
        customer = self.post('clients', {'name': '測試客戶'}).json['id']
        project = self.post('projects', {'client_id': customer, 'name': '專案'}).json['id']
        data = {'project_id': project, 'kind': kind, 'title': '初稿',
                'snapshot': snapshot or {'input': {'text': '逐字稿'}, 'result': {'notes': '原始記錄'}}}
        response = self.post('documents', data)
        self.assertEqual(response.status_code, 201)
        return customer, project, response.json['id'], data

    def test_requires_login_and_csrf(self):
        self.assertEqual(self.app.test_client().get('/api/library/clients').status_code, 401)
        self.assertEqual(self.client.post('/api/library/clients', json={'name': 'x'}).status_code, 403)
        self.assertEqual(self.post('clients', {'name': '客戶'}).status_code, 201)

    def test_disabled_auth_never_exposes_database(self):
        app = self.make_app({'AUTH_MODE': 'disabled', 'DATABASE_URL': self.database_url})
        self.assertEqual(app.test_client().get('/api/library/clients').status_code, 403)
        self.assertIn('尚未啟用', app.test_client().get('/library').text)
        app.extensions['project_library_engine'].dispose()

    def test_missing_database_and_vercel_sqlite_fail_closed(self):
        for url in ['', 'sqlite:////tmp/ephemeral.sqlite3', 'not-a-url']:
            app = self.make_app({**ENV, 'DATABASE_URL': url, 'VERCEL': '1'})
            self.assertIsNone(app.extensions['project_library_engine'])
            self.assertEqual(self.login('owner-a', app).get('/api/library/clients').status_code, 503)

    def test_owner_isolation_on_every_resource(self):
        customer, project, doc, data = self.setup_document()
        for path in ['clients', 'projects', 'documents', 'documents?project_id='+project]:
            self.assertEqual(self.other.get('/api/library/'+path).json['items'], [])
        for path in ['documents/'+doc, 'documents/'+doc+'/versions', 'documents/'+doc+'?version=1']:
            self.assertEqual(self.other.get('/api/library/'+path).status_code, 404)
        self.assertEqual(self.post('projects', {'client_id': customer, 'name': 'stolen'}, self.other).status_code, 404)
        self.assertEqual(self.post('documents', data, self.other).status_code, 404)
        self.assertEqual(self.other.put('/api/library/documents/'+doc, json={**data, 'expected_version':1}, headers=self.headers).status_code, 404)
        # Supplied owner is ignored; identity always comes from validated session.
        self.post('clients', {'name':'mine', 'owner':'owner-b'})
        self.assertEqual(self.other.get('/api/library/clients').json['items'], [])

    def test_version_history_conflict_and_old_version_restore(self):
        _, _, doc, data = self.setup_document()
        data['title'] = '修正版'
        data['snapshot']['result']['notes'] = '新記錄'
        response = self.client.put('/api/library/documents/'+doc, json={**data, 'expected_version':1}, headers=self.headers)
        self.assertEqual(response.json['version'], 2)
        stale = self.client.put('/api/library/documents/'+doc, json={**data, 'expected_version':1}, headers=self.headers)
        self.assertEqual(stale.status_code, 409)
        old = self.client.get('/api/library/documents/'+doc+'?version=1').json
        self.assertEqual(old['snapshot']['result']['notes'], '原始記錄')
        self.assertEqual(old['latest_version'], 2)
        restored = self.client.put('/api/library/documents/'+doc, json={**old, 'expected_version':2}, headers=self.headers)
        self.assertEqual(restored.json['version'], 3)
        self.assertEqual(len(self.client.get('/api/library/documents/'+doc+'/versions').json['items']), 3)

    def test_survives_new_application_instance(self):
        _, _, doc, _ = self.setup_document()
        app = self.make_app()
        response = self.login('owner-a', app).get('/api/library/documents/'+doc)
        self.assertEqual(response.json['snapshot']['input']['text'], '逐字稿')
        app.extensions['project_library_engine'].dispose()

    def test_secret_and_oversized_payload_rejected_without_new_version(self):
        _, _, doc, data = self.setup_document()
        for snapshot in [{'input': {'text': 'x', 'api_key':'secret'}, 'result':None},
                         {'input': {'text':'x'}, 'result':{'nested': [{'access_token':'secret'}]}},
                         {'input': {'text':'字'*700000}, 'result':None}]:
            response = self.client.put('/api/library/documents/'+doc, json={**data, 'snapshot':snapshot, 'expected_version':1}, headers=self.headers)
            self.assertEqual(response.status_code, 400)
        with self.engine.connect() as conn:
            stored = conn.execute(select(versions.c.snapshot)).scalars().all()
            self.assertEqual(len(stored), 1)
            self.assertNotIn('secret', stored[0])

    def test_search_treats_wildcards_as_text_and_paginates(self):
        _, _, doc, data = self.setup_document()
        data['title'] = '100%_成效'
        self.post('documents', data)
        self.assertEqual(len(self.client.get('/api/library/documents?q=%25_').json['items']), 1)
        self.assertEqual(len(self.client.get('/api/library/documents?offset=1').json['items']), 1)
        self.assertEqual(self.client.get('/api/library/documents?offset=-1').status_code, 400)

    def test_three_document_kinds_and_drafts(self):
        for kind, fields in [('meeting', {'text':'會議'}), ('ad-report', {'platforms':{'meta':'花費：10'}}),
                             ('work-dispatch', {'quotation_text':'提案', 'roles':[]})]:
            _, _, doc, _ = self.setup_document(kind, {'input':fields, 'result':None})
            self.assertIsNone(self.client.get('/api/library/documents/'+doc).json['snapshot']['result'])

    def test_database_failure_redacts_connection_details(self):
        metadata.drop_all(self.engine)
        response = self.client.get('/api/library/clients')
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('SELECT', response.text)
        self.assertNotIn(self.database_url, response.text)

    def test_initialization_is_repeatable_and_preserves_data(self):
        self.setup_document()
        for _ in range(2):
            result = self.app.test_cli_runner().invoke(args=['library-init'])
            self.assertEqual(result.exit_code, 0)
        self.assertEqual(len(self.client.get('/api/library/documents').json['items']), 1)

    def test_invalid_schema_and_identifiers(self):
        customer, project, _, data = self.setup_document()
        for kind in [[], None, 'bad']:
            self.assertEqual(self.post('documents', {**data, 'kind':kind}).status_code, 400)
        self.assertEqual(self.post('projects', {'name':'x','client_id':[]}).status_code, 400)
        self.assertEqual(self.post('clients', {'name':' '}).status_code, 400)
        self.assertEqual(self.post('clients', {'name':'x'*151}).status_code, 400)
