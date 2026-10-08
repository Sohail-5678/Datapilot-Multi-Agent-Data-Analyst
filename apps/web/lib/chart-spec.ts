import { z } from "zod";

/** zod mirror of the backend's Vega-Lite subset whitelist (SPEC §8.3). No data, transforms, URLs or expressions. */
const channel = z
  .object({
    field: z.string(),
    type: z.enum(["quantitative", "nominal", "ordinal", "temporal"]),
    title: z.string().max(80).optional(),
    sort: z.enum(["-y", "-x", "x", "y", "ascending", "descending"]).nullable().optional(),
    aggregate: z.enum(["sum", "mean", "count", "max", "min", "average", "median"]).optional(),
  })
  .strict();

const spec = z
  .object({
    mark: z.enum(["bar", "line", "point", "area", "arc"]),
    encoding: z
      .object({ x: channel.optional(), y: channel.optional(), color: channel.optional(), theta: channel.optional(), tooltip: z.union([channel, z.array(channel)]).optional() })
      .strict(),
    title: z.string().max(120).optional(),
  })
  .strict();

export type ChartSpec = z.infer<typeof spec>;

export function validateChartSpec(raw: unknown, columns: string[]): ChartSpec | null {
  const parsed = spec.safeParse(raw);
  if (!parsed.success) return null;
  const enc = parsed.data.encoding;
  const fields = [enc.x, enc.y, enc.color, enc.theta, ...(Array.isArray(enc.tooltip) ? enc.tooltip : enc.tooltip ? [enc.tooltip] : [])].filter(Boolean);
  if (!fields.every((f) => columns.includes(f!.field))) return null;
  return parsed.data;
}

/** Text summary for screen readers (charts have a text alternative + the table). */
export function describeChart(s: ChartSpec, columns: string[], rows: unknown[][]): string {
  const x = s.encoding.x?.field ?? s.encoding.theta?.field;
  const y = s.encoding.y?.field;
  const kind = { bar: "Bar chart", line: "Line chart", point: "Scatter plot", area: "Area chart", arc: "Pie chart" }[s.mark];
  const ix = x ? columns.indexOf(x) : -1;
  const iy = y ? columns.indexOf(y) : -1;
  const num = (i: number) => rows.map((r) => r[i]).filter((v): v is number => typeof v === "number");
  let extra = "";
  const nums = iy >= 0 ? num(iy) : ix >= 0 ? num(ix) : [];
  if (nums.length) extra = ` Values range from ${Math.min(...nums)} to ${Math.max(...nums)}.`;
  return `${kind} of ${y ?? ""}${y && x ? " by " : ""}${x ?? ""}, ${rows.length} data points.${extra}`;
}
