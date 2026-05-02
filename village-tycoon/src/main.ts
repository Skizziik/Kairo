import Phaser from "phaser";
import { BootScene } from "./scenes/BootScene";
import { MapScene } from "./scenes/MapScene";
import { UIScene } from "./scenes/UIScene";

// Tell Telegram we're ready and expand to full height.
const tg = window.Telegram?.WebApp;
if (tg) {
  try {
    tg.ready();
    tg.expand();
    // Bot API 7.7+: kill the swipe-down-to-close gesture so the user can
    // scroll/pan inside the game canvas without accidentally exiting.
    if (typeof (tg as any).disableVerticalSwipes === "function") {
      (tg as any).disableVerticalSwipes();
    }
    // Bot API 7.7+: lock background colour so iOS doesn't flash the white
    // safe-area when the user pulls the app.
    if (typeof (tg as any).setBackgroundColor === "function") {
      (tg as any).setBackgroundColor("#1A1410");
    }
    if (typeof (tg as any).setHeaderColor === "function") {
      (tg as any).setHeaderColor("#1A1410");
    }
  } catch {}
}

// Belt-and-braces: prevent native pull-to-refresh / overscroll on iOS Safari
// even on older Telegram clients that don't support disableVerticalSwipes.
document.addEventListener(
  "touchmove",
  (e) => {
    // Only block when the gesture is on the canvas/HUD root, not inside a modal.
    const target = e.target as HTMLElement;
    if (!target.closest(".modal, .modal-overlay")) {
      if (e.touches.length === 1 && e.cancelable) e.preventDefault();
    }
  },
  { passive: false },
);

const game = new Phaser.Game({
  type: Phaser.AUTO,
  parent: "game-root",
  width: window.innerWidth,
  height: window.innerHeight,
  backgroundColor: "#5BAE5B", // pleasant grass-green default
  scale: {
    mode: Phaser.Scale.RESIZE,
    autoCenter: Phaser.Scale.CENTER_BOTH,
  },
  render: {
    antialias: true,
    pixelArt: false,
    roundPixels: false,
  },
  scene: [BootScene, MapScene, UIScene],
});

(window as any).__GAME__ = game;
