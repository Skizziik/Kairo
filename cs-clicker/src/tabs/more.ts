import { api, haptic, hapticNotify } from "../api";
import { store } from "../state";
import type { LeaderboardEntry } from "../types";
import { el, fmt } from "../util";
import { toast } from "../ui/toast";
import { openModal } from "../ui/modal";

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
