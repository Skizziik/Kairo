import type { ConfigSnap, StateSnap } from "./types";

type Listener = () => void;

class Store {
  config: ConfigSnap | null = null;
  state: StateSnap | null = null;
  activeTab: "clicker" | "business" | "inventory" | "market" | "more" = "clicker";

  private listeners: Set<Listener> = new Set();

  setConfig(c: ConfigSnap) {
    this.config = c;
    this.notify();
  }

  setState(s: StateSnap) {
    this.state = s;
    this.notify();
  }

  setTab(t: typeof this.activeTab) {
    this.activeTab = t;
    this.notify();
  }

  subscribe(fn: Listener): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  notify() {
    for (const fn of this.listeners) {
      try { fn(); } catch (e) { console.error("listener fail", e); }
    }
  }
}

export const store = new Store();
