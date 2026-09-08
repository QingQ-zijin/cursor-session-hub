"""Consistent streaming backup; restoration only into an empty deployment."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import time

ROOT=Path(__file__).resolve().parents[1]
def command(args, **kwargs): return subprocess.run(['docker','compose',*args],cwd=ROOT,check=True,**kwargs)
def digest(path):
 with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def main():
 parser=argparse.ArgumentParser()
 group=parser.add_mutually_exclusive_group(required=True)
 group.add_argument('--output',type=Path);group.add_argument('--restore',type=Path)
 parser.add_argument('--confirm-restore',action='store_true');args=parser.parse_args()
 directory=(args.output or args.restore).expanduser().resolve()
 if args.output:
  directory.mkdir(parents=True,exist_ok=True)
  if any(directory.iterdir()):raise SystemExit('Backup output directory must be empty')
  command(['stop','api','worker'])
  try:
   with (directory/'database.dump').open('wb') as out:command(['exec','-T','db','pg_dump','-U','hub','-Fc','hub'],stdout=out)
   with (directory/'contents.tar.gz').open('wb') as out:command(['run','--rm','--no-deps','-T','--entrypoint','tar','api','-czf','-','-C','/data','.'],stdout=out)
   manifest={'version':1,'created_at':time.time(),'sha256':{name:digest(directory/name) for name in ('database.dump','contents.tar.gz')}}
   (directory/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
  finally:command(['start','api','worker'])
  print('Backup complete:',directory)
 else:
  if not args.confirm_restore:raise SystemExit('Restoring requires --confirm-restore and an empty deployment')
  manifest=json.loads((directory/'manifest.json').read_text(encoding='utf-8'))
  for name in ('database.dump','contents.tar.gz'):
   if digest(directory/name)!=manifest['sha256'][name]:raise SystemExit('Backup checksum mismatch: '+name)
  command(['stop','api','worker'])
  try:
   probe="from hub.config import Config; from hub import db; from sqlalchemy import select,func; c=Config();e=db.get_engine(c);db.init_db(e);con=e.connect();n=con.execute(select(func.count()).select_from(db.sessions)).scalar_one();u=con.execute(select(func.count()).select_from(db.users).where(db.users.c.id!='local')).scalar_one();assert n==0 and u==0,'Target deployment is not empty';assert not any(p.is_file() for p in c.home.rglob('*')),'Target content volume is not empty'"
   command(['run','--rm','--no-deps','-T','--entrypoint','python','api','-c',probe])
   with (directory/'database.dump').open('rb') as source:command(['exec','-T','db','pg_restore','-U','hub','-d','hub','--clean','--if-exists','--no-owner'],stdin=source)
   with (directory/'contents.tar.gz').open('rb') as source:command(['run','--rm','--no-deps','-T','--entrypoint','tar','api','-xzf','-','-C','/data'],stdin=source)
   command(['run','--rm','-T','api','python','-m','hub','migrate'])
  finally:command(['start','api','worker'])
  print('Restore complete:',directory)
if __name__=='__main__':main()
