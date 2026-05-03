// Utility helpers — number formatting, currency display, time, etc.

const SUFFIXES = [
  "", "k", "M", "B", "T", "Qa", "Qi", "Sx", "Sp", "Oc", "No", "Dc",
];

export function fmt(value: string | number | bigint, digits = 1): string {
  // Negatives → recurse on the absolute string.
  const raw = typeof value === "bigint" ? value.toString() : String(value);
  if (raw.startsWith("-")) return "-" + fmt(raw.substring(1), digits);

  // Small / fractional values: keep up to 1 decimal so weapon-01 lvl 3 shows "1.6".
  // Up to 9999 we format as a regular Russian-grouped number.
  const num = Number(raw);
  if (!Number.isNaN(num) && Math.abs(num) < 10000) {
    if (Math.abs(num) < 100) {
      const rounded = Math.round(num * 10) / 10;
      return rounded.toFixed(1).replace(/\.0$/, "").replace(".", ",");
    }
    return Math.floor(num).toLocaleString("ru-RU");
  }

  // Big numbers (string-based suffix abbreviation).
  const intPart = raw.split(".")[0];
  const len = intPart.length;
  if (len <= 4) return Number(intPart).toLocaleString("ru-RU");

  const groupIndex = Math.floor((len - 1) / 3);
  if (groupIndex < SUFFIXES.length) {
    const suffix = SUFFIXES[groupIndex];
    const headLen = ((len - 1) % 3) + 1;
    const head = intPart.slice(0, headLen);
    const dec = intPart.slice(headLen, headLen + digits).replace(/0+$/, "");
    return dec ? `${head}.${dec}${suffix}` : `${head}${suffix}`;
  }
  return Number(intPart).toExponential(2);
}

export function fmtTimer(ms: number): string {
  if (ms <= 0) return "0:00";
  const total = Math.ceil(ms / 1000);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

export function pct(num: string | number): string {
  const n = typeof num === "string" ? Number(num) : num;
  if (Number.isNaN(n)) return "0%";
  return `${n.toFixed(n < 10 ? 1 : 0)}%`;
}

export function rarityColor(rarity: string | null): string {
  switch ((rarity || "common").toLowerCase()) {
    case "common": return "#9CA3AF";
    case "uncommon": return "#22D3EE";
    case "rare": return "#3B82F6";
    case "epic": return "#A855F7";
    case "legendary": return "#F59E0B";
    case "mythic": return "#EC4899";
    default: return "#9CA3AF";
  }
}

export function rarityLabel(rarity: string | null): string {
  switch ((rarity || "common").toLowerCase()) {
    case "common": return "Common";
    case "uncommon": return "Uncommon";
    case "rare": return "Rare";
    case "epic": return "Epic";
    case "legendary": return "Legendary";
    case "mythic": return "Mythic";
    default: return rarity || "—";
  }
}

export function clamp(n: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, n));
}

type ElProps<K extends keyof HTMLElementTagNameMap> = Omit<Partial<HTMLElementTagNameMap[K]>, "style"> & {
  className?: string;
  dataset?: Record<string, string>;
  style?: Record<string, string | number>;
  // common allowed string-ish overrides
  innerHTML?: string;
  textContent?: string;
  src?: string;
  alt?: string;
};

export function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  props: ElProps<K> = {} as ElProps<K>,
  children: (Node | string)[] = [],
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (v === undefined || v === null) continue;
    if (k === "className") (node as any).className = v as string;
    else if (k === "dataset") for (const [dk, dv] of Object.entries(v as Record<string, string>)) (node as any).dataset[dk] = dv;
    else if (k === "style") for (const [sk, sv] of Object.entries(v as Record<string, string | number>)) (node.style as any)[sk] = sv;
    else (node as any)[k] = v;
  }
  for (const c of children) {
    node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
  }
  return node;
}

export const ASSET_BASE = "/assets";
