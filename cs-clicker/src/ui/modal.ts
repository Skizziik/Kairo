import { el } from "../util";

export interface ModalAction {
  label: string;
  className?: string;
  onClick: () => void | Promise<void>;
}

export interface ModalHandle {
  close: () => void;
  body: HTMLElement;
  root: HTMLElement;
  setActions: (actions: ModalAction[]) => void;
}

export function openModal(opts: {
  title: string;
  body: HTMLElement | string;
  actions?: ModalAction[];
  closeOnBackdrop?: boolean;
  className?: string;
}): ModalHandle {
  const overlay = el("div", { className: "modal-overlay" });
  const close = () => {
    overlay.style.transition = "opacity 200ms";
    overlay.style.opacity = "0";
    setTimeout(() => overlay.remove(), 220);
  };

  const closeBtn = el("button", { className: "modal-close", textContent: "×" });
  closeBtn.onclick = close;
  const header = el("div", { className: "modal-header" }, [
    el("div", { className: "modal-title", textContent: opts.title }),
    closeBtn,
  ]);

  const body = el("div", { className: "modal-body" });
  if (typeof opts.body === "string") body.innerHTML = opts.body;
  else body.appendChild(opts.body);

  const actions = el("div", { className: "modal-actions" });
  const setActions = (list: ModalAction[]) => {
    actions.innerHTML = "";
    if (!list || list.length === 0) {
      actions.style.display = "none";
      return;
    }
    actions.style.display = "flex";
    for (const a of list) {
      const btn = el("button", { className: `modal-btn ${a.className || ""}`, textContent: a.label });
      btn.onclick = () => { void a.onClick(); };
      actions.appendChild(btn);
    }
  };
  setActions(opts.actions || []);

  const modal = el("div", { className: `modal ${opts.className || ""}` }, [header, body, actions]);
  overlay.appendChild(modal);
  if (opts.closeOnBackdrop !== false) {
    overlay.addEventListener("click", (e) => {
      if (e.target === overlay) close();
    });
  }
  document.body.appendChild(overlay);

  return { close, body, root: overlay, setActions };
}
