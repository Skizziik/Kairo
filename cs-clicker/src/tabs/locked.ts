import { el } from "../util";

export function mountLockedTab(parent: HTMLElement, opts: {
  tabId: string;
  icon: string;
  title: string;
  message: string;
}): HTMLElement {
  const root = el("div", { className: "tab-page", dataset: { tab: opts.tabId } });
  const wrap = el("div", { className: "locked-tab" });
  wrap.appendChild(el("div", { className: "icon", textContent: opts.icon }));
  wrap.appendChild(el("div", { className: "tag", textContent: "PHASE 2" }));
  wrap.appendChild(el("div", { className: "tab-title", textContent: opts.title }));
  wrap.appendChild(el("div", { className: "msg", textContent: opts.message }));
  root.appendChild(wrap);
  parent.appendChild(root);
  return root;
}
