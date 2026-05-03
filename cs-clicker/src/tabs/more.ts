import { api, haptic, hapticNotify } from "../api";
import { store } from "../state";
import type { LeaderboardEntry, PrestigeNodeDef } from "../types";
import { el, fmt } from "../util";
import { toast } from "../ui/toast";
import { openModal } from "../ui/modal";
import { showBattlepassModal } from "./battlepass_modal";

let root: HTMLElement | null = null;
let lbMetric: "level" | "cash" | "casecoins" | "glory" | "prestige" | "bosses" = "level";
let cachedLb: { metric: string; rows: LeaderboardEntry[] } | null = null;

export function mountMoreTab(parent: HTMLElement): HTMLElement {
  root = el("div", { className: "tab-page", dataset: { tab: "more" } });
  parent.appendChild(root);
  store.subscribe(render);
  render();
  return root;
}

function render() {
  if (!root || !store.state) return;
  root.innerHTML = "";
  const content = el("div", { className: "tab-page-content" });

  content.appendChild(el("div", { className: "tab-title", textContent: "⚡ ЕЩЁ" }));

  // Prestige
  const u = store.state.user;
  const canPrestige = u.max_level >= 20;
  const projectedGlory = canPrestige ? Math.max(1, Math.floor(Math.pow(u.max_level / 20, 1.5))) : 0;
  const prestigeCard = el("div", { className: "prestige-card" });
  prestigeCard.appendChild(el("div", { className: "title", textContent: `★ ПРЕСТИЖ #${u.prestige_count + 1}` }));
  prestigeCard.appendChild(el("div", {
    className: "body",
    innerHTML: canPrestige
      ? `Сбросишь уровень и апгрейды → получишь <b>${projectedGlory}★</b> Славы.<br/>Артефакты, casecoins, глория и сундуки <b>сохранятся</b>.<br/>+1 слот артефакта (сейчас: ${u.artifact_slots}/6).`
      : `Доступно с уровня 20. Сейчас твой максимум: <b>${u.max_level}</b>.`,
  }));
  const pBtn = el("button", { textContent: canPrestige ? `СДЕЛАТЬ ПРЕСТИЖ +${projectedGlory}★` : "ЗАБЛОКИРОВАНО" });
  if (!canPrestige) pBtn.disabled = true;
  pBtn.onclick = () => doPrestige(projectedGlory);
  prestigeCard.appendChild(pBtn);
  content.appendChild(prestigeCard);

  // Battle Pass card
  const bpCard = el("div", { className: "prestige-card", style: { borderColor: "#3FA9F5", background: "linear-gradient(135deg, rgba(63,169,245,0.15), rgba(63,169,245,0.05))" } });
  bpCard.appendChild(el("div", { className: "title", textContent: "🎟️ BATTLE PASS", style: { color: "#3FA9F5" } }));
  bpCard.appendChild(el("div", {
    className: "body",
    innerHTML: `Недельный пасс на 50 уровней. Free + Premium треки. XP начисляется за весь нанесённый урон (1k = 1 XP).<br/>Premium открывается за <b>50 ⌬</b>, эксклюзив на 50-м уровне.`,
  }));
  const bpBtn = el("button", { textContent: "ОТКРЫТЬ BATTLE PASS", style: { background: "linear-gradient(180deg, #3FA9F5, #1B6FB5)", color: "#fff", borderColor: "#154E80" } });
  bpBtn.onclick = () => { haptic("light"); showBattlepassModal(); };
  bpCard.appendChild(bpBtn);
  content.appendChild(bpCard);

  // Prestige Tree (always visible so player sees the path forward).
  if (store.config) {
    content.appendChild(el("div", {
      className: "upg-section-title",
      textContent: `ДРЕВО ПРЕСТИЖА (${u.glory}★)`,
      style: { marginTop: "16px" },
    }));
    if (Number(u.glory) === 0 && u.prestige_count === 0) {
      content.appendChild(el("div", {
        textContent: "Сделай первый престиж (с уровня 20+) чтобы получить ★ Славу и купить узлы.",
        style: { fontSize: "12px", color: "#94A3B8", marginBottom: "10px", padding: "0 4px", lineHeight: "1.4" },
      }));
    }
    const ptList = el("div", { className: "pt-grid" });
    const owned = store.state.prestige_nodes || {};
    const sorted = [...store.config.prestige_tree].sort((a, b) => a.tier - b.tier);
    for (const node of sorted) {
      ptList.appendChild(renderPrestigeNode(node, owned[node.id] || 0, Number(u.glory)));
    }
    content.appendChild(ptList);
  }

  // Stats
  content.appendChild(el("div", { className: "upg-section-title", textContent: "СТАТИСТИКА", style: { marginTop: "16px" } }));
  const stats = el("div", { className: "lb-list" });
  stats.appendChild(statRow("Максимальный уровень", String(u.max_level)));
  stats.appendChild(statRow("Чекпоинт",              String(u.checkpoint)));
  stats.appendChild(statRow("Боссов убито",          String(u.bosses_killed)));
  stats.appendChild(statRow("Сундуков открыто",      String(u.chests_opened)));
  stats.appendChild(statRow("Урон всего",            fmt(u.total_damage)));
  stats.appendChild(statRow("Click Damage",          fmt(u.click_damage)));
  stats.appendChild(statRow("Auto-DPS",              fmt(u.auto_dps) + "/сек"));
  stats.appendChild(statRow("Crit Chance",           `${Number(u.crit_chance).toFixed(1)}%`));
  stats.appendChild(statRow("Crit ×",                `${Number(u.crit_multiplier).toFixed(2)}`));
  stats.appendChild(statRow("Удача",                 `${Number(u.luck).toFixed(0)}%`));
  content.appendChild(stats);

  // Leaderboard
  content.appendChild(el("div", { className: "upg-section-title", textContent: "ЛИДЕРБОРД", style: { marginTop: "20px" } }));
  const tabs = el("div", { className: "lb-tabs" });
  for (const m of [
    { id: "level",     label: "🏆 Уровень" },
    { id: "cash",      label: "💰 Бабосы" },
    { id: "casecoins", label: "⌬ Кейс" },
    { id: "glory",     label: "★ Слава" },
    { id: "bosses",    label: "💀 Боссы" },
  ] as const) {
    const t = el("div", { className: `lb-tab ${m.id === lbMetric ? "active" : ""}`, dataset: { metric: m.id }, textContent: m.label });
    t.onclick = () => {
      lbMetric = m.id;
      render();
      void loadLeaderboard();
    };
    tabs.appendChild(t);
  }
  content.appendChild(tabs);

  const lbList = el("div", { className: "lb-list" });
  if (cachedLb && cachedLb.metric === lbMetric) {
    cachedLb.rows.slice(0, 50).forEach((r, idx) => lbList.appendChild(renderLbRow(r, idx + 1)));
  } else {
    lbList.appendChild(el("div", { textContent: "Загружаем…", style: { textAlign: "center", color: "#64748B", padding: "16px" } }));
  }
  content.appendChild(lbList);

  root.appendChild(content);

  if (!cachedLb || cachedLb.metric !== lbMetric) {
    void loadLeaderboard();
  }
}

function renderPrestigeNode(node: PrestigeNodeDef, owned: number, glory: number): HTMLElement {
  const card = el("div", { className: `pt-node tier-${node.tier}` });
  const maxed = owned >= node.max_level;
  const costIdx = Math.min(owned, node.cost_per_level.length - 1);
  const cost = node.cost_per_level[costIdx];
  const can = !maxed && glory >= cost;

  const head = el("div", { className: "pt-head" });
  head.appendChild(el("span", { className: "pt-name", textContent: node.name }));
  head.appendChild(el("span", { className: "pt-lvl", textContent: `${owned}/${node.max_level}` }));
  card.appendChild(head);

  card.appendChild(el("div", { className: "pt-desc", textContent: node.desc }));

  const btn = el("button", { className: "pt-buy" });
  if (maxed) {
    btn.textContent = "MAX";
    btn.disabled = true;
  } else {
    btn.appendChild(document.createTextNode(`Купить за ${cost}★`));
    if (!can) btn.disabled = true;
  }
  btn.onclick = async () => {
    if (!can || maxed) {
      hapticNotify("error");
      toast("Не хватает ★", "error");
      return;
    }
    haptic("medium");
    btn.disabled = true;
    const r = await api.prestigeBuyNode(node.id);
    if (r.ok && r.data) {
      hapticNotify("success");
      toast(`${node.name} → ${r.data.new_level}`, "success");
      if (r.data.state) store.setState(r.data.state);
    } else {
      hapticNotify("error");
      toast(translatePrestigeError(r.error), "error");
    }
  };
  card.appendChild(btn);

  return card;
}

function translatePrestigeError(err?: string): string {
  switch (err) {
    case "not_enough_glory": return "Мало ★ Славы";
    case "max_level": return "Максимум";
    default: return err || "Ошибка";
  }
}

function statRow(label: string, value: string): HTMLElement {
  const row = el("div", { className: "lb-row" });
  row.appendChild(el("div", { className: "lb-name", textContent: label }));
  row.appendChild(el("div", { className: "lb-score", textContent: value }));
  return row;
}

function renderLbRow(entry: LeaderboardEntry, rank: number): HTMLElement {
  const row = el("div", { className: "lb-row" });
  let rankClass = "";
  if (rank === 1) rankClass = "gold";
  else if (rank === 2) rankClass = "silver";
  else if (rank === 3) rankClass = "bronze";
  row.appendChild(el("div", { className: `lb-rank ${rankClass}`, textContent: `#${rank}` }));
  const info = el("div", { style: { flex: "1", minWidth: "0" } });
  info.appendChild(el("div", { className: "lb-name", textContent: entry.first_name || entry.username || "Игрок" }));
  info.appendChild(el("div", { className: "lb-meta", textContent: `ур. ${entry.max_level} · ${entry.prestige_count}★ престижей` }));
  row.appendChild(info);
  row.appendChild(el("div", { className: "lb-score", textContent: fmt(entry.score) }));
  return row;
}

async function loadLeaderboard() {
  try {
    const r = await api.leaderboard(lbMetric, 50);
    if (r.ok && r.data) {
      cachedLb = { metric: lbMetric, rows: r.data };
      render();
    }
  } catch (e) { console.error(e); }
}

async function doPrestige(projected: number) {
  const body = el("div");
  body.appendChild(el("div", {
    innerHTML: `Получишь <b>${projected}★</b> Славы. Уровень/апгрейды сбросятся.<br/>Артефакты и casecoins останутся.<br/><br/>Уверен?`,
    style: { textAlign: "center" },
  }));
  const handle = openModal({
    title: "ПРЕСТИЖ",
    body,
    actions: [
      { label: "Отмена", onClick: () => handle.close() },
      { label: "ПРЕСТИЖ", className: "danger", onClick: async () => {
        haptic("heavy");
        const r = await api.prestige();
        if (r.ok && r.data) {
          hapticNotify("success");
          toast(`+${r.data.glory_gained}★ Славы`, "success", 3000);
          store.setState(r.data.state);
          handle.close();
        } else {
          hapticNotify("error");
          toast(r.error || "Ошибка", "error");
        }
      } },
    ],
  });
}
