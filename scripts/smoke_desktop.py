import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
def main():
 p=argparse.ArgumentParser();p.add_argument('--target',required=True);a=p.parse_args()
 release=ROOT/'desktop/target'/a.target/'release'
 binary=release/'cursor-session-hub.exe' if sys.platform=='win32' else release/'bundle/macos/Cursor Session Hub.app/Contents/MacOS/cursor-session-hub'
 output=ROOT/'.runtime'/f'smoke-desktop-{a.target}.json';output.parent.mkdir(exist_ok=True)
 with tempfile.TemporaryDirectory() as home:
  env=dict(os.environ,CSH_HOME=home,CSH_SMOKE_OUTPUT=str(output),CSH_MIN_FREE_RATIO='0',CSH_MIN_FREE_BYTES='1048576')
  subprocess.run([str(binary),'--smoke-test'],env=env,timeout=120,check=True)
  assert output.is_file() and json.loads(output.read_text())['ok'],'Desktop smoke output missing'
 print('Desktop startup smoke PASS:',a.target)
if __name__=='__main__':main()
