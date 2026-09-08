"""Set a stable release version consistently before committing and tagging."""
import json
from pathlib import Path
import re
import sys

root = Path(__file__).resolve().parents[1]
version = sys.argv[1] if len(sys.argv) == 2 else ''
if not re.fullmatch(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)', version):
    raise SystemExit('Usage: python scripts/set_version.py MAJOR.MINOR.PATCH')
old = json.loads((root / 'desktop/tauri.conf.json').read_text())['version']
for name in ('package.json', 'package-lock.json', 'frontend/package.json', 'frontend/package-lock.json', 'desktop/tauri.conf.json'):
    path = root / name
    data = json.loads(path.read_text())
    data['version'] = version
    if 'packages' in data:
        data['packages']['']['version'] = version
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
for name in ('hub/__init__.py', 'hub/api.py', 'desktop/Cargo.toml', 'desktop/Cargo.lock'):
    path = root / name
    content = path.read_text(encoding='utf-8')
    if name.endswith('Cargo.lock'):
        content = content.replace(f'name = "cursor-session-hub"\nversion = "{old}"', f'name = "cursor-session-hub"\nversion = "{version}"')
    elif name.endswith('Cargo.toml'):
        content = content.replace(f'version = "{old}"', f'version = "{version}"', 1)
    else:
        content = content.replace(f"'{old}'", f"'{version}'")
    path.write_text(content, encoding='utf-8')
print(f'Application version: {old} -> {version}')
