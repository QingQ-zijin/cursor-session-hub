"""Bounded, anonymous stable-release checks. No session or account data is sent."""
from __future__ import annotations

import json
import platform
import re
import sys
import threading
import time

import httpx

from hub import __version__

REPOSITORY = 'https://github.com/QingQ-zijin/cursor-session-hub'
LATEST = 'https://api.github.com/repos/QingQ-zijin/cursor-session-hub/releases/latest'
MAX_RESPONSE = 256 * 1024


def version_tuple(value):
    if not isinstance(value, str) or not re.fullmatch(r'v?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)', value):
        raise ValueError('Invalid stable version')
    return tuple(map(int, value.removeprefix('v').split('.')))


def release_info(data, current=__version__, system=sys.platform, machine=None):
    tag = data.get('tag_name')
    version_tuple(tag)
    if data.get('draft') or data.get('prerelease'):
        raise ValueError('Not a published stable release')
    version = tag.removeprefix('v')
    arch = (machine or platform.machine()).lower()
    suffix = ('x64-setup.exe' if system == 'win32' and arch in ('amd64', 'x86_64') else
              'aarch64.dmg' if system == 'darwin' and arch in ('arm64', 'aarch64') else
              'x64.dmg' if system == 'darwin' and arch in ('amd64', 'x86_64') else None)
    expected = f'Cursor.Session.Hub_{version}_{suffix}' if suffix else None
    # GitHub replaces spaces in uploaded asset names with periods.
    asset = next((a for a in data.get('assets', []) if isinstance(a, dict) and
                  a.get('name', '').replace(' ', '.') == expected and a.get('state') == 'uploaded'), None)
    download_url = None
    if asset:
        from urllib.parse import unquote
        url = asset.get('browser_download_url', '')
        expected_path = f'{REPOSITORY}/releases/download/{tag}/'
        if url.startswith(expected_path) and unquote(url[len(expected_path):]).replace(' ', '.') == expected:
            download_url = url
    return {'current_version': current, 'latest_version': version,
            'available': version_tuple(version) > version_tuple(current),
            'release_url': f'{REPOSITORY}/releases/tag/{tag}',
            'download_url': download_url, 'notes': str(data.get('body') or '')[:12000]}


class ReleaseChecker:
    def __init__(self):
        self.lock = threading.Lock()
        self.cached = None
        self.expires = 0.0

    def check(self):
        with self.lock:
            if self.cached and time.monotonic() < self.expires:
                return self.cached
            result = {'current_version': __version__, 'available': False,
                      'release_url': REPOSITORY + '/releases', 'status': 'unavailable'}
            try:
                with httpx.Client(timeout=httpx.Timeout(10, connect=5), follow_redirects=False) as client:
                    with client.stream('GET', LATEST, headers={
                        'Accept': 'application/vnd.github+json', 'User-Agent': 'Cursor-Session-Hub',
                    }) as response:
                        if response.status_code == 404:
                            result['status'] = 'no_release'
                        else:
                            response.raise_for_status()
                            content = bytearray()
                            for chunk in response.iter_bytes(16384):
                                if len(content) + len(chunk) > MAX_RESPONSE:
                                    raise ValueError('Release response too large')
                                content.extend(chunk)
                            result.update(release_info(json.loads(content)))
                            result['status'] = 'ok'
            except (httpx.HTTPError, ValueError, TypeError, AttributeError):
                pass
            self.cached = result
            self.expires = time.monotonic() + (900 if result['status'] != 'unavailable' else 60)
            return result
