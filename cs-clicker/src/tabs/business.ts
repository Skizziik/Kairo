import { api, haptic, hapticNotify } from "../api";
import { store } from "../state";
import type { BusinessDef, BusinessState, ResourceMeta } from "../types";
import { ASSET_BASE, el, fmt } from "../util";
import { toast } from "../ui/toast";

let root: HTMLElement | null = null;
let pendingPolls: any = null;

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
  }
  head.appendChild(headInfo);
  card.appendChild(head);

  if (locked) {
    return card;
  }

  // Pending bar
  const pending = Number(state?.pending || 0);
  const pendingEl = el("div", { className: "biz-pending", dataset: { pendingFor: bdef.id } });
  pendingEl.appendChild(el("span", {
    textContent: `📦 Накоплено: ${fmt(Math.floor(pending))}`,
  }));
  card.appendChild(pendingEl);

  // Action row
  const actions = el("div", { className: "biz-actions" });

  const tapBtn = el("button", { className: "biz-tap-btn" });
  tapBtn.appendChild(el("span", { textContent: "ТАП" }));
  tapBtn.appendChild(el("span", { className: "small", textContent: `+${fmt(state?.tap_yield || "0")}` }));
  tapBtn.onclick = async () => {
    haptic("light");
    const r = await api.businessTap(bdef.id);
    if (r.ok && r.data) {
      if ((r.data as any).state) store.setState((r.data as any).state);
    } else {
      hapticNotify("error");
      toast(translateError(r.error), "error");
    }
  };
  actions.appendChild(tapBtn);

  const collectBtn = el("button", { className: "biz-collect-btn" });
  collectBtn.appendChild(el("span", { textContent: "СОБРАТЬ" }));
  collectBtn.appendChild(el("span", { className: "small", textContent: `${fmt(Math.floor(pending))}` }));
  if (Math.floor(pending) <= 0) collectBtn.disabled = true;
  collectBtn.onclick = async () => {
    haptic("medium");
    const r = await api.businessCollect(bdef.id);
    if (r.ok && r.data) {
      hapticNotify("success");
      if ((r.data as any).state) store.setState((r.data as any).state);
    }
  };
  actions.appendChild(collectBtn);

  const upgradeBtn = el("button", { className: "biz-upgrade-btn" });
  upgradeBtn.appendChild(el("span", { textContent: `АП ур.${(state?.level ?? 0) + 1}` }));
  upgradeBtn.appendChild(el("span", { className: "small", textContent: `$${fmt(state?.upgrade_cost || "0")}` }));
  if (haveCash < Number(state?.upgrade_cost || 0)) upgradeBtn.disabled = true;
  upgradeBtn.onclick = async () => {
    haptic("medium");
    const r = await api.businessUpgrade(bdef.id);
    if (r.ok && r.data) {
      hapticNotify("success");
      toast(`${bdef.name} → ур. ${(r.data as any).new_level}`, "success");
      if ((r.data as any).state) store.setState((r.data as any).state);
    } else {
      hapticNotify("error");
      toast(translateError(r.error), "error");
    }
  };
  actions.appendChild(upgradeBtn);

  card.appendChild(actions);

  return card;
}

function refreshPendings() {
  if (!root || !store.state) return;
  for (const b of store.state.businesses || []) {
    const node = root.querySelector(`[data-pending-for="${b.id}"] span`);
    if (node) {
      const last = (store.state as any)._pendingTime || Date.now();
      const elapsed = (Date.now() - last) / 1000;
      const live = Number(b.pending) + Number(b.rate_per_sec) * elapsed;
      node.textContent = `📦 Накоплено: ${fmt(Math.floor(live))}`;
    }
  }
}

function translateError(err?: string): string {
  switch (err) {
    case "locked": return "Ещё заблокировано";
    case "not_enough_cash": return "Недостаточно $";
    case "unknown_business": return "Бизнес не найден";
    default: return err || "Ошибка";
  }
}
