import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { writeFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { resolve, extname, sep } from 'node:path';
import { chromium } from 'playwright';

const dist = fileURLToPath(new URL('../dist/', import.meta.url));
let browser, server, origin;
before(async () => {
  server = createServer(async (req, res) => {
    const path = decodeURIComponent(new URL(req.url, 'http://localhost').pathname);
    let file = resolve(dist, '.' + path);
    if (!file.startsWith(dist.endsWith(sep) ? dist : dist + sep)) { res.writeHead(403).end(); return; }
    if (!extname(file)) file = resolve(dist, 'index.html');
    try {
      const body = await readFile(file);
      res.writeHead(200, { 'Content-Type': ({ '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml' })[extname(file)] || 'application/octet-stream' }); res.end(body);
    } catch { res.writeHead(404).end(); }
  });
  await new Promise(done => server.listen(0, '127.0.0.1', done));
  origin = `http://127.0.0.1:${server.address().port}`;
  const edge = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
  const executablePath = process.env.HIVE_TEST_BROWSER || (existsSync(edge) ? edge : undefined);
  browser = await chromium.launch({ executablePath, headless: true });
});
after(async () => { await browser?.close(); await new Promise(done => server?.close(done)); });

const source = { pr: 123, head_sha: 'a'.repeat(40), vllm_sha: 'b'.repeat(40), repository: 'vllm-project/vllm-ascend' };
async function workspace({ spaces = [], presets = [], initialRuns = [], canRequest = true, admin = false } = {}) {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  page.setDefaultTimeout(2500);
  const workflows = [...initialRuns], submissions = [], errors = [], actions = [], debugRequests = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/api/**', async route => {
    const req = route.request(), path = new URL(req.url()).pathname;
    if (path === '/api/session') return route.fulfill({ json: { id: 'u1', username: 'alice', admin, can_request: canRequest, can_view_credentials: false } });
    if (path === '/api/sources/resolve') return route.fulfill({ json: source });
    if (path === '/api/requests' && req.method() === 'POST') { const body = req.postDataJSON(); debugRequests.push(body); return route.fulfill({ status: 202, json: { id: 'debug1', spec: body.spec } }); }
    if (path === '/api/workflows' && req.method() === 'POST') {
      const body = req.postDataJSON(); submissions.push(body);
      const result = { id: 'w1', name: body.name, owner_name: 'alice', status: 'QUEUED', spec: body, space_id: 's1', jobs: [], created_at: '2026-09-12T10:00:00Z' };
      workflows.push(result); return route.fulfill({ status: 202, json: result });
    }
    if (path === '/api/workflows') return route.fulfill({ json: workflows });
    if (path === '/api/spaces') return route.fulfill({ json: spaces });
    if (path === '/api/presets') return route.fulfill({ json: presets });
    if (/\/api\/workflows\/[^/]+\/cancel$/.test(path)) { actions.push(path); const run = workflows.find(item => path.includes(`/${item.id}/`)); if (run) run.status = 'CANCELLING'; return route.fulfill({ json: run }); }
    if (/\/api\/spaces\/[^/]+\/close$/.test(path)) { actions.push(path); return route.fulfill({ json: { status: 'CLOSING' } }); }
    if (/\/api\/workflows\/[^/]+$/.test(path)) return route.fulfill({ json: workflows.find(item => path.endsWith('/' + item.id)) });
    return route.fulfill({ json: [] });
  });
  await page.goto(origin + '/requests');
  return { page, workflows, submissions, errors, actions, debugRequests };
}

test('create a task with separate server/client environments on one resource node', async () => {
  const { page, submissions, errors } = await workspace();
  try {
    await page.getByRole('checkbox', { name: '新建任务' }).check();
    await page.getByRole('textbox', { name: '任务名称', exact: true }).fill('并排环境验证');
    await page.getByRole('textbox', { name: 'vLLM-Ascend PR', exact: true }).fill('123');
    await page.getByRole('button', { name: /解析 PR/ }).click();
    await page.getByText(source.head_sha, { exact: true }).waitFor();
    await page.getByRole('textbox', { name: 'server_env · 镜像', exact: true }).fill('fixture/server:1');
    await page.getByRole('textbox', { name: 'client_env · 镜像', exact: true }).fill('fixture/client:2');
    await page.getByRole('textbox', { name: 'job1 主执行 1 · PR 路径', exact: true }).fill('ci/check.sh');
    await page.getByRole('button', { name: '添加 job1 产物', exact: true }).click();
    await page.getByRole('textbox', { name: 'job1 产物 1 · 容器绝对路径', exact: true }).fill('/home/results/output.json');
    await page.getByRole('textbox', { name: 'job1 产物 1 · 名称', exact: true }).fill('运行结果');
    await page.getByRole('button', { name: /提交任务申请/ }).click();
    await page.getByRole('heading', { name: '并排环境验证', exact: true }).waitFor();
    assert.equal(submissions.length, 1);
    assert.equal(submissions[0].resource.machine_count, 1);
    assert.deepEqual(submissions[0].environments.map(env => [env.alias, env.role, env.node_alias, env.image]), [
      ['server_env', 'server', 'node0', 'fixture/server:1'], ['client_env', 'client', 'node0', 'fixture/client:2'],
    ]);
    assert.equal(submissions[0].source.head_sha, source.head_sha);
    assert.equal(submissions[0].environments[0].workdir, '/home');
    assert.equal(submissions[0].jobs[0].steps[0].path, 'ci/check.sh');
    assert.deepEqual(submissions[0].jobs[0].artifacts, [{ path: '/home/results/output.json', label: '运行结果', kind: 'file' }]);
    if (process.env.HIVE_BROWSER_PAYLOAD_PATH) await writeFile(process.env.HIVE_BROWSER_PAYLOAD_PATH, JSON.stringify(submissions[0], null, 2), 'utf8');
    assert.deepEqual(errors, []);
  } finally { await page.close(); }
});

test('attach Python and YAML files with explicit runners and independent environment setup', async () => {
  const { page, submissions } = await workspace();
  try {
    await page.getByRole('checkbox', { name: '新建任务' }).check();
    await page.getByRole('textbox', { name: '任务名称', exact: true }).fill('文件入口验证');
    await page.getByRole('textbox', { name: 'vLLM-Ascend PR', exact: true }).fill('123');
    await page.getByRole('button', { name: /解析 PR/ }).click();
    await page.getByText(source.head_sha, { exact: true }).waitFor();
    await page.getByRole('textbox', { name: 'server_env · 镜像', exact: true }).fill('fixture/server:1');
    await page.getByRole('textbox', { name: 'client_env · 镜像', exact: true }).fill('fixture/client:2');
    await page.getByText('server_env · 环境配置', { exact: true }).click();
    await page.getByRole('textbox', { name: 'server_env · Python 解释器', exact: true }).fill('/opt/server/bin/python');
    await page.getByRole('button', { name: /添加 server_env 安装文件/ }).click();
    await page.getByRole('textbox', { name: 'server_env 安装 1 · PR 路径', exact: true }).fill('ci/install.sh');
    await page.getByRole('combobox', { name: 'job1 主执行 1 · 文件类型', exact: true }).selectOption('python');
    await page.getByRole('textbox', { name: 'job1 主执行 1 · PR 路径', exact: true }).fill('ci/run.py');
    await page.getByLabel('job1 主执行 1 · 上传文件', { exact: true }).setInputFiles({ name: 'run.py', mimeType: 'text/plain', buffer: Buffer.from('print("fixture")\n') });
    await page.getByRole('button', { name: /添加 job1 主执行文件/ }).click();
    await page.getByRole('combobox', { name: 'job1 主执行 2 · 文件类型', exact: true }).selectOption('yaml');
    await page.getByRole('textbox', { name: 'job1 主执行 2 · PR 路径', exact: true }).fill('ci/input.yaml');
    await page.getByRole('button', { name: /提交任务申请/ }).click();
    assert.equal(submissions.length, 0, 'a YAML file cannot execute without a runner');
    await page.getByRole('textbox', { name: 'job1 主执行 2 · YAML 执行入口', exact: true }).fill('ci/runner.py');
    await page.getByLabel('job1 主执行 2 · 上传文件', { exact: true }).setInputFiles({ name: 'input.yaml', mimeType: 'text/yaml', buffer: Buffer.from('mode: fixture\n') });
    await page.getByRole('button', { name: /提交任务申请/ }).click();
    await page.getByRole('heading', { name: '文件入口验证', exact: true }).waitFor();
    assert.equal(submissions[0].environments[0].python, '/opt/server/bin/python');
    assert.equal(submissions[0].environments[1].python, 'python3');
    assert.equal(submissions[0].environments[0].install[0].path, 'ci/install.sh');
    assert.equal(submissions[0].jobs[0].steps[0].uploaded_content, 'print("fixture")\n');
    assert.equal(submissions[0].jobs[0].steps[1].runner.path, 'ci/runner.py');
    assert.deepEqual(submissions[0].jobs[0].steps[1].runner.args, ['${input}']);
  } finally { await page.close(); }
});

test('connect service readiness to a zero-NPU client and reject a dependency cycle', async () => {
  const { page, submissions } = await workspace();
  try {
    await page.getByRole('checkbox', { name: '新建任务' }).check();
    await page.getByRole('textbox', { name: '任务名称', exact: true }).fill('依赖编排验证');
    await page.getByRole('textbox', { name: 'vLLM-Ascend PR', exact: true }).fill('123');
    await page.getByRole('button', { name: /解析 PR/ }).click();
    await page.getByText(source.head_sha, { exact: true }).waitFor();
    await page.getByRole('textbox', { name: 'server_env · 镜像', exact: true }).fill('fixture/server:1');
    await page.getByRole('textbox', { name: 'client_env · 镜像', exact: true }).fill('fixture/client:2');
    await page.getByRole('combobox', { name: 'job1 · 作业类型', exact: true }).selectOption('service');
    await page.getByRole('textbox', { name: 'job1 主执行 1 · PR 路径', exact: true }).fill('ci/server.sh');
    await page.getByRole('button', { name: /添加 job1 就绪检查文件/ }).click();
    await page.getByRole('textbox', { name: 'job1 就绪检查 1 · PR 路径', exact: true }).fill('ci/ready.py');
    await page.getByRole('combobox', { name: 'job1 就绪检查 1 · 文件类型', exact: true }).selectOption('python');
    await page.getByRole('button', { name: /添加作业/ }).click();
    await page.getByRole('combobox', { name: 'job2 · 目标环境', exact: true }).selectOption('client_env');
    await page.getByRole('spinbutton', { name: 'job2 · NPU 数量', exact: true }).fill('0');
    await page.getByRole('textbox', { name: 'job2 主执行 1 · PR 路径', exact: true }).fill('ci/client.sh');
    await page.getByRole('button', { name: '从 job1 连线', exact: true }).click();
    await page.getByRole('button', { name: '连线到 job2', exact: true }).click();
    await page.getByRole('combobox', { name: 'job2 等待 job1', exact: true }).selectOption('ready');
    await page.getByRole('button', { name: '从 job2 连线', exact: true }).click();
    await page.getByRole('button', { name: '连线到 job1', exact: true }).click();
    await page.getByRole('alert').filter({ hasText: '依赖会形成环' }).waitFor();
    await page.getByRole('button', { name: /提交任务申请/ }).click();
    await page.getByRole('heading', { name: '依赖编排验证', exact: true }).waitFor();
    if (process.env.HIVE_BROWSER_SCREENSHOTS) { await page.locator('.workflow-graph').screenshot({ path: process.env.HIVE_BROWSER_SCREENSHOTS + '/workflow-dag.png' }); await page.screenshot({ path: process.env.HIVE_BROWSER_SCREENSHOTS + '/workflow-form.png', fullPage: true }); }
    assert.deepEqual(submissions[0].jobs[1].depends_on, [{ job_id: 'job1', condition: 'ready' }]);
    assert.equal(submissions[0].jobs[1].npu_count, 0);
    assert.deepEqual(submissions[0].jobs[0].depends_on, []);
    assert.equal(submissions[0].jobs[0].ready[0].path, 'ci/ready.py');
  } finally { await page.close(); }
});

test('reuse a retained space, inspect per-stage logs, and request task cancellation and space closure', async () => {
  const spaces = [{ id: 's1', status: 'READY', owner_name: 'alice', retain_until: '2099-01-01T00:00:00Z', spec: { resource: { machine_count: 1 } }, environments: [{ alias: 'server_env', role: 'server', node_alias: 'node0', status: 'READY', container_name: 'hive-owned-server' }] }];
  const initialRuns = [{ id: 'previous', name: '已有任务', owner_name: 'alice', status: 'RUNNING', space_id: 's1', created_at: '2026-09-12T10:00:00Z', spec: { source, jobs: [], environments: [] }, jobs: [{ id: 'job1', name: 'check', status: 'FAILED', phase: 'pre', reason: '检查失败', logs: [{ phase: 'pre', text: 'fixture precheck rejected' }] }] }];
  const { page, submissions, actions } = await workspace({ spaces, initialRuns });
  try {
    await page.getByRole('button', { name: '查看任务 已有任务', exact: true }).click();
    await page.getByText('fixture precheck rejected', { exact: true }).waitFor();
    await page.getByRole('button', { name: '关闭', exact: true }).click();
    await page.getByRole('button', { name: '取消任务 已有任务', exact: true }).click();
    await page.getByRole('button', { name: /确认取消任务/ }).click();
    await page.getByRole('checkbox', { name: '新建任务' }).check();
    await page.getByRole('textbox', { name: '任务名称', exact: true }).fill('复用环境任务');
    await page.getByRole('textbox', { name: 'vLLM-Ascend PR', exact: true }).fill('123');
    await page.getByRole('button', { name: /解析 PR/ }).click();
    await page.getByText(source.head_sha, { exact: true }).first().waitFor();
    await page.getByRole('checkbox', { name: '使用保留环境', exact: true }).check();
    await page.getByRole('combobox', { name: '运行空间', exact: true }).selectOption('s1');
    await page.getByRole('textbox', { name: 'job1 主执行 1 · PR 路径', exact: true }).fill('ci/check.sh');
    await page.getByRole('button', { name: /提交任务申请/ }).click();
    await page.getByRole('heading', { name: '复用环境任务', exact: true }).waitFor();
    assert.equal(submissions[0].space_id, 's1');
    assert.equal(submissions[0].resource, undefined);
    assert.deepEqual(submissions[0].environments, [], 'reuse sends no environment mutation; the API loads installation inputs from the owned space');
    await page.getByRole('button', { name: '关闭运行空间 s1', exact: true }).click();
    await page.getByRole('button', { name: /确认关闭运行空间/ }).click();
    assert.deepEqual(actions, ['/api/workflows/previous/cancel', '/api/spaces/s1/close']);
  } finally { await page.close(); }
});

test('filter externally supplied presets and keep unverified entries disabled', async () => {
  const presets = [
    { id: 'first', name: '已验收样例', enabled: true, tags: { cycle: 'nightly', kind: 'performance', model: 'fixture-model' }, workflow: { name: '预置任务', jobs: [{ id: 'job1', name: '外部样例', environment: 'server_env', kind: 'batch', npu_count: 1, ports: [], depends_on: [], pre: [], steps: [{ type: 'python', path: 'ci/sample.py', args: [] }], post: [], post_policy: 'success', ready: [], timeout_seconds: 60 }] } },
    { id: 'other', name: '等待验收条目', enabled: false, reason: '尚未验收，未开放执行', tags: { cycle: 'weekly', kind: 'accuracy' } },
  ];
  const { page, submissions } = await workspace({ presets });
  try {
    await page.getByRole('checkbox', { name: '新建任务' }).check();
    await page.getByText('选择外部预置', { exact: true }).click();
    await page.getByRole('combobox', { name: '预置周期', exact: true }).selectOption('weekly');
    assert.equal(await page.getByRole('button', { name: '使用预置 等待验收条目', exact: true }).isDisabled(), true);
    assert.equal(await page.getByRole('button', { name: '使用预置 已验收样例', exact: true }).count(), 0);
    await page.getByRole('combobox', { name: '预置周期', exact: true }).selectOption('nightly');
    await page.getByRole('textbox', { name: '搜索预置标签', exact: true }).fill('fixture-model');
    await page.getByRole('button', { name: '使用预置 已验收样例', exact: true }).click();
    assert.equal(await page.getByRole('textbox', { name: 'job1 主执行 1 · PR 路径', exact: true }).inputValue(), 'ci/sample.py');
    assert.equal(await page.getByRole('combobox', { name: 'job1 主执行 1 · 文件类型', exact: true }).inputValue(), 'python');
    await page.getByRole('textbox', { name: 'vLLM-Ascend PR', exact: true }).fill('123');
    await page.getByRole('button', { name: /解析 PR/ }).click();
    await page.getByText(source.head_sha, { exact: true }).waitFor();
    await page.getByRole('textbox', { name: 'server_env · 镜像', exact: true }).fill('fixture/server:1');
    await page.getByRole('textbox', { name: 'client_env · 镜像', exact: true }).fill('fixture/client:2');
    await page.getByRole('button', { name: /提交任务申请/ }).click();
    await page.getByRole('heading', { name: '预置任务', exact: true }).waitFor();
    assert.equal(submissions[0].preset_id, 'first');
    await page.setViewportSize({ width: 390, height: 844 });
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'mobile task form must stay within the viewport');
    if (process.env.HIVE_BROWSER_SCREENSHOTS) await page.screenshot({ path: process.env.HIVE_BROWSER_SCREENSHOTS + '/workflow-mobile.png', fullPage: true });
  } finally { await page.close(); }
});

test('ordinary requests remain debug-only and changing PR while resolving cannot keep the old snapshot', async () => {
  const { page, debugRequests, submissions } = await workspace();
  let releaseSource;
  const gate = new Promise(resolve => { releaseSource = resolve; });
  try {
    await page.getByRole('button', { name: /提交申请/ }).click();
    await page.getByRole('status').filter({ hasText: '已登记' }).waitFor();
    assert.equal(debugRequests[0].spec.purpose, 'debug');
    assert.equal(submissions.length, 0);
    await page.getByRole('checkbox', { name: '新建任务' }).check();
    await page.route('**/api/sources/resolve', async route => { await gate; await route.fulfill({ json: source }); });
    await page.getByRole('textbox', { name: 'vLLM-Ascend PR', exact: true }).fill('123');
    await page.getByRole('button', { name: /解析 PR/ }).click();
    await page.getByRole('textbox', { name: 'vLLM-Ascend PR', exact: true }).fill('124');
    releaseSource();
    await page.getByRole('button', { name: /解析 PR/ }).waitFor();
    assert.equal(await page.getByText(source.head_sha, { exact: true }).count(), 0, 'old PR response must not become the new PR snapshot');
  } finally { releaseSource(); await page.close(); }
});

test('users without request permission can inspect task configuration but cannot submit resource-consuming work', async () => {
  const { page, submissions } = await workspace({ canRequest: false });
  try {
    await page.getByRole('checkbox', { name: '新建任务' }).check();
    assert.equal(await page.getByRole('button', { name: /提交任务申请/ }).isDisabled(), true);
    await page.getByText('尚未获得服务器申请权限，请联系管理员开启任务提交。', { exact: true }).waitFor();
    assert.equal(submissions.length, 0);
  } finally { await page.close(); }
});

test('show execution evidence and artifact links without inventing a successful business verdict', async () => {
  const initialRuns = [{ id: 'done', name: '结果证据验证', owner_name: 'alice', status: 'SUCCEEDED', space_id: 's1', created_at: '2026-09-12T10:00:00Z', spec: { source, jobs: [], environments: [] }, jobs: [{ id: 'job1', name: '外部检查', status: 'SUCCEEDED', phase: 'post', cards: ['0'], endpoint: 'http://192.0.2.1:8000', attempts: [{ phase: 'main-0', status: 'SUCCEEDED', exit_code: 0, log_truncated: true }], logs: [{ phase: 'main-0', text: 'fixture completed', display_truncated: true }], artifacts: [{ id: 'a1', label: '原始结果', path: '/home/output.json', kind: 'file', size: 42, sha256: 'c'.repeat(64), download_url: '/api/workflows/done/jobs/job1/artifacts/a1' }], metrics: { metrics: [], verdict: 'unknown' } }] }];
  const spaces = [{ id: 's1', status: 'READY', owner_name: 'alice', spec: {}, environments: [{ alias: 'server_env', role: 'server', node_alias: 'node0', status: 'READY', host: '192.0.2.1', logical_ids: ['0'], container_name: 'hive-container', logs: [{ phase: 'verify', text: 'python=fixture' }] }] }];
  const { page } = await workspace({ initialRuns, spaces });
  try {
    await page.getByText('192.0.2.1', { exact: true }).waitFor();
    await page.getByRole('button', { name: '查看任务 结果证据验证', exact: true }).click();
    await page.getByText('业务判定未提供', { exact: true }).waitFor();
    assert.equal(await page.getByRole('link', { name: '下载产物 原始结果', exact: true }).getAttribute('href'), '/api/workflows/done/jobs/job1/artifacts/a1');
    await page.getByText('页面仅显示日志片段，请下载完整日志。', { exact: true }).waitFor();
    assert.match(await page.getByRole('link', { name: '下载完整日志 main-0', exact: true }).getAttribute('href'), /\/logs\/main-0$/);
    assert.equal(await page.getByText('测试通过', { exact: true }).count(), 0);
  } finally { await page.close(); }
});

test('load a minimal external workflow using the same generic defaults as the API', async () => {
  const presets = [{ id: 'minimal', name: '简洁外部预置', enabled: true, tags: { cycle: 'nightly' }, workflow: { jobs: [{ id: 'loaded', environment: 'server_env', steps: [{ type: 'python', path: 'ci/minimal.py' }] }] } }];
  const { page, errors } = await workspace({ presets });
  try {
    await page.getByRole('checkbox', { name: '新建任务' }).check();
    await page.getByText('选择外部预置', { exact: true }).click();
    await page.getByRole('button', { name: '使用预置 简洁外部预置', exact: true }).click();
    await page.getByRole('textbox', { name: 'loaded 主执行 1 · PR 路径', exact: true }).waitFor();
    assert.equal(await page.getByRole('textbox', { name: 'loaded 主执行 1 · 参数（JSON 数组）', exact: true }).inputValue(), '[]');
    assert.deepEqual(errors, []);
  } finally { await page.close(); }
});

test('administrators import a pinned PR catalog and enable one approved preset while seeing configuration errors', async () => {
  const presets = [];
  const { page } = await workspace({ admin: true, presets });
  const imports = [], enables = [];
  let configured = false;
  await page.route('**/api/presets/import', async route => {
    imports.push(route.request().postDataJSON());
    presets.push({ id: 'p1', name: '待启用样例', enabled: false, tags: { cycle: 'nightly' }, workflow: { jobs: [{ id: 'job1', environment: 'server_env', steps: [{ path: 'ci/run.sh' }] }] } });
    await route.fulfill({ status: 201, json: presets });
  });
  await page.route('**/api/presets/p1/enable', async route => {
    enables.push(route.request().method());
    if (!configured) return route.fulfill({ status: 409, json: { detail: 'HIVE_WORKFLOW_SAMPLE_PRESET_ID 未配置，当前不能启用样例。' } });
    presets[0].enabled = true; return route.fulfill({ json: presets[0] });
  });
  try {
    await page.getByRole('checkbox', { name: '新建任务' }).check();
    await page.getByRole('textbox', { name: 'vLLM-Ascend PR', exact: true }).fill('123');
    await page.getByRole('button', { name: /解析 PR/ }).click();
    await page.getByText(source.head_sha, { exact: true }).waitFor();
    await page.getByText('选择外部预置', { exact: true }).click();
    await page.getByRole('textbox', { name: '预置清单 PR 路径', exact: true }).fill('ci/catalog.json');
    await page.getByRole('button', { name: /导入预置清单/ }).click();
    await page.getByRole('button', { name: '启用预置 待启用样例', exact: true }).click();
    await page.getByRole('alert').filter({ hasText: 'HIVE_WORKFLOW_SAMPLE_PRESET_ID 未配置' }).waitFor();
    assert.equal(await page.getByRole('button', { name: '使用预置 待启用样例', exact: true }).isDisabled(), true);
    configured = true;
    await page.getByRole('button', { name: '启用预置 待启用样例', exact: true }).click();
    await page.getByText('已启用「待启用样例」。', { exact: true }).waitFor();
    assert.equal(await page.getByRole('button', { name: '使用预置 待启用样例', exact: true }).isEnabled(), true);
    assert.deepEqual(imports, [{ source: { pr: 123, head_sha: source.head_sha, vllm_sha: source.vllm_sha }, path: 'ci/catalog.json' }]);
    assert.deepEqual(enables, ['POST', 'POST']);
  } finally { await page.close(); }
});

test('cancellation and unknown job state remain pending confirmation in task details', async () => {
  const initialRuns = [{ id: 'pending-stop', name: '等待收尾任务', owner_name: 'alice', status: 'CANCELLING', space_id: 's1', created_at: '2026-09-12T10:00:00Z', spec: { source, jobs: [], environments: [] }, jobs: [{ id: 'unknown-job', name: '状态异常作业', status: 'UNKNOWN', logs: [] }] }];
  const { page } = await workspace({ initialRuns });
  try {
    await page.getByRole('button', { name: '查看任务 等待收尾任务', exact: true }).click();
    await page.getByText('取消处理中，等待所属进程退出与收尾复核，尚未确认资源归还。', { exact: true }).waitFor();
    await page.getByText('作业状态待核验，尚未确认执行结束；请查看原因与日志。', { exact: true }).waitFor();
    assert.equal(await page.getByText('已清理', { exact: true }).count(), 0);
  } finally { await page.close(); }
});
