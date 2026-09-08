"""Explicit desktop-to-team synchronization. Credentials only enter OS keyring."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import secrets
import time
import threading
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse, Response
import httpx
import keyring
from sqlalchemy import select, func

from . import db

SERVICE = 'CursorSessionHub'


def normalize_url(value, *, allow_insecure_http=False):
    value = str(value or '').strip().rstrip('/')
    parts = urlsplit(value)
    if parts.username or parts.password or parts.query or parts.fragment or not parts.hostname:
        raise ValueError('请输入不含账号、参数的服务器地址。')
    if parts.scheme != 'https' and not (parts.scheme == 'http' and (parts.hostname in ('localhost', '127.0.0.1', '::1') or allow_insecure_http is True)):
        raise ValueError('请使用 HTTPS；如需公网 HTTP 内测，请先勾选允许 HTTP 连接。')
    return value


def configured_url(saved, value=None):
    value = str(value or saved.get('url') or '').strip().rstrip('/')
    # HTTP consent belongs to exactly the configured URL, never another queued target.
    return normalize_url(value, allow_insecure_http=saved.get('allow_insecure_http') is True and value == saved.get('url'))


def _read_config(config):
    try:
        result = json.loads((config.home / 'remote.json').read_text(encoding='utf-8'))
        return result if isinstance(result, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_config(config, value):
    config.prepare()
    target = config.home / 'remote.json'
    temporary = target.with_name('remote.' + secrets.token_hex(6) + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    os.replace(temporary, target)


def _vault_key(config, url):
    return hashlib.sha256((str(config.home) + '\n' + url).encode()).hexdigest()


def get_token(config, url):
    try:
        return keyring.get_password(SERVICE, _vault_key(config, url))
    except Exception as error:
        raise RuntimeError('系统凭证存储不可用，请检查 Windows 凭据管理器或 Mac 钥匙串。') from error


def set_token(config, url, token):
    try:
        if token:
            keyring.set_password(SERVICE, _vault_key(config, url), token)
        else:
            try:
                keyring.delete_password(SERVICE, _vault_key(config, url))
            except keyring.errors.PasswordDeleteError:
                pass
    except Exception as error:
        raise RuntimeError('无法保存到系统凭证存储；账号未保存，请检查钥匙串权限。') from error


def _credentials(config, url=None):
    saved = _read_config(config)
    url = configured_url(saved, url)
    token = get_token(config, url)
    if not token:
        raise HTTPException(401, '请先登录团队服务器。')
    return url, {'Authorization': 'Bearer ' + token}


def _checked(response):
    try:
        result = response.json()
    except ValueError:
        result = {}
    if response.is_error:
        detail = result.get('detail') or '服务器请求失败：' + str(response.status_code)
        raise HTTPException(response.status_code, detail)
    return result


def _cancelled(engine, job_id):
    with engine.connect() as connection:
        row = connection.execute(select(db.jobs.c.cancel_requested, db.jobs.c.state).where(db.jobs.c.id == job_id)).first()
    if not row or row.cancel_requested or row.state in ('paused', 'cancelled'):
        raise RuntimeError('同步已暂停或取消。')


def run_sync_job(config, job_id):
    from .bundles import build_bundle
    engine = db.get_engine(config)
    with engine.connect() as connection:
        job = dict(connection.execute(select(db.jobs).where(db.jobs.c.id == job_id)).mappings().one())
        session = dict(connection.execute(select(db.sessions).where(db.sessions.c.id == job['session_id'])).mappings().one())
    payload = job['payload_json'] or {}
    checkpoint = dict(job.get('checkpoint_json') or {})
    bundle = config.home / 'bundles' / (job_id + '.csh')
    sync_id = payload.get('sync_id')
    try:
        url, headers = _credentials(config, payload.get('server_url'))
        with httpx.Client(timeout=httpx.Timeout(60, connect=10), headers=headers) as client:
            me = _checked(client.get(url + '/api/v1/auth/me'))
            if me.get('id') != payload.get('remote_user_id'):
                raise RuntimeError('团队账号已改变，请重新创建同步任务。')
            _cancelled(engine, job_id)
            if not checkpoint.get('bundle_ready') or not bundle.is_file():
                build_bundle(config, session['id'], payload.get('revision_id'), bundle, payload.get('excluded_asset_ids') or [])
                if bundle.stat().st_size > config.max_package_bytes:
                    raise RuntimeError('同步包超过 512 MiB，请在预览中减少关联图片。')
                with bundle.open('rb') as source:
                    digest = hashlib.file_digest(source, 'sha256').hexdigest()
                checkpoint.update(bundle_ready=True, sha256=digest)
            total = bundle.stat().st_size
            if checkpoint.get('upload_id'):
                response = client.get(url + '/api/v1/uploads/' + checkpoint['upload_id'])
                upload = _checked(response) if response.status_code != 404 else None
                if upload and upload.get('state') in ('cancelled', 'expired'):
                    upload = None
            else:
                upload = None
            if not upload:
                upload = _checked(client.post(url + '/api/v1/uploads', json={
                    'filename': (session.get('title') or 'session')[:100] + '.csh',
                    'total_bytes': total, 'sha256': checkpoint['sha256'],
                    'session_key': session['id'],
                    'device_id': payload.get('device_id', ''),
                }))
                checkpoint['upload_id'] = upload['id']
            received = {int(i) for i in upload.get('received_chunks', [])}
            chunk_size = int(upload.get('chunk_size') or config.max_chunk_bytes)
            chunk_size = min(chunk_size, config.max_chunk_bytes)
            with engine.begin() as connection:
                connection.execute(db.jobs.update().where(db.jobs.c.id == job_id).values(checkpoint_json=checkpoint, total=total, updated_at=db.now()))
            with bundle.open('rb') as source:
                number = 0
                while chunk := source.read(chunk_size):
                    _cancelled(engine, job_id)
                    if number not in received:
                        _checked(client.put(url + '/api/v1/uploads/' + checkpoint['upload_id'] + '/chunks/' + str(number),
                            content=chunk, headers={'Content-Type': 'application/octet-stream', 'X-Chunk-SHA256': hashlib.sha256(chunk).hexdigest()}))
                    number += 1
                    with engine.begin() as connection:
                        connection.execute(db.jobs.update().where(db.jobs.c.id == job_id).values(progress=min(number * chunk_size, total), updated_at=db.now()))
            _cancelled(engine, job_id)
            completed = _checked(client.post(url + '/api/v1/uploads/' + checkpoint['upload_id'] + '/complete', json={}))
            remote_job = completed.get('job') or {}
            remote_job_id = remote_job.get('id') if isinstance(remote_job, dict) else remote_job
            # Uploaded and server-processing are distinct states; the remote job
            # is checked by the synchronization history endpoint on demand.
            result = {'server_url': url, 'remote_session_id': completed.get('session_id'), 'remote_job_id': remote_job_id}
            with engine.begin() as connection:
                connection.execute(db.jobs.update().where(db.jobs.c.id == job_id).values(result_json=result, progress=total, updated_at=db.now()))
                if sync_id:
                    connection.execute(db.syncs.update().where(db.syncs.c.id == sync_id).values(state='processing', upload_id=checkpoint['upload_id'], metadata_json=result, updated_at=db.now()))
                db.emit(connection, 'sync', owner_id='local', session_id=session['id'], state='processing')
            if remote_job_id:
                deadline = time.monotonic() + 1800
                while True:
                    _cancelled(engine, job_id)
                    status = _checked(client.get(url + '/api/v1/jobs/' + remote_job_id))
                    if status.get('state') == 'succeeded':
                        break
                    if status.get('state') in ('failed', 'cancelled'):
                        raise RuntimeError('服务器处理失败：' + str(status.get('error') or status['state']))
                    if time.monotonic() >= deadline:
                        raise RuntimeError('服务器仍在处理，稍后重试将继续检查同一任务。')
                    time.sleep(2)
            with engine.begin() as connection:
                connection.execute(db.sessions.update().where(db.sessions.c.id == session['id']).values(sync_status='synced', updated_at=db.now()))
                if sync_id:
                    connection.execute(db.syncs.update().where(db.syncs.c.id == sync_id).values(state='succeeded', metadata_json=result, updated_at=db.now()))
                db.emit(connection, 'sync', owner_id='local', session_id=session['id'], state='succeeded')
            return result
    except Exception as error:
        with engine.begin() as connection:
            connection.execute(db.sessions.update().where(db.sessions.c.id == session['id']).values(sync_status='failed'))
            if sync_id:
                connection.execute(db.syncs.update().where(db.syncs.c.id == sync_id).values(state='failed', error=str(error), updated_at=db.now()))
        raise
    finally:
        engine.dispose()


def create_router(config, engine, require_user):
    router = APIRouter(prefix='/remote')
    admission_lock = getattr(engine, '_csh_admission_lock', threading.RLock())

    def local(user=Depends(require_user)):
        if config.mode != 'local':
            raise HTTPException(404, '仅桌面客户端支持此操作。')
        return user

    @router.get('/config')
    def read_config(user=Depends(local)):
        saved = _read_config(config)
        return {'url': saved.get('url', ''), 'allow_insecure_http': saved.get('allow_insecure_http') is True}

    @router.put('/config')
    def configure(body: dict, user=Depends(local)):
        try:
            url = normalize_url(body.get('url'), allow_insecure_http=body.get('allow_insecure_http') is True)
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
        saved = _read_config(config)
        allow_http = url.startswith('http://') and body.get('allow_insecure_http') is True
        saved.update(url=url, allow_insecure_http=allow_http, device_id=saved.get('device_id') or db.new_id())
        _save_config(config, saved)
        return {'url': url, 'allow_insecure_http': allow_http}

    @router.post('/login')
    def login(body: dict, user=Depends(local)):
        saved = _read_config(config)
        try:
            url = configured_url(saved)
            with httpx.Client(timeout=20) as client:
                data = _checked(client.post(url + '/api/v1/auth/device-login', json={'username': body.get('username'), 'password': body.get('password')}))
            set_token(config, url, data['token'])
            saved.update(user_id=data['user']['id'], device_id=saved.get('device_id') or db.new_id())
            _save_config(config, saved)
            return {'user': data['user']}
        except HTTPException:
            raise
        except (ValueError, RuntimeError, httpx.HTTPError) as error:
            raise HTTPException(503, str(error)) from error

    @router.get('/me')
    def me(user=Depends(local)):
        saved = _read_config(config)
        if not saved.get('url'):
            return {'user': None, 'url': ''}
        try:
            url, headers = _credentials(config)
            with httpx.Client(timeout=10) as client:
                response = client.get(url + '/api/v1/auth/me', headers=headers)
            if response.status_code in (401, 403):
                return {'user': None, 'url': url}
            return {'user': _checked(response), 'url': url}
        except HTTPException as error:
            if error.status_code == 401:
                return {'user': None, 'url': saved['url']}
            raise
        except (ValueError, RuntimeError, httpx.HTTPError) as error:
            raise HTTPException(503, str(error)) from error

    @router.post('/logout')
    def logout(user=Depends(local)):
        saved = _read_config(config)
        if saved.get('url'):
            try:
                url, headers = _credentials(config)
                with httpx.Client(timeout=5) as client:
                    client.post(url + '/api/v1/auth/logout', headers=headers)
            except (HTTPException, httpx.HTTPError, ValueError, RuntimeError):
                pass
            set_token(config, saved['url'], None)
        return {'ok': True}

    @router.post('/preview')
    def preview(body: dict, user=Depends(local)):
        selected = body.get('session_ids') or []
        if not isinstance(selected, list) or len(selected) > 50:
            raise HTTPException(400, '每次最多选择 50 条会话。')
        result = []
        with engine.connect() as connection:
            for session_id in selected:
                row = connection.execute(select(db.sessions).where(db.sessions.c.id == str(session_id), db.sessions.c.owner_id == 'local', db.sessions.c.revoked == False)).mappings().first()
                if not row or not row['current_revision']:
                    raise HTTPException(409, '请先完成所选会话的本地解析。')
                image_rows = connection.execute(select(db.assets).where(db.assets.c.revision_id == row['current_revision'])).mappings()
                images = [{k: item[k] for k in ('id', 'name', 'mime', 'bytes')} | {'missing': item['status'] != 'ready'} for item in image_rows]
                result.append({'id': row['id'], 'title': row['title'], 'revision_id': row['current_revision'], 'assets': images})
        return {'sessions': result}

    @router.post('/sync')
    def sync(body: dict, user=Depends(local)):
        items = preview(body, user)['sessions']
        remote = me(user)
        if not remote['user']:
            raise HTTPException(401, '请先登录团队服务器。')
        saved = _read_config(config)
        created = []
        with admission_lock, engine.begin() as connection:
            active = connection.execute(select(func.count()).select_from(db.jobs).where(db.jobs.c.state.in_(('queued', 'running', 'paused')))).scalar_one()
            if active + len(items) > config.max_user_queue:
                raise HTTPException(429, '任务队列已满，请等待已有任务完成。')
            for item in items:
                duplicate = connection.execute(select(db.jobs).where(db.jobs.c.kind == 'sync', db.jobs.c.session_id == item['id'], db.jobs.c.state.in_(('queued', 'running', 'paused')))).mappings().first()
                if duplicate:
                    created.append(dict(duplicate)); continue
                job_id, sync_id = db.new_id(), db.new_id()
                payload = {'session_id': item['id'], 'revision_id': item['revision_id'], 'server_url': saved['url'],
                           'device_id': saved['device_id'], 'remote_user_id': remote['user']['id'], 'sync_id': sync_id,
                           'excluded_asset_ids': body.get('excluded_asset_ids') or []}
                connection.execute(db.jobs.insert().values(id=job_id, owner_id='local', kind='sync', session_id=item['id'], revision_id=item['revision_id'], payload_json=payload))
                connection.execute(db.syncs.insert().values(id=sync_id, owner_id='local', device_id=saved['device_id'], session_id=item['id'], revision_id=item['revision_id'], job_id=job_id, state='queued'))
                connection.execute(db.sessions.update().where(db.sessions.c.id == item['id']).values(sync_status='pending'))
                db.emit(connection, 'job', owner_id='local', session_id=item['id'], job_id=job_id, state='queued')
                created.append(dict(connection.execute(select(db.jobs).where(db.jobs.c.id == job_id)).mappings().one()))
        return {'jobs': created}

    @router.api_route('/api/{path:path}', methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE'])
    async def proxy(path: str, request: Request, user=Depends(local)):
        if '..' in path.split('/') or not path or path.startswith('/') or '\\' in path:
            raise HTTPException(400, 'Invalid API path')
        if path == 'auth/register' and request.method == 'POST':
            try:
                url = configured_url(_read_config(config))
            except ValueError as error:
                raise HTTPException(400, str(error)) from error
            headers = {}
        else:
            url, headers = _credentials(config)
        target = url + '/api/v1/' + path
        if request.url.query:
            target += '?' + request.url.query
        content_type = request.headers.get('content-type', '')
        if content_type:
            headers['Content-Type'] = content_type
        body = bytearray()
        async for block in request.stream():
            body.extend(block)
            if len(body) > 1024**2:
                raise HTTPException(413, '请求过大。')
        client = httpx.AsyncClient(timeout=httpx.Timeout(60, connect=10))
        try:
            upstream = await client.send(client.build_request(request.method, target, headers=headers, content=bytes(body)), stream=True)
        except httpx.HTTPError as error:
            await client.aclose()
            raise HTTPException(503, '无法连接团队服务器。') from error
        async def stream():
            try:
                async for block in upstream.aiter_bytes(65536):
                    yield block
            finally:
                await upstream.aclose()
                await client.aclose()
        forwarded = {name: upstream.headers[name] for name in ('content-type', 'content-disposition', 'cache-control') if name in upstream.headers}
        return StreamingResponse(stream(), status_code=upstream.status_code, headers=forwarded)

    return router
