import { api, haptic, hapticNotify } from "../api";
import { store } from "../state";
import type { BusinessDef, BusinessState, ResourceMeta } from "../types";
import { ASSET_BASE, el, fmt } from "../util";
import { toast } from "../ui/toast";
import { showBusinessTreeModal } from "./biz_tree_modal";

let root: HTMLElement | null = null;
let pendingPolls: any = null;
// Client-side biz tap rate cap (mirrors server's biz_tap_window).
let bizTapWindowStart = 0;
let bizTapWindowCount = 0;

function hasClickerPermit(): boolean {
  const u = store.state?.upgrades.find((x) => x.kind === "permit" && x.slot_id === "clicker_permit");
  return !!(u && u.level > 0);
}

export function mountBusinessTab(parent: HTMLElement): HTMLElement {
  root = el("div", { className: "tab-page", dataset: { tab: "business" } });
  parent.appendChild(root);
  store.subscribe(render);
  render();

  // Refresh pending production every 2 sec while tab is open.
  if (pendingPolls) clearInterval(pendingPolls);
  pendingPolls = setInterval(() => {
    if (store.activeTab !== "business") return;
    if (!root || !store.state) return;
    // Re-render only the resource displays in cards (cheap).
    refreshPendings();
  }, 2000);

  return root;
}

function render() {
  if (!root || !store.state || !store.config) return;
  if (store.activeTab !== "business" && root.dataset.lastRender) return;

  // Preserve scroll position across re-renders to fix the "tap → jumps to top" bug.
  const oldContent = root.querySelector(".tab-page-content") as HTMLElement | null;
  const savedScroll = oldContent ? oldContent.scrollTop : 0;

  root.innerHTML = "";
  const content = el("div", { className: "tab-page-content" });

  content.appendChild(el("div", { className: "tab-title", textContent: "🏭 БИЗНЕСЫ" }));
  content.appendChild(el("div", {
    className: "tab-subtitle",
    textContent: "Тапай или жди — каждый бизнес производит свой ресурс. Прокачка ускоряет производство.",
  }));

  // Resources HUD
  const resHud = el("div", { className: "biz-res-hud" });
  for (const bdef of store.config.businesses) {
    const meta = store.config.resources_meta[bdef.resource];
    if (!meta) continue;
    const have = store.state.resources[bdef.resource] || "0";
    const chip = el("div", { className: "biz-res-chip", style: { borderColor: meta.color } });
    chip.appendChild(el("img", { src: `${ASSET_BASE}/${meta.icon}`, alt: meta.name }));
    chip.appendChild(el("span", { className: "amt", textContent: fmt(have), style: { color: meta.color } }));
    chip.title = meta.name;
    resHud.appendChild(chip);
  }
  content.appendChild(resHud);

  // Total pending across all businesses
  const totalPending = (store.state.businesses || []).reduce((s, b) => s + Number(b.pending || 0), 0);
  if (totalPending >= 1) {
    const collectAll = el("button", { className: "collect-all-btn-biz", textContent: `📦 СОБРАТЬ ВСЁ (${fmt(totalPending)})` });
    collectAll.onclick = async () => {
      haptic("medium");
      const r = await api.businessCollect(null);
      if (r.ok && r.data) {
        const collected = (r.data as any).collected || {};
        const totalNum = Object.values(collected).reduce((a: number, b: any) => a + Number(b), 0);
        if (totalNum > 0) {
          hapticNotify("success");
          toast(`+${fmt(totalNum)} ресурсов`, "success");
        }
        if ((r.data as any).state) store.setState((r.data as any).state);
      }
    };
    content.appendChild(collectAll);
  }

  // Cards
  const grid = el("div", { className: "biz-grid" });
  for (const bdef of store.config.businesses) {
    grid.appendChild(renderCard(bdef));
  }
  content.appendChild(grid);

  root.appendChild(content);
  root.dataset.lastRender = String(Date.now());

  // Restore scroll position.
  if (savedScroll > 0) {
    requestAnimationFrame(() => {
      content.scrollTop = savedScroll;
    });
  }
}

function renderCard(bdef: BusinessDef): HTMLElement {
  if (!store.state || !store.config) return el("div");
  const userMax = store.state.user.max_level;
  const locked = userMax < bdef.unlock_level;
  const state = (store.state.businesses || []).find((b) => b.id === bdef.id);
  const meta = store.config.resources_meta[bdef.resource];
  const haveCash = Number(store.state.user.cash);

  const card = el("div", { className: `biz-card ${locked ? "locked" : ""}`, dataset: { biz: bdef.id } });

  // Header: image + name + level
  const head = el("div", { className: "biz-head" });
  const imgWrap = el("div", { className: "biz-img-wrap" });
  imgWrap.appendChild(el("img", { src: `${ASSET_BASE}/${bdef.icon}`, alt: bdef.name }));
  head.appendChild(imgWrap);
  const headInfo = el("div", { className: "biz-head-info" });
  headInfo.appendChild(el("div", { className: "biz-name", textContent: bdef.name }));
  if (locked) {
    headInfo.appendChild(el("div", { className: "biz-lock", textContent: `🔒 Открывается на ур. ${bdef.unlock_level}` }));
  } else {
    headInfo.appendChild(el("div", { className: "biz-stat", innerHTML: `Ур. <b>${state?.level ?? 0}</b> · ${meta?.emoji || ""} ${meta?.name || bdef.resource}` }));
    headInfo.appendChild(el("div", { className: "biz-rate", textContent: `+${fmt(state?.rate_per_sec || "0")}/сек` }));
    // Idle consumption (Gun Range eats Energy etc).
    const cons = state?.idle_consumption_per_sec || {};
    const consParts: string[] = [];
    let starving = false;
    for (const [resType, amount] of Object.entries(cons)) {
      const m = store.config.resources_meta[resType];
      const haveR = Number(store.state.resources[resType] || "0");
      const need = Number(amount);
      if (need > 0 && haveR < need * 5) starving = true; // <5 sec sustainable
      consParts.push(`${m?.emoji || ""}-${fmt(amount)}/сек`);
    }
    if (consParts.length > 0) {
      headInfo.appendChild(el("div", {
        className: starving ? "biz-cons biz-cons-low" : "biz-cons",
        textContent: `Жрёт: ${consParts.join(" · ")}${starving ? " ⚠" : ""}`,
      }));
    }
  }
  head.appendChild(headInfo);
  card.appendChild(head);

  if (locked) {
    return card;
  }

  // Pending bar + current owned amount
  const pending = Number(state?.pending || 0);
  const owned = Number(store.state.resources[bdef.resource] || "0");
  const pendingRow = el("div", { className: "biz-pending-row" });
  const pendingEl = el("div", { className: "biz-pending", dataset: { pendingFor: bdef.id } });
  pendingEl.appendChild(el("span", { textContent: `📦 Накоплено: ${fmtPending(pending)}` }));
  pendingRow.appendChild(pendingEl);
  const ownedEl = el("div", { className: "biz-owned" });
  ownedEl.appendChild(el("span", { textContent: `${meta?.emoji || ""} Имею: ${fmt(owned)}`, style: { color: meta?.color || "#fff" } }));
  pendingRow.appendChild(ownedEl);
  card.appendChild(pendingRow);

  // Action row
  const actions = el("div", { className: "biz-actions" });

  const tapBtn = el("button", { className: "biz-tap-btn" });
  tapBtn.appendChild(el("span", { textContent: "ТАП" }));
  tapBtn.appendChild(el("span", { className: "small", textContent: `+${fmt(state?.tap_yield || "0")}` }));
  tapBtn.onclick = async () => {
    // Client-side rate cap matching server. Without permit, silently swallow
    // clicks beyond cap so the user doesn't feel like the button is "broken".
    if (!hasClickerPermit()) {
      const cap = Number(store.config?.constants?.tap_rate_base ?? 10);
      const now = Date.now();
      if (now - bizTapWindowStart < 1000) {
        if (bizTapWindowCount >= cap) return;
        bizTapWindowCount++;
      } else {
        bizTapWindowStart = now;
        bizTapWindowCount = 1;
      }
    }
    haptic("light");
    const r = await api.businessTap(bdef.id);
    if (r.ok && r.data) {
      if ((r.data as any).state) store.setState((r.data as any).state);
    } else if (r.error === "throttled") {
      // Server desynced (rare). Just swallow.
      return;
    } else {
      hapticNotify("error");
      toast(translateError(r.error), "error");
    }
  };
  actions.appendChild(tapBtn);

  const collectBtn = el("button", { className: "biz-collect-btn", dataset: { collectFor: bdef.id } });
  collectBtn.appendChild(el("span", { textContent: "СОБРАТЬ" }));
  // Show "collectable now" — already capped by available fuel — instead of optimistic pending.
  const collectableNow = Number(state?.collectable_now ?? state?.pending ?? 0);
  collectBtn.appendChild(el("span", { className: "small", textContent: fmtPending(collectableNow) }));
  if (collectableNow < 1) collectBtn.disabled = true;
  collectBtn.onclick = async () => {
    haptic("medium");
    const r = await api.businessCollect(bdef.id);
    if (r.ok && r.data) {
      hapticNotify("success");
      const data = r.data as any;
      // Show explicit fuel consumption so the player sees where their resources went.
      const fuelSpent = data.fuel_spent || {};
      const collected = data.collected || {};
      const meta = store.config?.resources_meta || {};
      const fuelParts: string[] = [];
      for (const [res, amt] of Object.entries(fuelSpent)) {
        if (Number(amt) > 0) fuelParts.push(`${meta[res]?.emoji || ""}-${fmt(amt as string)}`);
      }
      const gotParts: string[] = [];
      for (const [res, amt] of Object.entries(collected)) {
        if (Number(amt) > 0) gotParts.push(`${meta[res]?.emoji || ""}+${fmt(amt as string)}`);
      }
      if (gotParts.length > 0 || fuelParts.length > 0) {
        toast([...gotParts, ...fuelParts].join(" "), "info", 2200);
      }
      if (data.state) store.setState(data.state);
    }
  };
  actions.appendChild(collectBtn);

  const upgradeBtn = el("button", { className: "biz-upgrade-btn" });
  upgradeBtn.appendChild(el("span", { textContent: `АП ур.${(state?.level ?? 0) + 1}` }));
  // Build cost label: $cost + each resource cost
  const costParts: string[] = [`$${fmt(state?.upgrade_cost || "0")}`];
  const resCost = state?.upgrade_resource_cost || {};
  let resourcesEnough = true;
  for (const [resType, amount] of Object.entries(resCost)) {
    const have = Number(store.state.resources[resType] || "0");
    const need = Number(amount);
    if (have < need) resourcesEnough = false;
    const meta = store.config.resources_meta[resType];
    costParts.push(`${meta?.emoji || ""}${fmt(amount)}`);
  }
  upgradeBtn.appendChild(el("span", { className: "small", textContent: costParts.join(" · ") }));
  const cashOk = haveCash >= Number(state?.upgrade_cost || 0);
  if (!cashOk || !resourcesEnough) upgradeBtn.disabled = true;
  upgradeBtn.onclick = async () => {
    haptic("medium");
    const r = await api.businessUpgrade(bdef.id);
    if (r.ok && r.data) {
      hapticNotify("success");
      toast(`${bdef.name} → ур. ${(r.data as any).new_level}`, "success");
      if ((r.data as any).state) store.setState((r.data as any).state);
    } else {
      hapticNotify("error");
      toast(translateError(r.error, r.resource), "error");
    }
  };
  actions.appendChild(upgradeBtn);

  // 4th action: open branch tree
  const treeBtn = el("button", { className: "biz-tree-btn" });
  const branchSum = Object.values(state?.branches || {}).reduce((a, b) => a + (b || 0), 0);
  treeBtn.appendChild(el("span", { textContent: "ДЕРЕВО" }));
  treeBtn.appendChild(el("span", { className: "small", textContent: branchSum > 0 ? `${branchSum} ур.` : "5 веток" }));
  treeBtn.onclick = () => {
    haptic("light");
    showBusinessTreeModal(bdef.id);
  };
  actions.appendChild(treeBtn);

  card.appendChild(actions);

  return card;
}

function refreshPendings() {
  if (!root || !store.state) return;
  // Compute live pending = server's pending value at server_time, plus rate × elapsed-since-server_time.
  const serverTime = new Date(store.state.server_time).getTime();
  const elapsedMs = Date.now() - serverTime;
  const elapsedSec = Math.max(0, elapsedMs / 1000);
  for (const b of store.state.businesses || []) {
    const node = root.querySelector(`[data-pending-for="${b.id}"] span`);
    if (node) {
      const live = Number(b.pending) + Number(b.rate_per_sec) * elapsedSec;
      node.textContent = `📦 Накоплено: ${fmtPending(live)}`;
    }
    const collectBtn = root.querySelector(`[data-collect-for="${b.id}"]`) as HTMLButtonElement | null;
    if (collectBtn) {
      const live = Number(b.pending) + Number(b.rate_per_sec) * elapsedSec;
      const small = collectBtn.querySelector(".small");
      if (small) small.textContent = fmtPending(live);
      collectBtn.disabled = live < 1;
    }
  }
}

function fmtPending(v: number): string {
  if (v < 0.1) return "0";
  if (v < 10) return v.toFixed(1).replace(/\.0$/, "").replace(".", ",");
  return fmt(Math.floor(v));
}

function translateError(err?: string, resource?: string): string {
  switch (err) {
    case "locked": return "Ещё заблокировано";
    case "not_enough_cash": return "Недостаточно $";
    case "not_enough_resource":
      if (resource && store.config) {
        const meta = store.config.resources_meta[resource];
        return `Не хватает ${meta?.name || resource}`;
      }
      return "Не хватает ресурсов";
    case "unknown_business": return "Бизнес не найден";
    default: return err || "Ошибка";
  }
}
