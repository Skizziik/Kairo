/* ═══════════════════════════════════════════════════════════════
   JACKPOT — pool-style PvP mini-game (CS-case spinner)
   Connects to globals: state, api, fmt, escape, toast, tg, showView
   ═══════════════════════════════════════════════════════════════ */
(function() {
  'use strict';

  // ----- module state -----
  const JS_ = {
    pollTimer:    null,
    activeRound:  null,    // current /current response
    activeTab:    'play',  // play | history
    spinShownFor: null,    // round_id we've already animated
    inventoryCache: null,
  };

  // ----- main entry -----
  window.jackpotEnter = async function() {
    try { window.Telegram?.WebApp?.expand?.(); } catch (e) {}
    const root = document.getElementById('jackpot-app');
    root.innerHTML = '<div class="loader">Загрузка...</div>';
    await refresh(true);
    if (JS_.pollTimer) clearInterval(JS_.pollTimer);
    JS_.pollTimer = setInterval(() => {
      // Only run when Jackpot view is ACTUALLY active. The DOM node always
      // exists (other views just get display:none), so checking #jackpot-app
      // wasn't enough — toasts were firing for users browsing Inventory etc.
      if (!document.querySelector('.view[data-view="jackpot"].active')) return;
      // Pause polling while a deposit modal or verify modal is open
      if (document.querySelector('.jp-deposit-modal') ||
          document.querySelector('.jp-verify-modal')) return;
      refresh(false);
    }, 1500);
  };
  window.jackpotLeave = function() {
    if (JS_.pollTimer) { clearInterval(JS_.pollTimer); JS_.pollTimer = null; }
  };

  async function refresh(initial) {
    try {
      const cur = await api('/api/jackpot/current');
      const sameRound = JS_.activeRound && cur.id === JS_.activeRound.id;
      JS_.activeRound = cur;
      if (initial || !sameRound) {
        renderHub();
      } else {
        softRefresh();
      }
    } catch (e) {
      const root = document.getElementById('jackpot-app');
      if (root && initial) root.innerHTML = `<div class="loader">Ошибка: ${escape(e.message)}</div>`;
    }
  }

  // ----- main render -----
  function renderHub() {
    const root = document.getElementById('jackpot-app');
    if (!root) return;
    root.innerHTML = `
      <div class="jp-hub">
        <div class="jp-tabs">
          <button class="jp-tab" data-jp-tab="play">▶ Раунд</button>
          <button class="jp-tab" data-jp-tab="history">🏆 История</button>
          <button class="jp-tab" data-jp-tab="rules">ℹ Правила</button>
        </div>
        <div id="jp-tab-content"></div>
      </div>
    `;
    root.querySelectorAll('[data-jp-tab]').forEach(b => {
      b.addEventListener('click', () => switchTab(b.dataset.jpTab));
    });
    switchTab(JS_.activeTab);
  }

  function switchTab(tab) {
    JS_.activeTab = tab;
    document.querySelectorAll('.jp-tab').forEach(b =>
      b.classList.toggle('active', b.dataset.jpTab === tab));
    const c = document.getElementById('jp-tab-content');
    if (!c) return;
    if (tab === 'play')    paintPlay(c);
    if (tab === 'history') paintHistory(c);
    if (tab === 'rules')   paintRules(c);
  }

  // ═════════════════ PLAY TAB ═════════════════
  function paintPlay(c) {
    const r = JS_.activeRound;
    if (!r) {
      c.innerHTML = '<div class="loader">Раунд готовится...</div>';
      return;
    }
    if (r.status === 'idle') {
      c.innerHTML = '<div class="loader">Раунд скоро начнётся...</div>';
      return;
    }

    const isPending  = r.status === 'pending';
    const isSpinning = r.status === 'spinning';
    const isSettled  = r.status === 'settled' || r.status === 'cancelled';
    const seconds    = Math.max(0, r.server_seconds_left || 0);
    const timer      = Math.floor(seconds);
    const timerStr   = isPending ? formatSeconds(timer)
                      : isSpinning ? 'СПИН' : 'ОЖИДАНИЕ';
    const totalValue = r.total_value || 0;
    const deposits   = r.deposits || [];

    const hashShort = (r.server_seed_hash || '').slice(0, 12);

    c.innerHTML = `
      <div class="jp-header">
        <div class="jp-round-id">Раунд #${r.id}</div>
        <div class="jp-pool" id="jp-pool">${fmt(totalValue)} 🪙</div>
        <div class="jp-pool-label">Общий пул</div>
        <div class="jp-timer ${timer <= 10 && isPending ? 'urgent' : ''}" id="jp-timer">
          ${isPending ? '⏱' : isSpinning ? '🎰' : '⏸'} <b>${timerStr}</b>
        </div>
        <div class="jp-status-banner" id="jp-status-banner">
          ${isPending ? 'Принимаются ставки' : isSpinning ? 'Крутится спинер...' : (r.status === 'settled' ? 'Завершён' : 'Отменён')}
        </div>
      </div>

      <div class="jp-spinner-wrap" id="jp-spinner-wrap">
        <div class="jp-spinner-pointer"></div>
        <div class="jp-spinner-strip" id="jp-spinner-strip"></div>
      </div>

      <div class="jp-participants" id="jp-participants">
        ${renderParticipants(deposits, totalValue)}
      </div>

      <button class="jp-deposit-btn" id="jp-deposit-btn" ${isPending ? '' : 'disabled'}>
        💎 Поставить (скины + монеты)
      </button>

      <div style="text-align:center">
        <span class="jp-fair-pill" id="jp-fair-pill" title="Provably fair — кликни для деталей">
          🔒 hash ${hashShort}...
        </span>
      </div>
    `;

    document.getElementById('jp-deposit-btn').addEventListener('click', () => {
      if (!isPending) return;
      openDepositModal();
    });
    document.getElementById('jp-fair-pill').addEventListener('click', () => {
      openVerifyModal(r.id);
    });

    // Render initial spinner strip preview
    renderInitialSpinner(deposits);

    // If settled & not yet animated → trigger spin animation
    if (r.status === 'spinning' && r.spin_sequence && JS_.spinShownFor !== r.id) {
      JS_.spinShownFor = r.id;
      runSpinAnimation(r.spin_sequence, deposits, r.winner_id);
    }
  }

  // Render placeholder strip showing current participants (animation off)
  function renderInitialSpinner(deposits) {
    const strip = document.getElementById('jp-spinner-strip');
    if (!strip) return;
    if (!deposits || deposits.length === 0) {
      strip.innerHTML = '<div style="margin:auto; color:var(--text-dim); padding:0 16px; font-size:12px">Жду первых депозитов...</div>';
      return;
    }
    // Repeat deposits to fill the strip
    const tiles = [];
    const tileCount = 12;
    for (let i = 0; i < tileCount; i++) {
      const d = deposits[i % deposits.length];
      tiles.push(tileHtml(d));
    }
    strip.innerHTML = tiles.join('');
    strip.classList.remove('spinning');
    strip.style.transform = 'translateY(-50%) translateX(0)';
  }

  function tileHtml(d, isWinner) {
    const name = d.name || d.display_name || '?';
    const initial = name.charAt(0).toUpperCase();
    const isBot = d.is_bot;
    const visibleName = isBot ? name.replace('🤖 ', '') : name;
    const avatarInner = (!isBot && d.avatar_url)
      ? `<img src="${d.avatar_url}" alt="" />`
      : (isBot ? '🤖' : escape(initial));
    return `
      <div class="jp-tile ${isWinner ? 'winner' : ''}" style="--tile-color:${d.color || '#5cc15c'}">
        <div class="jp-tile-avatar">${avatarInner}</div>
        <div class="jp-tile-name">${escape(visibleName)}</div>
      </div>
    `;
  }

  function renderParticipants(deposits, totalValue) {
    if (!deposits || deposits.length === 0) {
      return '<div class="loader" style="font-size:13px; padding:18px">Никто пока не депозитил...</div>';
    }
    // Aggregate: real users by user_id; bots by user_id + bot_name (because
    // ALL bots share BOT_USER_ID=1, but Hydra/Cobra/Mamba are different
    // personalities — without name in key they all merged into one row).
    const byUser = new Map();
    for (const d of deposits) {
      const key = d.is_bot
        ? `bot:${d.bot_name || d.name || 'unknown'}`
        : `user:${d.user_id}`;
      if (!byUser.has(key)) {
        byUser.set(key, {
          user_id: d.user_id,
          name:    d.name || d.display_name || '?',
          color:   d.color,                   // first deposit's color wins
          is_bot:  !!d.is_bot,
          avatar_url: d.avatar_url,
          value:   0,
          coins:   0,
          skins_count: 0,
          deposits_count: 0,
        });
      }
      const agg = byUser.get(key);
      agg.value += d.value || 0;
      agg.coins += d.coins || 0;
      agg.skins_count += (d.inventory_ids && d.inventory_ids.length) || 0;
      agg.deposits_count += 1;
    }
    const aggregated = Array.from(byUser.values()).sort((a, b) => b.value - a.value);

    return aggregated.map(d => {
      const name = d.name;
      const pct = totalValue > 0 ? ((d.value / totalValue) * 100).toFixed(1) : '0';
      const initial = name.charAt(0).toUpperCase();
      const breakdown = [];
      if (d.skins_count) breakdown.push(`${d.skins_count} скин${d.skins_count === 1 ? '' : d.skins_count < 5 ? 'а' : 'ов'}`);
      if (d.coins)       breakdown.push(`${fmt(d.coins)} 🪙`);
      if (d.deposits_count > 1) breakdown.push(`(${d.deposits_count} депозита)`);
      const breakStr = breakdown.length ? breakdown.join(' · ') : '0';
      const avatarHtml = (!d.is_bot && d.avatar_url)
        ? `<img src="${d.avatar_url}" alt="" />`
        : (d.is_bot ? '🤖' : escape(initial));
      return `
        <div class="jp-participant" style="--p-color:${d.color}">
          <div class="jp-p-avatar">${avatarHtml}</div>
          <div>
            <div class="jp-p-name">${escape(name)}</div>
            <div class="jp-p-skins-count">${escape(breakStr)}</div>
          </div>
          <div class="jp-p-value">${fmt(d.value)}</div>
          <div class="jp-p-pct">${pct}%</div>
        </div>
      `;
    }).join('');
  }

  // Soft live update of timer + pool + participants
  function softRefresh() {
    const r = JS_.activeRound;
    if (!r) return;
    const seconds = Math.max(0, r.server_seconds_left || 0);
    const timer   = Math.floor(seconds);
    const timerEl = document.getElementById('jp-timer');
    if (timerEl) {
      timerEl.classList.toggle('urgent', timer <= 10 && r.status === 'pending');
      const inner = r.status === 'pending'  ? formatSeconds(timer)
                  : r.status === 'spinning' ? 'СПИН'
                  : 'ОЖИДАНИЕ';
      const icon  = r.status === 'pending'  ? '⏱'
                  : r.status === 'spinning' ? '🎰' : '⏸';
      timerEl.innerHTML = `${icon} <b>${inner}</b>`;
    }
    const poolEl = document.getElementById('jp-pool');
    if (poolEl) poolEl.textContent = `${fmt(r.total_value || 0)} 🪙`;
    const banner = document.getElementById('jp-status-banner');
    if (banner) {
      banner.textContent = r.status === 'pending'  ? 'Принимаются ставки'
                         : r.status === 'spinning' ? 'Крутится спинер...'
                         : (r.status === 'settled' ? 'Завершён' : 'Отменён');
    }
    const part = document.getElementById('jp-participants');
    if (part) part.innerHTML = renderParticipants(r.deposits || [], r.total_value || 0);

    // Maybe re-render strip preview if not currently spinning
    const strip = document.getElementById('jp-spinner-strip');
    if (strip && !strip.classList.contains('spinning') && r.status === 'pending') {
      renderInitialSpinner(r.deposits || []);
    }

    // Trigger spin animation when round transitions to spinning
    if (r.status === 'spinning' && r.spin_sequence && JS_.spinShownFor !== r.id) {
      JS_.spinShownFor = r.id;
      runSpinAnimation(r.spin_sequence, r.deposits || [], r.winner_id);
    }

    // Update deposit button state
    const btn = document.getElementById('jp-deposit-btn');
    if (btn) btn.disabled = r.status !== 'pending';
  }

  // ═════════════════ SPIN ANIMATION ═════════════════
  function runSpinAnimation(sequence, deposits, winnerId) {
    const strip = document.getElementById('jp-spinner-strip');
    const wrap  = document.getElementById('jp-spinner-wrap');
    if (!strip || !wrap) return;

    // Build tiles for full sequence
    const tilesHtml = sequence.map((s, i) => {
      const sname = s.name || '?';
      const visible = s.is_bot ? sname.replace('🤖 ', '') : sname;
      const inner = (!s.is_bot && s.avatar_url)
        ? `<img src="${s.avatar_url}" alt="" />`
        : (s.is_bot ? '🤖' : escape(sname.charAt(0).toUpperCase()));
      return `<div class="jp-tile" style="--tile-color:${s.color}" data-idx="${i}">
        <div class="jp-tile-avatar">${inner}</div>
        <div class="jp-tile-name">${escape(visible)}</div>
      </div>`;
    }).join('');
    strip.innerHTML = tilesHtml;
    // Reset transition / position for clean start
    strip.classList.remove('spinning');
    strip.style.transition = 'none';
    strip.style.transform  = 'translateY(-50%) translateX(0)';

    // Force reflow so the next class addition triggers transition
    void strip.offsetWidth;

    // Compute target translateX so winner tile (at SPIN_WINNER_INDEX = 44) lands under the pointer.
    // Tile width 86px + 4px gap = 90px each; pointer at center (wrap.width / 2)
    const TILE_W = 90;
    const winnerIdx = 44;
    const wrapWidth = wrap.clientWidth;
    // Position of the winner tile center, in strip coordinates
    const winnerCenter = winnerIdx * TILE_W + (86 / 2) + 4;
    // We want winnerCenter to align with wrapWidth / 2
    const targetX = -(winnerCenter - wrapWidth / 2);
    // Add a small jitter (±15px) so it's not always perfectly centered (more realistic)
    const jitter = (Math.random() - 0.5) * 30;

    // Kick off transition
    strip.style.transition = '';
    strip.classList.add('spinning');
    strip.style.transform = `translateY(-50%) translateX(${targetX + jitter}px)`;

    // After animation: highlight winner + confetti + balance refresh
    setTimeout(async () => {
      const winnerTile = strip.querySelector(`[data-idx="${winnerIdx}"]`);
      if (winnerTile) winnerTile.classList.add('winner');
      tg?.HapticFeedback?.notificationOccurred?.('success');
      launchConfetti(wrap);
      const winnerDep = deposits.find(d => Number(d.user_id) === Number(winnerId));
      if (winnerDep) {
        const wname = winnerDep.name || winnerDep.display_name || '?';
        const pct = JS_.activeRound && JS_.activeRound.total_value
                  ? ((winnerDep.value / JS_.activeRound.total_value) * 100).toFixed(1)
                  : '?';
        toast(`🏆 ${wname} забирает ${fmt(JS_.activeRound.total_value)} 🪙 (${pct}%)`, 4500);
      }
      // Refresh authoritative balance — server settled in DB by now and
      // winner's balance jumped (or loser stayed). UI must reflect that
      // so player doesn't see a stale number.
      await syncBalanceFromServer();
    }, 5050);
  }

  function launchConfetti(container) {
    const colors = ['#ffd700', '#eb4b4b', '#5aa9ff', '#5cc15c', '#d32ce6', '#ff6b35'];
    for (let i = 0; i < 26; i++) {
      const el = document.createElement('div');
      el.className = 'jp-confetti';
      el.style.left = Math.random() * container.clientWidth + 'px';
      el.style.background = colors[i % colors.length];
      el.style.animationDelay = (Math.random() * 0.4) + 's';
      el.style.animationDuration = (1.6 + Math.random() * 0.8) + 's';
      container.appendChild(el);
      setTimeout(() => el.remove(), 2400);
    }
  }

  // ═════════════════ DEPOSIT MODAL ═════════════════
  async function openDepositModal() {
    const modal = document.createElement('div');
    modal.className = 'jp-deposit-modal';
    modal.innerHTML = `
      <div class="jp-deposit-card">
        <h3 style="margin-bottom:10px">💎 Поставить в раунд</h3>
        <div class="jp-deposit-tabs">
          <button class="jp-deposit-tab active" data-dep-tab="coins">🪙 Монеты</button>
          <button class="jp-deposit-tab" data-dep-tab="skins">🎒 Скины</button>
        </div>

        <div class="jp-deposit-section active" data-dep-section="coins">
          <input class="jp-coin-input" type="text" inputmode="numeric" id="jp-coin-input" placeholder="0" />
          <div class="jp-coin-presets">
            <button class="jp-coin-preset" data-coin="1000">1K</button>
            <button class="jp-coin-preset" data-coin="5000">5K</button>
            <button class="jp-coin-preset" data-coin="25000">25K</button>
            <button class="jp-coin-preset" data-coin="100000">100K</button>
            <button class="jp-coin-preset" data-coin="500000">500K</button>
            <button class="jp-coin-preset" data-coin="MAX">MAX</button>
          </div>
        </div>

        <div class="jp-deposit-section" data-dep-section="skins">
          <div id="jp-skins-grid" class="jp-skins-grid">
            <div class="loader">Загрузка инвентаря...</div>
          </div>
        </div>

        <div class="jp-deposit-summary" id="jp-dep-summary">
          Будет поставлено: <b>0</b> 🪙
        </div>

        <div class="jp-deposit-actions">
          <button class="jp-deposit-cancel" id="jp-dep-cancel">Отмена</button>
          <button class="jp-deposit-confirm" id="jp-dep-confirm" disabled>💎 Поставить</button>
        </div>
      </div>
    `;
    document.body.appendChild(modal);

    const selectedSkins = new Set();
    let coinAmount = 0;
    let depositMode = 'coins';

    const updateSummary = () => {
      const skinsValue = computeSelectedSkinsValue(selectedSkins);
      const total = coinAmount + skinsValue;
      const sumEl = modal.querySelector('#jp-dep-summary');
      const confirmBtn = modal.querySelector('#jp-dep-confirm');
      sumEl.innerHTML = `Будет поставлено: <b>${fmt(total)}</b> 🪙`;
      confirmBtn.disabled = total < 1000;
    };

    // Tab switching
    modal.querySelectorAll('[data-dep-tab]').forEach(t => {
      t.addEventListener('click', () => {
        depositMode = t.dataset.depTab;
        modal.querySelectorAll('[data-dep-tab]').forEach(tt => tt.classList.toggle('active', tt === t));
        modal.querySelectorAll('[data-dep-section]').forEach(s =>
          s.classList.toggle('active', s.dataset.depSection === depositMode));
      });
    });

    // Coin input
    const coinInput = modal.querySelector('#jp-coin-input');
    coinInput.addEventListener('input', (e) => {
      let v = parseInt(e.target.value.replace(/\D/g, '')) || 0;
      if (v > 10_000_000) v = 10_000_000;
      coinAmount = v;
      e.target.value = v ? fmt(v) : '';
      updateSummary();
    });
    modal.querySelectorAll('.jp-coin-preset').forEach(b => {
      b.addEventListener('click', () => {
        if (b.dataset.coin === 'MAX') {
          coinAmount = Math.min(10_000_000, (window.state?.me?.balance || 0));
        } else {
          coinAmount = parseInt(b.dataset.coin);
        }
        coinInput.value = coinAmount ? fmt(coinAmount) : '';
        updateSummary();
      });
    });

    // Load inventory for skins tab
    loadSkinsForDeposit(modal, selectedSkins, updateSummary);

    // Cancel
    modal.querySelector('#jp-dep-cancel').addEventListener('click', () => modal.remove());

    // Confirm
    modal.querySelector('#jp-dep-confirm').addEventListener('click', async () => {
      const confirmBtn = modal.querySelector('#jp-dep-confirm');
      confirmBtn.disabled = true;
      confirmBtn.textContent = 'Ставится...';
      try {
        const inv_ids = Array.from(selectedSkins);
        const r = await api('/api/jackpot/deposit', {
          method: 'POST',
          body: JSON.stringify({ inventory_ids: inv_ids, coins: coinAmount }),
        });
        if (!r || !r.ok) {
          toast(r && r.error || 'Ошибка');
          confirmBtn.disabled = false;
          confirmBtn.textContent = '💎 Поставить';
          return;
        }
        toast(`Ставка ${fmt(r.value)} 🪙 принята! Удачи 🍀`);
        tg?.HapticFeedback?.notificationOccurred?.('success');
        modal.remove();
        // Reset inventory cache so re-opening modal reflects locked items
        JS_.inventoryCache = null;
        // Authoritative balance refresh — never trust client subtraction
        await syncBalanceFromServer();
        refresh(false);
      } catch (e) {
        toast('Ошибка: ' + e.message);
        confirmBtn.disabled = false;
        confirmBtn.textContent = '💎 Поставить';
      }
    });
  }

  // Pulls fresh balance from server and updates the top bar. Called after any
  // event that affects coin balance (deposit, settle, refund) so UI never
  // drifts from authoritative state.
  async function syncBalanceFromServer() {
    try {
      const me = await api('/api/me');
      if (me && typeof me.balance === 'number' && window.state) {
        window.state.me = me;
        const balEl = document.getElementById('balance-display');
        if (balEl) balEl.textContent = fmt(me.balance);
      }
    } catch (e) { /* ignore */ }
  }

  async function loadSkinsForDeposit(modal, selectedSkins, updateSummary) {
    let inv = JS_.inventoryCache;
    if (!inv) {
      try {
        inv = await api('/api/inventory');
        JS_.inventoryCache = inv;
      } catch (e) {
        modal.querySelector('#jp-skins-grid').innerHTML =
          `<div class="loader" style="grid-column: 1/-1">Ошибка загрузки: ${escape(e.message)}</div>`;
        return;
      }
    }
    const items = (inv && inv.items) || [];
    const usable = items.filter(i =>
      !i.locked && !i.coinflip_lobby_id);
    if (!usable.length) {
      modal.querySelector('#jp-skins-grid').innerHTML =
        '<div class="loader" style="grid-column: 1/-1; font-size:12px">Нет свободных скинов для депозита</div>';
      return;
    }
    usable.sort((a, b) => b.price - a.price);
    const grid = modal.querySelector('#jp-skins-grid');
    grid.innerHTML = usable.map(it => {
      const parts = (it.name || '').split('|').map(s => s.trim());
      const skin = parts[1] || it.name;
      return `
        <div class="jp-skin-tile" data-skin-id="${it.id}">
          <img src="${it.image_url}" alt="" loading="lazy" />
          <div class="jp-skin-tile-name">${escape(skin)}</div>
          <div class="jp-skin-tile-price">${fmt(it.price)} 🪙</div>
        </div>
      `;
    }).join('');
    grid.querySelectorAll('[data-skin-id]').forEach(tile => {
      tile.addEventListener('click', () => {
        const id = parseInt(tile.dataset.skinId);
        if (selectedSkins.has(id)) {
          selectedSkins.delete(id);
          tile.classList.remove('selected');
        } else {
          if (selectedSkins.size >= 10) return toast('Максимум 10 скинов');
          selectedSkins.add(id);
          tile.classList.add('selected');
        }
        updateSummary();
      });
    });
  }

  function computeSelectedSkinsValue(selectedSkins) {
    if (!JS_.inventoryCache) return 0;
    const items = (JS_.inventoryCache.items || []).filter(i => selectedSkins.has(i.id));
    return items.reduce((acc, i) => acc + (i.price || 0), 0);
  }

  // ═════════════════ HISTORY ═════════════════
  async function paintHistory(c) {
    c.innerHTML = '<div class="loader">Загрузка истории...</div>';
    try {
      const list = await api('/api/jackpot/history?limit=50');
      if (!list.length) {
        c.innerHTML = '<div class="loader">История пустая — будь первым!</div>';
        return;
      }
      c.innerHTML = `<div class="jp-history-list">${list.map(r => {
        const cancelled = r.status === 'cancelled';
        return `
          <div class="jp-history-row ${cancelled ? 'cancelled' : ''}">
            <div class="jp-history-id">#${r.id}</div>
            <div class="jp-history-winner">
              ${cancelled ? '— отменён' : '🏆 ' + escape(r.winner_name || '—')}
            </div>
            <div class="jp-history-value">${fmt(r.total_value)}</div>
            <button class="jp-history-verify" data-verify="${r.id}">verify</button>
          </div>
        `;
      }).join('')}</div>`;
      c.querySelectorAll('[data-verify]').forEach(b => {
        b.addEventListener('click', () => openVerifyModal(parseInt(b.dataset.verify)));
      });
    } catch (e) {
      c.innerHTML = `<div class="loader">Ошибка: ${escape(e.message)}</div>`;
    }
  }

  // ═════════════════ VERIFY MODAL ═════════════════
  async function openVerifyModal(roundId) {
    const modal = document.createElement('div');
    modal.className = 'jp-verify-modal';
    modal.innerHTML = `
      <div class="jp-verify-card">
        <h3>🔒 Provably Fair — Раунд #${roundId}</h3>
        <div id="jp-verify-content">Загрузка...</div>
        <button class="jp-verify-close">Закрыть</button>
      </div>
    `;
    document.body.appendChild(modal);
    modal.querySelector('.jp-verify-close').addEventListener('click', () => modal.remove());
    modal.addEventListener('click', (e) => {
      if (e.target === modal) modal.remove();
    });

    try {
      const v = await api(`/api/jackpot/verify/${roundId}`);
      const content = modal.querySelector('#jp-verify-content');
      if (!v.ok) {
        content.innerHTML = `<div style="color:var(--text-dim)">${escape(v.error || 'Раунд ещё не завершён')}</div>`;
        return;
      }

      const matchClass = v.hash_matches ? 'jp-verify-ok' : 'jp-verify-bad';
      const matchTxt   = v.hash_matches ? '✅ совпадает' : '❌ НЕ совпадает!';
      const winnerRange = v.ranges.find(r => r.user_id === v.winner_id);

      content.innerHTML = `
        <div class="jp-verify-row">
          <b>server_seed_hash</b> (опубликован при старте раунда):
          <pre>${escape(v.server_seed_hash || '—')}</pre>
        </div>
        <div class="jp-verify-row">
          <b>server_seed</b> (раскрыт после settle):
          <pre>${escape(v.server_seed || '—')}</pre>
        </div>
        <div class="jp-verify-row">
          <b>SHA256(server_seed)</b>:
          <pre>${escape(v.verified_hash || '—')}</pre>
        </div>
        <div class="jp-verify-row">
          Сверка hash: <span class="${matchClass}">${matchTxt}</span>
        </div>
        <hr style="border-color: var(--border); margin: 10px 0">
        <div class="jp-verify-row"><b>total_value</b>: ${fmt(v.total_value)}</div>
        <div class="jp-verify-row"><b>Формула:</b></div>
        <pre>${escape(v.formula)}</pre>
        <div class="jp-verify-row"><b>winning_ticket</b>: ${v.winning_ticket}</div>
        ${v.stored_ticket !== null ? `<div class="jp-verify-row" style="color:var(--text-dim)">stored_ticket: ${v.stored_ticket} ${v.stored_ticket === v.winning_ticket ? '<span class="jp-verify-ok">✅</span>' : '<span class="jp-verify-bad">❌</span>'}</div>` : ''}
        <hr style="border-color: var(--border); margin: 10px 0">
        <div class="jp-verify-row"><b>Диапазоны участников:</b></div>
        ${v.ranges.map(r =>
          `<div class="jp-verify-row" style="font-size:10px; padding:3px 0; ${r.user_id === v.winner_id ? 'background: rgba(255,215,0,0.1); padding: 4px 6px; border-radius: 4px' : ''}">
            ${escape(r.name || '?')}: <code>${r.from} — ${r.to}</code> (${fmt(r.value)})
            ${r.user_id === v.winner_id ? ' 🏆' : ''}
          </div>`
        ).join('')}
        ${winnerRange ? `<div class="jp-verify-row" style="margin-top:8px"><span class="jp-verify-ok">✅ Тикет ${v.winning_ticket} попал в диапазон ${winnerRange.from}—${winnerRange.to} → победитель ${escape(winnerRange.name || '')}</span></div>` : ''}
      `;
    } catch (e) {
      const content = modal.querySelector('#jp-verify-content');
      if (content) content.innerHTML = `<div style="color:#eb4b4b">Ошибка: ${escape(e.message)}</div>`;
    }
  }

  // ═════════════════ RULES TAB ═════════════════
  function paintRules(c) {
    c.innerHTML = `
      <div style="background:var(--bg-card); border:1px solid var(--border); border-radius:14px; padding:16px; line-height:1.6">
        <h3 style="margin-bottom:10px">🎰 Как играть</h3>
        <p style="font-size:13px; color:var(--text-dim); margin-bottom:10px">
          Каждые 60 секунд начинается новый раунд. Игроки кидают монеты или скины в общий пул.
          После окончания таймера — крутится спинер и выбирается победитель.
        </p>
        <p style="font-size:13px; color:var(--text-dim); margin-bottom:10px">
          <b>Шанс выиграть = твоя ставка ÷ общий пул × 100%</b>. Чем больше поставил — тем больше шанс.
        </p>
        <p style="font-size:13px; color:var(--text-dim); margin-bottom:10px">
          Правила:<br>
          • Минимальная ставка: <b>1 000 🪙</b><br>
          • Максимум на раунд от одного игрока: <b>10 000 000 🪙</b><br>
          • Максимум 10 скинов за один депозит<br>
          • При &lt;2 депозитах раунд отменяется и всё возвращается
        </p>
        <h3 style="margin-top:16px">🔒 Provably Fair</h3>
        <p style="font-size:13px; color:var(--text-dim); margin-bottom:10px">
          Каждый раунд имеет уникальный <b>server_seed</b>. Его SHA256-хэш публикуется
          в момент старта (до того как игроки делают ставки). После окончания раунда
          сам seed раскрывается. Любой может проверить:
        </p>
        <pre style="background:rgba(0,0,0,0.4); padding:8px; border-radius:6px; font-size:10px; font-family:monospace">
SHA256(server_seed) == server_seed_hash    ← должно совпасть

ticket = int(SHA256("&lt;round_id&gt;:&lt;server_seed&gt;")[:13], 16) % total_value
        </pre>
        <p style="font-size:13px; color:var(--text-dim); margin-top:10px">
          Тикет выпадает в диапазон одного из участников — он и выиграл.
          Тыкни <b>verify</b> в истории — увидишь все цифры и подтверждение.
        </p>
      </div>
    `;
  }

  function formatSeconds(s) {
    if (s < 0) s = 0;
    const m = Math.floor(s / 60);
    const ss = s % 60;
    return `${m}:${ss.toString().padStart(2, '0')}`;
  }
})();
