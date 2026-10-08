import Link from "next/link";
import { Star } from "@/components/brand/logo";

export function SiteFooter() {
  return (
    <footer className="relative z-10 mt-24 bg-navy text-ivory">
      <div className="mx-auto grid max-w-[1440px] gap-10 px-4 py-14 sm:px-8 md:grid-cols-[1.4fr_1fr_1fr]">
        <div>
          <p className="flex items-center gap-2.5 font-serif text-lg tracking-[0.32em]">
            <Star className="size-3.5 text-crimson-bright" /> DATAPILOT
          </p>
          <p className="mt-4 max-w-sm text-sm leading-relaxed text-stone">
            A team of agents that plans, writes, checks and explains SQL — and proves every number it tells you.
          </p>
          <p className="mt-6 font-mono text-[0.65rem] tracking-[0.22em] text-stone">#1B1E4A · #AFAEA2 · #D21319</p>
        </div>
        <div className="text-sm">
          <p className="label !text-stone">Explore</p>
          <ul className="mt-4 space-y-2.5">
            <li><Link className="link-underline" href="/app">Workspace</Link></li>
            <li><Link className="link-underline" href="/databases">Databases</Link></li>
            <li><Link className="link-underline" href="/benchmarks">Benchmarks</Link></li>
            <li><Link className="link-underline" href="/about">How it works</Link></li>
          </ul>
        </div>
        <div className="text-sm">
          <p className="label !text-stone">Credits</p>
          <ul className="mt-4 space-y-2.5 text-stone">
            <li>BIRD Mini-Dev · CC BY-SA 4.0</li>
            <li>Chinook database · MIT</li>
            <li>Pyodide · Vega-Lite · LangGraph</li>
            <li><a className="link-underline text-ivory" href="https://github.com/Sohail-5678/Datapilot-Multi-Agent-Data-Analyst">Source on GitHub</a></li>
          </ul>
        </div>
      </div>
      <div className="mx-auto max-w-[1440px] px-4 pb-8 sm:px-8">
        <div className="h-px bg-ivory/15" />
        <p className="mt-6 font-mono text-[0.6rem] tracking-[0.24em] text-stone">BUILT AT $0 · READ-ONLY BY DESIGN · DEMO DATA ONLY</p>
      </div>
    </footer>
  );
}
