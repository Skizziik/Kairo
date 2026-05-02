import Phaser from "phaser";
import { api, haptic, hapticNotify } from "../api";
import type { StateSnap, ConfigSnap } from "../types";
import { renderTopBar, renderBottomNav, renderCornerHud, showQuestList, toast } from "../ui/hud";

export class UIScene extends Phaser.Scene {
  constructor() { super("UIScene"); }

  create() {
    const state = this.registry.get("state") as StateSnap;
    const config = this.registry.get("config") as ConfigSnap;
    const map = this.scene.get("MapScene");

    // Initial render.
    renderTopBar(state, config);
    renderCornerHud(state, async () => {
      haptic("medium");
      const r = await api.collectAll();
      if (r.ok && r.data) {
        const collected = r.data.collected;
        const total = Object.values(collected).reduce((a, b) => a + Number(b), 0);
        if (total > 0) {
          hapticNotify("success");
          toast(`+ ${formatCollected(collected, config)}`, "success");
        } else {
          toast("Пока нечего собирать", "info");
        }
        map.events.emit("ui:state_updated", r.data.state);
      } else {
        toast("Ошибка", "error");
      }
    });
    renderBottomNav({
      onBuild: () => {
        haptic("light");
        map.events.emit("ui:open_build_menu");
      },
      onQuests: () => {
        haptic("light");
        const cur = this.registry.get("state") as StateSnap;
        const cfg = this.registry.get("config") as ConfigSnap;
        showQuestList(this, cur, cfg, async (qid) => {
          haptic("medium");
          const r = await api.questClaim(qid);
          if (r.ok && r.data) {
            hapticNotify("success");
            const rewards = r.data.rewards || {};
            toast(`Награда: ${formatRewards(rewards)}`, "success");
            map.events.emit("ui:state_updated", r.data.state);
            return true;
          }
          toast("Не получилось забрать награду", "error");
          return false;
        });
      },
      onTech: () => toast("Технологии — в Beta", "info"),
      onFriends: () => toast("Друзья — в Beta", "info"),
      onShop: () => toast("Магазин — в Beta", "info"),
    });

    // Listen for state updates from MapScene.
    map.events.on("ui:rerender_hud", (s: StateSnap) => {
      renderTopBar(s, config);
      renderCornerHud(s, async () => {
        haptic("medium");
        const r = await api.collectAll();
        if (r.ok && r.data) {
          const collected = r.data.collected;
          const total = Object.values(collected).reduce((a, b) => a + Number(b), 0);
          if (total > 0) {
            hapticNotify("success");
            toast(`+ ${formatCollected(collected, config)}`, "success");
          } else {
            toast("Пока нечего собирать", "info");
          }
          map.events.emit("ui:state_updated", r.data.state);
        }
      });
    });

    // Show offline summary if we just came back.
    const pending = state.pending_total;
    if (pending && Object.keys(pending).length > 0) {
      const total = Object.values(pending).reduce((a, b) => a + Number(b), 0);
      if (total > 5) {
        toast(`Накопилось пока тебя не было: ${formatCollected(pending, config)}`, "info");
      }
    }
  }
}

function formatCollected(map: Record<string, string | number>, config: ConfigSnap): string {
  const parts: string[] = [];
  for (const [k, v] of Object.entries(map)) {
    const def = config.resources[k];
    const num = Math.floor(Number(v));
    if (num <= 0) continue;
    parts.push(`${num} ${def?.name ?? k}`);
  }
  return parts.join(", ");
}

function formatRewards(map: Record<string, number>): string {
  const parts: string[] = [];
  if (map.gold) parts.push(`${map.gold} 🪙`);
  if (map.gems) parts.push(`${map.gems} 💎`);
  if (map.experience) parts.push(`+${map.experience} XP`);
  for (const k of ["wood", "stone", "food", "water"]) {
    if (map[k]) parts.push(`${map[k]} ${k}`);
  }
  return parts.join(" · ") || "—";
}
