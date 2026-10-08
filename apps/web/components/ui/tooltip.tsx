"use client";

import { useId, useState } from "react";
import { cn } from "@/lib/utils";

/** Hover/focus tooltip that is also announced (aria-describedby). */
export function Tooltip({ content, children, className, side = "bottom" }: { content: React.ReactNode; children: React.ReactNode; className?: string; side?: "bottom" | "top" }) {
  const id = useId();
  const [open, setOpen] = useState(false);
  return (
    <span className={cn("relative inline-flex", className)} onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)} onFocus={() => setOpen(true)} onBlur={() => setOpen(false)}>
      <span aria-describedby={id} tabIndex={0} className="inline-flex rounded-full">
        {children}
      </span>
      <span
        role="tooltip"
        id={id}
        className={cn(
          "pointer-events-none absolute right-0 z-40 w-72 rounded-xl border border-line bg-surface p-3 text-left text-xs leading-relaxed text-ink shadow-[var(--shadow-lift)] transition-all duration-200",
          side === "bottom" ? "top-full mt-2" : "bottom-full mb-2",
          open ? "visible translate-y-0 opacity-100" : "invisible -translate-y-1 opacity-0",
        )}
      >
        {content}
      </span>
    </span>
  );
}
