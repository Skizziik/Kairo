import { store } from "../state";
import { ASSET_BASE, el } from "../util";
import { haptic } from "../api";

const TABS = [
  { id: "clicker",   icon: `${ASSET_BASE}/ui/01.png`, label: "КЛИКЕР" },
  { id: "business",  icon: `${ASSET_BASE}/ui/02.png`, label: "БИЗНЕС" },
  { id: "inventory", icon: `${ASSET_BASE}/ui/03.png`, label: "ИНВЕНТАРЬ" },
  { id: "market",    icon: `${ASSET_BASE}/ui/04.png`, label: "МАРКЕТ" },
  { id: "more",      icon: `${ASSET_BASE}/ui/05.png`, label: "ЕЩЁ" },
] as const;

let root: HTMLElement | null = null;

export function mountBottomNav(parent: HTMLElement) {
  root = el("div", { className: "bottomnav" });
  for (const t of TABS) {
    const btn = el("button", { className: "nav-btn", dataset: { tab: t.id } }, [
      el("img", { src: t.icon, alt: t.label }),
      el("div", { className: "label", textContent: t.label }),
    ]);
    btn.onclick = () => {
      if (store.activeTab === t.id) return;
      haptic("light");
      store.setTab(t.id);
    };
    root.appendChild(btn);
  }
  parent.appendChild(root);
  store.subscribe(render);
  render();
}

function render() {
  if (!root) return;
  for (const btn of Array.from(root.children) as HTMLElement[]) {
    btn.classList.toggle("active", btn.dataset.tab === store.activeTab);
  }

  // badge for unopened chests in inventory
  const invBtn = root.querySelector('[data-tab="inventory"]') as HTMLElement | null;
  if (invBtn) {
    const old = invBtn.querySelector(".badge");
    if (old) old.remove();
    const chests = (store.state?.inventory || []).filter((i) => i.kind === "chest");
    if (chests.length > 0) {
      const b = el("div", { className: "badge", textContent: String(chests.length) });
      invBtn.appendChild(b);
    }
  }
}
