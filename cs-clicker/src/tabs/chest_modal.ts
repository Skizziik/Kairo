import { api, haptic, hapticNotify } from "../api";
import { store } from "../state";
import type { OpenChestResult } from "../types";
import { ASSET_BASE, el, fmt, rarityColor, rarityLabel } from "../util";
import { openModal, type ModalHandle } from "../ui/modal";
import { toast } from "../ui/toast";

export async function showChestRollModal(invId: number): Promise<void> {
  if (!store.config) return;
  const chest = store.state?.inventory.find((x) => x.id === invId && x.kind === "chest");
  if (!chest) return;

  const tier = chest.rarity || "common";
  const def = store.config.chests[tier];
  const body = el("div", { className: "chest-open" });
  body.appendChild(el("div", { className: "glow-bg" }));
  body.appendChild(el("img", { className: "chest-pic", src: `${ASSET_BASE}/${def?.icon || "chests/01.png"}`, alt: tier }));
  body.appendChild(el("div", { textContent: `${rarityLabel(tier).toUpperCase()} CHEST`, style: { fontWeight: "800", marginTop: "12px", color: rarityColor(tier), letterSpacing: "2px", fontSize: "16px" } }));

  let modal: ModalHandle = openModal({
    title: "СУНДУК",
    body,
    actions: [
      { label: "ОТКРЫТЬ", className: "primary", onClick: doOpen },
      { label: "Позже",   onClick: () => modal.close() },
    ],
    closeOnBackdrop: false,
  });

  async function doOpen() {
    haptic("heavy");
    modal.setActions([{ label: "Открываем…", onClick: () => {} }]);
    try {
      const r = await api.openChest(invId);
      if (!r.ok || !r.data) {
        hapticNotify("error");
        toast(r.error || "Ошибка открытия", "error");
        modal.close();
        return;
      }
      hapticNotify("success");
      renderResult(modal, r.data);
      if (r.data.state) store.setState(r.data.state);
    } catch (e) {
      console.error(e);
      modal.close();
    }
  }
}

function renderResult(modal: ModalHandle, data: OpenChestResult) {
  modal.body.innerHTML = "";

  const head = el("div", {
    textContent: `+ ДРОП`,
    style: { fontSize: "14px", color: "#94A3B8", letterSpacing: "2px", textAlign: "center", marginBottom: "12px" },
  });
  modal.body.appendChild(head);

  const grid = el("div", { className: "chest-rolls" });

  // Cash
  if (Number(data.cash) > 0) {
    const item = el("div", { className: "roll-item", style: { animationDelay: "0ms" } });
    item.appendChild(el("img", { src: `${ASSET_BASE}/ui/02.png` }));
    item.appendChild(el("div", { className: "name", textContent: `$${fmt(data.cash)}` }));
    item.appendChild(el("div", { className: "label", textContent: "Cash" }));
    grid.appendChild(item);
  }

  if (data.casecoins > 0) {
    const item = el("div", { className: "roll-item", style: { animationDelay: "100ms" } });
    item.appendChild(el("img", { src: `${ASSET_BASE}/resources/07.png` }));
    item.appendChild(el("div", { className: "name", textContent: `⌬ ${data.casecoins}` }));
    item.appendChild(el("div", { className: "label", textContent: "Casecoins" }));
    grid.appendChild(item);
  }

  if (data.artifact) {
    const item = el("div", { className: "roll-item", style: { animationDelay: "200ms", borderColor: rarityColor(data.artifact.rarity) } });
    item.appendChild(el("img", { src: `${ASSET_BASE}/${data.artifact.icon}` }));
    item.appendChild(el("div", { className: "name", textContent: data.artifact.name, style: { color: rarityColor(data.artifact.rarity) } }));
    item.appendChild(el("div", { className: "label", textContent: rarityLabel(data.artifact.rarity) }));
    grid.appendChild(item);
  }

  if (data.mythic) {
    const item = el("div", { className: "roll-item", style: { animationDelay: "300ms", borderColor: rarityColor("mythic") } });
    item.appendChild(el("img", { src: `${ASSET_BASE}/${data.mythic.icon}` }));
    item.appendChild(el("div", { className: "name", textContent: data.mythic.name, style: { color: rarityColor("mythic") } }));
    item.appendChild(el("div", { className: "label", textContent: "MYTHIC" }));
    grid.appendChild(item);
  }

  // Resource drops
  if (data.resources) {
    let delay = 400;
    for (const [resType, amount] of Object.entries(data.resources)) {
      const num = Number(amount);
      if (num <= 0) continue;
      const meta = store.config?.resources_meta[resType];
      const item = el("div", { className: "roll-item", style: { animationDelay: `${delay}ms`, borderColor: meta?.color || "#475569" } });
      const iconPath = meta?.icon || `resources/01.png`;
      item.appendChild(el("img", { src: `${ASSET_BASE}/${iconPath}` }));
      item.appendChild(el("div", { className: "name", textContent: `+${fmt(num)}`, style: { color: meta?.color || "#FFF" } }));
      item.appendChild(el("div", { className: "label", textContent: meta?.name || resType }));
      grid.appendChild(item);
      delay += 100;
    }
  }

  modal.body.appendChild(grid);
  modal.setActions([{ label: "ОК", className: "primary", onClick: () => modal.close() }]);
}
