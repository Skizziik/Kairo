import { api, haptic, hapticNotify } from "../api";
import { store } from "../state";
import type { PrestigeNodeDef } from "../types";
import { el, fmt } from "../util";
import { openModal, type ModalHandle } from "../ui/modal";
import { toast } from "../ui/toast";

let activeModal: ModalHandle | null = null;
let unsubscribe: (() => void) | null = null;

export function showPrestigeModal() {
  if (activeModal) return;
  if (!store.config) {
    toast("Конфиг не загружен", "error");
    return;
  }

  const body = el("div");

  activeModal = openModal({
    title: "★ ПРЕСТИЖ",
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

  unsubscribe = store.subscribe(renderBody);
  renderBody();

  function renderBody() {
    if (!store.state || !store.config) return;
    body.innerHTML = "";

    const u = store.state.user;
    const canPrestige = u.max_level >= 20;
    const projectedGlory = canPrestige ? Math.max(1, Math.floor(Math.pow(u.max_level / 20, 1.5))) : 0;

    // Top: do-prestige card
    const card = el("div", { className: "prestige-card" });
    card.appendChild(el("div", { className: "title", textContent: `★ Сделать престиж #${u.prestige_count + 1}` }));
    card.appendChild(el("div", {
      className: "body",
      innerHTML: canPrestige
        ? `Сбросишь уровень и апгрейды → получишь <b>${projectedGlory}★</b> Славы.<br/>Артефакты, casecoins, glory и сундуки <b>сохранятся</b>.<br/>+1 слот артефакта (сейчас: ${u.artifact_slots}/6).`
        : `Доступно с уровня 20. Сейчас твой максимум: <b>${u.max_level}</b>.`,
    }));
    const pBtn = el("button", { textContent: canPrestige ? `СДЕЛАТЬ ПРЕСТИЖ +${projectedGlory}★` : "ЗАБЛОКИРОВАНО" });
    if (!canPrestige) pBtn.disabled = true;
    pBtn.onclick = doPrestige;
    card.appendChild(pBtn);
    body.appendChild(card);

    // Tree
    body.appendChild(el("div", {
      className: "upg-section-title",
      textContent: `ДРЕВО ПРЕСТИЖА (${u.glory}★)`,
      style: { marginTop: "16px" },
    }));
    if (Number(u.glory) === 0 && u.prestige_count === 0) {
      body.appendChild(el("div", {
        textContent: "Сначала сделай первый престиж чтобы получить ★ Славу.",
        style: { fontSize: "12px", color: "#94A3B8", marginBottom: "10px", padding: "0 4px", lineHeight: "1.4" },
      }));
    }
    const ptList = el("div", { className: "pt-grid" });
    const owned = store.state.prestige_nodes || {};
    const sorted = [...store.config.prestige_tree].sort((a, b) => a.tier - b.tier);
    for (const node of sorted) {
      ptList.appendChild(renderNode(node, owned[node.id] || 0, Number(u.glory)));
    }
    body.appendChild(ptList);
  }

  async function doPrestige() {
    if (!confirm("Сбросить прогресс ради ★ Славы?")) return;
    haptic("heavy");
    const r = await api.prestige();
    if (r.ok && r.data) {
      hapticNotify("success");
      toast(`+${r.data.glory_gained}★ Славы`, "success", 3000);
      store.setState(r.data.state);
    } else {
      hapticNotify("error");
      toast(r.error || "Ошибка", "error");
    }
  }

  function renderNode(node: PrestigeNodeDef, owned: number, glory: number): HTMLElement {
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
      btn.textContent = `Купить за ${cost}★`;
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
        toast(r.error || "Ошибка", "error");
      }
    };
    card.appendChild(btn);

    return card;
  }
}
