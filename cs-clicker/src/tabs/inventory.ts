import { api, haptic, hapticNotify } from "../api";
import { store } from "../state";
import type { ArtifactDef, InventoryItem, MythicDef } from "../types";
import { ASSET_BASE, el, fmt, rarityColor, rarityLabel } from "../util";
import { openModal } from "../ui/modal";
import { toast } from "../ui/toast";
import { showChestRollModal } from "./chest_modal";

let root: HTMLElement | null = null;

export function mountInventoryTab(parent: HTMLElement): HTMLElement {
  root = el("div", { className: "tab-page", dataset: { tab: "inventory" } });
  parent.appendChild(root);
  store.subscribe(render);
  render();
  return root;
}

function render() {
  if (!root || !store.state || !store.config) return;
  root.innerHTML = "";
  const content = el("div", { className: "tab-page-content" });

  content.appendChild(el("div", { className: "tab-title", textContent: "🎒 ИНВЕНТАРЬ" }));
  content.appendChild(el("div", { className: "tab-subtitle", textContent: "Артефакты, сундуки, mythic-предметы. Тапни — действие." }));

  // Equipped slots row
  const slots = store.state.user.artifact_slots;
  const eqRow = el("div", { className: "equipped-row" });
  for (let i = 0; i < 6; i++) {
    const slot = el("div", { className: `eq-slot ${i >= slots ? "locked" : ""}` });
    const equippedItem = store.state.inventory.find((x) => x.equipped_slot === i);
    if (equippedItem) {
      slot.classList.add("has-item");
      const def = lookupArtifactDef(equippedItem);
      if (def) {
        slot.appendChild(el("img", { src: `${ASSET_BASE}/${def.icon}`, alt: def.name }));
      }
      slot.style.borderColor = rarityColor(equippedItem.rarity);
      slot.onclick = () => {
        haptic("light");
        unequipItem(equippedItem.id);
      };
    } else if (i < slots) {
      slot.appendChild(el("span", { textContent: "+", style: { color: "#475569", fontSize: "20px" } }));
    }
    slot.appendChild(el("div", { className: "slot-num", textContent: String(i + 1) }));
    eqRow.appendChild(slot);
  }
  content.appendChild(eqRow);

  // Sections: chests, artifacts, mythics
  const chests = store.state.inventory.filter((x) => x.kind === "chest");
  const artifacts = store.state.inventory.filter((x) => x.kind === "artifact" || x.kind === "mythic");

  if (chests.length > 0) {
    content.appendChild(el("div", { className: "upg-section-title", textContent: `СУНДУКИ (${chests.length})` }));
    const grid = el("div", { className: "inv-grid" });
    for (const c of chests) {
      const def = store.config.chests[c.rarity || "common"];
      const card = el("div", { className: "inv-card" });
      card.appendChild(el("div", { className: "rarity-tag", textContent: rarityLabel(c.rarity), style: { color: rarityColor(c.rarity) } }));
      const imgWrap = el("div", { className: "img-wrap" });
      imgWrap.appendChild(el("img", { src: `${ASSET_BASE}/${def?.icon || "chests/01.png"}`, alt: def?.name || c.rarity || "chest" }));
      card.appendChild(imgWrap);
      card.appendChild(el("div", { className: "name", textContent: def?.name || rarityLabel(c.rarity) }));
      card.onclick = () => { haptic("medium"); showChestRollModal(c.id); };
      grid.appendChild(card);
    }
    content.appendChild(grid);
  }

  if (artifacts.length > 0) {
    content.appendChild(el("div", { className: "upg-section-title", textContent: `АРТЕФАКТЫ (${artifacts.length})`, style: { marginTop: "20px" } }));
    const grid = el("div", { className: "inv-grid" });
    for (const a of artifacts) {
      const def = lookupArtifactDef(a);
      if (!def) continue;
      const isEquipped = a.equipped_slot !== null && a.equipped_slot !== undefined;
      const card = el("div", { className: `inv-card ${isEquipped ? "equipped" : ""}` });
      card.style.borderColor = isEquipped ? rarityColor(a.rarity) : "";
      card.appendChild(el("div", { className: "rarity-tag", textContent: rarityLabel(a.rarity), style: { color: rarityColor(a.rarity) } }));
      const imgWrap = el("div", { className: "img-wrap" });
      imgWrap.appendChild(el("img", { src: `${ASSET_BASE}/${def.icon}`, alt: def.name }));
      card.appendChild(imgWrap);
      card.appendChild(el("div", { className: "name", textContent: def.name }));
      card.onclick = () => {
        haptic("light");
        if (isEquipped) {
          unequipItem(a.id);
        } else {
          chooseSlot(a, def);
        }
      };
      grid.appendChild(card);
    }
    content.appendChild(grid);
  }

  if (chests.length === 0 && artifacts.length === 0) {
    const empty = el("div", { className: "locked-tab" });
    empty.appendChild(el("div", { className: "icon", textContent: "📦" }));
    empty.appendChild(el("div", { className: "msg", textContent: "Инвентарь пуст. Бей боссов чтобы выбить сундуки и артефакты." }));
    content.appendChild(empty);
  }

  root.appendChild(content);
}

function lookupArtifactDef(item: InventoryItem): ArtifactDef | MythicDef | null {
  if (!store.config) return null;
  if (item.kind === "artifact") {
    const short = item.item_id.replace("artifact_", "");
    return store.config.artifacts.find((a) => a.id === short) || null;
  }
  if (item.kind === "mythic") {
    const short = item.item_id.replace("mythic_", "");
    return store.config.mythics.find((m) => m.id === short) || null;
  }
  return null;
}

function chooseSlot(a: InventoryItem, def: ArtifactDef | MythicDef) {
  if (!store.state) return;
  const slots = store.state.user.artifact_slots;
  const body = el("div");
  body.appendChild(el("div", { textContent: `Куда экипировать "${def.name}"?`, style: { textAlign: "center", marginBottom: "12px", fontSize: "13px", color: "#94A3B8" } }));

  const row = el("div", { className: "equipped-row", style: { gridTemplateColumns: `repeat(${slots}, 1fr)` } });
  for (let i = 0; i < slots; i++) {
    const slot = el("div", { className: "eq-slot" });
    const cur = store.state.inventory.find((x) => x.equipped_slot === i);
    if (cur) {
      const curDef = lookupArtifactDef(cur);
      if (curDef) slot.appendChild(el("img", { src: `${ASSET_BASE}/${curDef.icon}` }));
      slot.style.borderColor = rarityColor(cur.rarity);
    }
    slot.appendChild(el("div", { className: "slot-num", textContent: String(i + 1) }));
    slot.onclick = async () => {
      haptic("medium");
      handle.close();
      const r = await api.equip(a.id, i);
      if (r.ok && r.data) {
        hapticNotify("success");
        toast(`Надет в слот ${i + 1}`, "success");
        store.setState(r.data.state);
      } else {
        hapticNotify("error");
        toast(r.error || "Ошибка", "error");
      }
    };
    row.appendChild(slot);
  }
  body.appendChild(row);

  const handle = openModal({
    title: "ВЫБЕРИ СЛОТ",
    body,
    actions: [{ label: "Отмена", onClick: () => handle.close() }],
  });
}

async function unequipItem(invId: number) {
  const r = await api.unequip(invId);
  if (r.ok && r.data) {
    hapticNotify("success");
    toast("Снято", "info");
    store.setState(r.data.state);
  }
}
