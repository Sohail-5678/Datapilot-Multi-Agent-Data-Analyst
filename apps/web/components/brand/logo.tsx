import Link from "next/link";
import { cn } from "@/lib/utils";

export function Star({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden className={cn("inline-block", className)}>
      <path d="M12 0c.6 6.2 5.8 11.4 12 12-6.2.6-11.4 5.8-12 12-.6-6.2-5.8-11.4-12-12C6.2 11.4 11.4 6.2 12 0Z" fill="currentColor" />
    </svg>
  );
}

export function Logo({ className, href = "/" }: { className?: string; href?: string }) {
  return (
    <Link href={href} className={cn("group inline-flex items-center gap-2.5", className)} aria-label="DataPilot home">
      <Star className="size-3.5 text-accent transition-transform duration-700 ease-[var(--ease-velora)] group-hover:rotate-90" />
      <span className="font-serif text-[1.15rem] font-semibold tracking-[0.32em]">DATAPILOT</span>
    </Link>
  );
}
