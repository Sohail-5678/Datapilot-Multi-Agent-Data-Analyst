"use client";

import { useRef } from "react";
import { cn } from "@/lib/utils";

export interface TabDef {
  id: string;
  label: string;
  badge?: string | number;
}

export const tabId = (base: string, id: string) => `${base}-tab-${id}`;
export const panelId = (base: string, id: string) => `${base}-panel-${id}`;

/** WAI-ARIA tabs with roving focus; scrolls horizontally on phones. The caller renders the panel with
 * id={panelId(base, value)} and aria-labelledby={tabId(base, value)}. */
export function Tabs({ tabs, value, onChange, className, base }: { tabs: TabDef[]; value: string; onChange: (id: string) => void; className?: string; base: string }) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const onKey = (e: React.KeyboardEvent, i: number) => {
    const dir = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
    if (!dir && e.key !== "Home" && e.key !== "End") return;
    e.preventDefault();
    const n = e.key === "Home" ? 0 : e.key === "End" ? tabs.length - 1 : (i + dir + tabs.length) % tabs.length;
    refs.current[n]?.focus();
    onChange(tabs[n].id);
  };
  return (
    <div role="tablist" aria-label="Answer views" className={cn("scrollbar-thin -mx-1 flex gap-1 overflow-x-auto px-1", className)}>
      {tabs.map((t, i) => {
        const active = t.id === value;
        return (
          <button
            key={t.id}
            ref={(el) => {
              refs.current[i] = el;
            }}
            role="tab"
            id={tabId(base, t.id)}
            aria-selected={active}
            aria-controls={active ? panelId(base, t.id) : undefined}
            tabIndex={active ? 0 : -1}
            onClick={() => onChange(t.id)}
            onKeyDown={(e) => onKey(e, i)}
            className={cn(
              "relative shrink-0 rounded-full px-3.5 py-1.5 font-mono text-[0.66rem] uppercase tracking-[0.18em] transition-colors",
              active ? "bg-navy text-ivory dark:bg-ivory dark:text-navy" : "text-muted hover:bg-surface-2 hover:text-ink",
            )}
          >
            {t.label}
            {t.badge !== undefined && <span className="ml-1.5 opacity-70">{t.badge}</span>}
          </button>
        );
      })}
    </div>
  );
}
