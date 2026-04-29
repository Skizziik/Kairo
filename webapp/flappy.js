/* Flappy Bird — «Взлёт»
   Connects to global `state` (window.state), `api`, `fmt`, `escape`, `toast`.

   Architecture:
   - boot() initializes once when nav switches to view="flappy"
   - Hub UI (menu/shop/upgrades/cases/leaderboard tabs) lives in #flappy-app
   - On Play press → fullscreen canvas overlay starts a run
   - Run records to /api/flappy/run on death/cashout */
(() => {
  const FS = {
    state: null,
    cfg: null,
    inited: false,
    activeTab: 'play',
    pollTimer: null,
  };
  const root = () => document.getElementById('flappy-app');
  const tg   = window.Telegram?.WebApp;

  // ═════════════════ BOOT ═════════════════
  async function boot() {
    if (FS.inited) { await refresh(); return; }
    FS.inited = true;
    try {
      const [cfg, st] = await Promise.all([
        api('/api/flappy/config'),
        api('/api/flappy/state'),
      ]);
      FS.cfg = cfg; FS.state = st;
    } catch (e) {
      const r = root(); if (r) r.innerHTML = '<div class="loader">Ошибка: ' + escape(e.message) + '</div>';
      return;
    }
    paintHub();
    if (FS.pollTimer) clearInterval(FS.pollTimer);
    FS.pollTimer = setInterval(async () => {
      const isActive = document.querySelector('.view[data-view="flappy"].active');
      if (!isActive) return;
      try {
        FS.state = await api('/api/flappy/state');
        if (!document.querySelector('.flappy-game-overlay')) paintHub();
      } catch (e) {}
    }, 20_000);
  }

  async function refresh() {
    try {
      FS.state = await api('/api/flappy/state');
      paintHub();
    } catch (e) {}
  }

  function fmtCompact(n) {
    if (typeof window.fmtCompact === 'function') return window.fmtCompact(n);
    n = Number(n) || 0;
    if (n >= 1e12) return (n/1e12).toFixed(2) + 'T';
    if (n >= 1e9)  return (n/1e9).toFixed(2) + 'B';
    if (n >= 1e6)  return (n/1e6).toFixed(1) + 'M';
    if (n >= 1e3)  return (n/1e3).toFixed(1) + 'K';
    return String(Math.round(n));
  }

  // ═════════════════ HUB ═════════════════
  function paintHub() {
    const r = root();
    if (!r || !FS.state || !FS.cfg) return;
    const s = FS.state;
    const lvlPct = Math.min(100, Math.floor(((s.xp - s.current_level_xp) / (s.next_level_xp - s.current_level_xp)) * 100));
    r.innerHTML = `
      <div class="flappy-hub">
        <div class="flappy-top">
          <div class="flappy-level">
            <span class="flappy-lvl-badge">⭐ LVL ${s.level}</span>
            <div class="flappy-xp-bar"><div class="flappy-xp-fill" style="width:${lvlPct}%"></div></div>
            <span class="flappy-xp-txt">${fmt(s.xp - s.current_level_xp)} / ${fmt(s.next_level_xp - s.current_level_xp)} XP</span>
          </div>
          <div class="flappy-stats-strip">
            <div class="flappy-stat"><div class="flappy-stat-i">🪙</div><div class="flappy-stat-v">${fmtCompact(s.pluma_balance)}</div><div class="flappy-stat-l">Pluma</div></div>
            <div class="flappy-stat"><div class="flappy-stat-i">🏆</div><div class="flappy-stat-v">${fmt(s.best_run_distance)}</div><div class="flappy-stat-l">Best</div></div>
            <div class="flappy-stat"><div class="flappy-stat-i">▶</div><div class="flappy-stat-v">${fmt(s.runs_count)}</div><div class="flappy-stat-l">Ранов</div></div>
            <div class="flappy-stat"><div class="flappy-stat-i">🔥</div><div class="flappy-stat-v">${fmt(s.best_combo)}</div><div class="flappy-stat-l">Combo</div></div>
          </div>
        </div>
        <div class="flappy-tabs">
          <button class="flappy-tab" data-tab="play"><span>▶</span><span>Игра</span></button>
          <button class="flappy-tab" data-tab="upgrades"><span>⚒</span><span>Прокачка</span></button>
          <button class="flappy-tab" data-tab="birds"><span>🐦</span><span>Птицы</span></button>
          <button class="flappy-tab" data-tab="maps"><span>🗺</span><span>Карты</span></button>
          <button class="flappy-tab" data-tab="cases"><span>🎁</span><span>Кейсы</span></button>
          <button class="flappy-tab" data-tab="exchange"><span>💱</span><span>Обмен</span></button>
          <button class="flappy-tab" data-tab="lb"><span>🏆</span><span>Топ</span></button>
        </div>
        <div class="flappy-tab-content" id="flappy-tab-content"></div>
      </div>
    `;
    r.querySelectorAll('.flappy-tab').forEach(b => {
      b.addEventListener('click', () => switchTab(b.dataset.tab));
    });
    switchTab(FS.activeTab);
  }

  function switchTab(tab) {
    FS.activeTab = tab;
    const r = root(); if (!r) return;
    r.querySelectorAll('.flappy-tab').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
    const c = document.getElementById('flappy-tab-content'); if (!c) return;
    if (tab === 'play')     paintPlay(c);
    if (tab === 'upgrades') paintUpgrades(c);
    if (tab === 'birds')    paintBirds(c);
    if (tab === 'maps')     paintMaps(c);
    if (tab === 'cases')    paintCases(c);
    if (tab === 'exchange') paintExchange(c);
    if (tab === 'lb')       paintLeaderboard(c);
  }

  // ─────── PLAY ───────
  function paintPlay(c) {
    const s = FS.state;
    const bird = FS.cfg.birds.find(b => b.key === s.current_bird_id);
    const map  = FS.cfg.maps.find(m => m.key === s.current_map_id);
    c.innerHTML = `
      <div class="flappy-play">
        <div class="flappy-play-card">
          <div class="flappy-play-row">
            <img class="flappy-play-bird" src="./img/flappy/birds/${bird.image}" alt="" />
            <div>
              <div class="flappy-play-label">Текущая птица</div>
              <div class="flappy-play-val">${escape(bird.name)}</div>
              <div class="flappy-play-sub">${escape(bird.passive_short)}</div>
            </div>
          </div>
          <div class="flappy-play-row">
            <img class="flappy-play-map" src="./img/flappy/maps/${map.image}" alt="" />
            <div>
              <div class="flappy-play-label">Карта</div>
              <div class="flappy-play-val">${escape(map.name)}</div>
              <div class="flappy-play-sub">${escape(map.bonus_short)}</div>
            </div>
          </div>
        </div>
        <button class="flappy-start-btn" id="flappy-start">▶ ВЗЛЕТ!</button>
        <div class="flappy-play-tip">💡 Тап = взмах. Cash-out: ×1.5 (10 truck) → ×5 (500 truck).</div>
      </div>
    `;
    document.getElementById('flappy-start').addEventListener('click', startGame);
  }

  // ─────── UPGRADES ───────
  function paintUpgrades(c) {
    const branches = FS.cfg.branches;
    const ups = FS.cfg.upgrades;
    const userLevels = FS.state.upgrades || {};
    const balance = FS.state.pluma_balance;
    const list = ups.map(u => {
      const cur = Number(userLevels[u.key] || 0);
      const max = u.max_level;
      const isMax = cur >= max;
      const tier = isMax ? null : u.tiers[cur];
      const cost = tier ? tier[2] : 0;
      const nextEffect = tier ? tier[1] : (u.tiers[max - 1] && u.tiers[max - 1][1]);
      const canAfford = !isMax && balance >= cost;
      const progress = (cur / max) * 100;
      const branchColor = (branches.find(b => b.key === u.branch) || {}).color || '#888';
      return `
        <div class="flappy-up-card" style="border-left-color:${branchColor}">
          <div class="flappy-up-icon">${u.icon}</div>
          <div class="flappy-up-info">
            <div class="flappy-up-name">${escape(u.name)}</div>
            <div class="flappy-up-desc">${escape(u.desc)}</div>
            <div class="flappy-up-progress">
              <span>${cur}/${max}</span>
              <div class="flappy-up-bar"><div class="flappy-up-bar-fill" style="width:${progress}%;background:${branchColor}"></div></div>
              <span>${nextEffect}${escape(u.unit || '')}</span>
            </div>
          </div>
          ${isMax
            ? `<button class="flappy-up-buy maxed" disabled>MAX</button>`
            : `<button class="flappy-up-buy" data-up="${u.key}" ${canAfford ? '' : 'disabled'}>${fmtCompact(cost)}</button>`}
        </div>
      `;
    }).join('');
    c.innerHTML = `<div class="flappy-up-list">${list}</div>`;
    c.querySelectorAll('[data-up]').forEach(b => {
      b.addEventListener('click', async () => {
        b.disabled = true;
        try {
          const r = await api('/api/flappy/upgrade', { method: 'POST', body: JSON.stringify({ key: b.dataset.up }) });
          if (!r.ok) { toast(r.error || 'Ошибка'); b.disabled = false; return; }
          tg?.HapticFeedback?.impactOccurred?.('light');
          await refresh();
        } catch (e) { toast(e.message); b.disabled = false; }
      });
    });
  }

  // ─────── BIRDS ───────
  function paintBirds(c) {
    const owned = new Set(FS.state.owned_birds || []);
    const cur = FS.state.current_bird_id;
    const balance = FS.state.pluma_balance;
    c.innerHTML = `<div class="flappy-birds-grid">${
      FS.cfg.birds.map(b => {
        const isOwned = owned.has(b.key);
        const isActive = cur === b.key;
        const canAfford = balance >= b.price;
        return `
          <div class="flappy-bird-card ${isActive ? 'active' : ''} ${isOwned ? 'owned' : ''}">
            <img src="./img/flappy/birds/${b.image}" alt="${escape(b.name)}" />
            <div class="flappy-bird-name">${escape(b.name)}</div>
            <div class="flappy-bird-passive">${escape(b.passive_short)}</div>
            <div class="flappy-bird-desc">${escape(b.passive_long)}</div>
            ${isActive
              ? `<div class="flappy-bird-tag">✓ Активна</div>`
              : isOwned
                ? `<button class="flappy-bird-btn equip" data-equip="${b.key}">Выбрать</button>`
                : `<button class="flappy-bird-btn buy" data-buy-bird="${b.key}" ${canAfford ? '' : 'disabled'}>
                    ${b.price === 0 ? 'Free' : fmtCompact(b.price) + ' Pluma'}
                  </button>`}
          </div>
        `;
      }).join('')
    }</div>`;
    c.querySelectorAll('[data-buy-bird]').forEach(b => {
      b.addEventListener('click', async () => {
        b.disabled = true;
        try {
          const r = await api('/api/flappy/bird/buy', { method: 'POST', body: JSON.stringify({ key: b.dataset.buyBird }) });
          if (!r.ok) { toast(r.error || 'Ошибка'); b.disabled = false; return; }
          tg?.HapticFeedback?.notificationOccurred?.('success');
          await refresh();
        } catch (e) { toast(e.message); b.disabled = false; }
      });
    });
    c.querySelectorAll('[data-equip]').forEach(b => {
      b.addEventListener('click', async () => {
        b.disabled = true;
        try {
          const r = await api('/api/flappy/bird/equip', { method: 'POST', body: JSON.stringify({ key: b.dataset.equip }) });
          if (!r.ok) { toast(r.error || 'Ошибка'); b.disabled = false; return; }
          await refresh();
        } catch (e) { toast(e.message); b.disabled = false; }
      });
    });
  }

  // ─────── MAPS ───────
  function paintMaps(c) {
    const owned = new Set(FS.state.owned_maps || []);
    const cur = FS.state.current_map_id;
    const lvl = FS.state.level;
    const balance = FS.state.pluma_balance;
    c.innerHTML = `<div class="flappy-maps-grid">${
      FS.cfg.maps.map(m => {
        const isOwned = owned.has(m.key);
        const isActive = cur === m.key;
        const lvlOk = lvl >= m.unlock_lvl;
        const canAfford = balance >= m.price;
        return `
          <div class="flappy-map-card ${isActive ? 'active' : ''} ${isOwned ? 'owned' : ''} ${!lvlOk ? 'locked' : ''}">
            <img src="./img/flappy/maps/${m.image}" alt="${escape(m.name)}" />
            <div class="flappy-map-name">${escape(m.name)}</div>
            <div class="flappy-map-bonus">${escape(m.bonus_short)}</div>
            <div class="flappy-map-desc">${escape(m.bonus_long)}</div>
            ${isActive
              ? `<div class="flappy-bird-tag">✓ Выбрана</div>`
              : isOwned
                ? `<button class="flappy-bird-btn equip" data-select-map="${m.key}">Выбрать</button>`
                : !lvlOk
                  ? `<div class="flappy-bird-tag locked">🔒 Lvl ${m.unlock_lvl}</div>`
                  : `<button class="flappy-bird-btn buy" data-unlock-map="${m.key}" ${canAfford ? '' : 'disabled'}>
                      ${fmtCompact(m.price)} Pluma
                    </button>`}
          </div>
        `;
      }).join('')
    }</div>`;
    c.querySelectorAll('[data-unlock-map]').forEach(b => {
      b.addEventListener('click', async () => {
        b.disabled = true;
        try {
          const r = await api('/api/flappy/map/unlock', { method: 'POST', body: JSON.stringify({ key: b.dataset.unlockMap }) });
          if (!r.ok) { toast(r.error || 'Ошибка'); b.disabled = false; return; }
          tg?.HapticFeedback?.notificationOccurred?.('success');
          await refresh();
        } catch (e) { toast(e.message); b.disabled = false; }
      });
    });
    c.querySelectorAll('[data-select-map]').forEach(b => {
      b.addEventListener('click', async () => {
        b.disabled = true;
        try {
          const r = await api('/api/flappy/map/select', { method: 'POST', body: JSON.stringify({ key: b.dataset.selectMap }) });
          if (!r.ok) { toast(r.error || 'Ошибка'); b.disabled = false; return; }
          await refresh();
        } catch (e) { toast(e.message); b.disabled = false; }
      });
    });
  }

  // ─────── CASES ───────
  function paintCases(c) {
    const balance = FS.state.pluma_balance;
    const owned = new Set(FS.state.artifacts || []);
    const cases = FS.cfg.cases;
    const arts = FS.cfg.artifacts;
    const casesHtml = cases.map(cs => {
      const canAfford = balance >= cs.price;
      return `
        <div class="flappy-case-card">
          <img src="./img/flappy/cases/${cs.image}" alt="" />
          <div class="flappy-case-name">${escape(cs.name)}</div>
          <button class="flappy-case-buy" data-case="${cs.key}" ${canAfford ? '' : 'disabled'}>
            ${fmtCompact(cs.price)} Pluma
          </button>
        </div>
      `;
    }).join('');
    const artsHtml = arts.map(a => {
      const isOwned = owned.has(a.key);
      return `
        <div class="flappy-art-card tier-${a.tier} ${isOwned ? 'owned' : ''}">
          <img src="./img/flappy/artifacts/${a.image}" alt="" />
          <div class="flappy-art-name">${escape(a.name)}</div>
          <div class="flappy-art-buff">${escape(a.buff_short)}</div>
          <div class="flappy-art-long">${escape(a.buff_long)}</div>
          ${isOwned
            ? '<div class="flappy-art-tag">✨ Активен</div>'
            : '<div class="flappy-art-tag locked">не получен</div>'}
        </div>
      `;
    }).join('');
    c.innerHTML = `
      <div class="flappy-cases-strip">${casesHtml}</div>
      <div class="flappy-section-label">🛡 Артефакты (${owned.size}/${arts.length})</div>
      <div class="flappy-arts-grid">${artsHtml}</div>
    `;
    c.querySelectorAll('[data-case]').forEach(b => {
      b.addEventListener('click', async () => {
        b.disabled = true;
        try {
          const r = await api('/api/flappy/case/buy', { method: 'POST', body: JSON.stringify({ key: b.dataset.case }) });
          if (!r.ok) { toast(r.error || 'Ошибка'); b.disabled = false; return; }
          tg?.HapticFeedback?.notificationOccurred?.('success');
          showDropModal(r.drop);
          await refresh();
        } catch (e) { toast(e.message); b.disabled = false; }
      });
    });
  }

  function showDropModal(drop) {
    const overlay = document.createElement('div');
    overlay.className = 'flappy-drop-overlay';
    overlay.innerHTML = `
      <div class="flappy-drop-box tier-${drop.tier}">
        <div class="flappy-drop-title">${drop.duplicate ? 'Дубликат!' : '✨ НОВЫЙ АРТЕФАКТ'}</div>
        <img src="./img/flappy/artifacts/${drop.image}" />
        <div class="flappy-drop-name">${escape(drop.name)}</div>
        <div class="flappy-drop-buff">${escape(drop.buff_short)}</div>
        <div class="flappy-drop-long">${escape(drop.buff_long)}</div>
        <button class="flappy-drop-close">Забрать</button>
      </div>
    `;
    document.body.appendChild(overlay);
    overlay.querySelector('.flappy-drop-close').addEventListener('click', () => overlay.remove());
    overlay.addEventListener('click', e => { if (e.target === overlay) overlay.remove(); });
  }

  // ─────── EXCHANGE ───────
  function paintExchange(c) {
    const s = FS.state;
    c.innerHTML = `
      <div class="flappy-exchange-card">
        <div class="flappy-exchange-rate">💱 Курс: <b>1 Pluma = 1 🪙</b></div>
        <div class="flappy-exchange-tax">⚠ Облагается налогом — обмен учтётся в дневном tax tick</div>
        <div class="flappy-exchange-bal">У вас: <b>${fmt(s.pluma_balance)}</b> Pluma</div>
        <input type="number" class="flappy-exchange-input" id="flappy-exch-input" placeholder="Сколько обменять?" min="1" max="${s.pluma_balance}" />
        <div class="flappy-exchange-quick">
          <button data-amt="10000">10K</button>
          <button data-amt="100000">100K</button>
          <button data-amt="1000000">1M</button>
          <button data-amt="all">ВСЁ</button>
        </div>
        <button class="flappy-exchange-btn" id="flappy-exch-btn">💱 Обменять</button>
      </div>
    `;
    const input = document.getElementById('flappy-exch-input');
    c.querySelectorAll('[data-amt]').forEach(b => {
      b.addEventListener('click', () => {
        input.value = b.dataset.amt === 'all' ? s.pluma_balance : b.dataset.amt;
      });
    });
    document.getElementById('flappy-exch-btn').addEventListener('click', async () => {
      const amount = Math.max(1, Math.min(s.pluma_balance, Number(input.value) || 0));
      if (!amount) { toast('Введи сумму'); return; }
      try {
        const r = await api('/api/flappy/exchange', { method: 'POST', body: JSON.stringify({ amount }) });
        if (!r.ok) { toast(r.error || 'Ошибка'); return; }
        toast(`✓ +${fmt(r.exchanged)} 🪙 на баланс`);
        if (typeof r.new_balance === 'number') {
          window.state.me.balance = r.new_balance;
          const balEl = document.getElementById('balance-display');
          if (balEl) balEl.textContent = fmt(r.new_balance);
        }
        await refresh();
      } catch (e) { toast(e.message); }
    });
  }

  // ─────── LEADERBOARD ───────
  let lbCache = { best_run: null, lifetime: null };
  let lbActiveSort = 'best_run';   // default — most exciting metric

  function paintLeaderboard(c) {
    c.innerHTML = `
      <div class="flappy-lb-toggle">
        <button class="flappy-lb-toggle-btn ${lbActiveSort === 'best_run' ? 'active' : ''}" data-sort="best_run">
          🏆 За один ран
        </button>
        <button class="flappy-lb-toggle-btn ${lbActiveSort === 'lifetime' ? 'active' : ''}" data-sort="lifetime">
          💰 Всего нафармлено
        </button>
      </div>
      <div id="flappy-lb-list" class="flappy-lb-list">
        <div class="loader">Загрузка...</div>
      </div>
    `;
    c.querySelectorAll('[data-sort]').forEach(b => {
      b.addEventListener('click', () => {
        lbActiveSort = b.dataset.sort;
        paintLeaderboard(c);
      });
    });
    fetchAndRenderLb(lbActiveSort);
  }

  async function fetchAndRenderLb(sort) {
    const list = document.getElementById('flappy-lb-list');
    if (!list) return;
    try {
      const rows = await api(`/api/flappy/leaderboard?sort_by=${encodeURIComponent(sort)}`);
      lbCache[sort] = rows;
      list.innerHTML = renderLbRows(rows, sort);
    } catch (e) {
      list.innerHTML = `<div class="loader">Ошибка: ${escape(e.message)}</div>`;
    }
  }

  function renderLbRows(rows, sort) {
    if (!rows || rows.length === 0) {
      return '<div class="flappy-lb-empty">Никто ещё не играл — стань первым 🐦</div>';
    }
    const myTgId = window.state?.me?.tg_id;
    return rows.map((r, i) => {
      const rank = i + 1;
      const isMe = myTgId && Number(myTgId) === Number(r.tg_id);
      const name = r.first_name || r.username || `tg${r.tg_id}`;
      const sub  = r.username ? `@${r.username}` : `lvl ${r.level}`;
      const main = sort === 'best_run' ? r.best_run_pluma : r.pluma_lifetime;
      const mainLabel = sort === 'best_run' ? 'best run' : 'всего';
      const subStat = sort === 'best_run'
        ? `<span>${fmt(r.best_run_distance)} truck'ов</span>`
        : `<span>${fmt(r.runs_count)} ранов</span>`;
      const avatar = r.photo_url
        ? `<img src="${escape(r.photo_url)}" alt="" />`
        : '<span>🐦</span>';
      const rankClass = rank === 1 ? 'top1' : rank === 2 ? 'top2' : rank === 3 ? 'top3' : '';
      return `
        <div class="flappy-lb-row ${isMe ? 'me' : ''}">
          <div class="flappy-lb-rank ${rankClass}">${rank}</div>
          <div class="flappy-lb-avatar">${avatar}</div>
          <div class="flappy-lb-info">
            <div class="flappy-lb-name">${escape(name)} ${isMe ? '<span class="flappy-lb-you">ТЫ</span>' : ''}</div>
            <div class="flappy-lb-sub">${escape(sub)} · ${subStat}</div>
          </div>
          <div class="flappy-lb-pluma">
            <div class="flappy-lb-pluma-v">${fmtCompact(main)}</div>
            <div class="flappy-lb-pluma-l">${mainLabel}</div>
          </div>
        </div>
      `;
    }).join('');
  }

  // ═════════════════ GAME LOOP ═════════════════
  function startGame() {
    const map  = FS.cfg.maps.find(m => m.key === FS.state.current_map_id);
    const bird = FS.cfg.birds.find(b => b.key === FS.state.current_bird_id);
    const upgrades = FS.state.upgrades || {};
    const artifacts = new Set(FS.state.artifacts || []);

    const overlay = document.createElement('div');
    overlay.className = 'flappy-game-overlay';
    overlay.style.backgroundImage = `url('./img/flappy/maps/${map.image}')`;
    overlay.innerHTML = `
      <div class="flappy-game-hud">
        <div class="flappy-hud-stat"><div class="flappy-hud-l">DIST</div><div class="flappy-hud-v" id="fg-dist">0</div></div>
        <div class="flappy-hud-stat"><div class="flappy-hud-l">PLUMA</div><div class="flappy-hud-v gold" id="fg-pluma">0</div></div>
        <div class="flappy-hud-stat"><div class="flappy-hud-l">COMBO</div><div class="flappy-hud-v fire" id="fg-combo">0</div></div>
        <button class="flappy-hud-quit" id="fg-quit">END</button>
      </div>
      <div class="flappy-cashout-wrap">
        <button class="flappy-cashout" id="fg-cashout">💰 ЗАФИКСИТЬ ×<span id="fg-cm">1.5</span></button>
      </div>
      <canvas class="flappy-canvas" id="fg-canvas"></canvas>
      <div class="flappy-tap-hint" id="fg-tap-hint">ТАП = ВЗМАХ</div>
    `;
    document.body.appendChild(overlay);
    const canvas = document.getElementById('fg-canvas');
    const ctx = canvas.getContext('2d');
    const W = canvas.width  = window.innerWidth;
    const H = canvas.height = window.innerHeight;

    // Pre-load bird sprite
    const birdImg = new Image(); birdImg.src = `./img/flappy/birds/${bird.image}`;
    const coinImgs = {};
    for (const c of FS.cfg.coin_rarities) {
      const img = new Image(); img.src = `./img/flappy/coins/${c.image}`;
      coinImgs[c.key] = img;
    }
    const pipeImg = new Image(); pipeImg.src = './img/flappy/obstacles/pipe.png';

    // Effective stats
    const flapPower = 9.0 * (1 + (Number(upgrades.flap_power || 0) * 0.005));
    const gravity   = 0.45 * (1 - (Number(upgrades.lighter_lungs || 0) * 0.003)) * (bird.passive?.gravity_mult || 1.0);
    const startShields = Number(upgrades.start_shield || 0)
                       + Number(bird.passive?.start_shield_bonus || 0)
                       + (artifacts.has('starter_shield') ? 1 : 0);

    const G = {
      // Bird hitbox is intentionally smaller than the visual sprite (64×56,
      // visual radius ~32). r=18 gives ~14px of forgiveness so it FEELS fair —
      // grazing the pipe doesn't kill you. Earlier r=28 felt unfair.
      bird:    { x: W * 0.25, y: H * 0.5, vy: 0, r: 18 },
      pipes:   [],
      coins:   [],
      powerups:[],
      pipeGap: 200,
      pipeSpeed: 4,
      pipeSpawnInterval: 1700,
      lastPipe: 0,
      lastCoin: 0,
      lastPowerup: 0,
      score: 0,
      pluma: 0,
      combo: 0,
      bestCombo: 0,
      shields: startShields,
      coinsCollected: { bronze: 0, star: 0, crystal: 0, rainbow: 0 },
      gameStarted: false,
      gameOver: false,
      startedAt: performance.now(),
      lastT: performance.now(),
      pipeSpeedMult: (map.bonus?.pipe_speed_mult || 1.0),
      coinMult: {
        bronze:  map.bonus?.coin_mult_bronze  || 1.0,
        star:    map.bonus?.coin_mult_star    || 1.0,
        crystal: map.bonus?.coin_mult_crystal || 1.0,
        rainbow: map.bonus?.coin_mult_rainbow || 1.0,
      },
      magnetUntil: 0,
      slowmoUntil: 0,
      doubleCoinUntil: 0,
      rocketUntil: 0,
    };

    // Input
    const flap = () => {
      if (G.gameOver) return;
      G.gameStarted = true;
      G.bird.vy = -flapPower;
      tg?.HapticFeedback?.impactOccurred?.('light');
      const hint = document.getElementById('fg-tap-hint');
      if (hint) hint.classList.add('hidden');
    };
    const onTouch = e => { if (e.target.id === 'fg-quit' || e.target.id === 'fg-cashout') return; e.preventDefault(); flap(); };
    overlay.addEventListener('touchstart', onTouch, { passive: false });
    overlay.addEventListener('mousedown', onTouch);

    // Cashout button updates with progress
    const updCashout = () => {
      const m = cashOutMult(G.score);
      const el = document.getElementById('fg-cm');
      if (el) el.textContent = m.toFixed(1);
    };

    document.getElementById('fg-cashout').addEventListener('click', () => {
      const m = cashOutMult(G.score);
      endRun('cashout', m);
    });
    document.getElementById('fg-quit').addEventListener('click', () => endRun('manual', 1.0));

    // ── LOOP ──
    let raf;
    function loop(t) {
      if (G.gameOver) return;
      const dt = Math.min(50, t - G.lastT) / 16.6667;  // normalized 60fps
      G.lastT = t;

      // Auto-magnet from artifact
      const passiveMagnetR = artifacts.has('coin_magnet') ? 80 : 0;
      const magnetActive = t < G.magnetUntil;
      const magnetRadius = (magnetActive ? 250 : 0) + passiveMagnetR;
      const slowmo = t < G.slowmoUntil ? 0.5 : 1.0;
      const rocketActive = t < G.rocketUntil;

      // Spawn pipes
      const spawnInterval = G.pipeSpawnInterval / slowmo;
      if (G.gameStarted && t - G.lastPipe > spawnInterval) {
        G.lastPipe = t;
        const minGap = 90;
        const gapY = minGap + Math.random() * (H - 200 - G.pipeGap);
        G.pipes.push({ x: W + 100, gapY, gapH: G.pipeGap, passed: false });
      }
      // Spawn coins
      if (G.gameStarted && t - G.lastCoin > 600) {
        G.lastCoin = t;
        const r = pickRarity();
        G.coins.push({ x: W + 50, y: 100 + Math.random() * (H - 200), r, taken: false });
      }
      // Spawn power-ups (rare)
      if (G.gameStarted && t - G.lastPowerup > 8000 && Math.random() < 0.3) {
        G.lastPowerup = t;
        const p = FS.cfg.power_ups[Math.floor(Math.random() * FS.cfg.power_ups.length)];
        G.powerups.push({ x: W + 50, y: 100 + Math.random() * (H - 200), key: p.key, taken: false });
      }

      // Physics
      if (G.gameStarted) {
        G.bird.vy += gravity * dt * slowmo;
        G.bird.y += G.bird.vy * dt * slowmo;
        const speed = G.pipeSpeed * G.pipeSpeedMult * (rocketActive ? 1.8 : 1.0) * slowmo;

        // Move pipes
        for (const p of G.pipes) p.x -= speed * dt;
        // Move coins (auto-grab rainbow if artifact)
        for (const c of G.coins) {
          c.x -= speed * dt;
          // Magnet pull
          const dx = G.bird.x - c.x, dy = G.bird.y - c.y;
          const dist = Math.sqrt(dx*dx + dy*dy);
          if ((dist < magnetRadius) || (artifacts.has('black_hole') && c.r.key === 'rainbow')) {
            c.x += dx * 0.12;
            c.y += dy * 0.12;
          }
        }
        // Move powerups
        for (const p of G.powerups) p.x -= speed * dt;

        // Cull
        G.pipes    = G.pipes.filter(p => p.x > -100);
        G.coins    = G.coins.filter(c => c.x > -50 && !c.taken);
        G.powerups = G.powerups.filter(p => p.x > -50 && !p.taken);

        // Floor / ceiling
        if (G.bird.y > H - 30) {
          if (rocketActive) G.bird.y = H - 30;
          else { hit(); }
        }
        if (G.bird.y < 30) { G.bird.y = 30; G.bird.vy = 0; }

        // Pipe pass + collision
        for (const p of G.pipes) {
          // Score
          if (!p.passed && p.x + 50 < G.bird.x) {
            p.passed = true;
            G.score += 1;
            // Pluma per truck
            const baseTruck = 50 * (1 + (FS.state.level || 1) / 10);
            let truckPluma = baseTruck;
            // Combo
            G.combo += 1;
            G.bestCombo = Math.max(G.bestCombo, G.combo);
            const comboBonus = Math.min(10, 1 + G.combo * 0.05);
            truckPluma *= comboBonus;
            // Map combo bonus
            if (map.bonus?.combo_mult_bonus) truckPluma *= map.bonus.combo_mult_bonus;
            // Galaxy Dust artifact
            if (artifacts.has('galaxy_dust')) truckPluma *= 1.5;
            // Feather token
            if (artifacts.has('feather_token')) truckPluma *= 1.20;
            // Supernova every 50th
            if (artifacts.has('supernova') && G.score % 50 === 0) {
              truckPluma *= 100;
              flashScreen('#ffd700');
            }
            // Double coins powerup
            if (t < G.doubleCoinUntil) truckPluma *= 2;
            G.pluma += Math.floor(truckPluma);
            updCashout();
            tg?.HapticFeedback?.selectionChanged?.();
          }
          // Collision (5px inset on pipe rect — visual 60px, hit zone 50px,
          // matches the bird's smaller hitbox for a forgiving feel)
          if (!rocketActive && circleRect(G.bird, p.x + 5, 0, 50, p.gapY - 4)) hit();
          if (!rocketActive && circleRect(G.bird, p.x + 5, p.gapY + p.gapH + 4, 50, H - p.gapY - p.gapH - 4)) hit();
        }

        // Coins pickup
        for (const c of G.coins) {
          if (c.taken) continue;
          const dx = G.bird.x - c.x, dy = G.bird.y - c.y;
          if (Math.sqrt(dx*dx + dy*dy) < 35) {
            c.taken = true;
            G.coinsCollected[c.r.key] = (G.coinsCollected[c.r.key] || 0) + 1;
            let val = c.r.pluma * (G.coinMult[c.r.key] || 1);
            // Lucky strike (with artifact bonus)
            const luckyP = (Number(upgrades.lucky_strike || 0) * 0.005) + (artifacts.has('lucky_charm') ? 0.30 : 0);
            if (Math.random() < luckyP) val *= 2;
            // Crit pickup
            const critP = Number(upgrades.crit_pickup || 0) * 0.0015;
            if (Math.random() < critP) val *= 10;
            if (t < G.doubleCoinUntil) val *= 2;
            G.pluma += Math.floor(val);
            updCashout();
          }
        }
        // Power-ups pickup
        for (const p of G.powerups) {
          if (p.taken) continue;
          const dx = G.bird.x - p.x, dy = G.bird.y - p.y;
          if (Math.sqrt(dx*dx + dy*dy) < 40) {
            p.taken = true;
            applyPowerup(p.key, t);
          }
        }
      }

      // Render
      render(t);
      raf = requestAnimationFrame(loop);
    }

    function applyPowerup(key, t) {
      const dur = (FS.cfg.power_ups.find(p => p.key === key) || {}).duration_sec * 1000;
      const masterLvl = Number(upgrades.powerup_master || 0);
      const extDur = dur * (1 + masterLvl * 0.01);
      if (key === 'magnet')        G.magnetUntil = t + extDur;
      if (key === 'shield')        G.shields += 1;
      if (key === 'rocket')        G.rocketUntil = t + extDur;
      if (key === 'slowmo')        G.slowmoUntil = t + extDur;
      if (key === 'double_coins')  G.doubleCoinUntil = t + extDur;
      flashScreen('#ffd700');
      tg?.HapticFeedback?.notificationOccurred?.('success');
    }

    function hit() {
      // Phoenix resurrect
      if (artifacts.has('phoenix') && !G._phoenixUsed) {
        G._phoenixUsed = true;
        G.bird.vy = -flapPower;
        G.bird.y = Math.max(60, G.bird.y - 100);
        flashScreen('#ff8866');
        return;
      }
      // Shield
      if (G.shields > 0) {
        G.shields -= 1;
        G.bird.vy = -flapPower * 0.7;
        flashScreen('#5aa9ff');
        return;
      }
      G.combo = 0;
      endRun('pipe', 1.0);
    }

    function pickRarity() {
      const rarities = FS.cfg.coin_rarities;
      const total = rarities.reduce((s, r) => s + r.weight, 0);
      let roll = Math.random() * total;
      for (const r of rarities) {
        roll -= r.weight;
        if (roll <= 0) return r;
      }
      return rarities[0];
    }

    function circleRect(circle, rx, ry, rw, rh) {
      const cx = Math.max(rx, Math.min(circle.x, rx + rw));
      const cy = Math.max(ry, Math.min(circle.y, ry + rh));
      const dx = circle.x - cx, dy = circle.y - cy;
      return (dx*dx + dy*dy) < (circle.r * circle.r);
    }

    function flashScreen(color) {
      const f = document.createElement('div');
      f.style.cssText = `position:fixed;inset:0;background:${color};opacity:0.4;pointer-events:none;z-index:9999;animation:flapFlash 0.4s ease-out forwards`;
      document.body.appendChild(f);
      setTimeout(() => f.remove(), 400);
    }

    function render(t) {
      ctx.clearRect(0, 0, W, H);
      // Pipes
      for (const p of G.pipes) {
        ctx.fillStyle = '#3a8c3a';
        ctx.fillRect(p.x, 0, 60, p.gapY);
        ctx.fillRect(p.x, p.gapY + p.gapH, 60, H - p.gapY - p.gapH);
        ctx.strokeStyle = '#2a6c2a';
        ctx.lineWidth = 4;
        ctx.strokeRect(p.x, 0, 60, p.gapY);
        ctx.strokeRect(p.x, p.gapY + p.gapH, 60, H - p.gapY - p.gapH);
      }
      // Coins
      for (const c of G.coins) {
        if (c.taken) continue;
        const img = coinImgs[c.r.key];
        if (img && img.complete) ctx.drawImage(img, c.x - 20, c.y - 20, 40, 40);
        else { ctx.fillStyle = c.r.color; ctx.beginPath(); ctx.arc(c.x, c.y, 16, 0, 2*Math.PI); ctx.fill(); }
      }
      // Powerups
      for (const p of G.powerups) {
        ctx.fillStyle = '#fff';
        ctx.beginPath(); ctx.arc(p.x, p.y, 22, 0, 2*Math.PI); ctx.fill();
        ctx.fillStyle = '#000'; ctx.font = '20px sans-serif'; ctx.textAlign = 'center';
        const lbl = { magnet:'🧲', shield:'🛡', rocket:'🚀', slowmo:'⏰', double_coins:'💰' }[p.key] || '?';
        ctx.fillText(lbl, p.x, p.y + 7);
      }
      // Bird
      const ang = Math.max(-0.5, Math.min(1.0, G.bird.vy * 0.06));
      ctx.save();
      ctx.translate(G.bird.x, G.bird.y);
      ctx.rotate(ang);
      if (birdImg.complete) ctx.drawImage(birdImg, -32, -28, 64, 56);
      else { ctx.fillStyle = '#ffd700'; ctx.beginPath(); ctx.arc(0, 0, 24, 0, 2*Math.PI); ctx.fill(); }
      ctx.restore();
      // Shield indicator
      if (G.shields > 0) {
        ctx.strokeStyle = 'rgba(90,169,255,0.6)';
        ctx.lineWidth = 3;
        ctx.beginPath(); ctx.arc(G.bird.x, G.bird.y, 36, 0, 2*Math.PI); ctx.stroke();
      }
      // HUD updates
      document.getElementById('fg-dist').textContent = G.score;
      document.getElementById('fg-pluma').textContent = fmtCompact(G.pluma);
      document.getElementById('fg-combo').textContent = G.combo;
    }

    raf = requestAnimationFrame(loop);

    function endRun(deathReason, cashoutMult) {
      if (G.gameOver) return;
      G.gameOver = true;
      cancelAnimationFrame(raf);
      overlay.removeEventListener('touchstart', onTouch);
      overlay.removeEventListener('mousedown', onTouch);
      submitRun(G, map.key, bird.key, cashoutMult, deathReason)
        .then(res => showResult(overlay, G, res, cashoutMult, deathReason));
    }
  }

  function cashOutMult(trucks) {
    if (trucks < 10)  return 1.50;
    if (trucks < 50)  return 2.00;
    if (trucks < 100) return 2.50;
    if (trucks < 200) return 3.00;
    if (trucks < 500) return 4.00;
    return 5.00;
  }

  async function submitRun(G, mapKey, birdKey, cashoutMult, deathReason) {
    const dur = Math.max(1, Math.floor((performance.now() - G.startedAt) / 1000)) || 1;
    try {
      return await api('/api/flappy/run', {
        method: 'POST',
        body: JSON.stringify({
          distance: G.score,
          pluma_earned: G.pluma,
          coin_pickups: G.coinsCollected,
          duration_sec: dur,
          map_id: mapKey,
          bird_id: birdKey,
          best_combo: G.bestCombo,
          cashed_out: deathReason === 'cashout',
          cashout_mult: cashoutMult,
          died_to: deathReason,
        }),
      });
    } catch (e) {
      return { ok: false, error: e.message };
    }
  }

  function showResult(overlay, G, res, cashoutMult, deathReason) {
    const ok = res && res.ok;
    const credited = ok ? res.pluma_credited : 0;
    const errorMsg = (!ok && res && res.error) ? res.error : '';
    overlay.innerHTML = `
      <div class="flappy-result">
        <div class="flappy-result-head">${deathReason === 'cashout' ? '💰 ЗАФИКСИЛ!' : 'УПАЛ'}</div>
        ${ok
          ? `<div class="flappy-result-pluma">+${fmt(credited)}</div>
             <div class="flappy-result-pluma-l">Pluma на счёт</div>`
          : `<div class="flappy-result-pluma" style="color:#eb4b4b;font-size:24px">⚠ Не сохранён</div>
             <div class="flappy-result-pluma-l" style="color:#eb4b4b">${escape(errorMsg)}</div>`}
        <div class="flappy-result-stats">
          <div><b>${G.score}</b> truck'ов</div>
          <div><b>${G.bestCombo}</b> combo</div>
          ${deathReason === 'cashout' ? `<div>×${cashoutMult.toFixed(1)} cash-out</div>` : ''}
          ${res && res.is_first_today ? '<div class="bonus">🌅 Первый ран дня!</div>' : ''}
        </div>
        <div class="flappy-result-actions">
          <button id="fg-r-again">▶ Ещё ран</button>
          <button id="fg-r-menu">К меню</button>
        </div>
      </div>
    `;
    document.getElementById('fg-r-again').addEventListener('click', () => {
      overlay.remove();
      refresh().then(() => startGame());
    });
    document.getElementById('fg-r-menu').addEventListener('click', () => {
      overlay.remove();
      refresh();
    });
  }

  // ═════════════════ ACTIVATION ═════════════════
  document.addEventListener('DOMContentLoaded', () => {
    const taxView = document.querySelector('.view[data-view="flappy"]');
    if (!taxView) return;
    const obs = new MutationObserver(() => {
      if (taxView.classList.contains('active')) boot();
    });
    obs.observe(taxView, { attributes: true, attributeFilter: ['class'] });
  });
})();
