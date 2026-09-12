import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { resolve, extname, sep } from 'node:path';
import { chromium } from 'playwright';

const dist = fileURLToPath(new URL('../dist/', import.meta.url));
let browser, server, origin;
before(async () => {
  server = createServer(async (req, res) => {
    let file = resolve(dist, '.' + new URL(req.url, 'http://localhost').pathname);
    if (!file.startsWith(dist.endsWith(sep) ? dist : dist + sep)) return res.writeHead(403).end();
    if (!extname(file)) file = resolve(dist, 'index.html');
    try {
      const body = await readFile(file);
      res.writeHead(200, { 'Content-Type': ({ '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml' })[extname(file)] || 'application/octet-stream' });
      res.end(body);
    } catch { res.writeHead(404).end(); }
  });
  await new Promise(done => server.listen(0, '127.0.0.1', done));
  origin = `http://127.0.0.1:${server.address().port}`;
  const edge = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
  browser = await chromium.launch({ executablePath: process.env.HIVE_TEST_BROWSER || (existsSync(edge) ? edge : undefined), headless: true });
});
after(async () => { await browser?.close(); await new Promise(done => server?.close(done)); });

async function workspace(admin = true) {
  const page = await browser.newPage({ viewport: { width: 1400, height: 1000 } });
  page.setDefaultTimeout(2500);
  let mappings = { node_id: 'n1', version: 0, entries: [{ kind: 'model', name: 'Qwen/Qwen3-8B', target: '/mnt/weight/Qwen3-8B' }], updated_by: 'admin' };
  const writes = [], admissions = [];
  await page.route('**/api/**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname;
    if (path === '/api/session') return route.fulfill({ json: { id: 'u1', username: admin ? 'admin' : 'viewer', admin, can_request: false, can_view_credentials: false } });
    if (path === '/api/nodes/n1/mappings') {
      if (request.method() === 'PUT') { const body = request.postDataJSON(); writes.push(body); mappings = { ...mappings, ...body, version: mappings.version + 1 }; }
      return route.fulfill({ json: mappings });
    }
    if (path === '/api/nodes/check') return route.fulfill({ json: { status: 'ok', model: 'TestServer', detail: 'SSH 连接正常', host_key: { fingerprint: 'SHA256:test', algorithm: 'ed25519' } } });
    if (path === '/api/nodes' && request.method() === 'POST') { admissions.push(request.postDataJSON()); return route.fulfill({ status: 201, json: { id: 'n2' } }); }
    if (path === '/api/nodes') return route.fulfill({ json: [{ id: 'n1', name: 'Node one', host: '10.0.0.1', port: 22, ssh_user: 'root', cluster_name: 'test', generation: 'A3', model: 'TestServer', status: 'unknown', maintenance: false, metadata: {}, mounts: [], devices: [] }] });
    return route.fulfill({ json: [] });
  });
  return { page, writes, admissions };
}

test('admin edits node-local resource aliases; other members only read', async () => {
  const { page, writes } = await workspace();
  try {
    await page.goto(origin + '/nodes/n1');
    await page.getByRole('button', { name: '编辑资源映射' }).click();
    await page.getByLabel('本机位置 1').fill('/mnt/updated/Qwen3-8B');
    await page.getByRole('button', { name: '保存资源映射' }).click();
    await page.getByText('资源映射已保存。', { exact: true }).waitFor();
    assert.equal(writes[0].version, 0);
    assert.equal(writes[0].entries[0].target, '/mnt/updated/Qwen3-8B');
    await page.getByRole('button', { name: '编辑资源映射' }).click();
    await page.getByRole('button', { name: '添加镜像映射', exact: true }).click();
    await page.getByLabel('逻辑名称 2').fill('ascend-base');
    await page.getByLabel('本机位置 2').fill('registry.local/base:v1');
    assert.equal(await page.getByRole('region', { name: '节点资源映射' }).getByRole('combobox').count(), 0);
    await page.getByRole('button', { name: '保存资源映射' }).click();
    await page.getByText('资源映射已保存。', { exact: true }).waitFor();
    assert.equal(writes[1].entries[1].kind, 'image');
    await page.getByRole('button', { name: '编辑资源映射' }).click();
    await page.getByRole('button', { name: '删除映射 1', exact: true }).click();
    await page.getByRole('button', { name: '保存资源映射' }).click();
    await page.getByText('资源映射已保存。', { exact: true }).waitFor();
    assert.deepEqual(writes[2].entries, [{ kind: 'image', name: 'ascend-base', target: 'registry.local/base:v1' }]);
    await page.screenshot({ path: fileURLToPath(new URL('../../data/node-mappings-desktop.png', import.meta.url)), fullPage: true });
  } finally { await page.close(); }
  const viewer = await workspace(false);
  try {
    await viewer.page.goto(origin + '/nodes/n1');
    await viewer.page.getByText('Qwen/Qwen3-8B', { exact: true }).waitFor();
    assert.equal(await viewer.page.getByRole('button', { name: '编辑资源映射' }).count(), 0);
  } finally { await viewer.page.close(); }
});

test('admission can include optional mappings and stays usable on a narrow screen', async () => {
  const { page, admissions } = await workspace();
  try {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(origin + '/clusters');
    await page.getByRole('button', { name: '纳管节点', exact: true }).click();
    await page.getByLabel('服务器 IP / 主机名').fill('10.0.0.2');
    await page.getByLabel('SSH 密码', { exact: true }).fill('test-only');
    await page.getByRole('button', { name: '添加权重映射', exact: true }).click();
    await page.getByLabel('逻辑名称 1').fill('Qwen/Qwen3-8B');
    await page.getByLabel('本机位置 1').fill('/mnt/weight/Qwen3-8B');
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
    await page.screenshot({ path: fileURLToPath(new URL('../../data/node-mappings-mobile.png', import.meta.url)), fullPage: true });
    await page.getByRole('button', { name: '检测连接', exact: true }).click();
    await page.getByRole('button', { name: '添加节点', exact: true }).click();
    await page.getByRole('dialog').waitFor({ state: 'hidden' });
    assert.deepEqual(admissions[0].mappings, [{ kind: 'model', name: 'Qwen/Qwen3-8B', target: '/mnt/weight/Qwen3-8B' }]);
  } finally { await page.close(); }
});
