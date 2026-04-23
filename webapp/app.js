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
  const resp = await fetch(url, opts);
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ detail: resp.statusText }));
    throw new Error(err.detail || `HTTP ${resp.status}`);
  }
  return resp.json();
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

  // after anim — show result
  setTimeout(() => {
    tg?.HapticFeedback?.notificationOccurred?.(
      (result.skin.rarity === 'covert' || result.skin.rarity === 'exceedingly_rare') ? 'success' : 'warning'
    );

    const resEl = document.getElementById('case-open-result');
    const stBadge = result.stat_trak ? '<span class="stattrak-badge">ST™</span>' : '';
    resEl.innerHTML = `
      <div class="result-card rarity-${result.skin.rarity}" style="position:relative">
        ${stBadge}
        <img class="result-img" src="${result.skin.image_url}" alt="" />
        <div class="result-name">${escape(result.skin.full_name)}</div>
        <div class="result-meta">${result.wear.replace('_', '-').toUpperCase()} • float ${result.float.toFixed(4)}</div>
        <div class="result-price">+${fmt(result.price)} 🪙 <span style="font-size:13px; color:var(--text-dim); font-weight:normal">в инвентарь</span></div>
      </div>
    `;
    resEl.classList.add('shown');
    document.getElementById('case-open-actions').style.display = 'flex';

    // update balance shown top
    state.me.balance = result.new_balance;
    state.me.cases_opened += 1;
    document.getElementById('balance-display').textContent = fmt(state.me.balance);
  }, 6100);

  // wire "open again"
  document.getElementById('case-open-again').onclick = () => openCase(caseId);
}

const invFilter = { rarity: '', sort: 'price_desc' };

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

  grid.innerHTML = filtered.map(it => `
    <div class="inv-item rarity-${it.rarity}" data-inv-id="${it.id}">
      ${it.stat_trak ? '<div class="stattrak-badge">ST™</div>' : ''}
      <img class="inv-item-img" src="${it.image_url}" alt="" loading="lazy" />
      <div class="inv-item-weapon">${escape(it.weapon)}</div>
      <div class="inv-item-name">${escape(it.skin_name)}</div>
      <div class="inv-item-wear">${it.wear_short} · ${it.float.toFixed(3)}</div>
      <div class="inv-item-price">${fmt(it.price)} 🪙</div>
    </div>
  `).join('');

  grid.querySelectorAll('.inv-item').forEach(card => {
    card.addEventListener('click', () => showItemDetail(parseInt(card.dataset.invId)));
  });
}

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
async function loadDailyTask() {
  try {
    const task = await api('/api/task');
    const card = document.getElementById('daily-task-card');
    document.getElementById('dt-prompt').textContent = task.prompt;
    document.getElementById('dt-reward').textContent = `💰 +${fmt(task.reward)} коинов за верный ответ`;
    card.classList.remove('hidden');
    const status = document.getElementById('dt-status');
    status.textContent = '';
    if (task.solved) {
      status.textContent = '✅ Решено сегодня. Завтра будет новая.';
      document.getElementById('dt-answer').disabled = true;
      document.getElementById('dt-submit').disabled = true;
    } else {
      document.getElementById('dt-answer').disabled = false;
      document.getElementById('dt-submit').disabled = false;
      if (task.attempts > 0) status.textContent = `Попыток: ${task.attempts}/5`;
    }
  } catch (e) {
    console.warn('daily task load', e);
  }
}

document.addEventListener('DOMContentLoaded', () => {
  const submit = document.getElementById('dt-submit');
  submit?.addEventListener('click', async () => {
    const answerEl = document.getElementById('dt-answer');
    const answer = (answerEl?.value || '').trim();
    if (!answer) return;
    try {
      const r = await api('/api/task/answer', { method: 'POST', body: JSON.stringify({ answer }) });
      const status = document.getElementById('dt-status');
      if (r.correct && r.reward) {
        tg?.HapticFeedback?.notificationOccurred?.('success');
        toast(`🎉 +${fmt(r.reward)} 🪙`);
        status.textContent = '✅ Решено!';
        answerEl.disabled = true;
        submit.disabled = true;
        state.me.balance = r.new_balance;
        document.getElementById('balance-display').textContent = fmt(state.me.balance);
      } else if (r.correct) {
        status.textContent = '✅ Уже решено';
      } else {
        tg?.HapticFeedback?.notificationOccurred?.('error');
        status.textContent = `❌ Неправильно. Попыток осталось: ${r.attempts_left ?? '?'}`;
      }
    } catch (e) {
      toast(`Ошибка: ${e.message}`);
    }
  });

  document.querySelectorAll('.game-card').forEach(card => {
    card.addEventListener('click', () => {
      document.querySelectorAll('.game-card').forEach(c => c.classList.remove('active'));
      card.classList.add('active');
      renderGamePlay(card.dataset.game);
    });
  });
});

function renderGamePlay(game) {
  const area = document.getElementById('game-play-area');
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
  }
}

async function playCoinflip(side) {
  const bet = parseInt(document.getElementById('cf-bet').value || '0');
  if (bet <= 0) return toast('Поставь сумму');
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
    if (target === 'games') loadDailyTask();
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
