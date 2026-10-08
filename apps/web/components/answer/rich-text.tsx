import { Fragment } from "react";

/** Minimal, safe Markdown subset for answers: paragraphs, "-"/"*" bullets, "1." numbered lists and **bold**. Rendered as React
 * text nodes (no HTML injection). */
function inline(s: string) {
  const parts = s.split(/(\*\*[^*]+\*\*)/g);
  return parts.map((p, i) =>
    p.startsWith("**") && p.endsWith("**") ? <strong key={i}>{p.slice(2, -2)}</strong> : <Fragment key={i}>{p.replace(/(^|\s)\*(\S[^*]*\S|\S)\*(?=\s|$)/g, "$1$2")}</Fragment>,
  );
}

export function RichText({ text, className }: { text: string; className?: string }) {
  const blocks: { type: "p" | "ul" | "ol"; lines: string[] }[] = [];
  for (const raw of text.split(/\n/)) {
    const line = raw.trimEnd();
    const bullet = /^\s*[-*•]\s+/.test(line);
    const numbered = /^\s*\d+[.)]\s+/.test(line);
    if (!line.trim()) {
      blocks.push({ type: "p", lines: [] });
      continue;
    }
    const last = blocks[blocks.length - 1];
    if (numbered) {
      const item = line.replace(/^\s*\d+[.)]\s+/, "");
      if (last?.type === "ol") last.lines.push(item);
      else blocks.push({ type: "ol", lines: [item] });
    } else if (bullet) {
      const item = line.replace(/^\s*[-*•]\s+/, "");
      if (last?.type === "ul") last.lines.push(item);
      else blocks.push({ type: "ul", lines: [item] });
    } else if (last?.type === "p" && last.lines.length) last.lines.push(line);
    else blocks.push({ type: "p", lines: [line] });
  }
  return (
    <div className={className}>
      {blocks
        .filter((b) => b.lines.length)
        .map((b, i) =>
          b.type === "ul" ? (
            <ul key={i}>
              {b.lines.map((l, j) => (
                <li key={j}>{inline(l)}</li>
              ))}
            </ul>
          ) : b.type === "ol" ? (
            <ol key={i}>
              {b.lines.map((l, j) => (
                <li key={j}>{inline(l)}</li>
              ))}
            </ol>
          ) : (
            <p key={i}>{inline(b.lines.join(" "))}</p>
          ),
        )}
    </div>
  );
}
