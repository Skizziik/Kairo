/* TRYLLA EXCHANGE — биржа.
   Tabs: Рынок / Портфель / Новости / Топ / Профиль
   Хитрые canvas-графики, реалтайм обновления.
   Connects to global `state` (window.state), `api`, `fmt`, `escape`, `toast`. */
(() => {
  const MS = {
    state: null,
    assets: null,
    news: null,
    inited: false,
    activeTab: 'market',
    activeFilter: 'all',     // category filter
    searchQuery: '',
    chart: null,             // currently-viewed asset chart data
    chartTimer: null,
    pollTimer: null,
    activeAsset: null,
    chartTf: '10m',          // timeframe: 10m | 1h | 12h | 24h
  };
  const root = () => document.getElementById('market-app');
  const tg   = window.Telegram?.WebApp;

  // Format TRYLLA cents → display
  function fmtT(cents) {
    return fmt(Math.floor(cents / 100));
  }
  function fmtTSign(cents) {
    const v = cents >= 0 ? '+' : '';
    return v + fmt(Math.floor(cents / 100));
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
  function fmtTCompact(cents) { return fmtCompact(Math.floor(cents / 100)); }
  function priceFmt(cents) {
    // Smart price formatting based on magnitude
    const v = cents / 100;
    if (v >= 1000) return fmt(Math.round(v));
    if (v >= 10)   return v.toFixed(2);
    if (v >= 0.1)  return v.toFixed(3);
    return v.toFixed(5);
  }

  function rarityColor(rarity) {
    return ({
      common: '#9aa6b2',
      uncommon: '#5cc15c',
      rare: '#5aa9ff',
      epic: '#b96eff',
      legendary: '#ff9c1a',
      mythic: '#ff58e8',
    })[rarity] || '#888';
  }

  function categoryName(cat) {
    return ({
      crypto: '🪙 Крипта',
      metals: '⚙ Металлы',
      energy: '⚡ Энергия',
      stocks: '🏢 Игровые',
      tech: '🌐 Tech',
      rare: '💎 Редкие',
      agro: '🌾 Агро',
      indexes: '📈 Индексы',
    })[cat] || cat;
  }

  // ════════ BOOT ════════
  async function boot() {
    if (MS.inited) { await refresh(); return; }
    MS.inited = true;
    try {
      const [state, assets, news] = await Promise.all([
        api('/api/market/state'),
        api('/api/market/assets'),
        api('/api/market/news?limit=20'),
      ]);
      MS.state = state; MS.assets = assets; MS.news = news;
    } catch (e) {
      const r = root(); if (r) r.innerHTML = '<div class="loader">Ошибка: ' + escape(e.message) + '</div>';
      return;
    }
    paintHub();
    startPoll();
  }

  function startPoll() {
    if (MS.pollTimer) clearInterval(MS.pollTimer);
    MS.pollTimer = setInterval(async () => {
      const isActive = document.querySelector('.view[data-view="market"].active');
      if (!isActive) return;
      try {
        const [state, assets] = await Promise.all([
          api('/api/market/state'),
          api('/api/market/assets'),
        ]);
        MS.state = state; MS.assets = assets;
        // In-place value updates only — НЕ полный ре-рендер. Иначе сбивается
        // скрол в списке/полосе категорий и фокус в поиске каждые 5 сек.
        if (!document.querySelector('.market-trade-modal')) {
          liveRefresh();
        } else {
          updateTradeModalPrice();
        }
      } catch (e) {}
    }, 5000);
  }

  // ─── Live polling refresh — обновляем только цифры, без перерисовки DOM ───
  function liveRefresh() {
    updateTopBar();
    if (MS.activeTab === 'market')    updateMarketPrices();
    if (MS.activeTab === 'portfolio') updatePortfolioPrices();
    // news/lb/bank/convert — top bar достаточно
  }

  function updateTopBar() {
    const s = MS.state;
    if (!s) return;
    const setText = (sel, txt) => {
      const el = document.querySelector(sel);
      if (el && el.textContent !== txt) el.textContent = txt;
    };
    setText('.market-balance .market-balance-v', fmtTCompact(s.trylla));
    setText('.market-portfolio .market-balance-v', fmtTCompact(s.portfolio_value));
    setText('.market-total .market-balance-v', fmtTCompact(s.total_value));
  }

  function updateMarketPrices() {
    if (!MS.assets) return;
    const map = new Map(MS.assets.map(a => [a.key, a]));
    document.querySelectorAll('.market-asset-row').forEach(row => {
      const a = map.get(row.dataset.asset);
      if (!a) return;
      const pv = row.querySelector('.market-price-v');
      if (pv) {
        const txt = priceFmt(a.current_price);
        if (pv.textContent !== txt) pv.textContent = txt;
      }
      const pc = row.querySelector('.market-price-change');
      if (pc) {
        const change = a.change_24h_pct;
        const cls = change > 0.5 ? 'up' : change < -0.5 ? 'down' : 'flat';
        if (!pc.classList.contains(cls)) {
          pc.classList.remove('up','down','flat');
          pc.classList.add(cls);
        }
        const txt = (change > 0 ? '+' : '') + change.toFixed(2) + '%';
        if (pc.textContent !== txt) pc.textContent = txt;
      }
    });
  }

  function updatePortfolioPrices() {
    const s = MS.state;
    if (!s) return;
    const holdings = s.holdings || [];
    // Если кол-во позиций изменилось (купил/продал) — нужен полный ре-рендер
    const rows = document.querySelectorAll('.market-port-row');
    if (rows.length !== holdings.length) {
      const c = document.getElementById('market-tab-content');
      if (c) paintPortfolio(c);
      return;
    }
    const totalValue = holdings.reduce((acc, h) => acc + h.value, 0);
    const map = new Map(holdings.map(h => [h.asset_key, h]));
    rows.forEach(row => {
      const h = map.get(row.dataset.asset);
      if (!h) return;
      const sharePct = totalValue > 0 ? (h.value / totalValue * 100) : 0;
      const setText = (sel, txt) => {
        const el = row.querySelector(sel);
        if (el && el.textContent !== txt) el.textContent = txt;
      };
      setText('.market-port-value', fmtTCompact(h.value));
      const plEl = row.querySelector('.market-port-pl');
      if (plEl) {
        const cls = h.pl >= 0 ? 'gain' : 'loss';
        if (!plEl.classList.contains(cls)) {
          plEl.classList.remove('gain','loss');
          plEl.classList.add(cls);
        }
        const txt = `${fmtTSign(h.pl)} (${h.pl_pct.toFixed(2)}%)`;
        if (plEl.textContent !== txt) plEl.textContent = txt;
      }
      setText('.market-port-share', `${sharePct.toFixed(1)}% портфеля`);
    });
  }

  async function refresh() {
    try {
      const [state, assets, news] = await Promise.all([
        api('/api/market/state'),
        api('/api/market/assets'),
        api('/api/market/news?limit=20'),
      ]);
      MS.state = state; MS.assets = assets; MS.news = news;
      paintHub();
    } catch (e) {}
  }

  // ════════ HUB ════════
  function paintHub() {
    const r = root();
    if (!r || !MS.state) return;
    const s = MS.state;
    const xpPct = s.next_level_xp > 0
      ? Math.min(100, Math.floor(s.xp / s.next_level_xp * 100))
      : 0;
    r.innerHTML = `
      <div class="market-hub">
        <div class="market-top">
          <div class="market-top-row">
            <div class="market-balance">
              <div class="market-balance-l">TRYLLA</div>
              <div class="market-balance-v">${fmtTCompact(s.trylla)}</div>
            </div>
            <div class="market-portfolio">
              <div class="market-balance-l">Портфель</div>
              <div class="market-balance-v gold">${fmtTCompact(s.portfolio_value)}</div>
            </div>
            <div class="market-total">
              <div class="market-balance-l">Всего</div>
              <div class="market-balance-v">${fmtTCompact(s.total_value)}</div>
            </div>
          </div>
          <div class="market-stats-row">
            <div class="market-stat">
              <span class="market-stat-l">⭐ LVL</span>
              <span class="market-stat-v">${s.level}</span>
            </div>
            <div class="market-xp-bar"><div class="market-xp-fill" style="width:${xpPct}%"></div></div>
            <div class="market-stat">
              <span class="market-stat-l">P/L</span>
              <span class="market-stat-v ${s.total_realized_pl >= 0 ? 'gain' : 'loss'}">${fmtTSign(s.total_realized_pl)}</span>
            </div>
            <div class="market-stat">
              <span class="market-stat-l">W/R</span>
              <span class="market-stat-v">${s.win_rate}%</span>
            </div>
          </div>
        </div>
        <div class="market-tabs">
          <button class="market-tab" data-tab="market"><span>📊</span><span>Рынок</span></button>
          <button class="market-tab" data-tab="portfolio"><span>💼</span><span>Портфель</span></button>
          <button class="market-tab" data-tab="news"><span>📰</span><span>Новости</span></button>
          <button class="market-tab" data-tab="lb"><span>🏆</span><span>Топ</span></button>
          <button class="market-tab" data-tab="bank"><span>🏦</span><span>Банк</span></button>
          <button class="market-tab" data-tab="convert"><span>💱</span><span>Обмен</span></button>
        </div>
        <div class="market-tab-content" id="market-tab-content"></div>
      </div>
    `;
    r.querySelectorAll('.market-tab').forEach(b => {
      b.addEventListener('click', () => switchTab(b.dataset.tab));
    });
    switchTab(MS.activeTab);
  }

  function repaintCurrentTab() {
    const c = document.getElementById('market-tab-content');
    if (!c) return;
    updateTopBar();
    if (MS.activeTab === 'market')    paintMarket(c);
    if (MS.activeTab === 'portfolio') paintPortfolio(c);
    if (MS.activeTab === 'news')      paintNews(c);
    if (MS.activeTab === 'lb')        paintLeaderboard(c);
    if (MS.activeTab === 'bank')      paintBank(c);
    if (MS.activeTab === 'convert')   paintConvert(c);
  }

  function switchTab(tab) {
    MS.activeTab = tab;
    const r = root(); if (!r) return;
    r.querySelectorAll('.market-tab').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
    repaintCurrentTab();
  }

  // ─────── MARKET TAB ───────
  function paintMarket(c) {
    if (!MS.assets) { c.innerHTML = '<div class="loader">…</div>'; return; }
    const cats = ['all', 'crypto', 'metals', 'energy', 'stocks', 'tech', 'rare', 'agro', 'indexes'];
    const filterStrip = cats.map(cat => `
      <button class="market-cat-pill ${MS.activeFilter === cat ? 'active' : ''}" data-cat="${cat}">
        ${cat === 'all' ? '🌐 Все' : categoryName(cat)}
      </button>
    `).join('');

    let filtered = MS.assets;
    if (MS.activeFilter !== 'all') {
      filtered = filtered.filter(a => a.category === MS.activeFilter);
    }
    if (MS.searchQuery) {
      const q = MS.searchQuery.toLowerCase();
      filtered = filtered.filter(a =>
        a.name.toLowerCase().includes(q) ||
        a.symbol.toLowerCase().includes(q)
      );
    }

    // Group by category for display headers
    const rows = filtered.map(a => {
      const change = a.change_24h_pct;
      const changeClass = change > 0.5 ? 'up' : change < -0.5 ? 'down' : 'flat';
      const changeStr = (change > 0 ? '+' : '') + change.toFixed(2) + '%';
      return `
        <div class="market-asset-row" data-asset="${a.key}">
          <img class="market-asset-img" src="./${a.image}" alt="" loading="lazy" onerror="this.style.display='none'" />
          <div class="market-asset-info">
            <div class="market-asset-name">
              <span>${escape(a.name)}</span>
              <span class="market-rarity dot-${a.rarity}"></span>
            </div>
            <div class="market-asset-sym">${escape(a.symbol)} · ${escape(categoryName(a.category))}</div>
          </div>
          <div class="market-asset-price">
            <div class="market-price-v">${priceFmt(a.current_price)}</div>
            <div class="market-price-change ${changeClass}">${changeStr}</div>
          </div>
        </div>
      `;
    }).join('');

    c.innerHTML = `
      <input type="search" class="market-search" id="market-search" placeholder="Поиск по названию или тикеру..." value="${escape(MS.searchQuery)}" />
      <div class="market-cat-strip">${filterStrip}</div>
      <div class="market-asset-list">${rows || '<div class="loader">Ничего не найдено</div>'}</div>
    `;
    c.querySelectorAll('.market-cat-pill').forEach(b => {
      b.addEventListener('click', () => {
        MS.activeFilter = b.dataset.cat;
        paintMarket(c);
      });
    });
    c.querySelectorAll('.market-asset-row').forEach(b => {
      b.addEventListener('click', () => openAssetDetail(b.dataset.asset));
    });
    const search = document.getElementById('market-search');
    if (search) search.addEventListener('input', (e) => {
      MS.searchQuery = e.target.value;
      // Debounce by re-painting only changed list
      const list = c.querySelector('.market-asset-list');
      if (list) {
        // re-render just the list
        let f = MS.assets;
        if (MS.activeFilter !== 'all') f = f.filter(a => a.category === MS.activeFilter);
        if (MS.searchQuery) {
          const q = MS.searchQuery.toLowerCase();
          f = f.filter(a => a.name.toLowerCase().includes(q) || a.symbol.toLowerCase().includes(q));
        }
        list.innerHTML = f.map(a => {
          const change = a.change_24h_pct;
          const changeClass = change > 0.5 ? 'up' : change < -0.5 ? 'down' : 'flat';
          const changeStr = (change > 0 ? '+' : '') + change.toFixed(2) + '%';
          return `
            <div class="market-asset-row" data-asset="${a.key}">
              <img class="market-asset-img" src="./${a.image}" alt="" loading="lazy" onerror="this.style.display='none'" />
              <div class="market-asset-info">
                <div class="market-asset-name"><span>${escape(a.name)}</span><span class="market-rarity dot-${a.rarity}"></span></div>
                <div class="market-asset-sym">${escape(a.symbol)} · ${escape(categoryName(a.category))}</div>
              </div>
              <div class="market-asset-price">
                <div class="market-price-v">${priceFmt(a.current_price)}</div>
                <div class="market-price-change ${changeClass}">${changeStr}</div>
              </div>
            </div>`;
        }).join('') || '<div class="loader">Ничего не найдено</div>';
        list.querySelectorAll('.market-asset-row').forEach(r => {
          r.addEventListener('click', () => openAssetDetail(r.dataset.asset));
        });
      }
    });
  }

  // ─────── ASSET DETAIL (overlay) ───────
  // Polling cadence per timeframe — для 12ч/24ч смысла дёргать каждые 3с нет.
  const TF_POLL_MS = { '10m': 3000, '1h': 5000, '12h': 30000, '24h': 60000 };

  function renderHoldingRow(assetKey, asset) {
    const h = MS.state?.holdings?.find(x => x.asset_key === assetKey);
    if (!h || h.quantity <= 0) {
      return '<div class="market-holding-empty">📦 Нет в портфеле</div>';
    }
    // Пересчитываем стоимость по свежей цене актива (state может отставать)
    const valueCents = Math.floor(h.quantity * asset.current_price / 1_000_000);
    const cost = h.cost;
    const pl = valueCents - cost;
    const plPct = cost > 0 ? (pl / cost * 100) : 0;
    const cls = pl >= 0 ? 'gain' : 'loss';
    const sign = pl >= 0 ? '+' : '';
    return `
      <div class="market-holding-row">
        <div class="market-holding-line">
          <span class="market-holding-l">У тебя</span>
          <b class="market-holding-qty">${(h.quantity / 1_000_000).toFixed(6)}</b>
          <span class="market-holding-sym">${escape(asset.symbol)}</span>
        </div>
        <div class="market-holding-line dim">
          ≈ <b>${fmtTCompact(valueCents)}</b> TRYLLA
          <span class="market-holding-pl ${cls}">${sign}${fmtTCompact(pl)} (${plPct.toFixed(1)}%)</span>
        </div>
      </div>
    `;
  }

  async function openAssetDetail(assetKey) {
    MS.activeAsset = assetKey;
    let chart;
    try {
      chart = await api(`/api/market/chart/${assetKey}?tf=${MS.chartTf}`);
      if (!chart.ok) { toast(chart.error || 'Ошибка'); return; }
    } catch (e) { toast(e.message); return; }
    MS.chart = chart;

    const tfBtn = (k, lbl) => `<button class="market-tf-btn ${MS.chartTf === k ? 'active' : ''}" data-tf="${k}">${lbl}</button>`;

    const overlay = document.createElement('div');
    overlay.className = 'market-detail-overlay';
    overlay.innerHTML = `
      <div class="market-detail-box">
        <button class="market-detail-close" id="md-close">←</button>
        <div class="market-detail-head">
          <img src="./${chart.asset.image}" alt="" onerror="this.style.display='none'" />
          <div>
            <div class="market-detail-name">${escape(chart.asset.name)}</div>
            <div class="market-detail-sym">${escape(chart.asset.symbol)}</div>
          </div>
          <div class="market-detail-price">
            <div class="market-detail-price-v" id="md-price">${priceFmt(chart.asset.current_price)}</div>
            <div class="market-detail-stats" id="md-stats">
              H: <b>${priceFmt(chart.asset.high_24h)}</b> · L: <b>${priceFmt(chart.asset.low_24h)}</b>
            </div>
          </div>
        </div>
        <div class="market-tf-bar" id="md-tf">
          ${tfBtn('10m', '10м')}
          ${tfBtn('1h', '1ч')}
          ${tfBtn('12h', '12ч')}
          ${tfBtn('24h', '24ч')}
        </div>
        <div class="market-chart-wrap">
          <canvas id="md-canvas"></canvas>
        </div>
        <div class="market-detail-holding" id="md-holding">${renderHoldingRow(assetKey, chart.asset)}</div>
        <div class="market-detail-actions">
          <button class="market-buy-btn" id="md-buy">⇪ КУПИТЬ</button>
          <button class="market-sell-btn" id="md-sell">⇩ ПРОДАТЬ</button>
        </div>
        <div class="market-detail-info">
          <div class="market-info-row"><span>Волатильность</span><b>${(chart.asset.volatility * 100).toFixed(0)}%</b></div>
          <div class="market-info-row"><span>Категория</span><b>${escape(categoryName(chart.asset.category))}</b></div>
          <div class="market-info-row"><span>Редкость</span><b style="color:${rarityColor(chart.asset.rarity)}">${chart.asset.rarity.toUpperCase()}</b></div>
        </div>
      </div>
    `;
    document.body.appendChild(overlay);
    const canvas = document.getElementById('md-canvas');
    drawChart(canvas, chart.points, chart.asset);

    document.getElementById('md-close').addEventListener('click', () => {
      MS.activeAsset = null;
      if (MS.chartTimer) { clearInterval(MS.chartTimer); MS.chartTimer = null; }
      overlay.remove();
    });
    document.getElementById('md-buy').addEventListener('click', () => openTradeModal(chart.asset, 'buy'));
    document.getElementById('md-sell').addEventListener('click', () => openTradeModal(chart.asset, 'sell'));

    const fetchChart = async () => {
      if (!MS.activeAsset || MS.activeAsset !== assetKey) return null;
      try {
        const upd = await api(`/api/market/chart/${assetKey}?tf=${MS.chartTf}`);
        if (!upd.ok) return null;
        MS.chart = upd;
        const canvas2 = document.getElementById('md-canvas');
        if (canvas2) drawChart(canvas2, upd.points, upd.asset);
        const pv = document.getElementById('md-price');
        if (pv) pv.textContent = priceFmt(upd.asset.current_price);
        const stats = document.getElementById('md-stats');
        if (stats) stats.innerHTML = `H: <b>${priceFmt(upd.asset.high_24h)}</b> · L: <b>${priceFmt(upd.asset.low_24h)}</b>`;
        const hold = document.getElementById('md-holding');
        if (hold) hold.innerHTML = renderHoldingRow(assetKey, upd.asset);
        return upd;
      } catch (e) { return null; }
    };

    const startTimer = () => {
      if (MS.chartTimer) clearInterval(MS.chartTimer);
      const ms = TF_POLL_MS[MS.chartTf] || 3000;
      MS.chartTimer = setInterval(() => {
        if (!MS.activeAsset || MS.activeAsset !== assetKey) {
          clearInterval(MS.chartTimer); MS.chartTimer = null; return;
        }
        fetchChart();
      }, ms);
    };

    document.querySelectorAll('#md-tf .market-tf-btn').forEach(b => {
      b.addEventListener('click', async () => {
        const newTf = b.dataset.tf;
        if (newTf === MS.chartTf) return;
        MS.chartTf = newTf;
        document.querySelectorAll('#md-tf .market-tf-btn').forEach(x => {
          x.classList.toggle('active', x.dataset.tf === newTf);
        });
        await fetchChart();
        startTimer();
      });
    });

    startTimer();
  }

  // ─────── CANVAS CHART (line + area + axis) ───────
  function drawChart(canvas, points, asset) {
    if (!canvas || !points || points.length < 2) return;
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    const W = canvas.width = rect.width * dpr;
    const H = canvas.height = rect.height * dpr;
    canvas.style.width = rect.width + 'px';
    canvas.style.height = rect.height + 'px';
    const ctx = canvas.getContext('2d');
    ctx.scale(dpr, dpr);

    const padL = 40, padR = 12, padT = 10, padB = 24;
    const w = rect.width - padL - padR;
    const h = rect.height - padT - padB;

    // Find min/max
    const prices = points.map(p => p.price);
    let lo = Math.min(...prices);
    let hi = Math.max(...prices);
    if (lo === hi) { lo *= 0.99; hi *= 1.01; }
    const range = hi - lo;
    const pad = range * 0.1;
    lo -= pad; hi += pad;

    // Grid
    ctx.strokeStyle = 'rgba(255,255,255,0.05)';
    ctx.lineWidth = 1;
    for (let i = 0; i <= 4; i++) {
      const y = padT + (h * i / 4);
      ctx.beginPath();
      ctx.moveTo(padL, y);
      ctx.lineTo(padL + w, y);
      ctx.stroke();
      const v = hi - (hi - lo) * (i / 4);
      ctx.fillStyle = '#666';
      ctx.font = '10px sans-serif';
      ctx.textAlign = 'right';
      ctx.fillText(priceFmt(v), padL - 4, y + 3);
    }

    // Trend color
    const startPrice = prices[0];
    const endPrice = prices[prices.length - 1];
    const trendUp = endPrice >= startPrice;
    const lineColor = trendUp ? '#5cc15c' : '#eb4b4b';
    const fillColor = trendUp ? 'rgba(92,193,92,0.18)' : 'rgba(235,75,75,0.18)';

    // Area fill
    ctx.beginPath();
    points.forEach((p, i) => {
      const x = padL + (w * i / (points.length - 1));
      const y = padT + h - ((p.price - lo) / (hi - lo)) * h;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.lineTo(padL + w, padT + h);
    ctx.lineTo(padL, padT + h);
    ctx.closePath();
    ctx.fillStyle = fillColor;
    ctx.fill();

    // Line
    ctx.beginPath();
    points.forEach((p, i) => {
      const x = padL + (w * i / (points.length - 1));
      const y = padT + h - ((p.price - lo) / (hi - lo)) * h;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.strokeStyle = lineColor;
    ctx.lineWidth = 2;
    ctx.stroke();

    // End-point dot with glow
    const lastX = padL + w;
    const lastY = padT + h - ((endPrice - lo) / (hi - lo)) * h;
    ctx.shadowColor = lineColor;
    ctx.shadowBlur = 12;
    ctx.fillStyle = lineColor;
    ctx.beginPath();
    ctx.arc(lastX, lastY, 4, 0, Math.PI * 2);
    ctx.fill();
    ctx.shadowBlur = 0;

    // Axis labels
    ctx.fillStyle = '#888';
    ctx.font = '9px sans-serif';
    ctx.textAlign = 'left';
    ctx.fillText('сейчас', padL, padT + h + 14);
    ctx.textAlign = 'right';
    ctx.fillText('-' + Math.floor(points.length * 5 / 60) + 'м', padL + w, padT + h + 14);
  }

  // ─────── TRADE MODAL ───────
  function openTradeModal(asset, side) {
    const overlay = document.createElement('div');
    overlay.className = 'market-trade-modal';
    const myCash = MS.state ? MS.state.trylla : 0;
    const holding = MS.state.holdings.find(h => h.asset_key === asset.key);
    const myQty = holding ? holding.quantity : 0;

    if (side === 'sell' && !holding) {
      toast('У тебя нет позиции по этому активу');
      return;
    }

    // Эффективная цена покупки (с учётом spread+commission ≈ 1.45%) — для превью.
    // Реальные значения посчитает бэк, тут лишь оценка чтобы юзер видел сколько получит.
    const FEE_TOTAL = 0.0145;   // 0.3% spread/2 + 1% commission + запас
    const buyEffective = asset.current_price * (1 + FEE_TOTAL);
    const sellEffective = asset.current_price * (1 - FEE_TOTAL);

    overlay.innerHTML = `
      <div class="market-trade-box">
        <button class="market-trade-close" id="mt-close">×</button>
        <div class="market-trade-head ${side}">
          <img src="./${asset.image}" onerror="this.style.display='none'" />
          <div>
            <div class="market-trade-title">${side === 'buy' ? 'Купить' : 'Продать'} ${escape(asset.name)}</div>
            <div class="market-trade-sym" id="mt-price">${escape(asset.symbol)} · <b>${priceFmt(asset.current_price)}</b></div>
          </div>
        </div>
        ${side === 'buy' ? `
          <div class="market-trade-bal">Доступно: <b>${fmtTCompact(myCash)}</b> TRYLLA</div>
          <div class="market-trade-modes">
            <button class="market-mode-btn active" data-mode="cash">💰 По TRYLLA</button>
            <button class="market-mode-btn" data-mode="qty">📦 По штукам</button>
          </div>
          <input type="number" class="market-trade-input" id="mt-amt" placeholder="Сумма (TRYLLA)" min="0" step="any" />
          <div class="market-trade-preview" id="mt-preview">Введи сумму…</div>
          <div class="market-trade-quick" id="mt-quick">
            <button data-amt="100">100</button>
            <button data-amt="1000">1K</button>
            <button data-amt="10000">10K</button>
            <button data-amt="quarter">25%</button>
            <button data-amt="half">50%</button>
            <button data-amt="all">ВСЁ</button>
          </div>
          <div class="market-trade-fee">Комиссия 1% (снижается перками) · Спред 0.3%</div>
          <button class="market-trade-go buy" id="mt-go">⇪ КУПИТЬ</button>
        ` : `
          <div class="market-trade-bal">У тебя: <b>${(myQty / 1_000_000).toFixed(6)}</b> ${escape(asset.symbol)} @ avg <b>${priceFmt(holding.avg_buy_price)}</b></div>
          <div class="market-trade-pl ${holding.pl >= 0 ? 'gain' : 'loss'}">
            P/L: ${fmtTSign(holding.pl)} (${holding.pl_pct.toFixed(2)}%)
          </div>
          <div class="market-trade-quick big" id="mt-quick">
            <button data-pct="25">25%</button>
            <button data-pct="50">50%</button>
            <button data-pct="75">75%</button>
            <button data-pct="100">100%</button>
          </div>
          <input type="number" class="market-trade-input" id="mt-pct" placeholder="Свой % (1-100)" min="1" max="100" step="1" />
          <div class="market-trade-preview" id="mt-preview">Получишь ≈ ${fmtTCompact(Math.floor(myQty * sellEffective / 1_000_000))} TRYLLA</div>
          <button class="market-trade-go sell" id="mt-go" data-pct="100">⇩ ПРОДАТЬ 100%</button>
        `}
      </div>
    `;
    document.body.appendChild(overlay);
    document.getElementById('mt-close').addEventListener('click', () => overlay.remove());
    overlay.addEventListener('click', e => { if (e.target === overlay) overlay.remove(); });

    if (side === 'buy') {
      const input    = document.getElementById('mt-amt');
      const preview  = document.getElementById('mt-preview');
      let mode = 'cash';   // 'cash' = ввод в TRYLLA, 'qty' = в штуках

      const updatePreview = () => {
        const v = Number(input.value);
        if (!v || v <= 0) { preview.textContent = mode === 'cash' ? 'Введи сумму…' : 'Введи количество…'; return; }
        if (mode === 'cash') {
          // cents = v * 100; qty ≈ cents / buyEffective (×1 since price in cents)
          const qty = (v * 100) / buyEffective;
          preview.innerHTML = `Получишь ≈ <b>${qty.toFixed(6)}</b> ${escape(asset.symbol)}`;
        } else {
          // qty = v штук → cost = qty * buyEffective (cents) / 100 = TRYLLA
          const cost = (v * buyEffective) / 100;
          const cls = (cost * 100) > myCash ? 'over' : '';
          preview.innerHTML = `Стоимость ≈ <b class="${cls}">${fmtTCompact(Math.ceil(cost * 100))}</b> TRYLLA`;
        }
      };

      const setMode = (m) => {
        mode = m;
        overlay.querySelectorAll('.market-mode-btn').forEach(b => {
          b.classList.toggle('active', b.dataset.mode === m);
        });
        input.placeholder = m === 'cash' ? 'Сумма (TRYLLA)' : `Количество (${asset.symbol})`;
        // Reset value when switching to avoid confusion
        input.value = '';
        updatePreview();
      };

      overlay.querySelectorAll('.market-mode-btn').forEach(b => {
        b.addEventListener('click', () => setMode(b.dataset.mode));
      });

      overlay.querySelectorAll('#mt-quick [data-amt]').forEach(b => {
        b.addEventListener('click', () => {
          // Quick-кнопки всегда работают в режиме TRYLLA (если был qty — переключаем).
          if (mode !== 'cash') setMode('cash');
          const v = b.dataset.amt;
          const cashUnits = myCash / 100;
          if (v === 'quarter') input.value = Math.floor(cashUnits * 0.25);
          else if (v === 'half') input.value = Math.floor(cashUnits * 0.5);
          else if (v === 'all') input.value = Math.floor(cashUnits);
          else input.value = v;
          updatePreview();
        });
      });

      input.addEventListener('input', updatePreview);

      document.getElementById('mt-go').addEventListener('click', async () => {
        const v = Number(input.value);
        if (!v || v <= 0) { toast(mode === 'cash' ? 'Введи сумму' : 'Введи количество'); return; }
        const body = { asset_key: asset.key };
        if (mode === 'cash') {
          const cents = Math.max(1, Math.min(myCash, Math.floor(v * 100)));
          body.cash_amount = cents;
        } else {
          // микро-юниты = штук × 1e6
          body.quantity_micro = Math.max(1, Math.floor(v * 1_000_000));
        }
        const r = await api('/api/market/buy', { method: 'POST', body: JSON.stringify(body) });
        if (!r.ok) { toast(r.error || 'Ошибка'); return; }
        toast('✓ Куплено ' + (r.quantity / 1_000_000).toFixed(6) + ' ' + asset.symbol);
        tg?.HapticFeedback?.notificationOccurred?.('success');
        overlay.remove();
        await refresh();
      });
    } else {
      let chosenPct = 100;
      const pctInput = document.getElementById('mt-pct');
      const preview  = document.getElementById('mt-preview');
      const goBtn    = document.getElementById('mt-go');

      const updatePctPreview = (pct) => {
        const sellQty = myQty * (pct / 100);
        const grossT  = (sellQty * sellEffective) / 1_000_000 / 100;   // TRYLLA (whole)
        const cost    = (sellQty * holding.avg_buy_price) / 1_000_000 / 100;
        const pl      = grossT - cost;
        const sign    = pl >= 0 ? '+' : '';
        const cls     = pl >= 0 ? 'gain' : 'loss';
        preview.innerHTML = `Получишь ≈ <b>${fmtTCompact(Math.floor(grossT))}</b> TRYLLA · <span class="${cls}">${sign}${fmtTCompact(Math.floor(pl))}</span>`;
        goBtn.textContent = `⇩ ПРОДАТЬ ${pct}%`;
        goBtn.dataset.pct = pct;
      };

      const setPct = (pct) => {
        chosenPct = Math.max(1, Math.min(100, Math.floor(pct)));
        overlay.querySelectorAll('.market-trade-quick.big button').forEach(x => {
          x.classList.toggle('active', Number(x.dataset.pct) === chosenPct);
        });
        updatePctPreview(chosenPct);
      };

      overlay.querySelectorAll('.market-trade-quick.big [data-pct]').forEach(b => {
        b.addEventListener('click', () => {
          pctInput.value = '';
          setPct(Number(b.dataset.pct));
        });
      });
      pctInput.addEventListener('input', () => {
        const v = Number(pctInput.value);
        if (v >= 1 && v <= 100) setPct(v);
      });

      goBtn.addEventListener('click', async () => {
        const r = await api('/api/market/sell', {
          method: 'POST',
          body: JSON.stringify({ asset_key: asset.key, quantity_pct: chosenPct }),
        });
        if (!r.ok) { toast(r.error || 'Ошибка'); return; }
        const sign = r.realized_pl >= 0 ? '+' : '';
        toast(`✓ Продано · P/L ${sign}${fmt(Math.floor(r.realized_pl/100))} TRYLLA`);
        tg?.HapticFeedback?.notificationOccurred?.('success');
        overlay.remove();
        await refresh();
      });

      // Initial preview at default 100%
      setPct(100);
    }
  }

  function updateTradeModalPrice() {
    const modal = document.querySelector('.market-trade-modal');
    if (!modal || !MS.assets) return;
    const head = modal.querySelector('.market-trade-head img');
    if (!head) return;
    // Find asset by image
    // Simpler: do nothing for now — rerender on full refresh
  }

  // ─────── PORTFOLIO ───────
  function paintPortfolio(c) {
    const s = MS.state;
    if (!s) return;
    const holdings = s.holdings || [];
    if (holdings.length === 0) {
      c.innerHTML = `
        <div class="market-empty">
          <div class="market-empty-icon">💼</div>
          <div class="market-empty-title">Портфель пуст</div>
          <div class="market-empty-sub">Купи активы во вкладке Рынок</div>
        </div>
      `;
      return;
    }

    const totalCost = holdings.reduce((s, h) => s + h.cost, 0);
    const totalValue = holdings.reduce((s, h) => s + h.value, 0);
    const totalPL = totalValue - totalCost;
    const totalPLPct = totalCost > 0 ? (totalPL / totalCost * 100) : 0;

    const rows = holdings.map(h => {
      const plClass = h.pl >= 0 ? 'gain' : 'loss';
      const sharePct = totalValue > 0 ? (h.value / totalValue * 100) : 0;
      return `
        <div class="market-port-row" data-asset="${h.asset_key}">
          <img src="./${h.image}" onerror="this.style.display='none'" />
          <div class="market-port-info">
            <div class="market-port-name">${escape(h.name)}</div>
            <div class="market-port-sym">${escape(h.symbol)} · ${(h.quantity / 1_000_000).toFixed(6)}</div>
          </div>
          <div class="market-port-price">
            <div class="market-port-value">${fmtTCompact(h.value)}</div>
            <div class="market-port-pl ${plClass}">${fmtTSign(h.pl)} (${h.pl_pct.toFixed(2)}%)</div>
            <div class="market-port-share">${sharePct.toFixed(1)}% портфеля</div>
          </div>
        </div>
      `;
    }).join('');

    c.innerHTML = `
      <div class="market-port-summary">
        <div class="market-port-card">
          <div class="market-stat-l">Стоимость</div>
          <div class="market-stat-v">${fmtTCompact(totalValue)}</div>
        </div>
        <div class="market-port-card">
          <div class="market-stat-l">Вложено</div>
          <div class="market-stat-v">${fmtTCompact(totalCost)}</div>
        </div>
        <div class="market-port-card">
          <div class="market-stat-l">P/L</div>
          <div class="market-stat-v ${totalPL >= 0 ? 'gain' : 'loss'}">${fmtTSign(totalPL)}</div>
        </div>
        <div class="market-port-card">
          <div class="market-stat-l">%</div>
          <div class="market-stat-v ${totalPL >= 0 ? 'gain' : 'loss'}">${totalPLPct.toFixed(2)}%</div>
        </div>
      </div>
      <div class="market-port-list">${rows}</div>
    `;
    c.querySelectorAll('.market-port-row').forEach(r => {
      r.addEventListener('click', () => openAssetDetail(r.dataset.asset));
    });
  }

  // ─────── NEWS ───────
  function paintNews(c) {
    if (!MS.news) { c.innerHTML = '<div class="loader">…</div>'; return; }
    if (MS.news.length === 0) {
      c.innerHTML = '<div class="market-empty"><div class="market-empty-icon">📰</div><div class="market-empty-title">Тишина</div><div class="market-empty-sub">Новости появятся скоро</div></div>';
      return;
    }
    const rows = MS.news.map(n => {
      const typeBadge = ({
        positive: '📈 Позитив', negative: '📉 Негатив', neutral: '➖ Нейтрально',
        rumor: '👁 Слух', regulation: '⚖ Регуляция', hack: '🔓 Взлом',
        discovery: '💎 Открытие', bankruptcy: '💸 Банкротство', war: '⚔ Война',
        whale: '🐋 Кит', hype: '🔥 Хайп', major_event: '⚡ MAJOR',
      })[n.type] || n.type;
      const sevCls = n.severity || 'medium';
      const ago = ageString(n.spawned_at);
      // affected намеренно не рендерим — пусть игроки сами догадываются,
      // что выросло, что упало, и насколько сильно. Это часть гейма.
      return `
        <div class="market-news-row sev-${sevCls} ${n.active ? 'active' : 'expired'}">
          <div class="market-news-head">
            <span class="market-news-type type-${n.type}">${typeBadge}</span>
            <span class="market-news-ago">${ago}</span>
          </div>
          <div class="market-news-headline">${escape(n.headline)}</div>
          ${n.body ? `<div class="market-news-body">${escape(n.body)}</div>` : ''}
        </div>
      `;
    }).join('');
    c.innerHTML = `<div class="market-news-list">${rows}</div>`;
  }

  function ageString(iso) {
    if (!iso) return '';
    const t = new Date(iso).getTime();
    const sec = Math.floor((Date.now() - t) / 1000);
    if (sec < 60) return `${sec}с назад`;
    if (sec < 3600) return `${Math.floor(sec/60)}мин назад`;
    return `${Math.floor(sec/3600)}ч назад`;
  }

  // ─────── LEADERBOARD ───────
  let lbActiveSort = 'total_value';
  async function paintLeaderboard(c) {
    c.innerHTML = `
      <div class="market-lb-toggle">
        <button class="market-lb-toggle-btn ${lbActiveSort === 'total_value' ? 'active' : ''}" data-sort="total_value">💰 Капитал</button>
        <button class="market-lb-toggle-btn ${lbActiveSort === 'realized_pl' ? 'active' : ''}" data-sort="realized_pl">📈 P/L</button>
        <button class="market-lb-toggle-btn ${lbActiveSort === 'win_rate' ? 'active' : ''}" data-sort="win_rate">🎯 Win%</button>
      </div>
      <div id="market-lb-list" class="market-lb-list"><div class="loader">…</div></div>
    `;
    c.querySelectorAll('[data-sort]').forEach(b => {
      b.addEventListener('click', () => {
        lbActiveSort = b.dataset.sort;
        paintLeaderboard(c);
      });
    });
    try {
      const rows = await api(`/api/market/leaderboard?sort_by=${lbActiveSort}`);
      const list = document.getElementById('market-lb-list');
      const myTgId = window.state?.me?.tg_id;
      list.innerHTML = rows.map((r, i) => {
        const rank = i + 1;
        const rankCls = rank === 1 ? 'top1' : rank === 2 ? 'top2' : rank === 3 ? 'top3' : '';
        const isMe = myTgId && Number(myTgId) === Number(r.tg_id);
        const name = r.first_name || r.username || `tg${r.tg_id}`;
        const mainV = lbActiveSort === 'realized_pl' ? r.total_realized_pl
                    : lbActiveSort === 'win_rate' ? r.win_rate
                    : r.total_value;
        const mainF = lbActiveSort === 'win_rate' ? mainV.toFixed(1) + '%'
                    : fmtTCompact(mainV) + ' T';
        const avatar = r.photo_url ? `<img src="${escape(r.photo_url)}" />` : '<span>👤</span>';
        const privIcon = r.portfolio_privacy === 'public' ? '🌐' : '🔒';
        return `
          <div class="market-lb-row ${isMe ? 'me' : ''}" data-tg="${r.tg_id}">
            <div class="market-lb-rank ${rankCls}">${rank}</div>
            <div class="market-lb-avatar">${avatar}</div>
            <div class="market-lb-info">
              <div class="market-lb-name">${escape(name)} ${isMe ? '<span class="market-lb-you">ТЫ</span>' : ''} ${privIcon}</div>
              <div class="market-lb-sub">lvl ${r.level} · ${fmt(r.total_trades)} сделок · W/R ${r.win_rate}%</div>
            </div>
            <div class="market-lb-main">
              <div class="market-lb-main-v">${mainF}</div>
              <div class="market-lb-main-l">${lbActiveSort}</div>
            </div>
          </div>
        `;
      }).join('');
      list.querySelectorAll('.market-lb-row').forEach(rw => {
        rw.addEventListener('click', () => openProfile(Number(rw.dataset.tg)));
      });
    } catch (e) {
      const list = document.getElementById('market-lb-list');
      if (list) list.innerHTML = `<div class="loader">Ошибка: ${escape(e.message)}</div>`;
    }
  }

  // ─────── PROFILE (subscribe to view) ───────
  async function openProfile(tgId) {
    let data;
    try { data = await api(`/api/market/profile/${tgId}`); }
    catch (e) { toast(e.message); return; }
    const overlay = document.createElement('div');
    overlay.className = 'market-detail-overlay';
    if (!data.ok && data.error) {
      overlay.innerHTML = `
        <div class="market-detail-box">
          <button class="market-detail-close" id="md-close">←</button>
          <div class="market-empty">
            <div class="market-empty-icon">🔒</div>
            <div class="market-empty-title">Профиль приватный</div>
            <div class="market-empty-sub">Купи подписку за 1000 TRYLLA на 24 часа</div>
            <button class="market-buy-btn" id="md-sub">💎 Подписаться (1000)</button>
          </div>
        </div>
      `;
      document.body.appendChild(overlay);
      document.getElementById('md-close').addEventListener('click', () => overlay.remove());
      document.getElementById('md-sub').addEventListener('click', async () => {
        const r = await api('/api/market/subscribe', { method: 'POST', body: JSON.stringify({ target_id: tgId }) });
        if (!r.ok) { toast(r.error || 'Ошибка'); return; }
        toast('✓ Подписка активна');
        overlay.remove();
        await refresh();
        openProfile(tgId);
      });
      return;
    }
    // Render profile (re-uses portfolio render but with read-only)
    const p = data;
    const rows = (p.holdings || []).map(h => `
      <div class="market-port-row">
        <img src="./${h.image}" onerror="this.style.display='none'" />
        <div class="market-port-info">
          <div class="market-port-name">${escape(h.name)}</div>
          <div class="market-port-sym">${escape(h.symbol)} · ${(h.quantity / 1_000_000).toFixed(6)}</div>
        </div>
        <div class="market-port-price">
          <div class="market-port-value">${fmtTCompact(h.value)}</div>
          <div class="market-port-pl ${h.pl >= 0 ? 'gain' : 'loss'}">${fmtTSign(h.pl)}</div>
        </div>
      </div>
    `).join('');
    overlay.innerHTML = `
      <div class="market-detail-box">
        <button class="market-detail-close" id="md-close">←</button>
        <div class="market-profile-head">
          <div class="market-profile-name">Игрок tg${tgId}</div>
          <div class="market-profile-stats">
            <div><b>${fmtTCompact(p.total_value)}</b><span>капитал</span></div>
            <div><b class="${p.total_realized_pl >= 0 ? 'gain' : 'loss'}">${fmtTSign(p.total_realized_pl)}</b><span>P/L lifetime</span></div>
            <div><b>${p.win_rate}%</b><span>win-rate</span></div>
            <div><b>${p.total_trades}</b><span>сделок</span></div>
          </div>
        </div>
        <div class="market-port-list">${rows || '<div class="loader">Портфель пуст</div>'}</div>
      </div>
    `;
    document.body.appendChild(overlay);
    document.getElementById('md-close').addEventListener('click', () => overlay.remove());
  }

  // ─────── BANK ───────
  async function paintBank(c) {
    c.innerHTML = '<div class="loader">Загрузка банка...</div>';
    let bank;
    try { bank = await api('/api/market/bank'); }
    catch (e) { c.innerHTML = `<div class="loader">Ошибка: ${escape(e.message)}</div>`; return; }

    const usedPct = bank.max_total_debt > 0
      ? Math.min(100, Math.floor(bank.current_total_debt / bank.max_total_debt * 100))
      : 0;

    const loansHtml = bank.active_loans.length === 0
      ? '<div class="market-empty"><div class="market-empty-icon">🏦</div><div class="market-empty-title">Кредитов нет</div><div class="market-empty-sub">Возьми если разорился — банк поможет</div></div>'
      : bank.active_loans.map(l => {
          const dueDate = new Date(l.due_at);
          const daysLeft = Math.floor((dueDate.getTime() - Date.now()) / 86400000);
          const dueText = daysLeft >= 0 ? `${daysLeft}д осталось` : `просрочка ${l.overdue_days}д`;
          const overdueClass = l.is_overdue ? 'overdue' : '';
          return `
            <div class="market-loan-row ${overdueClass}">
              <div class="market-loan-head">
                <div>
                  <div class="market-loan-title">Кредит #${l.id}</div>
                  <div class="market-loan-sub">Взят ${fmtTCompact(l.principal)} · ${(l.daily_rate*100).toFixed(0)}%/день</div>
                </div>
                <div class="market-loan-due ${l.is_overdue ? 'overdue' : ''}">${dueText}</div>
              </div>
              <div class="market-loan-amounts">
                <div><span>Долг сейчас</span><b>${fmtTCompact(l.current_debt)}</b></div>
                <div><span>Проценты</span><b>+${fmtTCompact(l.accrued_interest)}</b></div>
                <div><span>Погашено</span><b>${fmtTCompact(l.repaid)}</b></div>
              </div>
              <div class="market-loan-actions">
                <button class="market-loan-pay-half" data-loan-id="${l.id}" data-amount="${Math.floor(l.current_debt/2)}">Пол-долга</button>
                <button class="market-loan-pay-all" data-loan-id="${l.id}" data-amount="${l.current_debt}">Погасить ВСЁ (${fmtTCompact(l.current_debt)})</button>
              </div>
            </div>
          `;
        }).join('');

    c.innerHTML = `
      <div class="market-bank-header">
        <div class="market-bank-title">🏦 Банк TRYLLA</div>
        <div class="market-bank-meta">Ставка: <b>${(bank.daily_rate*100).toFixed(0)}%/день</b> · Срок: <b>${bank.default_term_days}д</b></div>
      </div>

      <div class="market-bank-limit-card">
        <div class="market-bank-limit-row">
          <span>Лимит по уровню ${bank.level}</span>
          <b>${fmtTCompact(bank.max_total_debt)} TRYLLA</b>
        </div>
        <div class="market-bank-limit-row">
          <span>Текущий долг</span>
          <b class="${bank.current_total_debt > 0 ? 'loss' : ''}">${fmtTCompact(bank.current_total_debt)}</b>
        </div>
        <div class="market-bank-bar"><div class="market-bank-bar-fill" style="width:${usedPct}%"></div></div>
        <div class="market-bank-limit-row">
          <span>Доступно</span>
          <b class="gain">${fmtTCompact(bank.available_credit)} TRYLLA</b>
        </div>
      </div>

      <div class="market-bank-take">
        <div class="market-bank-section-title">Взять кредит</div>
        <input type="number" id="market-bank-input" class="market-trade-input" placeholder="Сколько TRYLLA?" min="1" max="${Math.floor(bank.available_credit/100)}" />
        <div class="market-trade-quick">
          <button data-bank-amt="100">100</button>
          <button data-bank-amt="1000">1K</button>
          <button data-bank-amt="10000">10K</button>
          <button data-bank-amt="quarter">25%</button>
          <button data-bank-amt="half">50%</button>
          <button data-bank-amt="all">МАКС</button>
        </div>
        <button class="market-trade-go buy" id="market-bank-take">🏦 Взять кредит</button>
        <div class="market-bank-note">⚠ Не вернёшь в срок — пойдут штрафы +${(bank.overdue_rate*100).toFixed(0)}%/день</div>
      </div>

      <div class="market-bank-section-title">Активные кредиты</div>
      ${loansHtml}
    `;

    // Take loan handlers
    const input = document.getElementById('market-bank-input');
    c.querySelectorAll('[data-bank-amt]').forEach(b => {
      b.addEventListener('click', () => {
        const v = b.dataset.bankAmt;
        const avail = bank.available_credit / 100;
        if (v === 'all') input.value = Math.floor(avail);
        else if (v === 'half') input.value = Math.floor(avail * 0.5);
        else if (v === 'quarter') input.value = Math.floor(avail * 0.25);
        else input.value = v;
      });
    });
    document.getElementById('market-bank-take').addEventListener('click', async () => {
      const cents = Math.max(1, Math.min(bank.available_credit, Math.floor(Number(input.value) * 100)));
      if (!cents) { toast('Введи сумму'); return; }
      const r = await api('/api/market/loan/take', { method: 'POST', body: JSON.stringify({ amount: cents }) });
      if (!r.ok) { toast(r.error || 'Ошибка'); return; }
      toast('✓ Получено ' + fmtTCompact(r.amount) + ' TRYLLA');
      tg?.HapticFeedback?.notificationOccurred?.('success');
      await refresh();
      paintBank(c);
    });

    // Repay handlers
    c.querySelectorAll('[data-loan-id]').forEach(b => {
      b.addEventListener('click', async () => {
        const loanId = Number(b.dataset.loanId);
        const amount = Number(b.dataset.amount);
        if (!confirm(`Погасить ${fmtTCompact(amount)} TRYLLA?`)) return;
        const r = await api('/api/market/loan/repay', {
          method: 'POST',
          body: JSON.stringify({ loan_id: loanId, amount }),
        });
        if (!r.ok) { toast(r.error || 'Ошибка'); return; }
        toast('✓ Погашено ' + fmtTCompact(r.paid));
        tg?.HapticFeedback?.notificationOccurred?.('success');
        await refresh();
        paintBank(c);
      });
    });
  }

  // ─────── CONVERT ───────
  function paintConvert(c) {
    const s = MS.state; if (!s) return;
    c.innerHTML = `
      <div class="market-convert-card">
        <div class="market-convert-rate">💱 1 TRYLLA = 1 🪙</div>
        <div class="market-convert-tax">⚠ Облагается налогом — учтётся в дневном tax-tick</div>
        <div class="market-convert-bal">У тебя: <b>${fmtTCompact(s.trylla)}</b> TRYLLA</div>
        <input type="number" id="market-conv-input" class="market-trade-input" placeholder="Сколько обменять?" min="1" max="${Math.floor(s.trylla/100)}" />
        <div class="market-trade-quick">
          <button data-amt="1000">1K</button>
          <button data-amt="10000">10K</button>
          <button data-amt="100000">100K</button>
          <button data-amt="all">ВСЁ</button>
        </div>
        <button class="market-trade-go buy" id="market-conv-btn">💱 Конвертировать в коины</button>
      </div>
      <div class="market-convert-card">
        <div class="market-convert-rate">🔒 Приватность портфеля</div>
        <div class="market-convert-bal">Кто видит твой портфель</div>
        <div class="market-trade-quick">
          <button data-priv="public" class="${s.portfolio_privacy === 'public' ? 'active' : ''}">🌐 Все</button>
          <button data-priv="private" class="${s.portfolio_privacy === 'private' ? 'active' : ''}">🔒 По подписке</button>
        </div>
        <div class="market-convert-tax">При приватности подписчики платят 1000 TRYLLA, ты получаешь 80%.</div>
      </div>
    `;
    const input = document.getElementById('market-conv-input');
    c.querySelectorAll('[data-amt]').forEach(b => {
      b.addEventListener('click', () => {
        const v = b.dataset.amt;
        if (v === 'all') input.value = Math.floor(s.trylla / 100);
        else input.value = v;
      });
    });
    document.getElementById('market-conv-btn').addEventListener('click', async () => {
      const cents = Math.max(1, Math.min(s.trylla, Math.floor(Number(input.value) * 100)));
      if (!cents) { toast('Введи сумму'); return; }
      const r = await api('/api/market/convert', { method: 'POST', body: JSON.stringify({ amount_trylla: cents }) });
      if (!r.ok) { toast(r.error || 'Ошибка'); return; }
      toast('✓ Получено ' + fmt(r.credited_coins) + ' 🪙');
      if (typeof r.new_balance === 'number') {
        window.state.me.balance = r.new_balance;
        const balEl = document.getElementById('balance-display');
        if (balEl) balEl.textContent = fmt(r.new_balance);
      }
      await refresh();
    });
    c.querySelectorAll('[data-priv]').forEach(b => {
      b.addEventListener('click', async () => {
        const r = await api('/api/market/privacy', { method: 'POST', body: JSON.stringify({ privacy: b.dataset.priv }) });
        if (r.ok) await refresh();
      });
    });
  }

  // ═════════════════ ACTIVATION ═════════════════
  document.addEventListener('DOMContentLoaded', () => {
    const view = document.querySelector('.view[data-view="market"]');
    if (!view) return;
    const obs = new MutationObserver(() => {
      if (view.classList.contains('active')) boot();
    });
    obs.observe(view, { attributes: true, attributeFilter: ['class'] });
  });
})();
