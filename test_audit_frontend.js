'use strict';
// 全面审计新增回归：覆盖validateTarget/validateDetail/validateMenu、营养歧义、
// 约束检查与可替换项限定。与test_app.js并列，不修改其内容。
const assert = require('node:assert/strict');
const { test } = require('node:test');
const app = require('./app.js');

const detail = { code: 'combo', name: '测试套餐', rounds: [
  { id: 1, quantity: 1, choices: [{ code: 'main', name: '板烧鸡腿堡', isDefault: 1, quantity: 1 }, { code: 'fish', name: '麦香鱼' }] },
  { id: 2, quantity: 1, choices: [{ code: 'fries', name: '中薯条', isDefault: 1, quantity: 1 }, { code: 'corn', name: '小杯玉米杯' }] },
  { id: 3, quantity: 1, choices: [{ code: 'cola', name: '可乐中杯', isDefault: 1, quantity: 1 }, { code: 'zero', name: '无糖可口可乐中杯' }] }
] };

test('validateTarget 拒绝越界预算、非整数人数与非步长能量', () => {
  const base = { budget: 35, people: 1, calories: 850, priority: 'balanced', drink: false, fries: false, avoid: [] };
  assert.deepEqual(app.validateTarget({ ...base }), base);
  for (const patch of [{ budget: 0 }, { budget: 1001 }, { budget: Number.NaN }, { people: 0 }, { people: 5 }, { people: 1.5 }, { calories: 199 }, { calories: 1601 }, { calories: 851 }, { priority: 'unknown' }]) {
    assert.throws(() => app.validateTarget({ ...base, ...patch }), /预算|人数|能量|优先级/, JSON.stringify(patch));
  }
});

test('validateDetail 拒绝结构缺失、重复编码与多份轮次', () => {
  assert.equal(app.validateDetail(detail), detail);
  for (const bad of [
    { code: '', name: 'x' },
    { code: 'x' },
    { code: 'x', name: 'y', rounds: [{ id: 1, quantity: 2, choices: [{ code: 'a', name: 'A' }] }] },
    { code: 'x', name: 'y', rounds: [{ id: 1, quantity: 1, choices: [{ code: 'a', name: 'A' }, { code: 'a', name: 'B' }] }] },
    { code: 'x', name: 'y', rounds: [{ id: 1, quantity: 1, choices: [{ code: 'a', name: 'A' }] }, { id: 1, quantity: 1, choices: [{ code: 'b', name: 'B' }] }] },
    { code: 'x', name: 'y', rounds: [{ id: 1, quantity: 1, choices: [{ code: 'a' }] }] },
    { code: 'x', name: 'y', rounds: 'no' }
  ]) {
    assert.throws(() => app.validateDetail(bad), /套餐|餐品|详情/, JSON.stringify(bad).slice(0, 60));
  }
});

test('validateMenu 拒绝缺失字段与非对象餐品，空菜单结构合法', () => {
  const menu = { meals: { '1': { name: '板烧鸡腿堡三件套', currentPrice: '34.5' } }, categories: [{ code: 'c', name: '分类', meals: [{ code: '1', name: '板烧鸡腿堡三件套', tags: [] }] }] };
  assert.equal(app.validateMenu(menu), menu);
  // 空菜单是合法结构：门店当前无在售条目时接口可能返回空，界面走空状态而不是报错。
  assert.deepEqual(app.validateMenu({ meals: {}, categories: [] }), { meals: {}, categories: [] });
  for (const bad of [null, {}, { meals: {}, categories: 'no' }, { meals: { '': {} }, categories: [] }, { meals: { '1': null }, categories: [] }, { meals: { '1': { name: '  ' } }, categories: [] }, { meals: { '1': { name: 'A' } }, categories: [{}] }]) {
    assert.throws(() => app.validateMenu(bad), /菜单/, JSON.stringify(bad).slice(0, 60));
  }
});

test('营养重名歧义：展示标签不同但原名不同的同一规格不合并，也不计0', () => {
  // 两个原始名去标签后相同但不是同一字符串，必须判未知而不是取其一。
  const rows = [{ productName: '中薯条【大份】', energyKcal: 289 }, { productName: '中薯条', energyKcal: 302 }];
  const n = app.nutritionOf([{ name: '中薯条', quantity: 1 }], rows);
  assert.equal(n.energy, null);
  assert.ok(n.missing.includes('中薯条'));
  // 明确的单一匹配仍然求和。
  assert.equal(app.nutritionOf([{ name: '中薯条', quantity: 2 }], [{ productName: '中薯条', energyKcal: 289 }]).energy, 578);
});

test('parseNutrition 拒绝表头缺失、字段重复与引号不配对', () => {
  assert.throws(() => app.parseNutrition('[1]{energyKcal}:\n  1'), /表头/);
  assert.throws(() => app.parseNutrition('[1]{productName,productName}:\n  A'), /表头/);
  assert.throws(() => app.parseNutrition('[1]{productName,energyKcal}:\n  "A,1'), /行格式/);
  assert.throws(() => app.parseNutrition(42), /格式/);
});

test('constraintIssues 区分价格未验证、能量未验证与真实超额', () => {
  const target = { budget: 30, people: 2, calories: 800, drink: true, fries: true, avoid: [] };
  const ok = { price: 20, parts: [{ name: '可乐中杯', quantity: 2 }, { name: '中薯条', quantity: 2 }], nutrition: { energy: 1500, protein: null, sodium: null, missing: [] } };
  assert.deepEqual(app.constraintIssues(ok, target), []);
  assert.equal(app.satisfies(ok, target), true);
  const badPrice = { ...ok, price: Number.NaN };
  assert.ok(app.constraintIssues(badPrice, target).includes('价格未能验证'));
  const over = { ...ok, price: 31, nutrition: { energy: 1700, protein: null, sodium: null, missing: [] } };
  const issues = app.constraintIssues(over, target);
  assert.ok(issues.some(i => i.startsWith('超预算')));
  assert.ok(issues.some(i => i.includes('每人超出约')));
  const noEnergy = { ...ok, nutrition: { energy: null, protein: null, sodium: null, missing: ['中薯条'] } };
  assert.ok(app.constraintIssues(noEnergy, target).includes('能量未能验证'));
  assert.equal(app.satisfies(noEnergy, target), false);
  const noDrink = { ...ok, parts: [{ name: '中薯条', quantity: 2 }] };
  assert.ok(app.constraintIssues(noDrink, target).includes('饮料份数不足'));
});

test('单项替换只改饮料/薯条/玉米类，含主食的轮次整体跳过', () => {
  const picks = app.defaults(detail);
  const all = app.variants(detail, picks, 1);
  assert.ok(all.length > 0);
  for (const p of all) {
    assert.equal(p.items[0].roundList.filter((r, i) => JSON.stringify(r) !== JSON.stringify(app.makePlan(detail, picks, 1).items[0].roundList[i])).length, 1);
    assert.equal(p.items[0].roundList[0].comboItemList[0].code, 'main');
  }
  // 主食轮出现替代主食时，该轮不参与替换。
  const withMainAlt = { ...detail, rounds: detail.rounds.map(r => r.id === 2 ? { ...r, choices: [...r.choices, { code: 'burger2', name: '麦辣鸡腿堡' }] } : r) };
  for (const p of app.variants(withMainAlt, app.defaults(withMainAlt), 1)) {
    assert.ok(!p.parts.some(x => x.code === 'burger2'));
  }
});

test('makePlan 校验人数与选项归属，拒绝跨套餐选项', () => {
  const picks = app.defaults(detail);
  assert.throws(() => app.makePlan(detail, picks, 0), /人数/);
  assert.throws(() => app.makePlan(detail, picks, 5), /人数/);
  const foreign = [{ code: 'fish', name: '麦香鱼' }, picks[1], picks[2]];
  assert.throws(() => app.makePlan(detail, foreign, 1), /选项|套餐/);
  const short = [picks[0]];
  assert.throws(() => app.makePlan(detail, short, 1), /选择尚未完成/);
});
