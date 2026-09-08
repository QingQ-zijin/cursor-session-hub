"""Install/mount the actual distributable, then start its packaged backend."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
def main():
 parser=argparse.ArgumentParser();parser.add_argument('--target',required=True);args=parser.parse_args()
 release=ROOT/'desktop/target'/args.target/'release'
 output=ROOT/'.runtime'/f'smoke-desktop-{args.target}.json';output.parent.mkdir(exist_ok=True)
 with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
  root=Path(temporary);home=root/'data';home.mkdir()
  env=dict(os.environ,CSH_HOME=str(home),CSH_SMOKE_OUTPUT=str(output),CSH_MIN_FREE_RATIO='0',CSH_MIN_FREE_BYTES='1048576')
  mounted=False;installed=None
  try:
   if sys.platform=='win32':
    installer=next((release/'bundle/nsis').glob('*.exe'))
    installed=root/'installed'
    subprocess.run([str(installer),'/S','/D='+str(installed)],timeout=120,check=True)
    binary=installed/'cursor-session-hub.exe'
    assert binary.is_file(),'Installer did not install the desktop executable'
   else:
    image=next((release/'bundle/dmg').glob('*.dmg'))
    mount=root/'volume';mount.mkdir()
    subprocess.run(['hdiutil','attach',str(image),'-mountpoint',str(mount),'-nobrowse','-readonly'],timeout=60,check=True,stdout=subprocess.DEVNULL)
    mounted=True
    app=next(mount.glob('*.app'))
    binary=app/'Contents/MacOS/cursor-session-hub'
   subprocess.run([str(binary),'--smoke-test'],env=env,timeout=120,check=True)
   assert output.is_file() and json.loads(output.read_text())['ok'],'Desktop smoke output missing'
  finally:
   if mounted:subprocess.run(['hdiutil','detach',str(mount)],timeout=30,check=False,stdout=subprocess.DEVNULL)
   if installed and installed.is_dir():
    for uninstaller in installed.glob('*ninstall*.exe'):
     subprocess.run([str(uninstaller),'/S'],timeout=60,check=False)
 print('Installed desktop startup smoke PASS:',args.target)
if __name__=='__main__':main()
