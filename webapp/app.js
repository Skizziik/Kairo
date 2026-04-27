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
      // Render free tier cold-start can take 20-30s; give GETs more breathing room
      const timeoutId = setTimeout(() => ctrl.abort(), isMutation ? 45000 : 30000);
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
      // 'Load failed' is what iOS Safari/TG-WebView gives on network error
      // (instead of Chrome's 'Failed to fetch'). Treat the same.
      const isNetwork = msg === 'Failed to fetch'
        || msg === 'Load failed'
        || msg.includes('NetworkError')
        || msg.includes('Network request failed')
        || e?.name === 'AbortError';
      if (!isNetwork || attempt === maxRetries) break;
      await new Promise(r => setTimeout(r, 300 * (attempt + 1)));
    }
  }
  // Normalize message so users see something readable
  const msg = String(lastErr?.message || lastErr);
  if (msg === 'Failed to fetch' || msg === 'Load failed'
      || msg.includes('NetworkError') || msg.includes('aborted')
      || msg.includes('Network request failed')) {
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
  // Restore single-open reel UI in case multi-open hid it earlier
  document.querySelectorAll('.case-open-marker, .case-open-reel').forEach(el => el.style.display = '');

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
  document.querySelectorAll('.case-open-marker, .case-open-reel').forEach(el => el.style.display = 'none');
  titleEl.textContent = `${caseData.name} × ${count}`;
  resultEl.classList.add('shown');
  actionsEl.style.display = 'none';

  // Same params as single-open: 60 items, winner at index 53, 6s cubic-bezier
  const pool = caseData.items || [];
  const placeholders = pool.length > 0 ? pool : [{ image_url: '', name: '?', rarity: 'mil-spec' }];
  const REEL_COUNT = 60;
  const WINNER_INDEX = 53;
  const CELL_W = 90;

  // Show a "preparing" loader while we fetch the actual winners. We BUILD reels
  // only AFTER the server returns so the cell under the marker is always the real
  // winner — no mid-spin swap, no visible flicker at the end.
  resultEl.innerHTML = `<div class="multi-open-loader">Готовим барабаны…</div>`;

  tg?.HapticFeedback?.impactOccurred?.('heavy');
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
  if (resp.opened < (resp.expected || count)) {
    toast(`⚠ Открылось только ${resp.opened} из ${resp.expected || count}`, 4500);
  }
  const last = resp.results[resp.results.length - 1];
  if (last && typeof last.new_balance === 'number') {
    state.me.balance = last.new_balance;
    document.getElementById('balance-display').textContent = fmt(state.me.balance);
  }

  const wearShort = (w) => ({
    'Factory New':'FN','Minimal Wear':'MW','Field-Tested':'FT','Well-Worn':'WW','Battle-Scarred':'BS'
  }[w] || w || '');

  // Build all reels with REAL winners pre-placed at index 53. The cell under
  // the marker at the end of the animation is guaranteed to be the actual win.
  let reelsHtml = '';
  for (let i = 0; i < resp.results.length; i++) {
    const winSkin   = (resp.results[i] && resp.results[i].skin) || {};
    const winRarity = winSkin.rarity || 'mil-spec';
    const winImg    = winSkin.image_url || '';
    const cells = [];
    for (let j = 0; j < REEL_COUNT; j++) {
      if (j === WINNER_INDEX) {
        cells.push(`<div class="mr-cell winner rarity-${winRarity}"><img src="${winImg}" alt="" /></div>`);
      } else {
        const it = placeholders[Math.floor(Math.random() * placeholders.length)];
        cells.push(`<div class="mr-cell rarity-${it.rarity}"><img src="${it.image_url || ''}" alt="" /></div>`);
      }
    }
    reelsHtml += `<div class="multi-reel" data-reel="${i}"><div class="multi-reel-track" id="mr-track-${i}">${cells.join('')}</div><div class="multi-reel-marker"></div></div>`;
  }
  resultEl.innerHTML = `<div class="multi-reels-stack">${reelsHtml}</div>`;

  // Trigger spin — same params as single-open: 6s cubic-bezier with jitter.
  // Reels are pre-built with real winners so the final position is guaranteed correct.
  await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
  const reelEls = document.querySelectorAll('.multi-reel');
  reelEls.forEach((el) => {
    const track = el.querySelector('.multi-reel-track');
    if (!track) return;
    track.style.transition = 'transform 6s cubic-bezier(0.15, 0.45, 0.1, 1)';
    const reelW = el.getBoundingClientRect().width;
    const offset = (WINNER_INDEX * CELL_W) - (reelW / 2) + (CELL_W / 2);
    const jitter = (Math.random() - 0.5) * (CELL_W * 0.4);
    track.style.transform = `translateX(-${offset + jitter}px)`;
  });

  // Wait for animation completion
  await new Promise(r => setTimeout(r, 6100));

  // Show summary below
  const totalGot = resp.results.reduce((sum, r) => sum + (r.price || 0), 0);
  const netDelta = totalGot - totalCost;
  const cards = resp.results.map((r) => {
    const skin = r.skin || {};
    const rarity = skin.rarity || 'mil-spec';
    const name = skin.full_name || 'Unknown';
    const img = skin.image_url || '';
    const ws = wearShort(r.wear);
    const price = r.price || 0;
    const st = !!r.stat_trak;
    return `
      <div class="multi-open-card rarity-${rarity}">
        ${st ? '<div class="stattrak-badge">ST™</div>' : ''}
        <img class="result-img" src="${img}" alt="" />
        <div class="result-name">${escape(name)}</div>
        <div class="result-meta">${ws} · ${fmt(price)} 🪙</div>
      </div>
    `;
  }).join('');
  resultEl.innerHTML = `
    <div class="multi-open-summary ${netDelta >= 0 ? 'win' : 'lose'}">
      Получил скинов на <b>${fmt(totalGot)} 🪙</b> (${netDelta >= 0 ? '+' : ''}${fmt(netDelta)} 🪙)
    </div>
    <div class="multi-open-grid">${cards}</div>
  `;
  actionsEl.style.display = 'flex';
  if (netDelta > totalCost) tg?.HapticFeedback?.notificationOccurred?.('success');
  loadInventory();

  // Wire "Открыть ещё" — runs the same multi-open flow again with the current case.
  // .onclick re-assignment is intentional: previous handler (from earlier open) is replaced.
  const againBtn = document.getElementById('case-open-again');
  if (againBtn) {
    againBtn.onclick = () => {
      if (!state.me || state.me.balance < totalCost) {
        toast(`Не хватает: нужно ${fmt(totalCost)} 🪙`);
        return;
      }
      openCaseMulti(caseId, count);
    };
  }
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

  // Coinflip-lock notice — shown only when at least one item is staked in PvP
  const summaryEl = document.querySelector('.inv-summary');
  let cfNote = document.getElementById('inv-cf-locked-note');
  const lockedCount = inv.coinflip_locked_count || 0;
  const lockedValue = inv.coinflip_locked_value || 0;
  if (lockedCount > 0) {
    if (!cfNote) {
      cfNote = document.createElement('div');
      cfNote.id = 'inv-cf-locked-note';
      cfNote.className = 'inv-cf-locked';
      summaryEl?.insertAdjacentElement('afterend', cfNote);
    }
    cfNote.innerHTML = `🔒 <b>${lockedCount}</b> предмет(ов) на <b>${fmt(lockedValue)} 🪙</b> в Coinflip-лобби`;
  } else if (cfNote) {
    cfNote.remove();
  }

  if (!inv.items.length) {
    grid.innerHTML = lockedCount > 0
      ? '<div class="loader">Все скины поставлены в coinflip-лобби. Жди исхода или отмени лобби.</div>'
      : '<div class="loader">Пусто. Открой первый кейс!</div>';
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
  } else if (game === 'mines') {
    renderMines(area);
  } else if (game === 'plinko') {
    renderPlinko(area);
  } else if (game === 'cfpvp') {
    renderCfPvp(area);
  } else if (game === 'tycoon') {
    renderTycoon(area);
  } else if (game === 'forge') {
    renderForge(area);
  }
}

// ======================= MINES (CS-themed Сапёр) =======================

const minesState = {
  area: null,
  config: null,
  active: null,         // current game: { bet, bombs, revealed, multiplier, ... }
  bet: 100,
  bombs: 3,
  busy: false,
};

const MINES_DEFAULT_BOMBS = [1, 3, 5, 7, 10, 15, 24];
const MINES_BET_PRESETS   = [100, 500, 1000, 2500, 5000, 10000];

function _minesFmtMult(m) {
  if (m === undefined || m === null) return '—';
  if (m >= 1000) return (m / 1000).toFixed(1).replace(/\.0$/, '') + 'k×';
  if (m >= 100)  return m.toFixed(0) + '×';
  if (m >= 10)   return m.toFixed(2) + '×';
  return m.toFixed(2) + '×';
}

async function renderMines(area) {
  minesState.area = area;
  area.innerHTML = `<div class="mines-play"><div class="loader">Загрузка…</div></div>`;
  try {
    if (!minesState.config) {
      minesState.config = await api('/api/casino/mines/config');
    }
    const cur = await api('/api/casino/mines/state');
    if (cur && cur.active) {
      minesState.active = cur;
      minesState.bet = cur.bet;
      minesState.bombs = cur.bombs;
    } else {
      minesState.active = null;
    }
    _minesPaint();
  } catch (e) {
    area.innerHTML = `<div class="mines-play"><div class="loader">Ошибка: ${escape(e.message)}</div></div>`;
  }
}

function _minesPaint() {
  const area = minesState.area;
  if (!area) return;
  const active = minesState.active;
  const cfg = minesState.config || {};
  const firstMult = cfg.first_pick_mult || {};

  // Header (current state)
  const headerLeft = active
    ? `<div class="mines-stat"><span class="lbl">СТАВКА</span><span class="val">${fmt(active.bet)} 🪙</span></div>
       <div class="mines-stat"><span class="lbl">БОМБ</span><span class="val danger">${active.bombs}</span></div>
       <div class="mines-stat"><span class="lbl">МНОЖИТЕЛЬ</span><span class="val gold">${_minesFmtMult(active.multiplier)}</span></div>`
    : `<div class="mines-stat"><span class="lbl">СТАВКА</span><span class="val">${fmt(minesState.bet)} 🪙</span></div>
       <div class="mines-stat"><span class="lbl">БОМБ</span><span class="val danger">${minesState.bombs}</span></div>
       <div class="mines-stat"><span class="lbl">×1 ПИК</span><span class="val gold">${_minesFmtMult(firstMult[minesState.bombs] || 1)}</span></div>`;

  // Grid (always 5×5, blank cells if no active game)
  let cells = '';
  for (let i = 0; i < 25; i++) {
    let cls = 'mc-cell';
    let inner = '';
    if (active) {
      const isRevealed = active.revealed && active.revealed.includes(i);
      if (isRevealed) {
        cls += ' revealed safe';
        inner = `<div class="mc-icon mc-diamond">${_minesDiamondSvg()}</div>`;
      }
    } else {
      cls += ' inactive';
    }
    cells += `<button class="${cls}" data-cell="${i}" ${active ? '' : 'disabled'}>${inner}</button>`;
  }

  // Controls
  let controls = '';
  if (active) {
    const cashoutDisabled = (active.revealed_count || (active.revealed || []).length) === 0;
    controls = `
      <div class="mines-cashout-row">
        <button class="btn mines-cashout-btn ${cashoutDisabled ? 'disabled' : ''}" id="mines-cashout" ${cashoutDisabled ? 'disabled' : ''}>
          <div class="mc-cashout-label">CASH OUT</div>
          <div class="mc-cashout-amt">${fmt(active.potential_payout || 0)} 🪙</div>
        </button>
      </div>
      <div class="mines-next-hint">
        Следующий пик: <b class="gold">${_minesFmtMult(active.next_multiplier || 0)}</b>
        <span style="opacity:0.5">·</span>
        <b>${fmt(active.next_payout || 0)} 🪙</b>
      </div>
    `;
  } else {
    // Bomb selector chips
    const bombChips = MINES_DEFAULT_BOMBS.map(b => {
      const sel = (b === minesState.bombs) ? 'selected' : '';
      return `<button class="mc-chip ${sel}" data-bombs="${b}">${b}</button>`;
    }).join('');
    const betChips = MINES_BET_PRESETS.map(v => {
      const sel = (v === minesState.bet) ? 'selected' : '';
      return `<button class="mc-chip mc-chip-bet ${sel}" data-bet="${v}">${fmt(v)}</button>`;
    }).join('');
    controls = `
      <div class="mines-setup">
        <div class="mc-row">
          <div class="mc-row-label">СТАВКА</div>
          <input type="text" inputmode="numeric" pattern="[0-9]*" id="mc-bet-input" value="${minesState.bet}" autocomplete="off" />
        </div>
        <div class="mc-chips" id="mc-bet-chips">${betChips}</div>
        <div class="mc-row">
          <div class="mc-row-label">КОЛИЧЕСТВО БОМБ</div>
          <div class="mc-bombs-current">${minesState.bombs}</div>
        </div>
        <div class="mc-chips" id="mc-bomb-chips">${bombChips}</div>
        <button class="btn big-btn mines-start-btn" id="mines-start">
          <span class="mc-start-icon">${_minesDefuseSvg()}</span>
          <span>ИГРАТЬ · ${fmt(minesState.bet)} 🪙</span>
        </button>
        <div class="mines-rtp-note">RTP ${(((minesState.config && minesState.config.rtp) || 0.96) * 100).toFixed(0)}% · 5×5 · максимум <b>${_minesFmtMult((minesState.config && minesState.config.max_mult && minesState.config.max_mult[minesState.bombs]) || 1)}</b></div>
      </div>
    `;
  }

  area.innerHTML = `
    <div class="mines-play">
      <div class="mines-title">
        <span class="mc-title-bracket">[</span> DEFUSE <span class="mc-title-bracket">]</span>
        <span class="mc-title-sub">RIP × Mines</span>
      </div>
      <div class="mines-header">${headerLeft}</div>
      <div class="mines-grid" id="mines-grid">${cells}</div>
      ${controls}
      <div id="mines-out" class="mines-out" style="display:none"></div>
    </div>
  `;

  // wire grid clicks
  if (active) {
    area.querySelectorAll('.mc-cell').forEach(btn => {
      btn.addEventListener('click', () => {
        const i = parseInt(btn.dataset.cell);
        if (!isNaN(i)) _minesReveal(i);
      });
    });
    document.getElementById('mines-cashout')?.addEventListener('click', _minesCashout);
  } else {
    document.getElementById('mines-start')?.addEventListener('click', _minesStart);
    document.getElementById('mc-bet-input')?.addEventListener('input', (e) => {
      const v = parseInt(e.target.value.replace(/\D/g, ''));
      if (!isNaN(v) && v >= 0) {
        minesState.bet = v;
        _minesUpdateStartBtn();
        _minesUpdateBetChips();
      }
    });
    area.querySelectorAll('#mc-bet-chips [data-bet]').forEach(b => {
      b.addEventListener('click', () => {
        minesState.bet = parseInt(b.dataset.bet);
        document.getElementById('mc-bet-input').value = minesState.bet;
        _minesUpdateStartBtn();
        _minesUpdateBetChips();
      });
    });
    area.querySelectorAll('#mc-bomb-chips [data-bombs]').forEach(b => {
      b.addEventListener('click', () => {
        minesState.bombs = parseInt(b.dataset.bombs);
        _minesPaint();
      });
    });
  }
}

function _minesUpdateBetChips() {
  document.querySelectorAll('#mc-bet-chips [data-bet]').forEach(b => {
    b.classList.toggle('selected', parseInt(b.dataset.bet) === minesState.bet);
  });
}

function _minesUpdateStartBtn() {
  const btn = document.getElementById('mines-start');
  if (btn) btn.querySelector('span:last-child').textContent = `ИГРАТЬ · ${fmt(minesState.bet)} 🪙`;
}

async function _minesStart() {
  if (minesState.busy) return;
  if (!state.me || state.me.balance < minesState.bet) return toast('Не хватает монет');
  if (minesState.bet < 10) return toast('Минимальная ставка 10 🪙');
  minesState.busy = true;
  try {
    const r = await api('/api/casino/mines/start', {
      method: 'POST',
      body: JSON.stringify({ bet: minesState.bet, bombs: minesState.bombs }),
    });
    if (!r.ok) {
      toast(r.error || 'Не удалось запустить');
      return;
    }
    state.me.balance = r.new_balance;
    document.getElementById('balance-display').textContent = fmt(state.me.balance);
    minesState.active = r.state;
    tg?.HapticFeedback?.impactOccurred?.('light');
    _minesPaint();
  } catch (e) {
    toast(`Ошибка: ${e.message}`);
  } finally {
    minesState.busy = false;
  }
}

async function _minesReveal(cell) {
  if (minesState.busy || !minesState.active) return;
  if ((minesState.active.revealed || []).includes(cell)) return;
  minesState.busy = true;
  // Optimistic visual flip — server is authoritative, so we just lock the cell briefly.
  const cellEl = document.querySelector(`.mc-cell[data-cell="${cell}"]`);
  if (cellEl) cellEl.classList.add('revealing');
  try {
    const r = await api('/api/casino/mines/reveal', {
      method: 'POST',
      body: JSON.stringify({ cell }),
    });
    if (!r.ok) {
      toast(r.error || 'Ошибка');
      cellEl?.classList.remove('revealing');
      return;
    }
    if (r.safe) {
      tg?.HapticFeedback?.impactOccurred?.('medium');
      // Update active state in place — render new diamond, multiplier, cashout
      if (r.game_over) {
        // perfect run → instant payout
        await _minesShowResult({
          win: true,
          perfect: true,
          payout: r.payout,
          delta: r.delta,
          multiplier: r.multiplier,
          bombs: r.bombs_revealed,
          safe: r.safe_revealed,
          bombsCount: r.bombs_count,
          new_balance: r.new_balance,
        });
        return;
      }
      // mid-game safe — animate this cell as diamond, update header
      _minesAnimateReveal(cellEl, true);
      minesState.active.revealed = [...(minesState.active.revealed || []), cell];
      minesState.active.revealed_count = (minesState.active.revealed_count || 0) + 1;
      minesState.active.multiplier = r.multiplier;
      minesState.active.next_multiplier = r.next_multiplier;
      minesState.active.potential_payout = r.potential_payout;
      minesState.active.next_payout = r.next_payout;
      _minesUpdateHeaderAndCashout();
    } else {
      // BOMB → game over
      tg?.HapticFeedback?.notificationOccurred?.('error');
      _minesAnimateReveal(cellEl, false);
      await new Promise(res => setTimeout(res, 250));
      // Reveal all cells: bombs as bomb, others as diamonds (greyed)
      _minesRevealAllAfterLoss(r.bombs_revealed || [], r.safe_revealed || []);
      await new Promise(res => setTimeout(res, 700));
      await _minesShowResult({
        win: false,
        payout: 0,
        delta: -minesState.active.bet,
        multiplier: 0,
        bombs: r.bombs_revealed,
        safe: r.safe_revealed,
        bombsCount: r.bombs_count,
        new_balance: r.new_balance,
      });
    }
  } catch (e) {
    toast(`Ошибка: ${e.message}`);
    cellEl?.classList.remove('revealing');
  } finally {
    minesState.busy = false;
  }
}

function _minesAnimateReveal(cellEl, isSafe) {
  if (!cellEl) return;
  cellEl.classList.remove('revealing');
  cellEl.classList.add('revealed', isSafe ? 'safe' : 'bomb', 'flip');
  cellEl.innerHTML = `<div class="mc-icon ${isSafe ? 'mc-diamond' : 'mc-bomb'}">${isSafe ? _minesDiamondSvg() : _minesBombSvg()}</div>`;
}

function _minesRevealAllAfterLoss(bombs, safeOpened) {
  document.querySelectorAll('.mc-cell').forEach(btn => {
    const i = parseInt(btn.dataset.cell);
    if (btn.classList.contains('revealed')) return;
    btn.disabled = true;
    if (bombs.includes(i)) {
      btn.classList.add('revealed', 'bomb', 'dim');
      btn.innerHTML = `<div class="mc-icon mc-bomb">${_minesBombSvg()}</div>`;
    } else {
      btn.classList.add('revealed', 'safe', 'dim');
      btn.innerHTML = `<div class="mc-icon mc-diamond">${_minesDiamondSvg()}</div>`;
    }
  });
}

function _minesUpdateHeaderAndCashout() {
  const a = minesState.active;
  if (!a) return;
  const header = document.querySelector('.mines-header');
  if (header) {
    header.innerHTML = `
      <div class="mines-stat"><span class="lbl">СТАВКА</span><span class="val">${fmt(a.bet)} 🪙</span></div>
      <div class="mines-stat"><span class="lbl">БОМБ</span><span class="val danger">${a.bombs}</span></div>
      <div class="mines-stat"><span class="lbl">МНОЖИТЕЛЬ</span><span class="val gold">${_minesFmtMult(a.multiplier)}</span></div>`;
  }
  const co = document.getElementById('mines-cashout');
  if (co) {
    const amtEl = co.querySelector('.mc-cashout-amt');
    if (amtEl) amtEl.textContent = `${fmt(a.potential_payout || 0)} 🪙`;
    co.classList.remove('disabled');
    co.disabled = false;
  }
  const hint = document.querySelector('.mines-next-hint');
  if (hint) {
    hint.innerHTML = `Следующий пик: <b class="gold">${_minesFmtMult(a.next_multiplier || 0)}</b>
      <span style="opacity:0.5">·</span>
      <b>${fmt(a.next_payout || 0)} 🪙</b>`;
  }
}

async function _minesCashout() {
  if (minesState.busy || !minesState.active) return;
  minesState.busy = true;
  try {
    const r = await api('/api/casino/mines/cashout', { method: 'POST' });
    if (!r.ok) { toast(r.error || 'Ошибка'); return; }
    tg?.HapticFeedback?.notificationOccurred?.('success');
    // Reveal remaining cells visually (so player sees the would-have-been bombs)
    _minesRevealAllAfterLoss(r.bombs_revealed || [], r.safe_revealed || []);
    await new Promise(res => setTimeout(res, 600));
    await _minesShowResult({
      win: true,
      perfect: false,
      payout: r.payout,
      delta: r.delta,
      multiplier: r.multiplier,
      bombs: r.bombs_revealed,
      safe: r.safe_revealed,
      bombsCount: r.bombs_count,
      new_balance: r.new_balance,
    });
  } catch (e) {
    toast(`Ошибка: ${e.message}`);
  } finally {
    minesState.busy = false;
  }
}

async function _minesShowResult(res) {
  if (typeof res.new_balance === 'number') {
    state.me.balance = res.new_balance;
    document.getElementById('balance-display').textContent = fmt(state.me.balance);
  }
  const out = document.getElementById('mines-out');
  if (out) {
    const cls = res.win ? (res.perfect ? 'mines-out-perfect' : 'mines-out-win') : 'mines-out-lose';
    const title = res.win ? (res.perfect ? '🏆 ИДЕАЛЬНАЯ ПАРТИЯ' : '✅ CASHED OUT') : '💥 BOMB DEFUSED FAIL';
    const sub = res.win
      ? `${_minesFmtMult(res.multiplier)} · +${fmt(res.delta)} 🪙`
      : `−${fmt(Math.abs(res.delta))} 🪙`;
    out.className = `mines-out ${cls}`;
    out.style.display = 'block';
    out.innerHTML = `
      <div class="mc-out-title">${title}</div>
      <div class="mc-out-sub">${sub}</div>
      <button class="btn big-btn daily-btn" id="mines-replay">Сыграть ещё</button>
    `;
    document.getElementById('mines-replay')?.addEventListener('click', () => {
      minesState.active = null;
      _minesPaint();
    });
  }
}

// ---- Inline SVGs (CS-themed, no emojis on the grid) ----
function _minesDiamondSvg() {
  return `<svg viewBox="0 0 32 32" width="100%" height="100%" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="1.6">
    <linearGradient id="mcDiaGr" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#9be7ff" stop-opacity="1"/>
      <stop offset="100%" stop-color="#3a86ff" stop-opacity="1"/>
    </linearGradient>
    <polygon points="16,4 28,12 16,28 4,12" fill="url(#mcDiaGr)" stroke="#cfe9ff"/>
    <line x1="4"  y1="12" x2="28" y2="12" stroke="#0a0c14" stroke-opacity="0.45"/>
    <line x1="10" y1="12" x2="16" y2="28" stroke="#0a0c14" stroke-opacity="0.45"/>
    <line x1="22" y1="12" x2="16" y2="28" stroke="#0a0c14" stroke-opacity="0.45"/>
    <line x1="4"  y1="12" x2="10" y2="4"  stroke="#0a0c14" stroke-opacity="0.25"/>
    <line x1="28" y1="12" x2="22" y2="4"  stroke="#0a0c14" stroke-opacity="0.25"/>
  </svg>`;
}

function _minesBombSvg() {
  // C4-inspired: dark block with red wires + LED
  return `<svg viewBox="0 0 32 32" width="100%" height="100%" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round">
    <rect x="6" y="10" width="20" height="16" rx="2" fill="#1a1a1d" stroke="#3a2422"/>
    <rect x="9" y="13" width="14" height="6" rx="1" fill="#2a1a1a" stroke="#5b2a2a"/>
    <circle cx="22" cy="22" r="1.6" fill="#ff4040" stroke="#ff8888"/>
    <line x1="11" y1="10" x2="11" y2="6" stroke="#ff4040" stroke-width="1.8"/>
    <line x1="16" y1="10" x2="16" y2="4" stroke="#ffd84a" stroke-width="1.8"/>
    <line x1="21" y1="10" x2="21" y2="6" stroke="#3a86ff" stroke-width="1.8"/>
    <circle cx="11" cy="6" r="1.2" fill="#ff4040"/>
    <circle cx="16" cy="4" r="1.2" fill="#ffd84a"/>
    <circle cx="21" cy="6" r="1.2" fill="#3a86ff"/>
  </svg>`;
}

function _minesDefuseSvg() {
  return `<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
    <path d="M14 4l-2 6h4l-6 10 2-7H8z"/>
  </svg>`;
}

// ======================= PLINKO (drop-the-grenade) =======================

const plinkoState = {
  area: null,
  config: null,        // /config response
  mode: 'classic',
  bet: 100,
  history: [],         // last ~12 multipliers, recent on right
  lastClickAt: 0,      // anti-double-click rate limit
  ballSeq: 0,          // monotonic ball id
};

// Source of truth for "balls currently in flight" — live DOM, never desynced.
function _plinkoBallsInFlight() {
  return document.querySelectorAll('.pl-svg .pl-ball').length;
}

const PLINKO_BET_PRESETS = [50, 100, 200, 500, 1000];
const PLINKO_MIN_CLICK_GAP_MS = 90;     // 11 drops/sec max
const PLINKO_MAX_ACTIVE_BALLS  = 25;    // safety cap to prevent rendering catastrophe

// Bucket color tier by multiplier value
function _plinkoBucketTier(m) {
  if (m >= 100)      return 'jackpot';
  if (m >= 10)       return 'high';
  if (m >= 4)        return 'mid';
  if (m >= 1.5)      return 'low';
  if (m >= 1.0)      return 'flat';
  return 'loss';
}

function _plinkoFmtMult(m) {
  if (m >= 100)  return m.toFixed(0) + '×';
  if (m >= 10)   return m.toFixed(1).replace(/\.0$/, '') + '×';
  return m.toFixed(2).replace(/\.?0+$/, '') + '×';
}

async function renderPlinko(area) {
  plinkoState.area = area;
  area.innerHTML = `<div class="plinko-play"><div class="loader">Загрузка…</div></div>`;
  try {
    if (!plinkoState.config) {
      plinkoState.config = await api('/api/casino/plinko/config');
    }
    _plinkoPaint();
  } catch (e) {
    area.innerHTML = `<div class="plinko-play"><div class="loader">Ошибка: ${escape(e.message)}</div></div>`;
  }
}

function _plinkoPaint() {
  const area = plinkoState.area;
  const cfg  = plinkoState.config;
  if (!area || !cfg) return;

  const mode = cfg.modes[plinkoState.mode] || cfg.modes.classic;
  const rows = mode.rows;
  const pays = mode.pays;
  const maxBet = mode.max_bet;

  // Mode chips
  const modeChips = ['casual','classic','savage'].map(k => {
    const m = cfg.modes[k];
    if (!m) return '';
    const sel = k === plinkoState.mode ? 'selected' : '';
    return `<button class="pl-mode-chip ${sel}" data-mode="${k}" style="--mc:${m.color}">
      <div class="pl-mode-name">${m.label}</div>
      <div class="pl-mode-meta">${m.rows} · до ${pays_max(m.pays)}×</div>
    </button>`;
  }).join('');

  // Bet chips (clamped to mode's max)
  const betChips = PLINKO_BET_PRESETS.filter(v => v <= maxBet).map(v => {
    const sel = v === plinkoState.bet ? 'selected' : '';
    return `<button class="pl-bet-chip ${sel}" data-bet="${v}">${fmt(v)}</button>`;
  }).join('') + `<button class="pl-bet-chip" data-bet="${maxBet}">МАКС ${fmt(maxBet)}</button>`;

  // SVG board (precomputed, ball injected dynamically)
  const svgBoard = _plinkoBuildBoardSvg(rows, pays);

  // History strip
  const histHtml = plinkoState.history.length > 0
    ? plinkoState.history.map(m => `<span class="pl-hist-chip pl-tier-${_plinkoBucketTier(m)}">${_plinkoFmtMult(m)}</span>`).join('')
    : `<span class="pl-hist-empty">пусто</span>`;

  area.innerHTML = `
    <div class="plinko-play">
      <div class="pl-title">
        <span class="pl-title-bracket">[</span> PLINKO <span class="pl-title-bracket">]</span>
        <span class="pl-title-sub">DROP × MULTIPLY</span>
      </div>

      <div class="pl-history">
        <div class="pl-history-label">ПОСЛЕДНИЕ</div>
        <div class="pl-history-strip">${histHtml}</div>
      </div>

      <div class="pl-modes">${modeChips}</div>

      <div class="pl-board" id="pl-board">${svgBoard}</div>

      <div class="pl-controls">
        <div class="pl-bet-row">
          <div class="pl-row-label">СТАВКА</div>
          <input type="text" inputmode="numeric" pattern="[0-9]*" id="pl-bet-input" value="${plinkoState.bet}" autocomplete="off" />
        </div>
        <div class="pl-bet-chips" id="pl-bet-chips">${betChips}</div>
        <button class="btn big-btn pl-drop-btn" id="pl-drop-btn">
          <span class="pl-drop-icon">${_plinkoGrenadeSvg()}</span>
          <span>DROP · ${fmt(plinkoState.bet)} 🪙</span>
        </button>
        <div class="pl-rtp-note">RTP <b>${(mode.rtp * 100).toFixed(0)}%</b> · ${mode.rows} рядов · макс ставка <b>${fmt(maxBet)}</b></div>
      </div>

      <div id="pl-out" class="pl-out" style="display:none"></div>
    </div>
  `;

  // Wire events
  area.querySelectorAll('.pl-mode-chip[data-mode]').forEach(b => {
    b.addEventListener('click', () => {
      // Switching mode rebuilds the SVG; in-flight balls would lose their geometry.
      // Block only if there are ACTUAL balls in the DOM (no stale counter).
      if (_plinkoBallsInFlight() > 0) return toast('Сначала пусть упадут все шарики');
      plinkoState.mode = b.dataset.mode;
      const newMax = cfg.modes[plinkoState.mode].max_bet;
      if (plinkoState.bet > newMax) plinkoState.bet = newMax;
      _plinkoPaint();
    });
  });
  area.querySelectorAll('.pl-bet-chip[data-bet]').forEach(b => {
    b.addEventListener('click', () => {
      plinkoState.bet = parseInt(b.dataset.bet);
      document.getElementById('pl-bet-input').value = plinkoState.bet;
      _plinkoUpdateBetChips();
      _plinkoUpdateDropBtn();
    });
  });
  document.getElementById('pl-bet-input')?.addEventListener('input', e => {
    let v = parseInt(e.target.value.replace(/\D/g, '')) || 0;
    const max = cfg.modes[plinkoState.mode].max_bet;
    if (v > max) v = max;
    plinkoState.bet = v;
    _plinkoUpdateBetChips();
    _plinkoUpdateDropBtn();
  });
  document.getElementById('pl-drop-btn')?.addEventListener('click', _plinkoDrop);
}

function pays_max(arr) {
  return Math.max.apply(null, arr).toFixed(0);
}

function _plinkoUpdateBetChips() {
  document.querySelectorAll('.pl-bet-chip[data-bet]').forEach(b => {
    b.classList.toggle('selected', parseInt(b.dataset.bet) === plinkoState.bet);
  });
}
function _plinkoUpdateDropBtn() {
  const btn = document.getElementById('pl-drop-btn');
  if (btn) {
    const span = btn.querySelector('span:last-child');
    if (span) span.textContent = `DROP · ${fmt(plinkoState.bet)} 🪙`;
  }
}

// ----- Geometry helpers -----
const PL_VB_W      = 360;          // SVG viewBox width
const PL_PEG_R     = 2.6;          // peg radius
const PL_BALL_R    = 4.0;
const PL_TOP_PAD   = 14;
const PL_BUCKET_H  = 22;

function _plinkoLayout(rows) {
  // Bucket count = rows + 1; we use full viewBox width for buckets.
  const buckets = rows + 1;
  const bW = PL_VB_W / buckets;
  // Row height: scale so the board has a nice aspect
  const rowH = Math.max(14, 22 - 0.5 * (rows - 8)); // 8 rows: ~22, 16 rows: ~18
  const totalH = PL_TOP_PAD + rows * rowH + PL_BUCKET_H + 8;
  return { rows, buckets, bW, rowH, totalH };
}

// peg in row r, column c (c=0..r): canonical Galton-board lane formula.
function _plinkoPegPos(r, c, lay) {
  // peg lane = (rows - r)/2 + c (lanes count from 0); peg x = (lane + 0.5) * bW... but
  // using the bucket-centered convention: bucket k center at (k + 0.5) * bW.
  // Pegs interleave with buckets: row r col c is at lane = (rows - r)/2 + c, x = (lane + 0.5) * bW.
  const lane = (lay.rows - r) / 2 + c;
  return {
    x: (lane + 0.5) * lay.bW,
    y: PL_TOP_PAD + r * lay.rowH,
  };
}

function _plinkoBucketCenter(k, lay) {
  return {
    x: (k + 0.5) * lay.bW,
    y: PL_TOP_PAD + lay.rows * lay.rowH + PL_BUCKET_H / 2,
  };
}

function _plinkoBuildBoardSvg(rows, pays) {
  const lay = _plinkoLayout(rows);
  let pegsHtml = '';
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c <= r; c++) {
      const p = _plinkoPegPos(r, c, lay);
      pegsHtml += `<circle class="pl-peg" cx="${p.x.toFixed(2)}" cy="${p.y.toFixed(2)}" r="${PL_PEG_R}"/>`;
    }
  }

  let bucketsHtml = '';
  for (let k = 0; k <= rows; k++) {
    const bx = k * lay.bW;
    const by = PL_TOP_PAD + rows * lay.rowH;
    const tier = _plinkoBucketTier(pays[k]);
    bucketsHtml += `
      <g class="pl-bucket pl-tier-${tier}" data-bucket="${k}" transform="translate(${bx.toFixed(2)},${by.toFixed(2)})">
        <rect class="pl-bucket-rect" x="0.5" y="0" width="${(lay.bW - 1).toFixed(2)}" height="${PL_BUCKET_H}" rx="2.5"/>
        <text class="pl-bucket-text" x="${(lay.bW / 2).toFixed(2)}" y="${(PL_BUCKET_H / 2 + 3).toFixed(2)}" text-anchor="middle">${_plinkoFmtMult(pays[k])}</text>
      </g>
    `;
  }

  // Drop slot indicator at the very top
  const dropX = lay.bW * (lay.rows / 2 + 0.5);

  return `
    <svg class="pl-svg" viewBox="0 0 ${PL_VB_W} ${lay.totalH}" preserveAspectRatio="xMidYMid meet">
      <defs>
        <linearGradient id="pl-bg" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="#0a0c14"/>
          <stop offset="100%" stop-color="#11141d"/>
        </linearGradient>
        <radialGradient id="pl-ball" cx="0.35" cy="0.35" r="0.65">
          <stop offset="0%" stop-color="#fff5d8"/>
          <stop offset="40%" stop-color="#f5b042"/>
          <stop offset="100%" stop-color="#a85e08"/>
        </radialGradient>
        <filter id="pl-glow" x="-30%" y="-30%" width="160%" height="160%">
          <feGaussianBlur stdDeviation="1.6"/>
        </filter>
      </defs>
      <rect class="pl-bg-rect" x="0" y="0" width="${PL_VB_W}" height="${lay.totalH}" fill="url(#pl-bg)"/>
      <line class="pl-drop-line" x1="${dropX}" y1="2" x2="${dropX}" y2="${PL_TOP_PAD - 2}"/>
      ${pegsHtml}
      ${bucketsHtml}
    </svg>
  `;
}

// Drop a ball. Multi-click safe: each call → independent API + ball animation.
// Frontend pre-validates balance; server is final authority.
async function _plinkoDrop() {
  // Anti-double click rate-limit (allows ~11 drops/sec)
  const now = Date.now();
  if (now - plinkoState.lastClickAt < PLINKO_MIN_CLICK_GAP_MS) return;
  plinkoState.lastClickAt = now;

  // Cap simultaneous balls (prevents DoS on slow phones). DOM-based — never stale.
  if (_plinkoBallsInFlight() >= PLINKO_MAX_ACTIVE_BALLS) {
    return toast('Слишком много шариков в воздухе');
  }

  const bet = plinkoState.bet;
  const mode = plinkoState.mode;
  if (bet < 10) return toast('Минимум 10 🪙');
  if (!state.me || state.me.balance < bet) return toast('Не хватает монет');

  // Bet is deducted from the visible balance NOW. Payout (if any) is credited
  // only when the ball physically lands in its bucket (see _plinkoBallLanded).
  state.me.balance -= bet;
  const balEl = document.getElementById('balance-display');
  if (balEl) balEl.textContent = fmt(state.me.balance);

  let resp;
  try {
    resp = await api('/api/casino/plinko/play', {
      method: 'POST',
      body: JSON.stringify({ bet, mode }),
    });
  } catch (e) {
    state.me.balance += bet;
    if (balEl) balEl.textContent = fmt(state.me.balance);
    toast(`Ошибка: ${e.message}`);
    return;
  }
  if (!resp || !resp.ok) {
    state.me.balance += bet;
    if (balEl) balEl.textContent = fmt(state.me.balance);
    toast((resp && resp.error) || 'Ошибка');
    return;
  }

  // DO NOT sync new_balance here — that would credit the win before the ball lands.
  // The payout is added to the visible balance in _plinkoBallLanded.

  tg?.HapticFeedback?.impactOccurred?.('light');
  _plinkoSpawnBall(resp);
}

// ==================== ANIMATION ENGINE ====================
// One global rAF loop for ALL Plinko balls. Avoids per-ball recursive rAF
// (which can be silently lost if one frame throws or the tab is throttled),
// and is more efficient when many balls are in flight.
//
// Invariants:
//  - A ball lives in `_plinkoLiveBalls` until it lands (or its SVG element
//    leaves the DOM, e.g. mode change rebuilt the board).
//  - The loop ticks while there is at least one ball; otherwise it idles.
//  - The loop never `return`s on a per-ball error — each ball is wrapped in
//    try/catch so one bad ball can't freeze the others.

const _plinkoLiveBalls = [];   // array of ball-state objects
let   _plinkoLoopRunning = false;

function _plinkoStartLoopIfIdle() {
  if (_plinkoLoopRunning) return;
  _plinkoLoopRunning = true;
  requestAnimationFrame(_plinkoTick);
}

function _plinkoTick(now) {
  // Iterate from end so we can splice safely.
  for (let i = _plinkoLiveBalls.length - 1; i >= 0; i--) {
    const b = _plinkoLiveBalls[i];
    try {
      // Element was removed externally (mode change, view nav) — drop the entry
      if (!b.el.isConnected) {
        _plinkoLiveBalls.splice(i, 1);
        continue;
      }
      const elapsed = Math.max(0, now - b.startedAt);

      if (elapsed >= b.totalDur) {
        // Settle in bucket and call landed handler
        const last = b.waypoints[b.waypoints.length - 1];
        b.el.setAttribute('cx', last.x.toFixed(2));
        b.el.setAttribute('cy', last.y.toFixed(2));
        _plinkoLiveBalls.splice(i, 1);
        _plinkoBallLanded(b.el, b.resp);
        continue;
      }

      // Determine which segment we're in (clamp to [0, segments-1])
      const segCount = b.waypoints.length - 1;
      const segIdx = Math.min(segCount - 1, Math.max(0, Math.floor(elapsed / b.segDur)));
      const t = Math.min(1, Math.max(0, (elapsed - segIdx * b.segDur) / b.segDur));

      const a = b.waypoints[segIdx];
      const c = b.waypoints[segIdx + 1];
      // Gravity-bias y, linear x, plus a small parabolic hop to suggest a bounce
      const yT = Math.pow(t, 1.35);
      const x = a.x + (c.x - a.x) * t;
      let   y = a.y + (c.y - a.y) * yT;
      y -= Math.sin(Math.PI * t) * b.bounceAmp;
      b.el.setAttribute('cx', x.toFixed(2));
      b.el.setAttribute('cy', y.toFixed(2));

      // Peg-light when a new segment begins (and only on actual peg-rows)
      if (segIdx !== b.lastSegIdx) {
        b.lastSegIdx = segIdx;
        if (segIdx >= 0 && segIdx < b.rows) {
          // Count R's in path[0..segIdx-1] → peg column
          let rcount = 0;
          for (let p = 0; p < segIdx; p++) if (b.path[p] === 'R') rcount += 1;
          const pegPos = _plinkoPegPos(segIdx, rcount, b.lay);
          // Peg elements live in the SVG containing the ball
          const svg = b.el.parentNode;
          if (svg) {
            const pegEls = svg.querySelectorAll('.pl-peg');
            for (let p = 0; p < pegEls.length; p++) {
              const el = pegEls[p];
              const px = parseFloat(el.getAttribute('cx'));
              const py = parseFloat(el.getAttribute('cy'));
              if (Math.abs(px - pegPos.x) < 0.5 && Math.abs(py - pegPos.y) < 0.5) {
                el.classList.add('hit');
                setTimeout(() => el.classList.remove('hit'), 200);
                break;
              }
            }
          }
        }
      }
    } catch (err) {
      // A single misbehaving ball must NEVER stop the others.
      // Drop the offender and continue.
      try { b.el.remove(); } catch (_) {}
      _plinkoLiveBalls.splice(i, 1);
    }
  }

  if (_plinkoLiveBalls.length > 0) {
    requestAnimationFrame(_plinkoTick);
  } else {
    _plinkoLoopRunning = false;
  }
}

// Spawn an animated ball. Validates inputs, registers it with the global loop.
// Returns true on success, false if input was malformed.
function _plinkoSpawnBall(resp) {
  // ---- Validate config + mode ----
  const cfg = plinkoState.config;
  const mode = cfg && cfg.modes && cfg.modes[plinkoState.mode];
  if (!mode) return false;
  const rows = mode.rows;
  if (!Number.isFinite(rows) || rows < 1) return false;

  const svg = document.querySelector('.pl-svg');
  if (!svg) return false;

  // ---- Validate server response ----
  const path = Array.isArray(resp && resp.path) ? resp.path : null;
  if (!path || path.length !== rows) {
    // Mode/path mismatch — happens if user switched modes after click but before
    // the API resolved. Sync the visible balance to server-authoritative value
    // (which already includes the payout) so we're not desynced. No animation.
    if (resp && typeof resp.new_balance === 'number') {
      state.me.balance = resp.new_balance;
      const balEl = document.getElementById('balance-display');
      if (balEl) balEl.textContent = fmt(state.me.balance);
    }
    toast('Режим сменился во время дропа');
    return false;
  }
  // path entries must be 'L' or 'R'
  for (let i = 0; i < path.length; i++) {
    if (path[i] !== 'L' && path[i] !== 'R') return false;
  }
  const bucket = Number(resp.bucket);
  if (!Number.isInteger(bucket) || bucket < 0 || bucket > rows) return false;

  // ---- Build waypoints (drop point → pegs → bucket) ----
  const lay = _plinkoLayout(rows);
  const waypoints = new Array(rows + 2);
  waypoints[0] = { x: lay.bW * (lay.rows / 2 + 0.5), y: 0 };
  let prevR = 0;
  for (let r = 0; r < rows; r++) {
    const peg = _plinkoPegPos(r, prevR, lay);
    waypoints[r + 1] = { x: peg.x, y: peg.y - PL_BALL_R - 1 };
    if (path[r] === 'R') prevR += 1;
  }
  const fin = _plinkoBucketCenter(bucket, lay);
  waypoints[rows + 1] = { x: fin.x, y: fin.y };
  // Sanity: every waypoint must be a finite number
  for (let i = 0; i < waypoints.length; i++) {
    if (!Number.isFinite(waypoints[i].x) || !Number.isFinite(waypoints[i].y)) return false;
  }

  // ---- Create the SVG element ----
  const ballId = ++plinkoState.ballSeq;
  const el = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
  el.setAttribute('class', 'pl-ball');
  el.setAttribute('r', String(PL_BALL_R));
  el.setAttribute('fill', 'url(#pl-ball)');
  el.setAttribute('data-ball-id', String(ballId));
  el.setAttribute('cx', waypoints[0].x.toFixed(2));
  el.setAttribute('cy', waypoints[0].y.toFixed(2));
  svg.appendChild(el);

  // ---- Register with global loop ----
  // Per-row drop time is roughly constant (~165ms) regardless of rows so the
  // visual cadence feels the same: 8r ≈ 1.65s, 12r ≈ 2.31s, 16r ≈ 2.97s.
  const segDur = 165;
  _plinkoLiveBalls.push({
    el, resp, path, rows, lay, waypoints,
    startedAt: performance.now(),
    segDur,
    totalDur: (waypoints.length - 1) * segDur,
    bounceAmp: Math.min(4.5, lay.rowH * 0.35),
    lastSegIdx: -1,
  });
  _plinkoStartLoopIfIdle();
  return true;
}

function _plinkoBallLanded(ball, resp) {
  // Credit the payout NOW (bet was deducted on click; payout arrives on landing).
  // resp.payout = bet * multiplier (0 on full loss). Visible balance += payout.
  if (typeof resp.payout === 'number' && resp.payout > 0) {
    state.me.balance += resp.payout;
    const balEl = document.getElementById('balance-display');
    if (balEl) balEl.textContent = fmt(state.me.balance);
  }

  // Flash bucket
  const bucketEl = document.querySelector(`.pl-bucket[data-bucket="${resp.bucket}"]`);
  if (bucketEl) {
    bucketEl.classList.add('flash');
    setTimeout(() => bucketEl.classList.remove('flash'), 1100);
  }

  // Haptic per landing
  if (resp.win) {
    tg?.HapticFeedback?.notificationOccurred?.(resp.multiplier >= 50 ? 'success' : 'warning');
  } else {
    tg?.HapticFeedback?.notificationOccurred?.('error');
  }

  // Floating ±delta popup at the landing point (SVG text)
  const svg = ball.parentNode;
  if (svg) {
    const tier = _plinkoBucketTier(resp.multiplier);
    const popup = document.createElementNS('http://www.w3.org/2000/svg', 'text');
    popup.setAttribute('class', `pl-popup pl-tier-${tier}`);
    popup.setAttribute('x', ball.getAttribute('cx'));
    popup.setAttribute('y', (parseFloat(ball.getAttribute('cy')) - 4).toFixed(2));
    popup.setAttribute('text-anchor', 'middle');
    popup.textContent = (resp.delta >= 0 ? '+' : '') + fmt(resp.delta);
    svg.appendChild(popup);
    setTimeout(() => popup.remove(), 1200);
  }

  // Update history (recent on right)
  plinkoState.history = [...plinkoState.history, resp.multiplier].slice(-12);
  const histEl = document.querySelector('.pl-history-strip');
  if (histEl) {
    histEl.innerHTML = plinkoState.history
      .map(m => `<span class="pl-hist-chip pl-tier-${_plinkoBucketTier(m)}">${_plinkoFmtMult(m)}</span>`)
      .join('');
  }

  // Big result panel — only show for jackpot/high tier so rapid-fire isn't spammy
  const out = document.getElementById('pl-out');
  if (out && resp.multiplier >= 4) {
    const tier = _plinkoBucketTier(resp.multiplier);
    const cls = resp.win ? `pl-out-win pl-tier-${tier}` : `pl-out-lose`;
    out.className = `pl-out ${cls}`;
    out.style.display = 'block';
    out.innerHTML = `
      <div class="pl-out-mult">${_plinkoFmtMult(resp.multiplier)}</div>
      <div class="pl-out-amt">${resp.delta >= 0 ? '+' : ''}${fmt(resp.delta)} 🪙</div>
    `;
    // Auto-fade after 2.4s, but if a higher win comes in, the next call overwrites
    clearTimeout(out._fadeT);
    out._fadeT = setTimeout(() => { out.style.display = 'none'; }, 2400);
  }

  // Remove ball with a small "settle" delay so it visually rests in the bucket
  setTimeout(() => ball.remove(), 350);
}

function _plinkoGrenadeSvg() {
  return `<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
    <ellipse cx="12" cy="14" rx="6" ry="7" fill="rgba(245,176,66,0.25)"/>
    <rect x="10" y="4" width="4" height="3" rx="0.5" fill="#3a4459" stroke="#5a6a85"/>
    <line x1="12" y1="2" x2="14" y2="4" stroke="#aaa"/>
    <circle cx="14" cy="2.5" r="1.2" fill="#888"/>
  </svg>`;
}

// ======================= COINFLIP 1v1 (PvP) =======================

const cfState = {
  area: null,
  selectedIds: new Set(),  // creator: skins to stake. opponent: skins to match.
  pendingLobbyId: null,    // when navigated via deep link
};

const CF_MATCH_TOLERANCE = 0.10;

async function renderCfPvp(area) {
  cfState.area = area;
  cfState.selectedIds.clear();
  area.innerHTML = `<div class="cf-wrap"><div class="loader">Загрузка лобби…</div></div>`;
  try {
    const data = await api('/api/pvp/coinflip/list');
    _cfRenderHub(area, data);
  } catch (e) {
    area.innerHTML = `<div class="cf-wrap"><div class="loader">Ошибка: ${escape(e.message)}</div></div>`;
  }
}

function _cfRenderHub(area, data) {
  const open = data.open || [];
  const recent = data.recent || [];

  const openHtml = open.length === 0
    ? `<div class="cf-empty">Открытых лобби нет. Создай первое!</div>`
    : open.map(l => _cfLobbyCard(l)).join('');

  const recentHtml = recent.length === 0
    ? `<div class="cf-empty cf-empty-sm">История пуста</div>`
    : recent.map(l => {
        const winner = l.winner_id === l.creator_id ? l.creator_name : l.opponent_name;
        const loser  = l.winner_id === l.creator_id ? l.opponent_name : l.creator_name;
        return `
          <div class="cf-recent-row">
            <span class="cf-recent-winner">🏆 ${escape(winner || '—')}</span>
            <span class="cf-recent-vs">vs</span>
            <span class="cf-recent-loser">${escape(loser || '—')}</span>
            <span class="cf-recent-pot">${fmt(l.pot_value || 0)} 🪙</span>
          </div>
        `;
      }).join('');

  area.innerHTML = `
    <div class="cf-wrap">
      <div class="cf-title">
        <span class="cf-title-bracket">[</span> COINFLIP <span class="cf-title-bracket">]</span>
        <span class="cf-title-sub">SKIN DUEL</span>
      </div>

      <button class="btn big-btn cf-create-btn" id="cf-create">
        ⚔️ Создать лобби
      </button>

      <div class="cf-section-label">ОТКРЫТЫЕ ЛОББИ</div>
      <div class="cf-lobbies-list">${openHtml}</div>

      <div class="cf-section-label">НЕДАВНИЕ БИТВЫ</div>
      <div class="cf-recent-list">${recentHtml}</div>
    </div>
  `;

  document.getElementById('cf-create').addEventListener('click', () => _cfRenderCreate(area));
  area.querySelectorAll('.cf-lobby-card[data-lobby-id]').forEach(c => {
    c.addEventListener('click', () => _cfRenderLobby(area, parseInt(c.dataset.lobbyId)));
  });
  area.querySelectorAll('[data-cancel-lobby]').forEach(b => {
    b.addEventListener('click', async (e) => {
      e.stopPropagation();
      const id = parseInt(b.dataset.cancelLobby);
      if (!confirm('Отменить лобби и вернуть скины?')) return;
      try {
        const r = await api('/api/pvp/coinflip/cancel', { method: 'POST', body: JSON.stringify({ lobby_id: id }) });
        if (r.ok) { toast('Лобби отменено'); renderCfPvp(area); }
        else toast(r.error || 'Не удалось');
      } catch (err) { toast(err.message); }
    });
  });

  // If we arrived via deep-link (cf_<id>), auto-open that lobby now
  if (cfState.pendingLobbyId) {
    const target = cfState.pendingLobbyId;
    cfState.pendingLobbyId = null;
    setTimeout(() => _cfRenderLobby(area, target), 50);
  }
}

function _cfLobbyCard(l) {
  const skins = l.creator_skins || [];
  const previews = skins.slice(0, 4).map(s => `<img class="cf-thumb rarity-${s.rarity}" src="${s.image_url}" alt="" />`).join('');
  const more = skins.length > 4 ? `<span class="cf-thumb-more">+${skins.length - 4}</span>` : '';
  const expiresIn = l.expires_at ? Math.max(0, Math.round((new Date(l.expires_at) - Date.now()) / 60000)) : null;
  // Format expiry time as "Hh Mm" if > 60 min
  const expiryStr = expiresIn === null ? '' : (expiresIn >= 60 ? `${Math.floor(expiresIn/60)}ч ${expiresIn%60}м` : `${expiresIn}м`);
  const classExtra = (l.is_mine ? ' mine' : '') + (l.is_bot ? ' bot' : '');
  const tagHtml = l.is_mine
    ? '<span class="cf-mine-tag">ТЫ</span>'
    : (l.is_bot ? '<span class="cf-bot-tag">BOT</span>' : '');
  return `
    <div class="cf-lobby-card${classExtra}" data-lobby-id="${l.id}">
      <div class="cf-lobby-row">
        <div class="cf-lobby-creator">${escape(l.creator_name || '—')} ${tagHtml}</div>
        <div class="cf-lobby-value">${fmt(l.creator_value)} 🪙</div>
      </div>
      <div class="cf-lobby-thumbs">${previews}${more}</div>
      <div class="cf-lobby-row cf-lobby-foot">
        <div class="cf-lobby-meta">${skins.length} предмет(ов)${expiryStr ? ` · ${expiryStr}` : ''}</div>
        ${l.is_mine
          ? `<button class="cf-cancel-btn" data-cancel-lobby="${l.id}">отменить</button>`
          : `<div class="cf-lobby-action">⚔️ Принять →</div>`}
      </div>
    </div>
  `;
}

// ---------------- CREATE flow ----------------
async function _cfRenderCreate(area) {
  cfState.selectedIds.clear();
  area.innerHTML = `<div class="cf-wrap"><div class="loader">Загрузка инвентаря…</div></div>`;
  try {
    if (!state.inventory || !state.inventory.items) await loadInventory();
    _cfPaintCreate(area);
  } catch (e) {
    area.innerHTML = `<div class="cf-wrap"><div class="loader">Ошибка: ${escape(e.message)}</div></div>`;
  }
}

function _cfPaintCreate(area) {
  const inv = (state.inventory && state.inventory.items) || [];
  // Filter: not locked, not in another coinflip lobby
  const usable = inv.filter(i => !i.locked && !i.coinflip_lobby_id);
  // Sort by price desc
  usable.sort((a, b) => b.price - a.price);

  const grid = usable.map(it => {
    const sel = cfState.selectedIds.has(it.id) ? 'selected' : '';
    return `
      <div class="cf-inv-item rarity-${it.rarity} ${sel}" data-inv-id="${it.id}">
        <img src="${it.image_url}" alt="" loading="lazy" />
        <div class="cf-inv-name">${escape(it.weapon || '')}</div>
        <div class="cf-inv-price">${fmt(it.price)} 🪙</div>
      </div>
    `;
  }).join('');

  const total = _cfSelectedTotal(usable);
  const count = cfState.selectedIds.size;

  area.innerHTML = `
    <div class="cf-wrap">
      <button class="back-btn" id="cf-back">← к лобби</button>
      <div class="cf-title">
        <span class="cf-title-bracket">[</span> СОЗДАТЬ ЛОББИ <span class="cf-title-bracket">]</span>
      </div>
      <div class="cf-create-summary">
        <div>Выбрано: <b>${count}</b> · Сумма: <b class="gold">${fmt(total)} 🪙</b></div>
        <div class="cf-create-hint">Оппонент должен поставить ±${Math.round(CF_MATCH_TOLERANCE * 100)}% от суммы</div>
      </div>
      ${usable.length === 0
        ? `<div class="cf-empty">Нет доступных скинов (всё заблокировано или в других лобби)</div>`
        : `<div class="cf-inv-grid">${grid}</div>`}
      <div class="cf-create-actions">
        <button class="btn secondary" id="cf-clear-sel">Очистить</button>
        <button class="btn big-btn cf-create-confirm" id="cf-create-confirm" ${count === 0 ? 'disabled' : ''}>
          ⚔️ Создать (${fmt(total)} 🪙)
        </button>
      </div>
    </div>
  `;

  document.getElementById('cf-back').addEventListener('click', () => renderCfPvp(area));
  document.getElementById('cf-clear-sel').addEventListener('click', () => {
    cfState.selectedIds.clear();
    _cfPaintCreate(area);
  });
  area.querySelectorAll('.cf-inv-item[data-inv-id]').forEach(el => {
    el.addEventListener('click', () => {
      const id = parseInt(el.dataset.invId);
      if (cfState.selectedIds.has(id)) cfState.selectedIds.delete(id);
      else cfState.selectedIds.add(id);
      _cfPaintCreate(area);
    });
  });
  document.getElementById('cf-create-confirm').addEventListener('click', () => _cfCreateConfirm(area));
}

function _cfSelectedTotal(items) {
  let s = 0;
  for (const it of items) if (cfState.selectedIds.has(it.id)) s += (it.price || 0);
  return s;
}

async function _cfCreateConfirm(area) {
  const ids = Array.from(cfState.selectedIds);
  if (ids.length === 0) return;
  try {
    const r = await api('/api/pvp/coinflip/create', { method: 'POST', body: JSON.stringify({ inventory_ids: ids }) });
    if (!r.ok) { toast(r.error || 'Ошибка'); return; }
    cfState.selectedIds.clear();
    tg?.HapticFeedback?.notificationOccurred?.('success');
    toast('Лобби создано');
    // Reload inventory to reflect locked items, then go to lobby view
    state.inventory = null;
    await loadInventory();
    _cfRenderLobby(area, r.lobby.id);
  } catch (e) { toast(e.message); }
}

// ---------------- LOBBY VIEW (creator preview / opponent matchmaking) ----------------
async function _cfRenderLobby(area, lobbyId) {
  area.innerHTML = `<div class="cf-wrap"><div class="loader">Загрузка лобби…</div></div>`;
  try {
    const lobby = await api(`/api/pvp/coinflip/lobby/${lobbyId}`);
    const myId = state.me ? state.me.tg_id : null;
    const isCreator = myId && lobby.creator_id === myId;

    // If lobby already settled — show result animation
    if (lobby.status === 'settled') {
      _cfRenderSettledView(area, lobby, isCreator);
      return;
    }
    if (lobby.status === 'cancelled' || lobby.status === 'expired') {
      area.innerHTML = `<div class="cf-wrap"><div class="loader">Лобби закрыто (${lobby.status})</div><button class="btn" id="cf-back2">← к списку</button></div>`;
      document.getElementById('cf-back2').addEventListener('click', () => renderCfPvp(area));
      return;
    }

    if (isCreator) {
      _cfRenderCreatorWaiting(area, lobby);
    } else {
      cfState.selectedIds.clear();          // fresh selection per lobby entry
      // Always re-fetch inventory before showing the join picker. If the user
      // came straight from the lobby browser without ever opening the inventory
      // tab in this session, state.inventory would be empty → 'Нет доступных
      // скинов' even though they have plenty. Also picks up any items that
      // freed up from a just-resolved lobby.
      try { await loadInventory(); } catch (_) {}
      _cfRenderJoinFlow(area, lobby);
    }
  } catch (e) {
    area.innerHTML = `<div class="cf-wrap"><div class="loader">Ошибка: ${escape(e.message)}</div></div>`;
  }
}

function _cfRenderCreatorWaiting(area, lobby) {
  const skinsHtml = (lobby.creator_skins || []).map(s => `
    <div class="cf-stack-item rarity-${s.rarity}">
      <img src="${s.image_url}" alt="" />
      <div class="cf-stack-name">${escape(s.weapon || '')}</div>
      <div class="cf-stack-price">${fmt(s.price)} 🪙</div>
    </div>
  `).join('');

  area.innerHTML = `
    <div class="cf-wrap">
      <button class="back-btn" id="cf-back">← к лобби</button>
      <div class="cf-title">
        <span class="cf-title-bracket">[</span> ТВОЁ ЛОББИ #${lobby.id} <span class="cf-title-bracket">]</span>
      </div>
      <div class="cf-status-pill open">⏳ Ждём оппонента</div>

      <div class="cf-stack-block">
        <div class="cf-stack-label">ТВОЯ СТАВКА · ${fmt(lobby.creator_value)} 🪙</div>
        <div class="cf-stack-grid">${skinsHtml}</div>
      </div>

      <div class="cf-share-block">
        <button class="btn big-btn cf-share-btn" id="cf-share" ${lobby.invited_to_chat ? 'disabled' : ''}>
          ${lobby.invited_to_chat ? '✓ Уже отправлено в чат' : '📨 Кинуть приглашение в чат'}
        </button>
        <div class="cf-share-hint">Бот пришлёт пацанам кнопку «Принять вызов» в групповой чат</div>
      </div>

      <button class="btn secondary cf-cancel-full" id="cf-cancel">Отменить лобби (вернуть скины)</button>
    </div>
  `;
  document.getElementById('cf-back').addEventListener('click', () => renderCfPvp(area));
  document.getElementById('cf-cancel').addEventListener('click', async () => {
    if (!confirm('Отменить лобби?')) return;
    try {
      const r = await api('/api/pvp/coinflip/cancel', { method: 'POST', body: JSON.stringify({ lobby_id: lobby.id }) });
      if (r.ok) { toast('Лобби отменено'); state.inventory = null; renderCfPvp(area); }
      else toast(r.error || 'Не удалось');
    } catch (e) { toast(e.message); }
  });
  const shareBtn = document.getElementById('cf-share');
  if (shareBtn && !lobby.invited_to_chat) {
    shareBtn.addEventListener('click', async () => {
      shareBtn.disabled = true;
      try {
        const r = await api('/api/pvp/coinflip/share', { method: 'POST', body: JSON.stringify({ lobby_id: lobby.id }) });
        if (r.ok) {
          toast('✓ Приглашение отправлено в чат');
          shareBtn.textContent = '✓ Уже отправлено в чат';
        } else {
          toast(r.error || 'Не удалось');
          shareBtn.disabled = false;
        }
      } catch (e) { toast(e.message); shareBtn.disabled = false; }
    });
  }

  // Periodic poll: detect when opponent joins so we auto-show the result
  if (cfState.pollTimer) clearInterval(cfState.pollTimer);
  cfState.pollTimer = setInterval(async () => {
    if (!document.querySelector('.cf-wrap')) { clearInterval(cfState.pollTimer); cfState.pollTimer = null; return; }
    try {
      const fresh = await api(`/api/pvp/coinflip/lobby/${lobby.id}`);
      if (fresh.status === 'settled') {
        clearInterval(cfState.pollTimer); cfState.pollTimer = null;
        _cfRenderAnimation(area, fresh, /*creatorWon*/ fresh.winner_id === fresh.creator_id, /*spectatorMode*/ false);
      } else if (fresh.status !== 'open') {
        clearInterval(cfState.pollTimer); cfState.pollTimer = null;
        renderCfPvp(area);
      }
    } catch (_) {}
  }, 3500);
}

function _cfRenderJoinFlow(area, lobby) {
  // NOTE: do NOT clear selectedIds here — this function is also called as a
  // re-render after each item click. Clearing would wipe the user's selection
  // on every tap. The set is cleared by the caller when first entering the flow.
  const inv = (state.inventory && state.inventory.items) || [];
  const usable = inv.filter(i => !i.locked && !i.coinflip_lobby_id).sort((a, b) => b.price - a.price);

  const lo = Math.round(lobby.creator_value * (1 - CF_MATCH_TOLERANCE));
  const hi = Math.round(lobby.creator_value * (1 + CF_MATCH_TOLERANCE));

  const creatorThumbs = (lobby.creator_skins || []).slice(0, 6).map(s =>
    `<img class="cf-thumb rarity-${s.rarity}" src="${s.image_url}" alt="" />`
  ).join('');
  const moreCount = (lobby.creator_skins || []).length - 6;

  const myGrid = usable.map(it => {
    const sel = cfState.selectedIds.has(it.id) ? 'selected' : '';
    return `
      <div class="cf-inv-item rarity-${it.rarity} ${sel}" data-inv-id="${it.id}">
        <img src="${it.image_url}" alt="" loading="lazy" />
        <div class="cf-inv-name">${escape(it.weapon || '')}</div>
        <div class="cf-inv-price">${fmt(it.price)} 🪙</div>
      </div>
    `;
  }).join('');

  const myTotal = _cfSelectedTotal(usable);
  const inRange = myTotal >= lo && myTotal <= hi && cfState.selectedIds.size > 0;

  area.innerHTML = `
    <div class="cf-wrap">
      <button class="back-btn" id="cf-back">← назад</button>
      <div class="cf-title cf-title-sm">
        <span class="cf-title-bracket">[</span> ВЫЗОВ ОТ ${escape(lobby.creator_name || '—')} <span class="cf-title-bracket">]</span>
      </div>

      <div class="cf-vs-row">
        <div class="cf-vs-side">
          <div class="cf-vs-label">${escape(lobby.creator_name || '—')}</div>
          <div class="cf-vs-thumbs">${creatorThumbs}${moreCount > 0 ? `<span class="cf-thumb-more">+${moreCount}</span>` : ''}</div>
          <div class="cf-vs-value">${fmt(lobby.creator_value)} 🪙</div>
        </div>
        <div class="cf-vs-mid">VS</div>
        <div class="cf-vs-side cf-vs-me">
          <div class="cf-vs-label">ТЫ</div>
          <div class="cf-vs-thumbs cf-vs-thumbs-empty">собери стак ↓</div>
          <div class="cf-vs-value ${inRange ? 'gold' : 'danger'}">${fmt(myTotal)} 🪙</div>
        </div>
      </div>

      <div class="cf-join-range">
        Нужно поставить от <b>${fmt(lo)}</b> до <b>${fmt(hi)}</b> 🪙
      </div>

      ${usable.length === 0
        ? `<div class="cf-empty">Нет доступных скинов</div>`
        : `<div class="cf-inv-grid">${myGrid}</div>`}

      <button class="btn big-btn cf-join-confirm ${inRange ? '' : 'disabled'}" id="cf-join-confirm" ${inRange ? '' : 'disabled'}>
        ⚔️ Принять вызов (${fmt(myTotal)} 🪙)
      </button>
    </div>
  `;

  document.getElementById('cf-back').addEventListener('click', () => renderCfPvp(area));
  area.querySelectorAll('.cf-inv-item[data-inv-id]').forEach(el => {
    el.addEventListener('click', () => {
      const id = parseInt(el.dataset.invId);
      if (cfState.selectedIds.has(id)) cfState.selectedIds.delete(id);
      else cfState.selectedIds.add(id);
      _cfRenderJoinFlow(area, lobby);
    });
  });
  document.getElementById('cf-join-confirm').addEventListener('click', async () => {
    if (!inRange) return;
    const ids = Array.from(cfState.selectedIds);
    try {
      const r = await api('/api/pvp/coinflip/join', { method: 'POST', body: JSON.stringify({ lobby_id: lobby.id, inventory_ids: ids }) });
      if (!r.ok) { toast(r.error || 'Ошибка'); return; }
      cfState.selectedIds.clear();
      state.inventory = null;
      const creatorWon = r.creator_won;
      _cfRenderAnimation(area, r.lobby, creatorWon, /*spectatorMode*/ false);
    } catch (e) { toast(e.message); }
  });
}

// ---------------- AVATARS (deterministic by tg_id) ----------------
// Generates a colored circle with name initials. Bot uses a robot mask.
// Pure-CSS background — no remote photos needed.
function _avatarHtml(name, tgId, isBot, sizeClass) {
  const sz = sizeClass || '';
  if (isBot || tgId === 1) {
    return `<div class="cf-ava cf-ava-bot ${sz}"><span class="cf-ava-bot-emoji">🤖</span></div>`;
  }
  const ch = (name && name.trim().length > 0) ? name.trim()[0].toUpperCase() : '?';
  // Hash tg_id → palette index
  const palette = [
    '#eb4b4b', '#f5b042', '#58e070', '#5aa9ff',
    '#b667ff', '#00e4ff', '#ff7b9c', '#ffd84a',
    '#7dd3fc', '#a78bfa',
  ];
  const idx = Math.abs((Number(tgId) || 0) % palette.length);
  const color = palette[idx];
  return `<div class="cf-ava ${sz}" style="background:${color}"><span class="cf-ava-letter">${escape(ch)}</span></div>`;
}

function _isBotId(id) { return Number(id) === 1; }

// ---------------- COINFLIP ANIMATION ----------------
function _cfRenderAnimation(area, lobby, creatorWon, spectatorMode) {
  const myId = state.me ? state.me.tg_id : null;
  const iWon = (creatorWon && lobby.creator_id === myId) || (!creatorWon && lobby.opponent_id === myId);
  const winnerName = creatorWon ? lobby.creator_name : lobby.opponent_name;
  const loserName  = creatorWon ? lobby.opponent_name : lobby.creator_name;

  const creatorAva  = _avatarHtml(lobby.creator_name,  lobby.creator_id,  _isBotId(lobby.creator_id),  'lg');
  const opponentAva = _avatarHtml(lobby.opponent_name, lobby.opponent_id, _isBotId(lobby.opponent_id), 'lg');
  // Coin faces — front = creator, back = opponent. The settle animation lands
  // on the side of whoever won, so the visible avatar IS the winner's.
  const creatorCoinAva  = _avatarHtml(lobby.creator_name,  lobby.creator_id,  _isBotId(lobby.creator_id),  'coin');
  const opponentCoinAva = _avatarHtml(lobby.opponent_name, lobby.opponent_id, _isBotId(lobby.opponent_id), 'coin');

  area.innerHTML = `
    <div class="cf-wrap cf-anim-wrap">
      <div class="cf-anim-vs">
        <div class="cf-anim-side ${creatorWon ? 'is-winner' : 'is-loser'}">
          ${creatorAva}
          <div class="cf-anim-name">${escape(lobby.creator_name || '—')}</div>
          <div class="cf-anim-value">${fmt(lobby.creator_value)} 🪙</div>
        </div>
        <div class="cf-anim-mid">
          <div class="cf-coin" id="cf-coin">
            <div class="cf-coin-face cf-coin-front">${creatorCoinAva}</div>
            <div class="cf-coin-face cf-coin-back">${opponentCoinAva}</div>
          </div>
        </div>
        <div class="cf-anim-side ${creatorWon ? 'is-loser' : 'is-winner'}">
          ${opponentAva}
          <div class="cf-anim-name">${escape(lobby.opponent_name || '—')}</div>
          <div class="cf-anim-value">${fmt(lobby.opponent_value || 0)} 🪙</div>
        </div>
      </div>
      <div class="cf-anim-status" id="cf-anim-status">Подбрасываем монету…</div>
    </div>
  `;
  const coin = document.getElementById('cf-coin');
  coin.classList.add('spinning');
  if (creatorWon) coin.classList.add('settle-front');
  else            coin.classList.add('settle-back');

  // After ~3s, replace with the result panel
  setTimeout(() => {
    const status = document.getElementById('cf-anim-status');
    if (status) {
      const verdict = spectatorMode
        ? `🏆 ${escape(winnerName || '—')} забрал${winnerName && winnerName.endsWith('а') ? 'а' : ''} ${fmt(lobby.pot_value || 0)} 🪙`
        : (iWon
            ? `🏆 ПОБЕДА! Забрал ${fmt(lobby.pot_value || 0)} 🪙`
            : `💀 ПРОИГРЫШ. ${escape(winnerName || '—')} забрал стак на ${fmt(lobby.pot_value || 0)} 🪙`);
      status.innerHTML = `<div class="cf-anim-verdict ${iWon ? 'win' : (spectatorMode ? 'neutral' : 'lose')}">${verdict}</div>
        <button class="btn cf-replay-btn" id="cf-replay">К списку лобби</button>`;
      document.getElementById('cf-replay')?.addEventListener('click', () => renderCfPvp(area));
    }
    if (!spectatorMode) {
      tg?.HapticFeedback?.notificationOccurred?.(iWon ? 'success' : 'error');
    }
  }, 3100);
}

function _cfRenderSettledView(area, lobby, isCreator) {
  const creatorWon = lobby.winner_id === lobby.creator_id;
  _cfRenderAnimation(area, lobby, creatorWon, /*spectatorMode*/ !isCreator && lobby.opponent_id !== (state.me && state.me.tg_id));
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
  // We DON'T predict crits locally — server is authoritative. Predicting crits
  // causes a 20%-ish HP rebound: when local rolls crit (×3) but server doesn't,
  // reconciliation pulls HP back up. Apply only base damage; server will reveal
  // the real crit damage on flush, which only ever pulls HP DOWN (never up).
  const baseDmg = s.effects.damage || 1;
  const damage = baseDmg;

  // Apply base damage locally (matches Math.floor on server: int(base_dmg))
  const curHp = Math.max(0, (forgeState.displayedHp ?? s.weapon.hp) - damage);
  forgeState.displayedHp = curHp;
  forgeState.state.weapon.hp = Math.max(0, s.weapon.hp - damage);
  _renderHpDisplay(curHp, s.weapon.max_hp);

  tg?.HapticFeedback?.impactOccurred?.('light');

  // Shake animation (generic — crit visuals come from the server flush)
  img.classList.remove('hit', 'crit-hit');
  void img.offsetWidth;
  img.classList.add('hit');

  // Base damage popup (crit popup appears later on flush if a crit was rolled)
  _pruneEffects(wrap);
  const popup = document.createElement('div');
  popup.className = 'dmg-popup';
  popup.textContent = `-${damage}`;
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

    // Crit feedback (server is authoritative; we don't predict crits to avoid HP rebound).
    // Show one floating CRIT popup per crit rolled in this batch + heavy haptic + crit shake.
    if (r.crits > 0 && wrap && img) {
      img.classList.remove('crit-hit');
      void img.offsetWidth;
      img.classList.add('crit-hit');
      tg?.HapticFeedback?.impactOccurred?.('heavy');
      const wrapRect = wrap.getBoundingClientRect();
      const imgRect = img.getBoundingClientRect();
      for (let i = 0; i < r.crits; i++) {
        const cp = document.createElement('div');
        cp.className = 'dmg-popup crit';
        // Damage amount shown is approximate (server total / count) — for visual punch only
        const approxCritDmg = Math.round(r.damage / Math.max(1, count));
        cp.textContent = `CRIT -${approxCritDmg}`;
        cp.style.left = (imgRect.left - wrapRect.left + imgRect.width * 0.5 + (Math.random()*70-35)) + 'px';
        cp.style.top = (imgRect.top - wrapRect.top + imgRect.height * 0.3) + 'px';
        wrap.appendChild(cp);
        setTimeout(() => cp.remove(), 700);
      }
    }

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
    // Badges next to nickname — rare flair, hover/title shows badge name + desc
    const badgesHtml = (Array.isArray(r.badges) ? r.badges : [])
      .map(b => `<span class="lb-badge rarity-${escape(b.rarity || 'rare')}" title="${escape(b.name || '')}${b.desc ? ' — ' + escape(b.desc) : ''}">${escape(b.icon || '')}</span>`)
      .join('');
    return `
      <div class="lb-row">
        <div class="lb-rank ${rankClass}">${rankStr}</div>
        <div class="lb-name">
          <span class="lb-name-text">${escape(name)}</span>
          ${badgesHtml ? `<span class="lb-badges">${badgesHtml}</span>` : ''}
        </div>
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

// Cinematic badge unlock overlay — used when a rare badge is granted (e.g. RIP kill).
// Self-dismissing after 5s, also closeable via tap.
function _showBadgeUnlockOverlay(badge) {
  if (!badge || !badge.icon) return;
  let overlay = document.getElementById('badge-unlock-overlay');
  if (!overlay) {
    overlay = document.createElement('div');
    overlay.id = 'badge-unlock-overlay';
    overlay.className = 'badge-unlock-overlay';
    document.body.appendChild(overlay);
  }
  overlay.innerHTML = `
    <div class="bu-modal rarity-${escape(badge.rarity || 'rare')}">
      <div class="bu-burst"></div>
      <div class="bu-eyebrow">★ MYTHIC UNLOCK ★</div>
      <div class="bu-icon">${escape(badge.icon)}</div>
      <div class="bu-name">${escape(badge.name || 'Награда')}</div>
      <div class="bu-desc">${escape(badge.desc || '')}</div>
      <div class="bu-hint">Тап чтобы закрыть</div>
    </div>
  `;
  overlay.classList.add('shown');
  const close = () => {
    overlay.classList.remove('shown');
    setTimeout(() => { try { overlay.remove(); } catch (_) {} }, 350);
  };
  overlay.onclick = close;
  setTimeout(close, 5500);
}

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
  const isEndless = st.selected_tier > 11;
  const isHero    = !!st.is_hero;
  const heroImg   = st.image_url || null;

  // Boss tier picker (carousel of unlocked bosses)
  const tiersHtml = (st.tiers || []).map(t => {
    const tierHpPct = Math.max(0, (t.hp / t.max_hp) * 100);
    const onCd = (t.cooldown_left || 0) > 0;
    const iconHtml = t.is_hero && t.image_url
      ? `<div class="btc-icon btc-hero-thumb"><img src="${t.image_url}" alt="" /></div>`
      : `<div class="btc-icon">${t.icon}</div>`;
    return `
      <button class="boss-tier-card ${t.selected ? 'active' : ''} ${onCd ? 'cooldown' : ''} ${t.is_hero ? 'hero' : ''}" data-tier="${t.tier}">
        ${iconHtml}
        <div class="btc-tier">T${t.tier}${t.is_hero ? ' 👑' : ''}</div>
        <div class="btc-hp-bar"><div class="btc-hp-fill" style="width:${tierHpPct}%"></div></div>
        <div class="btc-kills">${onCd ? '💤 ' + _fmtCooldown(t.cooldown_left) : t.kills + '× kills'}</div>
      </button>
    `;
  }).join('');

  // Tap target — hero bosses (t11) use the full image, others use the emoji icon
  const tapTargetInner = isHero && heroImg
    ? `
      <img class="boss-hero-img" id="boss-hero-img" src="${heroImg}" alt="${escape(st.name)}" draggable="false" />
      <div class="boss-hero-glow"></div>
      <div class="boss-tap-hint hero" id="boss-tap-hint">${st.cooldown_seconds_left > 0 ? '💤 СПИТ' : 'TAP TO KILL'}</div>
    `
    : `
      <div class="boss-icon-big" id="boss-icon-big">${st.icon}</div>
      <div class="boss-tap-hint" id="boss-tap-hint">${st.cooldown_seconds_left > 0 ? '💤 спит' : 'тапай'}</div>
    `;

  root.innerHTML = `
    <div class="boss-tier-picker" id="boss-tier-picker">${tiersHtml}</div>

    <div class="boss-fight-card ${isHero ? 'hero-mode' : ''}">
      <div class="boss-tier-label">${isHero ? '👑 ФИНАЛЬНЫЙ БОСС · ' : ''}Тир ${st.selected_tier}${isEndless ? ' · ENDLESS' : ''}</div>
      <div class="boss-name ${isHero ? 'hero' : ''}">${escape(st.name)}</div>
      <div class="boss-lore">${escape(st.lore)}</div>

      <div class="boss-tap-target ${st.cooldown_seconds_left > 0 ? 'cooldown' : ''} ${isHero ? 'hero' : ''}" id="boss-tap-target">
        ${tapTargetInner}
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
    let curStr, nextStr;
    if (b.key === 'boss_megahit') {
      // Special: effect is "every Nth tap deals ×10". Server formula: max(15, 25 - lvl).
      const curInterval  = lvl > 0 ? Math.max(15, 25 - lvl) : null;
      const nextInterval = Math.max(15, 25 - (lvl + 1));
      curStr  = lvl === 0 ? 'выкл' : `каждый ${curInterval}-й ×10`;
      nextStr = isMaxed ? '—' : `каждый ${nextInterval}-й ×10`;
    } else {
      const cur  = (lvl * b.effect_per_level).toFixed(b.effect_per_level < 1 ? 2 : 0);
      const next = isMaxed ? '—' : ((lvl + 1) * b.effect_per_level).toFixed(b.effect_per_level < 1 ? 2 : 0);
      const unit = b.unit.startsWith('%') ? b.unit : (' ' + b.unit);
      curStr  = `${cur}${unit}`;
      nextStr = isMaxed ? '—' : `${next}${unit}`;
    }
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
          ? `<div class="bb-effect maxed">МАКС: ${curStr}</div>`
          : `<div class="bb-effect">Сейчас: ${curStr} → ${nextStr}</div>
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

  // Visual: shake whichever target is currently rendered (emoji icon or hero image)
  const target = document.getElementById('boss-icon-big') || document.getElementById('boss-hero-img');
  if (target) {
    target.classList.remove('hit');
    void target.offsetWidth;
    target.classList.add('hit');
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

    // Always-on procs feedback (megahit/double/crit) — visible even on non-kill grinding.
    if (r.megahits > 0) {
      toast(`💥 ×${r.megahits} МЕГА-УДАР!`, 2200);
      tg?.HapticFeedback?.notificationOccurred?.('success');
    } else if (r.doubles > 0) {
      toast(`⚡ ×${r.doubles} двойной удар`, 1600);
    } else if (r.crits > 0 && r.crits >= 3) {
      toast(`🎯 ×${r.crits} крита`, 1400);
    }

    if (r.kills && r.kills.length > 0) {
      tg?.HapticFeedback?.notificationOccurred?.('success');
      for (const k of r.kills) {
        toast(`☠ ${k.icon} ${k.name} убит! +${fmt(k.coin_reward)} 🪙`, 3500);
      }
      if (r.tier_unlocked) {
        toast(`🔓 Открыт новый тир: ${r.tier_unlocked}`, 4000);
      }
      // Cinematic badge unlock — fullscreen overlay with the rare flair
      if (Array.isArray(r.badges_unlocked) && r.badges_unlocked.length > 0) {
        for (const badge of r.badges_unlocked) {
          _showBadgeUnlockOverlay(badge);
          tg?.HapticFeedback?.notificationOccurred?.('success');
        }
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

let _megaslotState = {
  busy: false,
  bet: 100,
  configLoaded: false,
  turbo: false,
  autoCount: 0,        // remaining auto spins (0 = no auto active)
  autoTotal: 0,        // total spins in current auto session (for X / Y display)
  autoStopRequested: false,
};

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
        <div class="ms-toggles-row">
          <button class="ms-toggle-btn ${_megaslotState.turbo ? 'active' : ''}" id="ms-turbo" title="Быстрая прокрутка">
            ⚡ ТУРБО
          </button>
          <button class="ms-toggle-btn" id="ms-auto" title="Авто-крутки подряд">
            🔁 АВТО
          </button>
          <div class="ms-auto-counter" id="ms-auto-counter" style="display:none">
            <span id="ms-auto-progress">0 / 0</span>
            <button class="ms-auto-stop" id="ms-auto-stop">✕ STOP</button>
          </div>
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
  document.getElementById('ms-turbo').addEventListener('click', () => {
    if (_megaslotState.busy) return;
    _megaslotState.turbo = !_megaslotState.turbo;
    const btn = document.getElementById('ms-turbo');
    if (btn) btn.classList.toggle('active', _megaslotState.turbo);
    tg?.HapticFeedback?.selectionChanged?.();
  });
  document.getElementById('ms-auto').addEventListener('click', _openAutoSpinModal);
  document.getElementById('ms-auto-stop').addEventListener('click', () => {
    _megaslotState.autoStopRequested = true;
    toast('Остановим после текущей крутки');
  });
}

function _openAutoSpinModal() {
  if (_megaslotState.busy) return;
  if (_megaslotState.autoCount > 0) {
    // Already running — STOP button is active. Click the auto button = stop.
    _megaslotState.autoStopRequested = true;
    toast('Остановим после текущей крутки');
    return;
  }
  const el = document.createElement('div');
  el.className = 'ms-bonus-modal';
  el.innerHTML = `
    <div class="ms-bonus-backdrop"></div>
    <div class="ms-bonus-card">
      <div class="ms-bonus-title">🔁 Авто-крутки</div>
      <div class="ms-auto-grid">
        <button class="ms-auto-opt" data-n="10">10</button>
        <button class="ms-auto-opt" data-n="25">25</button>
        <button class="ms-auto-opt" data-n="50">50</button>
        <button class="ms-auto-opt" data-n="100">100</button>
        <button class="ms-auto-opt" data-n="250">250</button>
        <button class="ms-auto-opt" data-n="9999">∞ (до баланса)</button>
      </div>
      <div class="ms-auto-hint">Будет крутиться по текущей ставке. Кнопкой STOP остановишь после текущей.</div>
      <button class="ms-bonus-cancel">Отмена</button>
    </div>
  `;
  document.body.appendChild(el);
  const close = () => el.remove();
  el.querySelector('.ms-bonus-backdrop').addEventListener('click', close);
  el.querySelector('.ms-bonus-cancel').addEventListener('click', close);
  el.querySelectorAll('.ms-auto-opt').forEach(btn => {
    btn.addEventListener('click', () => {
      const n = parseInt(btn.dataset.n);
      close();
      _startAutoSpin(n);
    });
  });
}

async function _startAutoSpin(count) {
  if (_megaslotState.autoCount > 0) return;
  _megaslotState.autoCount = count;
  _megaslotState.autoTotal = count;
  _megaslotState.autoStopRequested = false;
  _updateAutoSpinUi();

  try {
    while (_megaslotState.autoCount > 0 && !_megaslotState.autoStopRequested) {
      // Bail out if balance dipped below bet (or game view changed)
      if (!state.me || state.me.balance < _megaslotState.bet) {
        toast('Не хватает на следующую крутку');
        break;
      }
      if (!document.getElementById('ms-grid')) break; // user navigated away
      _megaslotState.autoCount -= 1;
      _updateAutoSpinUi();
      await playMegaslot(false, null);
      if (_megaslotState.autoCount > 0 && !_megaslotState.autoStopRequested) {
        // brief pause between spins so the result is readable
        await _sleep(_megaslotState.turbo ? 250 : 600);
      }
    }
  } finally {
    _megaslotState.autoCount = 0;
    _megaslotState.autoTotal = 0;
    _megaslotState.autoStopRequested = false;
    _updateAutoSpinUi();
  }
}

function _updateAutoSpinUi() {
  const counter  = document.getElementById('ms-auto-counter');
  const progress = document.getElementById('ms-auto-progress');
  const autoBtn  = document.getElementById('ms-auto');
  const spinBtn  = document.getElementById('ms-spin');
  if (!counter || !autoBtn) return;
  if (_megaslotState.autoCount > 0) {
    counter.style.display = 'flex';
    if (progress) {
      const done = _megaslotState.autoTotal - _megaslotState.autoCount;
      progress.textContent = `${done} / ${_megaslotState.autoTotal === 9999 ? '∞' : _megaslotState.autoTotal}`;
    }
    autoBtn.classList.add('active');
    if (spinBtn) spinBtn.disabled = true;
  } else {
    counter.style.display = 'none';
    autoBtn.classList.remove('active');
    if (spinBtn) spinBtn.disabled = false;
  }
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
  if (!spinData) return;
  const turbo = !!_megaslotState.turbo;
  // In turbo mode, slash all timings ~3×
  if (turbo) { spinMs = Math.max(280, Math.round(spinMs * 0.35)); colDelay = Math.max(35, Math.round(colDelay * 0.35)); }

  const tumbles = spinData.tumbles || [];
  // Use the actual server-provided final_grid if available; fall back to first
  // tumble's grid only if final_grid isn't there (legacy/edge cases).
  let finalGrid = spinData.final_grid || (tumbles.length > 0 ? tumbles[0].grid : null);
  // Last-resort: render a random preview so we never silently skip animation
  if (!finalGrid) finalGrid = _randomGrid();

  // Always run the spin reel animation (even on pure loss — no symbol matches)
  await _spinAnimation(finalGrid, spinMs, colDelay);

  // No tumbles at all → result was determined on first roll without wins.
  // Stopped grid is already rendered by _spinAnimation; small "no win" pulse for feedback.
  if (tumbles.length === 0) {
    _renderMegaslotGrid(finalGrid);
    const gridEl = document.getElementById('ms-grid');
    if (gridEl) {
      gridEl.classList.add('no-win-flash');
      setTimeout(() => gridEl.classList.remove('no-win-flash'), 400);
    }
    return;
  }

  const tumbleWait1 = turbo ? 180 : 500;
  const tumbleWait2 = turbo ? 110 : 280;
  const tumbleWait3 = turbo ? 120 : 300;
  const orbOnlyWait = turbo ? 220 : 600;

  for (let i = 0; i < tumbles.length; i++) {
    const t = tumbles[i];
    const winSyms = new Set((t.wins || []).map(w => w.symbol));
    _renderMegaslotGrid(t.grid, { orbs: t.orbs, winningSymbols: winSyms });

    if (t.wins && t.wins.length > 0) {
      await _sleep(tumbleWait1);
      document.querySelectorAll('.ms-cell.winning').forEach(el => el.classList.add('exploding'));
      await _sleep(tumbleWait2);
      if (t.post_grid) {
        _renderMegaslotGrid(t.post_grid);
        const gridEl = document.getElementById('ms-grid');
        if (gridEl) {
          gridEl.querySelectorAll('.ms-cell').forEach(el => el.classList.add('tumble-in'));
        }
        await _sleep(tumbleWait3);
      }
    } else if (t.orbs && t.orbs.length > 0) {
      await _sleep(orbOnlyWait);
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
        await _sleep(_megaslotState.turbo ? 220 : 600);
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
    await _sleep(_megaslotState.turbo ? 280 : 800);
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
    // While auto-spin is running, keep the manual spin button disabled — the
    // auto loop will trigger the next call. Only release it when auto is idle.
    if (spinBtn) spinBtn.disabled = (_megaslotState.autoCount > 0);
    if (buyBtn) buyBtn.disabled = false;
  }
}

// ============================================================
// CASINO TYCOON — build-your-own-casino
// ============================================================
//
// Architecture:
//   Server is authoritative for state, chips/cash/coins, unit ownership,
//   bot effects and AFK ticking. The frontend is a "live show" on top:
//   it polls /tycoon/state every 5s, derives `chips_per_sec` per unit,
//   spawns visual visitors, and animates chip particles on collect.
//
// Visitors are PURELY visual — the server doesn't simulate individual NPCs.
// We use occupancy_pct per unit to decide visitor spawn frequency, so what
// you see on screen reflects the real economy.
//
// DOM layout: each unit + visitor is its own absolutely-positioned div
// inside the .tycoon-floor container. CSS transforms drive movement.
// Cell positions are converted to pixel coords via _tcCellPixel().

const tycoonState = {
  area: null,
  data: null,            // server response
  pollTimer: null,
  visitorTimer: null,
  visitorSeq: 0,
  liveVisitors: [],      // {el, kind, targetCell, busyUntil, despawnAt}
  chipDriftRaf: null,
  selectedShopKey: null, // when buying: which unit key is queued for placement
  panelOpen: 'shop',     // shop | bots | bank | decor | null
};

const TC_GRID_W = 4;     // visual cols (we render up to 4×3 = 12 cells, 4×6=24 endgame)
const TC_CELL_W = 86;    // base cell width in px (isometric base)
const TC_CELL_H = 60;    // base cell height (isometric depth)

// ---- VISITOR archetypes ----
const TC_VISITOR_TYPES = [
  // weight = relative spawn chance per reputation level
  { key: 'homeless', color: '#7a7a7a', accent: '#444', size: 1.0, weights: [60, 40, 20, 5, 0]   },
  { key: 'student',  color: '#5aa9ff', accent: '#1f5396', size: 1.0, weights: [25, 35, 30, 15, 5]  },
  { key: 'office',   color: '#444',    accent: '#bcaa66', size: 1.05, weights: [10, 20, 30, 35, 25] },
  { key: 'highroll', color: '#b667ff', accent: '#3a0d63', size: 1.1, weights: [3,  4,  15, 30, 35] },
  { key: 'whale',    color: '#f5b042', accent: '#7a4500', size: 1.15, weights: [1,  1,  4,  12, 30] },
  { key: 'celeb',    color: '#ffd984', accent: '#eb4b4b', size: 1.2, weights: [0,  0,  1,  3,  5]  },
];

// ---- ENTRY POINT ----
async function renderTycoon(area) {
  tycoonState.area = area;
  area.innerHTML = `<div class="tycoon-wrap"><div class="tc-loader-warm">
    <div class="tc-loader-emoji">🎰</div>
    <div>Запускаем казино…</div>
    <div class="tc-loader-sub">Render free tier бывает медленный — может занять до 30 сек на старте</div>
  </div></div>`;
  // Try up to 3 times if the first call hits a cold-start network error
  let lastErr = null;
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      tycoonState.data = await api('/api/tycoon/state');
      _tcPaintAll();
      _tcStartPolling();
      _tcStartVisitorLoop();
      return;
    } catch (e) {
      lastErr = e;
      const msg = String(e?.message || e);
      const isNet = msg.includes('Сеть лагает') || msg === 'Load failed' || msg === 'Failed to fetch';
      if (!isNet) break;  // non-network error — don't retry, show it
      area.innerHTML = `<div class="tycoon-wrap"><div class="tc-loader-warm">
        <div class="tc-loader-emoji">⏳</div>
        <div>Будим сервер... (попытка ${attempt + 2}/3)</div>
        <div class="tc-loader-sub">${escape(msg)}</div>
      </div></div>`;
      await new Promise(r => setTimeout(r, 1500 * (attempt + 1)));
    }
  }
  area.innerHTML = `
    <div class="tycoon-wrap">
      <div class="tc-loader-warm">
        <div class="tc-loader-emoji">⚠️</div>
        <div>Ошибка: ${escape(lastErr?.message || lastErr || 'unknown')}</div>
        <button class="btn" id="tc-retry-btn" style="margin-top:12px">Попробовать ещё раз</button>
      </div>
    </div>
  `;
  document.getElementById('tc-retry-btn')?.addEventListener('click', () => renderTycoon(area));
}

function _tcStopAll() {
  if (tycoonState.pollTimer) { clearInterval(tycoonState.pollTimer); tycoonState.pollTimer = null; }
  if (tycoonState.visitorTimer) { clearInterval(tycoonState.visitorTimer); tycoonState.visitorTimer = null; }
  if (tycoonState.chipDriftRaf) { cancelAnimationFrame(tycoonState.chipDriftRaf); tycoonState.chipDriftRaf = null; }
  tycoonState.liveVisitors.forEach(v => { try { v.el.remove(); } catch (_) {} });
  tycoonState.liveVisitors = [];
}

function _tcStartPolling() {
  if (tycoonState.pollTimer) clearInterval(tycoonState.pollTimer);
  tycoonState.pollTimer = setInterval(async () => {
    if (!document.querySelector('.tycoon-wrap')) { _tcStopAll(); return; }
    try {
      const fresh = await api('/api/tycoon/state');
      tycoonState.data = fresh;
      _tcRefreshHud();
      _tcRefreshUnits();
    } catch (_) {}
  }, 5000);
}

function _tcStartVisitorLoop() {
  if (tycoonState.visitorTimer) clearInterval(tycoonState.visitorTimer);
  tycoonState.visitorTimer = setInterval(() => {
    if (!document.querySelector('.tycoon-wrap')) { _tcStopAll(); return; }
    _tcMaybeSpawnVisitor();
    _tcCleanupDespawned();
  }, 1500);
}

// ---- LAYOUT ----
function _tcGridDims(capacity) {
  // Layout: prefer wider over deeper, max 6 wide
  let cols = Math.min(6, Math.max(3, Math.ceil(Math.sqrt(capacity * 1.5))));
  let rows = Math.ceil(capacity / cols);
  return { cols, rows };
}

function _tcCellPixel(cx, cy, cols) {
  // Plain grid (not strict isometric) so units stay readable on mobile
  const x = cx * TC_CELL_W + TC_CELL_W * 0.1;
  const y = cy * (TC_CELL_H + 14) + TC_CELL_H * 0.2;
  return { x, y };
}

// ---- MAIN PAINT ----
function _tcPaintAll() {
  const d = tycoonState.data;
  const area = tycoonState.area;
  if (!d || !area) return;
  const { cols, rows } = _tcGridDims(d.floor_capacity);
  const floorWidthPx = cols * TC_CELL_W + 16;
  const floorHeightPx = rows * (TC_CELL_H + 14) + 40;

  area.innerHTML = `
    <div class="tycoon-wrap">
      <div class="tc-hud" id="tc-hud">${_tcHudHtml(d)}</div>

      <div class="tc-stage theme-${d.theme || 'vegas'} ${(d.streak && d.streak.kind!=='neutral') ? 'streak-'+d.streak.kind : ''} ${(d.celeb && d.celeb.active) ? 'celeb-active' : ''}" id="tc-stage" style="height:${floorHeightPx + 70}px">
        ${(d.streak && d.streak.kind === 'hot') ? `<div class="tc-streak-banner hot">🔥 HOT STREAK · +25% дохода · ${d.streak.seconds_left}с</div>` : ''}
        ${(d.streak && d.streak.kind === 'cold') ? `<div class="tc-streak-banner cold">❄️ COLD STREAK · −25% дохода · ${d.streak.seconds_left}с</div>` : ''}
        ${(d.celeb && d.celeb.active) ? `<div class="tc-celeb-banner">🌟 ${escape(d.celeb.name)} в казино! +50% · ${Math.floor(d.celeb.seconds_left/60)}мин</div>` : ''}
        <div class="tc-bg-sky"></div>
        <div class="tc-door" id="tc-door">
          <div class="tc-door-frame"></div>
          <div class="tc-door-light"></div>
          <div class="tc-door-label">↘ ENTRY</div>
        </div>
        <div class="tc-floor" id="tc-floor" style="width:${floorWidthPx}px; height:${floorHeightPx}px">
          ${_tcFloorTilesHtml(cols, rows, d.floor_capacity)}
          ${(d.units || []).map(u => _tcUnitHtml(u, cols)).join('')}
        </div>
        <div class="tc-cashier" id="tc-cashier">${_tcCashierSvg()}<div class="tc-label-tag">КАССА</div></div>
        ${d.floor_capacity < d.max_floor ? `
          <button class="tc-buy-cell" id="tc-buy-cell">＋ ячейка <b>${fmt(d.next_cell_cost)} $</b></button>
        ` : ''}
      </div>

      <div class="tc-toolbar">
        <button class="tc-tool-btn ${tycoonState.panelOpen==='shop' ? 'active' : ''}" data-panel="shop">🛒 Магазин</button>
        <button class="tc-tool-btn ${tycoonState.panelOpen==='bots' ? 'active' : ''}" data-panel="bots">🤖 Боты</button>
        <button class="tc-tool-btn ${tycoonState.panelOpen==='bank' ? 'active' : ''}" data-panel="bank">🏦 Банк</button>
        <button class="tc-tool-btn ${tycoonState.panelOpen==='decor' ? 'active' : ''}" data-panel="decor">💎 Декор</button>
        <button class="tc-tool-btn ${tycoonState.panelOpen==='themes' ? 'active' : ''}" data-panel="themes">🎨 Темы</button>
        <button class="tc-tool-btn ${tycoonState.panelOpen==='missions' ? 'active' : ''}" data-panel="missions">📅 Квесты</button>
        <button class="tc-tool-btn ${tycoonState.panelOpen==='raids' ? 'active' : ''}" data-panel="raids">⚔️ Налёты</button>
        <button class="tc-tool-btn ${tycoonState.panelOpen==='news' ? 'active' : ''}" data-panel="news">📰 Лента</button>
      </div>

      <div class="tc-panel" id="tc-panel">${_tcPanelHtml(d, tycoonState.panelOpen)}</div>
    </div>
  `;

  _tcWireEvents();
}

function _tcHudHtml(d) {
  const stars = '⭐'.repeat(Math.floor(d.reputation)) + '☆'.repeat(5 - Math.floor(d.reputation));
  return `
    <div class="tc-hud-row">
      <div class="tc-hud-cur">
        <span class="tc-hud-icon">🎲</span>
        <span class="tc-hud-val" id="tc-chips">${fmt(d.chips)}</span>
        <span class="tc-hud-lbl">чипов</span>
      </div>
      <div class="tc-hud-cur">
        <span class="tc-hud-icon">💵</span>
        <span class="tc-hud-val" id="tc-cash">${fmt(d.cash)}</span>
        <span class="tc-hud-lbl">$</span>
      </div>
      <div class="tc-hud-cur tc-hud-stars" title="Репутация">
        <span>${stars}</span>
        <span class="tc-hud-lbl">${d.reputation.toFixed(1)}</span>
      </div>
    </div>
    ${d.vip_stars > 0 ? `<div class="tc-prestige-row">⭐ VIP Stars: <b>${d.vip_stars}</b> · престиж #${d.prestige_count}</div>` : ''}
    <button class="tc-collect-all" id="tc-collect-all">⛁ собрать со всех</button>
  `;
}

function _tcFloorTilesHtml(cols, rows, capacity) {
  let h = '';
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      const idx = r * cols + c;
      const locked = idx >= capacity;
      const p = _tcCellPixel(c, r, cols);
      h += `<div class="tc-tile ${locked ? 'locked' : ''}" data-cx="${c}" data-cy="${r}"
              style="left:${p.x}px; top:${p.y}px; width:${TC_CELL_W - 8}px; height:${TC_CELL_H}px"></div>`;
    }
  }
  return h;
}

function _tcUnitHtml(u, cols) {
  const p = _tcCellPixel(u.cell_x, u.cell_y, cols);
  const tray = u.chips_in_tray;
  return `
    <div class="tc-unit kind-${u.kind} tier-${u.tier}" data-uid="${u.id}"
         style="left:${p.x + 4}px; top:${p.y - 12}px; width:${TC_CELL_W - 16}px; height:${TC_CELL_H + 22}px">
      ${_tcUnitSpriteSvg(u)}
      ${tray > 0 ? `<div class="tc-tray glow"><span class="tc-tray-icon">🎲</span><span class="tc-tray-val">${fmt(tray)}</span></div>` : ''}
      <div class="tc-occ-bar"><div class="tc-occ-fill" style="width:${u.occupancy_pct||0}%"></div></div>
    </div>
  `;
}

// ---- SVG SPRITES (rich, NOT just emoji) ----
function _tcUnitSpriteSvg(u) {
  if (u.kind === 'slot')   return _tcSlotSvg(u);
  if (u.kind === 'table')  return _tcTableSvg(u);
  if (u.kind === 'amenity') return _tcAmenitySvg(u);
  return `<div style="font-size:32px">${u.icon}</div>`;
}

function _tcSlotSvg(u) {
  // Tier-based color
  const tierColors = {
    1: ['#5a6075', '#2a2f3d', '#1a1d28'],
    2: ['#eb4b4b', '#8c1818', '#440505'],
    3: ['#b07028', '#5a3a08', '#2a1c04'],
    4: ['#b667ff', '#3a0d63', '#220838'],
    5: ['#f5b042', '#a85e08', '#5a3204'],
  };
  const [c1, c2, c3] = tierColors[u.tier] || tierColors[1];
  return `
    <svg class="tc-unit-svg" viewBox="0 0 56 70" preserveAspectRatio="xMidYMid meet">
      <defs>
        <linearGradient id="slotGrad${u.id}" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="${c1}"/>
          <stop offset="60%" stop-color="${c2}"/>
          <stop offset="100%" stop-color="${c3}"/>
        </linearGradient>
        <linearGradient id="slotScreen${u.id}" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="#0a0c14"/>
          <stop offset="100%" stop-color="#1a1f2c"/>
        </linearGradient>
      </defs>
      <!-- Cabinet shadow -->
      <ellipse cx="28" cy="68" rx="22" ry="3" fill="rgba(0,0,0,0.5)"/>
      <!-- Cabinet body -->
      <rect x="8" y="12" width="40" height="54" rx="4" fill="url(#slotGrad${u.id})" stroke="#0a0c14" stroke-width="1"/>
      <!-- Top crown -->
      <rect x="6" y="6" width="44" height="10" rx="3" fill="${c1}" stroke="#0a0c14" stroke-width="0.8"/>
      <circle cx="13" cy="11" r="2" class="tc-slot-light" fill="#ffd984"/>
      <circle cx="28" cy="11" r="2" class="tc-slot-light" fill="#ff6b6b" style="animation-delay:0.3s"/>
      <circle cx="43" cy="11" r="2" class="tc-slot-light" fill="#5aa9ff" style="animation-delay:0.6s"/>
      <!-- Screen with reels -->
      <rect x="11" y="20" width="34" height="22" rx="2" fill="url(#slotScreen${u.id})" stroke="#0a0c14"/>
      <rect x="13" y="22" width="9" height="18" fill="#1a1f2c" class="tc-reel"/>
      <rect x="23.5" y="22" width="9" height="18" fill="#1a1f2c" class="tc-reel" style="animation-delay:0.15s"/>
      <rect x="34" y="22" width="9" height="18" fill="#1a1f2c" class="tc-reel" style="animation-delay:0.3s"/>
      <text x="17.5" y="34" text-anchor="middle" font-size="8" fill="#f5b042" font-weight="900">7</text>
      <text x="28" y="34" text-anchor="middle" font-size="8" fill="#f5b042" font-weight="900">7</text>
      <text x="38.5" y="34" text-anchor="middle" font-size="8" fill="#f5b042" font-weight="900">7</text>
      <!-- Coin slot + button -->
      <rect x="22" y="46" width="12" height="2" fill="#0a0c14"/>
      <circle cx="28" cy="55" r="4" fill="#eb4b4b" stroke="#000" stroke-width="0.5"/>
      <text x="28" y="57" text-anchor="middle" font-size="3" fill="#fff" font-weight="900">SPIN</text>
      <!-- Lever on side -->
      <rect x="48" y="22" width="2" height="18" fill="#888"/>
      <circle cx="49" cy="20" r="3" fill="#eb4b4b" stroke="#000"/>
    </svg>
  `;
}

function _tcTableSvg(u) {
  const tierColors = {
    2: '#1f7a3a', 3: '#28a149', 4: '#0a5530', 5: '#7a1818',
  };
  const felt = tierColors[u.tier] || '#1f7a3a';
  return `
    <svg class="tc-unit-svg" viewBox="0 0 60 70" preserveAspectRatio="xMidYMid meet">
      <defs>
        <radialGradient id="tableFelt${u.id}" cx="0.5" cy="0.45" r="0.6">
          <stop offset="0%" stop-color="${felt}" stop-opacity="1"/>
          <stop offset="100%" stop-color="#0a0c14" stop-opacity="1"/>
        </radialGradient>
      </defs>
      <ellipse cx="30" cy="68" rx="26" ry="3" fill="rgba(0,0,0,0.5)"/>
      <!-- Table top (oval) -->
      <ellipse cx="30" cy="40" rx="26" ry="20" fill="url(#tableFelt${u.id})" stroke="#3a2810" stroke-width="2"/>
      <ellipse cx="30" cy="38" rx="22" ry="16" fill="${felt}" opacity="0.3"/>
      <!-- Pattern on felt -->
      <text x="30" y="42" text-anchor="middle" font-size="14" fill="#f5b042" font-weight="900" opacity="0.6">${u.icon}</text>
      <!-- Chip stacks -->
      <ellipse cx="14" cy="34" rx="3" ry="1.2" fill="#eb4b4b" stroke="#000" stroke-width="0.4"/>
      <ellipse cx="14" cy="32" rx="3" ry="1.2" fill="#eb4b4b" stroke="#000" stroke-width="0.4"/>
      <ellipse cx="46" cy="34" rx="3" ry="1.2" fill="#5aa9ff" stroke="#000" stroke-width="0.4"/>
      <ellipse cx="46" cy="32" rx="3" ry="1.2" fill="#5aa9ff" stroke="#000" stroke-width="0.4"/>
      <!-- Dealer position -->
      <circle cx="30" cy="22" r="4" fill="#bcaa66" stroke="#000"/>
      <rect x="27" y="24" width="6" height="4" fill="#1a1f2c"/>
    </svg>
  `;
}

function _tcAmenitySvg(u) {
  const palette = { amen_bar: '#ffd984', amen_atm: '#5aa9ff', amen_vip: '#b667ff' };
  const c = palette[u.key] || '#888';
  return `
    <svg class="tc-unit-svg" viewBox="0 0 56 70" preserveAspectRatio="xMidYMid meet">
      <ellipse cx="28" cy="68" rx="22" ry="3" fill="rgba(0,0,0,0.5)"/>
      <rect x="10" y="20" width="36" height="44" rx="3" fill="${c}" opacity="0.3" stroke="${c}" stroke-width="2"/>
      <text x="28" y="50" text-anchor="middle" font-size="22">${u.icon}</text>
    </svg>
  `;
}

function _tcCashierSvg() {
  return `
    <svg viewBox="0 0 100 100" width="60" height="60">
      <defs>
        <linearGradient id="cashierGrad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="#f5b042"/>
          <stop offset="100%" stop-color="#5a3a08"/>
        </linearGradient>
      </defs>
      <rect x="15" y="20" width="70" height="60" rx="4" fill="url(#cashierGrad)" stroke="#0a0c14" stroke-width="2"/>
      <rect x="25" y="30" width="50" height="20" rx="2" fill="#0a0c14" stroke="#fff" stroke-width="0.5"/>
      <text x="50" y="44" text-anchor="middle" font-size="12" fill="#58e070" font-weight="900">$ $ $</text>
      <rect x="30" y="58" width="12" height="14" fill="#1a1f2c"/>
      <rect x="58" y="58" width="12" height="14" fill="#1a1f2c"/>
      <text x="36" y="68" text-anchor="middle" font-size="9" fill="#fff" font-weight="900">$</text>
      <text x="64" y="68" text-anchor="middle" font-size="9" fill="#fff" font-weight="900">€</text>
    </svg>
  `;
}

function _tcVisitorSvg(type) {
  const t = TC_VISITOR_TYPES.find(x => x.key === type) || TC_VISITOR_TYPES[0];
  return `
    <svg viewBox="0 0 24 36" width="22" height="34" class="tc-visitor-svg">
      <ellipse cx="12" cy="34" rx="6" ry="1.5" fill="rgba(0,0,0,0.4)"/>
      <!-- Body -->
      <rect x="6" y="14" width="12" height="14" rx="2" fill="${t.color}" stroke="#0a0c14" stroke-width="0.6"/>
      <!-- Arms -->
      <rect x="3" y="15" width="3" height="10" rx="1" fill="${t.color}"/>
      <rect x="18" y="15" width="3" height="10" rx="1" fill="${t.color}"/>
      <!-- Head -->
      <circle cx="12" cy="9" r="5" fill="#fbd5b5" stroke="#0a0c14" stroke-width="0.6"/>
      <!-- Hair / hat -->
      ${t.key === 'celeb' ? '<rect x="6" y="3" width="12" height="3" fill="#ffd984"/>' :
        t.key === 'whale' ? '<path d="M6 6 Q12 1 18 6 L18 9 L6 9 Z" fill="#222"/>' :
        '<path d="M7 5 Q12 2 17 5 L17 8 L7 8 Z" fill="' + t.accent + '"/>'}
      <!-- Legs -->
      <rect x="8" y="27" width="3" height="8" rx="0.8" fill="#1a1f2c"/>
      <rect x="13" y="27" width="3" height="8" rx="0.8" fill="#1a1f2c"/>
      ${t.key === 'whale' || t.key === 'celeb' ? '<text x="12" y="22" text-anchor="middle" font-size="3" fill="#ffd984" font-weight="900">$</text>' : ''}
    </svg>
  `;
}

// ---- VISITORS LOOP ----
function _tcMaybeSpawnVisitor() {
  const d = tycoonState.data;
  if (!d) return;
  // Spawn rate scales with reputation
  const baseRate = 0.6 + d.reputation * 0.35;  // chance per tick
  if (Math.random() > baseRate) return;
  // Cap simultaneous visitors so the floor doesn't get too busy
  if (tycoonState.liveVisitors.length >= 10) return;
  // Pick a unit (slot/table) that's not "full" with visitors
  const candidates = (d.units || []).filter(u => u.kind === 'slot' || u.kind === 'table');
  if (candidates.length === 0) return;
  // Filter out units already visited by max capacity visitors
  const target = candidates[Math.floor(Math.random() * candidates.length)];
  // Pick visitor type based on reputation (5 weight buckets)
  const repBucket = Math.min(4, Math.max(0, Math.floor(d.reputation - 1)));
  const vtypes = TC_VISITOR_TYPES.filter(t => (t.weights[repBucket] || 0) > 0);
  if (vtypes.length === 0) return;
  const w = vtypes.map(t => t.weights[repBucket]);
  const sum = w.reduce((a, b) => a + b, 0);
  let r = Math.random() * sum;
  let pick = vtypes[0];
  for (let i = 0; i < vtypes.length; i++) { r -= w[i]; if (r <= 0) { pick = vtypes[i]; break; } }
  _tcSpawnVisitor(pick.key, target);
}

function _tcSpawnVisitor(typeKey, target) {
  const floor = document.getElementById('tc-floor');
  const door = document.getElementById('tc-door');
  if (!floor || !door) return;
  const { cols } = _tcGridDims(tycoonState.data.floor_capacity);

  // Spawn position = right edge of stage (door)
  const startX = floor.offsetWidth + 30;
  const startY = floor.offsetHeight - 40 + Math.random() * 20;
  const target_p = _tcCellPixel(target.cell_x, target.cell_y, cols);
  const targetX = target_p.x + (TC_CELL_W - 16) / 2;
  const targetY = target_p.y + 20;

  const id = ++tycoonState.visitorSeq;
  const wrap = document.createElement('div');
  wrap.className = 'tc-visitor';
  wrap.dataset.id = String(id);
  wrap.style.left = startX + 'px';
  wrap.style.top  = startY + 'px';
  wrap.innerHTML = _tcVisitorSvg(typeKey);
  floor.appendChild(wrap);

  // Animate in: walk to target via two-phase translation (horizontal then to cell)
  requestAnimationFrame(() => {
    wrap.style.transition = 'transform 2.2s cubic-bezier(0.4, 0.0, 0.2, 1)';
    wrap.style.transform = `translate(${targetX - startX}px, ${targetY - startY}px)`;
  });

  const v = {
    id, el: wrap, kind: typeKey, target,
    arrivedAt: null,
    despawnAt: Date.now() + 2500 + (5000 + Math.random() * 8000) + 2200,  // walk+play+walk
  };
  tycoonState.liveVisitors.push(v);

  // Mark as arrived after walk-in completes; play short bobble + chip pop
  setTimeout(() => {
    if (!wrap.parentNode) return;
    v.arrivedAt = Date.now();
    wrap.classList.add('playing');
    // Trigger a chip particle as visual feedback (server-side actual gen handled by tick)
    _tcChipParticle(targetX, targetY);
  }, 2300);

  // Walk back out before despawn
  const playMs = v.despawnAt - Date.now() - 2200;
  setTimeout(() => {
    if (!wrap.parentNode) return;
    wrap.classList.remove('playing');
    wrap.classList.add('leaving');
    wrap.style.transition = 'transform 2.0s cubic-bezier(0.4, 0.0, 0.2, 1), opacity 0.4s ease 1.6s';
    wrap.style.transform = `translate(${startX - targetX + 20}px, ${startY - targetY}px)`;
    wrap.style.opacity = '0';
  }, playMs);
}

function _tcCleanupDespawned() {
  const now = Date.now();
  tycoonState.liveVisitors = tycoonState.liveVisitors.filter(v => {
    if (now > v.despawnAt) {
      try { v.el.remove(); } catch (_) {}
      return false;
    }
    return true;
  });
}

// Floating chip particle that drifts from a slot toward the chip counter
function _tcChipParticle(x, y) {
  const floor = document.getElementById('tc-floor');
  if (!floor) return;
  const p = document.createElement('div');
  p.className = 'tc-chip-pop';
  p.textContent = '🎲';
  p.style.left = x + 'px';
  p.style.top  = (y - 20) + 'px';
  floor.appendChild(p);
  setTimeout(() => p.remove(), 1100);
}

// ---- HUD refresh (without full repaint) ----
function _tcRefreshHud() {
  const d = tycoonState.data;
  if (!d) return;
  const ce = document.getElementById('tc-chips');
  const ca = document.getElementById('tc-cash');
  if (ce) ce.textContent = fmt(d.chips);
  if (ca) ca.textContent = fmt(d.cash);
}

function _tcRefreshUnits() {
  const d = tycoonState.data;
  if (!d) return;
  // Update tray + occupancy bars (no full re-paint to avoid flicker on visitors)
  for (const u of (d.units || [])) {
    const el = document.querySelector(`.tc-unit[data-uid="${u.id}"]`);
    if (!el) continue;
    let tray = el.querySelector('.tc-tray');
    if (u.chips_in_tray > 0) {
      if (!tray) {
        tray = document.createElement('div');
        tray.className = 'tc-tray glow';
        tray.innerHTML = `<span class="tc-tray-icon">🎲</span><span class="tc-tray-val"></span>`;
        el.appendChild(tray);
      }
      tray.querySelector('.tc-tray-val').textContent = fmt(u.chips_in_tray);
    } else if (tray) {
      tray.remove();
    }
    const fill = el.querySelector('.tc-occ-fill');
    if (fill) fill.style.width = (u.occupancy_pct || 0) + '%';
  }
}

// ---- PANELS (shop / bots / bank / decor) ----
function _tcPanelHtml(d, panel) {
  if (panel === 'shop')     return _tcShopHtml(d);
  if (panel === 'bots')     return _tcBotsHtml(d);
  if (panel === 'bank')     return _tcBankHtml(d);
  if (panel === 'decor')    return _tcDecorHtml(d);
  if (panel === 'themes')   return _tcThemesHtml(d);
  if (panel === 'missions') return _tcMissionsHtml(d);
  if (panel === 'raids')    return _tcRaidsHtml(d);
  if (panel === 'news')     return _tcNewsHtml(d);
  return '';
}

function _tcThemesHtml(d) {
  const items = (d.themes || []).map(t => {
    const owned = !!t.owned;
    const selected = !!t.selected;
    const canBuy = !owned && d.cash >= t.cost;
    return `
      <div class="tc-theme-card ${selected ? 'selected' : ''} ${owned ? 'owned' : ''}" data-theme="${t.key}">
        <div class="tc-theme-icon">${t.icon}</div>
        <div class="tc-theme-name">${escape(t.name)}</div>
        <div class="tc-theme-desc">${escape(t.description || '')}</div>
        <div class="tc-theme-stats">
          ${t.income_mult !== 1 ? `<span class="${t.income_mult>1?'good':'bad'}">×${t.income_mult.toFixed(2)}</span>` : '<span>×1.00</span>'}
          ${t.rep_delta !== 0 ? `<span class="${t.rep_delta>0?'good':'bad'}">${t.rep_delta>0?'+':''}${t.rep_delta.toFixed(1)}⭐</span>` : ''}
        </div>
        ${selected
          ? `<div class="tc-theme-active">✓ активна</div>`
          : owned
            ? `<button class="tc-theme-btn select" data-theme-select="${t.key}">выбрать</button>`
            : `<button class="tc-theme-btn ${canBuy?'':'disabled'}" data-theme-buy="${t.key}" ${canBuy?'':'disabled'}>${fmt(t.cost)} $</button>`
        }
      </div>
    `;
  }).join('');
  return `<div class="tc-panel-title">ТЕМА КАЗИНО</div><div class="tc-themes-grid">${items}</div>`;
}

function _tcMissionsHtml(d) {
  const ms = d.missions || [];
  const items = ms.map(m => {
    const pct = Math.min(100, (m.progress / m.target) * 100);
    const done = m.progress >= m.target;
    return `
      <div class="tc-mission-card ${done?'done':''} ${m.claimed?'claimed':''}">
        <div class="tc-mission-row">
          <div class="tc-mission-name">${escape(m.name)}</div>
          <div class="tc-mission-reward">+${fmt(m.reward_chips)} 🎲</div>
        </div>
        <div class="tc-mission-prog"><div class="tc-mission-prog-fill" style="width:${pct}%"></div></div>
        <div class="tc-mission-foot">
          <span>${fmt(m.progress)} / ${fmt(m.target)}</span>
          ${m.claimed
            ? '<span class="tc-mission-claimed">✓ забрано</span>'
            : done
              ? `<button class="tc-mission-claim" data-mission="${m.key}">забрать</button>`
              : '<span class="tc-mission-pending">в работе</span>'
          }
        </div>
      </div>
    `;
  }).join('');
  return `<div class="tc-panel-title">КВЕСТЫ ДНЯ</div>
    <div class="tc-panel-hint">Сбрасываются каждый день. Награда — чипы.</div>
    <div class="tc-missions-list">${items}</div>`;
}

function _tcRaidsHtml(d) {
  if (!tycoonState.raidTargets) {
    setTimeout(_tcLoadRaidTargets, 50);
    return `<div class="tc-panel-title">НАЛЁТЫ</div>
      <div class="tc-panel-hint">Загрузка целей...</div>`;
  }
  const targets = tycoonState.raidTargets || [];
  const items = targets.length === 0
    ? `<div class="tc-empty">Целей нет</div>`
    : targets.map(t => `
      <div class="tc-raid-card ${t.recently_raided?'cooldown':''}">
        <div class="tc-raid-row">
          <div class="tc-raid-name">${escape(t.name)}</div>
          <div class="tc-raid-cash">💵 ${fmt(t.cash)}</div>
        </div>
        <div class="tc-raid-stats">
          <span>⭐ ${t.reputation.toFixed(1)}</span>
          <span>🛡 охранников: ${t.guards}</span>
          <span>≈ +${fmt(Math.floor(t.cash * 0.05))} $ если повезёт</span>
        </div>
        <button class="tc-raid-btn ${t.recently_raided?'disabled':''}" data-raid="${t.user_id}" ${t.recently_raided?'disabled':''}>
          ${t.recently_raided ? '💤 цель отдыхает' : '⚔️ налёт'}
        </button>
      </div>
    `).join('');
  return `<div class="tc-panel-title">НАЛЁТЫ</div>
    <div class="tc-panel-hint">Своруй до 5% кассы. Кулдаун 6 часов. Охранники у цели снижают шанс на 10% каждый.</div>
    <div class="tc-raids-list">${items}</div>`;
}

function _tcNewsHtml(d) {
  const news = (d.news || []).slice().reverse();
  if (news.length === 0) return `<div class="tc-panel-title">ЛЕНТА</div><div class="tc-empty">Пусто. Зарабатывай и налётами на других казик</div>`;
  const items = news.map(n => {
    const ts = new Date(n.t);
    const time = ts.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
    return `
      <div class="tc-news-row">
        <span class="tc-news-time">${time}</span>
        <span class="tc-news-msg">${escape(n.msg)}</span>
      </div>
    `;
  }).join('');
  return `<div class="tc-panel-title">ЛЕНТА</div><div class="tc-news-list">${items}</div>`;
}

async function _tcLoadRaidTargets() {
  try {
    const r = await api('/api/tycoon/raid/targets');
    tycoonState.raidTargets = Array.isArray(r) ? r : [];
    if (tycoonState.panelOpen === 'raids') _tcPaintAll();
  } catch (_) {
    tycoonState.raidTargets = [];
  }
}

function _tcShopHtml(d) {
  const items = (d.catalog || []).map(c => {
    const canBuy = c.unlocked && d.cash >= c.cost_cash;
    return `
      <div class="tc-shop-card ${canBuy ? '' : 'locked'} kind-${c.kind} tier-${c.tier}" data-key="${c.key}">
        <div class="tc-shop-icon">${c.icon}</div>
        <div class="tc-shop-name">${escape(c.name)}</div>
        <div class="tc-shop-desc">${escape(c.description || '')}</div>
        <div class="tc-shop-stats">
          ${c.base_chips_per_sec > 0 ? `<span>+${c.base_chips_per_sec} 🎲/сек</span>` : ''}
          ${c.capacity > 1 ? `<span>${c.capacity} мест</span>` : ''}
          ${c.unlock_reputation > 0 ? `<span class="tc-rep-req ${c.unlocked ? '' : 'locked'}">⭐${c.unlock_reputation.toFixed(1)}</span>` : ''}
        </div>
        <button class="tc-buy-btn ${canBuy ? '' : 'disabled'}" data-buy="${c.key}" ${canBuy ? '' : 'disabled'}>
          ${c.unlocked ? `${fmt(c.cost_cash)} $` : '🔒 заблок'}
        </button>
      </div>
    `;
  }).join('');
  return `<div class="tc-panel-title">МАГАЗИН</div><div class="tc-shop-grid">${items}</div>
    <div class="tc-panel-hint">Тапни на свободную ячейку чтобы поставить выбранный юнит</div>`;
}

function _tcBotsHtml(d) {
  const items = (d.bot_kinds || []).map(b => {
    const canHire = d.cash >= b.hire_cost && b.owned < b.max_count;
    return `
      <div class="tc-bot-card ${canHire ? '' : 'locked'}">
        <div class="tc-bot-icon">${b.icon}</div>
        <div class="tc-bot-name">${escape(b.name)}</div>
        <div class="tc-bot-desc">${escape(b.description)}</div>
        <div class="tc-bot-stats">
          <span>💼 ${fmt(b.hire_cost)} $</span>
          <span>💸 ${b.salary_per_hr}/ч</span>
          <span>${b.owned} / ${b.max_count}</span>
        </div>
        <button class="tc-hire-btn ${canHire ? '' : 'disabled'}" data-hire="${b.key}" ${canHire ? '' : 'disabled'}>
          ${b.owned >= b.max_count ? 'максимум' : 'нанять'}
        </button>
      </div>
    `;
  }).join('');
  return `<div class="tc-panel-title">ПЕРСОНАЛ</div><div class="tc-bots-grid">${items}</div>`;
}

function _tcBankHtml(d) {
  const cap = d.bank_cap_total;
  const left = d.bank_cap_left;
  const usedPct = ((cap - left) / cap) * 100;
  return `
    <div class="tc-panel-title">КОНВЕРТЕР</div>
    <div class="tc-conv-block">
      <div class="tc-conv-label">🎲 Чипы → 💵 $ (касса)</div>
      <div class="tc-conv-rate">${d.chips_to_cash_rate} чипов = 1 $</div>
      <div class="tc-conv-row">
        <input type="text" inputmode="numeric" pattern="[0-9]*" id="tc-conv-chips" placeholder="${d.chips_to_cash_rate}" />
        <button class="tc-conv-btn" id="tc-conv-chips-btn">→</button>
        <button class="tc-conv-max" data-max-chips>МАКС</button>
      </div>
    </div>
    <div class="tc-conv-block">
      <div class="tc-conv-label">💵 $ → 🪙 coins (банк)</div>
      <div class="tc-conv-rate">${fmt(d.cash_to_coins_rate)} $ = 1 🪙 · лимит сегодня <b>${fmt(left)} / ${fmt(cap)}</b></div>
      <div class="tc-conv-cap-bar"><div class="tc-conv-cap-fill" style="width:${usedPct}%"></div></div>
      <div class="tc-conv-row">
        <input type="text" inputmode="numeric" pattern="[0-9]*" id="tc-conv-cash" placeholder="${d.cash_to_coins_rate}" />
        <button class="tc-conv-btn" id="tc-conv-cash-btn">→</button>
        <button class="tc-conv-max" data-max-cash>МАКС</button>
      </div>
    </div>
    ${d.lifetime_cash >= 1_000_000_000 ? `
      <button class="tc-prestige-btn" id="tc-prestige">⭐ ПРЕСТИЖ (сброс с VIP-звёздами)</button>
    ` : `
      <div class="tc-prestige-locked">До престижа: ${fmt(1_000_000_000 - d.lifetime_cash)} $ суммарных доходов</div>
    `}
  `;
}

function _tcDecorHtml(d) {
  const placed = (d.decor || []).map(dc => `
    <div class="tc-decor-card placed">
      <img src="${dc.image_url}" alt="" />
      <div class="tc-decor-name">${escape(dc.name || '')}</div>
      <div class="tc-decor-rep">+${dc.rep_bonus.toFixed(2)}⭐</div>
      <button class="tc-decor-remove" data-decor-remove="${dc.id}">снять</button>
    </div>
  `).join('');
  const inv = (state.inventory && state.inventory.items) || [];
  const placedIds = new Set((d.decor || []).map(dc => dc.inv_id));
  const placeable = inv
    .filter(i => !i.locked && !i.coinflip_lobby_id && !placedIds.has(i.id))
    .filter(i => ['mil-spec','restricted','classified','covert','exceedingly_rare'].includes(i.rarity))
    .sort((a,b) => b.price - a.price)
    .slice(0, 16);
  const offer = placeable.map(it => {
    const rarBonus = ({ 'mil-spec': 0.05, restricted: 0.1, classified: 0.2, covert: 0.4, exceedingly_rare: 0.8 })[it.rarity] || 0;
    return `
      <div class="tc-decor-card rarity-${it.rarity}">
        <img src="${it.image_url}" alt="" />
        <div class="tc-decor-name">${escape(it.weapon || '')}</div>
        <div class="tc-decor-rep">+${rarBonus.toFixed(2)}⭐</div>
        <button class="tc-decor-place" data-decor-place="${it.id}">повесить</button>
      </div>
    `;
  }).join('');
  return `
    <div class="tc-panel-title">ДЕКОР</div>
    <div class="tc-panel-hint">Скины из инвентаря дают бонус к репутации казино</div>
    ${placed ? `<div class="tc-decor-section-label">Повешено</div><div class="tc-decor-grid">${placed}</div>` : ''}
    ${offer ? `<div class="tc-decor-section-label">Можно повесить</div><div class="tc-decor-grid">${offer}</div>` : '<div class="tc-empty">Нет подходящих скинов (нужны mil-spec и выше)</div>'}
  `;
}

// ---- EVENT WIRING ----
function _tcWireEvents() {
  document.getElementById('tc-collect-all')?.addEventListener('click', _tcCollectAll);
  document.querySelectorAll('.tc-tool-btn[data-panel]').forEach(b => {
    b.addEventListener('click', () => {
      tycoonState.panelOpen = b.dataset.panel;
      _tcPaintAll();
    });
  });
  document.querySelectorAll('.tc-unit[data-uid]').forEach(el => {
    el.addEventListener('click', () => _tcCollectUnit(parseInt(el.dataset.uid)));
  });
  // Buy from shop: click card to "select", then click empty cell to place
  document.querySelectorAll('.tc-shop-card[data-key]').forEach(c => {
    c.addEventListener('click', (e) => {
      // Don't select if user clicked the explicit buy button — that path handles itself
      if (e.target.closest('.tc-buy-btn')) return;
      tycoonState.selectedShopKey = c.dataset.key;
      document.querySelectorAll('.tc-shop-card.selected').forEach(x => x.classList.remove('selected'));
      c.classList.add('selected');
      toast('Кликни свободную ячейку 🟢');
    });
  });
  document.querySelectorAll('.tc-buy-btn[data-buy]').forEach(b => {
    b.addEventListener('click', (e) => {
      e.stopPropagation();
      tycoonState.selectedShopKey = b.dataset.buy;
      document.querySelectorAll('.tc-shop-card.selected').forEach(x => x.classList.remove('selected'));
      b.closest('.tc-shop-card')?.classList.add('selected');
      toast('Кликни свободную ячейку 🟢');
    });
  });
  document.querySelectorAll('.tc-tile[data-cx]').forEach(t => {
    t.addEventListener('click', () => _tcPlaceUnit(parseInt(t.dataset.cx), parseInt(t.dataset.cy)));
  });
  document.querySelectorAll('.tc-hire-btn[data-hire]').forEach(b => {
    b.addEventListener('click', (e) => { e.stopPropagation(); _tcHire(b.dataset.hire); });
  });
  document.getElementById('tc-buy-cell')?.addEventListener('click', _tcBuyCell);
  document.getElementById('tc-conv-chips-btn')?.addEventListener('click', () => {
    const v = parseInt(document.getElementById('tc-conv-chips').value || '0');
    if (v > 0) _tcConvertChips(v);
  });
  document.getElementById('tc-conv-cash-btn')?.addEventListener('click', () => {
    const v = parseInt(document.getElementById('tc-conv-cash').value || '0');
    if (v > 0) _tcConvertCash(v);
  });
  document.querySelector('[data-max-chips]')?.addEventListener('click', () => {
    const inp = document.getElementById('tc-conv-chips');
    if (inp) inp.value = tycoonState.data.chips;
  });
  document.querySelector('[data-max-cash]')?.addEventListener('click', () => {
    const inp = document.getElementById('tc-conv-cash');
    if (inp) inp.value = tycoonState.data.cash;
  });
  document.getElementById('tc-prestige')?.addEventListener('click', _tcDoPrestige);
  document.querySelectorAll('[data-decor-place]').forEach(b => {
    b.addEventListener('click', () => _tcDecorPlace(parseInt(b.dataset.decorPlace)));
  });
  document.querySelectorAll('[data-decor-remove]').forEach(b => {
    b.addEventListener('click', () => _tcDecorRemove(parseInt(b.dataset.decorRemove)));
  });
  document.querySelectorAll('[data-theme-buy]').forEach(b => {
    b.addEventListener('click', () => _tcBuyTheme(b.dataset.themeBuy));
  });
  document.querySelectorAll('[data-theme-select]').forEach(b => {
    b.addEventListener('click', () => _tcSelectTheme(b.dataset.themeSelect));
  });
  document.querySelectorAll('[data-mission]').forEach(b => {
    b.addEventListener('click', () => _tcClaimMission(b.dataset.mission));
  });
  document.querySelectorAll('[data-raid]').forEach(b => {
    b.addEventListener('click', () => _tcDoRaid(parseInt(b.dataset.raid)));
  });
}

async function _tcBuyTheme(key) {
  try {
    const r = await api('/api/tycoon/theme/buy', { method: 'POST', body: JSON.stringify({ key }) });
    if (!r.ok) { toast(r.error || 'Ошибка'); return; }
    tycoonState.data = r;
    _tcPaintAll();
    toast('🎨 Тема куплена');
  } catch (e) { toast(e.message); }
}

async function _tcSelectTheme(key) {
  try {
    const r = await api('/api/tycoon/theme/select', { method: 'POST', body: JSON.stringify({ key }) });
    if (!r.ok) { toast(r.error || 'Ошибка'); return; }
    tycoonState.data = r;
    _tcPaintAll();
  } catch (e) { toast(e.message); }
}

async function _tcClaimMission(key) {
  try {
    const r = await api('/api/tycoon/mission/claim', { method: 'POST', body: JSON.stringify({ key }) });
    if (!r.ok) { toast(r.error || 'Ошибка'); return; }
    toast(`+${fmt(r.reward_chips)} 🎲 за квест!`);
    tg?.HapticFeedback?.notificationOccurred?.('success');
    // Refresh state
    tycoonState.data = await api('/api/tycoon/state');
    _tcPaintAll();
  } catch (e) { toast(e.message); }
}

async function _tcDoRaid(targetId) {
  if (!confirm('Совершить налёт? Удача 30% базово, охрана цели снижает.')) return;
  try {
    const r = await api('/api/tycoon/raid', { method: 'POST', body: JSON.stringify({ target_id: targetId }) });
    if (!r.ok) { toast(r.error || 'Ошибка'); return; }
    if (r.success && r.stolen > 0) {
      tg?.HapticFeedback?.notificationOccurred?.('success');
      toast(`⚔️ Унёс ${fmt(r.stolen)} $!`);
    } else {
      tg?.HapticFeedback?.notificationOccurred?.('error');
      toast(`🛡 Налёт провалился (шанс был ${Math.round(r.success_chance*100)}%)`);
    }
    tycoonState.raidTargets = null;
    tycoonState.data = await api('/api/tycoon/state');
    _tcPaintAll();
  } catch (e) { toast(e.message); }
}

// ---- ACTIONS ----
async function _tcCollectUnit(uid) {
  const el = document.querySelector(`.tc-unit[data-uid="${uid}"]`);
  if (el) {
    const rect = el.getBoundingClientRect();
    const fr = document.getElementById('tc-floor').getBoundingClientRect();
    const cx = rect.left - fr.left + rect.width / 2;
    const cy = rect.top - fr.top + rect.height / 2;
    for (let i = 0; i < 6; i++) {
      _tcChipParticle(cx + (Math.random() - 0.5) * 30, cy + (Math.random() - 0.5) * 20);
    }
  }
  try {
    const r = await api('/api/tycoon/collect', { method: 'POST', body: JSON.stringify({ unit_id: uid }) });
    if (r.ok && r.collected > 0) {
      tg?.HapticFeedback?.impactOccurred?.('light');
      tycoonState.data.chips = (tycoonState.data.chips || 0) + r.collected;
      _tcRefreshHud();
      // Clear local tray immediately
      const tray = el?.querySelector('.tc-tray');
      if (tray) tray.remove();
    }
  } catch (e) { toast(e.message); }
}

async function _tcCollectAll() {
  try {
    const r = await api('/api/tycoon/collect_all', { method: 'POST' });
    if (r.ok && r.collected > 0) {
      tg?.HapticFeedback?.notificationOccurred?.('success');
      toast(`+${fmt(r.collected)} 🎲`);
      tycoonState.data.chips = (tycoonState.data.chips || 0) + r.collected;
      _tcRefreshHud();
      document.querySelectorAll('.tc-tray').forEach(el => el.remove());
    }
  } catch (e) { toast(e.message); }
}

async function _tcPlaceUnit(cx, cy) {
  if (!tycoonState.selectedShopKey) return;
  try {
    const r = await api('/api/tycoon/buy', {
      method: 'POST',
      body: JSON.stringify({ unit_key: tycoonState.selectedShopKey, cell_x: cx, cell_y: cy }),
    });
    if (!r.ok) { toast(r.error || 'Ошибка'); return; }
    tycoonState.selectedShopKey = null;
    tycoonState.data = r;
    tg?.HapticFeedback?.notificationOccurred?.('success');
    _tcPaintAll();
  } catch (e) { toast(e.message); }
}

async function _tcHire(kind) {
  try {
    const r = await api('/api/tycoon/hire', { method: 'POST', body: JSON.stringify({ kind }) });
    if (!r.ok) { toast(r.error || 'Ошибка'); return; }
    tycoonState.data = r;
    _tcPaintAll();
    toast('Нанят');
  } catch (e) { toast(e.message); }
}

async function _tcBuyCell() {
  try {
    const r = await api('/api/tycoon/buy_cell', { method: 'POST' });
    if (!r.ok) { toast(r.error || 'Ошибка'); return; }
    tycoonState.data = r;
    _tcPaintAll();
    toast('+1 ячейка');
  } catch (e) { toast(e.message); }
}

async function _tcConvertChips(amount) {
  try {
    const r = await api('/api/tycoon/convert/chips_to_cash', { method: 'POST', body: JSON.stringify({ amount }) });
    if (!r.ok) { toast(r.error || 'Ошибка'); return; }
    toast(`+${fmt(r.cash_gained)} $`);
    tycoonState.data.chips -= r.spent_chips;
    tycoonState.data.cash  += r.cash_gained;
    _tcRefreshHud();
    _tcPaintAll();
  } catch (e) { toast(e.message); }
}

async function _tcConvertCash(amount) {
  try {
    const r = await api('/api/tycoon/convert/cash_to_coins', { method: 'POST', body: JSON.stringify({ amount }) });
    if (!r.ok) { toast(r.error || 'Ошибка'); return; }
    toast(`+${fmt(r.coins_gained)} 🪙`);
    if (typeof r.new_balance === 'number') {
      state.me.balance = r.new_balance;
      const bel = document.getElementById('balance-display');
      if (bel) bel.textContent = fmt(state.me.balance);
    }
    tycoonState.data.cash -= r.spent_cash;
    _tcRefreshHud();
    _tcPaintAll();
  } catch (e) { toast(e.message); }
}

async function _tcDoPrestige() {
  if (!confirm('Сбросить казино и получить VIP-звёзды? Все юниты/боты исчезнут, но конверсия станет лучше.')) return;
  try {
    const r = await api('/api/tycoon/prestige', { method: 'POST' });
    if (!r.ok) { toast(r.error || 'Ошибка'); return; }
    tycoonState.data = r;
    _tcPaintAll();
    toast('⭐ Престиж активирован!');
  } catch (e) { toast(e.message); }
}

async function _tcDecorPlace(invId) {
  try {
    if (!state.inventory) await loadInventory();
    const r = await api('/api/tycoon/decor/place', { method: 'POST', body: JSON.stringify({ inv_id: invId }) });
    if (!r.ok) { toast(r.error || 'Ошибка'); return; }
    tycoonState.data = r;
    _tcPaintAll();
    toast('Декор повешен');
  } catch (e) { toast(e.message); }
}

async function _tcDecorRemove(decorId) {
  try {
    const r = await api('/api/tycoon/decor/remove', { method: 'POST', body: JSON.stringify({ decor_id: decorId }) });
    if (!r.ok) { toast(r.error || 'Ошибка'); return; }
    tycoonState.data = r;
    _tcPaintAll();
  } catch (e) { toast(e.message); }
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

  // Deep-link handler: if app was opened via t.me/<bot>/<app>?startapp=cf_<id>,
  // jump straight to the PvP screen and auto-open that lobby.
  try {
    const startParam = tg && tg.initDataUnsafe && tg.initDataUnsafe.start_param;
    if (typeof startParam === 'string' && /^cf_\d+$/.test(startParam)) {
      const lobbyId = parseInt(startParam.slice(3));
      // Mark for renderCfPvp to auto-jump after rendering hub
      cfState.pendingLobbyId = lobbyId;
      // Switch to games view and trigger PvP card
      showView('games');
      const grid = document.querySelector('.game-grid');
      if (grid) grid.style.display = 'none';
      const area = document.getElementById('game-play-area');
      if (area) {
        area.innerHTML = `<button class="back-btn" id="game-back-btn" style="margin-bottom:10px">← к играм</button>`;
        const holder = document.createElement('div');
        area.appendChild(holder);
        document.getElementById('game-back-btn').addEventListener('click', closeGameScreen);
        renderCfPvp(holder);
      }
    }
  } catch (e) { /* non-critical */ }
})();
