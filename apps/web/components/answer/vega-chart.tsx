"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { validateChartSpec } from "@/lib/chart-spec";

/** Vega-Lite subset renderer. The spec comes from the chart agent (validated twice: server + zod here); the
 * data is injected from the result rows by the browser, never by the model. CSP-safe (expression interpreter). */
export function VegaChart({ spec, columns, rows, className }: { spec: Record<string, unknown>; columns: string[]; rows: unknown[][]; className?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);
  const valid = useMemo(() => validateChartSpec(spec, columns), [spec, columns]);
  useEffect(() => {
    const el = ref.current;
    if (!el || !valid) return;
    let view: { finalize: () => void } | null = null;
    let cancelled = false;
    (async () => {
      const [{ default: embed }, { expressionInterpreter }] = await Promise.all([import("vega-embed"), import("vega-interpreter")]);
      if (cancelled) return;
      const dark = document.documentElement.dataset.theme === "dark";
      const ink = dark ? "#efece4" : "#1b1e4a";
      const muted = dark ? "#b4b3a8" : "#5f5e57";
      const grid = dark ? "rgba(175,174,162,0.14)" : "rgba(27,30,74,0.10)";
      const values = rows.map((r) => Object.fromEntries(columns.map((c, i) => [c, r[i]])));
      // Horizontal bars: height follows the number of categories so a few bars stay slim and elegant.
      const yNominal = valid.mark === "bar" && valid.encoding.y && valid.encoding.y.type !== "quantitative";
      const height = yNominal ? Math.min(460, Math.max(120, new Set(values.map((v) => v[valid.encoding.y!.field])).size * 34)) : 300;
      const full = {
        $schema: "https://vega.github.io/schema/vega-lite/v6.json",
        ...valid,
        data: { values },
        width: "container",
        height,
        autosize: { type: "fit", contains: "padding" },
        config: {
          background: "transparent",
          font: "Manrope, ui-sans-serif, system-ui",
          view: { stroke: null },
          scale: { bandPaddingInner: 0.32, bandPaddingOuter: 0.18 },
          range: { category: ["#1b1e4a", "#d21319", "#8f8e83", "#4b4f8f", "#e58a8d", "#c9c7bc"] },
          mark: { color: dark ? "#f0444a" : "#1b1e4a" },
          bar: { cornerRadiusEnd: 3, color: dark ? "#f0444a" : "#1b1e4a" },
          line: { strokeWidth: 2.2, color: dark ? "#f0444a" : "#d21319", point: { filled: true, size: 40 } },
          point: { filled: true, size: 55, opacity: 0.7 },
          arc: { stroke: dark ? "#171a42" : "#f5f3ed", strokeWidth: 2 },
          axis: { labelColor: muted, titleColor: ink, gridColor: grid, domainColor: grid, tickColor: grid, labelFont: "JetBrains Mono, monospace", labelFontSize: 10.5, titleFont: "Manrope", titleFontWeight: 700, titleFontSize: 11.5, labelLimit: 180 },
          legend: { labelColor: muted, titleColor: ink, labelFont: "Manrope" },
          title: { color: ink, font: "Cormorant Garamond, Georgia, serif", fontSize: 20, fontWeight: 600, anchor: "start" },
        },
      };
      try {
        const res = await embed(el, full as never, { actions: false, renderer: "svg", ast: true, expr: expressionInterpreter, tooltip: { theme: dark ? "dark" : "light" } });
        if (cancelled) res.finalize();
        else view = res;
      } catch (e) {
        setError(e instanceof Error ? e.message : "Chart failed to render.");
      }
    })();
    return () => {
      cancelled = true;
      view?.finalize();
    };
  }, [valid, columns, rows]);
  if (!valid) return <p className="text-sm text-muted">The chart spec didn&apos;t pass validation.</p>;
  if (error) return <p className="text-sm text-muted">{error}</p>;
  return <div ref={ref} className={className} aria-hidden />;
}
