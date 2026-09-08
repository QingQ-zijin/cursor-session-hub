"""Opt-in reproducible resource probe; all generated data stays outside Git.

Run: python tests/stress_ingest.py --home .runtime/stress --records 100000
The source is synthetic, never a customer's transcript. Default is >100 MiB.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import psutil
from sqlalchemy import select, func
from hub import db
from hub.config import Config
from hub.worker import process_tree_rss, terminate_tree


def run(home, records=100000):
    home = Path(home).resolve()
    home.mkdir(parents=True, exist_ok=True)
    source = home / 'synthetic.jsonl'
    with source.open('w', encoding='utf-8') as out:
        for i in range(records):
            out.write(json.dumps({'role': 'user' if i % 100 == 0 else 'assistant', 'message': {'content': [{'type': 'text', 'text': f'Record {i}: ' + 'bounded parsing evidence ' * 48}]}}, separators=(',', ':')) + '\n')
    config = Config(home=home / 'installation', worker_external=True, local_token='stress-local-token')
    engine = db.get_engine(config)
    db.init_db(engine)
    session, job = db.new_id(), db.new_id()
    with engine.begin() as conn:
        conn.execute(db.sessions.insert().values(id=session, owner_id='local', title='Synthetic stress session'))
        conn.execute(db.jobs.insert().values(id=job, owner_id='local', session_id=session, kind='ingest', state='running', payload_json={'path': str(source), 'source_kind': 'cursor_jsonl'}))
    env = dict(os.environ, CSH_HOME=str(config.home), CSH_DATABASE_URL=config.database_url, CSH_MODE='local')
    child = subprocess.Popen([sys.executable, '-m', 'hub', '--worker-job', job], env=env)
    process = psutil.Process(child.pid)
    start, peak = time.monotonic(), 0
    memory_by_progress = []
    next_sample = 0
    while child.poll() is None:
        try:
            rss = process_tree_rss(child.pid)
            peak = max(peak, rss)
            if rss > 512 * 1024**2:
                terminate_tree(child.pid)
                raise AssertionError('RSS exceeded 512 MiB')
            with engine.connect() as conn:
                progress = conn.execute(select(db.jobs.c.progress).where(db.jobs.c.id == job)).scalar_one()
            ratio = progress / source.stat().st_size
            if ratio >= next_sample:
                memory_by_progress.append({'progress': round(ratio, 2), 'rss_mib': round(rss / 1024**2, 2)})
                next_sample += .1
        except psutil.NoSuchProcess:
            break
        time.sleep(.1)
    child.wait()
    elapsed = time.monotonic() - start
    with engine.connect() as conn:
        final = conn.execute(select(db.jobs).where(db.jobs.c.id == job)).mappings().one()
        assert final['state'] == 'succeeded', final['error']
        session_row = conn.execute(select(db.sessions).where(db.sessions.c.id == session)).mappings().one()
        assert session_row['event_count'] == records
        revision = session_row['current_revision']
    timings = []
    from concurrent.futures import ThreadPoolExecutor
    def reader(_):
        samples = []
        for page in range(100):
            begin = time.perf_counter()
            with engine.connect() as conn:
                rows = conn.execute(select(db.events).where(db.events.c.revision_id == revision, db.events.c.seq > (page * 997) % max(records - 40, 1)).order_by(db.events.c.seq).limit(40)).mappings().all()
                json.dumps([dict(row) for row in rows])
            samples.append(time.perf_counter() - begin)
        return samples
    with ThreadPoolExecutor(max_workers=5) as pool:
        for samples in pool.map(reader, range(5)):
            timings.extend(samples)
    p95 = sorted(timings)[int(.95 * len(timings))]
    result = {'records': records, 'source_mib': round(source.stat().st_size / 1024**2, 2), 'parse_seconds': round(elapsed, 2), 'peak_rss_mib': round(peak / 1024**2, 2), 'memory_by_progress': memory_by_progress, 'concurrent_readers': 5, 'queries': len(timings), 'indexed_page_p95_ms': round(p95 * 1000, 2), 'session_id': session, 'revision_id': revision, 'job_id': job}
    assert peak < 512 * 1024**2
    assert p95 < 1
    assert records < 100000 or source.stat().st_size >= 100 * 1024**2
    (home / 'metrics.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))
    engine.dispose()
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--home', required=True)
    parser.add_argument('--records', type=int, default=100000)
    args = parser.parse_args()
    run(args.home, args.records)
