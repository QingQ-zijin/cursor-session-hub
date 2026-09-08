"""One durable queue supervisor, one isolated bounded child at a time."""
from __future__ import annotations
import os
import subprocess
import sys
import threading
import time
from sqlalchemy import select, or_
from sqlalchemy.exc import IntegrityError
import psutil
from . import db


def _execute(config, job_id):
    # CPU affinity is a hard one-core bound where the OS permits it. The
    # supervisor additionally enforces RSS and Docker sets memory/CPU limits.
    try:
        process = psutil.Process()
        allowed = process.cpu_affinity()
        if allowed:
            process.cpu_affinity(allowed[:1])
    except (AttributeError, OSError, psutil.Error):
        pass
    engine = db.get_engine(config)
    heartbeat_stop = threading.Event()
    heartbeat = None
    try:
        with engine.connect() as conn:
            job = conn.execute(select(db.jobs).where(db.jobs.c.id == job_id)).mappings().one()
        def keep_lease():
            next_renew = 0
            while not heartbeat_stop.wait(.25):
                try:
                    if psutil.Process().memory_info().rss >= int(config.worker_memory_bytes * .90):
                        with engine.begin() as conn:
                            conn.execute(db.jobs.update().where(db.jobs.c.id == job_id).values(state='failed', error='Parser memory limit reached; committed checkpoint retained', updated_at=db.now()))
                        os._exit(71)
                    if job['lease_owner'] and time.monotonic() >= next_renew:
                        with engine.begin() as conn:
                            held = conn.execute(db.leases.update().where(db.leases.c.name == 'parser', db.leases.c.owner == job['lease_owner']).values(expires_at=db.now() + 30))
                            if held.rowcount != 1:
                                # Lease takeover must never leave two writers
                                # continuing the same revision concurrently.
                                os._exit(72)
                            conn.execute(db.jobs.update().where(db.jobs.c.id == job_id, db.jobs.c.state == 'running').values(lease_until=db.now() + 60))
                        next_renew = time.monotonic() + 5
                except psutil.NoSuchProcess:
                    return
                except Exception:
                    # A transient database reconnect cannot spin this loop.
                    heartbeat_stop.wait(1)
        heartbeat = threading.Thread(target=keep_lease, daemon=True, name='csh-child-lease')
        heartbeat.start()
        kind = job['kind']
        if kind == 'ingest':
            from .ingest import ingest_path
            ingest_path(config, job_id)
        elif kind == 'bundle_import':
            from .bundles import import_bundle
            import_bundle(config, job_id)
        elif kind == 'export':
            from .bundles import export_job
            export_job(config, job_id)
        elif kind == 'scan':
            from .sources import discover_sources
            result = discover_sources(config)
            with engine.begin() as conn:
                conn.execute(db.jobs.update().where(db.jobs.c.id == job_id).values(result_json=result))
        elif kind == 'sync':
            from .remote import run_sync_job
            run_sync_job(config, job_id)
        else:
            raise ValueError('Unknown job kind: ' + kind)
        with engine.begin() as conn:
            conn.execute(db.jobs.update().where(db.jobs.c.id == job_id, db.jobs.c.state == 'running').values(state='succeeded', updated_at=db.now()))
            db.emit(conn, 'job', owner_id=job['owner_id'], session_id=job['session_id'], job_id=job_id)
    except Exception as exc:
        from .ingest import JobInterrupted
        with engine.begin() as conn:
            current = conn.execute(select(db.jobs).where(db.jobs.c.id == job_id)).mappings().one()
            state = current['state']
            if current['cancel_requested']:
                state = 'cancelled'
            elif isinstance(exc, JobInterrupted):
                state = 'paused' if state != 'cancelled' else state
            else:
                state = 'failed'
            conn.execute(db.jobs.update().where(db.jobs.c.id == job_id).values(state=state, error=str(exc)[:2000], updated_at=db.now()))
            conn.execute(db.syncs.update().where(db.syncs.c.job_id == job_id).values(state=state, error=str(exc)[:2000], updated_at=db.now()))
            db.emit(conn, 'job', owner_id=current['owner_id'], session_id=current['session_id'], job_id=job_id, state=state)
    finally:
        heartbeat_stop.set()
        if heartbeat:
            heartbeat.join(timeout=2)
        engine.dispose()


def _lease(engine, owner):
    moment = db.now()
    try:
        with engine.begin() as conn:
            existing = conn.execute(select(db.leases).where(db.leases.c.name == 'parser')).mappings().first()
            if not existing:
                conn.execute(db.leases.insert().values(name='parser', owner=owner, expires_at=moment + 30))
                return True
            result = conn.execute(db.leases.update().where(db.leases.c.name == 'parser', or_(db.leases.c.owner == owner, db.leases.c.expires_at < moment)).values(owner=owner, expires_at=moment + 30))
            return result.rowcount == 1
    except IntegrityError:
        return False


execute_job = _execute


def process_tree_rss(pid):
    """Windows venv/PyInstaller executables may be launchers with child runtimes."""
    process = psutil.Process(pid)
    total = 0
    for member in [process] + process.children(recursive=True):
        try:
            total += member.memory_info().rss
        except psutil.NoSuchProcess:
            pass
    return total


def terminate_tree(pid):
    try:
        process = psutil.Process(pid)
        members = process.children(recursive=True)
        for member in reversed(members):
            try:
                member.terminate()
            except psutil.NoSuchProcess:
                pass
        process.terminate()
        _, alive = psutil.wait_procs(members + [process], timeout=3)
        for member in alive:
            try:
                member.kill()
            except psutil.NoSuchProcess:
                pass
    except psutil.NoSuchProcess:
        pass


def run_forever(config, stop_event=None):
    stop_event = stop_event or threading.Event()
    engine = db.get_engine(config)
    db.init_db(engine)
    owner = db.new_id()
    child = None
    try:
        while not stop_event.is_set():
            if not _lease(engine, owner):
                stop_event.wait(2)
                continue
            with engine.begin() as conn:
                # Recover only expired jobs: two supervisors cannot both claim
                # an in-flight import after an app restart.
                conn.execute(db.jobs.update().where(db.jobs.c.state == 'running', or_(db.jobs.c.lease_until < db.now(), db.jobs.c.lease_until == None)).values(state='queued', lease_owner=None, updated_at=db.now()))
                row = conn.execute(select(db.jobs).where(db.jobs.c.state == 'queued', db.jobs.c.cancel_requested == False, or_(db.jobs.c.lease_until == None, db.jobs.c.lease_until <= db.now())).order_by(db.jobs.c.created_at).limit(1)).mappings().first()
                if row:
                    changed = conn.execute(db.jobs.update().where(db.jobs.c.id == row['id'], db.jobs.c.state == 'queued').values(state='running', lease_owner=owner, lease_until=db.now() + 60, error=None, updated_at=db.now()))
                    if changed.rowcount != 1:
                        row = None
            if not row:
                stop_event.wait(0.8)
                continue
            command = [sys.executable, '--worker-job', row['id']] if getattr(sys, 'frozen', False) else [sys.executable, '-m', 'hub', '--worker-job', row['id']]
            environment = dict(os.environ, CSH_MODE=config.mode, CSH_HOME=str(config.home), CSH_DATABASE_URL=config.database_url, CSH_WORKER_MEMORY_BYTES=str(config.worker_memory_bytes))
            child = subprocess.Popen(command, env=environment, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            failure = None
            while child.poll() is None and not stop_event.is_set():
                if not _lease(engine, owner):
                    failure = 'Worker lease lost; retry from checkpoint'
                    break
                with engine.begin() as conn:
                    current = conn.execute(select(db.jobs).where(db.jobs.c.id == row['id'])).mappings().one()
                    conn.execute(db.jobs.update().where(db.jobs.c.id == row['id']).values(lease_until=db.now() + 60))
                if current['cancel_requested'] or current['state'] in ('cancelled', 'paused'):
                    failure = 'cancelled' if current['cancel_requested'] else current['state']
                    break
                try:
                    rss = process_tree_rss(child.pid)
                    if rss >= int(config.worker_memory_bytes * 0.90):
                        failure = 'Parser memory limit reached; committed checkpoint retained'
                        break
                except psutil.NoSuchProcess:
                    break
                stop_event.wait(0.25)
            if child.poll() is None:
                terminate_tree(child.pid)
            child.wait(timeout=5)
            with engine.begin() as conn:
                current = conn.execute(select(db.jobs).where(db.jobs.c.id == row['id'])).mappings().one()
                if current['state'] == 'running':
                    state = 'queued' if stop_event.is_set() else ('paused' if failure == 'paused' else 'cancelled' if failure == 'cancelled' else 'failed')
                    result = dict(current['result_json'] or {})
                    retry_after = None
                    if not stop_event.is_set() and failure is None and result.get('_crash_retries', 0) < 2:
                        result['_crash_retries'] = result.get('_crash_retries', 0) + 1
                        state = 'queued'
                        retry_after = db.now() + result['_crash_retries']
                    conn.execute(db.jobs.update().where(db.jobs.c.id == row['id']).values(state=state, error=failure or f'Worker exited before publication (exit {child.returncode})', lease_owner=None, lease_until=retry_after, result_json=result, updated_at=db.now()))
                    conn.execute(db.syncs.update().where(db.syncs.c.job_id == row['id']).values(state=state, error=failure, updated_at=db.now()))
            child = None
    finally:
        if child and child.poll() is None:
            terminate_tree(child.pid)
            child.wait(timeout=5)
        with engine.begin() as conn:
            conn.execute(db.leases.delete().where(db.leases.c.name == 'parser', db.leases.c.owner == owner))
        engine.dispose()


class WorkerHandle:
    def __init__(self, config):
        self.event = threading.Event()
        self.thread = threading.Thread(target=run_forever, args=(config, self.event), daemon=True, name='csh-supervisor')
        self.thread.start()

    def stop(self):
        self.event.set()

    def join(self, timeout=10):
        self.thread.join(timeout)


def start_worker(config):
    return WorkerHandle(config)
