import { test, expect, type Page } from '@playwright/test';

const member = { id: 'cloud-user', username: 'member', display_name: '成员', role: 'member', active: true };
const localSession = { id: 'local-one', title: '本地测试会话', current_revision: null, round_count: 0 };
const cloudSession = { id: 'cloud-one', title: '云端测试会话', current_revision: null, round_count: 0 };
function deferred() { let resolve!: () => void; const promise = new Promise<void>(r => resolve = r); return { promise, resolve }; }
async function setup(page: Page) {
  await page.route('**/api/v1/**', async route => {
    const path = new URL(route.request().url()).pathname;
    let value: unknown = { items: [], next_cursor: null };
    if (path.endsWith('/capabilities')) value = { mode: 'local' };
    else if (path.endsWith('/remote/me')) value = { user: member };
    else if (path.endsWith('/activity')) return route.fulfill({ contentType: 'text/event-stream', body: 'data: {"kind":"heartbeat"}\n\n' });
    else if (path.endsWith('/updates')) value = { status: 'ok', available: false, current_version: '0.1.3' };
    else if (path.endsWith('/members')) value = { items: [member] };
    else if (path.endsWith('/sessions')) value = { items: [path.includes('/remote/api/') ? cloudSession : localSession] };
    else if (path.endsWith('/sessions/local-one')) value = localSession;
    else if (path.endsWith('/sessions/cloud-one')) value = cloudSession;
    return route.fulfill({ json: value });
  });
}

test('local to cloud clears old rows and hash while waiting, then loads without syncing', async ({ page }) => {
  await setup(page);
  const gate = deferred(); let calls = 0;
  await page.route('**/api/v1/remote/api/sessions?*', async route => {
    calls++; await gate.promise; await route.fulfill({ json: { items: [cloudSession] } });
  });
  const wrong: string[] = [];
  page.on('request', req => { if (req.url().includes('/remote/api/sessions/local-one')) wrong.push(req.url()); });
  await page.goto('/#scope=local&session=local-one');
  await expect(page.locator('.session-main')).toContainText(localSession.title);
  await page.getByRole('button', { name: '团队空间', exact: true }).click();
  await expect(page.getByText('正在加载云端记录…')).toBeVisible();
  await expect(page.locator('.session-row')).toHaveCount(0);
  expect(new URL(page.url()).hash).toBe('');
  gate.resolve();
  await expect(page.locator('.session-main')).toContainText(cloudSession.title);
  expect(calls).toBeGreaterThan(0);
  expect(wrong).toEqual([]);
  await expect(page.getByRole('alert')).toHaveCount(0);
});

test('late cloud response cannot replace the local list after switching back', async ({ page }) => {
  await setup(page);
  const gate = deferred(); const started = deferred(); const delivered = deferred();
  await page.route('**/api/v1/remote/api/sessions?*', async route => {
    started.resolve(); await gate.promise;
    await route.fulfill({ json: { items: [cloudSession] } }); delivered.resolve();
  });
  await page.goto('/');
  await expect(page.locator('.session-main')).toContainText(localSession.title);
  await page.getByRole('button', { name: '团队空间', exact: true }).click();
  await started.promise;
  await page.getByRole('button', { name: '本地记录', exact: true }).click();
  await expect(page.locator('.session-main')).toContainText(localSession.title);
  gate.resolve(); await delivered.promise;
  await expect(page.locator('.session-main')).toContainText(localSession.title);
  await expect(page.getByText(cloudSession.title, { exact: true })).toHaveCount(0);
});

test('failed cloud loads show retry and never display local rows as cloud', async ({ page }) => {
  await setup(page);
  await page.route('**/api/v1/remote/api/sessions?*', route => route.fulfill({ status: 503, json: { detail: '连接暂时不可用' } }));
  await page.goto('/');
  await expect(page.locator('.session-main')).toBeVisible();
  await page.getByRole('button', { name: '团队空间', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('连接暂时不可用');
  await expect(page.locator('.session-row')).toHaveCount(0);
  await page.unroute('**/api/v1/remote/api/sessions?*');
  await page.getByRole('button', { name: '重新加载', exact: true }).click();
  await expect(page.locator('.session-main')).toContainText(cloudSession.title);
});

test('scoped cloud links start in cloud and late deep links cannot reopen a reader', async ({ page }) => {
  await setup(page);
  const gate = deferred(); const started = deferred();
  await page.route('**/api/v1/remote/api/sessions/cloud-one', async route => {
    started.resolve(); await gate.promise;
    await route.fulfill({ status: 404, json: { detail: '会话不存在或已撤销共享' } });
  });
  await page.goto('/#scope=team&session=cloud-one');
  await started.promise;
  await expect(page.locator('.session-main')).toContainText(cloudSession.title);
  await page.getByRole('button', { name: '本地记录', exact: true }).click();
  gate.resolve();
  await expect(page.locator('.session-main')).toContainText(localSession.title);
  await expect(page.getByRole('alert')).toHaveCount(0);
  await expect(page.getByText('选择会话开始阅读', { exact: true })).toBeVisible();
});

test('automatic new-release detection exposes platform download and notes', async ({ page }) => {
  await setup(page);
  await page.route('**/api/v1/updates*', route => route.fulfill({ json: {
    status: 'ok', available: true, current_version: '0.1.3', latest_version: '0.1.4', notes: '修复与改进',
    release_url: 'https://github.com/QingQ-zijin/cursor-session-hub/releases/tag/v0.1.4',
    download_url: 'https://github.com/QingQ-zijin/cursor-session-hub/releases/download/v0.1.4/test.exe',
  } }));
  await page.goto('/');
  await expect(page.getByRole('button', { name: '检查软件更新' })).toContainText('发现新版本', { timeout: 12000 });
  await page.getByRole('button', { name: '检查软件更新' }).click();
  const dialog = page.getByRole('dialog', { name: '软件更新' });
  await expect(dialog).toContainText('新版本 v0.1.4');
  await expect(dialog).toContainText('修复与改进');
  await expect(dialog.getByRole('button', { name: '下载安装包' })).toBeVisible();
});

test('offline release check remains nonfatal and can be retried', async ({ page }) => {
  await setup(page);
  await page.route('**/api/v1/updates*', route => route.fulfill({ json: { status: 'unavailable', available: false } }));
  await page.goto('/');
  await page.getByRole('button', { name: '检查软件更新' }).click();
  await expect(page.getByRole('dialog')).toContainText('更新服务暂时不可用');
  await page.unroute('**/api/v1/updates*');
  await page.getByRole('button', { name: '重新检查', exact: true }).click();
  await expect(page.getByRole('dialog')).toContainText('已是最新版本');
});
