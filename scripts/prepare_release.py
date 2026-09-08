"""Collect only this version's tested installers and public license files."""
import hashlib
import json
from pathlib import Path
import shutil
import sys

root = Path(__file__).resolve().parents[1]
version = json.loads((root / 'desktop/tauri.conf.json').read_text())['version']
source = Path(sys.argv[1]).resolve()
output = root / 'release-assets'
output.mkdir(exist_ok=True)
for suffix in ('x64-setup.exe', 'aarch64.dmg', 'x64.dmg'):
    name = f'Cursor Session Hub_{version}_{suffix}'
    matches = list(source.rglob(name))
    if len(matches) != 1:
        raise SystemExit(f'Expected exactly one {name}, found {len(matches)}')
    shutil.copy2(matches[0], output / name.replace(' ', '.'))
for name in ('LICENSE', 'NOTICE.md'):
    shutil.copy2(root / name, output / name)
allowed = {f'Cursor.Session.Hub_{version}_{s}' for s in ('x64-setup.exe', 'aarch64.dmg', 'x64.dmg')} | {'LICENSE', 'NOTICE.md'}
if {p.name for p in output.iterdir()} - allowed - {'SHA256SUMS.txt'}:
    raise SystemExit('Unexpected files in release output; use a clean output directory')
lines = [f'{hashlib.file_digest((output / name).open("rb"), "sha256").hexdigest()}  {name}' for name in sorted(allowed)]
(output / 'SHA256SUMS.txt').write_text('\n'.join(lines) + '\n')
print(f'Prepared {version}: three installers, licenses and SHA256SUMS.txt')
