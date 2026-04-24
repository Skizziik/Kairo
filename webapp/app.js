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
  const maxRetries = 2;
  let lastErr = null;
  for (let attempt = 0; attempt <= maxRetries; attempt++) {
    try {
      const ctrl = new AbortController();
      const timeoutId = setTimeout(() => ctrl.abort(), 10000);
      const resp = await fetch(url, { ...opts, signal: ctrl.signal });
      clearTimeout(timeoutId);
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: resp.statusText }));
        throw new Error(err.detail || `HTTP ${resp.status}`);
      }
      return resp.json();
    } catch (e) {
      lastErr = e;
      // Retry only on network-level errors, not on server 4xx/5xx
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
      <div class="case-preview-items">
        ${data.items.slice(0, 60).map(it => {
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
      </div>
    `;
    document.getElementById('case-preview-open-btn').addEventListener('click', () => openCase(caseId));
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
    state.inventory.items.forEach(it => invSelection.ids.add(it.id));
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
        <input type="number" id="cf-bet" min="1" value="100" />
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
        <input type="number" id="sl-bet" min="1" value="100" />
        <div class="slots-display" id="sl-display">❓ ❓ ❓</div>
        <button class="btn big-btn daily-btn" id="sl-spin">Крутить</button>
        <div class="game-out" id="sl-out" style="display:none"></div>
      </div>
    `;
    document.getElementById('sl-spin').addEventListener('click', playSlots);
  } else if (game === 'crash') {
    area.innerHTML = `
      <div class="game-play">
        <h3>💥 Crash</h3>
        <label>Ставка</label>
        <input type="number" id="cr-bet" min="1" value="100" />
        <label>Таргет множитель (1.01 – 50.00)</label>
        <input type="number" id="cr-target" min="1.01" max="50" step="0.1" value="2" />
        <button class="btn big-btn daily-btn" id="cr-play">Играть</button>
        <div class="game-out" id="cr-out" style="display:none"></div>
      </div>
    `;
    document.getElementById('cr-play').addEventListener('click', playCrash);
  } else if (game === 'upgrade') {
    area.innerHTML = `
      <div class="game-play">
        <h3>⚡ Upgrade (скоро)</h3>
        <p style="color:var(--text-dim); font-size:13px">
          Выбираешь свой скин, скидываешь доп коины, выбираешь цель —
          крутишь шанс прокачать. UI докрутим в следующем обновлении.
        </p>
      </div>
    `;
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
  document.getElementById('forge-skip-btn').addEventListener('click', onForgeSkip);
  document.getElementById('forge-lb-btn')?.addEventListener('click', () => renderForgeLeaderboard(area));
  _startForgePolling();
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
      return `
        <div class="branch-card" style="padding:10px 14px">
          <div style="display:flex;justify-content:space-between;align-items:center">
            <div>
              <div style="font-weight:800;font-size:15px">${rank} ${escape(name)}</div>
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
      const oldId = forgeState.state.weapon?.skin_id;
      const swap = r.weapon.skin_id !== oldId;
      if (swap) {
        setTimeout(() => {
          forgeState.state.weapon = r.weapon;
          forgeState.displayedHp = r.weapon.hp;
          swapWeaponInPlace(r.weapon);
        }, r.breaks > 0 ? 450 : 0);
      } else {
        // Same weapon — sync HP from server truth
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
      <input type="number" id="ex-amount" min="10" max="${s.particles}" step="10" value="${Math.min(s.particles, 1000)}" />
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
    const v = parseInt(input.value || '0');
    preview.textContent = Math.floor(v / 10);
  });

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

async function playSlots() {
  const bet = parseInt(document.getElementById('sl-bet').value || '0');
  if (bet <= 0) return toast('Поставь сумму');
  const display = document.getElementById('sl-display');
  const out = document.getElementById('sl-out');
  out.style.display = 'none';
  const btn = document.getElementById('sl-spin');
  if (btn) btn.disabled = true;

  const symbols = ['💀', '🔫', '💣', '💎', '🏆', '7️⃣'];
  const reels = ['', '', ''];
  const randSym = () => symbols[Math.floor(Math.random() * symbols.length)];
  const draw = () => { display.textContent = reels.map(s => s || randSym()).join(' '); };

  // Start spin
  let spinInterval = setInterval(draw, 75);

  // Fetch server result in parallel with visual spin
  let result;
  try {
    result = await api('/api/casino/slots', { method: 'POST', body: JSON.stringify({ bet }) });
  } catch (e) {
    clearInterval(spinInterval);
    if (btn) btn.disabled = false;
    toast(e.message);
    return;
  }

  // Spin for ~1.5s total, then stop reels one by one
  await new Promise(r => setTimeout(r, 800));
  reels[0] = result.reels[0]; draw();
  tg?.HapticFeedback?.impactOccurred?.('light');
  await new Promise(r => setTimeout(r, 380));
  reels[1] = result.reels[1]; draw();
  tg?.HapticFeedback?.impactOccurred?.('light');
  await new Promise(r => setTimeout(r, 380));
  clearInterval(spinInterval);
  reels[2] = result.reels[2]; draw();
  tg?.HapticFeedback?.impactOccurred?.('medium');

  // Show outcome
  await new Promise(r => setTimeout(r, 250));
  out.style.display = 'block';
  out.className = 'game-out ' + (result.delta > 0 ? 'win' : 'lose');
  out.textContent = result.outcome === 'jackpot'
    ? `🎉 JACKPOT +${fmt(result.delta)} 🪙`
    : result.outcome === 'pair'
      ? `Пара +${fmt(result.delta)} 🪙`
      : `${fmt(result.delta)} 🪙`;
  tg?.HapticFeedback?.notificationOccurred?.(
    result.outcome === 'jackpot' ? 'success' : (result.delta > 0 ? 'warning' : 'error')
  );
  state.me.balance = result.new_balance;
  document.getElementById('balance-display').textContent = fmt(state.me.balance);
  if (btn) btn.disabled = false;
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
  if (bet <= 0 || target < 1.01) return toast('Ставка > 0, таргет >= 1.01');
  const btn = document.getElementById('cr-play');
  if (btn?.disabled) return;
  if (btn) btn.disabled = true;
  try {
    const r = await api('/api/casino/crash', { method: 'POST', body: JSON.stringify({ bet, target_mult: target }) });
    const out = document.getElementById('cr-out');
    out.style.display = 'block';
    out.className = 'game-out ' + (r.win ? 'win' : 'lose');
    out.textContent = r.win
      ? `🚀 Взлетело до ${r.crash_point}x. Ты снял на ${r.target}x. +${fmt(r.delta)} 🪙`
      : `💥 Крэш на ${r.crash_point}x. Твой таргет ${r.target}x. ${fmt(r.delta)} 🪙`;
    tg?.HapticFeedback?.notificationOccurred?.(r.win ? 'success' : 'error');
    state.me.balance = r.new_balance;
    document.getElementById('balance-display').textContent = fmt(state.me.balance);
  } catch (e) {
    toast(e.message);
  } finally {
    if (btn) btn.disabled = false;
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
