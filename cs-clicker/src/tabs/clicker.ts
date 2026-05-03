import { api, haptic, hapticNotify } from "../api";
import { store } from "../state";
import type { StateSnap, TapResult } from "../types";
import { ASSET_BASE, el, fmt, fmtTimer } from "../util";
import { showUpgradeModal } from "./upgrade_modal";
import { toast } from "../ui/toast";
import { showChestRollModal } from "./chest_modal";

let root: HTMLElement | null = null;
let bgEl: HTMLElement;
let levelLocEl: HTMLElement;
let levelNameEl: HTMLElement;
let timerFillEl: HTMLElement;
let timerTextEl: HTMLElement;
let hpFillEl: HTMLElement;
let hpTextEl: HTMLElement;
let stageEl: HTMLElement;
let enemyBubbleEl: HTMLElement;
let enemyImgEl: HTMLImageElement;
let bossBannerEl: HTMLElement;
let statClickEl: HTMLElement;
let statAutoEl: HTMLElement;
let statCritEl: HTMLElement;

let pendingTaps = 0;
let pendingTapsTimer: any = null;
let lastTapAt = 0;
let timerInterval: any = null;
let lastLevel = -1;
let isFlushingTaps = false;

export function mountClickerTab(parent: HTMLElement): HTMLElement {
  root = el("div", { className: "tab-page clicker-tab", dataset: { tab: "clicker" } });
  bgEl = el("div", { className: "clicker-bg" });
  root.appendChild(bgEl);

  const content = el("div", { className: "clicker-content" });

  const levelHeader = el("div", { className: "level-header" });
  levelLocEl = el("div", { className: "level-loc", textContent: "" });
  levelNameEl = el("div", { className: "level-name", textContent: "" });
  levelHeader.appendChild(levelLocEl);
  levelHeader.appendChild(levelNameEl);
  content.appendChild(levelHeader);

  // Level navigation row
  const levelNav = el("div", { className: "level-nav" });
  const prevBtn = el("button", { className: "level-arrow", textContent: "‹", dataset: { dir: "prev" } });
  const nextBtn = el("button", { className: "level-arrow", textContent: "›", dataset: { dir: "next" } });
  const navInfo = el("div", { className: "level-nav-info" });
  const navCur = el("div", { className: "level-nav-cur", textContent: "lvl 1" });
  const navMax = el("div", { className: "level-nav-max", textContent: "max 1" });
  navInfo.appendChild(navCur);
  navInfo.appendChild(navMax);
  prevBtn.onclick = () => navigateLevel(-1);
  nextBtn.onclick = () => navigateLevel(+1);
  levelNav.appendChild(prevBtn);
  levelNav.appendChild(navInfo);
  levelNav.appendChild(nextBtn);
  content.appendChild(levelNav);
  // store refs as data so we can update without re-querying
  (root as any)._navCur = navCur;
  (root as any)._navMax = navMax;
  (root as any)._prevBtn = prevBtn;
  (root as any)._nextBtn = nextBtn;

  const timerRow = el("div", { className: "timer-row" });
  const timerBar = el("div", { className: "timer-bar" });
  timerFillEl = el("div", { className: "timer-fill", style: { width: "100%" } });
  timerTextEl = el("div", { className: "timer-text", textContent: "0:00" });
  timerBar.appendChild(timerFillEl);
  timerRow.appendChild(timerBar);
  timerRow.appendChild(timerTextEl);
  content.appendChild(timerRow);

  const hpWrap = el("div", { className: "hp-bar-wrap" });
  const hpBar = el("div", { className: "hp-bar" });
  hpFillEl = el("div", { className: "hp-fill", style: { width: "100%" } });
  hpTextEl = el("div", { className: "hp-text", textContent: "" });
  hpBar.appendChild(hpFillEl);
  hpBar.appendChild(hpTextEl);
  hpWrap.appendChild(hpBar);
  content.appendChild(hpWrap);

  stageEl = el("div", { className: "enemy-stage" });
  bossBannerEl = el("div", { className: "boss-banner", textContent: "BOSS", style: { display: "none" } });
  stageEl.appendChild(bossBannerEl);

  enemyBubbleEl = el("div", { className: "enemy-bubble" });
  enemyImgEl = el("img", { className: "enemy-img", alt: "Enemy" });
  enemyBubbleEl.appendChild(enemyImgEl);
  enemyBubbleEl.addEventListener("pointerdown", onTap);
  stageEl.appendChild(enemyBubbleEl);
  content.appendChild(stageEl);

  // Stats bar
  const statsBar = el("div", { className: "stats-bar" });
  statClickEl = makeStatItem(statsBar, "ТАП");
  statAutoEl = makeStatItem(statsBar, "АВТО/С");
  statCritEl = makeStatItem(statsBar, "КРИТ");
  content.appendChild(statsBar);

  // Action bar
  const actionBar = el("div", { className: "action-bar" });
  const upgradeBtn = el("button", { className: "action-btn primary", textContent: "АПГРЕЙДЫ" });
  upgradeBtn.onclick = () => {
    haptic("light");
    showUpgradeModal();
  };
  actionBar.appendChild(upgradeBtn);
  content.appendChild(actionBar);

  root.appendChild(content);
  parent.appendChild(root);

  store.subscribe(render);
  startTimerLoop();
  render();
  return root;
}

function makeStatItem(parent: HTMLElement, label: string): HTMLElement {
  const item = el("div", { className: "stat-item" });
  item.appendChild(el("div", { className: "label", textContent: label }));
  const value = el("div", { className: "value", textContent: "—" });
  item.appendChild(value);
  parent.appendChild(item);
  return value;
}

function render() {
  if (!root || !store.state) return;
  const s = store.state;

  // Background — location bg.
  const bg = s.level_meta.location_bg;
  if (bg) {
    bgEl.style.backgroundImage = `url("${ASSET_BASE}/${bg}")`;
  }

  levelLocEl.textContent = `${s.level_meta.location_name.toUpperCase()} · LVL ${s.level_meta.level}`;
  if (s.level_meta.is_boss) {
    levelNameEl.textContent = `🔥 ${s.level_meta.enemy_name}`;
    levelNameEl.classList.add("boss");
    timerFillEl.classList.add("boss");
    bossBannerEl.style.display = "block";
  } else {
    levelNameEl.textContent = "↳ цель";
    levelNameEl.classList.remove("boss");
    timerFillEl.classList.remove("boss");
    bossBannerEl.style.display = "none";
  }

  if (s.level_meta.enemy_sprite) {
    const newSrc = `${ASSET_BASE}/${s.level_meta.enemy_sprite}`;
    if (enemyImgEl.src !== newSrc) {
      enemyImgEl.src = newSrc;
      // pop-in animation on level change
      enemyBubbleEl.style.animation = "kill-fade 400ms reverse";
      requestAnimationFrame(() => { enemyBubbleEl.style.animation = ""; });
    }
  }
  enemyBubbleEl.classList.toggle("boss", s.level_meta.is_boss);

  updateHpBar(s);
  statClickEl.textContent = fmt(s.user.click_damage);
  statAutoEl.textContent = fmt(s.user.auto_dps);
  statCritEl.textContent = `${Number(s.user.crit_chance).toFixed(0)}%`;

  if (s.user.level !== lastLevel) {
    lastLevel = s.user.level;
  }

  // Level navigation status
  const navCur = (root as any)._navCur as HTMLElement | undefined;
  const navMax = (root as any)._navMax as HTMLElement | undefined;
  const prevBtn = (root as any)._prevBtn as HTMLButtonElement | undefined;
  const nextBtn = (root as any)._nextBtn as HTMLButtonElement | undefined;
  if (navCur) navCur.textContent = `lvl ${s.user.level}`;
  if (navMax) navMax.textContent = `frontier ${s.user.max_level}`;
  if (prevBtn) prevBtn.disabled = s.user.level <= 1;
  if (nextBtn) nextBtn.disabled = s.user.level >= s.user.max_level;
}

async function navigateLevel(delta: number) {
  if (!store.state) return;
  const s = store.state;
  const target = Math.max(1, Math.min(s.user.max_level, s.user.level + delta));
  if (target === s.user.level) return;
  haptic("light");
  const r = await api.gotoLevel(target);
  if (r.ok && r.data?.state) store.setState(r.data.state);
}

function updateHpBar(s: StateSnap) {
  const hp = Number(s.combat.enemy_hp);
  const max = Number(s.combat.enemy_max_hp);
  const pct = max > 0 ? Math.max(0, Math.min(100, (hp / max) * 100)) : 0;
  hpFillEl.style.width = `${pct}%`;
  hpTextEl.textContent = `${fmt(s.combat.enemy_hp)} / ${fmt(s.combat.enemy_max_hp)}`;
}

function startTimerLoop() {
  if (timerInterval) clearInterval(timerInterval);
  timerInterval = setInterval(() => {
    if (!store.state) return;
    const s = store.state;
    const ends = s.combat.timer_ends_at ? new Date(s.combat.timer_ends_at).getTime() : 0;
    if (!ends) {
      timerFillEl.style.width = "100%";
      timerTextEl.textContent = "—";
      return;
    }
    const totalSec = s.level_meta.is_boss ? 40 : 30;
    const remaining = ends - Date.now();
    const pct = Math.max(0, Math.min(100, (remaining / (totalSec * 1000)) * 100));
    timerFillEl.style.width = `${pct}%`;
    timerTextEl.textContent = fmtTimer(remaining);
    if (pct < 25) timerFillEl.classList.add("warn");
    else timerFillEl.classList.remove("warn");
  }, 200);
}

// ---------- TAP HANDLING ----------

function onTap(ev: PointerEvent) {
  ev.preventDefault();
  if (!store.state) return;
  const now = Date.now();
  if (now - lastTapAt < 16) return; // throttle very fast taps
  lastTapAt = now;

  haptic("light");
  pendingTaps++;

  // Optimistic local damage popup (server is authoritative for actual HP).
  const cd = Number(store.state.user.click_damage);
  const isCrit = Math.random() * 100 < Number(store.state.user.crit_chance);
  const dmg = isCrit ? cd * Number(store.state.user.crit_multiplier) : cd;
  spawnDamagePopup(ev.clientX, ev.clientY, dmg, isCrit);

  enemyBubbleEl.classList.remove("shake");
  void enemyBubbleEl.offsetWidth; // restart animation
  enemyBubbleEl.classList.add("shake");

  // Optimistically reduce HP bar.
  const s = store.state;
  const newHp = Math.max(0, Number(s.combat.enemy_hp) - dmg);
  s.combat.enemy_hp = String(newHp);
  updateHpBar(s);

  // Batch flush every 250ms or every 10 taps.
  if (pendingTapsTimer) return;
  pendingTapsTimer = setTimeout(flushTaps, 250);
}

async function flushTaps() {
  pendingTapsTimer = null;
  if (isFlushingTaps) return;
  if (pendingTaps === 0) return;
  isFlushingTaps = true;
  const taps = pendingTaps;
  pendingTaps = 0;
  try {
    const r = await api.tap(taps, 250);
    if (!r.ok || !r.data) {
      console.warn("tap failed", r);
      isFlushingTaps = false;
      return;
    }
    handleTapResult(r.data);
  } catch (e) {
    console.error(e);
  } finally {
    isFlushingTaps = false;
    // If more taps accumulated during request, schedule next flush.
    if (pendingTaps > 0 && !pendingTapsTimer) {
      pendingTapsTimer = setTimeout(flushTaps, 60);
    }
  }
}

function handleTapResult(data: TapResult) {
  if (data.killed) {
    haptic("medium");
    spawnKillFlash();
    if (data.coin_reward) {
      spawnCoinPopup(data.coin_reward);
    }
    if (data.was_boss) {
      hapticNotify("success");
      toast("💀 Босс повержен!", "success");
    }
    if (data.chest_dropped && data.state) {
      toast(`🎁 Сундук: ${data.chest_dropped}!`, "success", 3000);
    }
  } else if (data.timeout) {
    hapticNotify("error");
    toast("⏱ Время вышло — откат на уровень ниже", "error");
  }

  // ANTI-BOUNCE: server's view of enemy_hp can lag behind local optimistic taps
  // because we batch every 250ms. If user is mid-clicking, server sees fewer
  // taps than local has done. Returning that "older" HP would visibly bounce
  // the bar back UP. Solution: never let server-state HP exceed our current
  // local HP (unless the enemy was killed/timeout/level changed).
  if (data.state && store.state && !data.killed && !data.timeout) {
    const localHp = Number(store.state.combat.enemy_hp);
    const serverHp = Number(data.state.combat.enemy_hp);
    const sameMax = data.state.combat.enemy_max_hp === store.state.combat.enemy_max_hp;
    if (sameMax && serverHp > localHp) {
      data.state.combat.enemy_hp = String(localHp);
    }
  }

  if (data.state) store.setState(data.state);
}

function spawnDamagePopup(clientX: number, clientY: number, dmg: number, crit: boolean) {
  const bubble = enemyBubbleEl.getBoundingClientRect();
  const x = clientX - bubble.left;
  const y = clientY - bubble.top;
  const popup = el("div", { className: `dmg-popup ${crit ? "crit" : ""}`, textContent: (crit ? "CRIT! " : "") + fmt(dmg) });
  popup.style.left = `${x}px`;
  popup.style.top = `${y}px`;
  enemyBubbleEl.appendChild(popup);
  setTimeout(() => popup.remove(), 700);
}

function spawnCoinPopup(amount: string) {
  const popup = el("div", { className: "coin-pop", textContent: `+${fmt(amount)}$` });
  popup.style.left = `50%`;
  popup.style.top = `40%`;
  stageEl.appendChild(popup);
  setTimeout(() => popup.remove(), 900);
}

function spawnKillFlash() {
  const flash = el("div", { className: "kill-flash" });
  stageEl.appendChild(flash);
  setTimeout(() => flash.remove(), 420);
}
