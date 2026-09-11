import json

import httpx
import pytest
from fastapi.testclient import TestClient

from hub import updates
from hub.api import create_app
from hub.config import Config


def release(version='0.1.4', suffix='x64-setup.exe'):
    name = f'Cursor.Session.Hub_{version}_{suffix}'
    return {'tag_name': 'v' + version, 'draft': False, 'prerelease': False,
            'body': 'Release notes', 'assets': [{'name': name, 'state': 'uploaded',
            'browser_download_url': f'{updates.REPOSITORY}/releases/download/v{version}/{name}'}]}


@pytest.mark.parametrize('system,machine,suffix', [
    ('win32', 'AMD64', 'x64-setup.exe'), ('darwin', 'arm64', 'aarch64.dmg'),
    ('darwin', 'x86_64', 'x64.dmg')])
def test_platform_assets(system, machine, suffix):
    info = updates.release_info(release(suffix=suffix), '0.1.3', system, machine)
    assert info['available'] and info['download_url'].endswith(suffix)


@pytest.mark.parametrize('version,newer', [('0.1.3', False), ('0.1.2', False), ('0.1.10', True), ('1.0.0', True)])
def test_numeric_version_comparison(version, newer):
    assert updates.release_info(release(version), '0.1.3')['available'] is newer


@pytest.mark.parametrize('version', ['v1.0.0-rc1', '1.0', '../../x', None, 'v01.2.3'])
def test_invalid_versions(version):
    data = release(); data['tag_name'] = version
    with pytest.raises(ValueError): updates.release_info(data)


def test_do_not_offer_untrusted_downloads_or_drafts():
    data = release()
    data['assets'][0]['browser_download_url'] = 'https://example.com/installer.exe'
    assert updates.release_info(data, system='win32', machine='AMD64')['download_url'] is None
    data['draft'] = True
    with pytest.raises(ValueError): updates.release_info(data)
    data['draft'] = False; data['prerelease'] = True
    with pytest.raises(ValueError): updates.release_info(data)


def mock_client(monkeypatch, handler):
    original = httpx.Client
    monkeypatch.setattr(updates.httpx, 'Client', lambda **kw: original(**kw, transport=httpx.MockTransport(handler)))


def test_anonymous_bounded_cached_check(monkeypatch):
    calls = []
    def handler(request):
        calls.append(request)
        assert str(request.url) == updates.MANIFEST
        assert 'authorization' not in request.headers and 'cookie' not in request.headers
        assert not request.content
        return httpx.Response(200, json=release())
    mock_client(monkeypatch, handler)
    checker = updates.ReleaseChecker()
    assert checker.check()['status'] == 'ok'
    checker.check()
    assert len(calls) == 1


@pytest.mark.parametrize('status,body,expected', [
    (404, b'', 'no_release'), (429, b'', 'rate_limited'),
    (200, b'bad json', 'unavailable'), (200, b'x' * (updates.MAX_RESPONSE + 1), 'unavailable'),
    (302, b'', 'unavailable')], ids=['no-release', 'rate-limit', 'invalid-json', 'oversized', 'redirect'])
def test_release_failures_are_nonblocking(monkeypatch, status, body, expected):
    mock_client(monkeypatch, lambda req: httpx.Response(status, content=body))
    assert updates.ReleaseChecker().check()['status'] == expected


def test_update_endpoint_is_local_and_authenticated(tmp_path, monkeypatch):
    monkeypatch.setattr(updates.ReleaseChecker, 'check', lambda self, force=False: {'status': 'ok'})
    for mode in ('local', 'cloud'):
        cfg = Config(mode=mode, home=tmp_path / mode, local_token='test-only', worker_external=True)
        with TestClient(create_app(cfg)) as client:
            assert client.get('/api/v1/updates').status_code == 401
            if mode == 'local':
                assert client.get('/api/v1/updates', headers={'Authorization': 'Bearer test-only'}).json() == {'status': 'ok'}


def test_static_redirect_avoids_api_and_rejects_other_hosts(monkeypatch):
    calls=[]
    def handler(request):
        calls.append(str(request.url))
        if len(calls)==1:return httpx.Response(302,headers={'location':'https://release-assets.githubusercontent.com/test/update.json'})
        return httpx.Response(200,json=release())
    mock_client(monkeypatch,handler)
    assert updates.ReleaseChecker().check()['channel']=='static'
    assert len(calls)==2 and updates.LATEST not in calls
    calls.clear()
    monkeypatch.setattr(updates.ReleaseChecker,'fetch',lambda self,client,url: (404,{},None) if url==updates.MANIFEST else (200,{},release()))
    assert updates.ReleaseChecker().check()['channel']=='api'


def test_rate_limit_respects_reset_even_when_forced(monkeypatch):
    calls=[];reset=updates.time.time()+600
    def handler(request):
        calls.append(request)
        return httpx.Response(429,headers={'x-ratelimit-reset':str(reset)})
    mock_client(monkeypatch,handler)
    checker=updates.ReleaseChecker();result=checker.check()
    assert result['status']=='rate_limited' and result['retry_at']==reset
    checker.cached['checked_at']-=120
    checker.check(force=True)
    assert len(calls)==1


def test_untrusted_redirect_not_followed(monkeypatch):
    calls=[]
    def handler(request):
        calls.append(request);return httpx.Response(302,headers={'location':'http://127.0.0.1/secret'})
    mock_client(monkeypatch,handler)
    assert updates.ReleaseChecker().check()['status']=='unavailable'
    assert len(calls)==1
