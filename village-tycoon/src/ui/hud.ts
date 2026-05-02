import Phaser from "phaser";
import { api, haptic, hapticNotify } from "../api";
import type { BuildingSnap, ConfigSnap, StateSnap } from "../types";

const ASSET = "/assets";

function $(id: string): HTMLElement {
  const el = document.getElementById(id);
  if (!el) throw new Error(`#${id} missing`);
  return el;
}

function formatNum(s: string | number): string {
  let n: number;
  if (typeof s === "string") {
    n = Number(s);
    if (Number.isNaN(n)) return s;
  } else {
    n = s;
  }
  n = Math.floor(n);
  if (n >= 1_000_000_000) return (n / 1_000_000_000).toFixed(1).replace(/\.0$/, "") + "B";
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1).replace(/\.0$/, "") + "M";
  if (n >= 10_000) return (n / 1_000).toFixed(1).replace(/\.0$/, "") + "k";
  return n.toLocaleString("ru-RU");
}

// ---------- Top resource bar -------------------------------------------

let topBarEl: HTMLElement | null = null;

export function renderTopBar(state: StateSnap, config: ConfigSnap) {
  const root = $("hud-root");
  if (!topBarEl) {
    topBarEl = document.createElement("div");
    topBarEl.className = "hud-top hud-layer";
    root.appendChild(topBarEl);
  }
  topBarEl.innerHTML = "";
  const visible = ["wood", "stone", "food", "water", "gold"];
  for (const r of state.resources) {
    if (!visible.includes(r.type)) continue;
    const def = config.resources[r.type];
    if (!def) continue;
    const chip = document.createElement("div");
    chip.className = "res-chip";
    if (Number(r.amount) >= Number(r.cap)) chip.classList.add("capped");
    chip.innerHTML = `
      <img src="${ASSET}/resources/${def.icon}" alt="${def.name}">
      <span>${formatNum(r.amount)} / ${formatNum(r.cap)}</span>
    `;
    topBarEl.appendChild(chip);
  }
}

// ---------- Corner hud (collect-all + gems) ----------------------------

let cornerEl: HTMLElement | null = null;

export function renderCornerHud(state: StateSnap, onCollect: () => void) {
  const root = $("hud-root");
  if (!cornerEl) {
    cornerEl = document.createElement("div");
    cornerEl.className = "hud-corner hud-layer";
    root.appendChild(cornerEl);
  }
  cornerEl.innerHTML = "";

  const total = Object.values(state.pending_total || {}).reduce((a, b) => a + Number(b), 0);
  if (total > 0) {
    const btn = document.createElement("button");
    btn.className = "collect-all-btn";
    btn.innerHTML = `<span class="pulse-dot"></span>Собрать ${formatNum(total)}`;
    btn.onclick = onCollect;
    cornerEl.appendChild(btn);
  }

  // Gems chip.
  const gemsChip = document.createElement("div");
  gemsChip.className = "res-chip";
  gemsChip.innerHTML = `
    <img src="${ASSET}/resources/res_gems.png" alt="Гемы">
    <span>${formatNum(state.user.gems_balance)}</span>
  `;
  cornerEl.appendChild(gemsChip);
}

// ---------- Bottom nav ------------------------------------------------

let bottomEl: HTMLElement | null = null;

interface NavCallbacks {
  onBuild: () => void;
  onQuests: () => void;
  onTech: () => void;
  onFriends: () => void;
  onShop: () => void;
}

export function renderBottomNav(cb: NavCallbacks) {
  const root = $("hud-root");
  if (!bottomEl) {
    bottomEl = document.createElement("div");
    bottomEl.className = "hud-bottom hud-layer";
    root.appendChild(bottomEl);
  }
  bottomEl.innerHTML = "";
  const buttons: Array<[string, string, () => void]> = [
    ["ui_button_build.png", "Build", cb.onBuild],
    ["ui_button_tech.png", "Tech", cb.onTech],
    ["ui_button_quests.png", "Quests", cb.onQuests],
    ["ui_button_friends.png", "Friends", cb.onFriends],
    ["ui_button_shop.png", "Shop", cb.onShop],
  ];
  for (const [icon, label, onClick] of buttons) {
    const btn = document.createElement("button");
    btn.className = "nav-btn";
    btn.innerHTML = `<img src="${ASSET}/ui/${icon}" alt="${label}">`;
    btn.onclick = onClick;
    bottomEl.appendChild(btn);
  }
}

// ---------- Toast ------------------------------------------------------

export function toast(text: string, kind: "success" | "error" | "info" = "info", durationMs = 2500) {
  const root = $("hud-root");
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = text;
  root.appendChild(el);
  setTimeout(() => {
    el.style.transition = "opacity 220ms";
    el.style.opacity = "0";
    setTimeout(() => el.remove(), 220);
  }, durationMs);
}

// ---------- Modal helper ----------------------------------------------

function modal(title: string, bodyHtml: string, actions: Array<{ label: string; cls?: string; onClick: () => void; }>): { close: () => void; root: HTMLElement } {
  const root = $("hud-root");
  const overlay = document.createElement("div");
  overlay.className = "modal-overlay hud-layer";

  overlay.innerHTML = `
    <div class="modal">
      <div class="modal-header">
        <div class="modal-title">${title}</div>
        <button class="modal-close" aria-label="Close">
          <img src="${ASSET}/ui/ui_button_close.png" alt="">
        </button>
      </div>
      <div class="modal-body">${bodyHtml}</div>
      <div class="modal-actions"></div>
    </div>
  `;
  root.appendChild(overlay);

  const close = () => {
    overlay.style.transition = "opacity 200ms";
    overlay.style.opacity = "0";
    setTimeout(() => overlay.remove(), 200);
  };

  const closeBtn = overlay.querySelector(".modal-close") as HTMLButtonElement;
  closeBtn.onclick = close;
  overlay.onclick = (ev) => { if (ev.target === overlay) close(); };

  const actionsDiv = overlay.querySelector(".modal-actions") as HTMLElement;
  for (const a of actions) {
    const btn = document.createElement("button");
    btn.className = `btn ${a.cls || ""}`;
    btn.textContent = a.label;
    btn.onclick = a.onClick;
    actionsDiv.appendChild(btn);
  }

  return { close, root: overlay };
}

// ---------- Build menu -------------------------------------------------

export function showBuildMenu(scene: Phaser.Scene, config: ConfigSnap, state: StateSnap) {
  const cards = Object.entries(config.buildings).map(([type, def]) => {
    const lvl1 = def.levels[0];
    const cost = lvl1.cost || {};
    const eraOk = state.user.era >= def.era;
    const builtCount = state.buildings.filter((b) => b.type === type).length;
    const limitOk = builtCount < (def.max_per_user || 99);
    const locked = !eraOk || !limitOk;
    const reason = !eraOk ? `Эпоха ${def.era}+` : !limitOk ? `Макс. ${def.max_per_user}` : "";

    let costHtml = "";
    if (Object.keys(cost).length === 0) {
      costHtml = '<span style="color: var(--green-positive); font-weight: 700;">Бесплатно</span>';
    } else {
      costHtml = Object.entries(cost).map(([rt, c]) => {
        const have = Number(state.resources.find((r) => r.type === rt)?.amount || "0");
        const lacking = have < c;
        const rdef = config.resources[rt];
        return `<span class="cost-item ${lacking ? "lacking" : ""}">
          <img src="${ASSET}/resources/${rdef?.icon || ""}">${c}
        </span>`;
      }).join("");
    }

    const time = lvl1.build_time_seconds;
    const timeStr = time === 0 ? "сразу" : time < 60 ? `${time}с` : time < 3600 ? `${Math.round(time/60)}м` : `${Math.round(time/3600)}ч`;

    return `
      <div class="build-card ${locked ? "locked" : ""}" data-type="${type}" data-locked="${locked ? "1" : "0"}">
        <div class="img-wrap">
          <img src="${ASSET}/buildings/${def.icon}" alt="${def.name}">
        </div>
        <div class="name">${def.name}</div>
        <div class="meta">
          <div class="cost-list" style="justify-content: center;">${costHtml}</div>
          <div style="margin-top: 4px;">⏱ ${timeStr}${reason ? ` · ${reason}` : ""}</div>
        </div>
      </div>
    `;
  }).join("");

  const m = modal(
    "Построить",
    `<div class="build-grid">${cards}</div>`,
    [
      { label: "Закрыть", onClick: () => m.close() },
    ],
  );

  m.root.querySelectorAll(".build-card").forEach((card) => {
    (card as HTMLElement).onclick = () => {
      if (card.getAttribute("data-locked") === "1") return;
      const type = card.getAttribute("data-type")!;
      haptic("light");
      m.close();
      scene.events.emit("ui:place", type);
    };
  });
}

// ---------- Building details ------------------------------------------

export function showBuildingDetails(scene: Phaser.Scene, b: BuildingSnap) {
  const config = scene.registry.get("config") as ConfigSnap;
  const state = scene.registry.get("state") as StateSnap;
  const def = config.buildings[b.type];
  if (!def) return;
  const lvl = b.level;
  const ldef = def.levels[lvl - 1];
  const nextLdef = def.levels[lvl] || null;

  const output = ldef.output_per_hour || {};
  const outputHtml = Object.entries(output).map(([rt, n]) => {
    const rdef = config.resources[rt];
    return `<span class="cost-item"><img src="${ASSET}/resources/${rdef?.icon || ""}">${n}/ч</span>`;
  }).join("") || '<span style="color: var(--text-secondary);">—</span>';

  const storageBonus = ldef.storage_bonus || {};
  const storageHtml = Object.keys(storageBonus).length > 0
    ? `<div class="modal-row"><b>Склад:</b> ` +
      Object.entries(storageBonus).map(([rt, n]) => {
        const rdef = config.resources[rt];
        return `<span class="cost-item"><img src="${ASSET}/resources/${rdef?.icon || ""}">+${n}</span>`;
      }).join(" ") + `</div>`
    : "";

  let upgradeHtml = "";
  let canUpgrade = false;
  let upgradeBlockedReason = "";
  if (nextLdef) {
    const cost = nextLdef.cost || {};
    const costHtml = Object.entries(cost).map(([rt, c]) => {
      const have = Number(state.resources.find((r) => r.type === rt)?.amount || "0");
      const rdef = config.resources[rt];
      return `<span class="cost-item ${have < c ? "lacking" : ""}">
        <img src="${ASSET}/resources/${rdef?.icon || ""}">${c}
      </span>`;
    }).join("");
    const lacking = Object.entries(cost).some(([rt, c]) => Number(state.resources.find((r) => r.type === rt)?.amount || "0") < c);
    canUpgrade = !lacking && b.status === "active";
    if (b.status !== "active") upgradeBlockedReason = "Здание сейчас занято";
    else if (lacking) upgradeBlockedReason = "Не хватает ресурсов";

    const time = nextLdef.build_time_seconds;
    const timeStr = time === 0 ? "сразу" : time < 60 ? `${time}с` : time < 3600 ? `${Math.round(time/60)}м` : `${Math.round(time/3600)}ч`;
    upgradeHtml = `
      <div class="modal-row" style="border-top: 1px solid var(--wood-dark); padding-top: 12px;">
        <b>Апгрейд до ур. ${lvl + 1}:</b>
        <div class="cost-list" style="margin-top: 4px;">${costHtml}</div>
        <div style="margin-top: 4px; color: var(--text-secondary); font-size: 13px;">⏱ ${timeStr}</div>
      </div>
    `;
  }

  let pendingHtml = "";
  const pending = b.pending_collect || {};
  const totalPending = Object.values(pending).reduce((a, x) => a + Number(x), 0);
  if (totalPending > 0) {
    pendingHtml = `<div class="modal-row"><span class="collect-tag">Накоплено: ${formatNum(totalPending)}</span></div>`;
  }

  const statusBadge = b.status === "building" ? '<span style="color: var(--orange-event);">⏳ Строится</span>'
    : b.status === "upgrading" ? '<span style="color: var(--orange-event);">⏳ Апгрейд</span>'
    : '<span style="color: var(--green-positive);">✓ Активно</span>';

  const finishStr = b.finish_at ? formatTimeRemaining(b.finish_at) : null;

  const body = `
    <div style="text-align: center; margin-bottom: 12px;">
      <img src="${ASSET}/buildings/${ldef.icon}" style="max-width: 60%; max-height: 140px;">
    </div>
    <div class="modal-row" style="text-align: center;">
      <div style="font-size: 13px; color: var(--text-secondary);">${def.description}</div>
    </div>
    <div class="modal-row">${statusBadge}${finishStr ? ` · ${finishStr}` : ""}</div>
    ${pendingHtml}
    <div class="modal-row"><b>Уровень:</b> ${lvl} / ${def.max_level}</div>
    <div class="modal-row"><b>Производит:</b> ${outputHtml}</div>
    ${storageHtml}
    ${upgradeHtml}
  `;

  const actions: Array<{ label: string; cls?: string; onClick: () => void; }> = [];

  if (totalPending > 0 && b.status === "active") {
    actions.push({
      label: `Собрать ${formatNum(totalPending)}`,
      cls: "btn-primary",
      onClick: async () => {
        haptic("medium");
        const r = await api.collectAll();
        if (r.ok && r.data) {
          hapticNotify("success");
          scene.events.emit("ui:state_updated", r.data.state);
          m.close();
        }
      },
    });
  }

  if (nextLdef && lvl < def.max_level) {
    actions.push({
      label: canUpgrade ? `Прокачать` : (upgradeBlockedReason || "Прокачать"),
      cls: "btn-primary",
      onClick: async () => {
        if (!canUpgrade) {
          toast(upgradeBlockedReason || "Нельзя", "error");
          return;
        }
        haptic("medium");
        const r = await api.upgrade(b.id);
        if (r.ok && r.data) {
          hapticNotify("success");
          toast("Апгрейд начат!", "success");
          scene.events.emit("ui:state_updated", r.data.state);
          m.close();
        } else {
          hapticNotify("error");
          toast("Не получилось", "error");
        }
      },
    });
  }

  if (def.is_demolishable !== false && b.status === "active") {
    actions.push({
      label: "Снести",
      cls: "btn-danger",
      onClick: async () => {
        if (!confirm(`Снести ${def.name} ур. ${lvl}? Вернётся 50% стоимости.`)) return;
        haptic("heavy");
        const r = await api.demolish(b.id);
        if (r.ok && r.data) {
          hapticNotify("success");
          scene.events.emit("ui:state_updated", r.data.state);
          m.close();
        }
      },
    });
  }

  actions.push({ label: "Закрыть", onClick: () => m.close() });

  const m = modal(`${def.name}`, body, actions);
}

function formatTimeRemaining(iso: string): string {
  const finish = new Date(iso).getTime();
  const left = Math.max(0, finish - Date.now()) / 1000;
  if (left <= 0) return "готово";
  const h = Math.floor(left / 3600);
  const m = Math.floor((left % 3600) / 60);
  const s = Math.floor(left % 60);
  if (h > 0) return `${h}ч ${m}м`;
  if (m > 0) return `${m}м ${s}с`;
  return `${s}с`;
}

// ---------- Quest list ------------------------------------------------

export function showQuestList(
  _scene: Phaser.Scene,
  state: StateSnap,
  _config: ConfigSnap,
  onClaim: (questId: string) => Promise<boolean>,
) {
  const active = state.quests.filter((q) => q.status !== "claimed");
  const claimed = state.quests.filter((q) => q.status === "claimed");

  const cardHtml = (q: any) => {
    const rewards: string[] = [];
    if (q.rewards.gold) rewards.push(`🪙 ${q.rewards.gold}`);
    if (q.rewards.gems) rewards.push(`💎 ${q.rewards.gems}`);
    if (q.rewards.experience) rewards.push(`⭐ +${q.rewards.experience}`);
    for (const r of ["wood", "stone", "food", "water"]) {
      if (q.rewards[r]) rewards.push(`${q.rewards[r]} ${r}`);
    }
    const claimable = q.status === "completed";
    return `
      <div class="quest-card ${q.status === "claimed" ? "claimed" : (claimable ? "completed" : "")}">
        <div class="quest-name">${q.name}</div>
        <div class="quest-desc">${q.description}</div>
        <div class="quest-rewards">${rewards.join(" · ")}</div>
        ${claimable ? `<button class="btn btn-primary quest-claim-btn" data-quest="${q.id}">Забрать</button>` : ""}
      </div>
    `;
  };

  const body = `
    ${active.length === 0 ? '<div style="color: var(--text-secondary); text-align: center; padding: 16px;">Активных квестов нет</div>' : ""}
    ${active.map(cardHtml).join("")}
    ${claimed.length > 0 ? `<div style="margin-top: 16px; opacity: 0.7;"><b>Завершённые:</b></div>` : ""}
    ${claimed.map(cardHtml).join("")}
  `;

  const m = modal("Квесты", body, [
    { label: "Закрыть", onClick: () => m.close() },
  ]);

  m.root.querySelectorAll(".quest-claim-btn").forEach((btn) => {
    (btn as HTMLButtonElement).onclick = async () => {
      const qid = btn.getAttribute("data-quest")!;
      const ok = await onClaim(qid);
      if (ok) m.close();
    };
  });
}
