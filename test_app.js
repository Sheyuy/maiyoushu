'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const app = require('./app.js');
const output = path.join(__dirname, 'outputs');

if (!process.argv.includes('--browser')) {
  const selected = (code, name, extra = {}) => ({ code, name, isDefault: 1, quantity: 1, ...extra });
  const detail = { code: 'combo', name: '测试套餐', rounds: [
    { id: 1, quantity: 1, choices: [selected('main', '板烧鸡腿堡'), { code: 'other-main', name: '麦香鱼' }] },
    { id: 2, quantity: 1, choices: [selected('fries', '中薯条'), { code: 'corn', name: '小杯玉米杯' }, { code: 'wrap', name: '鸡肉卷' }] },
    { id: 3, quantity: 1, choices: [selected('cola', '可乐中杯'), { code: 'zero', name: '无糖可口可乐中杯' }] }
  ] };
  test('明确成功的官方 envelope 不受 code 白名单限制，失败与缺失数据被拒绝', () => {
    const wrap = structuredContent => ({ isError: false, structuredContent });
    assert.deepEqual(app.unwrapResult(wrap({ success: true, code: 10000, data: [] })), []);
    assert.throws(() => app.unwrapResult(wrap({ success: false, code: 200, message: '业务失败', data: [] })), /业务失败/);
    assert.throws(() => app.unwrapResult({ isError: true }), /官方服务/);
    assert.throws(() => app.unwrapResult(wrap({ success: true })), /缺少业务数据/);
    assert.throws(() => app.unwrapResult(wrap({ code: 999, data: [] })), /暂不可用/);
  });
  test('默认特调只传官方选中的 code、key 和 quantity', () => {
    assert.deepEqual(app.defaultMod({ modification: { items: [{ values: [
      { code: 'ice', selectedKey: '0-1', selectedQuantity: 1 },
      { code: 'ice', selectedKey: '0-0', selectedQuantity: 0 }
    ] }] } }), { values: [{ code: 'ice', key: '0-1', quantity: 1 }] });
    assert.throws(() => app.defaultMod({ modification: { items: [{ values: [{ code: 'x', selectedQuantity: 1 }] }] } }), /不完整/);
    assert.throws(() => app.defaultMod({ modification: { items: [{ minValues: 1, values: [] }] } }), /不完整/);
  });
  test('单改保留主食、套餐、人数，且 roundList 恰好一处变化', () => {
    const picks = app.defaults(detail), base = app.makePlan(detail, picks, 2);
    const variants = app.variants(detail, picks, 2);
    assert.ok(variants.length > 0);
    for (const p of variants) {
      assert.equal(p.items[0].productCode, base.items[0].productCode);
      assert.equal(p.items[0].quantity, 2);
      assert.deepEqual(p.items[0].roundList[0], base.items[0].roundList[0]);
      assert.equal(p.items[0].roundList.filter((r, i) => JSON.stringify(r) !== JSON.stringify(base.items[0].roundList[i])).length, 1);
      assert.ok(!p.parts.some(x => x.code === 'wrap' || x.code === 'other-main'));
    }
  });
  test('未知能量、蛋白质和钠不当作零；真实零可保留', () => {
    const parts = [{ name: 'A', quantity: 2 }];
    assert.equal(app.nutritionOf(parts, []).energy, null);
    const partial = app.nutritionOf(parts, [{ productName: 'A', energyKcal: 20, protein: null, sodium: '' }]);
    assert.equal(partial.energy, 40);
    assert.equal(partial.protein, null);
    assert.equal(partial.sodium, null);
    assert.equal(app.nutritionOf(parts, [{ productName: 'A', energyKcal: 0, protein: 0, sodium: 0 }]).energy, 0);
    assert.equal(app.nutritionOf(parts, [{ productName: 'A', energyKcal: 20 }, { productName: 'A', energyKcal: 30 }]).energy, null);
    assert.equal(app.nutritionOf(parts, [{ productName: 'A', energyKcal: 20, protein: 1 }, { productName: 'A', energyKcal: 20, protein: 2 }]).protein, null);
  });
  test('预算按本单真实总价比较，未知营养不能满足目标', () => {
    const target = { budget: 1, people: 1, calories: 850, avoid: [] };
    const plan = { price: 34.5, parts: [], nutrition: { energy: 600 } };
    assert.equal(app.satisfies(plan, target), false);
    assert.equal(target.budget, 1);
    assert.equal(app.satisfies({ ...plan, price: 0.5, nutrition: { energy: null } }, target), false);
  });
  test('营养表支持引号、逗号和 null', () => {
    const rows = app.parseNutrition('[1]{productName,energyKcal,protein,sodium}:\n  "A,B",123,null,0');
    assert.deepEqual(rows, [{ productName: 'A,B', energyKcal: '123', protein: null, sodium: '0' }]);
  });
} else {
  (async () => {
    const { chromium } = require('playwright');
    const browser = await chromium.launch({ headless: true });
    const context = await browser.newContext({ viewport: { width: 1440, height: 1100 }, deviceScaleFactor: 1, acceptDownloads: true });
    const page = await context.newPage();
    page.setDefaultTimeout(90000);
    const report = { browser: '真实 Edge / Playwright', startedAt: new Date().toISOString(), requests: [], responses: [], consoleErrors: [], pageErrors: [], checks: [] };
    const pending = new Set();
    page.on('pageerror', e => report.pageErrors.push(e.message));
    page.on('console', m => { if (m.type() === 'error') report.consoleErrors.push(m.text()); });
    await page.route('**/api/call', route => {
      const data = route.request().postDataJSON();
      const allowed = ['query-nearby-stores', 'query-meals', 'query-meal-detail', 'list-nutrition-foods', 'query-store-coupons', 'calculate-price'];
      assert.ok(allowed.includes(data.name), `禁止非只读工具 ${data.name}`);
      report.requests.push(data);
      return route.continue();
    });
    page.on('response', response => {
      if (!response.url().endsWith('/api/call')) return;
      const work = response.json().then(body => report.responses.push({ request: response.request().postDataJSON(), status: response.status(), body })).catch(e => report.consoleErrors.push(e.message));
      pending.add(work); work.finally(() => pending.delete(work));
    });
    const check = (name, value) => { assert.ok(value, name); report.checks.push(name); console.log('通过：' + name); };
    const idle = async () => { await page.waitForFunction(() => !document.getElementById('searchStores').disabled); };
    const snap = async name => page.screenshot({ path: path.join(output, name + '.png'), fullPage: true });
    const exportData = async name => {
      const download = page.waitForEvent('download');
      await page.locator('#exportResult').click();
      const file = path.join(output, name + '.json'); await (await download).saveAs(file);
      return JSON.parse(fs.readFileSync(file, 'utf8'));
    };
    try {
      await page.goto('http://127.0.0.1:8765', { waitUntil: 'domcontentloaded' });
      await page.waitForFunction(() => document.querySelectorAll('[data-store]').length > 0 || !document.getElementById('globalError').hidden);
      await idle();
      assert.equal(await page.locator('#globalError').isVisible(), false, await page.locator('#globalError').textContent());
      const storesResponse = report.responses.find(x => x.request.name === 'query-nearby-stores');
      const stores = app.unwrapResult(storesResponse.body.result);
      const index = stores.findIndex(s => String(s.storeCode) === '1450359');
      check('张江高科真实搜索命中1450359', index >= 0);
      await page.locator(`[data-store="${index}"]`).click();
      await snap('browser-01-stores');
      await page.locator('#loadMenu').click(); await idle();
      check('自动加载板烧套餐', (await page.locator('#detail h3').textContent()).includes('板烧鸡腿堡三件套'));
      await page.locator('#quoteCurrent').click(); await idle();
      report.currentQuote = await page.locator('#currentQuote').textContent();
      check('当前搭配真实核价34.50元', report.currentQuote.includes('34.50'));
      await snap('browser-02-current-quote');
      await page.locator('#generate').click(); await idle();
      check('生成真实核价结果', await page.locator('#step3').isVisible() && await page.locator('#results .plan').count() > 0);
      report.resultSummary = await page.locator('#resultSummary').textContent();
      const generated = await exportData('browser-real-results');
      report.generated = generated;
      await snap('browser-03-results');
      await page.locator('#results .plan').filter({ hasText: '板烧鸡腿堡' }).first().locator('[data-baseline]').click();
      const compareStart = report.requests.length;
      await page.locator('#compareOne').click(); await idle();
      const compareCalls = report.requests.slice(compareStart).filter(x => x.name === 'calculate-price');
      const baselineItem = compareCalls[0].arguments.items[0];
      for (const call of compareCalls.slice(1)) {
        assert.equal(call.arguments.storeCode, '1450359');
        assert.equal(call.arguments.items[0].productCode, baselineItem.productCode);
        assert.equal(call.arguments.items[0].quantity, baselineItem.quantity);
        assert.equal(call.arguments.items[0].roundList.filter((r, i) => JSON.stringify(r) !== JSON.stringify(baselineItem.roundList[i])).length, 1);
      }
      check('单改真实请求保留套餐、人数及其余round和默认特调', compareCalls.length > 1);
      report.compareProgress = await page.locator('#compareProgress').textContent();
      const compared = await exportData('browser-real-comparison');
      check('基线单改有真实重新核价结果', compared.comparisons.length > 0);
      const baseCalls = report.requests.filter(x => x.name === 'calculate-price');
      for (const p of compared.comparisons) {
        const base = compared.baseline.items, items = p.items;
        check('单改仅一个餐品名称改变：' + p.change.to, base.filter((x, i) => x.name !== items[i]?.name || x.quantity !== items[i]?.quantity).length === 1);
      }
      report.priceCallCount = baseCalls.length;
      await snap('browser-04-comparison');
      // 真实预算不足路径：不拦截核价，保留用户输入的一元预算。
      await page.locator('[data-step="2"]').last().click();
      await page.locator('#budget').fill('1');
      await page.locator('#generate').click(); await idle();
      report.lowBudgetSummary = await page.locator('#resultSummary').textContent();
      check('一元预算不夹值，明确显示不满足', (await page.locator('#resultContext').textContent()).includes('预算 ¥1 ') && report.lowBudgetSummary.includes('0 个同时满足目标') && (await page.locator('#results').textContent()).includes('超预算'));
      await snap('browser-05-low-budget');
      await page.locator('[data-step="2"]').last().click();
      await page.locator('#budget').fill('0');
      const beforeInvalid = report.requests.length;
      await page.locator('#generate').click(); await idle();
      check('非法预算显式报错且不发送核价', (await page.locator('#globalError').textContent()).includes('预算') && report.requests.length === beforeInvalid && await page.locator('#budget').inputValue() === '0');
      await snap('browser-06-invalid-budget');
      // 以下为明确标记的故障注入，不计入真实服务返回。
      await page.locator('#budget').fill('35');
      await page.route('**/api/call', async route => {
        const data = route.request().postDataJSON();
        if (data.name === 'calculate-price') return route.fulfill({ status: 502, contentType: 'application/json', body: JSON.stringify({ ok: false, message: '测试注入：核价服务不可用' }) });
        return route.fallback();
      });
      await page.locator('#quoteCurrent').click(); await idle();
      check('核价服务失败可见且控件恢复', (await page.locator('#globalError').textContent()).includes('核价服务不可用') && await page.locator('#quoteCurrent').isEnabled());
      await snap('browser-07-price-failure');
      await page.locator('#generate').click(); await idle();
      check('候选全部核价失败明确报错', (await page.locator('#globalError').textContent()).includes('候选均未核价成功'));
      await snap('browser-08-all-price-failure');
      await page.unroute('**/api/call');
      let priceIndex = 0;
      await page.route('**/api/call', async route => {
        const data = route.request().postDataJSON();
        assert.ok(['query-nearby-stores','query-meals','query-meal-detail','list-nutrition-foods','query-store-coupons','calculate-price'].includes(data.name));
        if (data.name === 'calculate-price' && ++priceIndex === 2) return route.fulfill({ status: 502, contentType: 'application/json', body: JSON.stringify({ ok: false, message: '测试注入：单个候选失败' }) });
        if (data.name === 'list-nutrition-foods' || data.name === 'query-store-coupons') return route.fulfill({ status: 502, contentType: 'application/json', body: JSON.stringify({ ok: false, message: '测试注入：参考数据不可用' }) });
        return route.continue();
      });
      await page.locator('[data-step="1"]').first().click();
      await page.locator('#loadMenu').click(); await idle();
      check('优惠失败与无券分开显示', (await page.locator('#couponStatus').textContent()).includes('优惠查询失败'));
      check('营养不可用显示缺失', (await page.locator('#detail').textContent()).includes('营养暂缺'));
      await page.locator('#generate').click(); await idle();
      const unknown = await exportData('browser-injected-missing-nutrition');
      check('营养缺失不填0也不宣称满足能量目标', unknown.candidates.every(p => p.energyKcal === null && p.protein === null) && (await page.locator('#resultSummary').textContent()).includes('0 个同时满足目标') && (await page.locator('#results').textContent()).includes('未知'));
      check('单个候选核价失败已排除且其余正常展示', (await page.locator('#resultSummary').textContent()).includes('1 个核价失败，已排除'));
      await snap('browser-09-missing-nutrition-coupons');
      await page.setViewportSize({ width: 390, height: 844 });
      await snap('browser-10-mobile');
      report.mobileOverflow = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, viewport: innerWidth }));
      check('移动端页面无整体横向溢出', report.mobileOverflow.scroll <= report.mobileOverflow.viewport);
      check('浏览器无未捕获JS异常', report.pageErrors.length === 0);
      report.completedAt = new Date().toISOString();
    } catch (e) {
      report.failure = e.stack; await snap('browser-failure'); throw e;
    } finally {
      await Promise.allSettled([...pending]);
      fs.writeFileSync(path.join(output, 'browser-review.json'), JSON.stringify(report, null, 2));
      await browser.close();
    }
  })().catch(e => { console.error(e); process.exitCode = 1; });
}
