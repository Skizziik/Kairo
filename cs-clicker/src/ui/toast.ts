import { el } from "../util";

let wrap: HTMLElement | null = null;

function ensureWrap(): HTMLElement {
  if (wrap) return wrap;
  wrap = el("div", { className: "toast-wrap" });
  document.body.appendChild(wrap);
  return wrap;
}

export function toast(text: string, kind: "success" | "error" | "info" = "info", durationMs = 2000) {
  const w = ensureWrap();
  const t = el("div", { className: `toast ${kind}`, textContent: text });
  w.appendChild(t);
  setTimeout(() => {
    t.style.transition = "opacity 200ms, transform 200ms";
    t.style.opacity = "0";
    t.style.transform = "translateY(-20px)";
    setTimeout(() => t.remove(), 240);
  }, durationMs);
}
