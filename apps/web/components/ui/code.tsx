"use client";

import { Check, Copy } from "lucide-react";
import { useEffect, useState } from "react";
import { cn } from "@/lib/utils";

/** Vélora code theme for Shiki: navy ground, ivory text, crimson keywords, stone strings. */
const VELORA_THEME = {
  name: "velora",
  type: "dark",
  colors: { "editor.background": "#1b1e4a", "editor.foreground": "#ece9e0" },
  tokenColors: [
    { scope: ["comment", "punctuation.definition.comment"], settings: { foreground: "#8f8e83", fontStyle: "italic" } },
    { scope: ["keyword", "storage", "keyword.operator.logical", "support.function.builtin"], settings: { foreground: "#ff7a7e" } },
    { scope: ["string", "string.quoted"], settings: { foreground: "#d9d6c8" } },
    { scope: ["constant.numeric", "constant.language"], settings: { foreground: "#f6c177" } },
    { scope: ["entity.name.function", "support.function"], settings: { foreground: "#a9c7ff" } },
    { scope: ["variable", "entity.name", "support.type"], settings: { foreground: "#ece9e0" } },
    { scope: ["keyword.operator", "punctuation"], settings: { foreground: "#b4b3a8" } },
  ],
} as const;

type Highlighter = { codeToHtml: (code: string, opts: { lang: string; theme: string }) => string };
let highlighter: Promise<Highlighter> | null = null;

function getHighlighter() {
  if (!highlighter) {
    highlighter = (async () => {
      const [{ createHighlighterCore }, { createJavaScriptRegexEngine }, sql, python] = await Promise.all([
        import("shiki/core"),
        import("shiki/engine/javascript"),
        import("shiki/langs/sql.mjs"),
        import("shiki/langs/python.mjs"),
      ]);
      return (await createHighlighterCore({
        themes: [VELORA_THEME as never],
        langs: [sql.default, python.default],
        engine: createJavaScriptRegexEngine(),
      })) as unknown as Highlighter;
    })();
  }
  return highlighter;
}

export function CodeBlock({ code, lang, className, label }: { code: string; lang: "sql" | "python"; className?: string; label?: string }) {
  const [html, setHtml] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    let alive = true;
    getHighlighter()
      .then((h) => alive && setHtml(h.codeToHtml(code, { lang, theme: "velora" })))
      .catch(() => alive && setHtml(null));
    return () => {
      alive = false;
    };
  }, [code, lang]);
  const copy = async () => {
    await navigator.clipboard.writeText(code);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };
  return (
    <div className={cn("group relative overflow-hidden rounded-2xl bg-[var(--code-bg)] text-[var(--code-ink)]", className)}>
      <div className="flex items-center justify-between border-b border-white/10 px-4 py-2">
        <span className="font-mono text-[0.6rem] uppercase tracking-[0.22em] text-stone">{label ?? lang}</span>
        <button type="button" onClick={copy} className="flex items-center gap-1.5 rounded-full px-2 py-1 font-mono text-[0.6rem] uppercase tracking-[0.18em] text-stone hover:bg-white/10 hover:text-ivory" aria-label={`Copy ${lang}`}>
          {copied ? <Check className="size-3" /> : <Copy className="size-3" />} {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <div className="shiki-wrap scrollbar-thin max-h-[26rem] overflow-auto px-4 py-3">
        {html ? <div dangerouslySetInnerHTML={{ __html: html }} /> : <pre className="font-mono text-[0.8125rem] leading-[1.65] whitespace-pre-wrap">{code}</pre>}
      </div>
    </div>
  );
}
