/* RIP Casino — Telegram Mini App client */

// ================= config =================
// API base — fill in at build time via CONFIG.js or fallback to same origin parent
const API_BASE = window.KAIRO_API_BASE || 'https://kairo-em51.onrender.com';

// ================= telegram sdk =================
const tg = window.Telegram?.WebApp;
if (tg) {
  tg.expand();
  tg.ready();
  tg.setHeaderColor('#0e0f14');
  tg.setBackgroundColor('#0e0f14');
  // Prevent accidental swipe-down closing while scrolling inventory etc.
  try { tg.disableVerticalSwipes?.(); } catch (e) { /* older SDK */ }
  try { tg.enableClosingConfirmation?.(); } catch (e) {}
}

const INIT_DATA = tg?.initData || '';
const TG_USER = tg?.initDataUnsafe?.user || null;

// ================= state =================
let state = {
  me: null,
  cases: [],
  inventory: null,
  leaderboard: null,
  currentCase: null,
};

// ================= api helpers =================
async function api(path, options = {}) {
  const opts = {
    ...options,
    headers: {
      'X-Telegram-Init-Data': INIT_DATA,
      'Content-Type': 'application/json',
      ...(options.headers || {}),
    },
  };
  const url = `${API_BASE}${path}`;
  // NEVER retry non-idempotent methods — server may have already committed the
  // mutation (e.g. slot spin, case open, gear buy) even if the client aborted.
  // Retrying causes double-processing: user sees second response but balance
  // reflects both transactions.
  const method = (options.method || 'GET').toUpperCase();
  const isMutation = method !== 'GET' && method !== 'HEAD';
  const maxRetries = isMutation ? 0 : 2;
  let lastErr = null;
  for (let attempt = 0; attempt <= maxRetries; attempt++) {
    try {
      const ctrl = new AbortController();
      // Mutations get a longer timeout so we don't abort during cold-start
      // (Render free tier can take 15-30s to wake up).
      const timeoutId = setTimeout(() => ctrl.abort(), isMutation ? 45000 : 10000);
      const resp = await fetch(url, { ...opts, signal: ctrl.signal });
      clearTimeout(timeoutId);
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: resp.statusText }));
        throw new Error(err.detail || `HTTP ${resp.status}`);
      }
      return resp.json();
    } catch (e) {
      lastErr = e;
      const msg = String(e?.message || e);
      const isNetwork = msg === 'Failed to fetch' || msg.includes('NetworkError') || e?.name === 'AbortError';
      if (!isNetwork || attempt === maxRetries) break;
      await new Promise(r => setTimeout(r, 300 * (attempt + 1)));
    }
  }
  // Normalize message so users see something readable
  const msg = String(lastErr?.message || lastErr);
  if (msg === 'Failed to fetch' || msg.includes('NetworkError') || msg.includes('aborted')) {
    throw new Error('Сеть лагает, попробуй ещё раз');
  }
  throw lastErr;
}

// ================= utils =================
function fmt(n) {
  return new Intl.NumberFormat('ru-RU').format(Math.round(n));
}

function toast(msg, duration = 2400) {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.classList.remove('hidden');
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.add('hidden'), duration);
}

function showView(name) {
  document.querySelectorAll('.view').forEach(v => v.classList.toggle('active', v.dataset.view === name));
  document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.target === name));
  if (tg && name !== 'home') tg.BackButton.show();
  else if (tg) tg.BackButton.hide();
}

// ================= loaders =================
async function loadMe() {
  try {
    state.me = await api('/api/me');
    renderMe();
  } catch (e) {
    toast(`Ошибка: ${e.message}`);
  }
}

async function loadCases() {
  const grid = document.getElementById('cases-grid');
  grid.innerHTML = '<div class="loader">Загрузка...</div>';
  try {
    state.cases = await api('/api/cases');
    renderCases();
  } catch (e) {
    grid.innerHTML = `<div class="loader">Ошибка: ${e.message}</div>`;
  }
}

async function loadInventory() {
  const grid = document.getElementById('inventory-grid');
  grid.innerHTML = '<div class="loader">Загрузка...</div>';
  try {
    state.inventory = await api('/api/inventory');
    renderInventory();
  } catch (e) {
    grid.innerHTML = `<div class="loader">Ошибка: ${e.message}</div>`;
  }
}

async function loadLeaderboard() {
  const list = document.getElementById('leaderboard-list');
  list.innerHTML = '<div class="loader">Загрузка...</div>';
  try {
    state.leaderboard = await api('/api/leaderboard');
    renderLeaderboard();
  } catch (e) {
    list.innerHTML = `<div class="loader">Ошибка: ${e.message}</div>`;
  }
}

// ================= renderers =================
async function renderMe() {
  const me = state.me;
  if (!me) return;

  document.getElementById('balance-display').textContent = fmt(me.balance);
  document.getElementById('stat-earned').textContent = fmt(me.total_earned);
  document.getElementById('stat-spent').textContent = fmt(me.total_spent);
  document.getElementById('stat-items').textContent = me.inventory_count;
  document.getElementById('stat-best-streak').textContent = `${me.best_streak} 🔥`;
  document.getElementById('streak-display').textContent = `🔥 ${me.current_streak}`;
  document.getElementById('cases-opened-display').textContent = `${me.cases_opened} кейсов`;

  // Level + title (fetch in background)
  try {
    const lvl = await api('/api/level');
    document.getElementById('level-num').textContent = lvl.level;
    const span = (lvl.next_level_xp - lvl.current_level_xp) || 1;
    const cur = Math.max(0, lvl.xp - lvl.current_level_xp);
    const pct = Math.max(0, Math.min(100, (cur / span) * 100));
    document.getElementById('level-fill').style.width = pct + '%';
    document.getElementById('level-xp').textContent = `${fmt(cur)} / ${fmt(span)} XP`;
  } catch (e) { /* non-critical */ }
  try {
    const ach = await api('/api/achievements');
    const titleEl = document.getElementById('profile-title');
    titleEl.textContent = ach.active_title || '';
  } catch (e) {}

  const name = me.username ? `@${me.username}` : (me.first_name || 'Игрок');
  document.getElementById('profile-name').textContent = name;

  const avatar = document.getElementById('profile-avatar');
  if (me.photo_url) {
    avatar.innerHTML = `<img src="${me.photo_url}" alt="" />`;
  } else {
    avatar.textContent = (me.first_name || '?').charAt(0).toUpperCase();
  }

  // daily button state
  const dailyBtn = document.getElementById('daily-btn');
  if (me.last_daily_at) {
    const diffHours = (Date.now() - new Date(me.last_daily_at).getTime()) / 3600000;
    if (diffHours < 23) {
      dailyBtn.classList.add('disabled');
      const remainHours = Math.ceil(23 - diffHours);
      dailyBtn.querySelector('.btn-text').textContent = `Через ~${remainHours} ч`;
      return;
    }
  }
  dailyBtn.classList.remove('disabled');
  dailyBtn.querySelector('.btn-text').textContent = 'Забрать ежедневные';
}

function renderCases() {
  const grid = document.getElementById('cases-grid');
  if (!state.cases.length) {
    grid.innerHTML = '<div class="loader">Кейсов нет. Админ должен запустить /seed_economy в боте.</div>';
    return;
  }
  const canAfford = c => !!state.me && state.me.balance >= c.price;

  grid.innerHTML = state.cases.map(c => {
    const preview = (c.preview_items || []).slice(0, 4);
    // Prefer the official CS2 case PNG if seeded; fall back to top-rarity weapon.
    const hero = c.image_url || (preview[0] && preview[0].image_url) || '';
    const isOfficialCase = Boolean(c.image_url);
    const topRarity = (preview[0] && preview[0].rarity) || 'mil-spec';
    const locked = !canAfford(c);
    return `
      <div class="case-tile rarity-border-${topRarity} ${locked ? 'locked' : ''}" data-case-id="${c.id}">
        <div class="case-tile-glow"></div>
        <div class="case-tile-hero ${isOfficialCase ? 'is-case' : ''}">
          ${hero ? `<img src="${hero}" alt="" />` : ''}
        </div>
        <div class="case-tile-body">
          <div class="case-tile-name">${escape(c.name)}</div>
          <div class="case-tile-desc">${escape(c.description || '')}</div>
          <div class="case-tile-preview-strip">
            ${preview.map(it => `
              <div class="preview-thumb rarity-${it.rarity}">
                <img src="${it.image_url}" alt="" />
              </div>
            `).join('')}
          </div>
        </div>
        <div class="case-tile-footer">
          <div class="case-tile-price ${locked ? 'locked' : ''}">
            ${locked ? '🔒' : '🪙'} ${fmt(c.price)}
          </div>
          <div class="case-tile-open">открыть →</div>
        </div>
      </div>
    `;
  }).join('');

  grid.querySelectorAll('.case-tile').forEach(card => {
    card.addEventListener('click', () => openCasePreview(parseInt(card.dataset.caseId)));
  });
}

async function openCasePreview(caseId) {
  showView('case-preview');
  const wrap = document.getElementById('case-preview-content');
  wrap.innerHTML = '<div class="loader">Загрузка...</div>';
  try {
    const data = await api(`/api/case/${caseId}/pool`);
    state.currentCase = data;
    wrap.innerHTML = `
      <div class="case-preview-header">
        <h2 class="case-preview-name">${escape(data.name)}</h2>
        <div class="case-preview-desc">${escape(data.description || '')}</div>
      </div>
      <div class="case-preview-summary">Всего в пуле: <b>${data.items.length}</b> скинов</div>
      <div class="case-preview-items">
        ${data.items.map(it => {
          const parts = (it.name || '').split('|').map(s => s.trim());
          const weapon = parts[0] || '';
          const skin = parts[1] || it.name;
          return `
          <div class="item-card rarity-${it.rarity}" title="${escape(it.name)}">
            <img class="item-card-img" src="${it.image_url}" alt="" loading="lazy" />
            <div class="item-card-weapon">${escape(weapon)}</div>
            <div class="item-card-name">${escape(skin)}</div>
            <div class="item-card-price">${fmt(it.base_price)} 🪙</div>
          </div>
          `;
        }).join('')}
      </div>
      <div class="open-case-fixed">
        <button class="btn big-btn daily-btn" id="case-preview-open-btn">
          <span class="btn-icon">🎁</span>
          <span class="btn-text">Открыть за ${fmt(data.price)} 🪙</span>
        </button>
        <button class="btn big-btn case-open-5x-btn" id="case-preview-open5-btn">
          ⚡ Открыть 5× за ${fmt(data.price * 5)} 🪙
        </button>
      </div>
    `;
    document.getElementById('case-preview-open-btn').addEventListener('click', () => openCase(caseId));
    document.getElementById('case-preview-open5-btn').addEventListener('click', () => openCaseMulti(caseId, 5));
  } catch (e) {
    wrap.innerHTML = `<div class="loader">Ошибка: ${e.message}</div>`;
  }
}

async function openCase(caseId) {
  const caseData = state.currentCase;
  if (!caseData) return;

  showView('case-open');
  document.getElementById('case-open-title').textContent = caseData.name;
  document.getElementById('case-open-result').classList.remove('shown');
  document.getElementById('case-open-result').innerHTML = '';
  document.getElementById('case-open-actions').style.display = 'none';

  // Start API call immediately (parallel with animation)
  let result;
  try {
    result = await api('/api/case/open', { method: 'POST', body: JSON.stringify({ case_id: caseId }) });
  } catch (e) {
    toast(`Не открылся: ${e.message}`);
    showView('cases');
    return;
  }

  tg?.HapticFeedback?.impactOccurred?.('medium');

  // Build reel: 60 random items from pool + winner at specific position
  const pool = caseData.items;
  const reelCount = 60;
  const winnerIndex = 53; // position where reel stops
  const reel = [];
  for (let i = 0; i < reelCount; i++) {
    if (i === winnerIndex) {
      reel.push({ ...result.skin, isWinner: true });
    } else {
      reel.push(pool[Math.floor(Math.random() * pool.length)]);
    }
  }

  const track = document.getElementById('case-open-track');
  track.innerHTML = reel.map(it => `
    <div class="reel-item rarity-${it.rarity}">
      <img src="${it.image_url}" alt="" />
      <div class="reel-item-name">${escape(it.name || it.full_name)}</div>
    </div>
  `).join('');

  // reset position, trigger reflow, then animate
  track.style.transition = 'none';
  track.style.transform = 'translateX(0)';
  await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
  track.style.transition = 'transform 6s cubic-bezier(0.15, 0.45, 0.1, 1)';

  // calculate final offset: item width 150 + gap 6 = 156px each
  const itemW = 156;
  const containerHalf = (document.querySelector('.case-open-reel').offsetWidth / 2);
  const offset = (winnerIndex * itemW) - containerHalf + (itemW / 2);
  const jitter = (Math.random() - 0.5) * (itemW * 0.4); // small shake for realism
  track.style.transform = `translateX(-${offset + jitter}px)`;

  // after anim — show result as fullscreen overlay (no scroll needed)
  setTimeout(() => {
    tg?.HapticFeedback?.notificationOccurred?.(
      (result.skin.rarity === 'covert' || result.skin.rarity === 'exceedingly_rare') ? 'success' : 'warning'
    );
    const stBadge = result.stat_trak ? '<div class="stattrak-badge">ST™</div>' : '';
    let overlay = document.getElementById('case-result-overlay');
    if (!overlay) {
      overlay = document.createElement('div');
      overlay.id = 'case-result-overlay';
      overlay.className = 'case-result-overlay';
      document.body.appendChild(overlay);
    }
    overlay.innerHTML = `
      <div class="case-result-modal rarity-${result.skin.rarity}">
        ${stBadge}
        <img src="${result.skin.image_url}" alt="" />
        <div class="result-name">${escape(result.skin.full_name)}</div>
        <div class="result-meta">${result.wear.replace('_', '-').toUpperCase()} • float ${result.float.toFixed(4)}</div>
        <div class="result-price">+${fmt(result.price)} 🪙 в инвентарь</div>
        ${(result.achievements && result.achievements.length) ? `
          <div style="margin-top:12px;padding:10px;background:rgba(245,176,66,0.1);border-radius:8px">
            <div style="font-size:11px;color:var(--accent-gold);font-weight:700">🏆 АЧИВКА</div>
            ${result.achievements.map(a => `<div style="font-size:13px;margin-top:4px">${escape(a.name)}</div>`).join('')}
          </div>
        ` : ''}
        ${(result.level && result.level.leveled_up) ? `
          <div style="margin-top:10px;padding:10px;background:rgba(136,71,255,0.15);border-radius:8px;font-size:13px">
            ⭐ Уровень <b>${result.level.new_level}</b>${result.level.perk ? ' — ' + escape(result.level.perk) : ''}
          </div>
        ` : ''}
        <div class="result-actions">
          <button class="btn daily-btn" id="case-overlay-again">Открыть ещё</button>
          <button class="btn secondary" id="case-overlay-inv">В инвентарь</button>
        </div>
      </div>
    `;
    overlay.classList.remove('hidden');

    // update balance shown top
    state.me.balance = result.new_balance;
    state.me.cases_opened += 1;
    document.getElementById('balance-display').textContent = fmt(state.me.balance);

    document.getElementById('case-overlay-again').onclick = () => {
      overlay.classList.add('hidden');
      overlay.innerHTML = '';
      openCase(caseId);
    };
    document.getElementById('case-overlay-inv').onclick = () => {
      overlay.classList.add('hidden');
      overlay.innerHTML = '';
      showView('inventory');
      loadInventory();
    };
  }, 6100);
}

// =================== MULTI-OPEN ===================
async function openCaseMulti(caseId, count) {
  const caseData = state.currentCase;
  if (!caseData) return;
  const totalCost = caseData.price * count;
  if (!confirm(`Открыть ${count}× за ${fmt(totalCost)} 🪙?`)) return;

  showView('case-open');
  const titleEl = document.getElementById('case-open-title');
  const resultEl = document.getElementById('case-open-result');
  const actionsEl = document.getElementById('case-open-actions');
  titleEl.textContent = `${caseData.name} × ${count}`;
  resultEl.classList.remove('shown');
  resultEl.innerHTML = `<div class="multi-open-loader">⚡ Открываем ${count} кейсов…</div>`;
  actionsEl.style.display = 'none';

  let resp;
  try {
    resp = await api('/api/case/open_multi', {
      method: 'POST',
      body: JSON.stringify({ case_id: caseId, count }),
    });
  } catch (e) {
    toast(`Не открылся: ${e.message}`);
    showView('cases');
    return;
  }
  if (!resp.ok) {
    toast(resp.error || 'Ошибка');
    showView('cases');
    return;
  }

  tg?.HapticFeedback?.impactOccurred?.('heavy');
  // Update balance from last result
  const last = resp.results[resp.results.length - 1];
  if (last && typeof last.new_balance === 'number') {
    state.me.balance = last.new_balance;
    document.getElementById('balance-display').textContent = fmt(state.me.balance);
  }

  // Render result grid (one card per opened case) with stagger fade-in
  const cards = resp.results.map((r, i) => {
    const it = r.item;
    return `
      <div class="multi-open-card rarity-${it.rarity}" style="animation-delay:${i * 150}ms">
        ${it.stat_trak ? '<div class="stattrak-badge">ST™</div>' : ''}
        <img class="result-img" src="${it.image_url}" alt="" />
        <div class="result-name">${escape(it.name)}</div>
        <div class="result-meta">${it.wear_short} · ${fmt(it.price)} 🪙</div>
      </div>
    `;
  }).join('');

  // Calculate net delta
  const totalGot = resp.results.reduce((sum, r) => sum + (r.item?.price || 0), 0);
  const netDelta = totalGot - totalCost;

  resultEl.innerHTML = `
    <div class="multi-open-summary ${netDelta >= 0 ? 'win' : 'lose'}">
      Получил скинов на <b>${fmt(totalGot)} 🪙</b> (${netDelta >= 0 ? '+' : ''}${fmt(netDelta)} 🪙)
    </div>
    <div class="multi-open-grid">${cards}</div>
  `;
  resultEl.classList.add('shown');
  actionsEl.style.display = 'flex';

  // Top haptic on big wins
  if (netDelta > totalCost) tg?.HapticFeedback?.notificationOccurred?.('success');

  loadInventory();  // refresh inventory in background
}

const invFilter = { rarity: '', sort: 'price_desc' };
const invSelection = { active: false, ids: new Set() };

function _sortInventory(items) {
  const copy = items.slice();
  switch (invFilter.sort) {
    case 'price_asc':  copy.sort((a, b) => a.price - b.price); break;
    case 'price_desc': copy.sort((a, b) => b.price - a.price); break;
    case 'recent':     copy.sort((a, b) => new Date(b.acquired_at) - new Date(a.acquired_at)); break;
    case 'float_asc':  copy.sort((a, b) => a.float - b.float); break;
    case 'float_desc': copy.sort((a, b) => b.float - a.float); break;
  }
  return copy;
}

function _filterInventory(items) {
  if (!invFilter.rarity) return items;
  return items.filter(i => i.rarity === invFilter.rarity);
}

function renderInventory() {
  const grid = document.getElementById('inventory-grid');
  const inv = state.inventory;
  document.getElementById('inv-count').textContent = inv.count;
  document.getElementById('inv-value').textContent = fmt(inv.total_value);

  if (!inv.items.length) {
    grid.innerHTML = '<div class="loader">Пусто. Открой первый кейс!</div>';
    return;
  }

  const filtered = _sortInventory(_filterInventory(inv.items));
  if (!filtered.length) {
    grid.innerHTML = '<div class="loader">Нет предметов этой редкости.</div>';
    return;
  }

  grid.classList.toggle('inv-grid-select', invSelection.active);
  grid.innerHTML = filtered.map(it => {
    const selected = invSelection.ids.has(it.id) ? 'selected' : '';
    return `
    <div class="inv-item rarity-${it.rarity} ${selected}" data-inv-id="${it.id}">
      <div class="sel-check"></div>
      ${it.stat_trak ? '<div class="stattrak-badge">ST™</div>' : ''}
      <img class="inv-item-img" src="${it.image_url}" alt="" loading="lazy" />
      <div class="inv-item-weapon">${escape(it.weapon)}</div>
      <div class="inv-item-name">${escape(it.skin_name)}</div>
      <div class="inv-item-wear">${it.wear_short} · ${it.float.toFixed(3)}</div>
      <div class="inv-item-price">${fmt(it.price)} 🪙</div>
    </div>
  `;
  }).join('');

  grid.querySelectorAll('.inv-item').forEach(card => {
    card.addEventListener('click', () => {
      const id = parseInt(card.dataset.invId);
      if (invSelection.active) {
        if (invSelection.ids.has(id)) invSelection.ids.delete(id);
        else invSelection.ids.add(id);
        updateBulkBar();
        renderInventory();
      } else {
        showItemDetail(id);
      }
    });
  });
}

function updateBulkBar() {
  const bar = document.getElementById('inv-bulk-bar');
  const cntEl = document.getElementById('inv-sel-count');
  if (!bar) return;
  bar.classList.toggle('hidden', !invSelection.active);
  if (cntEl) cntEl.textContent = invSelection.ids.size;
}

document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('inv-select-mode')?.addEventListener('click', () => {
    invSelection.active = !invSelection.active;
    if (!invSelection.active) invSelection.ids.clear();
    document.getElementById('inv-select-mode').textContent = invSelection.active ? 'Готово' : 'Выбрать';
    document.getElementById('inv-select-mode').classList.toggle('active', invSelection.active);
    updateBulkBar();
    renderInventory();
  });
  document.getElementById('inv-bulk-cancel')?.addEventListener('click', () => {
    invSelection.active = false;
    invSelection.ids.clear();
    document.getElementById('inv-select-mode').textContent = 'Выбрать';
    document.getElementById('inv-select-mode').classList.remove('active');
    updateBulkBar();
    renderInventory();
  });
  document.getElementById('inv-bulk-all')?.addEventListener('click', () => {
    if (!state.inventory) return;
    // Auto-enter selection mode if not already (covers the "Все first, then Продать" flow)
    if (!invSelection.active) {
      invSelection.active = true;
      const modeBtn = document.getElementById('inv-select-mode');
      if (modeBtn) {
        modeBtn.textContent = 'Готово';
        modeBtn.classList.add('active');
      }
    }
    // Toggle on visible (filtered) items: if all already selected — deselect; else — select all
    const visible = _sortInventory(_filterInventory(state.inventory.items));
    const allVisibleSelected = visible.length > 0 && visible.every(it => invSelection.ids.has(it.id));
    if (allVisibleSelected) {
      visible.forEach(it => invSelection.ids.delete(it.id));
      toast(`Снято выделение с ${visible.length}`);
    } else {
      visible.forEach(it => invSelection.ids.add(it.id));
      toast(`✓ Выбрано ${visible.length} предметов`);
    }
    updateBulkBar();
    renderInventory();
  });
  document.getElementById('inv-bulk-sell')?.addEventListener('click', async () => {
    if (invSelection.ids.size === 0) return toast('Ничего не выбрано');
    const ids = Array.from(invSelection.ids);
    const totalPrice = (state.inventory?.items || [])
      .filter(i => invSelection.ids.has(i.id))
      .reduce((sum, i) => sum + Math.round(i.price * 0.7), 0);
    if (!confirm(`Продать ${ids.length} предметов за ~${fmt(totalPrice)} 🪙?`)) return;
    try {
      const r = await api('/api/sell_bulk', { method: 'POST', body: JSON.stringify({ inventory_ids: ids }) });
      if (r.ok) {
        tg?.HapticFeedback?.notificationOccurred?.('success');
        toast(`✅ Продано ${r.sold_count}. +${fmt(r.payout)} 🪙`);
        state.me.balance = r.new_balance;
        document.getElementById('balance-display').textContent = fmt(state.me.balance);
        invSelection.ids.clear();
        invSelection.active = false;
        document.getElementById('inv-select-mode').textContent = 'Выбрать';
        document.getElementById('inv-select-mode').classList.remove('active');
        updateBulkBar();
        await loadInventory();
      }
    } catch (e) { toast(e.message); }
  });
});

// Wire filter controls once DOM is ready
document.addEventListener('DOMContentLoaded', () => {
  const sortEl = document.getElementById('inv-sort');
  sortEl?.addEventListener('change', () => {
    invFilter.sort = sortEl.value;
    if (state.inventory) renderInventory();
  });
  document.querySelectorAll('#inv-rarity-chips .chip').forEach(chip => {
    chip.addEventListener('click', () => {
      document.querySelectorAll('#inv-rarity-chips .chip').forEach(c => c.classList.remove('active'));
      chip.classList.add('active');
      invFilter.rarity = chip.dataset.rarity || '';
      if (state.inventory) renderInventory();
    });
  });
});

function showItemDetail(invId) {
  const it = state.inventory.items.find(i => i.id === invId);
  if (!it) return;
  const body = document.getElementById('item-modal-body');
  const dealerPayout = Math.max(1, Math.round(it.price * 0.7));
  body.innerHTML = `
    <div class="result-card rarity-${it.rarity}" style="border:0; padding:0; position:relative">
      ${it.stat_trak ? '<div class="stattrak-badge">ST™</div>' : ''}
      <img class="result-img" src="${it.image_url}" />
      <div class="result-name">${escape(it.name)}</div>
      <div class="result-meta">
        ${it.rarity_emoji} ${escape(it.rarity_label)}<br>
        ${escape(it.wear_label)} • float ${it.float.toFixed(4)}
      </div>
      <div class="result-price">${fmt(it.price)} 🪙</div>
    </div>
    <div style="display:flex; gap:8px; margin-top:16px; flex-wrap:wrap">
      <button class="btn" id="item-sell-btn" style="flex:1; background:linear-gradient(135deg,var(--accent) 0%,var(--accent-gold) 100%);color:#0e0f14;border:0;font-weight:800">
        Продать за ${fmt(dealerPayout)} 🪙
      </button>
      <button class="btn secondary" disabled style="flex:1">Трейд (скоро)</button>
    </div>
  `;
  document.getElementById('item-modal').classList.remove('hidden');

  document.getElementById('item-sell-btn').addEventListener('click', async () => {
    if (!confirm(`Продать за ${fmt(dealerPayout)} 🪙?`)) return;
    try {
      const r = await api('/api/sell', { method: 'POST', body: JSON.stringify({ inventory_id: it.id }) });
      tg?.HapticFeedback?.notificationOccurred?.('success');
      toast(`+${fmt(r.payout)} 🪙`);
      document.getElementById('item-modal').classList.add('hidden');
      state.me.balance = r.new_balance;
      document.getElementById('balance-display').textContent = fmt(state.me.balance);
      await loadInventory();
    } catch (e) {
      toast(`Ошибка: ${e.message}`);
    }
  });
}

// ============== GAMES ==============

document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.game-card').forEach(card => {
    card.addEventListener('click', () => {
      openGameScreen(card.dataset.game);
    });
  });
});

function openGameScreen(gameKey) {
  const grid = document.querySelector('.game-grid');
  if (grid) grid.style.display = 'none';
  const area = document.getElementById('game-play-area');
  area.innerHTML = `<button class="back-btn" id="game-back-btn" style="margin-bottom:10px">← к играм</button>`;
  const holder = document.createElement('div');
  area.appendChild(holder);
  document.getElementById('game-back-btn').addEventListener('click', closeGameScreen);
  renderGamePlay(gameKey, holder);
}

function closeGameScreen() {
  const grid = document.querySelector('.game-grid');
  if (grid) grid.style.display = '';
  document.getElementById('game-play-area').innerHTML = '';
}

function renderGamePlay(game, target) {
  // `target` is where to render; falls back to game-play-area for safety
  const area = target || document.getElementById('game-play-area');
  if (game === 'coinflip') {
    area.innerHTML = `
      <div class="game-play">
        <h3>🪙 Coinflip</h3>
        <label>Ставка</label>
        <input type="text" inputmode="numeric" pattern="[0-9]*" id="cf-bet" value="100" autocomplete="off" />
        <div class="game-row">
          <button class="btn" data-side="heads">🪙 Орёл</button>
          <button class="btn" data-side="tails">✨ Решка</button>
        </div>
        <div class="cf-result" id="cf-result">🪙</div>
        <div class="game-out" id="cf-out" style="display:none"></div>
      </div>
    `;
    area.querySelectorAll('[data-side]').forEach(btn => {
      btn.addEventListener('click', () => playCoinflip(btn.dataset.side));
    });
  } else if (game === 'slots') {
    area.innerHTML = `
      <div class="game-play">
        <h3>🎰 Слоты</h3>
        <label>Ставка</label>
        <input type="text" inputmode="numeric" pattern="[0-9]*" id="sl-bet" value="100" autocomplete="off" />
        <div class="slots-machine" id="sl-machine">
          <div class="slot-reel" id="sl-reel-0"><span>❓</span></div>
          <div class="slot-reel" id="sl-reel-1"><span>❓</span></div>
          <div class="slot-reel" id="sl-reel-2"><span>❓</span></div>
        </div>
        <button class="btn big-btn daily-btn" id="sl-spin">Крутить</button>
        <div class="game-out" id="sl-out" style="display:none"></div>
      </div>
    `;
    document.getElementById('sl-spin').addEventListener('click', playSlots);
  } else if (game === 'crash') {
    area.innerHTML = `
      <div class="game-play">
        <h3>💥 Crash</h3>
        <label>Ставка (макс 10 000)</label>
        <input type="text" inputmode="numeric" pattern="[0-9]*" id="cr-bet" value="100" autocomplete="off" />
        <label>Таргет множитель (1.20 – 50.00)</label>
        <input type="text" inputmode="decimal" id="cr-target" value="2" autocomplete="off" />
        <button class="btn big-btn daily-btn" id="cr-play">Играть</button>
        <div class="game-out" id="cr-out" style="display:none"></div>
      </div>
    `;
    document.getElementById('cr-play').addEventListener('click', playCrash);
  } else if (game === 'megaslot') {
    renderMegaslot(area);
  } else if (game === 'forge') {
    renderForge(area);
  }
}

// ======================= FORGE =======================

const forgeState = {
  state: null,
  branches: null,
  pendingClicks: 0,
  flushTimer: null,
  lastHitAt: 0,
  comboCount: 0,
  comboResetTimer: null,
  area: null,
  pollTimer: null,
  animFrame: null,
  displayedHp: 0,
  lastAnimAt: 0,
};

let _lastHpIntRendered = -1;
let _lastMaxHpRendered = -1;
function _renderHpDisplay(hp, maxHp) {
  const hpClamped = Math.max(0, hp);
  const hpInt = Math.ceil(hpClamped);
  // Skip DOM write if visible integer HP + max_hp unchanged (saves ~60 writes/sec during rAF).
  if (hpInt === _lastHpIntRendered && maxHp === _lastMaxHpRendered) return;
  _lastHpIntRendered = hpInt;
  _lastMaxHpRendered = maxHp;

  const hpFill = document.getElementById('hp-fill');
  const hpText = document.getElementById('hp-text');
  const pct = Math.max(0, (hpClamped / maxHp) * 100);
  if (hpFill) {
    hpFill.style.width = pct + '%';
    hpFill.classList.toggle('low', pct < 30);
  }
  if (hpText) hpText.textContent = `${fmt(hpInt)} / ${fmt(maxHp)}`;
}
function _resetHpRenderCache() { _lastHpIntRendered = -1; _lastMaxHpRendered = -1; }

function _stopForgePolling() {
  if (forgeState.pollTimer) {
    clearInterval(forgeState.pollTimer);
    forgeState.pollTimer = null;
  }
  if (forgeState.animFrame) {
    cancelAnimationFrame(forgeState.animFrame);
    forgeState.animFrame = null;
  }
  if (forgeState.flushTimer) {
    clearTimeout(forgeState.flushTimer);
    forgeState.flushTimer = null;
  }
  forgeState.pendingClicks = 0;
}

function _startForgePolling() {
  _stopForgePolling();
  // Seed displayed HP from current state
  forgeState.displayedHp = forgeState.state?.weapon?.hp ?? 0;
  forgeState.lastAnimAt = performance.now();

  // Client-side smooth HP drain between server polls, based on afk_rate_per_sec.
  // Server poll reconciles to authoritative value every 1s.
  const animLoop = (ts) => {
    if (!forgeState.pollTimer) return; // polling stopped
    const dt = Math.max(0, (ts - forgeState.lastAnimAt) / 1000);
    forgeState.lastAnimAt = ts;
    const s = forgeState.state;
    const w = s?.weapon;
    const rate = s?.effects?.afk_rate_per_sec || 0;
    if (w && rate > 0 && forgeState.displayedHp > 0) {
      forgeState.displayedHp = Math.max(0, forgeState.displayedHp - rate * dt);
      _renderHpDisplay(forgeState.displayedHp, w.max_hp);
    }
    forgeState.animFrame = requestAnimationFrame(animLoop);
  };
  forgeState.animFrame = requestAnimationFrame(animLoop);

  // Server poll — authoritative every 1s. Skipped while batch flusher is busy:
  // the flush response already returns authoritative state, poll would duplicate.
  forgeState.pollTimer = setInterval(async () => {
    const activeView = document.querySelector('.view.active')?.dataset.view;
    if (activeView !== 'games' || !document.getElementById('weapon-img')) {
      _stopForgePolling();
      return;
    }
    // Skip if there's a pending flush or clicks queued — flush will sync state
    if (forgeState.flushTimer || forgeState.pendingClicks > 0) return;
    try {
      const fresh = await api('/api/forge/state');
      const oldId = forgeState.state?.weapon?.skin_id;
      forgeState.state = fresh;
      if (fresh.weapon.skin_id !== oldId) {
        forgeState.displayedHp = fresh.weapon.hp;
        swapWeaponInPlace(fresh.weapon);
      } else {
        // Reconcile client prediction toward server truth. If server says less HP,
        // jump down immediately; if more (unlikely), accept server.
        forgeState.displayedHp = Math.min(forgeState.displayedHp, fresh.weapon.hp);
        if (forgeState.displayedHp > fresh.weapon.hp) {
          forgeState.displayedHp = fresh.weapon.hp;
        }
        _renderHpDisplay(forgeState.displayedHp, fresh.weapon.max_hp);
      }
      updateForgeStatsBar();
    } catch (e) {
      /* silent, try again next tick */
    }
  }, 1000);
}

async function renderForge(area) {
  area = area || forgeState.area;
  if (!area) return;
  forgeState.area = area;
  area.innerHTML = `<div class="loader">Загрузка кузницы...</div>`;
  try {
    const [state, branches] = await Promise.all([
      api('/api/forge/state'),
      forgeState.branches ? Promise.resolve(forgeState.branches) : api('/api/forge/tree'),
    ]);
    forgeState.state = state;
    forgeState.branches = branches;
    forgePaint(area);
    // Notify about AFK progress that happened while away
    const afk = state.afk || {};
    if ((afk.just_gained || 0) > 0 || (afk.just_broken || 0) > 0) {
      tg?.HapticFeedback?.notificationOccurred?.('success');
      const brPart = afk.just_broken ? `, ${afk.just_broken} оружий разобрано` : '';
      toast(`🤖 AFK-фарм: +${fmt(afk.just_gained)} ⚙️${brPart}`);
    }
  } catch (e) {
    area.innerHTML = `<div class="loader">Ошибка: ${e.message}</div>`;
  }
}

function forgePaint(area) {
  const s = forgeState.state;
  const w = s.weapon;
  const hpPct = Math.max(0, (w.hp / w.max_hp) * 100);
  const lowHp = hpPct < 30;
  const rarityColor = w.rarity_color || '#8b94a7';
  const stTag = w.stattrak ? ' <span style="color:#ff6633">ST™</span>' : '';

  area.innerHTML = `
    <div class="forge-screen">
      <div class="forge-top">
        <div class="forge-stat particles">⚙️ <span id="hud-particles">${fmt(s.particles)}</span></div>
        <div class="forge-stat afk" title="AFK-бот бьёт оружие автоматом, урон/сек">
          🤖 ${s.effects.afk_rate_per_sec} dmg/с
        </div>
        <div class="forge-stat forge-prestige-chip" style="cursor:pointer" id="forge-prestige-btn" title="Престиж — сброс прогресса за жетоны и вечные бонусы">
          ✨ ${(s.prestige?.level || 0) > 0 ? `P${s.prestige.level}` : 'Престиж'}
        </div>
        <div class="forge-stat forge-boss-chip" style="cursor:pointer" id="forge-boss-btn" title="Боссы — рейды с уникальными наградами">🛡 Боссы</div>
        <div class="forge-stat forge-gear-chip" style="cursor:pointer" id="forge-gear-btn" title="Шмот — магазин и инвентарь экипировки">🛒 Шмот</div>
        <div class="forge-stat" style="cursor:pointer" id="forge-lb-btn">🏆 Топ</div>
      </div>

      <div class="forge-weapon-card">
        <button class="forge-skip-corner" id="forge-skip-btn" title="Пропустить оружие (10% за выбитое HP)" aria-label="Skip">⏭</button>
        <div class="forge-weapon-name">${escape(w.full_name || '—')}${stTag}</div>
        <div class="forge-weapon-rarity" style="color:${rarityColor}">${escape(w.rarity || '')}</div>
        <div class="forge-weapon-image-wrap" id="weapon-wrap">
          <div class="forge-weapon-glow" style="background: radial-gradient(circle, ${rarityColor}88, transparent 60%)"></div>
          <img class="forge-weapon-image" id="weapon-img" src="${w.image_url || ''}" alt="" draggable="false" />
        </div>
        <div class="forge-hp-wrap">
          <div class="forge-hp-bar">
            <div class="forge-hp-fill ${lowHp ? 'low' : ''}" id="hp-fill" style="width:${hpPct}%"></div>
            <div class="forge-hp-text" id="hp-text">${fmt(w.hp)} / ${fmt(w.max_hp)}</div>
          </div>
        </div>
        <div class="forge-combo" id="combo-indicator"></div>
      </div>

      <div class="forge-effects-bar">
        <span>⚒ ${s.effects.damage}</span>
        <span>🎯 ${s.effects.crit_chance}% (x${s.effects.crit_multiplier || 3})</span>
        <span>🍀 +${s.effects.luck_bonus_pct}%</span>
        <span>Разобрано: ${s.total_breaks}</span>
      </div>

      <div class="forge-actions">
        <button class="btn secondary" id="forge-upgrades-btn">🛠 Апгрейды</button>
        <button class="btn" id="forge-exchange-btn" style="background:linear-gradient(135deg,#7dd3fc 0%,#a78bfa 100%);color:#0a0c14;border:0;font-weight:800">💱 Обмен</button>
      </div>
    </div>
  `;

  document.getElementById('weapon-img').addEventListener('click', onForgeHit);
  document.getElementById('forge-upgrades-btn').addEventListener('click', () => renderForgeUpgrades(area));
  document.getElementById('forge-exchange-btn').addEventListener('click', () => renderForgeExchange(area));
  document.getElementById('forge-gear-btn').addEventListener('click', () => renderGear(area));
  document.getElementById('forge-skip-btn').addEventListener('click', onForgeSkip);
  document.getElementById('forge-lb-btn')?.addEventListener('click', () => renderForgeLeaderboard(area));
  document.getElementById('forge-prestige-btn')?.addEventListener('click', () => renderForgePrestige(area));
  document.getElementById('forge-boss-btn')?.addEventListener('click', () => renderForgeBoss(area));
  _startForgePolling();
}

// ============================================================
// PRESTIGE SCREEN
// ============================================================
async function renderForgePrestige(area) {
  _stopForgePolling();
  area.innerHTML = `<button class="back-btn" id="prestige-back">← к кузнице</button><div class="prestige-wrap" id="prestige-root"><div class="loader">Загрузка престижа…</div></div>`;
  document.getElementById('prestige-back').addEventListener('click', () => forgePaint(area));
  try {
    const st = await api('/api/prestige/state');
    _paintPrestige(area, st);
  } catch (e) {
    document.getElementById('prestige-root').innerHTML = `<div class="loader">Ошибка: ${escape(e.message)}</div>`;
  }
}

function _paintPrestige(area, st) {
  const root = document.getElementById('prestige-root');
  if (!root) return;
  const next = st.next_prestige || {};
  const canPrestige = !!next.eligible;
  const runPct = Math.min(100, (st.run_particles / (next.threshold || 1)) * 100);

  root.innerHTML = `
    <div class="prestige-header">
      <div class="prestige-rank">Престиж <b>${st.level}</b></div>
      <div class="prestige-jetons">🎖 <b>${fmt(st.jetons)}</b> жетонов</div>
    </div>
    <div class="prestige-lifetime">За всё время заработано: <b>${fmt(st.jetons_lifetime)}</b></div>

    <div class="prestige-do-card">
      <div class="prestige-run-progress">
        <div class="prestige-run-progress-fill" style="width:${runPct}%"></div>
        <div class="prestige-run-progress-text">
          ${fmt(st.run_particles)} / ${fmt(next.threshold)} particles в этом ране
        </div>
      </div>
      <div class="prestige-do-reward">
        Сбросить сейчас → <b>+${next.jetons_on_prestige || 0}</b> 🎖
      </div>
      <button class="btn prestige-do-btn ${canPrestige ? '' : 'locked'}" id="prestige-do-btn" ${canPrestige ? '' : 'disabled'}>
        ${canPrestige ? '✨ Сбросить и получить жетоны' : `Нужно ${fmt(Math.max(0, next.threshold - st.run_particles))} particles`}
      </button>
    </div>

    <div class="prestige-bonus-grid" id="prestige-bonus-grid">
      ${st.bonuses.map(_bonusCardHtml).join('')}
    </div>
  `;

  document.getElementById('prestige-do-btn')?.addEventListener('click', () => _doPrestige(area));
  root.querySelectorAll('.prestige-bonus-card [data-pbuy]').forEach(btn => {
    btn.addEventListener('click', () => _buyPrestigeBonus(area, btn.dataset.pbuy));
  });
}

function _bonusCardHtml(b) {
  const isMaxed = b.level >= b.max_level;
  const canAfford = b.next_cost !== null && typeof b.next_cost === 'number';
  const progressPct = (b.level / b.max_level) * 100;
  const effFmt = (v) => {
    if (v == null) return '—';
    if (b.unit.startsWith('%')) return `+${(v).toFixed(v < 1 ? 2 : 1)}${b.unit.replace('%', '%')}`;
    if (b.unit.includes('⚙')) return `${fmt(v)} ${b.unit}`;
    return `${v}${b.unit}`;
  };
  return `
    <div class="prestige-bonus-card">
      <div class="prestige-bonus-head">
        <div class="prestige-bonus-name">${escape(b.name)}</div>
        <div class="prestige-bonus-level ${isMaxed ? 'maxed' : ''}">${b.level} / ${b.max_level}</div>
      </div>
      <div class="prestige-bonus-desc">${escape(b.desc)}</div>
      <div class="prestige-bonus-progress"><div class="prestige-bonus-progress-fill" style="width:${progressPct}%"></div></div>
      ${isMaxed
        ? `<div class="prestige-bonus-effect maxed">МАКС — ${effFmt(b.current_total)}</div>`
        : `<div class="prestige-bonus-effect">Сейчас: <b>${effFmt(b.current_total)}</b> → ${effFmt(b.next_total)}</div>
           <button class="btn prestige-bonus-buy" data-pbuy="${b.key}">⬆ ${b.next_cost} 🎖</button>`}
    </div>
  `;
}

async function _doPrestige(area) {
  if (!confirm('Сбросить весь прогресс Forge (уровни, ботов, оружие, particles) и получить жетоны? Lifetime-статы, инвентарь и коины сохранятся.')) return;
  try {
    const r = await api('/api/prestige/do', { method: 'POST' });
    if (!r.ok) { toast(r.error || 'Не удалось'); return; }
    tg?.HapticFeedback?.notificationOccurred?.('success');
    toast(`✨ Престиж! +${r.jetons_earned} 🎖`);
    // Reload prestige state + forge state silently
    forgeState.branches = null;
    const st = await api('/api/prestige/state');
    _paintPrestige(area, st);
  } catch (e) { toast(e.message); }
}

async function _buyPrestigeBonus(area, branch) {
  try {
    const r = await api('/api/prestige/buy', { method: 'POST', body: JSON.stringify({ branch }) });
    if (!r.ok) { toast(r.error || 'Не удалось'); return; }
    tg?.HapticFeedback?.impactOccurred?.('medium');
    const st = await api('/api/prestige/state');
    _paintPrestige(area, st);
  } catch (e) { toast(e.message); }
}

async function onForgeSkip() {
  if (!confirm('Пропустить оружие? Получишь 10% от доли выбитого HP (без урона — 0).')) return;
  try {
    const r = await api('/api/forge/skip', { method: 'POST' });
    if (r.ok) {
      tg?.HapticFeedback?.impactOccurred?.('light');
      toast(r.refund > 0 ? `+${fmt(r.refund)} ⚙️` : 'Скип без награды (оружие не битое)');
      const fresh = await api('/api/forge/state');
      forgeState.state = fresh;
      swapWeaponInPlace(fresh.weapon);
      updateForgeStatsBar();
    } else {
      toast(r.error || 'Нельзя');
    }
  } catch (e) { toast(e.message); }
}

async function renderForgeLeaderboard(area) {
  _stopForgePolling();
  area.innerHTML = `<button class="back-btn" id="lb-back">← к кузнице</button><div class="forge-tree" id="lb-list"><div class="loader">Загрузка...</div></div>`;
  document.getElementById('lb-back').addEventListener('click', () => forgePaint(area));
  try {
    const top = await api('/api/forge/leaderboard');
    const list = document.getElementById('lb-list');
    if (!top.length) {
      list.innerHTML = '<div class="loader">Ещё никто не фармил</div>';
      return;
    }
    const medals = ['🥇', '🥈', '🥉'];
    list.innerHTML = top.map((r, i) => {
      const name = r.username ? '@' + r.username : (r.first_name || 'user' + r.tg_id);
      const rank = medals[i] || `#${i + 1}`;
      const prestigeBadge = (r.prestige || 0) > 0
        ? `<span class="lb-prestige-badge" title="Престиж">✨P${r.prestige}</span>`
        : '';
      return `
        <div class="branch-card" style="padding:10px 14px">
          <div style="display:flex;justify-content:space-between;align-items:center">
            <div>
              <div style="font-weight:800;font-size:15px">${rank} ${escape(name)} ${prestigeBadge}</div>
              <div style="font-size:12px;color:var(--text-dim);margin-top:2px">
                Разобрано: ${fmt(r.total_breaks)} · Всего: ${fmt(r.total_earned)} ⚙️
              </div>
            </div>
            <div style="font-size:17px;font-weight:800;color:#7dd3fc">${fmt(r.particles)} ⚙️</div>
          </div>
        </div>
      `;
    }).join('');
  } catch (e) {
    document.getElementById('lb-list').innerHTML = `<div class="loader">Ошибка: ${e.message}</div>`;
  }
}

const MAX_ACTIVE_POPUPS = 18;  // cap DOM children to prevent GC thrash
function _pruneEffects(wrap) {
  if (!wrap) return;
  const popups = wrap.querySelectorAll('.dmg-popup');
  if (popups.length > MAX_ACTIVE_POPUPS) {
    for (let i = 0; i < popups.length - MAX_ACTIVE_POPUPS; i++) popups[i].remove();
  }
  const dots = wrap.querySelectorAll('.particle-dot');
  if (dots.length > MAX_ACTIVE_POPUPS) {
    for (let i = 0; i < dots.length - MAX_ACTIVE_POPUPS; i++) dots[i].remove();
  }
}

function onForgeHit(e) {
  const now = Date.now();
  if (now - forgeState.lastHitAt < 55) return;  // anti-double
  forgeState.lastHitAt = now;

  const img = document.getElementById('weapon-img');
  const wrap = document.getElementById('weapon-wrap');
  const s = forgeState.state;
  if (!img || !s?.weapon || !s.effects) return;

  // Combo logic (purely visual)
  forgeState.comboCount += 1;
  const comboEl = document.getElementById('combo-indicator');
  if (comboEl && forgeState.comboCount >= 10) {
    comboEl.textContent = `🔥 COMBO x${forgeState.comboCount}`;
    comboEl.classList.add('active');
  }
  clearTimeout(forgeState.comboResetTimer);
  forgeState.comboResetTimer = setTimeout(() => {
    forgeState.comboCount = 0;
    if (comboEl) comboEl.classList.remove('active');
  }, 1500);

  // ----- OPTIMISTIC LOCAL PREDICTION -----
  const baseDmg = s.effects.damage || 1;
  const critPct = s.effects.crit_chance || 0;
  const critMult = s.effects.crit_multiplier || 3;
  const isCrit = Math.random() * 100 < critPct;
  const damage = isCrit ? Math.round(baseDmg * critMult) : baseDmg;

  // Apply to local HP immediately (no network wait)
  const curHp = Math.max(0, (forgeState.displayedHp ?? s.weapon.hp) - damage);
  forgeState.displayedHp = curHp;
  forgeState.state.weapon.hp = Math.max(0, s.weapon.hp - damage);
  _renderHpDisplay(curHp, s.weapon.max_hp);

  tg?.HapticFeedback?.impactOccurred?.(isCrit ? 'heavy' : 'light');

  // Shake animation
  img.classList.remove('hit', 'crit-hit');
  void img.offsetWidth;
  img.classList.add(isCrit ? 'crit-hit' : 'hit');

  // Damage popup
  _pruneEffects(wrap);
  const popup = document.createElement('div');
  popup.className = 'dmg-popup' + (isCrit ? ' crit' : '');
  popup.textContent = isCrit ? `CRIT -${damage}` : `-${damage}`;
  const wrapRect = wrap.getBoundingClientRect();
  const imgRect = img.getBoundingClientRect();
  popup.style.left = (imgRect.left - wrapRect.left + imgRect.width * 0.5 + (Math.random()*60-30)) + 'px';
  popup.style.top = (imgRect.top - wrapRect.top + imgRect.height * 0.3) + 'px';
  wrap.appendChild(popup);
  setTimeout(() => popup.remove(), 600);

  // Queue the click for the next batch flush
  forgeState.pendingClicks = (forgeState.pendingClicks || 0) + 1;
  _scheduleForgeFlush();
}

// Batch flusher — collects optimistic clicks and sends them to server every ~150ms
function _scheduleForgeFlush() {
  if (forgeState.flushTimer) return;
  forgeState.flushTimer = setTimeout(_flushForgeBatch, 150);
}

async function _flushForgeBatch() {
  forgeState.flushTimer = null;
  const count = forgeState.pendingClicks || 0;
  if (count <= 0) return;
  forgeState.pendingClicks = 0;

  try {
    const r = await api('/api/forge/hit_batch', { method: 'POST', body: JSON.stringify({ count }) });
    if (!r.ok) {
      // Don't undo optimistic UI — next server poll will reconcile.
      return;
    }

    const wrap = document.getElementById('weapon-wrap');
    const img = document.getElementById('weapon-img');

    // Break visuals — if server reports at least one break in this batch
    if (r.breaks > 0 && wrap && img) {
      img.classList.add('breaking');
      tg?.HapticFeedback?.notificationOccurred?.('success');
      _pruneEffects(wrap);
      for (let i = 0; i < 14; i++) {
        const dot = document.createElement('div');
        dot.className = 'particle-dot';
        const wrapRect = wrap.getBoundingClientRect();
        const imgRect = img.getBoundingClientRect();
        dot.style.left = (imgRect.left - wrapRect.left + imgRect.width * 0.5) + 'px';
        dot.style.top = (imgRect.top - wrapRect.top + imgRect.height * 0.5) + 'px';
        const angle = (Math.PI * 2 / 14) * i;
        const distance = 80 + Math.random() * 80;
        dot.style.setProperty('--dx', Math.cos(angle) * distance + 'px');
        dot.style.setProperty('--dy', Math.sin(angle) * distance + 'px');
        wrap.appendChild(dot);
        setTimeout(() => dot.remove(), 900);
      }
      const bigPop = document.createElement('div');
      bigPop.className = 'dmg-popup crit';
      bigPop.style.color = '#7dd3fc';
      bigPop.textContent = `+${fmt(r.particles_earned)} ⚙️`;
      bigPop.style.left = '50%';
      bigPop.style.top = '50%';
      bigPop.style.transform = 'translate(-50%, -50%)';
      wrap.appendChild(bigPop);
      setTimeout(() => bigPop.remove(), 600);
    }

    // Reconcile state from server
    if (r.particles !== undefined) forgeState.state.particles = r.particles;
    if (r.total_breaks !== undefined) forgeState.state.total_breaks = r.total_breaks;
    updateForgeStatsBar();

    if (r.weapon) {
      if (r.weapon_swapped && r.weapon.skin_id) {
        // Full new weapon came back — swap in place after break animation
        setTimeout(() => {
          forgeState.state.weapon = r.weapon;
          forgeState.displayedHp = r.weapon.hp;
          swapWeaponInPlace(r.weapon);
        }, r.breaks > 0 ? 450 : 0);
      } else {
        // Partial response (same weapon): only {hp, max_hp} — sync HP only
        forgeState.state.weapon.hp = r.weapon.hp;
        forgeState.displayedHp = r.weapon.hp;
        _renderHpDisplay(r.weapon.hp, r.weapon.max_hp);
      }
    }
  } catch (e) {
    // Silent — optimistic UI stays, next poll reconciles
  }

  // If more clicks landed during the flush, queue another
  if (forgeState.pendingClicks > 0) _scheduleForgeFlush();
}

function swapWeaponInPlace(w) {
  const img = document.getElementById('weapon-img');
  if (!img) return;
  const nameEl = document.querySelector('.forge-weapon-name');
  const rarityEl = document.querySelector('.forge-weapon-rarity');
  const glowEl = document.querySelector('.forge-weapon-glow');
  const hpFill = document.getElementById('hp-fill');
  const hpText = document.getElementById('hp-text');

  // fade-in new weapon
  img.classList.remove('breaking', 'hit', 'crit-hit');
  img.style.opacity = '0';
  img.style.transform = 'scale(0.6)';
  img.src = w.image_url || '';
  // force paint then animate in
  requestAnimationFrame(() => {
    img.style.transition = 'opacity 0.25s ease, transform 0.25s ease';
    img.style.opacity = '1';
    img.style.transform = '';
  });
  if (nameEl) nameEl.innerHTML = `${escape(w.full_name || '—')}${w.stattrak ? ' <span style="color:#ff6633">ST™</span>' : ''}`;
  if (rarityEl) {
    rarityEl.textContent = w.rarity || '';
    rarityEl.style.color = w.rarity_color || '#8b94a7';
  }
  if (glowEl) {
    glowEl.style.background = `radial-gradient(circle, ${w.rarity_color || '#fff'}88, transparent 60%)`;
  }
  if (hpFill) {
    hpFill.classList.remove('low');
    hpFill.style.width = '100%';
  }
  if (hpText) hpText.textContent = `${fmt(w.hp)} / ${fmt(w.max_hp)}`;
  forgeState.displayedHp = w.hp;
  _resetHpRenderCache();
}

function updateForgeStatsBar() {
  const s = forgeState.state;
  if (!s) return;
  // Top particles counter
  const topEl = document.querySelector('.forge-stat.particles');
  if (topEl) topEl.textContent = `⚙️ ${fmt(s.particles)}`;
  // Effects bar breaks counter
  const bars = document.querySelectorAll('.forge-effects-bar span');
  if (bars.length >= 4) bars[3].textContent = `Разобрано: ${s.total_breaks}`;
}

async function onForgeClaimAfk() {
  try {
    const r = await api('/api/forge/claim_afk', { method: 'POST' });
    if (r.ok && r.claimed > 0) {
      tg?.HapticFeedback?.notificationOccurred?.('success');
      toast(`+${fmt(r.claimed)} ⚙️ из AFK`);
      await renderForge(forgeState.area);
    }
  } catch (e) { toast(e.message); }
}

function _buildBranchCardHtml(b, s) {
  const level = s.levels[b.key] || 0;
  const isLocked = ['silver', 'gold', 'global'].includes(b.key) && level < 0;
  const isMaxed = level >= b.max_level && !isLocked;
  let nextCost = null;
  if (isLocked) {
    nextCost = b.unlock_cost;
  } else if (!isMaxed) {
    const nextTier = b.tiers[level];
    if (nextTier) nextCost = nextTier.cost;
  }
  const canAfford = nextCost !== null && s.particles >= nextCost;
  const progressPct = isMaxed ? 100 : (level / b.max_level) * 100;
  return `
    <div class="branch-head">
      <div class="branch-name">${escape(b.name)}</div>
      <div class="branch-level-badge ${isMaxed ? 'maxed' : ''}">
        ${isLocked ? '🔒' : `${level} / ${b.max_level}`}
      </div>
    </div>
    <div class="branch-desc">${escape(b.description)}</div>
    ${!isMaxed && !isLocked ? `
      <div class="branch-effect">
        Сейчас: <b>${forgeEffectLabel(b.key, level)}</b>
        → ${forgeEffectLabel(b.key, level + 1)}
      </div>` : ''}
    ${isLocked ? `<div class="branch-effect">Разблокировать за ${fmt(b.unlock_cost)} ⚙️</div>` : ''}
    ${isMaxed ? `<div class="branch-effect" style="color:var(--accent-gold)">МАКС УРОВЕНЬ — эффект <b>${forgeEffectLabel(b.key, level)}</b></div>` : ''}
    <div class="branch-progress"><div class="branch-progress-fill" style="width:${progressPct}%"></div></div>
    ${isMaxed ? '' : `
      <button class="btn branch-upgrade-btn ${canAfford ? 'afford' : 'locked'}" data-branch="${b.key}" data-cost="${nextCost}" ${canAfford ? '' : 'disabled'}>
        ${isLocked ? '🔓 Разблокировать' : '⬆ Прокачать'} · ${fmt(nextCost)} ⚙️
      </button>`}
  `;
}

// Refresh only the afford/locked/disabled state of all upgrade buttons without rebuilding cards.
function _refreshUpgradeAffordability(s) {
  document.querySelectorAll('#forge-tree-list .branch-upgrade-btn[data-cost]').forEach(btn => {
    const cost = parseInt(btn.dataset.cost, 10);
    const canAfford = !isNaN(cost) && s.particles >= cost;
    btn.classList.toggle('afford', canAfford);
    btn.classList.toggle('locked', !canAfford);
    btn.disabled = !canAfford;
  });
}

function _attachUpgradeButtonHandler(btn, area) {
  btn.addEventListener('click', async () => {
    try {
      const branch = btn.dataset.branch;
      const r = await api('/api/forge/upgrade', { method: 'POST', body: JSON.stringify({ branch }) });
      if (!r.ok) { toast(r.error || 'Не удалось'); return; }
      tg?.HapticFeedback?.notificationOccurred?.('success');
      toast(r.unlocked ? `🔓 Разблокировано` : `⬆ Прокачка! ${fmt(r.cost)} ⚙️ снято`);

      // Local state delta — skip /api/forge/state roundtrip
      const s = forgeState.state;
      if (r.unlocked) {
        s.levels[branch] = 0;  // just unlocked
      } else if (typeof r.new_level === 'number') {
        s.levels[branch] = r.new_level;
      }
      if (typeof r.new_balance === 'number') s.particles = r.new_balance;

      // Rebuild ONLY the clicked branch card, then refresh affordability everywhere else.
      const branchCfg = (forgeState.branches || []).find(bb => bb.key === branch);
      const card = btn.closest('.branch-card');
      if (card && branchCfg) {
        card.innerHTML = _buildBranchCardHtml(branchCfg, s);
        const newBtn = card.querySelector('[data-branch]');
        if (newBtn) _attachUpgradeButtonHandler(newBtn, area);
      }
      _refreshUpgradeAffordability(s);

      // Balance pill on upgrades screen
      const pillEl = document.getElementById('upg-particles');
      if (pillEl) pillEl.textContent = fmt(s.particles);
    } catch (e) { toast(e.message); }
  });
}

function renderForgeUpgrades(area) {
  const s = forgeState.state;
  const branches = forgeState.branches;

  area.innerHTML = `
    <button class="back-btn" id="upgrades-back">← к кузнице</button>
    <div class="forge-balance-pill">⚙️ <b id="upg-particles">${fmt(s.particles)}</b></div>
    <div class="forge-tree" id="forge-tree-list"></div>
  `;

  const list = document.getElementById('forge-tree-list');
  list.innerHTML = branches.map(b => `<div class="branch-card" data-branch-card="${b.key}">${_buildBranchCardHtml(b, s)}</div>`).join('');

  document.getElementById('upgrades-back').addEventListener('click', () => forgePaint(area));
  list.querySelectorAll('[data-branch]').forEach(btn => _attachUpgradeButtonHandler(btn, area));
}

function forgeEffectLabel(branchKey, level) {
  const branch = (forgeState.branches || []).find(b => b.key === branchKey);
  if (!branch) return '';
  const baseLabels = {
    damage: { base: 1, suffix: ' урон' },
    crit: { base: 0, suffix: '% шанс' },
    luck: { base: 0, suffix: '% particles', prefix: '+' },
    offline_cap: { base: 8, suffix: ' ч' },
    silver: { base: 0.3, suffix: '/сек', decimals: 2 },
    gold: { base: 1.0, suffix: '/сек', decimals: 1 },
    global: { base: 4.0, suffix: '/сек', decimals: 1 },
  };
  const conf = baseLabels[branchKey] || { base: 0, suffix: '' };
  let value;
  if (level <= 0) value = conf.base;
  else {
    const idx = Math.min(level, branch.tiers.length) - 1;
    value = branch.tiers[idx].effect;
  }
  if (conf.decimals !== undefined) value = value.toFixed(conf.decimals);
  return `${conf.prefix || ''}${value}${conf.suffix}`;
}

function renderForgeExchange(area) {
  const s = forgeState.state;
  area.innerHTML = `
    <button class="back-btn" id="ex-back">← к кузнице</button>
    <div class="game-play">
      <h3>💱 Обмен particles → coins</h3>
      <p style="color:var(--text-dim); font-size:13px; margin-bottom:16px">
        Курс: 10 ⚙️ = 1 🪙. У тебя сейчас <b>${fmt(s.particles)}</b> ⚙️.
      </p>
      <label>Сколько particles обменять</label>
      <input type="text" inputmode="numeric" pattern="[0-9]*" id="ex-amount" value="${Math.min(s.particles, 1000)}" autocomplete="off" />
      <div style="font-size:13px; margin-bottom:12px; color:var(--accent-gold)">
        Получишь: <b id="ex-preview">${Math.floor(Math.min(s.particles, 1000) / 10)}</b> 🪙
      </div>
      <button class="btn big-btn daily-btn" id="ex-btn">Обменять</button>
    </div>
  `;

  document.getElementById('ex-back').addEventListener('click', () => forgePaint(area));

  const input = document.getElementById('ex-amount');
  const preview = document.getElementById('ex-preview');
  input.addEventListener('input', () => {
    // Strip non-digits so user can paste/type freely
    const raw = (input.value || '').replace(/[^0-9]/g, '');
    if (raw !== input.value) input.value = raw;
    const v = parseInt(raw || '0');
    preview.textContent = Math.floor(v / 10);
  });
  input.addEventListener('focus', () => input.select());

  document.getElementById('ex-btn').addEventListener('click', async () => {
    const amount = parseInt(input.value || '0');
    if (amount < 10) return toast('Минимум 10 ⚙️');
    try {
      const r = await api('/api/forge/exchange', { method: 'POST', body: JSON.stringify({ particles: amount }) });
      if (r.ok) {
        tg?.HapticFeedback?.notificationOccurred?.('success');
        toast(`+${fmt(r.coins)} 🪙`);
        state.me.balance = r.new_balance;
        document.getElementById('balance-display').textContent = fmt(state.me.balance);
        const s2 = await api('/api/forge/state');
        forgeState.state = s2;
        forgePaint(area);
      } else {
        toast(r.error || 'Ошибка');
      }
    } catch (e) { toast(e.message); }
  });
}

async function playCoinflip(side) {
  const bet = parseInt(document.getElementById('cf-bet').value || '0');
  if (bet <= 0) return toast('Поставь сумму');
  const btns = document.querySelectorAll('[data-side]');
  if (Array.from(btns).some(b => b.disabled)) return;  // request in flight
  btns.forEach(b => b.disabled = true);
  try {
    const r = await api('/api/casino/coinflip', { method: 'POST', body: JSON.stringify({ bet, side }) });
    const resEl = document.getElementById('cf-result');
    const out = document.getElementById('cf-out');
    resEl.textContent = r.result === 'heads' ? '🪙' : '✨';
    out.style.display = 'block';
    out.className = 'game-out ' + (r.win ? 'win' : 'lose');
    out.textContent = r.win ? `+${fmt(r.delta)} 🪙` : `${fmt(r.delta)} 🪙`;
    tg?.HapticFeedback?.notificationOccurred?.(r.win ? 'success' : 'error');
    state.me.balance = r.new_balance;
    document.getElementById('balance-display').textContent = fmt(state.me.balance);
  } catch (e) {
    toast(e.message);
  } finally {
    btns.forEach(b => b.disabled = false);
  }
}

const SLOT_SYMBOLS = ['💀', '🔫', '💣', '💎', '🏆', '7️⃣'];

async function playSlots() {
  const bet = parseInt(document.getElementById('sl-bet').value || '0');
  if (bet <= 0) return toast('Поставь сумму');
  const machine = document.getElementById('sl-machine');
  const reelEls = [0, 1, 2].map(i => document.getElementById(`sl-reel-${i}`));
  const out = document.getElementById('sl-out');
  const btn = document.getElementById('sl-spin');
  if (btn?.disabled) return;

  // Reset UI
  out.style.display = 'none';
  machine.classList.remove('jackpot');
  reelEls.forEach(r => r.classList.remove('stopped', 'matched'));
  if (btn) btn.disabled = true;

  // Start independent spin on each reel
  const spinTimers = reelEls.map(reel => {
    const span = reel.querySelector('span');
    reel.classList.add('spinning');
    return setInterval(() => {
      span.textContent = SLOT_SYMBOLS[Math.floor(Math.random() * SLOT_SYMBOLS.length)];
    }, 70);
  });

  // Fetch server result in parallel with animation
  let result;
  try {
    result = await api('/api/casino/slots', { method: 'POST', body: JSON.stringify({ bet }) });
  } catch (e) {
    spinTimers.forEach(clearInterval);
    reelEls.forEach(r => r.classList.remove('spinning'));
    if (btn) btn.disabled = false;
    toast(e.message);
    return;
  }

  if (!Array.isArray(result.reels) || result.reels.length !== 3) {
    spinTimers.forEach(clearInterval);
    reelEls.forEach(r => r.classList.remove('spinning'));
    if (btn) btn.disabled = false;
    toast('Сервер вернул кривой ответ');
    return;
  }

  // Stop reels one by one: 900ms, 1200ms, 1500ms from spin start
  const stopReel = (i) => {
    clearInterval(spinTimers[i]);
    const span = reelEls[i].querySelector('span');
    span.textContent = result.reels[i];
    reelEls[i].classList.remove('spinning');
    reelEls[i].classList.add('stopped');
    tg?.HapticFeedback?.impactOccurred?.('light');
  };

  await new Promise(r => setTimeout(r, 900));  stopReel(0);
  await new Promise(r => setTimeout(r, 300));  stopReel(1);
  await new Promise(r => setTimeout(r, 300));  stopReel(2);

  // Jackpot check: compare 3 reels
  const isJackpot = result.reels[0] === result.reels[1] && result.reels[1] === result.reels[2];

  if (isJackpot) {
    // Highlight each reel + machine-level glow
    reelEls.forEach(r => r.classList.add('matched'));
    machine.classList.add('jackpot');
    tg?.HapticFeedback?.notificationOccurred?.('success');
  }

  // Outcome banner
  await new Promise(r => setTimeout(r, 250));
  out.style.display = 'block';
  out.className = 'game-out ' + (isJackpot ? 'win' : 'lose');
  out.textContent = isJackpot
    ? `🎉 JACKPOT! +${fmt(result.delta)} 🪙`
    : `${fmt(result.delta)} 🪙`;

  // Balance sync
  if (typeof result.new_balance === 'number') {
    state.me.balance = result.new_balance;
    const bel = document.getElementById('balance-display');
    if (bel) bel.textContent = fmt(state.me.balance);
  }

  // On jackpot, lock button for 2.5s so next spin doesn't immediately overwrite the celebration
  if (isJackpot) {
    setTimeout(() => { if (btn) btn.disabled = false; }, 2500);
  } else {
    if (btn) btn.disabled = false;
  }
}

// ============== MISSIONS ==============
async function loadMissions() {
  const list = document.getElementById('missions-list');
  list.innerHTML = '<div class="loader">Загрузка...</div>';
  try {
    const d = await api('/api/missions');
    list.innerHTML = d.missions.map(m => {
      const pct = Math.min(100, Math.round((m.current / m.target) * 100));
      return `
        <div class="mission-card ${m.completed ? 'complete' : ''}">
          <div class="mission-head">
            <div class="mission-title">${m.completed ? '✅ ' : ''}${escape(m.title)}</div>
            <div class="mission-reward">+${fmt(m.reward)} 🪙</div>
          </div>
          <div class="mission-progress-bar"><div class="mission-progress-fill" style="width:${pct}%"></div></div>
          <div class="mission-progress-text">${m.current} / ${m.target}</div>
        </div>
      `;
    }).join('');
    const done = d.missions.filter(m => m.completed).length;
    if (d.all_complete) {
      if (!d.final_claimed) {
        list.innerHTML += `
          <div class="mission-final">
            <div style="font-size:16px;font-weight:800;margin-bottom:8px">🎉 Все миссии пройдены!</div>
            <div style="color:var(--text-dim);font-size:13px;margin-bottom:12px">Забери финальный бонус</div>
            <button class="btn daily-btn" id="claim-final-btn">Забрать ${fmt(d.final_reward)} 🪙</button>
          </div>`;
        document.getElementById('claim-final-btn').addEventListener('click', async () => {
          try {
            const r = await api('/api/missions/claim_final', { method: 'POST' });
            if (r.ok) {
              toast(`+${fmt(r.reward)} 🪙 финальный бонус!`);
              tg?.HapticFeedback?.notificationOccurred?.('success');
              state.me.balance = r.new_balance;
              document.getElementById('balance-display').textContent = fmt(state.me.balance);
              loadMissions();
            } else {
              toast(r.error || 'Ошибка');
            }
          } catch (e) { toast(e.message); }
        });
      } else {
        list.innerHTML += '<div class="mission-final">✅ Финальный бонус забран. До понедельника.</div>';
      }
    } else {
      list.innerHTML += `
        <div class="mission-final" style="border-style:solid;opacity:0.5">
          <div style="font-weight:700">Пройди все ${d.missions.length} миссий → ${fmt(d.final_reward)} 🪙</div>
          <div style="font-size:12px;color:var(--text-dim);margin-top:4px">${done}/${d.missions.length} готово</div>
        </div>`;
    }
  } catch (e) {
    list.innerHTML = `<div class="loader">Ошибка: ${e.message}</div>`;
  }
}

// ============== ACHIEVEMENTS ==============
async function loadAchievements() {
  const list = document.getElementById('achievements-list');
  list.innerHTML = '<div class="loader">Загрузка...</div>';
  try {
    const d = await api('/api/achievements');
    const active = d.active_title;
    list.innerHTML = d.items.map(a => {
      const canSetTitle = a.earned && a.title;
      const isActive = canSetTitle && active === a.title;
      return `
        <div class="ach-card ${a.earned ? 'earned' : ''}">
          <div class="ach-info">
            <div class="ach-name">${escape(a.name)}</div>
            <div class="ach-desc">${escape(a.description)}</div>
          </div>
          ${a.earned && a.reward ? `<div class="ach-reward">+${fmt(a.reward)} 🪙</div>` : ''}
          ${canSetTitle ? `
            <button class="ach-title-btn" data-title="${escape(a.title)}" style="${isActive ? 'background:var(--accent-gold);color:#0e0f14' : ''}">
              ${isActive ? '✓ Активный' : 'Сделать титулом'}
            </button>
          ` : ''}
        </div>
      `;
    }).join('');
    list.querySelectorAll('.ach-title-btn').forEach(btn => {
      btn.addEventListener('click', async () => {
        const title = btn.dataset.title;
        const isActive = active === title;
        try {
          await api('/api/achievements/title', { method: 'POST', body: JSON.stringify({ title: isActive ? null : title }) });
          toast(isActive ? 'Титул убран' : `Титул: ${title}`);
          loadAchievements();
          loadMe();
        } catch (e) { toast(e.message); }
      });
    });
  } catch (e) {
    list.innerHTML = `<div class="loader">Ошибка: ${e.message}</div>`;
  }
}

// ============== WHEEL OF FORTUNE ==============
async function loadWheel() {
  const view = document.getElementById('wheel-view');
  view.innerHTML = '<div class="loader">Загрузка...</div>';
  try {
    const s = await api('/api/wheel');
    view.innerHTML = `
      <div class="wheel-container">
        <div class="wheel-wrap">
          <div class="wheel-pointer"></div>
          <div class="wheel" id="wheel-disk"></div>
        </div>
        <div class="wheel-info" id="wheel-info">
          ${s.available ? 'Бесплатная крутка готова!' : `Следующая через ${Math.ceil(s.next_in_seconds / 3600)} ч`}
        </div>
        <button class="btn big-btn daily-btn" id="wheel-spin-btn" ${s.available ? '' : 'disabled'} style="margin-top:18px">
          🎡 ${s.available ? 'Крутить' : 'Недоступно'}
        </button>
        <div id="wheel-result"></div>
        <div style="margin-top:14px;font-size:11px;color:var(--text-dim)">
          Всего круток: <b>${s.total_spins || 0}</b>
        </div>
      </div>
    `;
    const btn = document.getElementById('wheel-spin-btn');
    if (btn) {
      btn.addEventListener('click', async () => {
        btn.disabled = true;
        const disk = document.getElementById('wheel-disk');
        disk.style.transform = `rotate(${1800 + Math.random() * 360}deg)`;
        try {
          const r = await api('/api/wheel/spin', { method: 'POST' });
          await new Promise(x => setTimeout(x, 5100));
          const info = document.getElementById('wheel-result');
          if (r.ok) {
            tg?.HapticFeedback?.notificationOccurred?.(r.prize.amount >= 1000 ? 'success' : 'warning');
            const cls = r.prize.amount > 0 ? 'win' : 'lose';
            info.innerHTML = `<div class="wheel-result game-out ${cls}">${r.prize.label}</div>`;
            if (r.new_balance !== undefined) {
              state.me.balance = r.new_balance;
              document.getElementById('balance-display').textContent = fmt(state.me.balance);
            }
          } else if (r.error === 'too_early') {
            info.innerHTML = `<div class="wheel-result">Рано. Ещё ~${Math.ceil(r.next_in_seconds/3600)} ч</div>`;
          }
        } catch (e) { toast(e.message); btn.disabled = false; }
      });
    }
  } catch (e) {
    view.innerHTML = `<div class="loader">Ошибка: ${e.message}</div>`;
  }
}

// ============== CRASH ==============
async function playCrash() {
  const bet = parseInt(document.getElementById('cr-bet').value || '0');
  const target = parseFloat(document.getElementById('cr-target').value || '0');
  if (bet <= 0) return toast('Ставка > 0');
  if (bet > 10000) return toast('Макс ставка 10 000');
  if (target < 1.20) return toast('Минимальный таргет 1.20x');
  const btn = document.getElementById('cr-play');
  if (btn?.disabled) return;
  const origText = btn ? btn.textContent : '';
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Жду результат…'; }
  const out = document.getElementById('cr-out');
  if (out) {
    out.style.display = 'block';
    out.className = 'game-out';
    out.textContent = '⏳ Ракета разгоняется…';
  }
  try {
    const r = await api('/api/casino/crash', { method: 'POST', body: JSON.stringify({ bet, target_mult: target }) });
    const winText = r.win
      ? `🚀 Взлетело до ${r.crash_point}x. Ты снял на ${r.target}x. +${fmt(r.delta)} 🪙`
      : `💥 Крэш на ${r.crash_point}x. Твой таргет ${r.target}x. ${fmt(r.delta)} 🪙`;
    if (out) {
      out.className = 'game-out ' + (r.win ? 'win' : 'lose');
      out.textContent = winText;
    }
    // Toast regardless of current view — so if user navigated away they still see the result
    toast(winText, 4000);
    tg?.HapticFeedback?.notificationOccurred?.(r.win ? 'success' : 'error');
    state.me.balance = r.new_balance;
    document.getElementById('balance-display').textContent = fmt(state.me.balance);
  } catch (e) {
    if (out) { out.className = 'game-out lose'; out.textContent = 'Ошибка: ' + e.message; }
    toast(e.message);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = origText; }
  }
}

function renderLeaderboard() {
  const list = document.getElementById('leaderboard-list');
  if (!state.leaderboard.length) {
    list.innerHTML = '<div class="loader">Пусто.</div>';
    return;
  }
  list.innerHTML = state.leaderboard.map((r, i) => {
    const rank = i + 1;
    const medals = ['🥇', '🥈', '🥉'];
    const rankStr = medals[i] || `#${rank}`;
    const rankClass = i === 0 ? 'top1' : i === 1 ? 'top2' : i === 2 ? 'top3' : '';
    const name = r.username ? `@${r.username}` : (r.first_name || `user${r.tg_id}`);
    return `
      <div class="lb-row">
        <div class="lb-rank ${rankClass}">${rankStr}</div>
        <div class="lb-name">${escape(name)}</div>
        <div class="lb-bal">${fmt(r.balance)} 🪙</div>
      </div>
    `;
  }).join('');
}

// ================= events =================
document.querySelectorAll('.tab').forEach(tab => {
  tab.addEventListener('click', () => {
    const target = tab.dataset.target;
    showView(target);
    if (target === 'cases' && !state.cases.length) loadCases();
    if (target === 'inventory') loadInventory();
    if (target === 'leaderboard') loadLeaderboard();
    if (target === 'home') loadMe();
    if (target === 'missions') loadMissions();
    if (target === 'achievements') loadAchievements();
    if (target === 'wheel') loadWheel();
  });
});

document.querySelectorAll('[data-back]').forEach(btn => {
  btn.addEventListener('click', () => {
    if (document.querySelector('.view[data-view="case-open"]').classList.contains('active')) {
      showView('inventory');
      loadInventory();
    } else {
      showView('cases');
    }
  });
});

document.querySelectorAll('[data-close-modal]').forEach(el => {
  el.addEventListener('click', () => document.getElementById('item-modal').classList.add('hidden'));
});

if (tg) {
  tg.BackButton.onClick(() => {
    const activeView = document.querySelector('.view.active')?.dataset.view;
    if (activeView === 'case-open' || activeView === 'case-preview') {
      showView('cases');
    } else if (activeView !== 'home') {
      showView('home');
      loadMe();
    } else {
      tg.close();
    }
  });
}

document.getElementById('wheel-btn')?.addEventListener('click', () => {
  showView('wheel');
  loadWheel();
});

document.getElementById('daily-btn').addEventListener('click', async () => {
  const btn = document.getElementById('daily-btn');
  if (btn.classList.contains('disabled')) {
    toast('Ещё рано');
    return;
  }
  try {
    const r = await api('/api/daily', { method: 'POST' });
    if (!r.ok) {
      const h = Math.ceil((r.next_in_seconds || 0) / 3600);
      toast(`Ещё ~${h} ч подожди`);
      return;
    }
    tg?.HapticFeedback?.notificationOccurred?.('success');
    toast(`+${fmt(r.amount)} 🪙 (стрик ${r.streak})`);
    await loadMe();
  } catch (e) {
    toast(`Ошибка: ${e.message}`);
  }
});

function escape(s) {
  if (s == null) return '';
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

// ============================================================
// BOSS RAIDS SCREEN
// ============================================================

const _bossState = { busy: false, pendingTaps: 0, flushTimer: null, area: null, st: null, cdInterval: null };

function _fmtCooldown(sec) {
  if (sec >= 3600) return Math.ceil(sec / 3600) + 'ч';
  if (sec >= 60) return Math.ceil(sec / 60) + 'м';
  return Math.max(0, sec) + 'с';
}

async function renderForgeBoss(area) {
  _stopForgePolling();
  _bossState.area = area;
  area.innerHTML = `<button class="back-btn" id="boss-back">← к кузнице</button><div class="boss-wrap" id="boss-root"><div class="loader">Загрузка боссов…</div></div>`;
  document.getElementById('boss-back').addEventListener('click', () => forgePaint(area));
  try {
    const [st, branches] = await Promise.all([
      api('/api/forge/boss/state'),
      api('/api/forge/boss/branches'),
    ]);
    _bossState.st = st;
    _paintBoss(area, st, branches);
  } catch (e) {
    document.getElementById('boss-root').innerHTML = `<div class="loader">Ошибка: ${escape(e.message)}</div>`;
  }
}

function _paintBoss(area, st, branches) {
  const root = document.getElementById('boss-root');
  if (!root) return;
  const hpPct = Math.max(0, (st.hp / st.max_hp) * 100);
  const isEndless = st.selected_tier > 10;

  // Boss tier picker (carousel of unlocked bosses)
  const tiersHtml = (st.tiers || []).map(t => {
    const tierHpPct = Math.max(0, (t.hp / t.max_hp) * 100);
    const onCd = (t.cooldown_left || 0) > 0;
    return `
      <button class="boss-tier-card ${t.selected ? 'active' : ''} ${onCd ? 'cooldown' : ''}" data-tier="${t.tier}">
        <div class="btc-icon">${t.icon}</div>
        <div class="btc-tier">T${t.tier}</div>
        <div class="btc-hp-bar"><div class="btc-hp-fill" style="width:${tierHpPct}%"></div></div>
        <div class="btc-kills">${onCd ? '💤 ' + _fmtCooldown(t.cooldown_left) : t.kills + '× kills'}</div>
      </button>
    `;
  }).join('');

  root.innerHTML = `
    <div class="boss-tier-picker" id="boss-tier-picker">${tiersHtml}</div>

    <div class="boss-fight-card">
      <div class="boss-tier-label">Тир ${st.selected_tier}${isEndless ? ' · ENDLESS' : ''}</div>
      <div class="boss-name">${escape(st.name)}</div>
      <div class="boss-lore">${escape(st.lore)}</div>

      <div class="boss-tap-target ${st.cooldown_seconds_left > 0 ? 'cooldown' : ''}" id="boss-tap-target">
        <div class="boss-icon-big" id="boss-icon-big">${st.icon}</div>
        <div class="boss-tap-hint" id="boss-tap-hint">${st.cooldown_seconds_left > 0 ? '💤 спит' : 'тапай'}</div>
        ${st.cooldown_seconds_left > 0 ? `<div class="boss-cd-overlay" id="boss-cd-overlay">💤<br>${_fmtCooldown(st.cooldown_seconds_left)}</div>` : ''}
      </div>

      <div class="boss-hp-bar">
        <div class="boss-hp-fill" id="boss-hp-fill" style="width:${hpPct}%"></div>
        <div class="boss-hp-text" id="boss-hp-text">${fmt(st.hp)} / ${fmt(st.max_hp)} HP</div>
      </div>

      <div class="boss-timer-row" id="boss-timer-row" ${st.regen_seconds_left == null ? 'style="visibility:hidden"' : ''}>
        ⏱ Регенерация через <b id="boss-timer">${st.regen_seconds_left ?? '—'}</b> сек
      </div>

      <div class="boss-stats">
        <span>⚔️ ≈ <b id="boss-preview-dmg">${fmt(st.preview_dmg)}</b></span>
        <span>💰 <b>${fmt(st.coin_reward)} 🪙</b></span>
        <span>☠ <b id="boss-total-kills">${st.total_kills}</b></span>
        <span>🛡 max <b>${st.max_tier}</b></span>
      </div>
    </div>

    <div class="boss-prestige-section">
      <div class="boss-section-title">🛡 Охотник на боссов <span class="boss-jetons">🎖 ${fmt(st.jetons || 0)}</span></div>
      <div class="boss-branches" id="boss-branches"></div>
    </div>
  `;

  // Wire tier picker
  document.querySelectorAll('.boss-tier-card').forEach(card => {
    card.addEventListener('click', () => _selectBossTier(area, parseInt(card.dataset.tier)));
  });

  // Wire tap-to-attack on the boss icon area
  const tapTarget = document.getElementById('boss-tap-target');
  tapTarget.addEventListener('click', (e) => _bossTap(area, tapTarget, e));

  // Start cooldown countdown ticker — updates the overlay "💤 Xм" every second
  if (_bossState.cdInterval) clearInterval(_bossState.cdInterval);
  if ((st.cooldown_seconds_left || 0) > 0) {
    let cdLeft = st.cooldown_seconds_left;
    _bossState.cdInterval = setInterval(() => {
      if (!document.getElementById('boss-root')) {
        clearInterval(_bossState.cdInterval);
        return;
      }
      cdLeft -= 1;
      if (_bossState.st) _bossState.st.cooldown_seconds_left = Math.max(0, cdLeft);
      const overlay = document.getElementById('boss-cd-overlay');
      if (cdLeft <= 0) {
        clearInterval(_bossState.cdInterval);
        if (overlay) overlay.remove();
        const tap = document.getElementById('boss-tap-target');
        if (tap) tap.classList.remove('cooldown');
        const hint = document.getElementById('boss-tap-hint');
        if (hint) hint.textContent = 'тапай';
      } else if (overlay) {
        overlay.innerHTML = '💤<br>' + _fmtCooldown(cdLeft);
      }
    }, 1000);
  }

  // Start regen countdown ticker (1Hz). Only re-render if user is STILL on boss screen.
  if (_bossState.timerInterval) clearInterval(_bossState.timerInterval);
  if (st.regen_seconds_left != null) {
    let secsLeft = st.regen_seconds_left;
    const timerEl = document.getElementById('boss-timer');
    _bossState.timerInterval = setInterval(() => {
      secsLeft -= 1;
      // If boss screen no longer in DOM (user navigated away) — silently stop, no re-render
      if (!document.getElementById('boss-root')) {
        clearInterval(_bossState.timerInterval);
        return;
      }
      if (secsLeft <= 0) {
        clearInterval(_bossState.timerInterval);
        renderForgeBoss(_bossState.area);
        return;
      }
      if (timerEl) timerEl.textContent = secsLeft;
    }, 1000);
  }

  // Branches
  const branchesEl = document.getElementById('boss-branches');
  branchesEl.innerHTML = branches.map(b => {
    const lvl = st.boss_levels[b.key] || 0;
    const isMaxed = lvl >= b.max_level;
    const cur = (lvl * b.effect_per_level).toFixed(b.effect_per_level < 1 ? 2 : 0);
    const next = isMaxed ? '—' : ((lvl + 1) * b.effect_per_level).toFixed(b.effect_per_level < 1 ? 2 : 0);
    const progressPct = (lvl / b.max_level) * 100;
    const cost = isMaxed ? null : (1 + Math.floor(lvl / 3));  // approx — server is authoritative
    return `
      <div class="boss-branch-card">
        <div class="bb-row">
          <div class="bb-name">${escape(b.name)}</div>
          <div class="bb-level ${isMaxed ? 'maxed' : ''}">${lvl}/${b.max_level}</div>
        </div>
        <div class="bb-desc">${escape(b.desc)}</div>
        <div class="bb-progress"><div class="bb-progress-fill" style="width:${progressPct}%"></div></div>
        ${isMaxed
          ? `<div class="bb-effect maxed">МАКС: ${cur}${b.unit.startsWith('%') ? b.unit : ' '+b.unit}</div>`
          : `<div class="bb-effect">Сейчас: ${cur} → ${next}${b.unit.startsWith('%') ? b.unit : ' '+b.unit}</div>
             <button class="btn bb-buy" data-bb="${b.key}">⬆ Прокачать (${cost} 🎖)</button>`}
      </div>
    `;
  }).join('');
  branchesEl.querySelectorAll('[data-bb]').forEach(btn => {
    btn.addEventListener('click', () => _buyBossUpgrade(area, btn.dataset.bb));
  });
}

async function _selectBossTier(area, tier) {
  if (_bossState.busy) return;
  try {
    const r = await api('/api/forge/boss/select', { method: 'POST', body: JSON.stringify({ tier }) });
    if (!r.ok) { toast(r.error || 'Не удалось выбрать'); return; }
    tg?.HapticFeedback?.impactOccurred?.('light');
    renderForgeBoss(area);
  } catch (e) { toast(e.message); }
}

function _bossTap(area, tapTarget, evt) {
  if (!_bossState.st) return;
  if ((_bossState.st.cooldown_seconds_left || 0) > 0) {
    toast('💤 Босс отдыхает после убийства');
    return;
  }

  // Visual: spawn floating damage number from tap point
  const rect = tapTarget.getBoundingClientRect();
  const x = (evt.clientX || rect.left + rect.width/2) - rect.left;
  const y = (evt.clientY || rect.top + rect.height/2) - rect.top;
  const dmg = _bossState.st.preview_dmg || 100;

  const pop = document.createElement('div');
  pop.className = 'boss-tap-pop';
  pop.textContent = `-${fmt(dmg)}`;
  pop.style.left = x + 'px';
  pop.style.top = y + 'px';
  tapTarget.appendChild(pop);
  setTimeout(() => pop.remove(), 800);

  // Visual: shake the icon
  const icon = document.getElementById('boss-icon-big');
  if (icon) {
    icon.classList.remove('hit');
    void icon.offsetWidth;
    icon.classList.add('hit');
  }

  tg?.HapticFeedback?.impactOccurred?.('light');

  // Optimistically update HP locally
  _bossState.st.hp = Math.max(0, _bossState.st.hp - dmg);
  const pct = (_bossState.st.hp / _bossState.st.max_hp) * 100;
  const hpFill = document.getElementById('boss-hp-fill');
  const hpText = document.getElementById('boss-hp-text');
  if (hpFill) hpFill.style.width = pct + '%';
  if (hpText) hpText.textContent = `${fmt(_bossState.st.hp)} / ${fmt(_bossState.st.max_hp)} HP`;

  // Queue tap for batch flush
  _bossState.pendingTaps += 1;
  if (!_bossState.flushTimer) {
    _bossState.flushTimer = setTimeout(_flushBossBatch, 200);
  }
}

async function _flushBossBatch() {
  _bossState.flushTimer = null;
  const taps = Math.min(50, _bossState.pendingTaps);
  if (taps <= 0) return;
  _bossState.pendingTaps = Math.max(0, _bossState.pendingTaps - taps);

  try {
    const r = await api('/api/forge/boss/attack', { method: 'POST', body: JSON.stringify({ taps }) });
    if (!r.ok) {
      if (r.error === 'cooldown' && r.cooldown_left) {
        toast(`💤 Босс отдыхает: ${_fmtCooldown(r.cooldown_left)}`);
        renderForgeBoss(_bossState.area);
      }
      return;
    }

    // Reconcile state from server truth
    if (_bossState.st) {
      _bossState.st.hp = r.boss_after.hp;
      _bossState.st.max_hp = r.boss_after.max_hp;
      _bossState.st.total_kills = (_bossState.st.total_kills || 0) + (r.kills?.length || 0);
    }

    const hpFill = document.getElementById('boss-hp-fill');
    const hpText = document.getElementById('boss-hp-text');
    const totalKillsEl = document.getElementById('boss-total-kills');
    if (hpFill) hpFill.style.width = (r.boss_after.hp / r.boss_after.max_hp * 100) + '%';
    if (hpText) hpText.textContent = `${fmt(r.boss_after.hp)} / ${fmt(r.boss_after.max_hp)} HP`;
    if (totalKillsEl && _bossState.st) totalKillsEl.textContent = _bossState.st.total_kills;

    if (typeof r.new_balance === 'number') {
      state.me.balance = r.new_balance;
      document.getElementById('balance-display').textContent = fmt(state.me.balance);
    }

    // Reset regen timer (tap = re-engage). Stops silently if user leaves boss screen.
    if (r.regen_total_sec) {
      const timerEl = document.getElementById('boss-timer');
      const timerRow = document.getElementById('boss-timer-row');
      if (timerEl && timerRow) {
        timerRow.style.visibility = '';
        let secsLeft = r.regen_total_sec;
        timerEl.textContent = secsLeft;
        if (_bossState.timerInterval) clearInterval(_bossState.timerInterval);
        _bossState.timerInterval = setInterval(() => {
          secsLeft -= 1;
          if (!document.getElementById('boss-root')) {
            clearInterval(_bossState.timerInterval);
            return;
          }
          if (secsLeft <= 0) {
            clearInterval(_bossState.timerInterval);
            renderForgeBoss(_bossState.area);
            return;
          }
          timerEl.textContent = secsLeft;
        }, 1000);
      }
    }

    if (r.kills && r.kills.length > 0) {
      tg?.HapticFeedback?.notificationOccurred?.('success');
      for (const k of r.kills) {
        toast(`☠ ${k.icon} ${k.name} убит! +${fmt(k.coin_reward)} 🪙`, 3500);
      }
      // Special crits/doubles toast
      if (r.megahits > 0) toast(`💥 ×${r.megahits} МЕГА-УДАР!`, 2500);
      if (r.tier_unlocked) {
        toast(`🔓 Открыт новый тир: ${r.tier_unlocked}`, 4000);
      }
      // If new tier unlocked, re-render — but only if user is still on boss screen
      if (r.tier_unlocked) {
        setTimeout(() => {
          if (document.getElementById('boss-root')) renderForgeBoss(_bossState.area);
        }, 1500);
      }
    }
  } catch (e) {
    // Silent — server is authoritative; user already saw optimistic update
  }

  // If more taps queued during the flush, schedule next
  if (_bossState.pendingTaps > 0 && !_bossState.flushTimer) {
    _bossState.flushTimer = setTimeout(_flushBossBatch, 200);
  }
}

async function _buyBossUpgrade(area, branch) {
  try {
    const r = await api('/api/forge/boss/upgrade', { method: 'POST', body: JSON.stringify({ branch }) });
    if (!r.ok) { toast(r.error || 'Не удалось'); return; }
    tg?.HapticFeedback?.impactOccurred?.('medium');
    toast(`⬆ +1 уровень`);
    renderForgeBoss(area);
  } catch (e) { toast(e.message); }
}

// ============================================================
// GEAR SCREEN (shop + inventory + equipped panel)
// ============================================================
let gearState = { tab: 'shop', shop: null, inventory: null };

async function renderGear(area) {
  _stopForgePolling();
  gearState.tab = 'shop';
  area.innerHTML = `
    <button class="back-btn" id="gear-back">← к кузнице</button>
    <div class="gear-wrap" id="gear-root">
      <div class="gear-tabs">
        <button class="gear-tab active" data-gtab="shop">🛒 Магазин</button>
        <button class="gear-tab" data-gtab="inventory">🎒 Инвентарь</button>
      </div>
      <div id="gear-panel"><div class="loader">Загружаем шмот…</div></div>
    </div>
  `;
  document.getElementById('gear-back').addEventListener('click', () => forgePaint(area));
  area.querySelectorAll('.gear-tab').forEach(t => {
    t.addEventListener('click', () => {
      gearState.tab = t.dataset.gtab;
      area.querySelectorAll('.gear-tab').forEach(tt => tt.classList.toggle('active', tt === t));
      _paintGearPanel(area);
    });
  });
  _paintGearPanel(area);
}

async function _paintGearPanel(area) {
  const panel = document.getElementById('gear-panel');
  if (!panel) return;
  panel.innerHTML = `<div class="loader">Загрузка…</div>`;
  try {
    if (gearState.tab === 'shop') {
      gearState.shop = await api('/api/gear/shop');
      _renderGearShop(area, gearState.shop);
    } else {
      gearState.inventory = await api('/api/gear/inventory');
      _renderGearInventory(area, gearState.inventory);
    }
  } catch (e) {
    panel.innerHTML = `<div class="loader">Ошибка: ${escape(e.message)}</div>`;
  }
}

function _gearItemCardHtml(it, ctx) {
  const isUltra = it.rarity === 'ultralegendary';
  const isUniverse = it.rarity === 'universe';
  const glowSize = isUniverse ? '40px' : (isUltra ? '24px' : '12px');
  const glowAlpha = isUniverse ? 'cc' : (isUltra ? 'aa' : '44');
  const rarityStyle = `border-color:${it.rarity_color}; box-shadow:0 0 ${glowSize} ${it.rarity_color}${glowAlpha}`;
  const cardClasses = ['gear-card'];
  if (isUltra) cardClasses.push('ultra-card');
  if (isUniverse) cardClasses.push('universe-card');
  const affixHtml = it.affixes.map(a => `<div class="gear-affix">${escape(a.label)}</div>`).join('');
  let actionHtml = '';
  if (ctx.mode === 'shop') {
    if (ctx.owned) {
      actionHtml = `<div class="gear-owned-tag">✓ Есть в инвентаре</div>`;
    } else {
      actionHtml = `<button class="btn gear-buy-btn" data-buy="${it.key}">Купить · ${fmt(it.price)} 💰</button>`;
    }
  } else if (ctx.mode === 'inv') {
    const equipBtn = ctx.equipped
      ? `<button class="btn secondary gear-inv-btn" data-unequip="${ctx.inv_id}">Снять</button>`
      : `<button class="btn gear-inv-btn" data-equip="${ctx.inv_id}">Надеть</button>`;
    actionHtml = `${equipBtn}<button class="btn danger gear-inv-btn" data-sell="${ctx.inv_id}">Продать · ${fmt(it.sell_price)} 💰</button>`;
  }
  return `
    <div class="${cardClasses.join(' ')}" style="${rarityStyle}">
      <div class="gear-card-icon" style="color:${it.rarity_color}">${it.icon}</div>
      <div class="gear-card-name">${escape(it.name)}</div>
      <div class="gear-card-rarity" style="color:${it.rarity_color}">${escape(it.rarity_label)}</div>
      <div class="gear-card-affixes">${affixHtml}</div>
      <div class="gear-card-action">${actionHtml}</div>
    </div>
  `;
}

function _renderGearShop(area, data) {
  const panel = document.getElementById('gear-panel');
  if (!panel) return;
  panel.innerHTML = data.slots.map(s => `
    <div class="gear-slot-group">
      <div class="gear-slot-title">${escape(s.label)}</div>
      <div class="gear-card-grid">
        ${s.items.map(it => _gearItemCardHtml(it, { mode: 'shop', owned: it.owned })).join('')}
      </div>
    </div>
  `).join('');
  panel.querySelectorAll('[data-buy]').forEach(b => {
    b.addEventListener('click', async () => {
      if (!confirm(`Купить предмет?`)) return;
      try {
        const r = await api('/api/gear/buy', { method: 'POST', body: JSON.stringify({ item_key: b.dataset.buy }) });
        if (!r.ok) { toast(r.error || 'Не удалось'); return; }
        tg?.HapticFeedback?.notificationOccurred?.('success');
        toast(r.auto_equipped ? '✓ Куплено и надето!' : '✓ Куплено, лежит в инвентаре');
        await _paintGearPanel(area);
      } catch (e) { toast(e.message); }
    });
  });
}

function _renderGearInventory(area, data) {
  const panel = document.getElementById('gear-panel');
  if (!panel) return;
  // Equipped panel + total affixes
  const equippedHtml = data.equipped.map(e => `
    <div class="gear-slot-box ${e.item ? 'filled' : 'empty'}">
      <div class="gear-slot-label">${escape(e.label)}</div>
      ${e.item
        ? `<div class="gear-slot-icon" style="color:${e.item.rarity_color};border-color:${e.item.rarity_color}">${e.item.icon}</div>
           <div class="gear-slot-name" style="color:${e.item.rarity_color}">${escape(e.item.name)}</div>`
        : `<div class="gear-slot-icon empty">—</div><div class="gear-slot-name">пусто</div>`}
    </div>
  `).join('');

  const totalsHtml = data.affix_totals.length
    ? data.affix_totals.map(t => `<span class="gear-total-affix">${escape(t.label)}</span>`).join('')
    : '<span style="color:var(--text-dim)">Ничего не надето</span>';

  const ownedGrid = data.items.length
    ? data.items.map(it => _gearItemCardHtml(it, { mode: 'inv', inv_id: it.inv_id, equipped: it.equipped })).join('')
    : '<div class="loader">Инвентарь пуст. Купи что-нибудь в магазине!</div>';

  panel.innerHTML = `
    <div class="gear-equipped-panel">
      <div class="gear-equipped-grid">${equippedHtml}</div>
      <div class="gear-affix-totals">${totalsHtml}</div>
    </div>
    <div class="gear-slot-title" style="margin-top:18px">🎒 Всё что у тебя есть</div>
    <div class="gear-card-grid">${ownedGrid}</div>
  `;

  panel.querySelectorAll('[data-equip]').forEach(b => b.addEventListener('click', async () => {
    try {
      const r = await api('/api/gear/equip', { method: 'POST', body: JSON.stringify({ inv_id: parseInt(b.dataset.equip) }) });
      if (!r.ok) { toast(r.error || 'Не удалось'); return; }
      tg?.HapticFeedback?.impactOccurred?.('medium');
      await _paintGearPanel(area);
    } catch (e) { toast(e.message); }
  }));
  panel.querySelectorAll('[data-unequip]').forEach(b => b.addEventListener('click', async () => {
    try {
      const r = await api('/api/gear/unequip', { method: 'POST', body: JSON.stringify({ inv_id: parseInt(b.dataset.unequip) }) });
      if (!r.ok) { toast(r.error || 'Не удалось'); return; }
      await _paintGearPanel(area);
    } catch (e) { toast(e.message); }
  }));
  panel.querySelectorAll('[data-sell]').forEach(b => b.addEventListener('click', async () => {
    if (!confirm('Продать? Получишь 50% от цены покупки.')) return;
    try {
      const r = await api('/api/gear/sell', { method: 'POST', body: JSON.stringify({ inv_id: parseInt(b.dataset.sell) }) });
      if (!r.ok) { toast(r.error || 'Не удалось'); return; }
      tg?.HapticFeedback?.notificationOccurred?.('success');
      toast(`+${fmt(r.refund)} 💰`);
      await _paintGearPanel(area);
    } catch (e) { toast(e.message); }
  }));
}

// ============================================================
// 🎰 CS GATES (megaslot)
// ============================================================

const MEGASLOT_ICON = {
  scatter: '💣', milspec: '🟦', classified: '🟪', covert: '🟥',
  m4: '🔫', gloves: '🧤', ak: '🎯', awp: '🏆', knife: '🔪',
};

// Weapon symbols that should render as actual skin images (filled from server config)
const MEGASLOT_WEAPON_SYMS = new Set(['knife', 'awp', 'ak', 'gloves', 'm4']);
// Gem rarity classes (for CSS styling)
const MEGASLOT_GEM_CLASSES = {
  milspec:    'gem-milspec',
  classified: 'gem-classified',
  covert:     'gem-covert',
};
// Populated from /api/casino/megaslot/config
const MEGASLOT_IMAGE = {};

let _megaslotState = { busy: false, bet: 100, configLoaded: false };

async function renderMegaslot(area) {
  area.innerHTML = `
    <div class="megaslot-wrap">
      <div class="megaslot-header">
        <h3>⚡ CS Gates</h3>
        <button class="ms-buy-chip" id="ms-buy" title="Купить бонус = мгновенные FS">
          ⚡ Купить<br><span>бонус</span>
        </button>
      </div>
      <div class="megaslot-fs-bar" id="ms-fs-bar" style="display:none">
        <div class="ms-fs-row">
          <div class="ms-fs-label">FREE SPINS <span id="ms-fs-left">0</span></div>
          <div class="ms-fs-mult">✨ <b id="ms-fs-mult">×0</b></div>
        </div>
        <div class="ms-fs-row subtle">
          <div>База: <b id="ms-fs-base">0 🪙</b></div>
          <div>→ Итого: <b id="ms-fs-projected">0 🪙</b></div>
        </div>
      </div>
      <div class="megaslot-grid" id="ms-grid"></div>
      <div class="megaslot-out" id="ms-out"></div>
      <div class="megaslot-controls">
        <div class="ms-bet-row">
          <label>Ставка</label>
          <input type="text" inputmode="numeric" pattern="[0-9]*" id="ms-bet" value="100" autocomplete="off" />
          <div class="ms-bet-hint">макс. ставка 10 000</div>
        </div>
        <button class="btn big-btn daily-btn" id="ms-spin">🎰 Крутить</button>
      </div>
      <details class="megaslot-rules">
        <summary>📖 Правила и таблица выплат</summary>
        <div class="ms-rules-body">
          <b>Pay-anywhere:</b> 8+ одинаковых символов где угодно на поле = выигрыш.<br>
          <b>Tumble:</b> выигравшие символы исчезают, падают новые — каскад продолжается.<br>
          <b>💣 Scatter:</b> 4/5/6 бомб = ×3/×10/×100 ставки + 15 бесплатных круток.<br>
          <b>Orb:</b> случайный множитель ×2 до ×500 падает на поле. Во FS множители накапливаются!<br>
          <b>Макс. выигрыш:</b> ×5000 ставки.<br><br>
          <b>Выплаты (множитель ставки):</b>
          <table class="ms-paytable">
            <thead><tr><th>Символ</th><th>8+</th><th>10+</th><th>12+</th></tr></thead>
            <tbody>
              <tr><td><span class="pt-icon">${MEGASLOT_IMAGE.knife ? `<img src="${MEGASLOT_IMAGE.knife}" />` : '🔪'}</span> Нож</td><td>×2.5</td><td>×6</td><td>×12</td></tr>
              <tr><td><span class="pt-icon">${MEGASLOT_IMAGE.awp ? `<img src="${MEGASLOT_IMAGE.awp}" />` : '🏆'}</span> AWP</td><td>×1</td><td>×3</td><td>×8</td></tr>
              <tr><td><span class="pt-icon">${MEGASLOT_IMAGE.ak ? `<img src="${MEGASLOT_IMAGE.ak}" />` : '🎯'}</span> AK-47</td><td>×0.5</td><td>×1.5</td><td>×5</td></tr>
              <tr><td><span class="pt-icon">${MEGASLOT_IMAGE.gloves ? `<img src="${MEGASLOT_IMAGE.gloves}" />` : '🧤'}</span> Gloves</td><td>×0.35</td><td>×0.8</td><td>×3.5</td></tr>
              <tr><td><span class="pt-icon">${MEGASLOT_IMAGE.m4 ? `<img src="${MEGASLOT_IMAGE.m4}" />` : '🔫'}</span> M4A4</td><td>×0.25</td><td>×0.5</td><td>×2.5</td></tr>
              <tr><td><span class="ms-cell-gem gem-covert pt-gem">◆</span> Covert</td><td>×0.15</td><td>×0.4</td><td>×2</td></tr>
              <tr><td><span class="ms-cell-gem gem-classified pt-gem">◆</span> Classified</td><td>×0.1</td><td>×0.25</td><td>×1</td></tr>
              <tr><td><span class="ms-cell-gem gem-milspec pt-gem">◆</span> Mil-spec</td><td>×0.05</td><td>×0.12</td><td>×0.5</td></tr>
            </tbody>
          </table>
        </div>
      </details>
    </div>
  `;
  // Load weapon images from server (once per session)
  if (!_megaslotState.configLoaded) {
    try {
      const cfg = await api('/api/casino/megaslot/config');
      (cfg.symbols || []).forEach(s => {
        if (s.image_url) MEGASLOT_IMAGE[s.key] = s.image_url;
      });
      _megaslotState.configLoaded = true;
    } catch {}
  }

  _renderMegaslotGrid(_randomGrid());
  document.getElementById('ms-bet').addEventListener('input', (e) => {
    let v = Math.max(10, parseInt(e.target.value) || 100);
    if (v > 10000) {
      v = 10000;
      e.target.value = v;
      toast('Макс. ставка 10 000');
    }
    _megaslotState.bet = v;
  });
  document.getElementById('ms-spin').addEventListener('click', () => playMegaslot(false, null));
  document.getElementById('ms-buy').addEventListener('click', _openBonusBuyModal);
}

function _openBonusBuyModal() {
  if (_megaslotState.busy) return;
  const bet = _megaslotState.bet;
  const regCost = bet * 70;
  const premCost = bet * 220;
  const el = document.createElement('div');
  el.className = 'ms-bonus-modal';
  el.innerHTML = `
    <div class="ms-bonus-backdrop"></div>
    <div class="ms-bonus-card">
      <div class="ms-bonus-title">⚡ Выбери бонус</div>
      <div class="ms-bonus-options">
        <button class="ms-bonus-opt" data-kind="regular">
          <div class="mb-opt-name">🎁 Обычный</div>
          <div class="mb-opt-spec">15 круток · старт ×0 (накапливается)</div>
          <div class="mb-opt-cost">${fmt(regCost)} 🪙</div>
        </button>
        <button class="ms-bonus-opt premium" data-kind="premium">
          <div class="mb-opt-name">💎 Премиум</div>
          <div class="mb-opt-spec">25 круток · старт <b>×10</b> (+ накапливается)</div>
          <div class="mb-opt-cost">${fmt(premCost)} 🪙</div>
        </button>
      </div>
      <button class="ms-bonus-cancel">Отмена</button>
    </div>
  `;
  document.body.appendChild(el);
  const close = () => el.remove();
  el.querySelector('.ms-bonus-backdrop').addEventListener('click', close);
  el.querySelector('.ms-bonus-cancel').addEventListener('click', close);
  el.querySelectorAll('.ms-bonus-opt').forEach(btn => {
    btn.addEventListener('click', () => {
      const kind = btn.dataset.kind;
      close();
      playMegaslot(true, kind);
    });
  });
}

function _randomGrid() {
  // Weight low-tier gems higher for initial preview look
  const pool = [
    'milspec','milspec','milspec','classified','classified',
    'covert','covert','m4','gloves','ak','awp','knife',
  ];
  const grid = [];
  for (let c = 0; c < 6; c++) {
    const col = [];
    for (let r = 0; r < 5; r++) col.push(pool[Math.floor(Math.random() * pool.length)]);
    grid.push(col);
  }
  return grid;
}

function _renderMegaslotSymbolHtml(sym) {
  // Scatter → big emoji
  if (sym === 'scatter') {
    return `<span class="ms-cell-sym scatter">💣</span>`;
  }
  // Weapon symbol → actual skin image
  if (MEGASLOT_WEAPON_SYMS.has(sym) && MEGASLOT_IMAGE[sym]) {
    return `<img class="ms-cell-img" src="${MEGASLOT_IMAGE[sym]}" alt="${sym}" loading="lazy" />`;
  }
  // Gem tier → styled colored diamond
  const gemClass = MEGASLOT_GEM_CLASSES[sym];
  if (gemClass) {
    return `<span class="ms-cell-gem ${gemClass}">◆</span>`;
  }
  // Fallback (shouldn't happen) → emoji
  return `<span class="ms-cell-sym">${MEGASLOT_ICON[sym] || '?'}</span>`;
}

function _renderMegaslotGrid(grid, options = {}) {
  const gridEl = document.getElementById('ms-grid');
  if (!gridEl) return;
  const orbMap = new Map();
  (options.orbs || []).forEach(o => orbMap.set(`${o.col},${o.row}`, o.value));
  const winPositions = options.winningSymbols
    ? (() => {
        const set = new Set();
        for (let c = 0; c < 6; c++) {
          for (let r = 0; r < 5; r++) {
            if (options.winningSymbols.has(grid[c][r])) set.add(`${c},${r}`);
          }
        }
        return set;
      })()
    : null;

  let html = '';
  for (let r = 0; r < 5; r++) {
    for (let c = 0; c < 6; c++) {
      const sym = grid[c][r];
      const key = `${c},${r}`;
      const orbVal = orbMap.get(key);
      const classes = ['ms-cell', `sym-${sym}`];
      if (winPositions?.has(key)) classes.push('winning');
      if (orbVal) classes.push('has-orb');
      html += `<div class="${classes.join(' ')}" data-cr="${key}">
        ${_renderMegaslotSymbolHtml(sym)}
        ${orbVal ? `<span class="ms-orb">×${orbVal}</span>` : ''}
      </div>`;
    }
  }
  gridEl.innerHTML = html;
}

function _sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

// Proper casino-style spin: each column has a vertical strip of symbols that
// scrolls down with CSS transform. Columns stop sequentially with smooth easing.
async function _spinAnimation(finalGrid, baseDuration = 900, colDelay = 160) {
  const gridEl = document.getElementById('ms-grid');
  if (!gridEl) return;

  const SYMS = ['milspec', 'classified', 'covert', 'm4', 'gloves', 'ak', 'awp', 'knife'];
  const STRIP_FILLER = baseDuration < 500 ? 14 : 22;
  const ROWS = 5;

  // Lock current grid box dimensions so switching display modes doesn't cause a resize jump
  const rect = gridEl.getBoundingClientRect();
  gridEl.style.height = rect.height + 'px';
  // Compute cell height from locked dimensions so reel strip aligns perfectly with 5 rows
  const colHeight = rect.height - 20;  // minus 10px padding top+bottom
  const cellH = colHeight / ROWS;
  gridEl.style.setProperty('--ms-cell-h', cellH + 'px');

  gridEl.classList.add('reel-mode');
  let html = '';
  for (let c = 0; c < 6; c++) {
    const fillers = Array.from({length: STRIP_FILLER}, () => SYMS[Math.floor(Math.random() * SYMS.length)]);
    const stripArr = [...fillers, ...finalGrid[c]];  // final 5 at the bottom
    const cellsHtml = stripArr.map(s =>
      `<div class="ms-strip-cell">${_renderMegaslotSymbolHtml(s)}</div>`
    ).join('');
    html += `<div class="ms-col" data-col="${c}">
      <div class="ms-strip" id="ms-strip-${c}" style="transform:translateY(0)">${cellsHtml}</div>
    </div>`;
  }
  gridEl.innerHTML = html;

  // Force layout so initial transform sticks
  gridEl.getBoundingClientRect();

  // Animate each column: translate strip so final 5 land in view
  const targetY = STRIP_FILLER * cellH;
  const stops = [];
  for (let c = 0; c < 6; c++) {
    const strip = document.getElementById(`ms-strip-${c}`);
    const duration = baseDuration + c * colDelay;  // each column later
    strip.style.transition = `transform ${duration}ms cubic-bezier(0.15, 0.45, 0.25, 1)`;
    // Trigger: two animation frames so transition engages
    await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
    strip.style.transform = `translateY(-${targetY}px)`;
    stops.push({ c, endTime: Date.now() + duration });
  }

  // Wait for columns to land, firing haptic and stop-bounce on each
  for (const s of stops) {
    const wait = s.endTime - Date.now();
    if (wait > 0) await _sleep(wait);
    const colEl = gridEl.querySelector(`.ms-col[data-col="${s.c}"]`);
    if (colEl) {
      colEl.classList.add('just-stopped');
      setTimeout(() => colEl.classList.remove('just-stopped'), 300);
    }
    tg?.HapticFeedback?.impactOccurred?.('light');
  }

  await _sleep(200);

  // Switch back to regular grid mode for tumble animations
  gridEl.classList.remove('reel-mode');
  gridEl.style.height = '';  // release locked height
  _renderMegaslotGrid(finalGrid);
}

async function _animateSpin(spinData, spinMs = 900, colDelay = 160) {
  const tumbles = spinData.tumbles || [];
  const finalGrid = tumbles.length > 0 ? tumbles[0].grid : spinData.final_grid;
  if (!finalGrid) return;

  await _spinAnimation(finalGrid, spinMs, colDelay);

  // If no tumbles at all (pure loss), we're done — just display the stopped grid
  if (tumbles.length === 0) {
    _renderMegaslotGrid(finalGrid);
    return;
  }

  for (let i = 0; i < tumbles.length; i++) {
    const t = tumbles[i];
    const winSyms = new Set((t.wins || []).map(w => w.symbol));
    _renderMegaslotGrid(t.grid, { orbs: t.orbs, winningSymbols: winSyms });

    if (t.wins && t.wins.length > 0) {
      await _sleep(500);
      document.querySelectorAll('.ms-cell.winning').forEach(el => el.classList.add('exploding'));
      await _sleep(280);
      if (t.post_grid) {
        _renderMegaslotGrid(t.post_grid);
        const gridEl = document.getElementById('ms-grid');
        if (gridEl) {
          gridEl.querySelectorAll('.ms-cell').forEach(el => el.classList.add('tumble-in'));
        }
        await _sleep(300);
      }
    } else if (t.orbs && t.orbs.length > 0) {
      await _sleep(600);
    }
  }
}

async function playMegaslot(bonusBuy, bonusType) {
  if (_megaslotState.busy) return;
  const bet = _megaslotState.bet;
  if (bet <= 0) return toast('Поставь сумму');
  const costMult = bonusBuy ? (bonusType === 'premium' ? 220 : 70) : 1;
  const cost = bet * costMult;
  // (modal already confirmed for bonus buy; no confirm() needed here)

  _megaslotState.busy = true;
  const spinBtn = document.getElementById('ms-spin');
  const buyBtn = document.getElementById('ms-buy');
  const out = document.getElementById('ms-out');
  if (spinBtn) spinBtn.disabled = true;
  if (buyBtn) buyBtn.disabled = true;
  out.textContent = '';
  out.className = 'megaslot-out';

  try {
    const r = await api('/api/casino/megaslot/spin', {
      method: 'POST', body: JSON.stringify({
        bet, bonus_buy: bonusBuy, bonus_type: bonusType || 'regular',
      }),
    });
    if (!r.ok) {
      toast(r.error || 'Ошибка');
      return;
    }

    // Apply balance IMMEDIATELY — the server has already committed the spin
    // (cost deducted + winnings credited atomically). If we defer, user can
    // navigate away mid-animation and exploit by seeing 'no deduction'.
    if (typeof r.new_balance === 'number') {
      state.me.balance = r.new_balance;
      const bel = document.getElementById('balance-display');
      if (bel) bel.textContent = fmt(state.me.balance);
    }

    // Base spin animation (skipped on bonus buy) — plays even on no-win spins
    if (r.base_spin) {
      await _animateSpin(r.base_spin, 900);

      // Show running win during tumble cascades
      const tumbles = r.base_spin.tumbles || [];
      let running = 0;
      for (const t of tumbles) {
        if (t.win_amount && t.win_amount > 0) {
          running += t.win_amount;
          out.textContent = `+${fmt(running)} 🪙`;
          out.className = 'megaslot-out win';
          tg?.HapticFeedback?.impactOccurred?.('light');
        }
      }
      // Include orbs multiplier in final base spin total
      if (r.base_spin.final_win > 0) {
        out.textContent = `+${fmt(r.base_spin.final_win)} 🪙`;
        out.className = 'megaslot-out win';
        await _sleep(600);
      }
    }

    // Free spins sequence
    if (r.fs) {
      const fsBar = document.getElementById('ms-fs-bar');
      fsBar.style.display = 'block';
      document.getElementById('ms-fs-left').textContent = r.fs.spins.length;
      const startMult = r.fs.start_mult || 0;
      document.getElementById('ms-fs-mult').textContent = '×' + startMult;
      document.getElementById('ms-fs-base').textContent = '0 🪙';
      document.getElementById('ms-fs-projected').textContent = '0 🪙';
      tg?.HapticFeedback?.notificationOccurred?.('success');
      const variantLabel = r.fs.variant === 'premium' ? '💎 ПРЕМИУМ ' : '';
      out.textContent = `🎰 ${variantLabel}FREE SPINS — ${r.fs.spins.length} круток!`;
      out.className = 'megaslot-out win';
      await _sleep(1200);

      let spinsLeft = r.fs.spins.length;
      let accumBase = 0;
      for (let i = 0; i < r.fs.spins.length; i++) {
        const s = r.fs.spins[i];
        spinsLeft--;
        document.getElementById('ms-fs-left').textContent = spinsLeft;
        // Faster spin for FS: shorter base + tight column spacing
        await _animateSpin(s, 350, 70);
        // Update after this FS spin: accumulated base + current multiplier → projected total
        accumBase += (s.final_win || 0);
        const mult = s.persistent_mult || 0;
        const effectiveMult = mult > 0 ? mult : 1;
        const projected = accumBase * effectiveMult;
        document.getElementById('ms-fs-mult').textContent = '×' + mult;
        document.getElementById('ms-fs-base').textContent = fmt(accumBase) + ' 🪙';
        document.getElementById('ms-fs-projected').textContent = fmt(projected) + ' 🪙';
      }

      await _sleep(500);
      out.textContent = `🎉 FS итог: ${fmt(r.fs.total_base)} × ${r.fs.applied_mult} = +${fmt(r.fs.final_win)} 🪙`;
      out.className = 'megaslot-out win big';
      tg?.HapticFeedback?.notificationOccurred?.('success');

      fsBar.style.display = 'none';
    }

    // Final summary
    await _sleep(800);
    const capped = r.capped ? ' (MAX WIN!)' : '';
    if (r.bonus_buy) {
      const tw = r.total_win || 0;
      out.textContent = tw > 0
        ? `🎁 Выигрыш с бонуса: +${fmt(tw)} 🪙${capped}`
        : `🎁 Бонус ничего не дал :(`;
      out.className = 'megaslot-out ' + (tw > 0 ? 'win big' : 'lose');
    } else {
      const delta = r.delta;
      out.textContent = delta >= 0
        ? `✅ Итого: +${fmt(delta)} 🪙${capped}`
        : `❌ ${fmt(delta)} 🪙`;
      out.className = 'megaslot-out ' + (delta >= 0 ? 'win' : 'lose');
    }
  } catch (e) {
    toast(e.message);
  } finally {
    _megaslotState.busy = false;
    if (spinBtn) spinBtn.disabled = false;
    if (buyBtn) buyBtn.disabled = false;
  }
}

// ================= init =================
(async () => {
  if (!INIT_DATA && !tg) {
    document.getElementById('main').innerHTML = `
      <div style="padding:40px; text-align:center; color:var(--text-dim)">
        Это приложение работает только внутри Telegram.<br>
        Открой /casino в боте.
      </div>
    `;
    return;
  }
  await loadMe();
  await loadCases();
})();
