import type { ApiResponse, ConfigSnap, LeaderboardEntry, OpenChestResult, StateSnap, TapResult } from "./types";

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
        disableVerticalSwipes?: () => void;
        setBackgroundColor?: (color: string) => void;
        setHeaderColor?: (color: string) => void;
        close: () => void;
      };
    };
  }
}

function getInitData(): string {
  const tg = window.Telegram?.WebApp;
  if (tg?.initData) return tg.initData;
  return (import.meta as any).env?.VITE_DEV_INIT_DATA || "";
}

async function request<T>(path: string, init: RequestInit = {}): Promise<ApiResponse<T>> {
  const url = `${API_BASE}${path}`;
  const headers = new Headers(init.headers);
  headers.set("X-Telegram-Init-Data", getInitData());
  if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
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
  config: () => request<ConfigSnap>("/api/clicker/config"),
  state: () => request<StateSnap>("/api/clicker/state").then((r) => {
    // /state returns the state directly under data.state OR data depending on path.
    // Backend wraps with _wrap_state which returns {ok, data:{state:...}}.
    return r;
  }),
  tap: (taps: number, dt_ms: number) =>
    request<TapResult>("/api/clicker/tap", {
      method: "POST",
      body: JSON.stringify({ taps, dt_ms }),
    }),
  upgrade: (kind: string, slot_id: string, count: number = 1) =>
    request<{ state: StateSnap; kind: string; slot_id: string; new_level: number; spent: string }>(
      "/api/clicker/upgrade",
      { method: "POST", body: JSON.stringify({ kind, slot_id, count }) },
    ),
  openChest: (chest_inventory_id: number) =>
    request<OpenChestResult>("/api/clicker/chest/open", {
      method: "POST",
      body: JSON.stringify({ chest_inventory_id }),
    }),
  equip: (inventory_id: number, slot: number) =>
    request<{ state: StateSnap; equipped: number; slot: number }>("/api/clicker/equip", {
      method: "POST",
      body: JSON.stringify({ inventory_id, slot }),
    }),
  unequip: (inventory_id: number) =>
    request<{ state: StateSnap; unequipped: number }>("/api/clicker/unequip", {
      method: "POST",
      body: JSON.stringify({ inventory_id }),
    }),
  prestige: () =>
    request<{ state: StateSnap; glory_gained: number }>("/api/clicker/prestige", {
      method: "POST",
    }),
  businessTap: (business_id: string) =>
    request<{ state: StateSnap; tapped: string; resource: string }>("/api/clicker/business/tap", {
      method: "POST",
      body: JSON.stringify({ business_id }),
    }),
  businessCollect: (business_id: string | null = null) =>
    request<{ state: StateSnap; collected: Record<string, string> }>("/api/clicker/business/collect", {
      method: "POST",
      body: JSON.stringify({ business_id }),
    }),
  businessUpgrade: (business_id: string) =>
    request<{ state: StateSnap; new_level: number; spent: string }>("/api/clicker/business/upgrade", {
      method: "POST",
      body: JSON.stringify({ business_id }),
    }),
  leaderboard: (metric: string, limit: number = 50) =>
    request<LeaderboardEntry[]>(`/api/clicker/leaderboard?metric=${encodeURIComponent(metric)}&limit=${limit}`),
};

export function haptic(kind: "light" | "medium" | "heavy" = "light") {
  try { window.Telegram?.WebApp?.HapticFeedback?.impactOccurred?.(kind); } catch {}
}
export function hapticNotify(kind: "success" | "error" | "warning") {
  try { window.Telegram?.WebApp?.HapticFeedback?.notificationOccurred?.(kind); } catch {}
}
