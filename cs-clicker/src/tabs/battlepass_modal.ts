import { api, haptic, hapticNotify } from "../api";
import { store } from "../state";
import { ASSET_BASE, el, fmt } from "../util";
import { openModal, type ModalHandle } from "../ui/modal";
import { toast } from "../ui/toast";

let activeModal: ModalHandle | null = null;
let bpData: any = null;

export async function showBattlepassModal() {
  if (activeModal) return;

  const body = el("div", { className: "bp-body" });
  body.appendChild(el("div", { textContent: "Загружаем…", style: { textAlign: "center", color: "#94A3B8", padding: "16px" } }));

  activeModal = openModal({
    title: "BATTLE PASS",
    body,
    actions: [{ label: "Закрыть", onClick: () => activeModal?.close() }],
  });
  const obs = new MutationObserver(() => {
    if (!document.body.contains(activeModal!.root)) {
      obs.disconnect();
      activeModal = null;
    }
  });
  obs.observe(document.body, { childList: true });

  const r = await api.battlepass();
  if (!r.ok || !r.data) {
    body.innerHTML = "";
    body.appendChild(el("div", { textContent: r.error || "Ошибка загрузки", style: { color: "#FCA5A5", padding: "16px" } }));
    return;
  }
  bpData = r.data;
  render(body);
}

function render(body: HTMLElement) {
  body.innerHTML = "";
  if (!bpData) return;

  // Header — week info, level, progress
  const head = el("div", { className: "bp-head" });
  head.appendChild(el("div", { className: "bp-week", textContent: `Неделя начиная ${bpData.week_start}` }));
  const lvlRow = el("div", { className: "bp-lvl-row" });
  lvlRow.appendChild(el("div", { className: "bp-lvl-num", textContent: `LVL ${bpData.bp_level} / ${bpData.max_level}` }));
  lvlRow.appendChild(el("div", { className: "bp-lvl-xp", textContent: `${fmt(bpData.xp_into_level)} / ${fmt(bpData.xp_for_next || "0")} XP` }));
  head.appendChild(lvlRow);
  const bar = el("div", { className: "bp-progress-bar" });
  const next = Number(bpData.xp_for_next) || 1;
  const pct = Math.min(100, (Number(bpData.xp_into_level) / next) * 100);
  bar.appendChild(el("div", { className: "bp-progress-fill", style: { width: `${pct}%` } }));
  head.appendChild(bar);
  body.appendChild(head);

  // Premium toggle / buy button
  if (!bpData.premium) {
    const pBtn = el("button", { className: "bp-buy-premium" });
    pBtn.appendChild(el("span", { textContent: "Открыть Premium трек" }));
    pBtn.appendChild(el("span", { className: "small", textContent: `⌬ ${bpData.premium_cost_casecoins}` }));
    pBtn.onclick = async () => {
      const have = Number(store.state?.user.casecoins || "0");
      if (have < bpData.premium_cost_casecoins) {
        hapticNotify("error");
        toast(`Не хватает ⌬ (нужно ${bpData.premium_cost_casecoins})`, "error");
        return;
      }
      haptic("medium");
      const r = await api.bpBuyPremium();
      if (r.ok && r.data) {
        const data: any = r.data;
        if (data.state) store.setState(data.state);
        hapticNotify("success");
        toast("Premium открыт!", "success");
        // Refresh BP data
        const r2 = await api.battlepass();
        if (r2.ok && r2.data) { bpData = r2.data; render(body); }
      } else {
        hapticNotify("error");
        toast(r.error || "Ошибка", "error");
      }
    };
    body.appendChild(pBtn);
  } else {
    body.appendChild(el("div", { className: "bp-premium-on", textContent: "✓ Premium активен на этой неделе" }));
  }

  // Reward rows — each level shows free + premium reward + claim buttons
  const rewardsList = el("div", { className: "bp-rewards" });
  const claimedSet = new Set<number>(bpData.rewards_claimed || []);
  for (const reward of bpData.rewards) {
    const lvl = reward.level as number;
    const unlocked = bpData.bp_level >= lvl;
    const row = el("div", { className: `bp-row ${unlocked ? "unlocked" : "locked"}` });

    row.appendChild(el("div", { className: "bp-row-lvl", textContent: String(lvl) }));

    // Free track
    const freeKey = lvl * 2;
    const freeClaimed = claimedSet.has(freeKey);
    row.appendChild(renderRewardCell(reward.free, "free", lvl, unlocked, freeClaimed, body));

    // Premium track
    const premiumKey = lvl * 2 + 1;
    const premClaimed = claimedSet.has(premiumKey);
    row.appendChild(renderRewardCell(reward.premium, "premium", lvl, unlocked && bpData.premium, premClaimed, body));

    rewardsList.appendChild(row);
  }
  body.appendChild(rewardsList);
}

function renderRewardCell(
  reward: any, track: "free" | "premium", level: number,
  unlocked: boolean, claimed: boolean, bodyRoot: HTMLElement,
): HTMLElement {
  const cell = el("div", { className: `bp-cell bp-cell-${track}` });
  if (!reward) {
    cell.appendChild(el("div", { className: "bp-empty", textContent: "—" }));
    return cell;
  }

  const summary = summarizeReward(reward);
  cell.appendChild(el("div", { className: "bp-reward-text", textContent: summary }));

  const btn = el("button", { className: "bp-claim-btn" });
  if (claimed) {
    btn.textContent = "✓";
    btn.disabled = true;
    btn.classList.add("claimed");
  } else if (!unlocked) {
    btn.textContent = "🔒";
    btn.disabled = true;
  } else {
    btn.textContent = "Забрать";
  }
  btn.onclick = async () => {
    if (claimed || !unlocked) return;
    haptic("medium");
    btn.disabled = true;
    const r = await api.bpClaim(level, track);
    if (r.ok && r.data) {
      hapticNotify("success");
      const granted = (r.data as any).granted || {};
      const summary = Object.entries(granted).map(([k, v]) => `${k}: ${typeof v === "object" ? JSON.stringify(v) : v}`).join(", ");
      toast(`Получено: ${summary || "награда"}`, "success", 3000);
      if ((r.data as any).state) store.setState((r.data as any).state);
      // Refresh BP
      const r2 = await api.battlepass();
      if (r2.ok && r2.data) { bpData = r2.data; render(bodyRoot); }
    } else {
      hapticNotify("error");
      toast(r.error || "Ошибка", "error");
      btn.disabled = false;
    }
  };
  cell.appendChild(btn);
  return cell;
}

function summarizeReward(reward: any): string {
  const parts: string[] = [];
  if (reward.cash) parts.push(`$${fmt(reward.cash)}`);
  if (reward.casecoins) parts.push(`⌬ ${reward.casecoins}`);
  if (reward.glory) parts.push(`★ ${reward.glory}`);
  if (reward.chest) parts.push(`📦 ${reward.chest}`);
  if (reward.artifact_rarity) parts.push(`🎲 ${reward.artifact_rarity}`);
  if (reward.resources) {
    for (const [k, v] of Object.entries(reward.resources)) parts.push(`${k}:${v}`);
  }
  if (reward.exclusive_artifact_weekly) parts.push("Эксклюзив");
  return parts.join(" + ") || "—";
}
