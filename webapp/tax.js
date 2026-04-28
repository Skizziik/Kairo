/* Tax Authority (Налоговая) — full UI for the new section.
   Connects to global `state` (window.state), `api`, `fmt`, `escape`, `toast`. */
(() => {
  const TS = {
    state: null,
    cfg: null,
    inited: false,
    pollTimer: null,
  };

  const root = () => document.getElementById('tax-app');

  // Boot — when tax view becomes active for the first time
  async function boot() {
    if (TS.inited) {
      // refresh on subsequent activations
      await refresh();
      return;
    }
    TS.inited = true;
    try {
      const [cfg, state] = await Promise.all([
        api('/api/tax/config'),
        api('/api/tax/state'),
      ]);
      TS.cfg = cfg;
      TS.state = state;
    } catch (e) {
      const r = root(); if (r) r.innerHTML = '<div class="loader">Ошибка: ' + escape(e.message) + '</div>';
      return;
    }
    paint();
    startPoll();
  }

  function startPoll() {
    if (TS.pollTimer) clearInterval(TS.pollTimer);
    TS.pollTimer = setInterval(async () => {
      const isActive = document.querySelector('.view[data-view="tax"].active');
      if (!isActive) return;
      try {
        TS.state = await api('/api/tax/state');
        paint();
      } catch (e) {}
    }, 15_000);
  }

  async function refresh() {
    try {
      TS.state = await api('/api/tax/state');
      paint();
    } catch (e) {}
  }

  function paint() {
    const r = root();
    if (!r || !TS.state || !TS.cfg) return;
    const s = TS.state;

    const owedTotal = (s.pending_tax_due || 0) + (s.tax_debt || 0);
    const debt = s.tax_debt || 0;

    // ── 1. ENTITY HEADER ────────────────────────────────────
    const entityHtml = `
      <div class="tax-entity-card ${debt > 0 ? 'debt' : ''}">
        <div class="tax-entity-icon">${escape(s.entity_icon || '👤')}</div>
        <div class="tax-entity-info">
          <div class="tax-entity-label">Текущая форма</div>
          <div class="tax-entity-name">${escape(s.entity_name || 'Физ. лицо')}</div>
          <div class="tax-entity-rate">
            <span class="tax-rate-num">${(s.effective_rate * 100).toFixed(1)}%</span>
            <span class="tax-rate-suffix">эффективная ставка</span>
          </div>
        </div>
        <div class="tax-entity-streak" title="Непрерывных дней без долгов">
          <div class="tax-streak-num">${s.streak_punctual_days || 0}</div>
          <div class="tax-streak-label">дней<br>без долгов</div>
          ${s.honest_citizen ? '<div class="tax-streak-badge">🏆 Honest Citizen</div>' : ''}
        </div>
      </div>
    `;

    // Rate breakdown
    const breakdownHtml = (s.rate_breakdown || []).map(it => `
      <div class="tax-rate-row">
        <span class="tax-rate-label">${escape(it.label)}</span>
        <span class="tax-rate-value" style="color:${it.color || ''}">${escape(it.value)}</span>
      </div>
    `).join('');

    // ── 2. PAY-DUE BANNER ────────────────────────────────────
    const debtBanner = debt > 0 ? `
      <div class="tax-debt-banner">
        <div class="tax-debt-icon">⚠</div>
        <div>
          <div class="tax-debt-title">У вас долг по налогам</div>
          <div class="tax-debt-amount">${fmt(debt)} 🪙</div>
          <div class="tax-debt-note">Растёт +${(s.debt_penalty_per_day * 100).toFixed(0)}%/день. Блокирует покупки.</div>
        </div>
      </div>
    ` : '';

    const paradiseBanner = s.paradise_active ? `
      <div class="tax-paradise-banner">
        🏖 <b>Налоговый рай активен!</b> 0% налогов до ${escape(formatDate(s.paradise_until))}
      </div>
    ` : '';

    const dueHtml = (owedTotal > 0 || debt > 0) ? `
      <div class="tax-due-card">
        <div class="tax-due-row">
          <span>К оплате (накоплено)</span>
          <b>${fmt(s.pending_tax_due || 0)} 🪙</b>
        </div>
        ${debt > 0 ? `<div class="tax-due-row debt">
          <span>Долг (с ×${(1 + s.debt_penalty_per_day).toFixed(2)}/день)</span>
          <b>${fmt(debt)} 🪙</b>
        </div>` : ''}
        <div class="tax-due-row total">
          <span>Итого</span>
          <b>${fmt(owedTotal)} 🪙</b>
        </div>
        <button class="tax-pay-btn" id="tax-pay-btn">💳 Заплатить ${fmt(owedTotal)} 🪙</button>
      </div>
    ` : `
      <div class="tax-due-card clean">
        <div class="tax-due-clean">✅ Все налоги уплачены. Доход за час обнулится при следующем тике.</div>
        ${(s.pending_taxable_income > 0)
          ? `<div class="tax-due-pending">Накоплено дохода для следующего тика: ${fmt(s.pending_taxable_income)} 🪙</div>`
          : ''}
      </div>
    `;

    // ── 3. ACTIONS ───────────────────────────────────────────
    const declareBtn = s.declared_today
      ? '<button class="tax-action declared" disabled>📋 Декларация подана сегодня (−1%)</button>'
      : '<button class="tax-action" id="tax-declare-btn">📋 Подать декларацию (−1% к ставке)</button>';

    const amnestyBtn = (debt > 0 && s.amnesty_available)
      ? `<button class="tax-action amnesty" id="tax-amnesty-btn">💼 Амнистия — погасить за ${fmt(Math.floor(debt / 2))} 🪙</button>`
      : (debt > 0
          ? '<button class="tax-action amnesty" disabled>💼 Амнистия (раз в месяц, на cooldown)</button>'
          : '');

    // ── 4. ENTITY UPGRADES (registration tiers) ──────────────
    const entitiesHtml = TS.cfg.entities.map(e => {
      const owned = s.entity_level >= e.level;
      const isCurrent = s.entity_level === e.level;
      const canBuy = s.entity_level < e.level && (state?.me?.balance || window.state?.me?.balance || 0) >= e.reg_fee;
      return `
        <div class="tax-entity-tier ${isCurrent ? 'current' : ''} ${owned ? 'owned' : ''}">
          <div class="tax-tier-icon">${escape(e.icon)}</div>
          <div class="tax-tier-info">
            <div class="tax-tier-row">
              <span class="tax-tier-name">${escape(e.name)}</span>
              <span class="tax-tier-rate">${(e.rate * 100).toFixed(0)}%</span>
            </div>
            <div class="tax-tier-desc">${escape(e.desc)}</div>
          </div>
          <div class="tax-tier-action">
            ${isCurrent
              ? '<span class="tax-tier-badge">текущая</span>'
              : owned
                ? '<span class="tax-tier-badge owned">пройдено</span>'
                : `<button class="tax-tier-buy" data-entity="${e.level}" ${canBuy ? '' : 'disabled'}>
                    ${e.reg_fee === 0 ? 'Free' : fmt(e.reg_fee) + ' 🪙'}
                  </button>`}
          </div>
        </div>
      `;
    }).join('');

    // ── 5. PERKS ─────────────────────────────────────────────
    const upgradesHtml = TS.cfg.upgrades.map(u => {
      const cur = Number((s.upgrades || {})[u.key] || 0);
      const max = u.max_level;
      const isMax = cur >= max && u.key !== 'tax_paradise';
      // tax_paradise is always re-buyable when not on cooldown
      const isParadise = u.key === 'tax_paradise';
      const tier = isMax ? null : u.tiers[cur];
      const cost = tier ? tier[2] : 0;
      const nextEffect = tier ? tier[1] : (u.tiers[max - 1] && u.tiers[max - 1][1]);
      const balance = window.state?.me?.balance || 0;
      const canAfford = !isMax && balance >= cost;

      const progressBar = (max > 1)
        ? `<div class="tax-perk-progress">
            <span>${cur}/${max}</span>
            <div class="tax-perk-bar"><div class="tax-perk-bar-fill" style="width:${(cur/max)*100}%"></div></div>
          </div>` : '';

      return `
        <div class="tax-perk-card">
          <div class="tax-perk-icon">${escape(u.icon)}</div>
          <div class="tax-perk-info">
            <div class="tax-perk-name">${escape(u.name)}</div>
            <div class="tax-perk-desc">${escape(u.desc)}</div>
            ${progressBar}
            ${tier ? `<div class="tax-perk-next">Следующий: <b>${nextEffect}${escape(u.unit)}</b></div>` : ''}
          </div>
          ${isMax
            ? `<button class="tax-perk-buy maxed" disabled>MAX</button>`
            : isParadise
              ? `<button class="tax-perk-buy paradise" data-perk="${u.key}" ${canAfford ? '' : 'disabled'}>
                  ${fmt(cost)} 🪙<br><small>1×/мес</small>
                </button>`
              : `<button class="tax-perk-buy" data-perk="${u.key}" ${canAfford ? '' : 'disabled'}>
                  ${fmtCompact(cost)} 🪙
                </button>`}
        </div>
      `;
    }).join('');

    // ── 6. STATS FOOTER ──────────────────────────────────────
    const statsHtml = `
      <div class="tax-stats-grid">
        <div class="tax-stat"><div class="tax-stat-num">${fmt(s.total_taxes_paid || 0)}</div><div class="tax-stat-lbl">Уплачено всего</div></div>
        <div class="tax-stat"><div class="tax-stat-num">${s.total_audits || 0}</div><div class="tax-stat-lbl">Проверок</div></div>
        <div class="tax-stat"><div class="tax-stat-num">${s.total_amnesties || 0}</div><div class="tax-stat-lbl">Амнистий</div></div>
        <div class="tax-stat"><div class="tax-stat-num">${s.streak_punctual_days || 0}</div><div class="tax-stat-lbl">Стрик</div></div>
      </div>
    `;

    // ── ASSEMBLE ─────────────────────────────────────────────
    r.innerHTML = `
      ${entityHtml}
      ${paradiseBanner}
      ${debtBanner}
      ${dueHtml}
      <div class="tax-rate-breakdown">
        <div class="tax-section-title">📊 Расчёт ставки</div>
        ${breakdownHtml}
      </div>
      <div class="tax-actions-strip">
        ${declareBtn}
        ${amnestyBtn}
      </div>
      <div class="tax-section-title">🏢 Юридические формы</div>
      <div class="tax-entities-list">${entitiesHtml}</div>
      <div class="tax-section-title">🛠 Прокачка налоговой</div>
      <div class="tax-perks-list">${upgradesHtml}</div>
      ${statsHtml}
    `;

    bindHandlers();

    // Update home-screen badge
    updateHomeBadge();
  }

  function updateHomeBadge() {
    const badge = document.getElementById('tax-btn-badge');
    if (!badge || !TS.state) return;
    const owed = (TS.state.pending_tax_due || 0) + (TS.state.tax_debt || 0);
    if (owed > 0) {
      badge.style.display = 'inline-block';
      badge.textContent = fmtCompact(owed);
      badge.classList.toggle('debt', (TS.state.tax_debt || 0) > 0);
    } else {
      badge.style.display = 'none';
    }
  }

  function bindHandlers() {
    const r = root();
    if (!r) return;

    const payBtn = r.querySelector('#tax-pay-btn');
    if (payBtn) payBtn.addEventListener('click', payAll);

    const declareBtn = r.querySelector('#tax-declare-btn');
    if (declareBtn) declareBtn.addEventListener('click', doDeclare);

    const amnestyBtn = r.querySelector('#tax-amnesty-btn');
    if (amnestyBtn) amnestyBtn.addEventListener('click', doAmnesty);

    r.querySelectorAll('[data-entity]').forEach(b => {
      b.addEventListener('click', () => doRegister(Number(b.dataset.entity)));
    });

    r.querySelectorAll('[data-perk]').forEach(b => {
      b.addEventListener('click', () => doUpgrade(b.dataset.perk));
    });
  }

  async function payAll() {
    try {
      const r = await api('/api/tax/pay', { method: 'POST', body: JSON.stringify({}) });
      if (!r.ok) { toast(r.error || 'Ошибка'); return; }
      toast('💳 Уплачено ' + fmt(r.paid) + ' 🪙');
      if (typeof r.new_balance === 'number') updateBalance(r.new_balance);
      tg?.HapticFeedback?.notificationOccurred?.('success');
      await refresh();
    } catch (e) { toast('Ошибка: ' + e.message); }
  }

  async function doDeclare() {
    try {
      const r = await api('/api/tax/declare', { method: 'POST', body: JSON.stringify({}) });
      if (!r.ok) { toast(r.error || 'Ошибка'); return; }
      toast('📋 Декларация подана. −1% сегодня');
      tg?.HapticFeedback?.impactOccurred?.('light');
      await refresh();
    } catch (e) { toast('Ошибка: ' + e.message); }
  }

  async function doAmnesty() {
    if (!confirm('Амнистия спишет долг за 50% его суммы. Один раз в месяц. Продолжить?')) return;
    try {
      const r = await api('/api/tax/amnesty', { method: 'POST', body: JSON.stringify({}) });
      if (!r.ok) { toast(r.error || 'Ошибка'); return; }
      toast('💼 Долг ' + fmt(r.wiped_debt) + ' 🪙 списан за ' + fmt(r.paid));
      if (typeof r.new_balance === 'number') updateBalance(r.new_balance);
      tg?.HapticFeedback?.notificationOccurred?.('success');
      await refresh();
    } catch (e) { toast('Ошибка: ' + e.message); }
  }

  async function doRegister(level) {
    const ent = (TS.cfg.entities || []).find(e => e.level === level);
    if (!ent) return;
    if (!confirm(`Зарегистрировать «${ent.name}» за ${fmt(ent.reg_fee)} 🪙? Ставка станет ${(ent.rate * 100).toFixed(0)}%.`)) return;
    try {
      const r = await api('/api/tax/register', {
        method: 'POST', body: JSON.stringify({ target_level: level }),
      });
      if (!r.ok) { toast(r.error || 'Ошибка'); return; }
      toast('🏛 ' + r.name + ' зарегистрирован!');
      // Update balance from /api/me to be safe
      try {
        const me = await api('/api/me');
        if (me && typeof me.balance === 'number') updateBalance(me.balance);
      } catch (e) {}
      tg?.HapticFeedback?.notificationOccurred?.('success');
      await refresh();
    } catch (e) { toast('Ошибка: ' + e.message); }
  }

  async function doUpgrade(key) {
    try {
      const r = await api('/api/tax/upgrade', {
        method: 'POST', body: JSON.stringify({ key }),
      });
      if (!r.ok) { toast(r.error || 'Ошибка'); return; }
      if (r.kind === 'paradise_activated') {
        toast('🏖 Налоговый рай! 7 дней нулевого налога');
      } else {
        toast('✓ Прокачано');
      }
      if (typeof r.new_balance === 'number') updateBalance(r.new_balance);
      tg?.HapticFeedback?.impactOccurred?.('light');
      await refresh();
    } catch (e) { toast('Ошибка: ' + e.message); }
  }

  function updateBalance(newBal) {
    if (window.state && window.state.me) window.state.me.balance = newBal;
    const balEl = document.getElementById('balance-display');
    if (balEl) balEl.textContent = fmt(newBal);
  }

  function formatDate(iso) {
    if (!iso) return '';
    try {
      const d = new Date(iso);
      return d.toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
    } catch (e) { return iso; }
  }

  // Wire activation: when nav switches to data-view="tax", boot/refresh
  document.addEventListener('DOMContentLoaded', () => {
    // Home-screen tax button
    const taxBtn = document.getElementById('tax-btn');
    if (taxBtn) {
      taxBtn.addEventListener('click', () => {
        // Switch to tax view (using existing nav helpers — fall back to manual)
        if (typeof switchView === 'function') {
          switchView('tax');
        } else {
          document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
          const v = document.querySelector('.view[data-view="tax"]');
          if (v) v.classList.add('active');
        }
        boot();
      });
    }

    // Also fire if user lands on tax view directly
    const taxView = document.querySelector('.view[data-view="tax"]');
    if (taxView) {
      // Use MutationObserver to detect activation
      const obs = new MutationObserver(() => {
        if (taxView.classList.contains('active')) {
          boot();
        }
      });
      obs.observe(taxView, { attributes: true, attributeFilter: ['class'] });
    }
  });

  // Periodic poll of state for the home-screen badge (regardless of which view active).
  // Lightweight — only the state endpoint, no UI work unless tax view is active.
  setInterval(async () => {
    if (!window.state || !window.state.me) return;
    try {
      const s = await api('/api/tax/state');
      TS.state = s;
      updateHomeBadge();
      const isActive = document.querySelector('.view[data-view="tax"].active');
      if (isActive) paint();
    } catch (e) {}
  }, 30_000);

  // Convenience helper: fmtCompact (some app helpers may expose it; fallback)
  function fmtCompact(n) {
    if (typeof window.fmtCompact === 'function') return window.fmtCompact(n);
    n = Number(n) || 0;
    if (n >= 1e12) return (n / 1e12).toFixed(2) + 'T';
    if (n >= 1e9)  return (n / 1e9).toFixed(2) + 'B';
    if (n >= 1e6)  return (n / 1e6).toFixed(1) + 'M';
    if (n >= 1e3)  return (n / 1e3).toFixed(1) + 'K';
    return String(Math.round(n));
  }
})();
