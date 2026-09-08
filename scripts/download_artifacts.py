"""Download validated private Actions artifacts without exposing signed URLs."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import zipfile
import httpx
from concurrent.futures import ThreadPoolExecutor, as_completed

def download_ranges(url, destination, label):
 with httpx.Client(timeout=30,trust_env=False) as client:
  response=client.get(url,headers={'Range':'bytes=0-0','Accept-Encoding':'identity'})
  if response.status_code!=206:raise ValueError('Artifact server did not support a bounded range')
  total=int(response.headers['content-range'].rsplit('/',1)[1])
 part_size=2*1024**2
 with destination.open('wb') as file:file.truncate(total)
 def fetch_part(start):
  end=min(start+part_size,total)-1
  for attempt in range(3):
   try:
    with httpx.Client(timeout=httpx.Timeout(30,connect=15),trust_env=False) as client, client.stream('GET',url,headers={'Range':f'bytes={start}-{end}','Accept-Encoding':'identity'}) as response:
     if response.status_code!=206:raise ValueError('Invalid artifact range response')
     received=0
     with destination.open('r+b') as file:
      file.seek(start)
      for block in response.iter_raw(65536):
       received+=len(block)
       if received>end-start+1:raise ValueError('Artifact range overflow')
       file.write(block)
     if received!=end-start+1:raise ValueError('Incomplete artifact range')
    return received
   except httpx.HTTPError:
    if attempt==2:raise
 with ThreadPoolExecutor(max_workers=8) as pool:
  futures=[pool.submit(fetch_part,start) for start in range(0,total,part_size)]
  done=0
  for future in as_completed(futures):
   done+=future.result()
   print(label,done//1024**2,'/',total//1024**2,'MiB',flush=True)

ROOT=Path(__file__).resolve().parents[1]
def main():
 parser=argparse.ArgumentParser();parser.add_argument('run_id');args=parser.parse_args()
 gh=shutil.which('gh.exe')
 if not gh:
  gh=str(next((Path(os.environ['LOCALAPPDATA'])/'Microsoft/WinGet/Packages').glob('GitHub.cli*/**/gh.exe')))
 token=subprocess.check_output([gh,'auth','token'],text=True).strip()
 base='https://api.github.com/repos/QingQ-zijin/cursor-session-hub'
 auth={'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json'}
 output=ROOT/'artifacts';output.mkdir(exist_ok=True)
 with httpx.Client(timeout=httpx.Timeout(45,connect=20),follow_redirects=False) as client:
  response=client.get(base+'/actions/runs/'+args.run_id+'/artifacts',headers=auth);response.raise_for_status()
  run=client.get(base+'/actions/runs/'+args.run_id,headers=auth);run.raise_for_status();commit=run.json()['head_sha']
  for item in response.json()['artifacts']:
   if not item['name'].startswith('cursor-session-hub-') or item['expired']:continue
   marker=output/(item['name']+'.json')
   if marker.exists() and json.loads(marker.read_text()).get('artifact_id')==item['id']:
    print('Already downloaded:',item['name']);continue
   redirect=client.get(item['archive_download_url'],headers=auth)
   if redirect.status_code not in (301,302,303,307,308):redirect.raise_for_status();raise ValueError('Missing artifact redirect')
   archive=output/(item['name']+'.zip.partial')
   download_ranges(redirect.headers['location'],archive,item['name'])
   delivered=[]
   with zipfile.ZipFile(archive) as bundle:
    for member in bundle.infolist():
     path=PurePosixPath(member.filename)
     if path.is_absolute() or '..' in path.parts:raise ValueError('Unsafe artifact path')
     if path.suffix not in ('.exe','.dmg','.json'):continue
     if path.suffix=='.json':
      target=output/'validation'/item['name']/path.name
     else:
      target=output/path.name
     target.parent.mkdir(parents=True,exist_ok=True)
     with bundle.open(member) as source,target.open('wb') as dest:shutil.copyfileobj(source,dest,1024**2)
     with target.open('rb') as source:digest=hashlib.file_digest(source,'sha256').hexdigest()
     delivered.append({'path':str(target),'bytes':target.stat().st_size,'sha256':digest})
   archive.unlink()
   marker.write_text(json.dumps({'artifact_id':item['id'],'run_id':args.run_id,'commit':commit,'files':delivered},indent=2),encoding='utf-8')
   print('Downloaded:',item['name'],flush=True)
if __name__=='__main__':
 try:main()
 except httpx.HTTPError as error:raise SystemExit('Artifact network error: '+type(error).__name__)
