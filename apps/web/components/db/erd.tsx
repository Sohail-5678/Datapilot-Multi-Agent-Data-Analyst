"use client";

import { useMemo } from "react";
import type { TableInfo } from "@/lib/types";
import { cn, fmtInt } from "@/lib/utils";

/** Lightweight ERD: tables on an ellipse, curved FK edges (dashed when inferred). No Mermaid runtime needed. */
export function Erd({ tables, erd, onPick, active }: { tables: TableInfo[]; erd: string; onPick: (t: string) => void; active: string | null }) {
  const W = 1000;
  const H = Math.max(360, Math.min(560, 180 + tables.length * 26));
  const layout = useMemo(() => {
    const names = new Set(tables.map((t) => t.name));
    const edges = erd
      .split("\n")
      .map((l) => /^\s*(\S+) \|\|--o\{ (\S+) : "(\w+)"/.exec(l))
      .filter(Boolean)
      .map((m) => ({ a: m![1], b: m![2], inferred: m![3] === "inferred" }))
      .filter((e) => names.has(e.a) && names.has(e.b));
    // Hub-and-spoke: the most-connected table sits in the middle so its edges fan out instead of crossing.
    const degree = new Map<string, number>();
    edges.forEach((e) => {
      degree.set(e.a, (degree.get(e.a) ?? 0) + 1);
      degree.set(e.b, (degree.get(e.b) ?? 0) + 1);
    });
    const hub = [...degree.entries()].sort((a, b) => b[1] - a[1])[0];
    const center = hub && hub[1] >= 3 && tables.length > 4 ? hub[0] : null;
    const ring = tables.filter((t) => t.name !== center);
    const pos = new Map<string, { x: number; y: number }>();
    if (center) pos.set(center, { x: W / 2, y: H / 2 });
    ring.forEach((t, i) => {
      const a = (i / ring.length) * Math.PI * 2 - Math.PI / 2;
      pos.set(t.name, { x: W / 2 + Math.cos(a) * (W / 2 - 120), y: H / 2 + Math.sin(a) * (H / 2 - 56) });
    });
    return { pos, edges, center };
  }, [tables, erd, H]);
  return (
    <div className="scrollbar-thin overflow-x-auto">
      <svg viewBox={`0 0 ${W} ${H}`} className="min-w-[680px]" role="group" aria-label={`Entity-relationship diagram with ${tables.length} tables and ${layout.edges.length} relationships`}>
        {layout.edges.map((e, i) => {
          const p = layout.pos.get(e.a)!;
          const q = layout.pos.get(e.b)!;
          const viaHub = e.a === layout.center || e.b === layout.center;
          const pull = viaHub ? 0 : 0.35;
          const mx = (p.x + q.x) / 2 + (W / 2 - (p.x + q.x) / 2) * pull;
          const my = (p.y + q.y) / 2 + (H / 2 - (p.y + q.y) / 2) * pull;
          const hot = active && (e.a === active || e.b === active);
          return <path key={i} d={`M${p.x},${p.y} Q${mx},${my} ${q.x},${q.y}`} fill="none" stroke={hot ? "var(--accent)" : "var(--line-strong)"} strokeWidth={hot ? 1.8 : 1} strokeDasharray={e.inferred ? "4 4" : undefined} />;
        })}
        {tables.map((t) => {
          const p = layout.pos.get(t.name)!;
          const on = t.name === active;
          const w = Math.max(110, t.name.length * 9 + 36);
          return (
            <g key={t.name} transform={`translate(${p.x - w / 2},${p.y - 22})`} className="cursor-pointer" onClick={() => onPick(t.name)} role="button" tabIndex={0} aria-label={`${t.name}, ${t.rows} rows`} onKeyDown={(ev) => (ev.key === "Enter" || ev.key === " ") && onPick(t.name)}>
              <rect width={w} height="44" rx="10" className={cn(on ? "fill-[var(--navy)]" : "fill-[var(--surface)]")} stroke={on ? "var(--accent)" : "var(--line-strong)"} />
              <text x={w / 2} y="20" textAnchor="middle" className={cn("font-serif text-[15px] font-semibold", on ? "fill-[var(--ivory)]" : "fill-[var(--ink)]")}>{t.name}</text>
              <text x={w / 2} y="34" textAnchor="middle" className={cn("font-mono text-[9px] tracking-[0.12em]", on ? "fill-[var(--stone)]" : "fill-[var(--muted)]")}>{fmtInt(t.rows)} ROWS</text>
            </g>
          );
        })}
      </svg>
      <p className="px-6 pb-4 text-xs text-muted">Solid lines are declared foreign keys; dashed lines are inferred from matching key names. Click a table for its columns.</p>
    </div>
  );
}
