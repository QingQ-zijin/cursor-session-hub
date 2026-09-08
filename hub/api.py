"""Indexed, bounded API shared by the desktop sidecar and team web server."""
from __future__ import annotations
import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
import math
from pathlib import Path
import re
import secrets
import shutil
import threading
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response, UploadFile, File
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import select, func, and_, or_, delete, update
from .config import Config
from . import db, auth

COOKIE = 'csh_session'
ACTIVE_JOBS = ('queued', 'running', 'paused')
TERMINAL_JOBS = ('failed', 'cancelled', 'succeeded')

class Login(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=1024)

class Register(Login):
    token: str = Field(min_length=20, max_length=200)
    display_name: str = Field(min_length=1, max_length=200)

class Title(BaseModel):
    title: str = Field(min_length=1, max_length=1000)

class Comment(BaseModel):
    text: str = Field(min_length=1, max_length=20000)
    revision_id: str
    event_id: str | None = None

class CommentEdit(BaseModel):
    text: str = Field(min_length=1, max_length=20000)

class MemberEdit(BaseModel):
    active: bool | None = None
    role: str | None = None
    display_name: str | None = Field(default=None, min_length=1, max_length=200)

class Favorite(BaseModel):
    session_id: str
    revision_id: str | None = None
    event_id: str | None = None

class Export(BaseModel):
    format: str = 'html'
    revision_id: str | None = None

class PathImport(BaseModel):
    path: str = Field(min_length=1, max_length=32768)

class UploadStart(BaseModel):
    filename: str = Field(min_length=1, max_length=300)
    total_bytes: int = Field(gt=0, le=512 * 1024**2)
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    session_key: str = Field(min_length=1, max_length=300)
    device_id: str = Field(min_length=1, max_length=300)

def page(rows, limit):
    has_next = len(rows) > limit
    rows = rows[:limit]
    return {'items': [dict(row) for row in rows], 'next_cursor': str(rows[-1]['id']) if has_next and rows else None}

def bounded_limit(value, maximum):
    if value < 1:
        raise HTTPException(422, 'limit 必须大于 0')
    return min(value, maximum)

def safe_path(config, value):
    path = Path(value)
    if not path.is_absolute():
        path = config.home / path
    path = path.resolve()
    if not path.is_relative_to(config.home):
        raise HTTPException(404, '内容文件不可用')
    return path

def check_disk(config):
    usage = shutil.disk_usage(config.home)
    if usage.free < config.min_free_bytes or usage.free / usage.total < config.min_free_ratio:
        raise HTTPException(507, '磁盘空间不足，已暂停新上传')

def job_public(row):
    return {key: row.get(key) for key in ('id', 'kind', 'state', 'progress', 'total', 'error', 'session_id', 'revision_id', 'created_at', 'updated_at')}

def enqueue(conn, config, owner_id, kind, session_id=None, payload=None):
    count = conn.execute(select(func.count()).select_from(db.jobs).where(db.jobs.c.state.in_(ACTIVE_JOBS))).scalar_one()
    own = conn.execute(select(func.count()).select_from(db.jobs).where(db.jobs.c.state.in_(ACTIVE_JOBS), db.jobs.c.owner_id == owner_id)).scalar_one()
    if count >= config.max_queue or own >= config.max_user_queue:
        raise HTTPException(429, '后台队列已满，请等待现有任务完成')
    ident = db.new_id()
    conn.execute(db.jobs.insert().values(id=ident, owner_id=owner_id, kind=kind, session_id=session_id, state='queued', payload_json=payload or {}))
    db.emit(conn, 'job', owner_id, session_id, job_id=ident, state='queued')
    return dict(conn.execute(select(db.jobs).where(db.jobs.c.id == ident)).mappings().one())

def create_app(config: Config | None = None):
    config = config or Config()
    if config.mode == 'local' and not config.local_token:
        config.local_token = secrets.token_urlsafe(48)
    engine = db.get_engine(config)
    db.init_db(engine)
    if config.mode=='local':
        from .migration import migrate_legacy
        migrate_legacy(config,engine)
    admission_lock = threading.RLock()
    engine._csh_admission_lock = admission_lock
    login_attempts = {}

    @asynccontextmanager
    async def lifespan(app):
        worker = None
        if not config.worker_external:
            from .worker import start_worker
            worker = start_worker(config)
        yield
        if worker and hasattr(worker, 'stop'):
            worker.stop()
            if hasattr(worker, 'join'):
                await asyncio.to_thread(worker.join,10)
        engine.dispose()

    app = FastAPI(title='Cursor Session Hub', version='0.1.1', lifespan=lifespan)
    # Register before the HTTP decorator below so this sits directly around
    # routing. Receive-limit exceptions then reach FastAPI without being
    # wrapped in BaseHTTPMiddleware's request-relay task groups.
    from .body_limits import StreamBodyLimitMiddleware
    app.add_middleware(StreamBodyLimitMiddleware,config=config)
    app.state.config = config
    app.state.engine = engine
    @app.exception_handler(RequestValidationError)
    async def validation_error(request,exc):
        details=[]
        for error in exc.errors()[:5]:
            location='.'.join(str(x) for x in error.get('loc',()))
            details.append(location+': '+error.get('msg','无效参数'))
        return JSONResponse(status_code=422,content={'detail':'; '.join(details)})
    local_origins = ['tauri://localhost', 'http://tauri.localhost', 'https://tauri.localhost', 'http://localhost:5173', 'http://127.0.0.1:5173']
    if config.mode == 'local':
        app.add_middleware(CORSMiddleware, allow_origins=local_origins, allow_credentials=False, allow_methods=['GET','POST','PUT','PATCH','DELETE'], allow_headers=['Authorization','Content-Type','X-CSH-Token','X-Chunk-SHA256','Last-Event-ID'])

    @app.middleware('http')
    async def protect_origins(request, call_next):
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            origin = request.headers.get('origin')
            expected = config.public_url.rstrip('/') or str(request.base_url).rstrip('/')
            if origin and origin.rstrip('/') != expected and not (config.mode == 'local' and origin in local_origins):
                return Response(content='{"detail":"请求来源不受信任"}', status_code=403, media_type='application/json')
            if request.cookies.get(COOKIE) and not origin and request.headers.get('sec-fetch-site') == 'cross-site':
                return Response(content='{"detail":"请求来源不受信任"}', status_code=403, media_type='application/json')
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Cache-Control'] = 'no-store'
        return response

    def require_user(request: Request):
        bearer = request.headers.get('authorization', '')
        raw = bearer[7:] if bearer.lower().startswith('bearer ') else ''
        if config.mode == 'local':
            raw = request.headers.get('x-csh-token', '') or raw
            if not raw or not secrets.compare_digest(raw, config.local_token):
                raise HTTPException(401, '需要本地应用访问凭证')
            with engine.connect() as conn:
                return dict(conn.execute(select(db.users).where(db.users.c.id == 'local')).mappings().one())
        with engine.connect() as conn:
            row = auth.lookup_token(conn, raw or request.cookies.get(COOKIE, ''))
            if row:
                return dict(row)
        raise HTTPException(401, '请先登录')

    app.state.require_user = require_user
    router = APIRouter(prefix='/api/v1')

    def admin(user):
        if user['role'] != 'admin':
            raise HTTPException(403, '需要管理员权限')

    def local_only():
        if config.mode != 'local':
            raise HTTPException(404, '此功能仅可在本地客户端使用')

    def session_row(conn, session_id, user, writable=False):
        row = conn.execute(select(db.sessions).where(db.sessions.c.id == session_id, db.sessions.c.revoked.is_(False))).mappings().first()
        if not row:
            raise HTTPException(404, '会话不存在或已撤销共享')
        if writable and user['id'] != row['owner_id'] and user['role'] != 'admin':
            raise HTTPException(403, '只能修改自己共享的会话')
        return row

    def revision_row(conn, session_id, revision_id, user):
        session = session_row(conn, session_id, user)
        revision_id = revision_id or session['current_revision']
        if not revision_id:
            raise HTTPException(409, '会话正在等待解析')
        row = conn.execute(select(db.revisions).where(db.revisions.c.id == revision_id, db.revisions.c.session_id == session_id, db.revisions.c.state.in_(('complete', 'ready', 'ready_with_diagnostics', 'succeeded')))).mappings().first()
        if not row:
            raise HTTPException(404, '会话版本不可用')
        return row

    def anchor(conn, session_id, revision_id, event_id, user):
        rev = revision_row(conn, session_id, revision_id, user)
        if event_id and not conn.execute(select(db.events.c.id).where(db.events.c.id == event_id, db.events.c.revision_id == rev['id'])).first():
            raise HTTPException(422, '消息不属于指定会话版本')
        return rev['id']

    def session_view(conn, row, user):
        item = dict(row)
        item.pop('metadata_json', None)
        if config.mode=='cloud':
            item.pop('source_id', None)
        item['owner_name'] = conn.execute(select(db.users.c.display_name).where(db.users.c.id == row['owner_id'])).scalar() or ''
        item['comment_count'] = conn.execute(select(func.count()).select_from(db.comments).where(db.comments.c.session_id == row['id'])).scalar_one()
        item['favorite'] = bool(conn.execute(select(db.favorites.c.id).where(db.favorites.c.owner_id == user['id'], db.favorites.c.session_id == row['id'])).first())
        return item

    @router.get('/capabilities')
    def capabilities(request: Request):
        user = None
        try:
            user = auth.public_user(require_user(request))
        except HTTPException:
            pass
        return {'mode': config.mode, 'local_import': config.mode == 'local', 'local_sources': config.mode == 'local', 'server_sync': config.mode == 'local', 'comments': True, 'favorites': True, 'user': user}

    @router.get('/auth/me')
    def me(user=Depends(require_user)):
        return auth.public_user(user)

    def do_login(body, request, response, kind):
        if config.mode != 'cloud':
            raise HTTPException(404, '本地模式无需账号')
        peer = request.client.host if request.client else 'unknown'
        key = (peer, body.username.strip().lower())
        stamp = db.now()
        for old in list(login_attempts):
            if login_attempts[old][1] < stamp - 900:
                del login_attempts[old]
        attempts, started = login_attempts.get(key, (0, stamp))
        if attempts >= 10:
            raise HTTPException(429, '登录尝试过多，请 15 分钟后重试')
        login_attempts[key] = (attempts + 1, started)
        with engine.begin() as conn:
            user = conn.execute(select(db.users).where(db.users.c.username == key[1], db.users.c.id != 'local', db.users.c.active.is_(True))).mappings().first()
            if not user or not auth.verify_password(body.password, user['password_hash']):
                raise HTTPException(401, '用户名或密码错误')
            raw = auth.issue_token(conn, user['id'], kind)
            result = {'user': auth.public_user(user)}
        login_attempts.pop(key, None)
        if kind == 'device':
            result['token'] = raw
        else:
            response.set_cookie(COOKIE, raw, httponly=True, secure=config.cookie_secure, samesite='strict', max_age=7 * 86400, path='/api/v1')
        return result

    @router.post('/auth/login')
    def login(body: Login, request: Request, response: Response):
        return do_login(body, request, response, 'browser')

    @router.post('/auth/device-login')
    def device_login(body: Login, request: Request, response: Response):
        return do_login(body, request, response, 'device')

    @router.post('/auth/logout')
    def logout(request: Request, response: Response, user=Depends(require_user)):
        bearer = request.headers.get('authorization', '')
        raw = bearer[7:] if bearer.lower().startswith('bearer ') else request.cookies.get(COOKIE, '')
        if config.mode == 'cloud' and raw:
            with engine.begin() as conn:
                conn.execute(delete(db.tokens).where(db.tokens.c.token_hash == auth.digest(raw)))
        response.delete_cookie(COOKIE, path='/api/v1')
        return {'ok': True}

    @router.post('/auth/register')
    def register(body: Register, response: Response):
        if config.mode != 'cloud':
            raise HTTPException(404, '本地模式无需账号')
        if len(body.password) < 12:
            raise HTTPException(422, '密码至少需要 12 个字符')
        username = body.username.strip().lower()
        if not re.fullmatch(r'[a-z0-9_.-]{2,100}', username):
            raise HTTPException(422, '用户名使用 2–100 位字母、数字、下划线、点或短横线')
        with admission_lock, engine.begin() as conn:
            invite = conn.execute(select(db.invites).where(db.invites.c.token_hash == auth.digest(body.token), db.invites.c.used_by.is_(None), db.invites.c.expires_at > db.now()).with_for_update()).mappings().first()
            if not invite:
                raise HTTPException(410, '邀请已使用、过期或撤销')
            if conn.execute(select(db.users.c.id).where(db.users.c.username == username)).first():
                raise HTTPException(409, '用户名已存在')
            uid = db.new_id()
            conn.execute(db.users.insert().values(id=uid, username=username, display_name=body.display_name, role='member', active=True, password_hash=auth.hasher.hash(body.password)))
            conn.execute(update(db.invites).where(db.invites.c.id == invite['id']).values(used_by=uid))
            raw = auth.issue_token(conn, uid)
            user = conn.execute(select(db.users).where(db.users.c.id == uid)).mappings().one()
        response.set_cookie(COOKIE, raw, httponly=True, secure=config.cookie_secure, samesite='strict', max_age=7 * 86400, path='/api/v1')
        return {'user': auth.public_user(user)}

    @router.get('/members')
    def members(user=Depends(require_user), cursor: str | None = None, limit: int = 50):
        limit = bounded_limit(limit, 100)
        query = select(db.users).order_by(db.users.c.id).limit(limit + 1)
        query = query.where(db.users.c.id == 'local') if config.mode == 'local' else query.where(db.users.c.id != 'local')
        if cursor:
            query = query.where(db.users.c.id > cursor)
        with engine.connect() as conn:
            rows = [auth.public_user(x) for x in conn.execute(query).mappings()]
        return page(rows, limit)

    @router.patch('/members/{ident}')
    def edit_member(ident: str, body: MemberEdit, user=Depends(require_user)):
        admin(user)
        if ident == 'local':
            raise HTTPException(403, '离线账号不可修改')
        values = {k:v for k,v in body.model_dump().items() if v is not None}
        if 'role' in values and values['role'] not in ('admin', 'member'):
            raise HTTPException(422, '未知角色')
        with admission_lock, engine.begin() as conn:
            target = conn.execute(select(db.users).where(db.users.c.id == ident)).mappings().first()
            if not target:
                raise HTTPException(404, '成员不存在')
            if target['role'] == 'admin' and target['active'] and (values.get('active') is False or values.get('role') == 'member'):
                n = conn.execute(select(func.count()).select_from(db.users).where(db.users.c.role == 'admin', db.users.c.active.is_(True), db.users.c.id != 'local')).scalar_one()
                if n <= 1:
                    raise HTTPException(409, '必须保留至少一名有效管理员')
            conn.execute(update(db.users).where(db.users.c.id == ident).values(**values, updated_at=db.now()))
            if values.get('active') is False:
                auth.revoke_user(conn, ident)
            return auth.public_user(conn.execute(select(db.users).where(db.users.c.id == ident)).mappings().one())

    @router.post('/invites')
    def create_invite(user=Depends(require_user)):
        admin(user)
        raw, ident, expiry = secrets.token_urlsafe(32), db.new_id(), db.now() + 7 * 86400
        with engine.begin() as conn:
            conn.execute(db.invites.insert().values(id=ident, token_hash=auth.digest(raw), created_by=user['id'], expires_at=expiry))
        return {'id': ident, 'token': raw, 'expires_at': expiry}

    @router.get('/invites')
    def list_invites(user=Depends(require_user)):
        admin(user)
        with engine.connect() as conn:
            rows = conn.execute(select(db.invites.c.id, db.invites.c.created_at, db.invites.c.expires_at, db.invites.c.used_by).order_by(db.invites.c.created_at.desc()).limit(100)).mappings().all()
        return {'items':[dict(x) for x in rows], 'next_cursor':None}

    @router.delete('/invites/{ident}')
    def revoke_invite(ident: str, user=Depends(require_user)):
        admin(user)
        with engine.begin() as conn:
            conn.execute(delete(db.invites).where(db.invites.c.id == ident))
        return {'ok':True}

    @router.get('/sessions')
    def list_sessions(user=Depends(require_user), cursor: str | None=None, limit:int=50, owner_id:str|None=None, project:str|None=None, q:str|None=None, favorite:bool=False, date_from:float|None=None, date_to:float|None=None):
        limit=bounded_limit(limit,50)
        query=select(db.sessions).where(db.sessions.c.revoked.is_(False)).order_by(db.sessions.c.updated_at.desc(),db.sessions.c.id.desc()).limit(limit+1)
        if owner_id: query=query.where(db.sessions.c.owner_id==owner_id)
        if project: query=query.where(db.sessions.c.project==project)
        if date_from is not None: query=query.where(db.sessions.c.updated_at>=date_from)
        if date_to is not None: query=query.where(db.sessions.c.updated_at<=date_to)
        if favorite: query=query.where(db.sessions.c.id.in_(select(db.favorites.c.session_id).where(db.favorites.c.owner_id==user['id'])))
        if q:
            if len(q)>200: raise HTTPException(422,'搜索词过长')
            # Index exact tokens generated by the ingest worker; no raw file reads.
            words=re.findall(r'[a-z0-9_]+|[\u4e00-\u9fff]',q.lower())[:20]
            for word in words:
                query=query.where(or_(db.sessions.c.title.ilike('%'+word.replace('%','').replace('_','\\_')+'%'),db.sessions.c.current_revision.in_(select(db.search_tokens.c.revision_id).where(db.search_tokens.c.token==word))))
        with engine.connect() as conn:
            if cursor:
                at=conn.execute(select(db.sessions.c.updated_at,db.sessions.c.id).where(db.sessions.c.id==cursor)).first()
                if not at: raise HTTPException(422,'无效分页游标')
                query=query.where(or_(db.sessions.c.updated_at<at.updated_at,and_(db.sessions.c.updated_at==at.updated_at,db.sessions.c.id<at.id)))
            rows=conn.execute(query).mappings().all()
            result=page(rows,limit)
            result['items']=[session_view(conn,row,user) for row in rows[:limit]]
            return result

    @router.get('/sessions/{ident}')
    def get_session(ident:str,user=Depends(require_user)):
        with engine.connect() as conn:
            return session_view(conn,session_row(conn,ident,user),user)

    @router.patch('/sessions/{ident}')
    def rename_session(ident:str,body:Title,user=Depends(require_user)):
        if not body.title.strip(): raise HTTPException(422,'标题不能为空')
        with engine.begin() as conn:
            session=session_row(conn,ident,user,True)
            metadata=dict(session['metadata_json'] or {});metadata['custom_title']=body.title.strip()
            conn.execute(update(db.sessions).where(db.sessions.c.id==ident).values(title=body.title.strip(),metadata_json=metadata,updated_at=db.now()))
            db.emit(conn,'session',user['id'],ident)
            return session_view(conn,session_row(conn,ident,user),user)

    @router.delete('/sessions/{ident}')
    def revoke_session(ident:str,user=Depends(require_user)):
        with engine.begin() as conn:
            session_row(conn,ident,user,True)
            conn.execute(update(db.sessions).where(db.sessions.c.id==ident).values(revoked=True,updated_at=db.now()))
            conn.execute(update(db.jobs).where(db.jobs.c.session_id==ident,db.jobs.c.state.in_(ACTIVE_JOBS)).values(cancel_requested=True))
            db.emit(conn,'session',user['id'],ident,revoked=True)
        return {'ok':True}

    @router.get('/sessions/{ident}/revisions')
    def list_revisions(ident:str,user=Depends(require_user),cursor:str|None=None,limit:int=50):
        limit=bounded_limit(limit,50)
        with engine.connect() as conn:
            session_row(conn,ident,user)
            query=select(db.revisions.c.id,db.revisions.c.session_id,db.revisions.c.state,db.revisions.c.event_count,db.revisions.c.round_count,db.revisions.c.diagnostic_count,db.revisions.c.created_at).where(db.revisions.c.session_id==ident,db.revisions.c.state.in_(('complete','ready','ready_with_diagnostics','succeeded'))).order_by(db.revisions.c.id).limit(limit+1)
            if cursor: query=query.where(db.revisions.c.id>cursor)
            return page(conn.execute(query).mappings().all(),limit)

    @router.get('/sessions/{ident}/rounds')
    def list_rounds(ident:str,user=Depends(require_user),revision:str|None=None,cursor:int|None=None,limit:int=100,recent:int|None=None):
        limit=bounded_limit(limit,100)
        with engine.connect() as conn:
            rev=revision_row(conn,ident,revision,user)
            query=select(db.rounds).where(db.rounds.c.revision_id==rev['id'])
            if cursor is not None: query=query.where(db.rounds.c.number>cursor)
            query=query.order_by(db.rounds.c.number.desc() if recent else db.rounds.c.number).limit(min(recent,3) if recent else limit+1)
            rows=[dict(x) for x in conn.execute(query).mappings()]
            if recent: rows.reverse()
            return {'items':rows[:limit],'next_cursor':str(rows[limit-1]['number']) if len(rows)>limit else None}

    @router.get('/sessions/{ident}/events')
    def list_events(ident:str,user=Depends(require_user),revision:str|None=None,round:int|None=None,cursor:int|None=None,limit:int=40):
        limit=bounded_limit(limit,40)
        with engine.connect() as conn:
            rev=revision_row(conn,ident,revision,user)
            query=select(db.events).where(db.events.c.revision_id==rev['id']).order_by(db.events.c.seq).limit(limit+1)
            if round is not None: query=query.where(db.events.c.round_number==round)
            if cursor is not None: query=query.where(db.events.c.seq>cursor)
            rows=conn.execute(query).mappings().all()
            items=[]
            used=64
            for row in rows[:limit]:
                item={'id':row['id'],'seq':row['seq'],'round_number':row['round_number'],'event':row['event_json']}
                size=len(json.dumps(item,ensure_ascii=False).encode('utf-8'))+2
                if used+size>1024**2:
                    if not items: raise HTTPException(500,'索引记录超出分页上限，请重新索引此会话')
                    break
                items.append(item);used+=size
            return {'items':items,'next_cursor':str(items[-1]['seq']) if items and len(items)<len(rows) else None}

    def content_permission(conn,table,ident,user):
        item=conn.execute(select(table).where(table.c.id==ident)).mappings().first()
        if not item: raise HTTPException(404,'内容不存在')
        rev=conn.execute(select(db.revisions).where(db.revisions.c.id==item['revision_id'])).mappings().first()
        if not rev: raise HTTPException(404,'内容版本不存在')
        revision_row(conn,rev['session_id'],rev['id'],user)
        return item

    @router.get('/contents/{ident}')
    def get_content(ident:str,user=Depends(require_user),cursor:int=0,limit:int=262144):
        limit=bounded_limit(limit,262144)
        if cursor<0: raise HTTPException(422,'游标不能小于 0')
        with engine.connect() as conn: item=content_permission(conn,db.contents,ident,user)
        path=safe_path(config,item['path'])
        if not path.is_file(): raise HTTPException(404,'内容文件缺失')
        content_bytes=item['bytes']
        offset=(item['metadata_json'] or {}).get('offset',0)
        if cursor>content_bytes: raise HTTPException(422,'游标超过内容长度')
        with path.open('rb') as handle:
            handle.seek(offset+cursor);data=handle.read(min(limit,content_bytes-cursor))
            # UTF-8 pages always end at complete code points and use byte cursors.
            try: text=data.decode('utf-8')
            except UnicodeDecodeError as exc:
                if exc.end==len(data) and exc.reason=='unexpected end of data':
                    data=data[:exc.start];text=data.decode('utf-8')
                else: text=data.decode('utf-8',errors='replace')
            nxt=cursor+len(data)
        return {'text':text,'next_cursor':str(nxt) if nxt<content_bytes else None,'kind':item['kind']}

    @router.get('/assets/{ident}')
    def get_asset(ident:str,user=Depends(require_user)):
        with engine.connect() as conn: item=content_permission(conn,db.assets,ident,user)
        if item['status']!='ready' or not item['path']: raise HTTPException(404,'关联图片缺失或未同步')
        path=safe_path(config,item['path'])
        if not path.is_file(): raise HTTPException(404,'关联图片缺失')
        mime=item['mime'] if item['mime'] in ('image/png','image/jpeg','image/gif','image/webp') else 'application/octet-stream'
        return FileResponse(path,media_type=mime,headers={'Content-Security-Policy':"default-src 'none'; sandbox"})

    @router.get('/sources')
    def list_sources(user=Depends(require_user),cursor:str|None=None,limit:int=100,q:str|None=None):
        local_only();limit=bounded_limit(limit,100)
        query=select(db.sources).where(db.sources.c.owner_id==user['id']).order_by(db.sources.c.id).limit(limit+1)
        if cursor: query=query.where(db.sources.c.id>cursor)
        if q:
            if len(q)>200: raise HTTPException(422,'搜索词过长')
            term='%'+q.replace('%','\\%').replace('_','\\_')+'%'
            query=query.where(or_(db.sources.c.title.ilike(term,escape='\\'),db.sources.c.project.ilike(term,escape='\\'),db.sources.c.path.ilike(term,escape='\\')))
        with engine.connect() as conn: return page(conn.execute(query).mappings().all(),limit)

    @router.post('/sources/scan')
    def scan_sources(user=Depends(require_user)):
        local_only()
        with admission_lock,engine.begin() as conn:
            existing=conn.execute(select(db.jobs).where(db.jobs.c.kind=='scan',db.jobs.c.state.in_(ACTIVE_JOBS))).mappings().first()
            return {'job':job_public(existing or enqueue(conn,config,user['id'],'scan'))}

    @router.post('/sources/{ident}/index')
    def index_source(ident:str,user=Depends(require_user)):
        local_only()
        with admission_lock,engine.begin() as conn:
            source=conn.execute(select(db.sources).where(db.sources.c.id==ident,db.sources.c.owner_id==user['id'])).mappings().first()
            if not source: raise HTTPException(404,'来源不存在，请重新扫描')
            linked=conn.execute(select(db.sessions).where(db.sessions.c.id==source['session_id'],db.sessions.c.revoked.is_(False))).mappings().first() if source['session_id'] else None
            sid=linked['id'] if linked else db.new_id()
            existing=conn.execute(select(db.jobs).where(db.jobs.c.session_id==sid,db.jobs.c.kind=='ingest',db.jobs.c.state.in_(ACTIVE_JOBS))).mappings().first()
            if existing: return {'job':job_public(existing),'session_id':sid}
            if not linked:
                conn.execute(db.sessions.insert().values(id=sid,owner_id=user['id'],title=source['title'] or '未命名会话',original_title=source['title'],project=source['project'],source_kind=source['source_kind'],native_id=source['native_id'],source_id=ident,status='queued'))
                conn.execute(update(db.sources).where(db.sources.c.id==ident).values(session_id=sid))
            job=enqueue(conn,config,user['id'],'ingest',sid,{'source_id':ident,'path':source['path'],'source_kind':source['source_kind'],'native_id':source['native_id']})
            return {'job':job_public(job),'session_id':sid}

    def queue_import(path,filename,user,digest,native_path=None):
        with admission_lock,engine.begin() as conn:
            title=Path(filename).stem[:1000]
            source=None;existing=None
            if native_path:
                source=conn.execute(select(db.sources).where(db.sources.c.path==str(native_path),db.sources.c.source_kind=='cursor_jsonl',db.sources.c.owner_id==user['id'])).mappings().first()
                if source and source['session_id']:
                    existing=conn.execute(select(db.sessions).where(db.sessions.c.id==source['session_id'],db.sessions.c.revoked.is_(False))).mappings().first()
            if not existing:
                existing=conn.execute(select(db.sessions).where(db.sessions.c.owner_id==user['id'],db.sessions.c.revoked.is_(False),or_(db.sessions.c.metadata_json['import_sha256'].as_string()==digest,db.sessions.c.current_revision.in_(select(db.revisions.c.id).where(db.revisions.c.source_hash==digest)))).order_by(db.sessions.c.created_at).limit(1)).mappings().first()
            sid=existing['id'] if existing else db.new_id()
            source_id=source['id'] if source else None
            if native_path:
                info=native_path.stat()
                if not source:
                    source_id=db.new_id()
                    conn.execute(db.sources.insert().values(id=source_id,owner_id=user['id'],path=str(native_path),native_id=native_path.stem,source_kind='cursor_jsonl',title=title,project=str(native_path.parent),session_id=sid,size=info.st_size,mtime=info.st_mtime))
                else:
                    conn.execute(update(db.sources).where(db.sources.c.id==source_id).values(session_id=sid,size=info.st_size,mtime=info.st_mtime,updated_at=db.now()))
            if existing:
                same_job=conn.execute(select(db.jobs).where(db.jobs.c.session_id==sid,db.jobs.c.kind=='ingest',db.jobs.c.state.in_(ACTIVE_JOBS),db.jobs.c.payload_json['source_sha256'].as_string()==digest).order_by(db.jobs.c.created_at.desc()).limit(1)).mappings().first()
                if same_job:
                    path.unlink(missing_ok=True)
                    return {'job':job_public(same_job),'session_id':sid,'unchanged':True}
                current=conn.execute(select(db.revisions).where(db.revisions.c.id==existing['current_revision'])).mappings().first() if existing['current_revision'] else None
                if current and current['source_hash']==digest:
                    jid=db.new_id()
                    conn.execute(db.jobs.insert().values(id=jid,owner_id=user['id'],kind='ingest',state='succeeded',session_id=sid,revision_id=current['id'],progress=current['source_size'],total=current['source_size'],result_json={'unchanged':True,'revision_id':current['id']}))
                    path.unlink(missing_ok=True)
                    return {'job':job_public(conn.execute(select(db.jobs).where(db.jobs.c.id==jid)).mappings().one()),'session_id':sid,'unchanged':True}
                meta=dict(existing['metadata_json'] or {});meta['import_sha256']=digest
                changes={'metadata_json':meta,'updated_at':db.now()}
                if source_id and not existing['source_id']: changes['source_id']=source_id
                conn.execute(update(db.sessions).where(db.sessions.c.id==sid).values(**changes))
            else:
                conn.execute(db.sessions.insert().values(id=sid,owner_id=user['id'],title=title,original_title=title,status='queued',source_kind='cursor_jsonl',native_id=title,source_id=source_id,metadata_json={'import_sha256':digest}))
            payload={'path':str(path),'source_kind':'cursor_jsonl','source_sha256':digest}
            if source_id: payload['source_id']=source_id
            job=enqueue(conn,config,user['id'],'ingest',sid,payload)
            return {'job':job_public(job),'session_id':sid}

    @router.post('/imports')
    async def import_file(file:UploadFile=File(...),user=Depends(require_user)):
        local_only();check_disk(config)
        if not (file.filename or '').lower().endswith(('.jsonl','.json')): raise HTTPException(422,'请选择 JSONL 文件')
        path=config.home/'imports'/(db.new_id()+'.jsonl')
        total=0;hasher=hashlib.sha256()
        try:
            with path.open('wb') as handle:
                while chunk:=await file.read(1024**2):
                    total+=len(chunk)
                    if total>config.max_package_bytes: raise HTTPException(413,'文件超过 512 MiB')
                    handle.write(chunk);hasher.update(chunk)
            if not total: raise HTTPException(422,'文件为空')
            return queue_import(path,file.filename,user,hasher.hexdigest())
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        finally:
            await file.close()

    @router.post('/imports/path')
    def import_path(body:PathImport,user=Depends(require_user)):
        local_only();check_disk(config)
        source=Path(body.path).expanduser().resolve()
        if not source.is_file() or source.suffix.lower() not in ('.jsonl','.json'): raise HTTPException(422,'请选择可读取的 JSONL 文件')
        if source.stat().st_size>config.max_package_bytes: raise HTTPException(413,'文件超过 512 MiB')
        path=config.home/'imports'/(db.new_id()+'.jsonl')
        try:
            with source.open('rb') as inp,path.open('wb') as out:
                copied=0;hasher=hashlib.sha256()
                while chunk:=inp.read(1024**2):
                    copied+=len(chunk)
                    if copied>config.max_package_bytes: raise HTTPException(413,'文件超过 512 MiB')
                    out.write(chunk);hasher.update(chunk)
            return queue_import(path,source.name,user,hasher.hexdigest(),source)
        except BaseException:
            path.unlink(missing_ok=True)
            raise

    @router.get('/jobs')
    def list_jobs(user=Depends(require_user),cursor:str|None=None,limit:int=50):
        limit=bounded_limit(limit,50)
        query=select(db.jobs).where(db.jobs.c.owner_id==user['id']).order_by(db.jobs.c.created_at.desc(),db.jobs.c.id.desc()).limit(limit+1)
        with engine.connect() as conn:
            if cursor:
                row=conn.execute(select(db.jobs).where(db.jobs.c.id==cursor,db.jobs.c.owner_id==user['id'])).mappings().first()
                if not row: raise HTTPException(422,'无效分页游标')
                query=query.where(or_(db.jobs.c.created_at<row['created_at'],and_(db.jobs.c.created_at==row['created_at'],db.jobs.c.id<cursor)))
            rows=conn.execute(query).mappings().all()
            result=page(rows,limit);result['items']=[job_public(x) for x in rows[:limit]]
            return result

    @router.post('/jobs/{ident}/{action}')
    def job_action(ident:str,action:str,user=Depends(require_user)):
        if action not in ('cancel','retry','pause','resume'): raise HTTPException(404,'任务操作不存在')
        with admission_lock,engine.begin() as conn:
            row=conn.execute(select(db.jobs).where(db.jobs.c.id==ident)).mappings().first()
            if not row: raise HTTPException(404,'任务不存在')
            if row['owner_id']!=user['id'] and user['role']!='admin': raise HTTPException(403,'只能修改自己的任务')
            changes={'updated_at':db.now()}
            if action=='cancel':
                changes['cancel_requested']=True
                if row['state']!='running': changes['state']='cancelled'
            elif action=='pause':
                if row['state'] not in ('running','queued'): raise HTTPException(409,'该任务无法暂停')
                changes['state']='paused'
            else:
                permitted=('failed','cancelled') if action=='retry' else ('paused',)
                if row['state'] not in permitted: raise HTTPException(409,'任务状态不允许此操作')
                if row['session_id']: session_row(conn,row['session_id'],user)
                if action=='retry':
                    queued=conn.execute(select(func.count()).select_from(db.jobs).where(db.jobs.c.state.in_(ACTIVE_JOBS))).scalar_one()
                    own=conn.execute(select(func.count()).select_from(db.jobs).where(db.jobs.c.state.in_(ACTIVE_JOBS),db.jobs.c.owner_id==row['owner_id'])).scalar_one()
                    if queued>=config.max_queue or own>=config.max_user_queue: raise HTTPException(429,'后台队列已满，请稍后重试')
                changes.update(state='queued',cancel_requested=False,error=None,lease_owner=None,lease_until=None)
            conn.execute(update(db.jobs).where(db.jobs.c.id==ident).values(**changes))
            db.emit(conn,'job',row['owner_id'],row['session_id'],job_id=ident,state=changes.get('state',row['state']))
            return {'job':job_public(conn.execute(select(db.jobs).where(db.jobs.c.id==ident)).mappings().one())}

    @router.get('/jobs/{ident}')
    def get_job(ident:str,user=Depends(require_user)):
        with engine.connect() as conn:
            row=conn.execute(select(db.jobs).where(db.jobs.c.id==ident,db.jobs.c.owner_id==user['id'])).mappings().first()
            if not row: raise HTTPException(404,'任务不存在')
            return job_public(row)

    @router.get('/activity')
    async def activities(request:Request,user=Depends(require_user)):
        try: after=int(request.headers.get('last-event-id',request.query_params.get('after','0')))
        except ValueError: raise HTTPException(422,'无效事件游标')
        if not after:
            with engine.connect() as conn: after=conn.execute(select(func.max(db.activity.c.id))).scalar() or 0
        async def generate():
            nonlocal after
            last_keepalive=db.now()
            while not await request.is_disconnected():
                # Revocation also closes previously established event streams.
                try: require_user(request)
                except HTTPException: return
                with engine.connect() as conn:
                    rows=conn.execute(select(db.activity).where(db.activity.c.id>after).order_by(db.activity.c.id).limit(100)).mappings().all()
                for row in rows:
                    after=row['id']
                    if row['kind']=='job' and row['owner_id']!=user['id']: continue
                    yield f'id: {after}\nevent: update\ndata: {json.dumps(dict(row),ensure_ascii=False)}\n\n'
                if db.now()-last_keepalive>20:
                    yield ': keepalive\n\n';last_keepalive=db.now()
                await asyncio.sleep(2)
        return StreamingResponse(generate(),media_type='text/event-stream',headers={'X-Accel-Buffering':'no'})

    def comment_view(conn,row):
        item=dict(row)
        item['owner_name']=conn.execute(select(db.users.c.display_name).where(db.users.c.id==row['owner_id'])).scalar() or ''
        return item

    @router.get('/sessions/{ident}/comments')
    def list_comments(ident:str,user=Depends(require_user),revision:str|None=None,event_id:str|None=None,cursor:str|None=None,limit:int=50):
        limit=bounded_limit(limit,100)
        with engine.connect() as conn:
            session_row(conn,ident,user)
            query=select(db.comments).where(db.comments.c.session_id==ident).order_by(db.comments.c.id).limit(limit+1)
            if revision: query=query.where(db.comments.c.revision_id==revision)
            if event_id: query=query.where(db.comments.c.event_id==event_id)
            if cursor: query=query.where(db.comments.c.id>cursor)
            rows=conn.execute(query).mappings().all();result=page(rows,limit)
            result['items']=[comment_view(conn,row) for row in rows[:limit]]
            return result

    @router.post('/sessions/{ident}/comments')
    def add_comment(ident:str,body:Comment,user=Depends(require_user)):
        with engine.begin() as conn:
            rev=anchor(conn,ident,body.revision_id,body.event_id,user)
            cid=db.new_id()
            conn.execute(db.comments.insert().values(id=cid,session_id=ident,revision_id=rev,event_id=body.event_id,owner_id=user['id'],text=body.text))
            db.emit(conn,'comment',user['id'],ident,comment_id=cid,revision_id=rev)
            return comment_view(conn,conn.execute(select(db.comments).where(db.comments.c.id==cid)).mappings().one())

    def own_comment(conn,ident,user):
        row=conn.execute(select(db.comments).where(db.comments.c.id==ident)).mappings().first()
        if not row: raise HTTPException(404,'评论不存在')
        session_row(conn,row['session_id'],user)
        if row['owner_id']!=user['id'] and user['role']!='admin': raise HTTPException(403,'只能修改自己的评论')
        return row

    @router.patch('/comments/{ident}')
    def edit_comment(ident:str,body:CommentEdit,user=Depends(require_user)):
        with engine.begin() as conn:
            row=own_comment(conn,ident,user)
            conn.execute(update(db.comments).where(db.comments.c.id==ident).values(text=body.text,updated_at=db.now()))
            db.emit(conn,'comment',user['id'],row['session_id'],comment_id=ident)
            return comment_view(conn,conn.execute(select(db.comments).where(db.comments.c.id==ident)).mappings().one())

    @router.delete('/comments/{ident}')
    def delete_comment(ident:str,user=Depends(require_user)):
        with engine.begin() as conn:
            row=own_comment(conn,ident,user)
            conn.execute(delete(db.comments).where(db.comments.c.id==ident))
            db.emit(conn,'comment',user['id'],row['session_id'],comment_id=ident,deleted=True)
        return {'ok':True}

    @router.get('/favorites')
    def list_favorites(user=Depends(require_user),cursor:str|None=None,limit:int=50,session_id:str|None=None):
        limit=bounded_limit(limit,50)
        query=select(db.favorites).join(db.sessions,db.sessions.c.id==db.favorites.c.session_id).where(db.favorites.c.owner_id==user['id'],db.sessions.c.revoked.is_(False)).order_by(db.favorites.c.id).limit(limit+1)
        if cursor: query=query.where(db.favorites.c.id>cursor)
        if session_id: query=query.where(db.favorites.c.session_id==session_id)
        with engine.connect() as conn:
            result=page(conn.execute(query).mappings().all(),limit)
            for item in result['items']:
                if item['event_id']:
                    indexed=conn.execute(select(db.events.c.seq,db.events.c.round_number).where(db.events.c.id==item['event_id'],db.events.c.revision_id==item['revision_id'])).mappings().first()
                    if indexed: item.update(event_seq=indexed['seq'],round_number=indexed['round_number'])
            return result

    @router.post('/favorites')
    def add_favorite(body:Favorite,user=Depends(require_user)):
        with engine.begin() as conn:
            session_row(conn,body.session_id,user)
            if body.revision_id or body.event_id: anchor(conn,body.session_id,body.revision_id,body.event_id,user)
            same=and_(db.favorites.c.owner_id==user['id'],db.favorites.c.session_id==body.session_id,db.favorites.c.revision_id==body.revision_id,db.favorites.c.event_id==body.event_id)
            existing=conn.execute(select(db.favorites).where(same)).mappings().first()
            if existing: return dict(existing)
            ident=db.new_id();conn.execute(db.favorites.insert().values(id=ident,owner_id=user['id'],**body.model_dump()))
            return dict(conn.execute(select(db.favorites).where(db.favorites.c.id==ident)).mappings().one())

    @router.delete('/favorites/{ident}')
    def remove_favorite(ident:str,user=Depends(require_user)):
        with engine.begin() as conn: conn.execute(delete(db.favorites).where(db.favorites.c.id==ident,db.favorites.c.owner_id==user['id']))
        return {'ok':True}

    @router.post('/sessions/{ident}/exports')
    def export(ident:str,body:Export,user=Depends(require_user)):
        if body.format not in ('html','markdown'): raise HTTPException(422,'导出格式仅支持 html 或 markdown')
        with admission_lock,engine.begin() as conn:
            rev=revision_row(conn,ident,body.revision_id,user)
            return {'job':job_public(enqueue(conn,config,user['id'],'export',ident,{'format':body.format,'revision_id':rev['id']}))}

    @router.get('/exports/{ident}/download')
    def download_export(ident:str,user=Depends(require_user)):
        with engine.connect() as conn:
            job=conn.execute(select(db.jobs).where(db.jobs.c.id==ident,db.jobs.c.kind=='export',db.jobs.c.owner_id==user['id'])).mappings().first()
            if not job: raise HTTPException(404,'导出不存在')
            session_row(conn,job['session_id'],user)
            if job['state']!='succeeded': raise HTTPException(409,'导出尚未完成')
            result=job['result_json'] or {}
            path=safe_path(config,result.get('path',''))
            if not path.is_file(): raise HTTPException(404,'导出文件不存在')
        return FileResponse(path,filename=Path(result.get('filename',path.name)).name,media_type=result.get('mime','application/octet-stream'),headers={'Content-Security-Policy':"sandbox; default-src 'none'; style-src 'unsafe-inline'"})

    def own_upload(conn,ident,user):
        row=conn.execute(select(db.uploads).where(db.uploads.c.id==ident,db.uploads.c.owner_id==user['id'])).mappings().first()
        if not row: raise HTTPException(404,'上传任务不存在')
        return row

    def upload_view(row):
        return {**{k:row[k] for k in ('id','state','total_bytes','session_id','job_id')},'chunk_size':config.max_chunk_bytes,'received_chunks':sorted(int(x) for x in (row['received_json'] or {}))}

    @router.post('/uploads')
    def upload_start(body:UploadStart,user=Depends(require_user)):
        if config.mode!='cloud': raise HTTPException(404,'同步上传仅由团队服务器接收')
        check_disk(config)
        with admission_lock,engine.begin() as conn:
            existing=conn.execute(select(db.uploads).where(db.uploads.c.owner_id==user['id'],db.uploads.c.sha256==body.sha256,db.uploads.c.session_key==body.session_key,db.uploads.c.device_id==body.device_id,db.uploads.c.state.not_in(('cancelled','expired')),or_(db.uploads.c.session_id.is_(None),db.uploads.c.session_id.in_(select(db.sessions.c.id).where(db.sessions.c.revoked.is_(False))))).order_by(db.uploads.c.created_at.desc())).mappings().first()
            if existing: return upload_view(existing)
            # Stale abandoned slots expire; their files remain available for explicit cleanup.
            conn.execute(update(db.uploads).where(db.uploads.c.state=='uploading',db.uploads.c.updated_at<db.now()-86400).values(state='expired'))
            active=conn.execute(select(db.uploads.c.owner_id).where(db.uploads.c.state.in_(('uploading','assembling')))).scalars().all()
            if len(active)>=2 or user['id'] in active: raise HTTPException(429,'上传并发已满，请完成或取消现有上传')
            ident=db.new_id()
            conn.execute(db.uploads.insert().values(id=ident,owner_id=user['id'],state='uploading',received_json={},**body.model_dump()))
            (config.home/'uploads'/ident).mkdir(exist_ok=True)
            return upload_view(conn.execute(select(db.uploads).where(db.uploads.c.id==ident)).mappings().one())

    @router.get('/uploads/{ident}')
    def get_upload(ident:str,user=Depends(require_user)):
        with engine.connect() as conn: return upload_view(own_upload(conn,ident,user))

    @router.put('/uploads/{ident}/chunks/{number}')
    async def put_chunk(ident:str,number:int,request:Request,user=Depends(require_user)):
        with engine.connect() as conn: row=own_upload(conn,ident,user)
        if row['state']!='uploading': raise HTTPException(409,'上传状态不允许写入分块')
        count=math.ceil(row['total_bytes']/config.max_chunk_bytes)
        if number<0 or number>=count: raise HTTPException(422,'分块序号无效')
        expected_size=min(config.max_chunk_bytes,row['total_bytes']-number*config.max_chunk_bytes)
        expected=request.headers.get('x-chunk-sha256','')
        if not re.fullmatch(r'[0-9a-f]{64}',expected): raise HTTPException(422,'必须提供有效分块 SHA256')
        chunk_dir=config.home/'uploads'/ident
        temporary=chunk_dir/(str(number)+'.'+db.new_id()+'.tmp')
        hasher=hashlib.sha256();total=0
        try:
            with temporary.open('wb') as handle:
                async for chunk in request.stream():
                    total+=len(chunk)
                    if total>expected_size: raise HTTPException(413,'分块超过指定大小')
                    hasher.update(chunk);handle.write(chunk)
            if total!=expected_size or hasher.hexdigest()!=expected: raise HTTPException(422,'分块长度或校验值不匹配')
            with admission_lock,engine.begin() as conn:
                row=own_upload(conn,ident,user)
                if row['state']!='uploading': raise HTTPException(409,'上传已取消或提交')
                received=dict(row['received_json'] or {})
                if str(number) in received and received[str(number)]!=expected: raise HTTPException(409,'分块已存在且内容不同')
                temporary.replace(chunk_dir/(str(number)+'.chunk'))
                received[str(number)]=expected
                conn.execute(update(db.uploads).where(db.uploads.c.id==ident).values(received_json=received,updated_at=db.now()))
            return {'ok':True,'number':number}
        finally: temporary.unlink(missing_ok=True)

    @router.post('/uploads/{ident}/complete')
    def complete_upload(ident:str,user=Depends(require_user)):
        with admission_lock,engine.begin() as conn:
            row=own_upload(conn,ident,user)
            if row['job_id']:
                job=conn.execute(select(db.jobs).where(db.jobs.c.id==row['job_id'])).mappings().one()
                return {'job':job_public(job),'session_id':row['session_id']}
            if row['state']!='uploading': raise HTTPException(409,'上传状态不允许完成')
            count=math.ceil(row['total_bytes']/config.max_chunk_bytes)
            if len(row['received_json'] or {})!=count: raise HTTPException(409,'分块尚未全部上传')
            conn.execute(update(db.uploads).where(db.uploads.c.id==ident).values(state='assembling',updated_at=db.now()))
        bundle=config.home/'uploads'/ident/'bundle.zip'
        try:
            hasher=hashlib.sha256();size=0
            with bundle.open('wb') as out:
                for number in range(count):
                    with (bundle.parent/(str(number)+'.chunk')).open('rb') as inp:
                        while chunk:=inp.read(1024**2): hasher.update(chunk);size+=len(chunk);out.write(chunk)
            if size!=row['total_bytes'] or hasher.hexdigest()!=row['sha256']: raise HTTPException(422,'文件整体校验失败')
            with admission_lock,engine.begin() as conn:
                latest=own_upload(conn,ident,user)
                if latest['state']!='assembling': raise HTTPException(409,'上传已取消')
                # Server identity belongs to (member, device, native session), never a client owner ID.
                key=hashlib.sha256((row['device_id']+'\0'+row['session_key']).encode()).hexdigest()
                existing=conn.execute(select(db.sessions).where(db.sessions.c.owner_id==user['id'],db.sessions.c.source_id=='remote:'+key,db.sessions.c.revoked.is_(False))).mappings().first()
                sid=existing['id'] if existing else db.new_id()
                if not existing:
                    conn.execute(db.sessions.insert().values(id=sid,owner_id=user['id'],title=Path(row['filename']).stem,original_title=Path(row['filename']).stem,source_kind='bundle',source_id='remote:'+key,status='queued',sync_status='syncing'))
                job=enqueue(conn,config,user['id'],'bundle_import',sid,{'path':str(bundle),'upload_id':ident,'device_id':row['device_id'],'session_key':row['session_key']})
                conn.execute(update(db.uploads).where(db.uploads.c.id==ident).values(state='queued',session_id=sid,job_id=job['id'],updated_at=db.now()))
                conn.execute(db.syncs.insert().values(id=db.new_id(),owner_id=user['id'],device_id=row['device_id'],session_id=sid,upload_id=ident,job_id=job['id'],state='queued'))
                return {'job':job_public(job),'session_id':sid}
        except BaseException:
            with engine.begin() as conn:
                conn.execute(update(db.uploads).where(db.uploads.c.id==ident,db.uploads.c.state=='assembling').values(state='uploading',updated_at=db.now()))
            bundle.unlink(missing_ok=True)
            raise

    @router.delete('/uploads/{ident}')
    def cancel_upload(ident:str,user=Depends(require_user)):
        with admission_lock,engine.begin() as conn:
            row=own_upload(conn,ident,user)
            if row['job_id']: raise HTTPException(409,'上传已提交，请在任务列表中取消')
            conn.execute(update(db.uploads).where(db.uploads.c.id==ident).values(state='cancelled',updated_at=db.now()))
        return {'ok':True}

    @router.get('/syncs')
    def list_syncs(user=Depends(require_user),owner_id:str|None=None,cursor:str|None=None,limit:int=50):
        limit=bounded_limit(limit,50)
        query=select(db.syncs).order_by(db.syncs.c.created_at.desc(),db.syncs.c.id.desc()).limit(limit+1)
        if owner_id: query=query.where(db.syncs.c.owner_id==owner_id)
        with engine.connect() as conn:
            if cursor:
                at=conn.execute(select(db.syncs).where(db.syncs.c.id==cursor)).mappings().first()
                if not at: raise HTTPException(422,'无效分页游标')
                query=query.where(or_(db.syncs.c.created_at<at['created_at'],and_(db.syncs.c.created_at==at['created_at'],db.syncs.c.id<cursor)))
            rows=conn.execute(query).mappings().all();result=page(rows,limit)
            for item in result['items']:
                item.pop('metadata_json',None)
                item['owner_name']=conn.execute(select(db.users.c.display_name).where(db.users.c.id==item['owner_id'])).scalar() or ''
                if item['job_id']:
                    job=conn.execute(select(db.jobs).where(db.jobs.c.id==item['job_id'])).mappings().first()
                    if job: item.update(state=job['state'],error=job['error'],revision_id=job['revision_id'])
            return result

    if config.mode=='local':
        from .remote import create_router
        router.include_router(create_router(config,engine,require_user))
    app.include_router(router)

    @app.get('/health')
    def health():
        with engine.connect() as conn: conn.execute(select(db.schema_versions.c.version).limit(1)).first()
        return {'ok':True,'mode':config.mode}

    import os
    frontend=Path(os.getenv('CSH_FRONTEND_DIR',str(Path(__file__).resolve().parent.parent/'frontend'/'dist')))
    if not frontend.exists():
        import sys
        frontend=Path(getattr(sys,'_MEIPASS',''))/'frontend'/'dist'
    if frontend.is_dir():
        from fastapi.staticfiles import StaticFiles
        app.mount('/assets-static',StaticFiles(directory=frontend/'assets'),name='frontend-assets') if (frontend/'assets').is_dir() else None
        @app.get('/{path:path}')
        def frontend_page(path:str):
            if path.startswith('api/'):
                raise HTTPException(404,'接口不存在')
            target=(frontend/path).resolve()
            if target.is_relative_to(frontend.resolve()) and target.is_file(): return FileResponse(target)
            return FileResponse(frontend/'index.html')
    return app
