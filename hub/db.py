"""Portable SQLAlchemy Core metadata shared by API and bounded workers."""
from __future__ import annotations
import time
import uuid
from sqlalchemy import (MetaData, Table, Column, String, Text, Integer, Float, Boolean,
                        JSON, Index, UniqueConstraint, create_engine, event, select)

metadata = MetaData()
def new_id(): return str(uuid.uuid4())
def now(): return time.time()
def ident(): return Column('id', String(100), primary_key=True)
def timestamp(name='created_at'): return Column(name, Float, nullable=False, default=now)

schema_versions = Table('schema_versions', metadata, Column('version', Integer, primary_key=True), timestamp())
users = Table('users', metadata, ident(), Column('username', String(100), unique=True, nullable=False), Column('display_name', String(200), nullable=False), Column('password_hash', Text), Column('role', String(20), nullable=False, default='member'), Column('active', Boolean, nullable=False, default=True), timestamp(), timestamp('updated_at'))
tokens = Table('tokens', metadata, ident(), Column('user_id', String(100), nullable=False, index=True), Column('token_hash', String(64), unique=True, nullable=False), Column('kind', String(20), nullable=False), Column('expires_at', Float, nullable=False), timestamp())
invites = Table('invites', metadata, ident(), Column('token_hash', String(64), unique=True, nullable=False), Column('created_by', String(100), nullable=False), Column('expires_at', Float, nullable=False), Column('used_by', String(100)), timestamp())
sessions = Table('sessions', metadata, ident(), Column('owner_id', String(100), nullable=False, index=True), Column('title', Text, nullable=False, default='未命名会话'), Column('original_title', Text, default=''), Column('project', Text, default=''), Column('source_kind', String(40), default='cursor_jsonl'), Column('native_id', String(300), default=''), Column('source_id', String(100)), Column('current_revision', String(100)), Column('status', String(30), default='discovered'), Column('sync_status', String(30), default='local_only'), Column('event_count', Integer, default=0), Column('round_count', Integer, default=0), Column('metadata_json', JSON, default=dict), Column('revoked', Boolean, nullable=False, default=False), timestamp(), timestamp('updated_at'))
revisions = Table('revisions', metadata, ident(), Column('session_id', String(100), nullable=False, index=True), Column('source_hash', String(64)), Column('source_size', Integer, default=0), Column('source_path', Text), Column('state', String(30), default='building'), Column('event_count', Integer, default=0), Column('round_count', Integer, default=0), Column('diagnostic_count', Integer, default=0), Column('metadata_json', JSON, default=dict), timestamp(), timestamp('updated_at'))
rounds = Table('rounds', metadata, ident(), Column('revision_id', String(100), nullable=False), Column('number', Integer, nullable=False), Column('start_seq', Integer, nullable=False), Column('end_seq', Integer, nullable=False), Column('preview', Text, default=''), Column('count', Integer, default=0), UniqueConstraint('revision_id','number'))
events = Table('events', metadata, ident(), Column('revision_id', String(100), nullable=False), Column('seq', Integer, nullable=False), Column('round_number', Integer, nullable=False), Column('kind', String(40), nullable=False), Column('event_json', JSON, nullable=False), Column('byte_size', Integer, default=0), UniqueConstraint('revision_id','seq'))
Index('ix_events_round_page', events.c.revision_id, events.c.round_number, events.c.seq)
contents = Table('contents', metadata, ident(), Column('revision_id', String(100), nullable=False, index=True), Column('event_id', String(100)), Column('kind', String(40), default='text'), Column('path', Text, nullable=False), Column('bytes', Integer, default=0), Column('sha256', String(64)), Column('metadata_json', JSON, default=dict))
assets = Table('assets', metadata, ident(), Column('revision_id', String(100), nullable=False, index=True), Column('event_id', String(100)), Column('path', Text), Column('bytes', Integer, default=0), Column('sha256', String(64)), Column('mime', String(100), default='application/octet-stream'), Column('name', Text, default=''), Column('status', String(30), default='ready'), Column('metadata_json', JSON, default=dict))
sources = Table('sources', metadata, ident(), Column('owner_id', String(100), default='local'), Column('path', Text, nullable=False), Column('native_id', String(300), default=''), Column('title', Text, default=''), Column('source_kind', String(40), nullable=False), Column('project', Text, default=''), Column('session_id', String(100)), Column('status', String(30), default='discovered'), Column('size', Integer, default=0), Column('mtime', Float, default=0), Column('metadata_json', JSON, default=dict), timestamp(), timestamp('updated_at'))
jobs = Table('jobs', metadata, ident(), Column('owner_id', String(100), nullable=False, index=True), Column('kind', String(40), nullable=False), Column('state', String(30), nullable=False, default='queued'), Column('session_id', String(100)), Column('revision_id', String(100)), Column('progress', Integer, default=0), Column('total', Integer, default=0), Column('error', Text), Column('payload_json', JSON, nullable=False, default=dict), Column('checkpoint_json', JSON, nullable=False, default=dict), Column('result_json', JSON, nullable=False, default=dict), Column('lease_owner', String(100)), Column('lease_until', Float), Column('cancel_requested', Boolean, nullable=False, default=False), timestamp(), timestamp('updated_at'))
Index('ix_jobs_queue', jobs.c.state, jobs.c.created_at)
search_tokens = Table('search_tokens', metadata, Column('session_id', String(100)), Column('revision_id', String(100), nullable=False), Column('event_id', String(100), nullable=False), Column('token', String(300), nullable=False), UniqueConstraint('revision_id','event_id','token'))
Index('ix_search_token', search_tokens.c.token, search_tokens.c.revision_id)
comments = Table('comments', metadata, ident(), Column('session_id', String(100), nullable=False, index=True), Column('revision_id', String(100), nullable=False), Column('event_id', String(100)), Column('owner_id', String(100), nullable=False), Column('text', Text, nullable=False), timestamp(), timestamp('updated_at'))
favorites = Table('favorites', metadata, ident(), Column('owner_id', String(100), nullable=False, index=True), Column('session_id', String(100), nullable=False), Column('revision_id', String(100)), Column('event_id', String(100)), timestamp())
uploads = Table('uploads', metadata, ident(), Column('owner_id', String(100), nullable=False, index=True), Column('filename', Text, nullable=False), Column('total_bytes', Integer, nullable=False), Column('sha256', String(64), nullable=False), Column('session_key', String(300), nullable=False), Column('device_id', String(300), nullable=False), Column('state', String(30), nullable=False, default='uploading'), Column('received_json', JSON, default=dict), Column('job_id', String(100)), Column('session_id', String(100)), timestamp(), timestamp('updated_at'))
syncs = Table('syncs', metadata, ident(), Column('owner_id', String(100), nullable=False, index=True), Column('device_id', String(300), default=''), Column('session_id', String(100)), Column('revision_id', String(100)), Column('upload_id', String(100)), Column('job_id', String(100)), Column('state', String(30), nullable=False), Column('error', Text), Column('metadata_json', JSON, default=dict), timestamp(), timestamp('updated_at'))
activity = Table('activity', metadata, Column('id', Integer, primary_key=True, autoincrement=True), Column('kind', String(40), nullable=False), Column('owner_id', String(100)), Column('session_id', String(100)), Column('payload_json', JSON, default=dict), timestamp())
leases = Table('leases', metadata, Column('name', String(100), primary_key=True), Column('owner', String(100), nullable=False), Column('expires_at', Float, nullable=False))
ai_settings = Table('ai_settings', metadata, Column('id', Integer, primary_key=True), Column('config_json', JSON, nullable=False, default=dict), Column('key_cipher', Text), timestamp('updated_at'))
ai_threads = Table('ai_threads', metadata, ident(), Column('owner_id', String(100), nullable=False, index=True), Column('title', Text, nullable=False), timestamp(), timestamp('updated_at'))
ai_messages = Table('ai_messages', metadata, ident(), Column('thread_id', String(100), nullable=False, index=True), Column('owner_id', String(100), nullable=False), Column('seq', Integer, nullable=False), Column('role', String(20), nullable=False), Column('text', Text, nullable=False, default=''), Column('state', String(20), nullable=False), Column('error', Text), Column('request_id', String(100)), Column('metadata_json', JSON, default=dict), timestamp(), timestamp('updated_at'), UniqueConstraint('thread_id','seq'), UniqueConstraint('thread_id','request_id'))

def get_engine(config):
    config.prepare()
    kwargs = {'pool_pre_ping':True}
    if config.database_url.startswith('sqlite:'):
        kwargs['connect_args'] = {'check_same_thread':False, 'timeout':30}
    engine = create_engine(config.database_url, **kwargs)
    if engine.dialect.name == 'sqlite':
        @event.listens_for(engine, 'connect')
        def setup(dbapi, _):
            dbapi.execute('PRAGMA journal_mode=WAL')
            dbapi.execute('PRAGMA busy_timeout=30000')
            dbapi.execute('PRAGMA foreign_keys=ON')
    return engine

def init_db(engine):
    metadata.create_all(engine)
    with engine.begin() as conn:
        if not conn.execute(select(schema_versions.c.version).where(schema_versions.c.version == 1)).first():
            conn.execute(schema_versions.insert().values(version=1))
        if not conn.execute(select(schema_versions.c.version).where(schema_versions.c.version == 2)).first():
            conn.execute(schema_versions.insert().values(version=2))
        if not conn.execute(select(users.c.id).where(users.c.id == 'local')).first():
            conn.execute(users.insert().values(id='local', username='__local__', display_name='我', role='admin', active=True))

def emit(conn, kind, owner_id=None, session_id=None, **payload):
    conn.execute(activity.insert().values(kind=kind, owner_id=owner_id, session_id=session_id, payload_json=payload))
