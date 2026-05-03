import { api, haptic, hapticNotify } from "../api";
import { store } from "../state";
import type { BusinessBranchDef } from "../types";
import { ASSET_BASE, el, fmt } from "../util";
import { openModal, type ModalHandle } from "../ui/modal";
import { toast } from "../ui/toast";

let activeModal: ModalHandle | null = null;
let unsubscribe: (() => void) | null = null;

const EFFECT_LABEL: Record<string, string> = {
  idle_pct: "+ idle производство",
  tap_pct: "+ тап-урон",
  all_yield_pct: "+ ко всему доходу бизнеса",
  offline_pct: "+ оффлайн прогрессия",
  crit_yield_pct: "Шанс крит-тапа",
  raid_def_pct: "Защита от рейдов",
  rare_drop_pct: "Шанс редких ресурсов",
};

export function showBusinessTreeModal(businessId: string) {
  if (activeModal) return;
  if (!store.config) {
    toast("Конфиг не загружен — обнови страницу", "error");
    return;
  }
  const branches = store.config.business_tree?.[businessId] || [];
  const bdef = store.config.businesses.find((b) => b.id === businessId);
  if (!bdef) {
    toast("Бизнес не найден", "error");
    return;
  }
  if (branches.length === 0) {
    toast("Дерево этого бизнеса не настроено", "error");
    return;
  }

  const body = el("div");
  const list = el("div", { className: "biz-branch-list" });
  body.appendChild(list);

  activeModal = openModal({
    title: `${bdef.name} — Дерево`,
    body,
    actions: [{ label: "Закрыть", onClick: () => activeModal?.close() }],
  });

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
    const bizState = (store.state.businesses || []).find((b) => b.id === businessId);
    const ownedLevels = bizState?.branches || {};
    for (const branch of branches) {
      list.appendChild(renderBranch(businessId, branch, ownedLevels[branch.id] || 0));
    }
  }
}

function renderBranch(businessId: string, branch: BusinessBranchDef, owned: number): HTMLElement {
  if (!store.state || !store.config) return el("div");
  const card = el("div", { className: "biz-branch-card" });
  const maxed = owned >= branch.max_level;

  const cashCost = Math.ceil(branch.base_cost * Math.pow(1.20, owned));
  const haveCash = Number(store.state.user.cash);

  let resCost = 0;
  let haveRes = 0;
  if (branch.cost_resource) {
    resCost = Math.ceil((branch.cost_per_level || 0) * Math.pow(1.20, owned));
    haveRes = Number(store.state.resources[branch.cost_resource] || "0");
  }

  const cashOk = haveCash >= cashCost;
  const resOk = !branch.cost_resource || haveRes >= resCost;
  const can = !maxed && cashOk && resOk;

  const head = el("div", { className: "biz-branch-head" });
  const imgWrap = el("div", { className: "biz-branch-img" });
  imgWrap.appendChild(el("img", { src: `${ASSET_BASE}/${branch.icon}`, alt: branch.name }));
  head.appendChild(imgWrap);

  const headInfo = el("div", { className: "biz-branch-info" });
  const titleRow = el("div", { className: "biz-branch-title-row" });
  titleRow.appendChild(el("span", { className: "biz-branch-name", textContent: branch.name }));
  titleRow.appendChild(el("span", { className: "biz-branch-lvl", textContent: `${owned}/${branch.max_level}` }));
  headInfo.appendChild(titleRow);

  const totalEffect = branch.per_level * owned;
  headInfo.appendChild(el("div", {
    className: "biz-branch-effect",
    textContent: `${EFFECT_LABEL[branch.effect] || branch.effect}: +${totalEffect}% (+${branch.per_level}%/ур)`,
  }));
  head.appendChild(headInfo);
  card.appendChild(head);

  const btn = el("button", { className: "biz-branch-buy" });
  if (maxed) {
    btn.textContent = "MAX";
    btn.disabled = true;
  } else {
    const top = el("div", { textContent: `Купить +1` });
    btn.appendChild(top);
    const costRow = el("div", { className: "small" });
    const cashSpan = el("span", { textContent: `$${fmt(cashCost)}`, style: { color: cashOk ? "#FFB800" : "#FCA5A5" } });
    costRow.appendChild(cashSpan);
    if (branch.cost_resource) {
      const meta = store.config.resources_meta[branch.cost_resource];
      const resSpan = el("span", {
        textContent: ` · ${meta?.emoji || ""}${fmt(resCost)}`,
        style: { color: resOk ? "#94A3B8" : "#FCA5A5" },
      });
      costRow.appendChild(resSpan);
    }
    btn.appendChild(costRow);
    if (!can) btn.disabled = true;
  }
  btn.onclick = async () => {
    if (!can || maxed) {
      hapticNotify("error");
      toast(!cashOk ? "Не хватает $" : !resOk ? "Не хватает ресурса" : "Нельзя", "error");
      return;
    }
    haptic("medium");
    btn.disabled = true;
    const r = await api.businessBranchBuy(businessId, branch.id);
    if (r.ok && r.data) {
      hapticNotify("success");
      toast(`${branch.name} → ${r.data.new_level}`, "success");
      if (r.data.state) store.setState(r.data.state);
    } else {
      hapticNotify("error");
      toast(r.error || "Ошибка", "error");
    }
  };
  card.appendChild(btn);

  return card;
}
