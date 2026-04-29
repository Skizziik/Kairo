/* Tax Authority (Налоговая) — full UI for the new section.
   Connects to global `state` (window.state), `api`, `fmt`, `escape`, `toast`. */
(() => {
  const TS = {
    state: null,
    cfg: null,
    raid: null,           // active raid (or null)
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
      const [cfg, state, raid] = await Promise.all([
        api('/api/tax/config'),
        api('/api/tax/state'),
        api('/api/tax/raid'),
      ]);
      TS.cfg = cfg;
      TS.state = state;
      TS.raid = raid && raid.active ? raid : null;
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
      const [state, raid] = await Promise.all([
        api('/api/tax/state'),
        api('/api/tax/raid'),
      ]);
      TS.state = state;
      TS.raid = raid && raid.active ? raid : null;
      paint();
    } catch (e) {}
  }

  function paint() {
    const r = root();
    if (!r || !TS.state || !TS.cfg) return;
    const s = TS.state;

    // In the daily-tick world, "owed" is computed on the fly from accrued
    // income × effective rate. The estimate that will be charged at 00:00 UTC.
    const owedTotal = s.next_tick_tax_estimate || 0;

    // ── 1. ENTITY HEADER ────────────────────────────────────
    // Avatar: prefer the player's Telegram photo. Fall back to the entity icon
    // emoji (e.g. 👤 / 💼 / 🌴) if no photo. Keeps the card personal — every
    // player sees their own face above their tax form.
    const photoUrl = window.state?.me?.photo_url || '';
    const avatarInner = photoUrl
      ? `<img src="${escape(photoUrl)}" alt="" />`
      : escape(s.entity_icon || '👤');

    const entityHtml = `
      <div class="tax-entity-card">
        <div class="tax-entity-icon ${photoUrl ? 'has-photo' : ''}">${avatarInner}</div>
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

    const paradiseBanner = s.paradise_active ? `
      <div class="tax-paradise-banner">
        🏖 <b>Налоговый рай активен!</b> 0% налогов до ${escape(formatDate(s.paradise_until))}
      </div>
    ` : '';

    // Newbie exemption banner — shown until lifetime total_earned >= 1B
    const newbieBanner = s.is_newbie ? (() => {
      const earned = Number(s.total_earned || 0);
      const threshold = Number(s.newbie_threshold || 1_000_000_000);
      const left = Math.max(0, threshold - earned);
      const pct = Math.min(100, Math.floor((earned / threshold) * 100));
      return `
        <div class="tax-newbie-banner">
          <div class="tax-newbie-head">
            <span>🆓 <b>Налоговые каникулы</b></span>
            <span class="tax-newbie-pct">${pct}%</span>
          </div>
          <div class="tax-newbie-text">
            Налогов нет, пока твой общий заработок меньше ${fmtCompact(threshold)} 🪙.
          </div>
          <div class="tax-newbie-progress">
            <div class="tax-newbie-bar"><div class="tax-newbie-bar-fill" style="width:${pct}%"></div></div>
            <div class="tax-newbie-numbers">${fmtCompact(earned)} / ${fmtCompact(threshold)}</div>
          </div>
          <div class="tax-newbie-text small">Осталось до начала налогов: <b>${fmtCompact(left)} 🪙</b></div>
        </div>
      `;
    })() : '';

    // Next-tick estimate — what will be added to pending_tax_due in ≤1h
    const nextTickEstimate = Number(s.next_tick_tax_estimate || 0);
    const nextTickBlock = (s.is_newbie || nextTickEstimate <= 0)
      ? (s.pending_taxable_income > 0
          ? `<div class="tax-due-pending">Накоплено дохода: ${fmt(s.pending_taxable_income)} 🪙${s.is_newbie ? ' (налог 0% — каникулы)' : ''}</div>`
          : '')
      : `<div class="tax-next-tick">
          <div class="tax-next-tick-row">
            <span>Доход за день</span>
            <b>${fmt(s.pending_taxable_income)} 🪙</b>
          </div>
          <div class="tax-next-tick-row tick-tax">
            <span>Налог в полночь UTC (${(s.effective_rate * 100).toFixed(1)}%)</span>
            <b>+${fmt(nextTickEstimate)} 🪙</b>
          </div>
        </div>`;

    const dueHtml = owedTotal > 0 ? `
      <div class="tax-due-card">
        <div class="tax-due-row total">
          <span>К списанию в полночь UTC</span>
          <b>${fmt(owedTotal)} 🪙</b>
        </div>
        <button class="tax-pay-btn" id="tax-pay-btn">💳 Заплатить сейчас ${fmt(owedTotal)} 🪙</button>
        <div class="tax-due-note">Спишется автоматом раз в сутки в 00:00 UTC. Если на балансе не хватит — баланс уйдёт в минус.</div>
        ${nextTickBlock}
      </div>
    ` : `
      <div class="tax-due-card clean">
        <div class="tax-due-clean">✅ Налогов к списанию нет.</div>
        ${nextTickBlock}
      </div>
    `;

    // ── 2.5 RAID ────────────────────────────────────────────
    const raidHtml = buildRaidHtml();

    // ── 3. ACTIONS ───────────────────────────────────────────
    const declareBtn = s.declared_today
      ? '<button class="tax-action declared" disabled>📋 Декларация подана сегодня (−1%)</button>'
      : '<button class="tax-action" id="tax-declare-btn">📋 Подать декларацию (−1% к ставке)</button>';

    // Amnesty removed — no debt system anymore.
    const amnestyBtn = '';

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
      ${newbieBanner}
      ${paradiseBanner}
      ${raidHtml}
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

    const raidStart = r.querySelector('#tax-raid-start');
    if (raidStart) raidStart.addEventListener('click', startRaid);
    r.querySelectorAll('[data-raid-donate]').forEach(b => {
      b.addEventListener('click', () => donateRaid(Number(b.dataset.raidDonate), Number(b.dataset.amount)));
    });
  }

  // ─── RAID UI ─────────────────────────────────────────────
  function buildRaidHtml() {
    const raid = TS.raid;
    if (!raid) {
      // No active raid — show "start" CTA
      return `
        <div class="tax-raid-card start">
          <div class="tax-raid-head">
            <div class="tax-raid-title">🎯 Рейд на налоговую</div>
            <div class="tax-raid-cooldown">1 раз в 48ч</div>
          </div>
          <div class="tax-raid-text">
            Собери рейд — все игроки могут пожертвовать <b>500 скинов</b> за 10 минут подготовки.
            Если успеете — налоговая <b>не работает 25 часов</b> (один полный день без налогов для всех).
          </div>
          <button class="tax-raid-btn" id="tax-raid-start">⚔ Собрать рейд</button>
        </div>
      `;
    }

    const status = raid.status;

    if (status === 'preparing') {
      const deadline = new Date(raid.deadline).getTime();
      const remainSec = Math.max(0, Math.floor((deadline - Date.now()) / 1000));
      const mm = Math.floor(remainSec / 60);
      const ss = remainSec % 60;
      const remainText = `${mm}:${String(ss).padStart(2, '0')}`;
      const pct = Math.min(100, Math.round((raid.skins_donated / raid.skins_required) * 100));
      const donations = raid.donations || [];
      const donorList = donations.slice(0, 8).map(d => {
        const name = d.username ? '@' + d.username : (d.first_name || `tg${d.user_id}`);
        return `<div class="tax-raid-donor"><span>${escape(name)}</span><b>${d.skins} скинов</b></div>`;
      }).join('');

      return `
        <div class="tax-raid-card preparing">
          <div class="tax-raid-head">
            <div class="tax-raid-title">⚔ ИДЁТ СБОР НА РЕЙД!</div>
            <div class="tax-raid-timer" data-raid-deadline="${escape(raid.deadline)}">${remainText}</div>
          </div>
          <div class="tax-raid-progress-block">
            <div class="tax-raid-numbers">
              <b>${raid.skins_donated}</b> / ${raid.skins_required} скинов
              <span class="tax-raid-pct">${pct}%</span>
            </div>
            <div class="tax-raid-bar"><div class="tax-raid-bar-fill" style="width:${pct}%"></div></div>
          </div>
          <div class="tax-raid-donate-row">
            <button class="tax-raid-mini" data-raid-donate="${raid.id}" data-amount="10">+10 скинов</button>
            <button class="tax-raid-mini" data-raid-donate="${raid.id}" data-amount="50">+50</button>
            <button class="tax-raid-mini" data-raid-donate="${raid.id}" data-amount="100">+100</button>
          </div>
          ${donorList ? `<div class="tax-raid-donors">${donorList}</div>` : '<div class="tax-raid-text" style="text-align:center">Нет пожертвований. Будь первым!</div>'}
        </div>
      `;
    }

    if (status === 'success' && raid.raid_until) {
      const until = new Date(raid.raid_until).getTime();
      const remainSec = Math.max(0, Math.floor((until - Date.now()) / 1000));
      const hh = Math.floor(remainSec / 3600);
      const mm = Math.floor((remainSec % 3600) / 60);
      const ss = remainSec % 60;
      const remainText = `${hh}:${String(mm).padStart(2,'0')}:${String(ss).padStart(2,'0')}`;
      return `
        <div class="tax-raid-card success">
          <div class="tax-raid-success-icon">🔥</div>
          <div class="tax-raid-success-title">НАЛОГОВАЯ РАЗБИТА!</div>
          <div class="tax-raid-success-sub">Доход не облагается налогом ещё <b data-raid-until="${escape(raid.raid_until)}">${remainText}</b></div>
        </div>
      `;
    }
    return '';
  }

  async function startRaid() {
    if (!confirm('Собрать рейд на налоговую? У тебя будет 10 минут чтобы все вместе нанесли 500 скинов.')) return;
    try {
      const r = await api('/api/tax/raid/start', { method: 'POST', body: JSON.stringify({}) });
      if (!r.ok) { toast(r.error || 'Ошибка'); return; }
      toast('⚔ Рейд начат! Зови всех донатить скины');
      tg?.HapticFeedback?.notificationOccurred?.('success');
      await refresh();
    } catch (e) { toast('Ошибка: ' + e.message); }
  }

  async function donateRaid(raidId, count) {
    if (!confirm(`Пожертвовать ${count} скинов в рейд? Они будут потрачены безвозвратно.`)) return;
    try {
      const r = await api('/api/tax/raid/donate', {
        method: 'POST',
        body: JSON.stringify({ raid_id: raidId, count }),
      });
      if (!r.ok) { toast(r.error || 'Ошибка'); return; }
      toast(`💥 +${r.donated} скинов в атаку! Всего: ${r.raid_total}/${r.required}`);
      tg?.HapticFeedback?.impactOccurred?.('medium');
      await refresh();
    } catch (e) { toast('Ошибка: ' + e.message); }
  }

  // Tick the raid timer every second when visible (without re-fetching)
  setInterval(() => {
    if (!TS.raid) return;
    const isActive = document.querySelector('.view[data-view="tax"].active');
    if (!isActive) return;
    const r = root();
    if (!r) return;
    const timer = r.querySelector('[data-raid-deadline]');
    if (timer) {
      const deadline = new Date(timer.dataset.raidDeadline).getTime();
      const remainSec = Math.max(0, Math.floor((deadline - Date.now()) / 1000));
      const mm = Math.floor(remainSec / 60);
      const ss = remainSec % 60;
      timer.textContent = `${mm}:${String(ss).padStart(2,'0')}`;
      if (remainSec <= 0) refresh();    // resolve to result on timeout
    }
    const until = r.querySelector('[data-raid-until]');
    if (until) {
      const t = new Date(until.dataset.raidUntil).getTime();
      const remainSec = Math.max(0, Math.floor((t - Date.now()) / 1000));
      if (remainSec <= 0) { refresh(); return; }
      const hh = Math.floor(remainSec / 3600);
      const mm = Math.floor((remainSec % 3600) / 60);
      const ss = remainSec % 60;
      until.textContent = `${hh}:${String(mm).padStart(2,'0')}:${String(ss).padStart(2,'0')}`;
    }
  }, 1000);

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
