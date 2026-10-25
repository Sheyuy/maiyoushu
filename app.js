/* 麦有数：实时菜单、确定性候选生成与只改一处核价。 */
(function (root) {
  'use strict';
  const normalize = s => String(s || '').normalize('NFKC').replace(/【[^】]*】/g, '').replace(/\s+/g, '').trim();
  function csv(line) { const out=[]; let value='', quoted=false; for(let i=0;i<line.length;i++){ const c=line[i]; if(c==='"'){if(quoted && line[i+1]==='"'){value+='"';i++;}else quoted=!quoted;}else if(c===','&&!quoted){out.push(value);value='';}else value+=c;} out.push(value); return out; }
  const record = v => v !== null && typeof v === 'object' && !Array.isArray(v);
  const identifier = v => (typeof v === 'string' && v.trim() !== '') || (typeof v === 'number' && Number.isFinite(v));
  const named = v => record(v) && identifier(v.code) && typeof v.name === 'string' && v.name.trim() !== '';
  const numeric = v => (typeof v === 'number' || (typeof v === 'string' && v.trim() !== '')) && Number.isFinite(Number(v)) && Number(v) >= 0;
  function validateTarget(t) {
    if (!Number.isFinite(t.budget) || t.budget < 1 || t.budget > 1000) throw new Error('请输入1至1000元的本单预算');
    if (!Number.isInteger(t.people) || t.people < 1 || t.people > 4) throw new Error('用餐人数须为1至4的整数');
    if (!Number.isInteger(t.calories) || t.calories < 200 || t.calories > 1600 || t.calories % 25 !== 0) throw new Error('每人能量上限须为200至1600千卡，步长25');
    if (!['balanced', 'price', 'energy', 'protein'].includes(t.priority)) throw new Error('请选择有效的比较优先级');
    return t;
  }
  function validateDetail(d) {
    if (!named(d) || (d.rounds !== undefined && !Array.isArray(d.rounds))) throw new Error('商品详情格式不完整');
    const ids = new Set();
    for (const r of d.rounds || []) {
      if (!record(r) || !identifier(r.id) || ids.has(String(r.id)) || Number(r.quantity) !== 1 || !Array.isArray(r.choices) || !r.choices.length) throw new Error('此套餐有多份或复杂选配，暂不支持自动比较');
      ids.add(String(r.id));
      if (r.choices.some(c => !named(c)) || new Set(r.choices.map(c => String(c.code))).size !== r.choices.length) throw new Error('套餐选项格式不完整或编码重复');
    }
    return d;
  }
  function validateMenu(menu) {
    if (!record(menu) || !record(menu.meals) || !Array.isArray(menu.categories) || Object.entries(menu.meals).some(([code, m]) => !code || !record(m) || typeof m.name !== 'string' || !m.name.trim()) || menu.categories.some(c => !record(c) || !Array.isArray(c.meals) || c.meals.some(m => !record(m) || !identifier(m.code) || (m.tags !== undefined && (!Array.isArray(m.tags) || m.tags.some(t => typeof t !== 'string')))))) throw new Error('在售菜单格式无法识别');
    return menu;
  }
  function parseNutrition(text) {
    if(Array.isArray(text)) {
      if (text.some(row => !record(row) || typeof row.productName !== 'string' || !row.productName.trim())) throw new Error('营养数据行格式无法识别');
      return text;
    }
    if(typeof text!=='string') throw new Error('官方营养数据格式暂不支持');
    const lines=text.split(/\r?\n/), header=lines.findIndex(l=>/^\s*\[\d+\]\{/.test(l));
    if(header<0) throw new Error('无法识别官方营养数据表头');
    const fields=lines[header].match(/\{([^}]+)\}/)[1].split(',').map(f=>f.trim());
    if (!fields.includes('productName') || new Set(fields).size !== fields.length) throw new Error('营养数据表头不完整');
    return parseNutrition(lines.slice(header+1).filter(l=>l.trim()).map(line=>{const values=csv(line.trim()); if(values.length!==fields.length || (line.match(/"/g)||[]).length%2) throw new Error('营养数据行格式无法识别'); return Object.fromEntries(fields.map((f,i)=>[f,values[i]==='null'?null:values[i]]));}));
  }
  function nutritionOf(items, rows) {
    const sums={energyKcal:0,protein:0,sodium:0},unknown=new Set(),missing=[];
    for(const item of items){
      const matched=(Array.isArray(rows)?rows:[]).filter(n=>record(n)&&typeof n.productName==='string'&&normalize(n.productName)===normalize(item.name));
      // 去除展示标签后重名但原名不同，无法可靠判断是否为同一份规格。
      const ambiguous = new Set(matched.map(n=>String(n.productName).normalize('NFKC').replace(/\s+/g,''))).size > 1;
      for(const field of Object.keys(sums)){
        const valid=matched.filter(n=>numeric(n[field]));
        if(ambiguous||!Number.isInteger(item.quantity)||item.quantity<1||!matched.length||valid.length!==matched.length||new Set(valid.map(n=>Number(n[field]))).size!==1){unknown.add(field);if(field==='energyKcal')missing.push(item.name);}
        else sums[field]+=Number(valid[0][field])*item.quantity;
      }
    }
    return {energy:unknown.has('energyKcal')?null:sums.energyKcal,knownEnergy:sums.energyKcal,protein:unknown.has('protein')?null:sums.protein,sodium:unknown.has('sodium')?null:sums.sodium,missing};
  }
  function defaultMod(choice) {
    const invalid = () => { throw new Error('默认特调选项信息不完整'); };
    if (!record(choice)) return invalid();
    if (choice.modification == null) { if (choice.supportModify) return invalid(); return undefined; }
    if (!record(choice.modification) || !Array.isArray(choice.modification.items)) return invalid();
    const values=choice.modification.items.flatMap(group=>{
      if (!record(group) || !Array.isArray(group.values) || group.values.some(v=>!record(v) || (v.selectedQuantity !== undefined && (!numeric(v.selectedQuantity) || !Number.isInteger(Number(v.selectedQuantity)))))) return invalid();
      const selected=group.values.filter(v=>Number(v.selectedQuantity)>0), count=selected.reduce((s,v)=>s+Number(v.selectedQuantity),0);
      if ((group.minValues !== undefined && !numeric(group.minValues)) || (group.maxValues !== undefined && !numeric(group.maxValues)) || count<Number(group.minValues||0) || (group.maxValues !== undefined && count>Number(group.maxValues))) return invalid();
      return selected.map(v=>({code:identifier(v.code)?String(v.code):'',key:v.selectedKey,quantity:Number(v.selectedQuantity)}));
    });
    if(values.some(v=>!v.code||!identifier(v.key)||!Number.isInteger(v.quantity)||v.quantity<1)) return invalid();
    if(choice.supportModify && !values.length) return invalid();
    return values.length?{values}:undefined;
  }
  function defaults(detail) {
    validateDetail(detail);
    return (detail.rounds||[]).map(r=>{const list=r.choices.filter(c=>Number(c.isDefault)===1&&Number(c.quantity)>0);if(list.length!==1||Number(list[0].quantity)!==1) throw new Error('此套餐有多份或复杂选配，暂不支持自动比较');defaultMod(list[0]);return list[0];});
  }
  function makePlan(detail, choices, people=1, title='当前搭配') {
    validateDetail(detail);
    if(!Number.isInteger(people)||people<1||people>4)throw new Error('用餐人数须为1至4的整数');
    if(!Array.isArray(choices)||choices.length!==(detail.rounds||[]).length||(detail.rounds||[]).some((r,i)=>!r.choices.includes(choices[i])))throw new Error('套餐选择尚未完成或选项不属于此套餐');
    const item={productCode:String(detail.code),quantity:people};
    let parts=[];
    if(detail.rounds?.length){if(choices.length!==detail.rounds.length)throw new Error('套餐选择尚未完成');item.roundList=detail.rounds.map((r,i)=>{const c=choices[i], selected={code:String(c.code),quantity:1},mod=defaultMod(c);if(mod)selected.modification=mod;parts.push({name:c.name,code:String(c.code),quantity:people,supportModify:!!c.supportModify});return{round:String(r.id),comboItemList:[selected]};});}
    else{const mod=defaultMod(detail);if(mod)item.modification=mod;parts=[{name:detail.name,code:String(detail.code),quantity:people,supportModify:!!detail.supportModify}];}
    return {title,items:[item],parts,detail,choices:[...choices],people};
  }
  const keyOf = p => JSON.stringify(p.items);
  const drink = n => /可乐|雪碧|茶|咖啡|美式|奶|豆浆|果汁|纯悦|柠檬|雪冰/.test(n);
  const fries = n => /薯条/.test(n);
  const mainDish = n => /堡|麦满分|卷|粥|麦香鱼|麦香鸡/.test(n);
  const replaceable = n => !mainDish(n) && (drink(n) || fries(n) || /玉米杯/.test(n));
  function variants(detail, selected, people) {
    const all=[];
    for(let i=0;i<(detail.rounds||[]).length;i++){
      const round=detail.rounds[i],current=selected[i];
      if(!current||!replaceable(current.name)||round.choices.some(c=>mainDish(c.name))) continue;
      const alternatives=round.choices.filter(c=>String(c.code)!==String(current.code)&&replaceable(c.name)).sort((a,b)=>{
        const score=c=>(/无糖可口可乐中杯|小杯玉米杯|中薯条|小薯条/.test(c.name)?0:1);return score(a)-score(b);
      }).slice(0,3);
      for(const choice of alternatives){const picks=[...selected];picks[i]=choice;try{const p=makePlan(detail,picks,people,`仅将${current.name}换成${choice.name}`);p.change={from:current.name,to:choice.name,round:String(round.id)};all.push(p);}catch{ /* 无法完整表达默认配方的选项不纳入候选。 */ }}
    }
    return all;
  }
  function constraintIssues(plan, target) {
    const n=plan.nutrition, issues=[];
    if(!Number.isFinite(plan.price)||plan.price<0)issues.push('价格未能验证');
    else if(plan.price>target.budget)issues.push(`超预算 ¥${(plan.price-target.budget).toFixed(2)}`);
    if(!Number.isFinite(n.energy)||n.energy<0)issues.push('能量未能验证');
    else if(n.energy>target.calories*target.people)issues.push(`每人超出约 ${Math.ceil(n.energy/target.people-target.calories)} 千卡`);
    const count=predicate=>plan.parts.filter(p=>predicate(p.name)).reduce((s,p)=>s+p.quantity,0);
    if(target.drink&&count(drink)<target.people)issues.push('饮料份数不足');
    if(target.fries&&count(fries)<target.people)issues.push('薯条份数不足');
    if(plan.parts.some(p=>(target.avoid||[]).some(word=>normalize(p.name).includes(normalize(word)))))issues.push('包含避开关键词');
    return issues;
  }
  function satisfies(plan, target){return constraintIssues(plan,target).length===0;}
  function unwrapResult(result){
    if(!result||result.isError)throw new Error('官方服务未能完成该查询，请调整方案后重试');
    const data=result.structuredContent;
    if(!record(data)||data.success===false||(data.success!==true&&data.code!==undefined&&![0,'0',200,'200'].includes(data.code)))throw new Error(data?.message||'官方服务返回的数据暂不可用');
    if(!Object.hasOwn(data,'data'))throw new Error('官方响应缺少业务数据');
    return data.data;
  }
  const lib={parseNutrition,nutritionOf,defaultMod,defaults,makePlan,variants,satisfies,constraintIssues,validateTarget,validateMenu,validateDetail,normalize,keyOf,unwrapResult};
  if(typeof module!=='undefined'&&module.exports)module.exports=lib;
  root.MaiYouShu=lib;
  if(typeof document==='undefined')return;
  const $=id=>document.getElementById(id), esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const state={stores:[],store:null,menu:null,nutrition:[],details:new Map(),detail:null,choices:[],quotes:[],baseline:null,comparisons:[],coupons:null,couponError:false,busy:false,version:0,connected:false};
  function error(e){$('globalError').textContent=e instanceof Error?e.message:String(e);$('globalError').hidden=false;}
  function clearError(){$('globalError').hidden=true;}
  function toast(message){$('toast').textContent=message;$('toast').hidden=false;setTimeout(()=>$('toast').hidden=true,3500);}
  function progress(id,text,busy=false){$(id).textContent=text;$(id).setAttribute('aria-busy',String(busy));}
  const apiToken=()=>document.querySelector('meta[name="maiyoushu-token"]')?.content||'';
  async function api(name,args){const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),65000);try{const response=await fetch('/api/call',{method:'POST',headers:{'Content-Type':'application/json','X-Maiyoushu-Token':apiToken()},body:JSON.stringify({name,arguments:args}),signal:controller.signal});const envelope=await response.json();if(!response.ok||!record(envelope)||envelope.ok!==true)throw new Error(envelope?.message||'查询未完成，请重试');return unwrapResult(envelope.result);}catch(e){if(e.name==='AbortError')throw new Error('查询超时，请稍后重试');if(e instanceof TypeError)throw new Error('无法连接本机服务，请确认服务仍在运行');throw e;}finally{clearTimeout(timer);}}
  const context=()=>({storeCode:String(state.store.storeCode),orderType:1,beType:1});
  function syncControls(){
    $('loadMenu').disabled=state.busy||!state.store;
    for(const id of ['generate','quoteCurrent','refreshResults'])$(id).disabled=state.busy||!state.detail;
    $('compareOne').disabled=state.busy||!state.baseline?.detail?.rounds?.length;
    $('exportResult').disabled=state.busy||!state.quotes.length;
    document.querySelectorAll('[data-step="2"]').forEach(el=>el.disabled=state.busy||!state.menu);
    document.querySelectorAll('[data-step="3"]').forEach(el=>el.disabled=state.busy||!state.quotes.length);
  }
  function setBusy(value){state.busy=value;document.querySelectorAll('button,input,select').forEach(el=>el.disabled=value);syncControls();}
  function invalidate(message='搭配或目标已改变，请重新核价。'){
    state.version++;state.quotes=[];state.displayed=[];state.baseline=null;state.comparisons=[];state.target=null;state.checkedAt=null;state.failed=0;
    for(const id of ['results','comparisonTable','resultContext','resultSummary','sourceTime','currentQuote'])$(id).textContent='';
    $('baselineLabel').textContent='尚未选定基线';progress('compareProgress','');progress('generateProgress',message);syncControls();
    if(!$('step3').hidden)go(state.menu?2:1,true);
  }
  async function run(fn){if(state.busy)return;clearError();setBusy(true);try{await fn();}catch(e){error(e);}finally{setBusy(false);}}
  function go(step, internal=false){if(state.busy&&!internal)return;if(step===2&&!state.menu){toast('先选择门店并加载菜单');return;}if(step===3&&!state.quotes.length){toast('先完成一轮候选核价');return;}document.querySelectorAll('.panel').forEach((el,i)=>el.hidden=i+1!==step);document.querySelectorAll('[data-step]').forEach(el=>{el.classList.toggle('active',Number(el.dataset.step)===step);if(Number(el.dataset.step)===step)el.setAttribute('aria-current','step');else el.removeAttribute('aria-current');});}
  function target(){return validateTarget({budget:Number($('budget').value),people:Number($('people').value),calories:Number($('calories').value),priority:$('priority').value,drink:$('needDrink').checked,fries:$('needFries').checked,avoid:$('avoid').value.split(/[,，、;；]/).map(s=>s.trim()).filter(Boolean)});}
  async function findStores(){progress('storeProgress','正在查询附近门店…',true);try{const city=$('city').value.trim(),keyword=$('landmark').value.trim();if(!city||!keyword)throw new Error('请填写城市和地标');const stores=await api('query-nearby-stores',{beType:1,searchType:2,city,keyword});if(!Array.isArray(stores)||stores.some(s=>!record(s)||!identifier(s.storeCode)||typeof s.storeName!=='string'||!s.storeName.trim()))throw new Error('门店返回格式无法识别');invalidate('');state.stores=stores;state.store=null;state.menu=null;state.detail=null;state.quotes=[];state.baseline=null;state.details.clear();state.version++;$('selectedStore').textContent='请选择一家门店';renderStores();progress('storeProgress',stores.length?`找到 ${stores.length} 家门店，请点击选择`:'当前地标没有找到门店，请换一个地标');}finally{$('storeProgress').setAttribute('aria-busy','false');}}
  function renderStores(){$('stores').innerHTML=state.stores.map((s,i)=>`<button class="store ${state.store===s?'selected':''}" data-store="${i}" aria-pressed="${state.store===s}"><span class="store-name">${esc(s.storeName)}</span><span class="store-check">${state.store===s?'✓':''}</span><span class="store-distance">${Number.isFinite(Number(s.distance))?`${esc(s.distance)} 米`:'距离未返回'}</span><span class="store-address">${esc(s.address)}</span><span class="store-hours">${esc(s.businessStatus)} · ${esc(s.businessStartTime||'')}—${esc(s.businessEndTime||'')}</span></button>`).join('');}
  function restricted(code,meal){const tags=state.menu.categories.flatMap(c=>c.meals.filter(m=>String(m.code)===code).flatMap(m=>m.tags||[]));return /麦金卡|早餐卡|买卡|购卡/.test([meal.name,meal.discountType,...tags].join(' '))||!!meal.withOrder;}
  function availableMeals(){return Object.entries(state.menu.meals).filter(([code,m])=>!restricted(code,m));}
  function renderCatalog(){const search=normalize($('menuSearch').value);const rows=availableMeals().filter(([,m])=>!search||normalize(m.name).includes(search)).sort((a,b)=>Number(!/板烧鸡腿堡三件套|随心配/.test(a[1].name))-Number(!/板烧鸡腿堡三件套|随心配/.test(b[1].name)));$('catalog').innerHTML=rows.length?rows.map(([code,m])=>`<button class="meal ${state.detail?.code===code?'selected':''}" data-meal="${esc(code)}">${/^https:\/\/[^/]*mcd\.cn\//.test(m.image||'')?`<img src="${esc(m.image)}" alt="" loading="lazy" referrerpolicy="no-referrer">`:''}<span><strong>${esc(m.name)}</strong><small>菜单参考 ¥${esc(m.currentPrice)} · 点击选配</small></span></button>`).join(''):'<div class="empty">没有匹配的餐品，换个关键词试试。</div>';}
  async function detailFor(code){code=String(code);if(!state.details.has(code)){const detail=await api('query-meal-detail',{...context(),code});validateDetail(detail);if(String(detail.code)!==code)throw new Error('商品详情编码与所选餐品不一致');state.details.set(code,detail);}return state.details.get(code);}
  async function pickMeal(code){invalidate();state.detail=null;state.choices=[];$('detail').hidden=true;syncControls();progress('detailProgress','正在读取套餐组成与默认搭配…',true);try{const detail=await detailFor(code);const choices=defaults(detail);makePlan(detail,choices);state.detail=detail;state.choices=choices;$('currentQuote').textContent='';state.quotes=[];state.baseline=null;renderCatalog();renderDetail();progress('detailProgress','已读取官方默认搭配，可点击替换项');}finally{$('detailProgress').setAttribute('aria-busy','false');}}
  function renderDetail(){const d=state.detail;$('detail').hidden=false;$('detail').innerHTML=`<h3>${esc(d.name)}${d.supportModify?'【可特调】':''}</h3><p class="small muted">${d.rounds?.length?'默认搭配：'+esc(defaults(d).map(c=>c.name).join(' + ')):'标准单品，每人一份。'}。特调保持标准配方。</p>`+(d.rounds||[]).map((r,i)=>`<div class="round"><label for="round-${i}">${esc(r.name||'套餐选项 '+(i+1))}</label><select id="round-${i}" data-round="${i}">${r.choices.map(c=>`<option value="${esc(c.code)}" ${state.choices[i]?.code===c.code?'selected':''}>${esc(c.name)}${c.supportModify?'【可特调】':''} ${esc(c.diffPrice||'')}</option>`).join('')}</select></div>`).join('')+`<div class="notice small">${esc(nutritionText(nutritionOf(makePlan(d,state.choices).parts,state.nutrition)))}。标准份参考。</div>`;}
  function nutritionText(n){return n.energy===null?`营养暂缺：${n.missing.join('、')}`:`约 ${Math.round(n.energy)} 千卡 · 蛋白质 ${n.protein===null?'未知':Math.round(n.protein)+' 克'}`;}
  async function loadMenu(){invalidate('');state.menu=null;state.detail=null;state.details.clear();$('detail').hidden=true;progress('storeProgress','正在读取门店菜单、营养与适用优惠…',true);try{const [menu,nutrition,coupons]=await Promise.allSettled([api('query-meals',context()),api('list-nutrition-foods',{}),api('query-store-coupons',context())]);if(menu.status!=='fulfilled')throw menu.reason;state.menu=validateMenu(menu.value);state.details.clear();state.nutrition=[];let nutritionError=nutrition.status==='rejected';if(!nutritionError){try{state.nutrition=parseNutrition(nutrition.value);}catch{nutritionError=true;}}state.couponError=coupons.status==='rejected'||!Array.isArray(coupons.value);state.coupons=state.couponError?null:coupons.value;state.detail=null;state.quotes=[];state.baseline=null;$('detail').hidden=true;$('storeContext').textContent=state.store.storeName+' · 即时到店自取';$('couponStatus').textContent=state.couponError?'优惠查询失败，不能判断是否有券':Array.isArray(state.coupons)&&!state.coupons.length?'当前门店无适用券':'已查询优惠 · 暂未自动套券';$('menuNotice').textContent=`当前门店 ${Object.keys(state.menu.meals).length} 个在售条目，购卡条件商品不参与自动比较。${nutritionError?'营养查询失败或格式异常，本轮无法验证能量上限。':'标准配方、标准份营养。'}网页暂不自动使用优惠券；套餐/促销实际金额由逐项核价确认。`;$('menuSearch').value='';renderCatalog();progress('storeProgress','菜单已加载');state.busy=false;go(2);state.busy=true;const first=availableMeals().find(([,m])=>m.name==='板烧鸡腿堡三件套')||availableMeals()[0];if(first)await pickMeal(first[0]);}finally{$('storeProgress').setAttribute('aria-busy','false');}}
  async function quote(plan){const data=await api('calculate-price',{...context(),items:plan.items});if(!record(data)||!Number.isFinite(data.price)||data.price<0)throw new Error('核价响应没有有效应付金额');const price=data.price/100,discount=Number.isFinite(data.discount)?data.discount/100:null;return{...plan,price,discount,nutrition:nutritionOf(plan.parts,state.nutrition),at:new Date().toISOString()};}
  async function quoteCurrent(){const t=target();progress('currentQuote','正在核价…',true);try{const q=await quote(makePlan(state.detail,state.choices,t.people));progress('currentQuote',`实际试算 ¥${q.price.toFixed(2)} · ${nutritionText(q.nutrition)}`);}catch(e){progress('currentQuote','核价未完成，请重试');throw e;}finally{$('currentQuote').setAttribute('aria-busy','false');}}
  async function candidates(t){const out=[], seen=new Set();const add=p=>{const k=keyOf(p);if(!seen.has(k)&&out.length<22){seen.add(k);out.push(p);}};const primary=makePlan(state.detail,state.choices,t.people,'当前搭配');add(primary);variants(state.detail,state.choices,t.people).forEach(add);
    // 在选中主套餐中组合低能量配菜与饮料，核价后再判断是否满足目标。
    const lighter=[...state.choices];(state.detail.rounds||[]).forEach((r,i)=>{if(!replaceable(lighter[i]?.name||'')||r.choices.some(c=>mainDish(c.name)))return;const c=r.choices.find(c=>/无糖可口可乐中杯|小杯玉米杯/.test(c.name));if(c)lighter[i]=c;});if(lighter.length){try{add(makePlan(state.detail,lighter,t.people,'同主食的另一种搭配'));}catch{state.candidateFailures++;}}
    const alternatives=availableMeals().filter(([code,m])=>String(code)!==String(state.detail.code)&&(/三件套|随心配/.test(m.name))).sort((a,b)=>Number(a[1].currentPrice)-Number(b[1].currentPrice)).slice(0,4);
    const pairPlans=[];
    for(const [code] of alternatives){try{const d=await detailFor(code),picks=defaults(d);let p=makePlan(d,picks,t.people,d.name);add(p);if(/随心配/.test(d.name))pairPlans.push(p);const v=variants(d,picks,t.people);v.slice(0,2).forEach(add);const light=[...picks];(d.rounds||[]).forEach((r,i)=>{if(!replaceable(light[i]?.name||'')||r.choices.some(c=>mainDish(c.name)))return;const choice=r.choices.find(c=>/无糖可口可乐中杯|小杯玉米杯/.test(c.name));if(choice)light[i]=choice;});if(light.length)add(makePlan(d,light,t.people,d.name+' · 换搭配'));}catch{state.candidateFailures++;}}
    if(pairPlans.length>=2){const a=pairPlans[0],b=pairPlans[1];add({title:'两组随心配组合',items:[...a.items,...b.items],parts:[...a.parts,...b.parts],people:t.people,detail:null,choices:[]});}
    return out.filter(p=>!p.parts.some(part=>t.avoid.some(word=>normalize(part.name).includes(normalize(word)))));}
  async function generate(){invalidate('');const t=target();state.target=t;state.candidateFailures=0;state.quotes=[];state.baseline=null;state.comparisons=[];progress('generateProgress','正在读取可选组合…',true);try{const plans=await candidates(t);let failed=0;for(let i=0;i<plans.length;i++){progress('generateProgress',`正在逐一核价 ${i+1} / ${plans.length}…`,true);try{state.quotes.push(await quote(plans[i]));}catch{failed++;}}if(!plans.length)throw new Error('没有符合避开关键词的候选，请调整关键词或搭配');if(!state.quotes.length)throw new Error('候选均未核价成功，请更换餐品或稍后重试');state.failed=failed;state.checkedAt=new Date().toISOString();renderResults();progress('generateProgress',`已完成 ${state.quotes.length} 个候选，${failed} 个核价失败`);state.busy=false;go(3);state.busy=true;}catch(e){progress('generateProgress','生成未完成，请调整搭配或重试');throw e;}finally{$('generateProgress').setAttribute('aria-busy','false');}}
  function violations(p){return constraintIssues(p,state.target);}
  function renderResults(){const t=state.target,feasible=state.quotes.filter(p=>satisfies(p,t)),pool=feasible.length?feasible:state.quotes;const ranked=[...pool].sort((a,b)=>{const penalty=p=>violations(p).length*10000;const score=p=>t.priority==='energy'?(p.nutrition.energy??1e6):t.priority==='protein'?-(p.nutrition.protein??-1e4)*20+p.price:t.priority==='price'?p.price:p.price+((p.nutrition.energy??1e5)/t.people)*.012;return penalty(a)-penalty(b)||score(a)-score(b);});const chosen=[],add=p=>{if(p&&!chosen.includes(p))chosen.push(p);};add(ranked[0]);add([...pool].sort((a,b)=>a.price-b.price)[0]);add([...pool].filter(p=>p.nutrition.energy!==null).sort((a,b)=>a.nutrition.energy-b.nutrition.energy)[0]);ranked.forEach(p=>{if(chosen.length<3)add(p);});state.displayed=chosen;state.baseline=chosen[0];$('resultContext').textContent=`${state.store.storeName} · ${t.people} 人 · 本单预算 ¥${t.budget} · 每人 ${t.calories} 千卡以内`;$('resultSummary').textContent=`共 ${state.quotes.length} 个候选核价成功，${feasible.length} 个同时满足目标。${state.failed?`${state.failed} 个核价失败，已排除。`:''}${state.candidateFailures?`${state.candidateFailures} 个候选构建失败，比较范围不完整。`:''}${feasible.length?'以下为已比较候选中的不同取舍。':'本轮没有同时满足全部条件的方案，下面明确列出缺口，请调整搭配或目标。'} ${state.couponError?'优惠查询失败。':'未自动套用优惠券。'}`;$('results').innerHTML=chosen.map((p,i)=>{const issues=violations(p),n=p.nutrition;return`<article class="plan ${i===0?'best':''}"><span class="pill ${issues.length?'red':'green'}">${issues.length?'有条件未满足':'满足当前目标'}</span><h3 style="margin-top:14px">${i===0?'按当前优先级推荐':i===1?'另一个价格取舍':'另一个能量取舍'}</h3><span class="small muted">${esc(p.title)}</span><div class="price">¥${p.price.toFixed(2)}<small>本单实时试算</small></div><ul class="plan-list">${p.parts.map(part=>`<li>${esc(part.name)}${part.supportModify?'【可特调】':''} × ${part.quantity}</li>`).join('')}</ul><div class="metrics"><div><small>每人能量</small><strong>${n.energy===null?'未知':Math.round(n.energy/t.people)+' 千卡'}</strong></div><div><small>每人蛋白质</small><strong>${n.protein===null?'未知':Math.round(n.protein/t.people)+' 克'}</strong></div></div><p class="reason">${issues.length?esc(issues.join('；')):'预算与能量在目标内，且满足已勾选的品类要求。'}${n.missing.length?' 缺失：'+esc(n.missing.join('、'))+'。':''} 核价 ${esc(new Date(p.at).toLocaleTimeString('zh-CN',{hour12:false}))}。</p><button class="btn secondary small" data-baseline="${i}">以这套为基线，只改一处</button></article>`;}).join('');$('sourceTime').textContent='本轮查询时间：'+new Date(state.checkedAt).toLocaleString('zh-CN',{hour12:false});setBaseline(0);}
  function setBaseline(index){state.baseline=state.displayed[index];state.comparisons=[];$('baselineLabel').textContent=`基线 ¥${state.baseline.price.toFixed(2)}`;$('comparisonTable').innerHTML='';progress('compareProgress',state.baseline.detail?.rounds?.length?'点击按钮，对同一套餐的一个配菜或饮料替换重新核价。':'此组合包含多个套餐或为单品，暂不支持自动单项替换。');$('compareOne').disabled=!state.baseline.detail?.rounds?.length;}
  async function compareOne(){const base=state.baseline, t=state.target;state.comparisons=[];$('comparisonTable').innerHTML='';progress('compareProgress','正在重新核价基线…',true);try{const fresh=await quote(base);state.baseline=fresh;const plans=variants(base.detail,base.choices,base.people).filter(p=>!p.parts.some(part=>t.avoid.some(word=>normalize(part.name).includes(normalize(word)))));if(!plans.length){progress('compareProgress','本套餐没有可比较的单项替换');return;}let failures=0;const results=[];for(let i=0;i<plans.length;i++){progress('compareProgress',`正在核价单项替换 ${i+1} / ${plans.length}…`,true);try{results.push(await quote(plans[i]));}catch{failures++;}}state.comparisons=results;$('baselineLabel').textContent=`基线 ¥${fresh.price.toFixed(2)}`;$('comparisonTable').innerHTML=`<table><caption>单项替换与重新核价基线的比较</caption><thead><tr><th scope="col">只改变这一项</th><th scope="col">本单价格</th><th scope="col">相对基线</th><th scope="col">每人能量变化</th><th scope="col">目标检查</th></tr></thead><tbody>${results.map(p=>{const delta=p.price-fresh.price,energy=p.nutrition.energy!==null&&fresh.nutrition.energy!==null?(p.nutrition.energy-fresh.nutrition.energy)/t.people:null;return`<tr><td>${esc(p.change.from)} → ${esc(p.change.to)}</td><td>¥${p.price.toFixed(2)}</td><td class="delta">${delta>0?'+':delta<0?'−':''}¥${Math.abs(delta).toFixed(2)}</td><td>${energy===null?'未知':(energy>0?'+':'')+Math.round(energy)+' 千卡'}</td><td>${esc(violations(p).join('；')||'满足目标')}</td></tr>`;}).join('')}</tbody></table>`;progress('compareProgress',`基线与 ${results.length} 个单项替换均已重新核价。${failures?failures+' 个失败已排除。':''}主食、其他搭配和人数保持一致。`);}catch(e){progress('compareProgress','比较未完成，请重试');throw e;}finally{$('compareProgress').setAttribute('aria-busy','false');}}
  function exportResult(){if(state.busy||!state.quotes.length||!state.target)return;const compact=p=>({title:p.title,items:p.parts.map(({name,quantity})=>({name,quantity})),priceYuan:p.price,energyKcal:p.nutrition.energy,protein:p.nutrition.protein,missing:p.nutrition.missing,quotedAt:p.at,change:p.change||null});const data={product:'麦有数',storeName:state.store.storeName,mode:'即时到店自取',target:state.target,checkedAt:state.checkedAt,couponApplied:false,failedQuotes:state.failed,failedCandidates:state.candidateFailures,candidates:state.quotes.map(compact),baseline:state.baseline?compact(state.baseline):null,comparisons:state.comparisons.map(compact),note:'价格为查询时试算；营养按标准份，未知不作零。有限候选不代表全局最优。'};const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='麦有数-本次比较.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
  $('storeForm').addEventListener('submit',e=>{e.preventDefault();run(findStores);});$('stores').addEventListener('click',e=>{const b=e.target.closest('[data-store]');if(!b||state.busy)return;const picked=state.stores[Number(b.dataset.store)];if(state.store!==picked){invalidate('');state.menu=null;state.detail=null;state.quotes=[];state.baseline=null;state.details.clear();}state.store=picked;$('selectedStore').textContent='已选择：'+state.store.storeName;syncControls();renderStores();});$('loadMenu').onclick=()=>run(loadMenu);$('menuSearch').addEventListener('input',renderCatalog);$('catalog').addEventListener('click',e=>{const b=e.target.closest('[data-meal]');if(b)run(()=>pickMeal(b.dataset.meal));});$('detail').addEventListener('change',e=>{if(!e.target.matches('[data-round]'))return;const i=Number(e.target.dataset.round),choice=state.detail.rounds[i].choices.find(c=>String(c.code)===e.target.value);run(()=>{try{defaultMod(choice);}catch(e){renderDetail();throw e;}state.choices[i]=choice;invalidate();renderDetail();});});for(const id of ['budget','people','calories','priority','needDrink','needFries','avoid']){
    const changed=()=>{if(state.busy)return;invalidate();if(id==='calories'){$('caloriesValue').textContent=$('calories').value+' 千卡';$('calories').setAttribute('aria-valuetext',$('calories').value+' 千卡');}};
    $(id).addEventListener('input',changed);$(id).addEventListener('change',changed);
  }$('quoteCurrent').onclick=()=>run(quoteCurrent);$('generate').onclick=()=>run(generate);$('refreshResults').onclick=()=>run(generate);$('compareOne').onclick=()=>run(compareOne);$('exportResult').onclick=exportResult;$('results').addEventListener('click',e=>{const b=e.target.closest('[data-baseline]');if(b&&!state.busy){setBaseline(Number(b.dataset.baseline));$('compareOne').focus();}});document.querySelectorAll('[data-step]').forEach(b=>b.onclick=()=>go(Number(b.dataset.step)));
  async function connect(){if(location.protocol==='file:'){$('connection').textContent='请从本机服务打开';error(new Error('实时版本需要运行 python server.py 后访问 http://127.0.0.1:8765；直接打开文件无法查询。'));return;}try{const response=await fetch('/api/status',{headers:{'X-Maiyoushu-Token':apiToken()}});const result=await response.json();if(!record(result)||result.ok!==true)throw new Error(result?.message||'无法连接本机服务');state.connected=result.connected;$('connection').textContent=result.connected?'实时服务已连接 · 只读试算':'实时服务未连接';$('connection').classList.toggle('live',!!result.connected);if(!result.connected)throw new Error(result.message);await run(findStores);}catch(e){$('connection').textContent='连接未就绪';error(e);}}
  setBusy(false);
  connect();
})(typeof globalThis!=='undefined'?globalThis:this);
