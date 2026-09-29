"""Private, versioned project documents. No database connection at import time."""
import json
import os
import uuid
from datetime import datetime, timezone

import click
from flask import Blueprint, g, jsonify, render_template, request
from sqlalchemy import (Column, ForeignKey, Integer, MetaData, String, Table, Text,
                        create_engine, insert, select, update)
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import NullPool

metadata = MetaData()
clients = Table('marketai_clients', metadata,
    Column('id', String(36), primary_key=True), Column('owner', String(64), nullable=False, index=True),
    Column('name', String(150), nullable=False), Column('created_at', String(40), nullable=False))
projects = Table('marketai_projects', metadata,
    Column('id', String(36), primary_key=True), Column('owner', String(64), nullable=False, index=True),
    Column('client_id', String(36), ForeignKey('marketai_clients.id'), nullable=False),
    Column('name', String(150), nullable=False), Column('created_at', String(40), nullable=False))
documents = Table('marketai_documents', metadata,
    Column('id', String(36), primary_key=True), Column('owner', String(64), nullable=False, index=True),
    Column('project_id', String(36), ForeignKey('marketai_projects.id'), nullable=False, index=True),
    Column('kind', String(30), nullable=False), Column('title', String(150), nullable=False),
    Column('version', Integer, nullable=False), Column('updated_at', String(40), nullable=False))
versions = Table('marketai_document_versions', metadata,
    Column('document_id', String(36), ForeignKey('marketai_documents.id'), primary_key=True),
    Column('version', Integer, primary_key=True), Column('title', String(150), nullable=False),
    Column('snapshot', Text, nullable=False), Column('created_at', String(40), nullable=False))
KINDS = {'meeting', 'ad-report', 'work-dispatch'}
SECRET_FIELDS = {'api_key', 'apikey', 'access_token', 'refresh_token', 'id_token', 'client_secret',
                 'csrf_token', 'password', 'deepseek_key', 'groq_key'}


def now():
    return datetime.now(timezone.utc).isoformat()


def title(value):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 150:
        raise ValueError('名稱須為 1–150 個字元。')
    return value.strip()


def validate_snapshot(kind, value):
    if not isinstance(kind, str) or kind not in KINDS or not isinstance(value, dict) or set(value) != {'input', 'result'}:
        raise ValueError('文件格式不正確。')
    if not isinstance(value['input'], dict) or (value['result'] is not None and not isinstance(value['result'], dict)):
        raise ValueError('輸入與結果必須為物件。')
    allowed = {
        'meeting': {'text', 'notes'},
        'ad-report': {'client_name', 'company_name', 'report_month', 'platforms', 'platform_metadata'},
        'work-dispatch': {'project_name', 'quotation_text', 'roles'},
    }
    if set(value['input']) - allowed[kind]:
        raise ValueError('文件包含不支援的輸入欄位。')
    def check(obj, depth=0):
        if depth > 25:
            raise ValueError('文件結構過深。')
        if isinstance(obj, dict):
            for key, item in obj.items():
                if key.lower().replace('-', '_') in SECRET_FIELDS:
                    raise ValueError('文件不可包含 API Key 或登入憑證欄位。')
                check(item, depth + 1)
        elif isinstance(obj, list):
            for item in obj:
                check(item, depth + 1)
    check(value)
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
    if len(encoded.encode()) > 2_000_000:
        raise ValueError('每份文件內容上限為 2 MB，請縮減文字後儲存。')
    return encoded


def init_project_library(app, environ=None):
    env = os.environ if environ is None else environ
    database_url = env.get('DATABASE_URL', '').strip()
    engine = None
    invalid = False
    if database_url:
        try:
            url = make_url(database_url)
            if url.drivername in ('postgres', 'postgresql', 'postgresql+psycopg'):
                url = url.set(drivername='postgresql+psycopg')
                engine = create_engine(url, poolclass=NullPool, connect_args={'connect_timeout': 10})
            elif url.drivername == 'sqlite' and env.get('VERCEL') != '1' and url.database not in (None, '', ':memory:'):
                engine = create_engine(url, connect_args={'timeout': 10})
            else:
                invalid = True
        except Exception:
            invalid = True
    app.extensions['project_library_engine'] = engine
    library = Blueprint('library', __name__)

    @app.context_processor
    def library_context():
        auth = app.extensions['company_auth_config']
        return {'library_available': bool(engine is not None and auth['enabled'] and not auth['error'])}

    @app.cli.command('library-init')
    def initialize():
        """Create additive v1 tables in the configured database (never drops data)."""
        if engine is None:
            raise click.ClickException('請設定有效 DATABASE_URL；Vercel 必須使用 PostgreSQL。')
        try:
            metadata.create_all(engine)
        except SQLAlchemyError:
            raise click.ClickException('資料表初始化失敗，請檢查連線及建表權限。') from None
        click.echo('MarketAI 專案資料表 v1 已就緒。')

    @library.before_request
    def protect_library():
        # Public tool mode must NEVER expose a persistent shared company archive.
        if request.path.startswith('/api/library/'):
            auth = app.extensions['company_auth_config']
            if not auth['enabled'] or not getattr(g, 'company_user', None):
                return jsonify(error='請先啟用公司登入，才能使用私人專案資料庫。'), 403
            if engine is None or invalid:
                return jsonify(error='專案資料庫尚未設定，請聯絡管理員。'), 503

    @library.errorhandler(ValueError)
    def invalid_input(exc):
        return jsonify(error=str(exc)), 400

    @library.errorhandler(SQLAlchemyError)
    def database_failure(exc):
        app.logger.warning('Project library database operation failed')
        return jsonify(error='資料庫暫時無法使用；內容尚未儲存，請保留頁面稍後重試。'), 503

    @library.get('/library')
    def library_page():
        return render_template('library.html', active_page='library')

    def owner():
        return g.company_user['id']

    def body():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise ValueError('請提供 JSON 物件。')
        return data

    def owned(conn, table, identity):
        if not isinstance(identity, str):
            raise ValueError('資料識別碼不正確。')
        return conn.execute(select(table).where(table.c.id == identity, table.c.owner == owner())).mappings().first()

    def missing():
        return jsonify(error='找不到文件或沒有存取權限。'), 404

    def page_offset():
        try:
            offset = int(request.args.get('offset', 0))
        except ValueError:
            raise ValueError('分頁參數不正確。')
        if offset < 0 or offset > 1_000_000:
            raise ValueError('分頁參數不正確。')
        return offset

    @library.route('/api/library/clients', methods=['GET', 'POST'])
    def client_list():
        with engine.begin() as conn:
            if request.method == 'POST':
                row = {'id': str(uuid.uuid4()), 'owner': owner(), 'name': title(body().get('name')), 'created_at': now()}
                conn.execute(insert(clients).values(**row))
                return jsonify(id=row['id'], name=row['name']), 201
            rows = conn.execute(select(clients.c.id, clients.c.name).where(clients.c.owner == owner())
                                .order_by(clients.c.created_at, clients.c.id).limit(100).offset(page_offset())).mappings()
            return jsonify(items=[dict(r) for r in rows])

    @library.route('/api/library/projects', methods=['GET', 'POST'])
    def project_list():
        with engine.begin() as conn:
            if request.method == 'POST':
                data = body()
                if not owned(conn, clients, data.get('client_id')):
                    return missing()
                row = {'id': str(uuid.uuid4()), 'owner': owner(), 'client_id': data['client_id'],
                       'name': title(data.get('name')), 'created_at': now()}
                conn.execute(insert(projects).values(**row))
                return jsonify(id=row['id'], name=row['name']), 201
            query = select(projects.c.id, projects.c.name, projects.c.client_id, clients.c.name.label('client_name')).join(clients)
            query = query.where(projects.c.owner == owner(), clients.c.owner == owner())
            if request.args.get('client_id'):
                query = query.where(projects.c.client_id == request.args['client_id'])
            rows = conn.execute(query.order_by(projects.c.created_at, projects.c.id).limit(100).offset(page_offset())).mappings()
            return jsonify(items=[dict(r) for r in rows])

    @library.route('/api/library/documents', methods=['GET', 'POST'])
    def document_list():
        with engine.begin() as conn:
            if request.method == 'POST':
                data = body()
                if not owned(conn, projects, data.get('project_id')):
                    return missing()
                encoded = validate_snapshot(data.get('kind'), data.get('snapshot'))
                row = {'id': str(uuid.uuid4()), 'owner': owner(), 'project_id': data['project_id'],
                       'kind': data['kind'], 'title': title(data.get('title')), 'version': 1, 'updated_at': now()}
                conn.execute(insert(documents).values(**row))
                conn.execute(insert(versions).values(document_id=row['id'], version=1, title=row['title'], snapshot=encoded, created_at=row['updated_at']))
                return jsonify(id=row['id'], version=1), 201
            query = select(documents).where(documents.c.owner == owner())
            if request.args.get('project_id'):
                query = query.where(documents.c.project_id == request.args['project_id'])
            if request.args.get('q'):
                query = query.where(documents.c.title.icontains(request.args['q'][:150], autoescape=True))
            rows = conn.execute(query.order_by(documents.c.updated_at.desc(), documents.c.id).limit(100).offset(page_offset())).mappings()
            return jsonify(items=[{k: v for k, v in dict(r).items() if k != 'owner'} for r in rows])

    @library.route('/api/library/documents/<identity>', methods=['GET', 'PUT'])
    def document(identity):
        with engine.begin() as conn:
            row = owned(conn, documents, identity)
            if not row:
                return missing()
            if request.method == 'PUT':
                data = body()
                expected = data.get('expected_version')
                if type(expected) is not int or expected < 1:
                    raise ValueError('儲存時必須提供原始版本號。')
                encoded = validate_snapshot(row['kind'], data.get('snapshot'))
                name, timestamp = title(data.get('title')), now()
                updated = conn.execute(update(documents).where(documents.c.id == identity, documents.c.owner == owner(),
                    documents.c.version == expected).values(title=name, version=expected + 1, updated_at=timestamp))
                if updated.rowcount != 1:
                    return jsonify(error='其他分頁已儲存新版本。請保留目前內容，另開歷史紀錄核對；不會覆蓋新版。'), 409
                conn.execute(insert(versions).values(document_id=identity, version=expected + 1,
                             title=name, snapshot=encoded, created_at=timestamp))
                return jsonify(id=identity, version=expected + 1)
            number = request.args.get('version', str(row['version']))
            if not number.isdigit():
                raise ValueError('版本號不正確。')
            version = conn.execute(select(versions).where(versions.c.document_id == identity, versions.c.version == int(number))).mappings().first()
            if not version:
                return missing()
            return jsonify(id=identity, kind=row['kind'], project_id=row['project_id'], title=version['title'],
                           version=version['version'], latest_version=row['version'], snapshot=json.loads(version['snapshot']),
                           updated_at=version['created_at'])

    @library.get('/api/library/documents/<identity>/versions')
    def version_list(identity):
        with engine.connect() as conn:
            if not owned(conn, documents, identity):
                return missing()
            rows = conn.execute(select(versions.c.version, versions.c.title, versions.c.created_at)
                .where(versions.c.document_id == identity).order_by(versions.c.version.desc()).limit(100).offset(page_offset())).mappings()
            return jsonify(items=[dict(r) for r in rows])

    app.register_blueprint(library)
