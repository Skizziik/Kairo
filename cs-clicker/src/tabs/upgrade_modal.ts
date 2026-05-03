import { api, haptic, hapticNotify } from "../api";
import { store } from "../state";
import type { CritLuckDef, MercDef, WeaponDef } from "../types";
import { ASSET_BASE, el, fmt } from "../util";
import { openModal, type ModalHandle } from "../ui/modal";
import { toast } from "../ui/toast";

let activeModal: ModalHandle | null = null;
let unsubscribe: (() => void) | null = null;
let bulkSize = 1;

export function showUpgradeModal() {
  if (activeModal) return;
  const body = el("div");

  const tabsEl = el("div", { className: "lb-tabs" });
  const sections = [
    { id: "weapons",  label: "ОРУЖИЕ" },
    { id: "mercs",    label: "НАЁМНИКИ" },
    { id: "crit",     label: "КРИТ" },
    { id: "luck",     label: "УДАЧА" },
  ];
  let currentSection: "weapons" | "mercs" | "crit" | "luck" = "weapons";

  // bulk-buy switch
  const bulkRow = el("div", { className: "lb-tabs", style: { marginTop: "0", marginBottom: "8px" } });
  for (const n of [1, 5, 10, 25]) {
    const t = el("div", { className: `lb-tab ${n === bulkSize ? "active" : ""}`, dataset: { bulk: String(n) }, textContent: `×${n}` });
    t.onclick = () => {
      bulkSize = n;
      bulkRow.querySelectorAll(".lb-tab").forEach((b) => {
        const v = Number((b as HTMLElement).dataset.bulk);
        (b as HTMLElement).classList.toggle("active", v === bulkSize);
      });
      renderList();
    };
    bulkRow.appendChild(t);
  }

  const list = el("div", { className: "upg-section" });

  for (const sec of sections) {
    const t = el("div", { className: `lb-tab ${sec.id === currentSection ? "active" : ""}`, dataset: { sec: sec.id }, textContent: sec.label });
    t.onclick = () => {
      currentSection = sec.id as any;
      tabsEl.querySelectorAll(".lb-tab").forEach((b) => {
        (b as HTMLElement).classList.toggle("active", (b as HTMLElement).dataset.sec === currentSection);
      });
      renderList();
    };
    tabsEl.appendChild(t);
  }

  body.appendChild(tabsEl);
  body.appendChild(bulkRow);
  body.appendChild(list);

  activeModal = openModal({
    title: "АПГРЕЙДЫ",
    body,
    actions: [{ label: "Закрыть", onClick: () => activeModal?.close() }],
  });
  // Cleanup on close.
  const obs = new MutationObserver(() => {
    if (!document.body.contains(activeModal!.root)) {
      obs.disconnect();
      unsubscribe?.();
      activeModal = null;
    }
  });
  obs.observe(document.body, { childList: true });

  unsubscribe = store.subscribe(renderList);
  renderList();

  function renderList() {
    list.innerHTML = "";
    if (!store.config || !store.state) return;
    if (currentSection === "weapons") {
      for (const w of store.config.weapons) list.appendChild(renderWeapon(w));
    } else if (currentSection === "mercs") {
      for (const m of store.config.mercs) list.appendChild(renderMerc(m));
    } else if (currentSection === "crit") {
      for (const u of store.config.crit_luck.crit_chance) list.appendChild(renderCritLuck("crit_chance", u));
      for (const u of store.config.crit_luck.crit_damage) list.appendChild(renderCritLuck("crit_damage", u));
    } else if (currentSection === "luck") {
      for (const u of store.config.crit_luck.luck) list.appendChild(renderCritLuck("luck", u));
    }
  }
}

function ownedLevel(kind: string, slot_id: string): number {
  const u = store.state?.upgrades.find((x) => x.kind === kind && x.slot_id === slot_id);
  return u ? u.level : 0;
}

function totalCost(baseCost: number, fromLevel: number, count: number): number {
  let c = 0;
  for (let i = 0; i < count; i++) c += baseCost * Math.pow(1.15, fromLevel + i);
  return Math.ceil(c);
}

function renderWeapon(w: WeaponDef): HTMLElement {
  const slot = `weapon_${w.id}`;
  const lvl = ownedLevel("weapon", slot);
  return renderUpgCard({
    kind: "weapon",
    slot_id: slot,
    name: w.name,
    icon: w.icon,
    level: lvl,
    maxLevel: w.max_level,
    unlock_level: w.unlock_level,
    base_cost: w.base_cost,
    description: `Урон: ${fmt(w.base_dmg * (1 + lvl * 0.20))}`,
  });
}

function renderMerc(m: MercDef): HTMLElement {
  const slot = `merc_${m.id}`;
  const lvl = ownedLevel("merc", slot);
  return renderUpgCard({
    kind: "merc",
    slot_id: slot,
    name: m.name,
    icon: m.icon,
    level: lvl,
    maxLevel: m.max_level,
    unlock_level: m.unlock_level,
    base_cost: m.base_cost,
    description: `${m.role} · ${fmt(m.base_dps * (1 + lvl * 0.20))}/сек`,
  });
}

function renderCritLuck(kind: "crit_chance" | "crit_damage" | "luck", u: CritLuckDef): HTMLElement {
  const prefix = kind === "crit_chance" ? "cc" : kind === "crit_damage" ? "cd" : "lk";
  const slot = `${prefix}_${u.id}`;
  const lvl = ownedLevel(kind, slot);
  const label = kind === "crit_chance" ? "Crit Chance" : kind === "crit_damage" ? "Crit Damage" : "Luck";
  const totalPct = u.per_level_pct * lvl;
  return renderUpgCard({
    kind,
    slot_id: slot,
    name: u.name,
    icon: u.icon,
    level: lvl,
    maxLevel: u.max_level,
    unlock_level: u.unlock_level,
    base_cost: u.base_cost,
    description: `${label}: +${totalPct}% (+${u.per_level_pct}%/ур)`,
  });
}

function renderUpgCard(opts: {
  kind: string; slot_id: string; name: string; icon: string;
  level: number; maxLevel: number; unlock_level: number; base_cost: number;
  description: string;
}): HTMLElement {
  const card = el("div", { className: "upg-card" });
  const userLevel = store.state?.user.max_level || 0;
  const cash = Number(store.state?.user.cash || "0");
  const locked = userLevel < opts.unlock_level;
  const maxed = opts.level >= opts.maxLevel;

  if (locked) card.classList.add("locked");

  const imgWrap = el("div", { className: "img-wrap" });
  imgWrap.appendChild(el("img", { src: `${ASSET_BASE}/${opts.icon}`, alt: opts.name }));
  card.appendChild(imgWrap);

  const info = el("div", { className: "info" });
  const name = el("div", { className: "name" });
  name.appendChild(el("span", { className: "lvl-pill", textContent: `${opts.level}/${opts.maxLevel}` }));
  name.appendChild(document.createTextNode(opts.name));
  info.appendChild(name);
  info.appendChild(el("div", { className: "meta", textContent: locked ? `🔒 Открывается на ур. ${opts.unlock_level}` : opts.description }));
  card.appendChild(info);

  const buyN = Math.max(1, Math.min(bulkSize, opts.maxLevel - opts.level));
  const cost = totalCost(opts.base_cost, opts.level, buyN);
  const can = !locked && !maxed && cash >= cost;
  const btn = el("button", { className: "buy" });
  btn.appendChild(document.createTextNode(maxed ? "MAX" : `×${buyN}`));
  if (!maxed && !locked) {
    const costSpan = el("span", { className: "cost", textContent: `$${fmt(cost)}` });
    btn.appendChild(costSpan);
  }
  if (!can || maxed) btn.disabled = true;
  btn.onclick = async (e) => {
    e.stopPropagation();
    if (!can) {
      hapticNotify("error");
      toast("Не хватает $ или заблокировано", "error");
      return;
    }
    haptic("medium");
    btn.disabled = true;
    try {
      const r = await api.upgrade(opts.kind, opts.slot_id, buyN);
      if (!r.ok || !r.data) {
        hapticNotify("error");
        toast(translateError(r.error), "error");
        return;
      }
      hapticNotify("success");
      toast(`${opts.name} → ${r.data.new_level}`, "success");
      if (r.data.state) store.setState(r.data.state);
    } catch (err) {
      console.error(err);
    }
  };
  card.appendChild(btn);
  return card;
}

function translateError(err?: string): string {
  switch (err) {
    case "not_enough_cash": return "Недостаточно $";
    case "max_level": return "Уже максимальный уровень";
    case "locked": return "Ещё заблокировано";
    default: return err || "Ошибка";
  }
}
