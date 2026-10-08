import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

const nf = new Intl.NumberFormat("en-US");
export const fmtInt = (n: number | null | undefined) => (n == null ? "—" : nf.format(n));

export function fmtCell(v: unknown): string {
  if (v === null || v === undefined) return "NULL";
  if (typeof v === "number") {
    if (Number.isInteger(v)) return nf.format(v);
    return Math.abs(v) >= 1000 ? nf.format(Math.round(v * 100) / 100) : String(Math.round(v * 10000) / 10000);
  }
  return String(v);
}

export function fmtMs(ms: number | null | undefined) {
  if (ms == null) return "—";
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(ms < 10_000 ? 1 : 0)} s`;
}

export function fmtUsd(v: number | null | undefined) {
  if (v == null) return "—";
  return v < 0.01 ? `$${v.toFixed(4)}` : `$${v.toFixed(3)}`;
}

export function pad2(n: number) {
  return String(n).padStart(2, "0");
}

export function toCsv(columns: string[], rows: unknown[][]) {
  const esc = (v: unknown) => {
    const s = v === null || v === undefined ? "" : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  return [columns.map(esc).join(","), ...rows.map((r) => r.map(esc).join(","))].join("\n");
}

export function timeAgo(iso: string | null | undefined) {
  if (!iso) return "";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
}
