import Phaser from "phaser";
import { api } from "../api";
import type { ConfigSnap, StateSnap } from "../types";

export class BootScene extends Phaser.Scene {
  constructor() { super("BootScene"); }

  preload() {
    // Hooks the loader to the HTML loading bar.
    const fillEl = document.getElementById("loading-fill") as HTMLDivElement | null;
    const hintEl = document.getElementById("loading-hint") as HTMLDivElement | null;

    this.load.on("progress", (v: number) => {
      if (fillEl) fillEl.style.width = `${Math.max(8, Math.round(v * 100))}%`;
    });
    if (hintEl) hintEl.textContent = "Загружаем картинки…";

    const baseAsset = (path: string) => `/assets/${path}`;

    // Resources
    const resources = [
      "wood", "stone", "food", "water", "gold", "gems",
    ];
    for (const r of resources) {
      this.load.image(`res_${r}`, baseAsset(`resources/res_${r}.png`));
    }

    // Buildings (8 × 3 + construction).
    const buildings = [
      "townhall", "lumbermill", "quarry", "farm",
      "storage", "house", "well", "builderhut",
    ];
    for (const b of buildings) {
      for (const lvl of [1, 2, 3]) {
        this.load.image(`b_${b}_${lvl}`, baseAsset(`buildings/building_${b}_lvl${lvl}.png`));
      }
    }
    this.load.image("b_construction", baseAsset("buildings/building_construction.png"));

    // Decorations
    const decos = ["tree_oak", "tree_pine", "tree_dead", "rock_1", "rock_2", "bush_1", "bush_2"];
    for (const d of decos) {
      this.load.image(`deco_${d}`, baseAsset(`decorations/deco_${d}.png`));
    }

    // UI
    const ui = [
      "button_build", "button_quests", "button_friends", "button_shop", "button_tech",
      "button_close", "button_collect",
      "panel_modal", "panel_top_resources", "resource_chip",
      "progress_bar_frame", "notification_toast",
    ];
    for (const u of ui) {
      this.load.image(`ui_${u}`, baseAsset(`ui/ui_${u}.png`));
    }
  }

  async create() {
    const hintEl = document.getElementById("loading-hint") as HTMLDivElement | null;
    if (hintEl) hintEl.textContent = "Подключаемся к серверу…";

    let config: ConfigSnap | null = null;
    let state: StateSnap | null = null;

    try {
      const cfg = await api.config();
      if (!cfg.ok || !cfg.data) throw new Error(cfg.error || "config_failed");
      config = cfg.data;

      const st = await api.state();
      if (!st.ok || !st.data) throw new Error(st.error || "state_failed");
      state = st.data;
    } catch (e: any) {
      if (hintEl) {
        hintEl.textContent = "Ошибка соединения. Попробуй перезайти.";
        hintEl.style.color = "#F26B5B";
      }
      console.error("[boot] failed:", e);
      return;
    }

    // Stash on registry — used by other scenes.
    this.registry.set("config", config);
    this.registry.set("state", state);

    // Hide the HTML loading overlay with fade.
    const overlay = document.getElementById("loading-overlay");
    if (overlay) {
      overlay.classList.add("hidden");
      setTimeout(() => overlay.remove(), 500);
    }

    this.scene.start("MapScene");
    this.scene.launch("UIScene");
  }
}
