import { api } from "./api";
import { store } from "./state";
import { mountTopbar } from "./ui/topbar";
import { mountBottomNav } from "./ui/bottomnav";
import { mountClickerTab } from "./tabs/clicker";
import { mountInventoryTab } from "./tabs/inventory";
import { mountMoreTab } from "./tabs/more";
import { mountBusinessTab } from "./tabs/business";
import { mountMarketTab } from "./tabs/market";
import { el } from "./util";
import { toast } from "./ui/toast";

// Telegram WebApp init
const tg = window.Telegram?.WebApp;
if (tg) {
  try {
    tg.ready();
    tg.expand();
    if (typeof (tg as any).disableVerticalSwipes === "function") (tg as any).disableVerticalSwipes();
    if (typeof (tg as any).setBackgroundColor === "function") (tg as any).setBackgroundColor("#0B1426");
    if (typeof (tg as any).setHeaderColor === "function") (tg as any).setHeaderColor("#0B1426");
  } catch {}
}

// Block iOS rubber-band on the canvas.
document.addEventListener("touchmove", (e) => {
  const target = e.target as HTMLElement;
  if (target.closest(".modal, .modal-overlay, .tab-page-content")) return;
  if (e.touches.length === 1 && e.cancelable) e.preventDefault();
}, { passive: false });

async function boot() {
  const fillEl = document.getElementById("loading-fill") as HTMLDivElement | null;
  const hintEl = document.getElementById("loading-hint") as HTMLDivElement | null;
  const setProg = (p: number, hint?: string) => {
    if (fillEl) fillEl.style.width = `${Math.max(8, Math.min(100, p))}%`;
    if (hint && hintEl) hintEl.textContent = hint;
  };

  setProg(20, "Загружаем конфиг…");
  const cfg = await api.config();
  if (!cfg.ok || !cfg.data) {
    if (hintEl) { hintEl.textContent = "Ошибка соединения. Перезайди."; hintEl.style.color = "#FCA5A5"; }
    return;
  }
  store.setConfig(cfg.data);

  setProg(60, "Получаем стейт…");
  const st = await api.state();
  if (!st.ok || !st.data) {
    if (hintEl) { hintEl.textContent = "Не удалось авторизоваться."; hintEl.style.color = "#FCA5A5"; }
    return;
  }
  // /state returns either {state: ...} (wrapped) or the snap directly. game.py wraps under state.
  const stateData: any = st.data;
  const snap = stateData.state ?? stateData;
  store.setState(snap);

  setProg(85, "Готовим UI…");
  buildUI();

  setProg(100, "Поехали");
  setTimeout(() => {
    const overlay = document.getElementById("loading-overlay");
    if (overlay) {
      overlay.classList.add("hidden");
      setTimeout(() => overlay.remove(), 500);
    }
  }, 200);

  // Polling: refresh state periodically (covers auto-DPS accumulation, idle progress).
  setInterval(async () => {
    try {
      const r = await api.state();
      if (r.ok && r.data) {
        const data: any = r.data;
        const snap = data.state ?? data;
        store.setState(snap);
      }
    } catch {}
  }, 15000);
}

function buildUI() {
  const app = document.getElementById("app")!;
  app.innerHTML = "";

  mountTopbar(app);

  const tabArea = el("div", { className: "tab-area" });
  app.appendChild(tabArea);

  mountClickerTab(tabArea);
  mountBusinessTab(tabArea);
  mountInventoryTab(tabArea);
  mountMarketTab(tabArea);
  mountMoreTab(tabArea);

  mountBottomNav(app);

  // Switch tab visibility.
  const updateTabs = () => {
    Array.from(tabArea.children).forEach((node) => {
      const elem = node as HTMLElement;
      elem.classList.toggle("active", elem.dataset.tab === store.activeTab);
    });
  };
  store.subscribe(updateTabs);
  updateTabs();
}

void boot();
