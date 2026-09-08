"""Restore an actual backup into disposable, isolated Compose volumes.

The original project's volumes are never deleted. The test's unique project
name and every volume label/name are checked before disposable cleanup.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time
import httpx

ROOT=Path(__file__).resolve().parent.parent
ORIGINAL='cursor-session-hub'
INVENTORY_CODE=r'''
import hashlib,json
from sqlalchemy import select
from hub import db
from hub.config import Config
config=Config();engine=db.get_engine(config)
result={'tables':{}}
names=['users','sessions','revisions','rounds','events','contents','assets','comments','favorites']
with engine.connect() as conn:
    for name in names:
        table=db.metadata.tables[name]
        digest=hashlib.sha256();count=0
        for row in conn.execution_options(stream_results=True,yield_per=100).execute(select(table).order_by(table.c.id)).mappings():
            digest.update(json.dumps(dict(row),sort_keys=True,ensure_ascii=False,separators=(',',':')).encode('utf-8'))
            digest.update(b'\n');count+=1
        result['tables'][name]={'rows':count,'sha256':digest.hexdigest()}
digest=hashlib.sha256();count=0;size=0
for path in sorted(config.home.rglob('*')):
    if not path.is_file():continue
    with path.open('rb') as stream:content=hashlib.file_digest(stream,'sha256').hexdigest()
    digest.update((path.relative_to(config.home).as_posix()+'\0'+content+'\n').encode())
    count+=1;size+=path.stat().st_size
result['files']={'count':count,'bytes':size,'sha256':digest.hexdigest()}
engine.dispose();print(json.dumps(result,sort_keys=True),flush=True)
'''

def compose(environment,*args,check=True):
    result=subprocess.run(['docker','compose',*args],cwd=ROOT,env=environment,capture_output=True,text=True,encoding='utf-8',errors='replace')
    if check and result.returncode:
        raise AssertionError('Compose '+str(args[0])+' failed: '+result.stderr[-1200:])
    return result

def wait_health(url,timeout=45):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        try:
            response=httpx.get(url+'/health',timeout=2)
            if response.status_code==200:return
        except httpx.HTTPError:pass
        time.sleep(.5)
    raise AssertionError('HTTP health not ready: '+url)

def inventory(environment):
    result=compose(environment,'run','--rm','--no-deps','-T','--entrypoint','python','api','-c',INVENTORY_CODE)
    return json.loads(result.stdout)

def image_id(value):
    if isinstance(value,dict):
        if value.get('type')=='image' and value.get('asset_id'):return value['asset_id']
        for child in value.values():
            found=image_id(child)
            if found:return found
    if isinstance(value,list):
        for child in value:
            found=image_id(child)
            if found:return found
    return None

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--source-url',default='http://127.0.0.1:8088')
    parser.add_argument('--restore-port',type=int,default=8091)
    args=parser.parse_args()
    tag=secrets.token_hex(5)
    test_project='csh-restore-test-'+tag
    folder=ROOT/'.runtime/backup-smoke'/tag;folder.mkdir(parents=True)
    backup=folder/'archive'
    source_env=dict(os.environ,COMPOSE_PROJECT_NAME=ORIGINAL)
    target_url=f'http://127.0.0.1:{args.restore_port}'
    target_env=dict(os.environ,COMPOSE_PROJECT_NAME=test_project,CSH_PORT=str(args.restore_port),CSH_PUBLIC_URL=target_url,CSH_COOKIE_SECURE='0')
    metrics={'ok':False,'started_at':time.time(),'source_project':ORIGINAL,'test_project':test_project,'backup_path':str(backup),'steps':[]}
    original_stopped=False;target_created=False
    def passed(name):
        metrics['steps'].append(name)
        print(json.dumps({'step':name,'passed':True}),flush=True)
    try:
        # Resolve names from Compose itself, not from string assumptions alone.
        source_config=json.loads(compose(source_env,'config','--format','json').stdout)
        target_config=json.loads(compose(target_env,'config','--format','json').stdout)
        assert source_config['name']==ORIGINAL
        assert target_config['name']==test_project and re.fullmatch(r'csh-restore-test-[0-9a-f]{10}',test_project)
        assert test_project!=ORIGINAL
        original_volumes={value.get('name') for value in source_config.get('volumes',{}).values()}
        target_volumes=target_config.get('volumes',{})
        assert target_volumes and all(not value.get('external') and value.get('name','').startswith(test_project+'_') for value in target_volumes.values())
        assert not original_volumes.intersection(value['name'] for value in target_volumes.values())
        assert not compose(target_env,'ps','-q').stdout.strip(),'Disposable project unexpectedly already exists'
        wait_health(args.source_url)
        passed('verified_disposable_project_is_distinct_and_empty')
        metrics['downtime_started_at']=time.time()
        compose(source_env,'stop','api','worker');original_stopped=True
        original_inventory=inventory(source_env)
        (folder/'original-inventory.json').write_text(json.dumps(original_inventory,indent=2),encoding='utf-8')
        # backup.py is responsible for stop/dump/tar/checksums/restart.
        run=subprocess.run([sys.executable,str(ROOT/'scripts/backup.py'),'--output',str(backup)],cwd=ROOT,env=source_env,capture_output=True,text=True,encoding='utf-8',errors='replace')
        if run.returncode:raise AssertionError('Backup script failed: '+run.stderr[-1500:])
        original_stopped=False
        wait_health(args.source_url)
        metrics['original_downtime_seconds']=round(time.time()-metrics['downtime_started_at'],3)
        passed('backup_script_restarted_original_services')
        manifest=json.loads((backup/'manifest.json').read_text())
        for name in ('database.dump','contents.tar.gz'):
            with (backup/name).open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
            assert digest==manifest['sha256'][name]
        metrics['archive_bytes']={name:(backup/name).stat().st_size for name in ('database.dump','contents.tar.gz')}
        passed('backup_archives_match_manifest_checksums')
        target_created=True
        compose(target_env,'up','-d','--no-build')
        wait_health(target_url)
        restored=subprocess.run([sys.executable,str(ROOT/'scripts/backup.py'),'--restore',str(backup),'--confirm-restore'],cwd=ROOT,env=target_env,capture_output=True,text=True,encoding='utf-8',errors='replace')
        if restored.returncode:raise AssertionError('Restore script failed: '+restored.stderr[-1500:])
        wait_health(target_url)
        passed('restored_into_separate_postgresql_and_content_volumes')
        restored_inventory=inventory(target_env)
        (folder/'restored-inventory.json').write_text(json.dumps(restored_inventory,indent=2),encoding='utf-8')
        assert restored_inventory==original_inventory,'Restored table/content checksums differ from frozen source inventory'
        metrics['inventory']=restored_inventory
        passed('all_nine_tables_and_every_content_file_match')
        credentials=json.loads((ROOT/'.runtime/cloud-e2e/credentials.json').read_text(encoding='utf-8'))
        sid=credentials['demo_session_id'];summaries=[]
        for url in (args.source_url,target_url):
            with httpx.Client(base_url=url,headers={'Origin':url},timeout=20) as client:
                login=client.post('/api/v1/auth/login',json={key:credentials['bob'][key] for key in ('username','password')})
                assert login.status_code==200 and 'httponly' in login.headers.get('set-cookie','').lower()
                session=client.get('/api/v1/sessions/'+sid);assert session.status_code==200
                events=client.get('/api/v1/sessions/'+sid+'/events');assert events.status_code==200
                comments=client.get('/api/v1/sessions/'+sid+'/comments');assert comments.status_code==200
                aid=image_id(events.json());assert aid
                image=client.get('/api/v1/assets/'+aid);assert image.status_code==200
                summaries.append({'session':session.json(),'events':events.json(),'comments':comments.json(),'image_sha256':hashlib.sha256(image.content).hexdigest()})
        assert summaries[0]==summaries[1]
        metrics['demo_session_id']=sid;metrics['demo_image_sha256']=summaries[1]['image_sha256']
        passed('restored_browser_login_session_comments_and_image_match')
        # A second restore must be refused rather than replacing user data.
        denied=subprocess.run([sys.executable,str(ROOT/'scripts/backup.py'),'--restore',str(backup),'--confirm-restore'],cwd=ROOT,env=target_env,capture_output=True,text=True,encoding='utf-8',errors='replace')
        assert denied.returncode!=0 and 'not empty' in denied.stderr.lower()
        wait_health(target_url)
        passed('nonempty_restore_is_rejected_and_services_restart')
        metrics['ok']=True
    except Exception as error:
        metrics['error']=str(error)
        raise
    finally:
        if original_stopped:
            compose(source_env,'start','api','worker',check=False)
        if target_created:
            # Validate every final name immediately before volume deletion.
            assert test_project!=ORIGINAL and re.fullmatch(r'csh-restore-test-[0-9a-f]{10}',test_project)
            config=json.loads(compose(target_env,'config','--format','json').stdout)
            assert config['name']==test_project
            listed=subprocess.run(['docker','volume','ls','--filter','label=com.docker.compose.project='+test_project,'--format','{{.Name}}'],capture_output=True,text=True,encoding='utf-8',check=True).stdout.splitlines()
            assert all(name.startswith(test_project+'_') for name in listed)
            compose(target_env,'down','-v','--remove-orphans')
            metrics['test_volumes_removed']=listed
            assert not compose(target_env,'ps','-q').stdout.strip()
            passed('removed_only_verified_disposable_project_volumes')
        wait_health(args.source_url)
        metrics['original_healthy_after_cleanup']=True
        metrics['finished_at']=time.time();metrics['elapsed_seconds']=round(metrics['finished_at']-metrics['started_at'],3)
        (ROOT/'.runtime/backup-restore-metrics.json').write_text(json.dumps(metrics,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(metrics,ensure_ascii=False),flush=True)

if __name__=='__main__':main()
