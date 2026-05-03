import { api, haptic, hapticNotify } from "../api";
import { store } from "../state";
import { ASSET_BASE, el, fmt } from "../util";
import { openModal, type ModalHandle } from "../ui/modal";
import { toast } from "../ui/toast";

let activeModal: ModalHandle | null = null;
let activeSubTab: "targets" | "history" = "targets";
let targetsCache: any[] = [];
let historyCache: { raids: any[]; duels: any[] } = { raids: [], duels: [] };

export async function showPvPModal() {
  if (activeModal) return;
  if (!store.config) return;

  const body = el("div", { className: "pvp-body" });
  body.appendChild(el("div", { textContent: "Загружаем…", style: { textAlign: "center", color: "#94A3B8", padding: "16px" } }));

  activeModal = openModal({
    title: "⚔️ PvP",
    body,
    actions: [{ label: "Закрыть", onClick: () => activeModal?.close() }],
  });
  const obs = new MutationObserver(() => {
    if (!document.body.contains(activeModal!.root)) {
      obs.disconnect();
      activeModal = null;
    }
  });
  obs.observe(document.body, { childList: true });

  await loadActive();
  render(body);
}

async function loadActive() {
  if (activeSubTab === "targets") {
    const r = await api.pvpTargets();
    if (r.ok && r.data) targetsCache = r.data;
  } else {
    const r = await api.pvpHistory();
    if (r.ok && r.data) historyCache = r.data;
  }
}

function render(body: HTMLElement) {
  body.innerHTML = "";

  // Sub-tabs
  const tabs = el("div", { className: "lb-tabs" });
  for (const [id, label] of [["targets","🎯 Цели"], ["history","📜 История"]] as ["targets"|"history", string][]) {
    const t = el("div", {
      className: `lb-tab ${id === activeSubTab ? "active" : ""}`,
      textContent: label,
    });
    t.onclick = async () => {
      activeSubTab = id;
      await loadActive();
      render(body);
    };
    tabs.appendChild(t);
  }
  body.appendChild(tabs);

  if (activeSubTab === "targets") body.appendChild(renderTargets(body));
  else body.appendChild(renderHistory());
}

function renderTargets(bodyRoot: HTMLElement): HTMLElement {
  const wrap = el("div", { className: "pvp-list" });

  if (!store.state) return wrap;
  const myLevel = store.state.user.max_level;

  wrap.appendChild(el("div", {
    className: "pvp-info",
    innerHTML: `Рейд (lvl 30+, $100k) → 10% ресурса жертвы за 24ч.<br/>Дуэль (lvl 15+) → асинхронная по DPS.`,
  }));

  if (targetsCache.length === 0) {
    wrap.appendChild(el("div", { className: "market-empty", textContent: "Других игроков пока нет." }));
    return wrap;
  }

  for (const t of targetsCache) {
    const card = el("div", { className: "pvp-target" });

    const head = el("div", { className: "pvp-target-head" });
    head.appendChild(el("div", { className: "pvp-target-name", textContent: t.first_name || t.username || `player#${t.tg_id}` }));
    head.appendChild(el("div", { className: "pvp-target-meta", textContent: `lvl ${t.max_level} · ${t.prestige_count}★` }));
    card.appendChild(head);

    const stats = el("div", { className: "pvp-target-stats" });
    stats.appendChild(el("span", { textContent: `⚔️ ${fmt(t.click_damage)}` }));
    stats.appendChild(el("span", { textContent: `⏱ ${fmt(t.auto_dps)}/с` }));
    stats.appendChild(el("span", { textContent: `🎯 ${Number(t.crit_chance).toFixed(0)}%` }));
    card.appendChild(stats);

    const actions = el("div", { className: "pvp-target-actions" });
    const raidBtn = el("button", { className: "pvp-raid-btn", textContent: "🪖 РЕЙД" });
    raidBtn.disabled = myLevel < 30;
    raidBtn.onclick = () => openRaidPicker(t, bodyRoot);
    actions.appendChild(raidBtn);

    const duelBtn = el("button", { className: "pvp-duel-btn", textContent: "⚔️ ДУЭЛЬ" });
    duelBtn.disabled = myLevel < 15;
    duelBtn.onclick = () => openDuelPicker(t, bodyRoot);
    actions.appendChild(duelBtn);

    card.appendChild(actions);
    wrap.appendChild(card);
  }
  return wrap;
}

function openRaidPicker(target: any, bodyRoot: HTMLElement) {
  if (!store.config) return;
  const body = el("div");
  body.appendChild(el("div", {
    textContent: `Цель: ${target.first_name || "Player"} (lvl ${target.max_level})`,
    style: { fontWeight: "700", marginBottom: "8px", textAlign: "center" },
  }));
  body.appendChild(el("div", {
    textContent: "Выбери бизнес для рейда:",
    style: { color: "#94A3B8", fontSize: "12px", marginBottom: "8px", textAlign: "center" },
  }));

  const grid = el("div", { className: "raid-biz-grid" });
  for (const b of store.config.businesses) {
    if (target.max_level < b.unlock_level) continue;
    const card = el("div", { className: "raid-biz-card" });
    card.appendChild(el("img", { src: `${ASSET_BASE}/${b.icon}`, alt: b.name }));
    card.appendChild(el("div", { textContent: b.name, style: { fontWeight: "700", fontSize: "12px", marginTop: "4px" } }));
    card.onclick = async () => {
      modal.close();
      haptic("heavy");
      const r = await api.pvpRaid(target.tg_id, b.id);
      if (r.ok && r.data) {
        const d = r.data;
        if (d.success) {
          hapticNotify("success");
          toast(`✅ Успех! Украдено ${fmt(d.amount_stolen)} ${d.resource_type}`, "success", 4000);
        } else {
          hapticNotify("error");
          toast(`❌ Провал. Защита удержала. -$${fmt(d.cost_paid)}`, "error", 4000);
        }
        // Refresh state
        const st = await api.state();
        if (st.ok && st.data) {
          const data: any = st.data;
          store.setState(data.state ?? data);
        }
        await loadActive();
        render(bodyRoot);
      } else {
        hapticNotify("error");
        toast(translatePvPError(r.error, r), "error");
      }
    };
    grid.appendChild(card);
  }
  body.appendChild(grid);

  const modal = openModal({
    title: "🪖 РЕЙД ($100k)",
    body,
    actions: [{ label: "Отмена", onClick: () => modal.close() }],
  });
}

function openDuelPicker(target: any, bodyRoot: HTMLElement) {
  if (!store.config) return;
  let stakeKind: "cash" | "casecoins" | "resource" = "cash";
  let stakeId: string | null = null;
  let stakeAmount = 1000;

  const body = el("div");
  body.appendChild(el("div", {
    textContent: `Дуэль: ${target.first_name || "Player"}`,
    style: { fontWeight: "700", marginBottom: "8px", textAlign: "center" },
  }));

  const myStats = store.state!.user;
  body.appendChild(el("div", {
    innerHTML: `<b>Твой DPS</b>: ${fmt(myStats.click_damage)} тап + ${fmt(myStats.auto_dps)}/сек<br/><b>Соперник</b>: ${fmt(target.click_damage)} тап + ${fmt(target.auto_dps)}/сек`,
    style: { fontSize: "11px", textAlign: "center", marginBottom: "12px", color: "#94A3B8" },
  }));

  // Stake kind toggle
  const kindRow = el("div", { className: "asset-kind-row" });
  for (const k of ["cash", "casecoins"]) {
    const t = el("div", {
      className: `asset-kind-tab ${stakeKind === k ? "active" : ""}`,
      textContent: k === "cash" ? "$" : "⌬",
    });
    t.onclick = () => {
      stakeKind = k as any;
      stakeId = null;
      kindRow.querySelectorAll(".asset-kind-tab").forEach((b) => b.classList.remove("active"));
      t.classList.add("active");
    };
    kindRow.appendChild(t);
  }
  body.appendChild(kindRow);

  // Amount
  const amountRow = el("div", { className: "asset-amount-row", style: { marginTop: "8px" } });
  const input = el("input", { type: "number", value: String(stakeAmount), min: "1" }) as HTMLInputElement;
  input.oninput = () => { stakeAmount = Math.max(1, Math.floor(Number(input.value) || 1)); };
  amountRow.appendChild(el("label", { textContent: "Ставка:" }));
  amountRow.appendChild(input);
  body.appendChild(amountRow);

  body.appendChild(el("div", {
    textContent: "Соперник тоже отдаёт ставку. Победитель забирает 1.8× (10% сжигается).",
    style: { fontSize: "11px", color: "#94A3B8", marginTop: "8px", textAlign: "center" },
  }));

  const modal = openModal({
    title: "⚔️ ДУЭЛЬ",
    body,
    actions: [
      { label: "Отмена", onClick: () => modal.close() },
      { label: "В бой", className: "primary", onClick: async () => {
        haptic("heavy");
        const r = await api.pvpDuel(target.tg_id, stakeKind, stakeId, stakeAmount);
        if (r.ok && r.data) {
          const won = r.data.winner_tg_id === store.state!.user.tg_id;
          hapticNotify(won ? "success" : "error");
          toast(won ? `🏆 ПОБЕДА! +${fmt(r.data.payout)}` : `💀 Поражение. -${fmt(r.data.stake)}`, won ? "success" : "error", 4000);
          modal.close();
          const st = await api.state();
          if (st.ok && st.data) {
            const data: any = st.data;
            store.setState(data.state ?? data);
          }
          await loadActive();
          render(bodyRoot);
        } else {
          hapticNotify("error");
          toast(translatePvPError(r.error, r), "error");
        }
      } },
    ],
  });
}

function renderHistory(): HTMLElement {
  const wrap = el("div", { className: "pvp-list" });

  if (historyCache.raids.length === 0 && historyCache.duels.length === 0) {
    wrap.appendChild(el("div", { className: "market-empty", textContent: "История пока пуста." }));
    return wrap;
  }

  if (historyCache.raids.length > 0) {
    wrap.appendChild(el("div", { className: "upg-section-title", textContent: "🪖 РЕЙДЫ" }));
    for (const r of historyCache.raids) {
      const card = el("div", { className: `pvp-history-card ${r.success ? "win" : "lose"}` });
      const dirText = r.self_was_raider ? `→ ${r.victim_name || "Player"}` : `← рейд от противника`;
      card.appendChild(el("div", { className: "pvp-h-head", textContent: dirText }));
      const result = r.success
        ? `Успех: +${fmt(r.amount_stolen)} ${r.resource_type || ""}`
        : `Провал: $${fmt(r.cost_paid)} потеряно`;
      card.appendChild(el("div", { className: "pvp-h-result", textContent: result }));
      wrap.appendChild(card);
    }
  }

  if (historyCache.duels.length > 0) {
    wrap.appendChild(el("div", { className: "upg-section-title", textContent: "⚔️ ДУЭЛИ", style: { marginTop: "12px" } }));
    for (const d of historyCache.duels) {
      const card = el("div", { className: `pvp-history-card ${d.self_won ? "win" : "lose"}` });
      const opponent = d.challenger_tg_id === store.state!.user.tg_id ? d.challenged_name : d.challenger_name;
      card.appendChild(el("div", { className: "pvp-h-head", textContent: `vs ${opponent || "Player"}` }));
      card.appendChild(el("div", {
        className: "pvp-h-result",
        textContent: `${d.self_won ? "🏆 Победа" : "💀 Поражение"} (ставка ${fmt(d.stake_amount)} ${d.stake_kind})`,
      }));
      wrap.appendChild(card);
    }
  }

  return wrap;
}

function translatePvPError(err?: string, full?: any): string {
  switch (err) {
    case "level_locked": return `Нужен уровень ${full?.needed || "?"}`;
    case "not_enough_cash": return `Не хватает $ (нужно ${full?.needed || "?"})`;
    case "not_enough_casecoins": return "Не хватает ⌬";
    case "not_enough_resource": return "Не хватает ресурса";
    case "cooldown":
      const sec = full?.seconds_remaining || 0;
      const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60);
      return `Кулдаун: ${h}ч ${m}м`;
    case "self_raid":
    case "self_duel": return "Себя нельзя";
    case "victim_business_locked": return "У жертвы нет такого бизнеса";
    case "opponent_not_enough_cash":
    case "opponent_not_enough_casecoins":
    case "opponent_not_enough_resource": return "У соперника нет ставки";
    default: return err || "Ошибка";
  }
}
