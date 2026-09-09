"""Build Python on the target OS; PyInstaller is not a cross compiler."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--target');args=parser.parse_args()
    target=args.target or subprocess.check_output(['rustc','-vV'],text=True).split('host: ')[1].splitlines()[0]
    destination=ROOT/'desktop'/'binaries';destination.mkdir(parents=True,exist_ok=True)
    cmd=[sys.executable,'-m','PyInstaller','--noconfirm','--clean','--onefile','--name','hub-core','--paths',str(ROOT),
         '--distpath',str(ROOT/'.runtime'/'sidecar-dist'),'--workpath',str(ROOT/'.runtime'/'sidecar-work'),
         '--specpath',str(ROOT/'.runtime'),'--collect-all','jieba','--collect-all','keyring',
         '--hidden-import','uvicorn.logging','--hidden-import','uvicorn.loops.auto','--hidden-import','uvicorn.protocols.http.auto',
         '--hidden-import','uvicorn.protocols.websockets.auto','--hidden-import','uvicorn.lifespan.on',
         '--hidden-import','psycopg','--hidden-import','psycopg_binary',
         '--hidden-import','cursor_parser','--hidden-import','cursor_binary','--hidden-import','common',
         '--hidden-import','hub.worker','--hidden-import','hub.remote','--hidden-import','hub.bundles',
         '--hidden-import','hub.documents','--collect-all','pypdf',
         '--add-data',str(ROOT/'frontend/dist/export')+os.pathsep+'hub/export_assets',
         '--add-data',str(ROOT/'hub/fonts')+os.pathsep+'hub/fonts','--collect-all','reportlab',
         str(ROOT/'scripts'/'sidecar_entry.py')]
    subprocess.run(cmd,cwd=ROOT,check=True)
    ext='.exe' if sys.platform=='win32' else ''
    output=destination/f'hub-core-{target}{ext}'
    shutil.copy2(ROOT/'.runtime'/'sidecar-dist'/('hub-core'+ext),output)
    if sys.platform!='win32': output.chmod(0o755)
    print(output)
if __name__=='__main__': main()
