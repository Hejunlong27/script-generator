'use strict';

/* ============================================================
   状态
   ============================================================ */
const S = {
  cat: null,
  bp: { schema_version: '1.0', meta: {}, elements: {}, overrides: {}, constraints: {} },
  edit: null,          // 正在编辑覆盖的要素 id
  script: null,
  detail: null,        // 正在看的集号
  tab: 'plan',
  timer: null,
  validateSeq: 0,
  llmCfg: null,
  expanded: {},        // 哪些槽位展开了「不相关」的候选
};

const $ = (s) => document.querySelector(s);
const el = (tag, cls, html) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (html != null) n.innerHTML = html;
  return n;
};
const esc = (s) => String(s == null ? '' : s)
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;');
const short = (s, n = 30) => { s = String(s || ''); return s.length > n ? s.slice(0, n) + '…' : s; };

function toast(msg, ms = 2600) {
  const t = $('#toast');
  t.textContent = msg;
  t.classList.remove('hidden');
  clearTimeout(t._t);
  t._t = setTimeout(() => t.classList.add('hidden'), ms);
}

async function api(path, body) {
  const opt = body
    ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }
    : {};
  const r = await fetch(path, opt);
  let data;
  try { data = await r.json(); } catch (e) { throw new Error('服务返回非 JSON'); }
  if (data.error) {
    const e = new Error(data.error.message || '请求失败');
    e.detail = data.error;
    throw e;
  }
  return data;
}

/* ============================================================
   启动
   ============================================================ */
async function init() {
  try {
    S.cat = await api('/api/catalog?market=MX');
  } catch (e) {
    $('#slots').innerHTML = `<div class="empty">载入失败：${esc(e.message)}<br><span class="mono">先跑 tools/build_elements.py</span></div>`;
    return;
  }
  const c = S.cat;

  $('#f-market').innerHTML = c.markets.map((m) => `<option value="${m}">${m}</option>`).join('');
  $('#f-style').innerHTML = c.styles.map((s) => `<option value="${s.id}">${esc(s.name)}</option>`).join('');

  S.bp.meta = {
    title: '顶替新娘', market: 'MX', episode_count: 30, episode_seconds: 60,
    style: (c.styles[0] || {}).id || 'mx-telenovela', output_language: 'zh',
    seed: 20261020,
  };
  // 开箱就有完整的一套（等于 examples/blueprint-bag1.json），
  // 打开界面直接能看懂在干什么；想换直接点「🎲 换一批」。
  S.bp.elements = {
    package: 'MX-PKG-001',
    core: 'MX-COR-B03',
    arc: 'MX-ARC-C01',
    conflict: ['MX-CFT-F01', 'MX-CFT-F04', 'MX-CFT-F03'],
    ending: 'MX-END-H15',
    character: 'MX-CHR-D01',
    support: ['MX-SPT-E01', 'MX-SPT-E02'],
    city: 'MX-LOC-A30',
    open_hook: 'MX-HOK-G01',
  };
  S.bp.constraints = {
    max_cast_per_scene: 3, max_crowd_scenes: 3, max_scene_per_episode: 2,
    allow_s_levels: ['S0', 'S1', 'S2'], auto_fill: true, autofill_randomness: 0.6,
    forbidden_topics: ['毒枭', '总统', '选举'], beat_level_min: 1, beat_level_max: 10,
  };

  refreshLlmStatus();
  renderSlots();
  bind();
  validateNow();

  // 支持 #script / #export 直达某个标签页
  const hash = (location.hash || '').replace('#', '');
  if (['plan', 'script', 'prompt', 'json', 'export'].includes(hash)) switchTab(hash);
}

async function refreshLlmStatus() {
  try { S.llmCfg = await api('/api/config'); } catch (e) { S.llmCfg = null; }
  const llm = (S.llmCfg && S.llmCfg.llm) || {};
  const p = $('#pill-llm');
  if (llm.configured) {
    p.textContent = `模型：${llm.model}${S.llmCfg.source === 'env' ? '（env）' : ''}`;
    p.className = 'pill ok';
    $('#hintline').textContent = '';
  } else {
    p.textContent = '模型：未配置（走模板）';
    p.className = 'pill warn';
    $('#hintline').textContent = '点右上角「⚙ 模型」填 API Key；不填也能用模板模式出稿';
  }
  p.title = JSON.stringify(llm, null, 2);
}

function bind() {
  $('#f-title').oninput = () => { S.bp.meta.title = $('#f-title').value; };
  $('#f-count').oninput = () => { S.bp.meta.episode_count = +$('#f-count').value || 1; scheduleValidate(); };
  $('#f-secs').oninput = () => { S.bp.meta.episode_seconds = +$('#f-secs').value || 60; scheduleValidate(); };
  $('#f-style').onchange = () => { S.bp.meta.style = $('#f-style').value; scheduleValidate(); };
  $('#f-market').onchange = async () => {
    const m = $('#f-market').value;
    S.cat = await api('/api/catalog?market=' + m);
    S.bp.meta.market = m;
    S.bp.elements = {}; S.bp.overrides = {}; S.edit = null;
    renderSlots(); validateNow();
  };
  $('#btn-gen').onclick = generate;
  $('#btn-clear').onclick = () => {
    S.bp.elements = {}; S.bp.overrides = {}; S.edit = null; S.expanded = {};
    renderSlots(); validateNow();
  };
  // 换一批：清空 + 换种子 + 重新抽一套
  $('#btn-reshuffle').onclick = () => {
    S.bp.elements = {}; S.bp.overrides = {}; S.edit = null; S.expanded = {};
    S.bp.meta.seed = Math.floor(Math.random() * 1e9);
    renderSlots();
    validateNow();
    toast(`换了一批（seed ${S.bp.meta.seed}）`);
  };
  // 补齐缺口：保留已选，只填空白，种子不变
  $('#btn-autofill').onclick = async () => {
    const r = await api('/api/validate', { blueprint: S.bp });
    S.bp = r.blueprint || S.bp;
    renderSlots(); renderPlan(r);
    const n = (r.autofilled || []).length;
    toast(n ? `已补齐 ${n} 个槽位（seed ${S.bp.meta.seed}）` : '没有缺口');
  };
  $('#f-random').onchange = () => {
    S.bp.constraints.autofill_randomness = +$('#f-random').value;
    // 换随机度后重新抽一次，否则看不出差别
    S.bp.elements = {}; S.bp.overrides = {}; S.edit = null;
    S.bp.meta.seed = Math.floor(Math.random() * 1e9);
    renderSlots(); validateNow();
    toast('随机度已改，重新抽了一批');
  };
  document.querySelectorAll('.tabs button').forEach((b) => {
    b.onclick = () => switchTab(b.dataset.tab);
  });
  $('#btn-llm').onclick = openLlm;
  $('#llm-close').onclick = closeLlm;
  $('#llm-save').onclick = saveLlm;
  $('#llm-btn-test').onclick = testLlm;
  $('#llm-clear').onclick = clearLlmKey;
  $('#modal-llm').addEventListener('click', (e) => {
    if (e.target.id === 'modal-llm') closeLlm();
  });
  document.querySelectorAll('[data-preset]').forEach((b) => {
    b.onclick = () => applyPreset(b.dataset.preset);
  });
}

function switchTab(tab) {
  S.tab = tab;
  document.querySelectorAll('.tabs button').forEach((b) => b.classList.toggle('active', b.dataset.tab === tab));
  ['plan', 'script', 'prompt', 'json', 'export'].forEach((t) => {
    $('#tab-' + t).classList.toggle('hidden', t !== tab);
  });
  if (tab === 'prompt') renderPrompt();
  if (tab === 'json') renderJson();
  if (tab === 'export') renderExport();
  if (tab === 'script') renderScript();
}

/* ============================================================
   要素选择（按 step 顺序 = 用户的构建顺序）
   ============================================================ */
function label(e) {
  const a = e.attrs || {};
  return a.one_line || a.skeleton || a.conflict || a.identity || a.place
    || a.ending || a.visual || a.text || e.name || e.id;
}

function elementsOf(slot) {
  const type = slot.bound_type || slot.key;
  return (S.cat.elements[type] || []);
}

function findEl(id) {
  if (!id) return null;
  for (const list of Object.values(S.cat.elements)) {
    const hit = list.find((x) => x.id === id);
    if (hit) return hit;
  }
  return null;
}

/** 当前题材包给的推荐集合 */
function presets() {
  const pkg = findEl(S.bp.elements.package);
  return {
    cores: new Set((pkg && pkg.attrs && pkg.attrs.preset_cores) || []),
    arcs: new Set((pkg && pkg.attrs && pkg.attrs.preset_arcs) || []),
  };
}

/** 已选内核在 arc 里标了「配内核」的那些 */
function arcsByCore() {
  const coreId = typeof S.bp.elements.core === 'string' ? S.bp.elements.core : null;
  if (!coreId) return new Set();
  const code = coreId.split('-').pop();
  return new Set((S.cat.elements.arc || [])
    .filter((a) => (a.attrs.pairs_core || []).includes(code))
    .map((a) => a.id));
}

/**
 * 某个槽位的「相关候选」。
 * 返回 null 表示不做折叠（没有参照物，折叠只会添乱）。
 *
 * 规则与后端的校验/补齐保持一致：
 *   core      -> 题材包的 preset_cores
 *   arc       -> preset_arcs ∪ 声明了「配这个内核」的
 *   character -> tags 与 core / arc 有交集的（C∩D 的软版本）
 *   support   -> 同上
 */
function relatedSet(slot) {
  const pkg = findEl(S.bp.elements.package);
  const pa = (pkg && pkg.attrs) || {};
  const coreId = typeof S.bp.elements.core === 'string' ? S.bp.elements.core : null;
  const arcId = typeof S.bp.elements.arc === 'string' ? S.bp.elements.arc : null;

  if (slot.key === 'core') {
    return pa.preset_cores && pa.preset_cores.length ? new Set(pa.preset_cores) : null;
  }
  if (slot.key === 'arc') {
    const s = new Set(pa.preset_arcs || []);
    const byCore = arcsByCore();
    byCore.forEach((x) => s.add(x));
    return s.size ? s : null;
  }
  if (slot.key === 'character' || slot.key === 'support') {
    const ref = new Set();
    [coreId, arcId].forEach((id) => {
      const e = findEl(id);
      if (e) (e.tags || []).forEach((t) => ref.add(t));
    });
    if (!ref.size) return null;
    return new Set(elementsOf(slot)
      .filter((e) => (e.tags || []).some((t) => ref.has(t)))
      .map((e) => e.id));
  }
  return null;
}

function picked(slot) {
  const v = S.bp.elements[slot.key];
  if (v == null) return [];
  return Array.isArray(v) ? v : [v];
}

function renderSlots() {
  const box = $('#slots');
  box.innerHTML = '';
  const rec = presets();
  const recArc = arcsByCore();

  let lastStage = null;
  let stageSlots = [];
  let stepNo = 0;
  const heads = [];

  for (const slot of S.cat.slots) {
    if (slot.stage !== lastStage) {
      if (lastStage !== null && heads.length) {
        const done = stageSlots.every((s) => picked(s).length >= Math.max(1, s.min_pick || 1));
        heads[heads.length - 1].querySelector('.mark').outerHTML = done
          ? '<span class="mark done">已定</span>'
          : '<span class="mark todo">待选</span>';
      }
      lastStage = slot.stage;
      stageSlots = [];
      stepNo = 0;
      const m = String(slot.stage || '').match(/^(\d+)\s*(.*)$/);
      const num = m ? m[1] : '';
      const title = m ? m[2] : slot.stage;
      const head = el('div', 'stagehead',
        `${num ? `<span class="n">${esc(num)}</span>` : ''}<span>${esc(title)}</span>
         <span class="mark todo">待选</span>`);
      heads.push(head);
      box.appendChild(head);
    }
    stageSlots.push(slot);
    stepNo++;

    const sec = el('div', 'slot');
    const arr = picked(slot);
    const need = slot.pick || slot.min_pick || 1;
    sec.appendChild(el('div', 'slot-head',
      `<b>${esc(slot.cn)}</b>
       <span class="stepno">第 ${stepNo} 步</span>
       <span class="meta">${slot.role === 'multi' ? `选 ${need} 项` : '选 1 项'}</span>
       <span class="sel">${arr.length}/${need}</span>`));
    sec.appendChild(el('div', 'slot-hint', esc(slot.hint || '')));

    // 与当前题材/内核无关的候选默认折叠，避免 30 个不搭的选项糊一脸
    const rel = relatedSet(slot);
    const expanded = !!S.expanded[slot.key];
    const all = elementsOf(slot);
    const shown = all.filter((e) => !rel || rel.has(e.id) || arr.includes(e.id) || expanded);
    const hiddenN = all.length - shown.length;

    // 冲突必须凑齐三层，按层分组渲染比 30 个平铺好读得多
    const layerOf = (e) => (e.attrs && e.attrs.layer) || '其他';
    const isConflict = slot.key === 'conflict';
    const layers = isConflict
      ? ['外部', '内部', '结构', '其他'].filter((L) => shown.some((e) => layerOf(e) === L))
      : [null];

    const newChips = () => {
      const box = el('div', 'chips' + (slot.key === 'package' ? ' pkglist' : ''));
      sec.appendChild(box);
      return box;
    };
    let chips = newChips();

    for (const L of layers) {
      if (isConflict && L) {
        const gotL = arr.some((id) => { const x = findEl(id); return x && ((x.attrs || {}).layer === L); });
        const tip = { 外部: '推动剧情', 内部: '主角自己也是障碍', 结构: '靠努力解决不了' }[L] || '';
        sec.appendChild(el('div', 'layertag' + (gotL ? ' ok' : ''),
          `${L}${tip ? ' · ' + tip : ''}${gotL ? '　✓ 已选' : ''}`));
        chips = newChips();
      }
      for (const e of shown.filter((x) => !isConflict || layerOf(x) === L)) {
      const on = arr.includes(e.id);
      const isPkg = slot.key === 'package';
      const isRec = (slot.key === 'core' && rec.cores.has(e.id))
        || (slot.key === 'arc' && rec.arcs.has(e.id));
      const isPair = slot.key === 'arc' && recArc.has(e.id);
      const offTopic = rel && !rel.has(e.id);

      const chip = el('button', 'chip' + (on ? ' on' : '') + (isPkg ? ' pkg' : '') + (offTopic ? ' off' : ''));
      if (isPkg) {
        const rd = (e.attrs && e.attrs.readiness) || {};
        const flag = e.attrs.ready
          ? '<span class="flag ok">可开工</span>'
          : `<span class="flag warn">缺 ${e.attrs.gaps || 0} 个场景</span>`;
        chip.title = [e.name, (e.attrs.labels || []).join(' + '),
          e.attrs.sellpoint, e.attrs.core_hook ? '核心钩子：' + e.attrs.core_hook : '',
          rd.coverage ? '场景覆盖：' + rd.coverage : ''].filter(Boolean).join('\n');
        chip.innerHTML = `<span class="t1"><b>${esc(e.name)}</b>${flag}</span>
          <span class="labels">${esc((e.attrs.labels || []).join(' + '))}</span>
          <span class="sell">${esc(short(e.attrs.sellpoint || '', 46))}</span>`;
      } else {
        chip.title = `${e.id}\n${label(e)}` + (e.name_local ? `\n${e.name_local}` : '')
          + (e.attrs && e.attrs.never_do ? `\n不可做：${e.attrs.never_do}` : '')
          + (e.attrs && e.attrs.layer ? `\n层级：${e.attrs.layer}` : '')
          + (offTopic ? '\n⚠ 与当前题材不搭' : '');
        const badge = isRec ? '<span class="rec">推荐</span>'
          : isPair ? '<span class="rec pair">配此内核</span>'
            : offTopic ? '<span class="rec off">不搭</span>' : '';
        chip.innerHTML = `${badge}
          <span>${esc(short(label(e), 26))}</span>
          <span class="id">${esc(e.id.split('-').slice(-1)[0])}</span>`
          + (e.s_level && e.s_level !== 'S1' ? `<span class="tag">${esc(e.s_level)}</span>` : '')
          + (on ? '<span class="edit">✎</span>' : '');
      }
      chip.onclick = (ev) => {
        if (on && ev.target.classList.contains('edit')) { S.edit = e.id; renderOverride(); renderSlots(); return; }
        toggle(slot, e.id);
      };
      chips.appendChild(chip);
      }
    }


    if (hiddenN > 0) {
      const more = el('button', 'ghost more',
        `＋ 显示其他 ${hiddenN} 项（与当前题材不搭）`);
      more.onclick = () => { S.expanded[slot.key] = true; renderSlots(); };
      sec.appendChild(more);
    } else if (expanded && rel) {
      const less = el('button', 'ghost more', '收起不相关的项');
      less.onclick = () => { delete S.expanded[slot.key]; renderSlots(); };
      sec.appendChild(less);
    }

    if (slot.key === 'conflict' && arr.length) {
      const layers = arr.map((id) => ((findEl(id) || {}).attrs || {}).layer);
      const miss = ['外部', '内部', '结构'].filter((l) => !layers.includes(l));
      sec.appendChild(el('div', 'slot-hint',
        `已选层：${layers.filter(Boolean).join(' / ') || '—'}`
        + (miss.length ? `　<b style="color:var(--err)">缺 ${miss.join('、')}</b>`
          : '　<b style="color:var(--ok)">三层齐</b>')));
    }
    box.appendChild(sec);
  }
  if (heads.length) {
    const done = stageSlots.every((s) => picked(s).length >= Math.max(1, s.min_pick || 1));
    heads[heads.length - 1].querySelector('.mark').outerHTML = done
      ? '<span class="mark done">已定</span>'
      : '<span class="mark todo">待选</span>';
  }
  renderOverride();
}

function toggle(slot, id) {
  const cur = S.bp.elements[slot.key];
  const arr = cur == null ? [] : (Array.isArray(cur) ? cur.slice() : [cur]);
  const i = arr.indexOf(id);
  if (slot.role === 'multi') {
    if (i >= 0) arr.splice(i, 1);
    else {
      const cap = slot.pick || slot.min_pick || 99;
      if (arr.length >= cap) { toast(`「${slot.cn}」最多选 ${cap} 项，先取消一个`); return; }
      arr.push(id);
    }
    S.bp.elements[slot.key] = arr;
  } else {
    if (i >= 0) { delete S.bp.elements[slot.key]; delete S.bp.overrides[id]; }
    else {
      S.bp.elements[slot.key] = id;
      if (cur && cur !== id) delete S.bp.overrides[cur];
    }
  }
  if (slot.key === 'package') applyPackagePreset(id, i < 0);
  renderSlots();
  validateNow();
}

/** 选完题材，把剧情内核/主线骨架的推荐位填上（只填空位，不覆盖用户已选） */
function applyPackagePreset(pkgId, justSelected) {
  if (!justSelected) return;
  const pkg = findEl(pkgId);
  if (!pkg) return;
  const cores = pkg.attrs.preset_cores || [];
  const arcs = pkg.attrs.preset_arcs || [];
  const filled = [];
  const mismatch = [];
  if (!S.bp.elements.core) {
    if (cores.length) { S.bp.elements.core = cores[0]; filled.push('剧情内核'); }
  } else if (cores.length && !cores.includes(S.bp.elements.core)) {
    mismatch.push('剧情内核');
  }
  if (!S.bp.elements.arc) {
    if (arcs.length) { S.bp.elements.arc = arcs[0]; filled.push('主线骨架'); }
  } else if (arcs.length && !arcs.includes(S.bp.elements.arc)) {
    mismatch.push('主线骨架');
  }
  if (filled.length) {
    toast(`已按「${pkg.name}」带出${filled.join('、')}，可随时改`);
  } else if (mismatch.length) {
    toast(`${mismatch.join('、')}不在「${pkg.name}」的推荐位里，建议换一下（推荐项已标「推荐」）`, 3800);
  }
}

/* ============================================================
   覆盖编辑
   ============================================================ */
function renderOverride() {
  const box = $('#override');
  if (!S.edit) { box.classList.add('hidden'); box.innerHTML = ''; return; }
  const e = findEl(S.edit);
  if (!e) { box.classList.add('hidden'); return; }
  const ov = S.bp.overrides[e.id] || {};
  const type = e.id.split('-')[1];

  let fields = `<label>显示名（覆盖 name）<input id="ov-name" value="${esc(ov.name || '')}" placeholder="${esc(e.name)}"></label>`;
  if (type === 'CHR' || type === 'SPT') {
    const f = (k, cn, ph) => `<label>${cn}<input data-k="attrs.${k}" value="${esc(ov['attrs.' + k] || '')}" placeholder="${esc(ph || '')}"></label>`;
    fields += `<div class="row">${f('identity', '身份')}${f('surface', '表面人格')}</div>`;
    fields += f('flaw', '真实缺陷（必须可演）', e.attrs.flaw);
    fields += f('secret', '秘密', e.attrs.secret);
    fields += f('speech', '说话方式 / 称谓变化', e.attrs.speech);
    fields += `<label>不可做清单（每行一条）<textarea data-k="attrs.never_do" rows="3"
      placeholder="${esc(e.attrs.never_do || '')}">${esc((ov['attrs.never_do'] || '').toString().replace(/,/g, '\n'))}</textarea></label>`;
  } else if (type === 'COR' || type === 'ARC' || type === 'END' || type === 'PKG') {
    fields += `<label>一句话内容覆盖<textarea data-k="attrs.one_line" rows="2">${esc(ov['attrs.one_line'] || '')}</textarea></label>`;
  }
  box.innerHTML = `<h3>编辑要素</h3><div class="oid">${esc(e.id)} · ${esc(e.name)}</div>
    <div id="ov-fields">${fields}</div>
    <div class="row" style="margin-top:10px">
      <button class="ghost" id="ov-save" style="flex:none">保存覆盖</button>
      <button class="ghost" id="ov-reset" style="flex:none">还原</button>
      <button class="ghost" id="ov-close" style="flex:none">关闭</button>
    </div>
    <div class="slot-hint" style="padding:8px 0 0">覆盖只作用于本次蓝图，不改素材库本体。</div>`;
  box.classList.remove('hidden');

  box.querySelector('#ov-close').onclick = () => { S.edit = null; renderOverride(); renderSlots(); };
  box.querySelector('#ov-reset').onclick = () => {
    delete S.bp.overrides[e.id]; S.edit = null; renderOverride(); renderSlots(); validateNow();
  };
  box.querySelector('#ov-save').onclick = () => {
    const ov2 = {};
    const nm = box.querySelector('#ov-name').value.trim();
    if (nm) ov2.name = nm;
    box.querySelectorAll('[data-k]').forEach((inp) => {
      const k = inp.dataset.k;
      let v = inp.value.trim();
      if (!v) return;
      if (k === 'attrs.never_do') v = v.split('\n').map((s) => s.trim()).filter(Boolean);
      ov2[k] = v;
    });
    if (Object.keys(ov2).length) S.bp.overrides[e.id] = ov2;
    else delete S.bp.overrides[e.id];
    toast('已保存覆盖');
    renderSlots(); validateNow();
  };
}

/* ============================================================
   校验 & 编译预览
   ============================================================ */
function scheduleValidate() {
  clearTimeout(S.timer);
  S.timer = setTimeout(validateNow, 350);
}

async function validateNow() {
  const seq = ++S.validateSeq;
  let r;
  try {
    r = await api('/api/validate', { blueprint: S.bp });
  } catch (e) {
    $('#pill-check').className = 'pill err';
    $('#pill-check').textContent = '校验：失败';
    $('#tab-plan').innerHTML = `<div class="issue err"><b>${esc((e.detail && e.detail.code) || 'ERR')}</b><span>${esc(e.message)}</span></div>`;
    return;
  }
  if (seq !== S.validateSeq) return;
  let elementsChanged = false;
  if (r.blueprint) {
    const before = JSON.stringify(S.bp.elements || {});
    S.bp = r.blueprint;
    if (JSON.stringify(S.bp.elements || {}) !== before) elementsChanged = true;
  }
  if (elementsChanged) renderSlots();     // 自动补齐后左侧要显示选中态
  const st = $('#seedtag');
  if (st) st.textContent = 'seed ' + (S.bp.meta.seed != null ? S.bp.meta.seed : '—');
  renderPlan(r);
  const errs = r.issues.filter((i) => i.level === 'error').length;
  const warns = r.issues.filter((i) => i.level === 'warn').length;
  const pill = $('#pill-check');
  pill.className = 'pill ' + (errs ? 'err' : warns ? 'warn' : 'ok');
  pill.textContent = errs ? `校验：${errs} 阻断` : warns ? `校验：${warns} 提醒` : '校验：通过';
}

function renderPlan(r) {
  const box = $('#tab-plan');
  const est = r.estimate;
  let h = '';

  if (r.issues && r.issues.length) {
    h += '<h3 class="sec">校验结果</h3>';
    for (const i of r.issues) {
      h += `<div class="issue ${i.level === 'error' ? 'err' : 'warn'}">
        <b>${esc(i.code)}</b><span>${esc(i.message)}${i.hint ? `<span class="hint">→ ${esc(i.hint)}</span>` : ''}</span></div>`;
    }
  } else {
    h += '<h3 class="sec">校验结果</h3><div class="issue ok" style="background:var(--ok-bg);color:#14532d">'
      + '<b>OK</b><span>蓝图通过：三层冲突齐、B→C→D 链式相连、簇未溢出、S 等级合规</span></div>';
  }

  if (r.autofilled && r.autofilled.length) {
    h += `<h3 class="sec">自动补齐</h3><div class="card">`;
    for (const a of r.autofilled) {
      h += `<div style="font-size:12.5px">· <b>${esc(a.cn)}</b> → ${a.filled.map((x) => `<span class="mono">${esc(x)}</span>`).join('、')}
        <span style="color:var(--tx3)">（${esc(a.reason)}）</span></div>`;
    }
    h += `</div>`;
  }

  if (est) {
    h += '<h3 class="sec">编译结果</h3><div class="grid">';
    h += `<div class="kpi"><b>${est.episodes}×${est.episode_seconds}s</b><span>规格（集 × 单集秒数）</span></div>`;
    h += `<div class="kpi"><b>${est.cards}</b><span>张组装卡</span></div>`;
    h += `<div class="kpi"><b>${est.cost_summary.distinct_scenes}</b><span>个场景被使用</span></div>`;
    h += `<div class="kpi"><b>${est.cost_summary.crowd_scenes}</b><span>场群戏</span></div>`;
    h += `<div class="kpi"><b>${est.cost_summary.avg_cast}</b><span>平均在场人数</span></div>`;
    h += `<div class="kpi"><b>${est.calls_min}–${est.calls_max}</b><span>模型调用次数</span></div>`;
    h += '</div>';

    h += '<h3 class="sec">五段结构</h3><table><tr><th>段位</th><th>集数</th><th>任务</th></tr>';
    for (const s of est.segments || []) {
      h += `<tr><td><b>${esc(s.name)}</b></td><td class="mono">${s.start}–${s.end}</td><td>${esc(short(s.goal, 60))}</td></tr>`;
    }
    h += '</table>';

    const l = est.beat_ladder || [];
    if (l.length) {
      h += '<h3 class="sec">爽点梯度（每根柱 = 一集，柱高 = 强度 1–10）</h3>';
      h += '<div class="ladder">' + l.map((v, i) => {
        const cls = v >= 9 ? 'top' : v >= 7 ? 'hi' : '';
        return `<i class="${cls}" style="height:${Math.max(4, v / 10 * 100)}%" title="E${i + 1} · ${v}"></i>`;
      }).join('') + '</div>';
      h += `<div style="font-size:11.5px;color:var(--tx3);margin-top:6px">
        深蓝 = 7 以上（大爽）· 红 = 9 以上（全季级，≤3 次）· 相邻差 ≤2 已自动修复</div>`;
    }
  }
  box.innerHTML = h;
}

/* ============================================================
   模型配置
   ============================================================ */
const PRESETS = {
  deepseek: { base_url: 'https://api.deepseek.com/v1', model: 'deepseek-chat', note: '用 chat，别用 flash/reasoner —— 推理模型会把 token 全花在思考上' },
  openai: { base_url: 'https://api.openai.com/v1', model: 'gpt-4o-mini' },
  qwen: { base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1', model: 'qwen-plus' },
  zhipu: { base_url: 'https://open.bigmodel.cn/api/paas/v4', model: 'glm-4-flash' },
  ollama: { base_url: 'http://127.0.0.1:11434/v1', model: 'qwen2.5:14b' },
};

async function openLlm() {
  $('#modal-llm').classList.remove('hidden');
  $('#llm-test').classList.add('hidden');
  await refreshLlmStatus();
  const c = S.llmCfg || {};
  const llm = c.llm || {};
  const saved = c.saved || {};
  $('#llm-base').value = saved.base_url || '';
  $('#llm-model').value = saved.model || '';
  $('#llm-temp').value = saved.temperature != null ? saved.temperature : '';
  $('#llm-timeout').value = saved.timeout != null ? saved.timeout : '';
  $('#llm-maxtok').value = saved.max_tokens != null ? saved.max_tokens : '';
  $('#llm-key').value = '';
  $('#llm-key').placeholder = c.has_key ? `已配置 ${c.key_hint}，留空表示不改` : 'sk-…';
  $('#llm-hint').innerHTML = c.env_locked
    ? '⚠ 检测到环境变量 <code>SG_LLM_API_KEY</code>，它的优先级更高，这里的设置不会生效。'
    : 'API Key 只存在本机 <code>' + esc(String(c.config_path || 'config.local.json').split(/[\\/]/).pop())
      + '</code>，不进版本库、不回传到页面。环境变量 <code>SG_LLM_*</code> 优先级高于这里的设置。';
}

function closeLlm() { $('#modal-llm').classList.add('hidden'); }

function applyPreset(name) {
  const p = PRESETS[name];
  if (!p) return;
  $('#llm-base').value = p.base_url;
  $('#llm-model').value = p.model;
  if (name === 'ollama' && !$('#llm-key').value) $('#llm-key').value = 'ollama';
  toast(`已填入 ${name} 的地址与模型，只需补 API Key`);
}

function llmForm() {
  const g = (id) => $(id).value.trim();
  const num = (id) => ($(id).value.trim() === '' ? undefined : Number($(id).value));
  const out = { base_url: g('#llm-base'), model: g('#llm-model') };
  const key = g('#llm-key');
  if (key) out.api_key = key;
  const t = num('#llm-temp'); if (t !== undefined) out.temperature = t;
  const to = num('#llm-timeout'); if (to !== undefined) out.timeout = to;
  const mt = num('#llm-maxtok'); if (mt !== undefined) out.max_tokens = mt;
  return out;
}

async function saveLlm() {
  try {
    const r = await api('/api/config', { llm: llmForm() });
    S.llmCfg = r;
    await refreshLlmStatus();
    toast('已保存到本机配置');
    closeLlm();
  } catch (e) { toast('保存失败：' + e.message); }
}

async function testLlm() {
  const box = $('#llm-test');
  box.className = 'test-box';
  box.textContent = '测试中…';
  box.classList.remove('hidden');
  try {
    await api('/api/config', { llm: llmForm() });
    await refreshLlmStatus();
    const r = await api('/api/config/test', {});
    if (r.ok) {
      box.className = 'test-box ok';
      box.innerHTML = `连通 ✓　模型 <span class="mono">${esc(r.model)}</span>　${r.latency_ms} ms`
        + (r.sample ? `<br>返回：<span class="mono">${esc(r.sample)}</span>` : '');
    } else {
      box.className = 'test-box err';
      box.innerHTML = `失败：${esc((r.error && r.error.message) || '未知错误')}`
        + (r.error && r.error.hint ? `<br><span class="mono">→ ${esc(r.error.hint)}</span>` : '');
    }
  } catch (e) {
    box.className = 'test-box err';
    box.textContent = '失败：' + e.message;
  }
}

async function clearLlmKey() {
  if (!confirm('清除本机保存的 API Key？清除后只能走模板模式，直到重新配置。')) return;
  try {
    const r = await api('/api/config', { llm: { api_key: null } });
    S.llmCfg = r;
    $('#llm-key').value = '';
    await refreshLlmStatus();
    toast('已清除 Key');
  } catch (e) { toast('清除失败：' + e.message); }
}

/* ============================================================
   生成
   ============================================================ */
async function generate() {
  const btn = $('#btn-gen');
  btn.disabled = true;
  $('#progress').classList.remove('hidden');
  $('#bar-fill').style.width = '2%';
  $('#progress-text').textContent = '提交任务…';
  try {
    const r = await api('/api/generate', {
      blueprint: S.bp,
      mode: $('#f-mode').value,
      episodes: ($('#f-range').value || '').trim() || null,
      workers: 4,
    });
    poll(r.job_id);
  } catch (e) {
    btn.disabled = false;
    $('#progress').classList.add('hidden');
    toast('生成失败：' + e.message);
    if (e.detail) {
      $('#tab-plan').innerHTML = `<div class="issue err"><b>${esc(e.detail.code)}</b>
        <span>${esc(e.detail.message)}<span class="hint">→ ${esc(e.detail.hint || '')}</span></span></div>`;
      switchTab('plan');
    }
  }
}

async function poll(id) {
  let miss = 0;
  const tick = async () => {
    let j;
    try { j = await api('/api/job/' + id); } catch (e) { if (++miss > 6) { finish(); return; } setTimeout(tick, 1000); return; }
    $('#bar-fill').style.width = (j.pct || 0) + '%';
    $('#progress-text').textContent = j.message || j.status;
    if (j.status === 'done') {
      S.script = j.script;
      S.detail = (j.script.episodes[0] || {}).episode || null;
      finish();
      const outDir = ((j.script.files || {}).md || '').replace(/\\/g, '/').split('/').slice(-2, -1)[0];
      toast(`完成：${j.script.episodes.length} 集，${j.script.checks.errors} 个阻断项`
        + (outDir ? ` · 已存到 ${outDir}/` : ''), 4000);
      switchTab('script');
      return;
    }
    if (j.status === 'failed') {
      finish();
      if (j.error) {
        $('#tab-plan').innerHTML = `<div class="issue err"><b>${esc(j.error.code)}</b>
          <span>${esc(j.error.message)}<span class="hint">→ ${esc(j.error.hint || '')}</span></span></div>`;
        switchTab('plan');
      }
      toast('生成失败');
      return;
    }
    setTimeout(tick, 900);
  };
  tick();
}

function finish() {
  $('#btn-gen').disabled = false;
  setTimeout(() => $('#progress').classList.add('hidden'), 600);
}

/* ============================================================
   剧本
   ============================================================ */
function renderScript() {
  const box = $('#tab-script');
  if (!S.script) { box.innerHTML = '<div class="empty">还没有剧本。选好要素后点右上角「生成剧本」。</div>'; return; }
  const sc = S.script;
  const eps = sc.episodes || [];

  // 一集都没出来：把失败原因摊开讲，别留个空白页
  if (!eps.length) {
    const ab = sc.aborted || {};
    const fails = sc.failures || [];
    const first = fails[0] || {};
    box.innerHTML = `
      <div class="empty" style="padding:34px 12px 18px">
        <div style="font-size:15px;font-weight:650;color:var(--err);margin-bottom:6px">没有生成出任何一集</div>
        <div style="font-size:12.5px;color:var(--tx2)">${esc(ab.reason || '全部集数生成失败')}</div>
      </div>
      ${first.message ? `<div class="issue err"><b>${esc(first.code || 'GEN')}</b>
        <span>${esc(first.message)}${first.hint ? `<span class="hint">→ ${esc(first.hint)}</span>` : ''}</span></div>` : ''}
      <div class="card" style="margin-top:12px;font-size:12.5px;line-height:1.9">
        <b>怎么处理</b><br>
        1. 点右上角「⚙ 模型」→「测试连接」，先确认模型通不通<br>
        2. 如果报「正文为空 / 全是推理」：把模型换成 <code>deepseek-chat</code> 这类<strong>非推理模型</b>，或把 max_tokens 调到 12000 以上<br>
        3. 想先看看结构对不对：顶栏「模式」切到 <code>template</code>，不调模型也能出完整稿
      </div>
      <div class="badges" style="margin-top:12px">
        <span class="badge err">${fails.length} 集失败</span>
        <span class="badge">模式 ${esc((sc.meta || {}).mode || '')}</span>
        <span class="badge">${esc((sc.meta || {}).elapsed_sec || 0)} 秒</span>
      </div>`;
    return;
  }

  if (!S.detail || !eps.find((e) => e.episode === S.detail)) S.detail = (eps[0] || {}).episode;
  const ep = eps.find((e) => e.episode === S.detail) || {};

  const mdPath = (sc.files || {}).md || '';
  const shortDir = mdPath ? String(mdPath).replace(/\\/g, '/').split('/').slice(-2).join('/') : '';
  let h = '';
  if (shortDir) {
    h += `<div class="savebar">已保存到 <code>${esc(shortDir)}</code>
      <button class="fbtn" id="btn-goto-export" style="margin-left:auto">打开文件夹 / 看全部产物</button></div>`;
  }
  h += '<h3 class="sec">分集</h3><div class="eplist">';
  h += eps.map((e) => `<button class="${e.episode === S.detail ? 'on' : ''}" data-ep="${e.episode}"
     title="${esc(e.title || '')}">E${e.episode}${e.generator && e.generator.degraded ? '·降级' : ''}</button>`).join('');
  h += '</div>';

  const ck = ep._checks || {};
  h += `<div class="ep"><h4>第 ${ep.episode} 集《${esc(ep.title || '')}》</h4>
    <div class="log">${esc(ep.logline || '')}</div>`;

  h += '<div class="tl">';
  for (const s of ep.segments || []) {
    h += `<div>${esc(s.range)}</div><div><b>${esc(s.kind)}</b>　${esc(s.text || '')}</div>`;
  }
  h += '</div>';

  for (const scn of ep.scenes || []) {
    h += `<div class="scene">
      <div class="slug">${esc(scn.slug)}　${esc(scn.location || '')}</div>
      <div class="meta2">${esc(scn.time || '')}　镜头：${esc(scn.camera || '')}　
        场景 <span class="mono">${esc(scn.scene_ref || '')}</span>
        ${(scn.props || []).length ? '　道具 <span class="mono">' + (scn.props || []).map(esc).join(', ') + '</span>' : ''}</div>
      <div class="act">${esc(scn.action || '')}</div>`;
    for (const ln of scn.lines || []) {
      h += `<div class="line"><span class="who">${esc(ln.speaker || '')}</span>
        <span class="reg">${esc(ln.register || '')}</span>
        <span class="es">${esc(ln.text || '')}</span>
        ${ln.gloss && ln.gloss !== ln.text ? `<span class="gl">↳ ${esc(ln.gloss)}</span>` : ''}</div>`;
    }
    h += '</div>';
  }

  const b = ep.beat || {}, hk = ep.hook || {};
  h += `<div class="card" style="margin-top:8px">
    <div><b>爽点</b> <span class="mono">${esc(b.ref || '')}</span>
      <span class="badge ${b.level >= 7 ? 'warn' : ''}">刻度 ${esc(b.level == null ? '—' : b.level)}</span></div>
    <div style="font-size:12.5px;color:var(--tx2);margin-top:4px">${esc(b.info_gap || b.landing || '')}</div>
    <div style="margin-top:8px"><b>集尾钩</b> <span class="mono">${esc(hk.ref || '')}</span></div>
    <div style="font-size:12.5px;color:var(--tx2);margin-top:4px">${esc(hk.promise || '')}</div>
  </div>`;

  h += '<div class="badges">';
  if (ep.generator) {
    h += `<span class="badge ${ep.generator.degraded ? 'warn' : 'ok'}">${ep.generator.mode === 'llm' ? 'LLM 稿' : '模板稿'}</span>`;
    if (ep.generator.model) h += `<span class="badge">${esc(ep.generator.model)}</span>`;
    if (ep.generator.repaired) h += '<span class="badge">已自修复</span>';
  }
  h += `<span class="badge ${ck.errors ? 'err' : 'ok'}">校验 ${ck.errors || 0} 错 / ${ck.warnings || 0} 警</span>`;
  h += '</div>';
  if ((ck.issues || []).length) {
    h += '<div style="margin-top:10px">';
    for (const i of ck.issues.slice(0, 8)) {
      h += `<div class="issue ${i.level === 'error' ? 'err' : 'warn'}"><b>${esc(i.code)}</b>
        <span>${esc(i.message)}<span class="hint">${esc(i.where || '')}</span></span></div>`;
    }
    h += '</div>';
  }
  h += '</div>';

  if (sc.aborted) {
    h += `<div class="issue err" style="margin-top:12px"><b>已中止</b>
      <span>${esc(sc.aborted.reason)}（已生成 ${sc.aborted.generated} 集，剩 ${sc.aborted.remaining} 集未跑）
      ${sc.aborted.first_error ? `<span class="hint">首条错误：${esc(sc.aborted.first_error)}</span>` : ''}</span></div>`;
  }

  const g = sc.checks || {};
  h += '<h3 class="sec">全剧检查</h3>';
  h += `<div class="badges">
    <span class="badge ${g.errors ? 'err' : 'ok'}">${g.errors || 0} 阻断 / ${g.warnings || 0} 提醒</span>
    <span class="badge">模式 ${esc((sc.meta || {}).mode || '')}</span>
    <span class="badge">${esc((sc.meta || {}).elapsed_sec || 0)} 秒</span></div>`;
  if ((g.issues || []).length) {
    for (const i of g.issues.slice(0, 10)) {
      h += `<div class="issue ${i.level === 'error' ? 'err' : 'warn'}" style="margin-top:6px">
        <b>${esc(i.code)}</b><span>${esc(i.message)}</span></div>`;
    }
  }
  box.innerHTML = h;
  box.querySelectorAll('[data-ep]').forEach((b2) => {
    b2.onclick = () => { S.detail = +b2.dataset.ep; renderScript(); };
  });
  const gotoExport = box.querySelector('#btn-goto-export');
  if (gotoExport) gotoExport.onclick = () => switchTab('export');
}

/* ============================================================
   Prompt / JSON / 导出
   ============================================================ */
async function renderPrompt() {
  const box = $('#tab-prompt');
  box.innerHTML = '<div class="empty">组装中…</div>';
  try {
    const r = await api('/api/preview', { blueprint: S.bp, episode: S.detail || 1 });
    box.innerHTML = `<h3 class="sec">这一集真正发给模型的 prompt（约 ${r.est_tokens} tokens）</h3>
      <div style="font-size:12px;color:var(--tx3);margin-bottom:8px">
        第 ${r.episode} 集 · 上方为 system，下方为 user。改蓝图会立刻反映到这里。</div>
      <pre>${esc('=== SYSTEM ===\n' + r.system + '\n\n=== USER ===\n' + r.user)}</pre>`;
  } catch (e) {
    box.innerHTML = `<div class="issue err"><b>ERR</b><span>${esc(e.message)}</span></div>`;
  }
}

function renderJson() {
  const box = $('#tab-json');
  if (!S.script) { box.innerHTML = '<div class="empty">先生成，再导出 JSON。</div>'; return; }
  const copy = JSON.parse(JSON.stringify(S.script));
  ['element_index', 'library_counts'].forEach((k) => delete copy[k]);
  box.innerHTML = `<h3 class="sec">结构化输出（可直接进配音/分镜流水线）</h3>
    <pre>${esc(JSON.stringify(copy, null, 1))}</pre>`;
}

async function renderExport() {
  const box = $('#tab-export');
  box.innerHTML = '<div class="empty">读取产物目录…</div>';

  let out = { dir: '', files: [] };
  try { out = await api('/api/outputs'); } catch (e) { out.error = e.message; }

  const fmtSize = (n) => (n >= 1048576 ? (n / 1048576).toFixed(1) + ' MB'
    : n >= 1024 ? Math.round(n / 1024) + ' KB' : n + ' B');
  const fmtTime = (ts) => {
    const d = new Date(ts * 1000);
    const p = (x) => String(x).padStart(2, '0');
    return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
  };
  const extName = { md: '剧本', html: '阅读页', json: 'JSON', csv: '分镜表', fountain: 'Fountain' };

  let h = '';

  // ---- 目录 ----
  h += `<h3 class="sec">产物目录</h3>
    <div class="outdir">
      <code id="outdir-path">${esc(out.dir || '（未获取到）')}</code>
      <button class="primary sm" id="btn-open-folder">📂 打开文件夹</button>
      <button class="ghost" id="btn-copy-path">复制路径</button>
    </div>`;
  if (out.error) h += `<div class="issue err" style="margin-top:8px"><b>ERR</b><span>${esc(out.error)}</span></div>`;

  // ---- 本次生成的文件 ----
  if (S.script && S.script.files) {
    h += '<h3 class="sec">本次生成</h3><div class="filelist">';
    for (const [k, path] of Object.entries(S.script.files)) {
      const name = String(path).replace(/\\/g, '/').split('/').pop();
      h += `<div class="fileitem">
        <span class="ftag">${esc(extName[k] || k)}</span>
        <span class="fname">${esc(name)}</span>
        <a class="fbtn" href="/api/download?name=${encodeURIComponent(name)}">下载</a>
        <button class="fbtn" data-locate="${esc(name)}">定位</button>
      </div>`;
    }
    h += '</div>';
  }

  // ---- 全部产物 ----
  const files = out.files || [];
  if (files.length) {
    h += `<h3 class="sec">历史产物（${files.length}）</h3><div class="filelist">`;
    for (const f of files) {
      h += `<div class="fileitem">
        <span class="ftag">${esc(extName[f.ext] || f.ext)}</span>
        <span class="fname">${esc(f.name)}</span>
        <span class="fmeta">${fmtSize(f.size)} · ${fmtTime(f.mtime)}</span>
        <a class="fbtn" href="/api/download?name=${encodeURIComponent(f.name)}">下载</a>
        <button class="fbtn" data-locate="${esc(f.name)}">定位</button>
      </div>`;
    }
    h += '</div>';
  } else {
    h += '<h3 class="sec">历史产物</h3><div class="empty">还没有产物。生成一次剧本就会出现在这里。</div>';
  }

  // ---- 制作口径 ----
  if (S.script) {
    const cost = (S.script.plan || {}).cost_summary || {};
    const chk = (S.script.plan || {}).beat_ladder_check || {};
    h += `<h3 class="sec">制作口径</h3><div class="card" style="font-size:12.5px;line-height:1.9">
      · 场景 ${cost.distinct_scenes || 0} 个，群戏 ${cost.crowd_scenes || 0} 场，平均在场 ${cost.avg_cast || 0} 人<br>
      · 爽点相邻最大差 ${chk.max_adjacent_delta == null ? '—' : chk.max_adjacent_delta}（铁律 ≤2），
        每 10 集峰值 ≥${chk.min_peak_per_10 == null ? '—' : chk.min_peak_per_10}，
        9–10 档 ${chk.count_9_10 || 0} 次（铁律 ≤3）<br>
      · 称谓线：${((S.script.plan || {}).register_timeline || []).map((r) => 'E' + r.episode + ' ' + r.register).join(' → ')}
    </div>`;
  }

  box.innerHTML = h;

  // ---- 事件 ----
  const openFolder = async (name) => {
    try {
      await api('/api/open-folder', name ? { name } : {});
      toast(name ? '已在文件管理器中定位' : '已打开产物目录');
    } catch (e) { toast('打不开：' + e.message); }
  };
  const btnOpen = box.querySelector('#btn-open-folder');
  if (btnOpen) btnOpen.onclick = () => openFolder();
  const btnCopy = box.querySelector('#btn-copy-path');
  if (btnCopy) {
    btnCopy.onclick = async () => {
      try { await navigator.clipboard.writeText(out.dir || ''); toast('路径已复制'); }
      catch (e) { toast('复制失败，手动选中路径吧'); }
    };
  }
  box.querySelectorAll('[data-locate]').forEach((b) => {
    b.onclick = () => openFolder(b.dataset.locate);
  });
}

/* ============================================================ */
init();
