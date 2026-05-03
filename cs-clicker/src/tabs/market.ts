import { api, haptic, hapticNotify } from "../api";
import { store } from "../state";
import { ASSET_BASE, el, fmt } from "../util";
import { toast } from "../ui/toast";
import { openModal, type ModalHandle } from "../ui/modal";

let root: HTMLElement | null = null;
let activeSubTab: "browse" | "mine" | "history" = "browse";
let lotsCache: any[] = [];
let myLotsCache: any[] = [];
let historyCache: any[] = [];
let lastFetched: { browse?: number; mine?: number; history?: number } = {};

export function mountMarketTab(parent: HTMLElement): HTMLElement {
  root = el("div", { className: "tab-page", dataset: { tab: "market" } });
  parent.appendChild(root);
  store.subscribe(render);
  render();
  return root;
}

async function loadCurrentTab(force = false) {
  if (!store.state) return;
  const now = Date.now();
  if (activeSubTab === "browse") {
    if (!force && lastFetched.browse && now - lastFetched.browse < 5000) return;
    const r = await api.marketLots();
    if (r.ok && r.data) { lotsCache = r.data; lastFetched.browse = now; }
  } else if (activeSubTab === "mine") {
    const r = await api.marketMyLots();
    if (r.ok && r.data) { myLotsCache = r.data; lastFetched.mine = now; }
  } else if (activeSubTab === "history") {
    const r = await api.marketHistory();
    if (r.ok && r.data) { historyCache = r.data; lastFetched.history = now; }
  }
  render();
}

function render() {
  if (!root || !store.state || !store.config) return;
  if (store.activeTab !== "market" && root.dataset.lastRender) return;

  const oldContent = root.querySelector(".tab-page-content") as HTMLElement | null;
  const savedScroll = oldContent ? oldContent.scrollTop : 0;

  root.innerHTML = "";
  const content = el("div", { className: "tab-page-content" });

  content.appendChild(el("div", { className: "tab-title", textContent: "🛒 МАРКЕТ P2P" }));
  content.appendChild(el("div", { className: "tab-subtitle", textContent: "Обмен ресурсами и артефактами с другими игроками. Лот живёт 48ч." }));

  // Sub-tabs
  const tabs = el("div", { className: "lb-tabs" });
  for (const [id, label] of [["browse","🌐 Смотреть"], ["mine","📦 Мои лоты"], ["history","📜 История"]] as [typeof activeSubTab, string][]) {
    const t = el("div", { className: `lb-tab ${id === activeSubTab ? "active" : ""}`, dataset: { sub: id }, textContent: label });
    t.onclick = () => {
      activeSubTab = id;
      void loadCurrentTab(true);
    };
    tabs.appendChild(t);
  }
  content.appendChild(tabs);

  // Create button
  const createBtn = el("button", { className: "market-create-btn", textContent: "+ ВЫСТАВИТЬ ЛОТ" });
  createBtn.onclick = () => { haptic("light"); openCreateLotWizard(); };
  content.appendChild(createBtn);

  // Active sub-tab content
  if (activeSubTab === "browse") content.appendChild(renderBrowse());
  else if (activeSubTab === "mine") content.appendChild(renderMine());
  else content.appendChild(renderHistory());

  root.appendChild(content);
  root.dataset.lastRender = String(Date.now());
  if (savedScroll > 0) requestAnimationFrame(() => { content.scrollTop = savedScroll; });

  if (!lastFetched[activeSubTab]) void loadCurrentTab();
}

function renderBrowse(): HTMLElement {
  const wrap = el("div", { className: "market-list" });
  if (lotsCache.length === 0) {
    wrap.appendChild(el("div", { className: "market-empty", textContent: "Лотов пока нет." }));
    return wrap;
  }
  for (const lot of lotsCache) wrap.appendChild(renderLotCard(lot, "browse"));
  return wrap;
}

function renderMine(): HTMLElement {
  const wrap = el("div", { className: "market-list" });
  if (myLotsCache.length === 0) {
    wrap.appendChild(el("div", { className: "market-empty", textContent: "У тебя нет лотов." }));
    return wrap;
  }
  for (const lot of myLotsCache) wrap.appendChild(renderLotCard(lot, "mine"));
  return wrap;
}

function renderHistory(): HTMLElement {
  const wrap = el("div", { className: "market-list" });
  if (historyCache.length === 0) {
    wrap.appendChild(el("div", { className: "market-empty", textContent: "История пуста." }));
    return wrap;
  }
  for (const lot of historyCache) wrap.appendChild(renderLotCard(lot, "history"));
  return wrap;
}

function renderLotCard(lot: any, mode: "browse" | "mine" | "history"): HTMLElement {
  const card = el("div", { className: `market-lot status-${lot.status}` });

  const head = el("div", { className: "market-lot-head" });
  head.appendChild(el("div", { className: "market-lot-seller", textContent: `от ${lot.seller_name || "Player"}` }));
  if (lot.expires_at && lot.status === "active") {
    const remaining = Math.max(0, new Date(lot.expires_at).getTime() - Date.now());
    const hours = Math.floor(remaining / 3600000);
    const mins = Math.floor((remaining % 3600000) / 60000);
    head.appendChild(el("div", { className: "market-lot-time", textContent: `⏱ ${hours}ч ${mins}м` }));
  } else if (lot.status !== "active") {
    head.appendChild(el("div", { className: "market-lot-status", textContent: lot.status }));
  }
  card.appendChild(head);

  const trade = el("div", { className: "market-trade" });
  trade.appendChild(renderSide("ОТДАЁТ", lot.offer_kind, lot.offer_id, lot.offer_amount, lot.offer_payload));
  trade.appendChild(el("div", { className: "market-arrow", textContent: "↔" }));
  trade.appendChild(renderSide("ХОЧЕТ", lot.ask_kind, lot.ask_id, lot.ask_amount));
  card.appendChild(trade);

  if (mode === "browse" && lot.status === "active") {
    const buyBtn = el("button", { className: "market-buy-btn", textContent: "КУПИТЬ" });
    buyBtn.onclick = async () => {
      haptic("medium");
      buyBtn.disabled = true;
      const r = await api.marketAccept(lot.id);
      if (r.ok) {
        hapticNotify("success");
        toast("Лот куплен!", "success");
        await loadCurrentTab(true);
        // refresh game state
        const st = await api.state();
        if (st.ok && st.data) {
          const data: any = st.data;
          store.setState(data.state ?? data);
        }
      } else {
        hapticNotify("error");
        toast(translateMarketError(r.error), "error");
        buyBtn.disabled = false;
      }
    };
    card.appendChild(buyBtn);
  } else if (mode === "mine" && lot.status === "active") {
    const cancelBtn = el("button", { className: "market-cancel-btn", textContent: "ОТМЕНИТЬ" });
    cancelBtn.onclick = async () => {
      haptic("medium");
      cancelBtn.disabled = true;
      const r = await api.marketCancel(lot.id);
      if (r.ok) {
        hapticNotify("success");
        toast("Лот снят, предмет вернулся", "info");
        await loadCurrentTab(true);
        const st = await api.state();
        if (st.ok && st.data) {
          const data: any = st.data;
          store.setState(data.state ?? data);
        }
      } else {
        hapticNotify("error");
        toast(r.error || "Ошибка", "error");
        cancelBtn.disabled = false;
      }
    };
    card.appendChild(cancelBtn);
  }

  return card;
}

function renderSide(label: string, kind: string, id: string | null, amount: string, payload?: any): HTMLElement {
  const side = el("div", { className: "market-side" });
  side.appendChild(el("div", { className: "market-side-label", textContent: label }));
  side.appendChild(el("div", { className: "market-side-content", innerHTML: describeAsset(kind, id, amount, payload) }));
  return side;
}

function describeAsset(kind: string, id: string | null, amount: string, payload?: any): string {
  const num = fmt(amount);
  if (kind === "cash") return `💵 $${num}`;
  if (kind === "casecoins") return `⌬ ${num}`;
  if (kind === "resource") {
    const meta = store.config?.resources_meta[id || ""];
    return `${meta?.emoji || ""} ${meta?.name || id} ×${num}`;
  }
  if (kind === "artifact") {
    if (payload?.item_id) {
      const short = String(payload.item_id).replace(/^(artifact_|mythic_)/, "");
      const def = store.config?.artifacts.find((a) => a.id === short)
        || store.config?.mythics.find((m) => m.id === short);
      if (def) return `🎲 ${def.name}`;
    }
    return `🎲 артефакт #${id}`;
  }
  return `${kind} ${num}`;
}

function translateMarketError(err?: string): string {
  switch (err) {
    case "lot_not_found": return "Лот пропал";
    case "lot_inactive": return "Лот закрыт";
    case "expired": return "Лот истёк";
    case "self_buy": return "Это твой лот";
    case "not_enough_cash": return "Не хватает $";
    case "not_enough_casecoins": return "Не хватает ⌬";
    case "not_enough_resource": return "Не хватает ресурса";
    case "ask_artifact_not_owned": return "У тебя нет такого артефакта";
    default: return err || "Ошибка";
  }
}

// ----------- CREATE LOT WIZARD -----------

let wizardModal: ModalHandle | null = null;
let wizardOffer: { kind: string; id: string | null; amount: number } = { kind: "resource", id: "energy", amount: 10 };
let wizardAsk: { kind: string; id: string | null; amount: number } = { kind: "cash", id: null, amount: 1000 };

function openCreateLotWizard() {
  if (!store.state || !store.config) return;
  if (wizardModal) return;
  const body = el("div", { className: "lot-wizard" });

  const offerSection = el("div", { className: "lot-wizard-section" });
  offerSection.appendChild(el("div", { className: "lot-wizard-label", textContent: "ОТДАЮ" }));
  offerSection.appendChild(buildAssetPicker("offer"));
  body.appendChild(offerSection);

  const askSection = el("div", { className: "lot-wizard-section" });
  askSection.appendChild(el("div", { className: "lot-wizard-label", textContent: "ХОЧУ" }));
  askSection.appendChild(buildAssetPicker("ask"));
  body.appendChild(askSection);

  body.appendChild(el("div", { className: "lot-wizard-fee", textContent: "Комиссия 5% (минимум $100) сжигается." }));

  wizardModal = openModal({
    title: "ВЫСТАВИТЬ ЛОТ",
    body,
    actions: [
      { label: "Отмена", onClick: () => { wizardModal?.close(); wizardModal = null; } },
      { label: "Выставить", className: "primary", onClick: submit },
    ],
  });
  const obs = new MutationObserver(() => {
    if (!document.body.contains(wizardModal!.root)) {
      obs.disconnect();
      wizardModal = null;
    }
  });
  obs.observe(document.body, { childList: true });

  async function submit() {
    if (!store.state) return;
    haptic("medium");
    const r = await api.marketCreate(
      wizardOffer.kind, wizardOffer.id, wizardOffer.amount,
      wizardAsk.kind, wizardAsk.id, wizardAsk.amount,
    );
    if (r.ok) {
      hapticNotify("success");
      toast("Лот выставлен!", "success");
      wizardModal?.close();
      wizardModal = null;
      await loadCurrentTab(true);
      const st = await api.state();
      if (st.ok && st.data) {
        const data: any = st.data;
        store.setState(data.state ?? data);
      }
    } else {
      hapticNotify("error");
      toast(translateMarketError(r.error) || "Ошибка", "error");
    }
  }
}

function buildAssetPicker(side: "offer" | "ask"): HTMLElement {
  const target = side === "offer" ? wizardOffer : wizardAsk;
  const wrap = el("div", { className: "asset-picker" });

  // Kind tabs
  const kindRow = el("div", { className: "asset-kind-row" });
  for (const k of ["resource", "cash", "casecoins"]) {
    const t = el("div", {
      className: `asset-kind-tab ${target.kind === k ? "active" : ""}`,
      textContent: k === "resource" ? "Ресурс" : k === "cash" ? "$" : "⌬",
    });
    t.onclick = () => {
      target.kind = k;
      if (k === "resource") target.id = "energy";
      else target.id = null;
      wrap.replaceWith(buildAssetPicker(side));
    };
    kindRow.appendChild(t);
  }
  wrap.appendChild(kindRow);

  // Resource selector (only when kind=resource)
  if (target.kind === "resource") {
    const resRow = el("div", { className: "asset-res-row" });
    for (const [resType, meta] of Object.entries(store.config!.resources_meta)) {
      const chip = el("div", {
        className: `asset-res-chip ${target.id === resType ? "active" : ""}`,
        textContent: `${meta.emoji} ${meta.name}`,
      });
      chip.onclick = () => {
        target.id = resType;
        wrap.replaceWith(buildAssetPicker(side));
      };
      resRow.appendChild(chip);
    }
    wrap.appendChild(resRow);
  }

  // Amount input
  const amountRow = el("div", { className: "asset-amount-row" });
  const input = el("input", { type: "number", value: String(target.amount), min: "1" }) as HTMLInputElement;
  input.onchange = () => { target.amount = Math.max(1, Math.floor(Number(input.value) || 1)); };
  input.oninput = () => { target.amount = Math.max(1, Math.floor(Number(input.value) || 1)); };
  amountRow.appendChild(el("label", { textContent: "Кол-во:" }));
  amountRow.appendChild(input);
  wrap.appendChild(amountRow);

  return wrap;
}
