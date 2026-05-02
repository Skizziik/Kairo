import type { ApiResponse, ConfigSnap, StateSnap } from "./types";

// Default backend = Render-hosted casino backend (already deployed).
// Override via VITE_API_BASE env if you run backend locally on Docker too.
// Default = Render-hosted casino backend (same as webapp/app.js).
// Override via VITE_API_BASE env if you run backend locally on Docker.
const API_BASE: string = (import.meta as any).env?.VITE_API_BASE || "https://kairo-bot-hc22.onrender.com";

declare global {
  interface Window {
    Telegram?: {
      WebApp?: {
        initData: string;
        ready: () => void;
        expand: () => void;
        colorScheme: "light" | "dark";
        HapticFeedback?: {
          impactOccurred: (style: "light" | "medium" | "heavy" | "rigid" | "soft") => void;
          notificationOccurred: (type: "error" | "success" | "warning") => void;
          selectionChanged: () => void;
        };
        MainButton?: any;
        BackButton?: any;
        close: () => void;
      };
    };
  }
}

function getInitData(): string {
  // Telegram WebApp injects initData. In dev/browser, fall back to env or empty.
  const tg = window.Telegram?.WebApp;
  if (tg?.initData) return tg.initData;
  // Dev fallback for browser testing (set VITE_DEV_INIT_DATA in .env.local).
  return (import.meta as any).env?.VITE_DEV_INIT_DATA || "";
}

async function request<T>(path: string, init: RequestInit = {}): Promise<ApiResponse<T>> {
  const url = `${API_BASE}${path}`;
  const headers = new Headers(init.headers);
  headers.set("X-Telegram-Init-Data", getInitData());
  if (init.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  let resp: Response;
  try {
    resp = await fetch(url, { ...init, headers });
  } catch (e) {
    return { ok: false, error: "network", message: String(e) };
  }
  let body: any;
  try {
    body = await resp.json();
  } catch {
    return { ok: false, error: "bad_json", message: `status ${resp.status}` };
  }
  if (!resp.ok && body?.ok === undefined) {
    return { ok: false, error: "http_" + resp.status, message: body?.detail || body?.message };
  }
  return body as ApiResponse<T>;
}

export const api = {
  config: () => request<ConfigSnap>("/api/villager/config"),
  state: () => request<StateSnap>("/api/villager/state"),
  build: (type: string, x: number, y: number) =>
    request<{ state: StateSnap; new_building_id: number }>("/api/villager/build", {
      method: "POST",
      body: JSON.stringify({ type, x, y }),
    }),
  upgrade: (building_id: number) =>
    request<{ state: StateSnap }>("/api/villager/upgrade", {
      method: "POST",
      body: JSON.stringify({ building_id }),
    }),
  move: (building_id: number, x: number, y: number) =>
    request<{ state: StateSnap }>("/api/villager/move", {
      method: "POST",
      body: JSON.stringify({ building_id, x, y }),
    }),
  demolish: (building_id: number) =>
    request<{ state: StateSnap; refund: Record<string, number> }>("/api/villager/demolish", {
      method: "POST",
      body: JSON.stringify({ building_id }),
    }),
  collectAll: () =>
    request<{ state: StateSnap; collected: Record<string, string> }>("/api/villager/collect_all", {
      method: "POST",
    }),
  questClaim: (quest_id: string) =>
    request<{ state: StateSnap; rewards: Record<string, number> }>("/api/villager/quest/claim", {
      method: "POST",
      body: JSON.stringify({ quest_id }),
    }),
};

export function haptic(kind: "light" | "medium" | "heavy" = "light") {
  try {
    window.Telegram?.WebApp?.HapticFeedback?.impactOccurred?.(kind);
  } catch {}
}

export function hapticNotify(kind: "success" | "error" | "warning") {
  try {
    window.Telegram?.WebApp?.HapticFeedback?.notificationOccurred?.(kind);
  } catch {}
}
