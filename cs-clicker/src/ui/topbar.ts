import { store } from "../state";
import type { StateSnap } from "../types";
import { fmt, el } from "../util";

let root: HTMLElement | null = null;

export function mountTopbar(parent: HTMLElement) {
  root = el("div", { className: "topbar" });
  parent.appendChild(root);
  store.subscribe(render);
  render();
}

function render() {
  if (!root || !store.state) return;
  const s = store.state;
  root.innerHTML = "";

  root.appendChild(el("div", { className: "topbar-level" }, [
    el("div", { className: "label", textContent: "LVL" }),
    el("div", { className: "value", textContent: String(s.user.level) }),
  ]));

  const cur = el("div", { className: "topbar-currencies" });
  cur.appendChild(currencyChip("$", s.user.cash, "gold"));
  cur.appendChild(currencyChip("⌬", s.user.casecoins, "cc"));
  if (Number(s.user.glory) > 0 || s.user.prestige_count > 0) {
    cur.appendChild(currencyChip("★", s.user.glory, "glory"));
  }
  root.appendChild(cur);
}

function currencyChip(icon: string, value: string, klass: string): HTMLElement {
  return el("div", { className: `cur-chip ${klass}` }, [
    el("span", { className: "icon", textContent: icon }),
    el("span", { textContent: fmt(value) }),
  ]);
}
