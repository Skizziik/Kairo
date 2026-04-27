/* ═══════════════════════════════════════════════════════════════
   SNAKE MINI-GAME — full client.
   Connects to global `state` (window.state), `api`, `fmt`, `escape`,
   `toast`, `tg`, `showView` from app.js.
   ═══════════════════════════════════════════════════════════════ */
(function() {
  'use strict';

  // ----- module state -----
  const SS = {
    cfg: null,            // /api/snake/config response (cached for session)
    state: null,          // /api/snake/state response (cached & refreshed)
    activeTab: 'play',
    activeBranch: 'body',
    selectedMode: 'classic',
    selectedMap: 'park',
    afkRefreshTimer: null,
  };

  // Expose entry point for the global tab system
  window.snakeEnter = async function() {
    // Set background-aware Telegram WebApp expansion
    try { window.Telegram?.WebApp?.expand?.(); } catch (e) {}
    const root = document.getElementById('snake-app');
    root.innerHTML = '<div class="loader">Загрузка...</div>';
    try {
      if (!SS.cfg) SS.cfg = await api('/api/snake/config');
      SS.state = await api('/api/snake/state');
      SS.selectedMap = SS.state.current_map_id || 'park';
      paintHub();
      // Auto-refresh AFK panel every 30s while user is on snake page
      if (SS.afkRefreshTimer) clearInterval(SS.afkRefreshTimer);
      SS.afkRefreshTimer = setInterval(async () => {
        if (!document.querySelector('.snake-hub')) return;  // user navigated away
        if (document.querySelector('.snake-game-overlay')) return; // mid-run, don't poke
        try {
          const fresh = await api('/api/snake/state');
          SS.state = fresh;
          softRefresh();
        } catch (e) {}
      }, 5000);
    } catch (e) {
      root.innerHTML = '<div class="loader">Ошибка: ' + escape(e.message) + '</div>';
    }
  };

  // In-place update of "live" numbers without re-rendering tab content.
  // Lets the AFK farm visibly accumulate every poll without scroll-jump.
  function softRefresh() {
    const s = SS.state;
    if (!s) return;
    // Top casino balance
    if (typeof s.balance === 'number' && window.state && window.state.me) {
      window.state.me.balance = s.balance;
      const balEl = document.getElementById('balance-display');
      if (balEl) balEl.textContent = fmt(s.balance);
    }
    // Hub stat: AFK rate (lives in the .snake-hub-top grid)
    const afkStatEl = document.querySelector('[data-snake-stat="afk-rate"]');
    if (afkStatEl) afkStatEl.textContent = fmtCompact(s.afk_rate_per_min || 0);
    // Hub stat: lifetime coins
    const lifeEl = document.querySelector('[data-snake-stat="lifetime"]');
    if (lifeEl) lifeEl.textContent = fmtCompact(s.coins_lifetime);
    // Terrarium banner — AFK rate, daily cap progress
    if (SS.activeTab === 'terrarium') {
      const rateEl = document.querySelector('.snake-afk-rate-value');
      if (rateEl) rateEl.textContent = fmt((s.afk_rate_per_min || 0).toFixed(1)) + ' / мин';
      const earnedEl = document.querySelector('.snake-afk-cap-text');
      const cap = s.afk_cap_today || 1;
      const earned = s.daily_afk_earned || 0;
      if (earnedEl) earnedEl.textContent = fmt(earned) + ' / ' + fmt(cap) + ' (дневной кап)';
      const fillEl = document.querySelector('.snake-afk-cap-fill');
      if (fillEl) fillEl.style.width = Math.min(100, (earned / cap) * 100) + '%';
    }
    // Level XP bar
    const span = Math.max(1, s.next_level_xp - s.current_level_xp);
    const cur = Math.max(0, s.xp - s.current_level_xp);
    const barFill = document.querySelector('.snake-level-bar-fill');
    if (barFill) barFill.style.width = Math.min(100, (cur / span) * 100) + '%';
    const xpTxt = document.querySelector('.snake-level-xp');
    if (xpTxt) xpTxt.textContent = fmt(cur) + ' / ' + fmt(span) + ' XP';
  }

  function paintHub(soft) {
    const root = document.getElementById('snake-app');
    if (!root) return;
    const s = SS.state;
    const span = Math.max(1, s.next_level_xp - s.current_level_xp);
    const cur = Math.max(0, s.xp - s.current_level_xp);
    const pct = Math.min(100, (cur / span) * 100);

    if (!soft) {
      root.innerHTML = `
        <div class="snake-hub">
          <div class="snake-level-card">
            <div class="snake-level-row">
              <div>🐍 <span class="snake-level-num">${s.level}<small>/100</small></span></div>
              <div class="snake-level-xp">${fmt(cur)} / ${fmt(span)} XP</div>
            </div>
            <div class="snake-level-bar"><div class="snake-level-bar-fill" style="width:${pct}%"></div></div>
          </div>
          <div class="snake-hub-top">
            <div class="snake-stat-card gold">
              <div class="snake-stat-icon" style="color:#ffd700">💰</div>
              <div class="snake-stat-value" data-snake-stat="lifetime">${fmtCompact(s.coins_lifetime)}</div>
              <div class="snake-stat-label">Lifetime</div>
            </div>
            <div class="snake-stat-card red">
              <div class="snake-stat-icon" style="color:#eb4b4b">🏆</div>
              <div class="snake-stat-value">${fmtCompact(s.best_run_coins)}</div>
              <div class="snake-stat-label">Best run</div>
            </div>
            <div class="snake-stat-card blue">
              <div class="snake-stat-icon" style="color:#5aa9ff">▶</div>
              <div class="snake-stat-value">${fmt(s.runs_count)}</div>
              <div class="snake-stat-label">Ранов</div>
            </div>
            <div class="snake-stat-card green">
              <div class="snake-stat-icon" style="color:#5cc15c">🤖</div>
              <div class="snake-stat-value" data-snake-stat="afk-rate">${fmtCompact(s.afk_rate_per_min || 0)}</div>
              <div class="snake-stat-label">AFK/мин</div>
            </div>
          </div>
          <div class="snake-tabs">
            <button class="snake-tab" data-tab="play">▶ Играть</button>
            <button class="snake-tab" data-tab="upgrades">⚒ Апгрейды</button>
            <button class="snake-tab" data-tab="terrarium">🤖 Терариум</button>
            <button class="snake-tab" data-tab="skins">🎨 Скины</button>
            <button class="snake-tab" data-tab="maps">🗺 Карты</button>
            <button class="snake-tab" data-tab="lb">🏆 Топ</button>
          </div>
          <div class="snake-tab-content" id="snake-tab-content"></div>
        </div>
      `;
      root.querySelectorAll('.snake-tab').forEach(b => {
        b.addEventListener('click', () => switchTab(b.dataset.tab));
      });
      switchTab(SS.activeTab);
    }
  }

  function switchTab(tab) {
    SS.activeTab = tab;
    const root = document.getElementById('snake-app');
    if (!root) return;
    root.querySelectorAll('.snake-tab').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
    const c = document.getElementById('snake-tab-content');
    if (!c) return;
    if (tab === 'play')      paintPlayTab(c);
    if (tab === 'upgrades')  paintUpgradesTab(c);
    if (tab === 'terrarium') paintTerrariumTab(c);
    if (tab === 'skins')     paintSkinsTab(c);
    if (tab === 'maps')      paintMapsTab(c);
    if (tab === 'lb')        paintLeaderboardTab(c);
  }

  // ═════════════════ PLAY TAB ═════════════════
  function paintPlayTab(c) {
    const lvl = SS.state.level;
    const modeChips = SS.cfg.modes.map(m => {
      const locked = lvl < m.unlock_lvl;
      const active = SS.selectedMode === m.key && !locked;
      return `
        <div class="snake-mode-chip ${active ? 'active' : ''} ${locked ? 'locked' : ''}" data-mode="${m.key}">
          <div>${escape(m.name)}</div>
          <div class="snake-mode-chip-sub">${locked ? 'lvl ' + m.unlock_lvl : escape(m.desc)}</div>
        </div>
      `;
    }).join('');

    const unlocked = new Set(SS.state.unlocked_maps || ['park']);
    const mapChips = SS.cfg.maps.map(m => {
      const ok = unlocked.has(m.key) || lvl >= m.unlock_lvl;
      const active = SS.selectedMap === m.key && ok;
      return `
        <div class="snake-map-chip ${active ? 'active' : ''} ${!ok ? 'locked' : ''}" data-map="${m.key}">
          <div>${escape(m.name)}</div>
          <div class="snake-map-chip-sub">${ok ? m.size + '×' + m.size : 'lvl ' + m.unlock_lvl}</div>
        </div>
      `;
    }).join('');

    c.innerHTML = `
      <div class="snake-play-block">
        <div>
          <div class="snake-section-label">⚔ Режим</div>
          <div class="snake-mode-strip">${modeChips}</div>
        </div>
        <div>
          <div class="snake-section-label">🗺 Карта</div>
          <div class="snake-map-strip">${mapChips}</div>
        </div>
        <button class="snake-start-btn" id="snake-start">▶ СТАРТ ИГРЫ</button>
      </div>
    `;

    c.querySelectorAll('.snake-mode-chip').forEach(ch => {
      ch.addEventListener('click', () => {
        if (ch.classList.contains('locked')) {
          toast('Откроется на уровне ' + (SS.cfg.modes.find(m => m.key === ch.dataset.mode) || {}).unlock_lvl);
          return;
        }
        SS.selectedMode = ch.dataset.mode;
        paintPlayTab(c);
      });
    });
    c.querySelectorAll('.snake-map-chip').forEach(ch => {
      ch.addEventListener('click', async () => {
        if (ch.classList.contains('locked')) {
          const m = SS.cfg.maps.find(x => x.key === ch.dataset.map);
          if (m) toast('Карта откроется на уровне ' + m.unlock_lvl);
          return;
        }
        SS.selectedMap = ch.dataset.map;
        try { await api('/api/snake/map/select', { method: 'POST', body: JSON.stringify({ map_id: ch.dataset.map }) }); } catch (e) {}
        paintPlayTab(c);
      });
    });

    document.getElementById('snake-start').addEventListener('click', startGame);
  }

  // ═════════════════ GAME ENGINE (canvas + input) ═════════════════
  function startGame() {
    const mapCfg = SS.cfg.maps.find(m => m.key === SS.selectedMap) || SS.cfg.maps[0];
    const modeCfg = SS.cfg.modes.find(m => m.key === SS.selectedMode) || SS.cfg.modes[0];
    const upgrades = SS.state.upgrades || {};
    const skinKey = SS.state.current_skin_id || 'default';
    const skinCfg = SS.cfg.skins.find(s => s.key === skinKey) || SS.cfg.skins[0];

    // Field expansion upgrade
    const fieldLvl = Number(upgrades.field_expansion || 0);
    const baseSize = mapCfg.size;
    const size = baseSize + Math.min(fieldLvl, 10) * 0;  // map dictates size; expansion is a future hook

    // Build overlay
    const overlay = document.createElement('div');
    overlay.className = 'snake-game-overlay';
    overlay.style.setProperty('--snake-map-bg', mapCfg.theme || '#1a3a1a');
    overlay.innerHTML = `
      <div class="snake-game-hud">
        <div class="snake-hud-stat">
          <div class="snake-hud-stat-label">Coins</div>
          <div class="snake-hud-stat-value coins" id="sg-coins">0</div>
        </div>
        <div class="snake-hud-stat">
          <div class="snake-hud-stat-label">Length</div>
          <div class="snake-hud-stat-value length" id="sg-length">3</div>
        </div>
        <div class="snake-hud-stat">
          <div class="snake-hud-stat-label">${modeCfg.duration_sec ? 'Time' : 'Skins'}</div>
          <div class="snake-hud-stat-value timer" id="sg-meta">${modeCfg.duration_sec ? modeCfg.duration_sec + 's' : '0'}</div>
        </div>
        <button class="snake-hud-quit" id="sg-quit">END</button>
      </div>
      <div class="snake-canvas-wrap">
        <canvas class="snake-canvas" id="sg-canvas"></canvas>
        <div class="snake-flash" id="sg-flash"></div>
      </div>
    `;
    document.body.appendChild(overlay);

    const canvas = document.getElementById('sg-canvas');
    const ctx = canvas.getContext('2d');

    // Determine pixel size
    const wrapEl = overlay.querySelector('.snake-canvas-wrap');
    const wrapRect = wrapEl.getBoundingClientRect();
    const maxPx = Math.min(wrapRect.width, wrapRect.height) - 16;
    const cellPx = Math.floor(maxPx / size);
    canvas.width = cellPx * size;
    canvas.height = cellPx * size;

    // ----- game state -----
    const snake = {
      cells: [{ x: Math.floor(size / 2) - 1, y: Math.floor(size / 2) }, { x: Math.floor(size / 2) - 2, y: Math.floor(size / 2) }, { x: Math.floor(size / 2) - 3, y: Math.floor(size / 2) }],
      dir: { x: 1, y: 0 },
      pendingDir: { x: 1, y: 0 },
    };

    const G = {
      size,
      cellPx,
      mapCfg,
      modeCfg,
      skinCfg,
      upgrades,
      coins: 0,
      skinsEaten: 0,
      rarityCounts: {},   // {key: count}
      length: snake.cells.length,
      foods: [],          // [{x, y, rarity}]
      obstacles: [],      // [{x, y, dx?, dy?}]  (moving obstacles have dx/dy)
      shieldsLeft: Number(upgrades.iron_shield || 0),
      bouncesLeft: Number(upgrades.wall_bounce || 0),
      ghostMs: 0,
      tickMs: 200,
      lastMoveAt: performance.now(),
      startedAt: performance.now(),
      stopped: false,
      paused: false,
      popups: [],         // {x, y, text, color, t}
      particles: [],
      animFrame: null,
      survivalScale: 1,
      pendingFoodTimer: 0,
      lastObstacleMoveAt: performance.now(),
    };

    // Initial speed (slow_start makes it slower)
    const slowStartLvl = Number(upgrades.slow_start || 0);
    G.tickMs = 200 + slowStartLvl * 6;  // 200 → 260ms

    // Spawn obstacles
    const totalObs = mapCfg.obstacles + mapCfg.moving;
    for (let i = 0; i < totalObs; i++) {
      const o = randomEmptyCell(G, snake);
      if (!o) break;
      if (i >= mapCfg.obstacles) {
        // moving
        o.dx = (Math.random() < 0.5 ? 1 : -1);
        o.dy = 0;
      }
      G.obstacles.push(o);
    }

    // Spawn initial food
    spawnFood(G, snake, weightedRollRarity(G, SS.cfg.rarities));

    // Density bump
    const densityLvl = Number(upgrades.skin_density || 0);
    G.foodTarget = 1 + Math.floor(densityLvl / 3);
    while (G.foods.length < G.foodTarget) {
      spawnFood(G, snake, weightedRollRarity(G, SS.cfg.rarities));
    }

    // ----- input -----
    const setDir = (dx, dy) => {
      // Prevent 180° flip
      if (dx === -snake.dir.x && dy === -snake.dir.y) return;
      snake.pendingDir = { x: dx, y: dy };
    };

    const keyHandler = (e) => {
      const k = e.key.toLowerCase();
      if (k === 'arrowup' || k === 'w')    { setDir(0, -1); e.preventDefault(); }
      if (k === 'arrowdown' || k === 's')  { setDir(0,  1); e.preventDefault(); }
      if (k === 'arrowleft' || k === 'a')  { setDir(-1, 0); e.preventDefault(); }
      if (k === 'arrowright' || k === 'd') { setDir( 1, 0); e.preventDefault(); }
    };
    window.addEventListener('keydown', keyHandler);

    // Touch swipes — restricted to canvas; preventDefault to keep Telegram from
    // interpreting them as navigation gestures.
    let touchStart = null;
    const touchStartH = (e) => {
      if (!e.touches || e.touches.length === 0) return;
      const t = e.touches[0];
      touchStart = { x: t.clientX, y: t.clientY, ts: Date.now() };
    };
    const touchMoveH = (e) => {
      e.preventDefault();
    };
    const touchEndH = (e) => {
      if (!touchStart) return;
      const t = e.changedTouches && e.changedTouches[0];
      if (!t) { touchStart = null; return; }
      const dx = t.clientX - touchStart.x;
      const dy = t.clientY - touchStart.y;
      const ax = Math.abs(dx), ay = Math.abs(dy);
      if (Math.max(ax, ay) < 18) { touchStart = null; return; }
      if (ax > ay) setDir(dx > 0 ? 1 : -1, 0);
      else         setDir(0, dy > 0 ? 1 : -1);
      touchStart = null;
    };
    overlay.addEventListener('touchstart', touchStartH, { passive: true });
    overlay.addEventListener('touchmove',  touchMoveH,  { passive: false });
    overlay.addEventListener('touchend',   touchEndH,   { passive: true });

    // Quit
    document.getElementById('sg-quit').addEventListener('click', () => {
      endRun(G, snake, 'manual');
    });

    // ----- game loop -----
    function loop() {
      if (G.stopped) return;
      const now = performance.now();

      // Mode timer
      if (G.modeCfg.duration_sec > 0) {
        const elapsed = (now - G.startedAt) / 1000;
        const left = Math.max(0, G.modeCfg.duration_sec - elapsed);
        document.getElementById('sg-meta').textContent = left.toFixed(0) + 's';
        if (left <= 0) { endRun(G, snake, 'timeout'); return; }
      } else {
        document.getElementById('sg-meta').textContent = String(G.skinsEaten);
      }

      // Survival mode: ramp up
      if (G.modeCfg.key === 'survival') {
        const elapsed = (now - G.startedAt) / 1000;
        G.survivalScale = 1 + Math.floor(elapsed / 60) * 0.2;
        // Spawn extra obstacle every minute
        if (elapsed > G.lastSurvivalSpawn + 60) {
          G.lastSurvivalSpawn = elapsed;
          const o = randomEmptyCell(G, snake);
          if (o) G.obstacles.push(o);
        }
      }

      // Move snake at tickMs cadence
      if (now - G.lastMoveAt >= G.tickMs) {
        G.lastMoveAt = now;
        snake.dir = snake.pendingDir;
        const head = { x: snake.cells[0].x + snake.dir.x, y: snake.cells[0].y + snake.dir.y };

        // Wall collision
        if (head.x < 0 || head.x >= G.size || head.y < 0 || head.y >= G.size) {
          if (G.bouncesLeft > 0) {
            G.bouncesLeft--;
            // Reverse direction
            snake.dir = { x: -snake.dir.x, y: -snake.dir.y };
            snake.pendingDir = snake.dir;
            // Skip move (snake stays)
            G.animFrame = requestAnimationFrame(loop);
            render(G, snake, ctx, now);
            return;
          }
          if (Number(G.upgrades.tough_skin || 0) > 0 && !G._toughUsed) {
            G._toughUsed = true;
            // Reset to center, keep half length
            const keep = Math.max(3, Math.floor(snake.cells.length * 0.5));
            snake.cells = [];
            const cx = Math.floor(G.size/2), cy = Math.floor(G.size/2);
            for (let i = 0; i < keep; i++) snake.cells.push({ x: cx - i, y: cy });
            snake.dir = { x: 1, y: 0 };
            snake.pendingDir = snake.dir;
            flash(G);
            G.animFrame = requestAnimationFrame(loop);
            render(G, snake, ctx, now);
            return;
          }
          return endRun(G, snake, 'wall');
        }

        // Self collision
        const selfHit = snake.cells.some(c => c.x === head.x && c.y === head.y);
        if (selfHit) {
          const phantomChance = Number(G.upgrades.phantom_tail || 0) * 0.017;  // 1.7% per lvl
          if (Math.random() < phantomChance) {
            // pass through self this tick — no dying, no growth
            snake.cells.unshift(head);
            snake.cells.pop();
            G.animFrame = requestAnimationFrame(loop);
            render(G, snake, ctx, now);
            return;
          }
          if (G.shieldsLeft > 0) {
            G.shieldsLeft--;
            flash(G);
            // Don't kill — just don't move this tick
            G.animFrame = requestAnimationFrame(loop);
            render(G, snake, ctx, now);
            return;
          }
          return endRun(G, snake, 'self');
        }

        // Obstacle collision
        const obstacleHit = G.obstacles.findIndex(o => o.x === head.x && o.y === head.y);
        if (obstacleHit >= 0) {
          if (G.ghostMs > 0) {
            // Ghost pass — destroy obstacle
            G.obstacles.splice(obstacleHit, 1);
          } else if (Number(G.upgrades.obstacle_smash || 0) > 0 && !G._smashesUsed) {
            G._smashesUsed = (G._smashesUsed || 0) + 1;
            if (G._smashesUsed <= Number(G.upgrades.obstacle_smash)) {
              G.obstacles.splice(obstacleHit, 1);
            } else {
              return endRun(G, snake, 'obstacle');
            }
          } else if (G.shieldsLeft > 0) {
            G.shieldsLeft--;
            G.obstacles.splice(obstacleHit, 1);
            flash(G);
          } else {
            return endRun(G, snake, 'obstacle');
          }
        }

        // Move
        snake.cells.unshift(head);

        // Check food
        const foodIdx = G.foods.findIndex(f => f.x === head.x && f.y === head.y);
        if (foodIdx >= 0) {
          const food = G.foods[foodIdx];
          eatFood(G, food, head);
          G.foods.splice(foodIdx, 1);
          // Spawn new food
          while (G.foods.length < G.foodTarget) {
            spawnFood(G, snake, weightedRollRarity(G, SS.cfg.rarities));
          }
          // Magnet vacuum
          if (Number(G.upgrades.skin_vacuum || 0) > 0) {
            const vChance = Number(G.upgrades.skin_vacuum || 0) * 0.05;
            for (let i = G.foods.length - 1; i >= 0; i--) {
              const f = G.foods[i];
              const dist = Math.abs(f.x - head.x) + Math.abs(f.y - head.y);
              if (dist === 1 && Math.random() < vChance) {
                eatFood(G, f, head);
                G.foods.splice(i, 1);
              }
            }
            while (G.foods.length < G.foodTarget) {
              spawnFood(G, snake, weightedRollRarity(G, SS.cfg.rarities));
            }
          }
        } else {
          snake.cells.pop();
        }

        // Magnet pull
        const magRange = Number(G.upgrades.magnet_range || 0);
        const magCells = magRange >= 1 ? Math.max(0, Math.floor((magRange + 1) / 4)) : 0;
        if (magCells > 0) {
          for (let i = 0; i < G.foods.length; i++) {
            const f = G.foods[i];
            const dx = head.x - f.x, dy = head.y - f.y;
            const d = Math.abs(dx) + Math.abs(dy);
            if (d <= magCells + 1 && d > 1) {
              if (Math.abs(dx) > Math.abs(dy)) f.x += Math.sign(dx);
              else f.y += Math.sign(dy);
            }
          }
        }

        G.length = snake.cells.length;
        document.getElementById('sg-length').textContent = G.length;
      }

      // Move obstacles every 600ms
      if (now - G.lastObstacleMoveAt >= 600) {
        G.lastObstacleMoveAt = now;
        for (const o of G.obstacles) {
          if (o.dx !== undefined) {
            const nx = o.x + (o.dx || 0), ny = o.y + (o.dy || 0);
            if (nx < 0 || nx >= G.size || ny < 0 || ny >= G.size) {
              o.dx = -(o.dx || 0); o.dy = -(o.dy || 0);
            } else {
              o.x = nx; o.y = ny;
            }
          }
        }
      }

      // Decrement ghost
      if (G.ghostMs > 0) {
        G.ghostMs = Math.max(0, G.ghostMs - 16);
      }

      render(G, snake, ctx, now);
      G.animFrame = requestAnimationFrame(loop);
    }

    G.animFrame = requestAnimationFrame(loop);

    // Store cleanup hook
    overlay._cleanup = () => {
      window.removeEventListener('keydown', keyHandler);
      overlay.removeEventListener('touchstart', touchStartH);
      overlay.removeEventListener('touchmove', touchMoveH);
      overlay.removeEventListener('touchend', touchEndH);
      if (G.animFrame) cancelAnimationFrame(G.animFrame);
    };
  }

  function eatFood(G, food, head) {
    const r = SS.cfg.rarities.find(x => x.key === food.rarity);
    if (!r) return;
    let coins = Math.floor(r.coin_min + Math.random() * (r.coin_max - r.coin_min + 1));

    // Lucky strike
    const luckyP = Number(G.upgrades.lucky_strike || 0) * 0.02;
    if (Math.random() < luckyP) coins *= 2;
    // Critical bite
    const critP = Number(G.upgrades.critical_bite || 0) * 0.005;
    if (Math.random() < critP) coins *= 10;

    G.coins += coins;
    G.skinsEaten += 1;
    G.rarityCounts[r.key] = (G.rarityCounts[r.key] || 0) + 1;
    document.getElementById('sg-coins').textContent = fmt(G.coins);

    // Popup
    G.popups.push({
      x: head.x * G.cellPx + G.cellPx / 2,
      y: head.y * G.cellPx + G.cellPx / 2,
      text: '+' + fmt(coins),
      color: r.color,
      t0: performance.now(),
    });

    // Haptic
    if (coins > 5000) tg?.HapticFeedback?.notificationOccurred?.('success');
    else tg?.HapticFeedback?.impactOccurred?.('light');
  }

  function flash(G) {
    const f = document.getElementById('sg-flash');
    if (f) {
      f.classList.remove('active');
      void f.offsetWidth;
      f.classList.add('active');
    }
  }

  function spawnFood(G, snake, rarity) {
    let cell = null;
    for (let tries = 0; tries < 100; tries++) {
      const x = Math.floor(Math.random() * G.size);
      const y = Math.floor(Math.random() * G.size);
      const onSnake = snake.cells.some(c => c.x === x && c.y === y);
      const onFood = G.foods.some(f => f.x === x && f.y === y);
      const onObs = G.obstacles.some(o => o.x === x && o.y === y);
      if (!onSnake && !onFood && !onObs) { cell = { x, y, rarity: rarity }; break; }
    }
    if (cell) G.foods.push(cell);
    return cell;
  }

  function randomEmptyCell(G, snake) {
    for (let tries = 0; tries < 100; tries++) {
      const x = Math.floor(Math.random() * G.size);
      const y = Math.floor(Math.random() * G.size);
      const onSnake = snake.cells.some(c => c.x === x && c.y === y);
      const onFood = G.foods.some(f => f.x === x && f.y === y);
      const onObs = G.obstacles.some(o => o.x === x && o.y === y);
      // Not too close to head
      const distFromHead = Math.abs(x - snake.cells[0].x) + Math.abs(y - snake.cells[0].y);
      if (!onSnake && !onFood && !onObs && distFromHead > 3) return { x, y };
    }
    return null;
  }

  function weightedRollRarity(G, rarities) {
    // Apply mythic_magnet boost
    const magnetLvl = Number(G.upgrades.mythic_magnet || 0);
    const driftLvl  = Number(G.upgrades.skin_drop_plus || 0);

    let weights = rarities.map(r => Math.max(0.1, r.weight));
    if (magnetLvl > 0) {
      // boost covert + exc_rare
      weights = rarities.map((r, i) => {
        if (r.key === 'covert' || r.key === 'exceedingly_rare') return r.weight * (1 + magnetLvl * 0.5);
        return r.weight;
      });
    }
    let total = weights.reduce((a, b) => a + b, 0);
    let r = Math.random() * total;
    let chosenIdx = 0;
    for (let i = 0; i < weights.length; i++) {
      r -= weights[i];
      if (r <= 0) { chosenIdx = i; break; }
    }
    // Drop+ — small chance to lift rarity by 1
    if (driftLvl > 0 && Math.random() < driftLvl * 0.01 && chosenIdx + 1 < rarities.length) {
      chosenIdx += 1;
    }
    return rarities[chosenIdx].key;
  }

  function render(G, snake, ctx, now) {
    const W = ctx.canvas.width, H = ctx.canvas.height;
    ctx.clearRect(0, 0, W, H);

    // Grid lines
    ctx.strokeStyle = 'rgba(255,255,255,0.04)';
    ctx.lineWidth = 1;
    for (let i = 0; i <= G.size; i++) {
      ctx.beginPath();
      ctx.moveTo(i * G.cellPx, 0);
      ctx.lineTo(i * G.cellPx, H);
      ctx.stroke();
      ctx.beginPath();
      ctx.moveTo(0, i * G.cellPx);
      ctx.lineTo(W, i * G.cellPx);
      ctx.stroke();
    }

    // Obstacles
    for (const o of G.obstacles) {
      ctx.fillStyle = o.dx !== undefined ? '#aa3030' : '#555';
      ctx.fillRect(o.x * G.cellPx + 1, o.y * G.cellPx + 1, G.cellPx - 2, G.cellPx - 2);
      ctx.strokeStyle = o.dx !== undefined ? '#ff5555' : '#888';
      ctx.lineWidth = 1;
      ctx.strokeRect(o.x * G.cellPx + 1, o.y * G.cellPx + 1, G.cellPx - 2, G.cellPx - 2);
    }

    // Foods
    for (const f of G.foods) {
      const r = SS.cfg.rarities.find(x => x.key === f.rarity) || SS.cfg.rarities[0];
      const cx = f.x * G.cellPx + G.cellPx / 2;
      const cy = f.y * G.cellPx + G.cellPx / 2;
      const sz = Math.floor(G.cellPx * 0.65);
      // Glow for rare
      if (f.rarity === 'covert' || f.rarity === 'exceedingly_rare') {
        const pulse = Math.sin(now * 0.005) * 0.3 + 0.7;
        ctx.shadowColor = r.color;
        ctx.shadowBlur = 12 * pulse;
      }
      ctx.fillStyle = r.color;
      ctx.beginPath();
      ctx.arc(cx, cy, sz / 2, 0, Math.PI * 2);
      ctx.fill();
      ctx.shadowBlur = 0;
    }

    // Snake
    const skin = G.skinCfg;
    for (let i = 0; i < snake.cells.length; i++) {
      const c = snake.cells[i];
      const isHead = i === 0;
      const t = i / Math.max(1, snake.cells.length - 1);
      // Skin color logic
      let color = '#5cc15c';
      if (skin.key === 'cyber') color = i % 2 ? '#00ffe1' : '#0084ff';
      else if (skin.key === 'rainbow') {
        const colors = ['#ff5757','#ffe85c','#5cc15c','#5aa9ff','#d32ce6'];
        color = colors[i % colors.length];
      }
      else if (skin.key === 'dragon') color = `hsl(${20 + t * 30}, 80%, ${40 + (1-t) * 20}%)`;
      else if (skin.key === 'electric') color = (i % 3 === 0) ? '#fffd6e' : '#5aa9ff';
      else if (skin.key === 'skull') color = i % 2 ? '#1a1a1a' : '#666';
      else if (skin.key === 'phoenix') color = `hsl(${15 + t * 35}, 100%, 50%)`;
      else if (skin.key === 'cosmic') color = i % 3 === 0 ? '#7340c4' : '#0a0a14';
      else if (skin.key === 'royal') color = i % 2 ? '#ffd700' : '#fff5b8';
      else if (skin.key === 'universe') {
        const hue = (now * 0.3 + i * 30) % 360;
        color = `hsl(${hue}, 100%, 60%)`;
      }
      else color = `hsl(120, 60%, ${50 - t * 25}%)`;

      ctx.fillStyle = color;
      const pad = isHead ? 0 : 1;
      const x = c.x * G.cellPx + pad;
      const y = c.y * G.cellPx + pad;
      const sz = G.cellPx - pad * 2;
      ctx.beginPath();
      const radius = isHead ? G.cellPx * 0.25 : G.cellPx * 0.18;
      roundRect(ctx, x, y, sz, sz, radius);
      ctx.fill();

      // Head: eye
      if (isHead) {
        ctx.fillStyle = '#fff';
        const eyeSz = Math.max(2, G.cellPx * 0.15);
        const ex = c.x * G.cellPx + G.cellPx / 2 + snake.dir.x * G.cellPx * 0.2;
        const ey = c.y * G.cellPx + G.cellPx / 2 + snake.dir.y * G.cellPx * 0.2;
        ctx.beginPath(); ctx.arc(ex, ey, eyeSz, 0, Math.PI * 2); ctx.fill();
        ctx.fillStyle = '#000';
        ctx.beginPath(); ctx.arc(ex, ey, eyeSz * 0.5, 0, Math.PI * 2); ctx.fill();
      }
    }

    // Popups
    for (let i = G.popups.length - 1; i >= 0; i--) {
      const p = G.popups[i];
      const dt = now - p.t0;
      if (dt > 1000) { G.popups.splice(i, 1); continue; }
      const a = 1 - (dt / 1000);
      ctx.fillStyle = p.color;
      ctx.globalAlpha = a;
      ctx.font = 'bold 14px sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText(p.text, p.x, p.y - dt * 0.04);
      ctx.globalAlpha = 1;
    }
  }

  function roundRect(ctx, x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.lineTo(x + w - r, y);
    ctx.quadraticCurveTo(x + w, y, x + w, y + r);
    ctx.lineTo(x + w, y + h - r);
    ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
    ctx.lineTo(x + r, y + h);
    ctx.quadraticCurveTo(x, y + h, x, y + h - r);
    ctx.lineTo(x, y + r);
    ctx.quadraticCurveTo(x, y, x + r, y);
    ctx.closePath();
  }

  function endRun(G, snake, diedTo) {
    if (G.stopped) return;
    G.stopped = true;
    if (G.animFrame) cancelAnimationFrame(G.animFrame);
    const overlay = document.querySelector('.snake-game-overlay');
    if (overlay && overlay._cleanup) overlay._cleanup();
    if (overlay) overlay.remove();    // remove playfield immediately

    const duration = Math.floor((performance.now() - G.startedAt) / 1000);
    const length = snake.cells.length;

    // Show modal IMMEDIATELY — API runs in background and updates fields in place.
    // Earlier impl awaited the API first; if it hung or failed silently the user
    // saw "game stopped, nothing happens".
    const reasons = { wall: 'Стена', self: 'Себя сожрал', obstacle: 'Препятствие', timeout: 'Время вышло', manual: 'Сам ушёл' };
    const modal = document.createElement('div');
    modal.className = 'snake-death-modal';
    modal.innerHTML = `
      <div class="snake-death-card">
        <div class="snake-death-title">${escape(reasons[diedTo] || 'Ран окончен')}</div>
        <div class="snake-death-coins" id="sg-d-coins">…</div>
        <div class="snake-death-stats">
          <div class="snake-death-stat">
            <div class="snake-death-stat-label">Скинов</div>
            <div class="snake-death-stat-value">${G.skinsEaten}</div>
          </div>
          <div class="snake-death-stat">
            <div class="snake-death-stat-label">Длина</div>
            <div class="snake-death-stat-value">${length}</div>
          </div>
          <div class="snake-death-stat">
            <div class="snake-death-stat-label">Время</div>
            <div class="snake-death-stat-value">${duration}s</div>
          </div>
          <div class="snake-death-stat">
            <div class="snake-death-stat-label">XP</div>
            <div class="snake-death-stat-value" id="sg-d-xp">…</div>
          </div>
        </div>
        <div class="snake-death-actions">
          <button class="snake-death-close" id="sg-close">К меню</button>
          <button class="snake-death-retry" id="sg-retry">▶ Ещё</button>
        </div>
      </div>
    `;
    document.body.appendChild(modal);

    document.getElementById('sg-close').addEventListener('click', async () => {
      modal.remove();
      try { SS.state = await api('/api/snake/state'); } catch (e) {}
      paintHub();
    });
    document.getElementById('sg-retry').addEventListener('click', async () => {
      modal.remove();
      try { SS.state = await api('/api/snake/state'); } catch (e) {}
      startGame();
    });

    tg?.HapticFeedback?.notificationOccurred?.('warning');

    // Fire-and-forget: settle on the server, then patch numbers in the modal.
    api('/api/snake/run', {
      method: 'POST',
      body: JSON.stringify({
        rarity_counts: G.rarityCounts,
        duration_sec: duration,
        length,
        mode: G.modeCfg.key,
        map_id: G.mapCfg.key,
        died_to: diedTo,
      }),
    }).then(resp => {
      if (!resp) return;
      const credited = resp.coins_credited || 0;
      const xp = resp.xp_gained || 0;
      const coinsEl = document.getElementById('sg-d-coins');
      const xpEl = document.getElementById('sg-d-xp');
      if (coinsEl) coinsEl.textContent = '+' + fmt(credited) + ' 🪙';
      if (xpEl)    xpEl.textContent = '+' + fmt(xp);
      if (typeof resp.new_balance === 'number') {
        window.state.me.balance = resp.new_balance;
        const balEl = document.getElementById('balance-display');
        if (balEl) balEl.textContent = fmt(resp.new_balance);
      }
      if (credited > 10000) tg?.HapticFeedback?.notificationOccurred?.('success');
    }).catch(e => {
      toast('Ошибка сохранения: ' + e.message);
      const coinsEl = document.getElementById('sg-d-coins');
      if (coinsEl) coinsEl.textContent = '+0 🪙';
      const xpEl = document.getElementById('sg-d-xp');
      if (xpEl) xpEl.textContent = '+0';
    });
  }

  // ═════════════════ UPGRADES TAB ═════════════════
  function paintUpgradesTab(c) {
    const branchPills = SS.cfg.branches.map(b => `
      <div class="snake-branch-pill ${SS.activeBranch === b.key ? 'active' : ''}" data-branch="${b.key}" style="border-color:${b.color}">
        <span class="icon">${b.icon}</span>
        <span>${escape(b.name)}</span>
      </div>
    `).join('');

    const ups = SS.cfg.upgrades.filter(u => u.branch === SS.activeBranch);
    const userLevels = SS.state.upgrades || {};
    const balance = window.state?.me?.balance || 0;

    const list = ups.map(u => {
      const cur = Number(userLevels[u.key] || 0);
      const max = u.max_level;
      const isMax = cur >= max;
      const tier = isMax ? null : u.tiers[cur];
      const cost = tier ? tier[2] : 0;
      const nextEffect = tier ? tier[1] : (u.tiers[max - 1] && u.tiers[max - 1][1]);
      const canAfford = !isMax && balance >= cost;
      const progress = (cur / max) * 100;

      return `
        <div class="snake-upgrade-card">
          <div class="snake-upgrade-icon">${u.icon}</div>
          <div class="snake-upgrade-info">
            <div class="snake-upgrade-name">${escape(u.name)}</div>
            <div class="snake-upgrade-desc">${escape(u.desc)}</div>
            <div class="snake-upgrade-progress">
              <span>${cur}/${max}</span>
              <div class="snake-upgrade-bar"><div class="snake-upgrade-bar-fill" style="width:${progress}%"></div></div>
              <span>${nextEffect}${escape(u.unit)}</span>
            </div>
          </div>
          ${isMax
            ? `<button class="snake-upgrade-buy maxed" disabled>MAX</button>`
            : `<button class="snake-upgrade-buy" data-key="${u.key}" ${canAfford ? '' : 'disabled'}>${fmt(cost)} 🪙</button>`}
        </div>
      `;
    }).join('');

    c.innerHTML = `
      <div class="snake-branch-strip">${branchPills}</div>
      <div class="snake-upgrade-list">${list}</div>
    `;

    c.querySelectorAll('.snake-branch-pill').forEach(p => {
      p.addEventListener('click', () => {
        SS.activeBranch = p.dataset.branch;
        paintUpgradesTab(c);
      });
    });
    c.querySelectorAll('[data-key]').forEach(b => {
      b.addEventListener('click', async () => {
        b.disabled = true;
        try {
          const r = await api('/api/snake/upgrade', { method: 'POST', body: JSON.stringify({ key: b.dataset.key }) });
          if (!r.ok) { toast(r.error || 'Ошибка'); return; }
          // Update balance + state
          if (typeof r.new_balance === 'number') {
            window.state.me.balance = r.new_balance;
            const balEl = document.getElementById('balance-display');
            if (balEl) balEl.textContent = fmt(r.new_balance);
          }
          SS.state.upgrades = SS.state.upgrades || {};
          SS.state.upgrades[r.key] = r.new_level;
          tg?.HapticFeedback?.impactOccurred?.('light');
          paintUpgradesTab(c);
        } catch (e) { toast('Ошибка: ' + e.message); }
      });
    });
  }

  // ═════════════════ TERRARIUM TAB (AFK) ═════════════════
  function paintTerrariumTab(c) {
    const owned = SS.state.afk_snakes || {};
    const balance = window.state?.me?.balance || 0;
    const totalRate = SS.state.afk_rate_per_min || 0;
    const dailyEarned = SS.state.daily_afk_earned || 0;
    const cap = SS.state.afk_cap_today || 50000;
    const capPct = Math.min(100, (dailyEarned / cap) * 100);

    const cards = SS.cfg.afk_snakes.map((sn, idx) => {
      const copies = owned[sn.key] || [];
      const nextCost = sn.base_cost * Math.pow(2, copies.length);
      const canBuy = balance >= nextCost;
      const totalForSnake = copies.reduce((acc, lvl) => acc + sn.base_rate * Math.pow(sn.rate_mult, lvl), 0);

      const copiesHtml = copies.map((lvl, i) => {
        const isMax = lvl >= SS.cfg.afk_snake_max_level;
        const upCost = sn.upgrade_cost_base * Math.pow(1.4, lvl);
        const copyRate = sn.base_rate * Math.pow(sn.rate_mult, lvl);
        return `
          <div class="snake-afk-copy-pill ${isMax ? 'maxed' : ''}" data-snake="${sn.key}" data-idx="${i}" title="Уровень копии. Тап = апгрейд.">
            #${i+1} L${lvl} · ${fmt(copyRate.toFixed(0))}/мин${isMax ? ' ⭐' : ' · ↑' + fmt(upCost) + '🪙'}
          </div>
        `;
      }).join('');

      // Show base rate prominently — even when zero copies are owned the user
      // can see what one copy contributes, makes the price-tag click-through.
      const ownedSummary = copies.length === 0
        ? `1 копия = <b>${fmt(sn.base_rate)} мон/мин</b>`
        : `${copies.length} шт · сейчас даёт <b>${fmt(totalForSnake.toFixed(1))} мон/мин</b>`;

      return `
        <div class="snake-afk-snake-card">
          <div class="snake-afk-snake-head">
            <div class="snake-afk-snake-icon">${sn.icon}</div>
            <div>
              <div class="snake-afk-snake-name">${escape(sn.name)}</div>
              <div class="snake-afk-snake-stats">${ownedSummary}</div>
            </div>
          </div>
          <button class="snake-afk-buy-btn" data-buy="${sn.key}" ${canBuy ? '' : 'disabled'}>
            Купить #${copies.length + 1} за ${fmt(nextCost)} 🪙
          </button>
          ${copies.length > 0 ? `<div class="snake-afk-copies">${copiesHtml}</div>` : ''}
        </div>
      `;
    }).join('');

    c.innerHTML = `
      <div class="snake-afk-rate-banner">
        <div class="snake-afk-rate-row">
          <div class="snake-afk-rate-label">AFK Rate</div>
          <div class="snake-afk-rate-value">${fmt(totalRate.toFixed(1))} / мин</div>
        </div>
        <div class="snake-afk-cap-bar"><div class="snake-afk-cap-fill" style="width:${capPct}%"></div></div>
        <div class="snake-afk-cap-text">${fmt(dailyEarned)} / ${fmt(cap)} (дневной кап)</div>
      </div>
      <div class="snake-afk-list">${cards}</div>
    `;

    c.querySelectorAll('[data-buy]').forEach(b => {
      b.addEventListener('click', async () => {
        b.disabled = true;
        try {
          const r = await api('/api/snake/afk/buy', { method: 'POST', body: JSON.stringify({ snake_key: b.dataset.buy }) });
          if (!r.ok) { toast(r.error || 'Ошибка'); return; }
          if (typeof r.new_balance === 'number') {
            window.state.me.balance = r.new_balance;
            const balEl = document.getElementById('balance-display');
            if (balEl) balEl.textContent = fmt(r.new_balance);
          }
          SS.state = await api('/api/snake/state');
          paintTerrariumTab(c);
        } catch (e) { toast('Ошибка: ' + e.message); }
      });
    });

    c.querySelectorAll('.snake-afk-copy-pill').forEach(p => {
      if (p.classList.contains('maxed')) return;
      p.addEventListener('click', async () => {
        const sk = p.dataset.snake;
        const idx = parseInt(p.dataset.idx);
        try {
          const r = await api('/api/snake/afk/upgrade', { method: 'POST', body: JSON.stringify({ snake_key: sk, copy_idx: idx }) });
          if (!r.ok) { toast(r.error || 'Ошибка'); return; }
          if (typeof r.new_balance === 'number') {
            window.state.me.balance = r.new_balance;
            const balEl = document.getElementById('balance-display');
            if (balEl) balEl.textContent = fmt(r.new_balance);
          }
          SS.state = await api('/api/snake/state');
          paintTerrariumTab(c);
        } catch (e) { toast('Ошибка: ' + e.message); }
      });
    });
  }

  // ═════════════════ SKINS TAB ═════════════════
  function paintSkinsTab(c) {
    const owned = new Set(SS.state.owned_skins || ['default']);
    const equipped = SS.state.current_skin_id || 'default';
    const balance = window.state?.me?.balance || 0;

    const cards = SS.cfg.skins.map(sk => {
      const isOwned = owned.has(sk.key);
      const isEquipped = sk.key === equipped;
      const canBuy = !isOwned && balance >= sk.price;
      return `
        <div class="snake-skin-card ${isOwned ? 'owned' : ''} ${isEquipped ? 'equipped' : ''} rarity-${sk.rarity}" data-skin="${sk.key}">
          <span class="snake-skin-rarity-badge">${escape(sk.rarity)}</span>
          <div class="snake-skin-preview" style="--skin-preview:${sk.preview}"></div>
          <div class="snake-skin-name">${escape(sk.name)}</div>
          <div class="snake-skin-price">
            ${isEquipped ? '✓ ВЫБРАН' : (isOwned ? 'Выбрать' : (canBuy ? fmt(sk.price) + ' 🪙' : 'Не хватает'))}
          </div>
        </div>
      `;
    }).join('');

    c.innerHTML = `<div class="snake-skins-grid">${cards}</div>`;

    c.querySelectorAll('[data-skin]').forEach(card => {
      card.addEventListener('click', async () => {
        const key = card.dataset.skin;
        const isOwned = owned.has(key);
        try {
          if (!isOwned) {
            const r = await api('/api/snake/skin/buy', { method: 'POST', body: JSON.stringify({ skin_key: key }) });
            if (!r.ok) { toast(r.error || 'Ошибка'); return; }
            // After buy, equip
            await api('/api/snake/skin/equip', { method: 'POST', body: JSON.stringify({ skin_key: key }) });
          } else {
            await api('/api/snake/skin/equip', { method: 'POST', body: JSON.stringify({ skin_key: key }) });
          }
          SS.state = await api('/api/snake/state');
          if (window.state) window.state.me.balance = (window.state.me.balance || 0); // refresh from another fetch if needed
          // Refresh top balance
          try { const me = await api('/api/me'); window.state.me = me; const balEl = document.getElementById('balance-display'); if (balEl) balEl.textContent = fmt(me.balance); } catch (e) {}
          paintSkinsTab(c);
        } catch (e) { toast('Ошибка: ' + e.message); }
      });
    });
  }

  // ═════════════════ MAPS TAB ═════════════════
  function paintMapsTab(c) {
    const lvl = SS.state.level;
    const cards = SS.cfg.maps.map(m => {
      const ok = lvl >= m.unlock_lvl;
      const isSelected = SS.selectedMap === m.key && ok;
      return `
        <div class="snake-map-card ${ok ? '' : 'locked'} ${isSelected ? 'selected' : ''}" data-map="${m.key}" style="--map-bg:${m.theme}">
          ${!ok ? `<div class="snake-map-card-lock">lvl ${m.unlock_lvl}</div>` : ''}
          <div class="snake-map-card-preview" style="--map-bg:${m.theme}"></div>
          <div class="snake-map-card-name">${escape(m.name)}</div>
          <div class="snake-map-card-info">${m.size}×${m.size} · ${m.obstacles + m.moving} препятств.</div>
        </div>
      `;
    }).join('');
    c.innerHTML = `<div class="snake-maps-grid">${cards}</div>`;
    c.querySelectorAll('[data-map]').forEach(card => {
      card.addEventListener('click', async () => {
        if (card.classList.contains('locked')) {
          const m = SS.cfg.maps.find(x => x.key === card.dataset.map);
          toast('Откроется на уровне ' + (m && m.unlock_lvl));
          return;
        }
        SS.selectedMap = card.dataset.map;
        try { await api('/api/snake/map/select', { method: 'POST', body: JSON.stringify({ map_id: card.dataset.map }) }); } catch (e) {}
        paintMapsTab(c);
      });
    });
  }

  // ═════════════════ LEADERBOARD TAB ═════════════════
  let lbPeriod = 'all';
  async function paintLeaderboardTab(c) {
    c.innerHTML = `
      <div class="snake-lb-tabs">
        <button class="snake-lb-tab ${lbPeriod === 'all' ? 'active' : ''}" data-p="all">Всё время</button>
        <button class="snake-lb-tab ${lbPeriod === 'week' ? 'active' : ''}" data-p="week">Неделя</button>
      </div>
      <div id="snake-lb-list"><div class="loader">Загрузка...</div></div>
    `;
    c.querySelectorAll('.snake-lb-tab').forEach(b => b.addEventListener('click', () => {
      lbPeriod = b.dataset.p;
      paintLeaderboardTab(c);
    }));
    try {
      const list = await api('/api/snake/leaderboard?period=' + lbPeriod);
      const el = document.getElementById('snake-lb-list');
      if (!el) return;
      if (!list.length) { el.innerHTML = '<div class="loader">Пусто</div>'; return; }
      el.innerHTML = list.map((r, i) => {
        const rank = i + 1;
        const rankCls = i === 0 ? 'top1' : i === 1 ? 'top2' : i === 2 ? 'top3' : '';
        const medal = ['🥇','🥈','🥉'][i] || '#' + rank;
        const name = r.username ? '@' + r.username : (r.first_name || ('user' + r.tg_id));
        if (lbPeriod === 'week') {
          return `
            <div class="snake-lb-row">
              <div class="snake-lb-rank ${rankCls}">${medal}</div>
              <div>
                <div class="snake-lb-name">${escape(name)}</div>
                <div class="snake-lb-sub">${r.runs} ранов</div>
              </div>
              <div class="snake-lb-coins">${fmt(r.best_coins)}</div>
            </div>
          `;
        }
        return `
          <div class="snake-lb-row">
            <div class="snake-lb-rank ${rankCls}">${medal}</div>
            <div>
              <div class="snake-lb-name">${escape(name)}</div>
              <div class="snake-lb-sub">lvl ${r.level} · ${r.runs} ранов · best ${fmt(r.best_coins)}</div>
            </div>
            <div class="snake-lb-coins">${fmt(r.coins_lifetime)}</div>
          </div>
        `;
      }).join('');
    } catch (e) {
      const el = document.getElementById('snake-lb-list');
      if (el) el.innerHTML = '<div class="loader">Ошибка: ' + escape(e.message) + '</div>';
    }
  }

  // ═════════════════ Cleanup on view change ═════════════════
  window.snakeLeave = function() {
    if (SS.afkRefreshTimer) {
      clearInterval(SS.afkRefreshTimer);
      SS.afkRefreshTimer = null;
    }
  };
})();
