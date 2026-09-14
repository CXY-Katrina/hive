import { before, after, test } from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import { chromium } from 'playwright';
import { fileURLToPath } from 'node:url';
import { mkdir } from 'node:fs/promises';

let server, browser, origin;
before(async () => {
  server = await createServer({ configFile: false, root: fileURLToPath(new URL('..', import.meta.url)), server: { host: '127.0.0.1', port: 0 }, plugins: [react(), {
    name: 'graph-test-page', configureServer(vite) {
      vite.middlewares.use('/__graph_test', async (_req, res) => {
        const html = await vite.transformIndexHtml('/__graph_test', '<!doctype html><html><head><meta charset="UTF-8"></head><body><div id="root"></div><script type="module" src="/tests/fixtures/workflowGraphHarness.tsx"></script></body></html>');
        res.setHeader('Content-Type', 'text/html'); res.end(html);
      });
    },
  }] });
  await server.listen();
  origin = `http://127.0.0.1:${server.httpServer.address().port}`;
  browser = await chromium.launch({ executablePath: process.env.HIVE_TEST_BROWSER || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
});
after(async () => { await browser?.close(); await server?.close(); });
const job = (id, dependencies = [], kind = 'batch') => ({ id, name: id === 'serve' ? '模型服务' : id === 'evaluate' ? '性能验证' : '结果检查', environment: kind === 'service' ? 'server_env' : 'client_env', kind, npu_count: kind === 'service' ? 2 : 0, ports: [], depends_on: dependencies.map(job_id => ({ job_id, condition: job_id === 'serve' ? 'ready' : 'succeeded' })), pre: [{ type: 'shell', path: 'checks/preflight.sh', args: [] }], steps: [{ type: 'python', path: 'tests/evaluate.py', args: [] }], post: [{ type: 'shell', path: 'checks/cleanup.sh', args: [] }], post_policy: 'always', ready: [], timeout_seconds: 300 });
async function open(jobs) {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  page.setDefaultTimeout(10000);
  await page.addInitScript(value => { window.graphJobs = value; }, jobs);
  await page.goto(origin + '/__graph_test');
  await page.locator('.workflow-graph-node').first().waitFor();
  return page;
}
async function value(page) { return JSON.parse(await page.getByTestId('graph-value').textContent()); }

test('ports choose service readiness automatically and a visible button removes its edge', async () => {
  const page = await open([job('serve', [], 'service'), job('evaluate')]);
  try {
    await page.getByRole('button', { name: '从 serve 连线', exact: true }).click();
    await page.getByRole('button', { name: '连线到 evaluate', exact: true }).click();
    assert.deepEqual((await value(page))[1].depends_on, [{ job_id: 'serve', condition: 'ready' }]);
    const remove = page.getByRole('button', { name: '删除依赖 serve → evaluate', exact: true });
    assert.match(await remove.textContent(), /删除/);
    await remove.click();
    assert.deepEqual((await value(page))[1].depends_on, []);
    await page.getByRole('button', { name: '从 evaluate 连线', exact: true }).click();
    await page.getByRole('button', { name: '连线到 serve', exact: true }).click();
    assert.deepEqual((await value(page))[0].depends_on, [{ job_id: 'evaluate', condition: 'succeeded' }]);
  } finally { await page.close(); }
});

test('nodes show pre/main/post steps and every dependency route avoids unrelated job blocks', async () => {
  const page = await open([job('serve', [], 'service'), job('evaluate', ['serve']), job('report', ['serve', 'evaluate']), job('parallel', ['serve'])]);
  try {
    assert.equal(await page.getByText('执行前检查', { exact: true }).count(), 4);
    assert.equal(await page.getByText('执行后检查', { exact: true }).count(), 4);
    assert.equal(await page.getByText('preflight.sh', { exact: true }).count(), 4);
    const collisions = await page.evaluate(() => {
      const nodes = [...document.querySelectorAll('[data-job-id]')].map(n => ({ id: n.dataset.jobId, rect: n.getBoundingClientRect() }));
      const failures = [];
      for (const path of document.querySelectorAll('path[data-edge-from]')) {
        const matrix = path.getScreenCTM();
        for (let length = 0; length <= path.getTotalLength(); length += 3) {
          const p = path.getPointAtLength(length), point = new DOMPoint(p.x, p.y).matrixTransform(matrix);
          for (const { id, rect } of nodes) if (![path.dataset.edgeFrom, path.dataset.edgeTo].includes(id) && point.x > rect.left && point.x < rect.right && point.y > rect.top && point.y < rect.bottom) failures.push(`${path.dataset.edgeFrom}->${path.dataset.edgeTo} crosses ${id}`);
        }
      }
      return { failures: [...new Set(failures)], edges: document.querySelectorAll('path[data-edge-from]').length };
    });
    assert.equal(collisions.edges, 4);
    assert.deepEqual(collisions.failures, []);
    await mkdir(fileURLToPath(new URL('../../data', import.meta.url)), { recursive: true });
    await page.screenshot({ path: fileURLToPath(new URL('../../data/workflow-graph-browser.png', import.meta.url)), fullPage: true });
  } finally { await page.close(); }
});

test('64 jobs keep a scrollable canvas and show attached file names for launch steps', async () => {
  const jobs = Array.from({ length: 64 }, (_, index) => job('task' + index,
    index < 8 ? [] : ['task' + (index - 8), ...(index % 8 ? ['task' + (index - 9)] : [])]));
  jobs[63].depends_on.push({ job_id: 'task0', condition: 'succeeded' });
  jobs[0].steps = [{ type: 'shell', path: '', args: [], launch: 'bash "${source_dir}/checks/run.sh"', files: [{ name: 'run.sh', content: '#!/bin/bash\ntrue\n' }] }];
  const page = await open(jobs);
  try {
    assert.equal(await page.locator('[data-job-id]').count(), 64);
    assert.equal(await page.getByText('run.sh', { exact: true }).count(), 1);
    const result = await page.evaluate(() => {
      const canvas = document.querySelector('.workflow-graph-scroll'), stage = document.querySelector('.workflow-graph-stage');
      const nodes = [...document.querySelectorAll('[data-job-id]')].map(n => ({ id: n.dataset.jobId, rect: n.getBoundingClientRect() }));
      let collisions = 0;
      for (const path of document.querySelectorAll('path[data-edge-from]')) {
        const matrix = path.getScreenCTM();
        for (let length = 0; length <= path.getTotalLength(); length += 12) {
          const p = path.getPointAtLength(length), point = new DOMPoint(p.x, p.y).matrixTransform(matrix);
          for (const { id, rect } of nodes) if (![path.dataset.edgeFrom, path.dataset.edgeTo].includes(id) && point.x > rect.left && point.x < rect.right && point.y > rect.top && point.y < rect.bottom) collisions++;
        }
      }
      return { collisions, overflow: getComputedStyle(canvas).overflow, viewportHeight: canvas.clientHeight, contentHeight: stage.clientHeight, viewportWidth: canvas.clientWidth, contentWidth: stage.clientWidth };
    });
    assert.equal(result.collisions, 0);
    assert.equal(result.overflow, 'auto');
    assert.ok(result.viewportHeight <= 760 && result.contentHeight > result.viewportHeight);
    assert.ok(result.contentWidth > result.viewportWidth);
  } finally { await page.close(); }
});

test('empty pre/post stages fit the card under the application styles', async () => {
  const only = job('empty_checks'); only.pre = []; only.post = [];
  const page = await open([only]);
  try {
    const fitting = await page.locator('[data-job-id]').evaluate(card => {
      const node = card.getBoundingClientRect();
      return [...card.querySelectorAll('.job-graph-phase')].every(stage => stage.getBoundingClientRect().bottom <= node.bottom);
    });
    assert.equal(fitting, true, 'Empty phases pushed execution or post checks outside the job card');
  } finally { await page.close(); }
});
