/**
 * Engraved astrolabe — DataPilot's hero illustration (original artwork, drawn in code).
 * Hairline rings, degree ticks and Roman numerals; a "constellation" that is secretly a line chart;
 * a crimson needle that settles on the answer. Strokes draw in on load; the outer ring turns slowly.
 */
const C = 300;

function polar(r: number, deg: number) {
  const a = ((deg - 90) * Math.PI) / 180;
  return [C + r * Math.cos(a), C + r * Math.sin(a)] as const;
}

const ROMAN = ["XII", "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI"];
// A tiny "time series" laid across the inner dial: data as constellation.
const SERIES = [0.42, 0.55, 0.48, 0.66, 0.6, 0.78, 0.71, 0.88];

export function Astrolabe({ className }: { className?: string }) {
  const ticks = Array.from({ length: 180 }, (_, i) => i * 2);
  const chartPts = SERIES.map((v, i) => [180 + i * 34, 380 - v * 170] as const);
  const needle = polar(212, 38);
  return (
    <svg viewBox="0 0 600 600" className={className} role="img" aria-label="An engraved astrolabe whose stars form a rising line chart">
      <defs>
        <radialGradient id="dp-glow" cx="50%" cy="45%" r="55%">
          <stop offset="0%" stopColor="#afaea2" stopOpacity="0.16" />
          <stop offset="70%" stopColor="#afaea2" stopOpacity="0.03" />
          <stop offset="100%" stopColor="#afaea2" stopOpacity="0" />
        </radialGradient>
        <pattern id="dp-hatch" width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(35)">
          <line x1="0" y1="0" x2="0" y2="5" stroke="#afaea2" strokeWidth="0.6" strokeOpacity="0.45" />
        </pattern>
      </defs>
      <circle cx={C} cy={C} r="296" fill="url(#dp-glow)" />
      <g fill="none" stroke="#afaea2" strokeLinecap="round" className="[&_*]:[stroke-dasharray:var(--len)] [&_*]:[animation:dp-draw_2.6s_var(--ease-velora)_both]">
        <circle cx={C} cy={C} r="284" strokeWidth="0.8" style={{ ["--len" as string]: 1790 }} />
        <circle cx={C} cy={C} r="276" strokeWidth="1.6" style={{ ["--len" as string]: 1740 }} />
        <circle cx={C} cy={C} r="232" strokeWidth="0.8" style={{ ["--len" as string]: 1460 }} />
        <circle cx={C} cy={C} r="226" strokeWidth="0.5" style={{ ["--len" as string]: 1420 }} />
        <circle cx={C} cy={C} r="150" strokeWidth="0.6" strokeDasharray="1 5" />
        <circle cx={C} cy={C} r="96" strokeWidth="0.6" style={{ ["--len" as string]: 610 }} />
        <ellipse cx={C} cy={C - 26} rx="168" ry="150" strokeWidth="0.6" strokeOpacity="0.7" style={{ ["--len" as string]: 1000 }} />
        <line x1={C - 226} y1={C} x2={C + 226} y2={C} strokeWidth="0.5" strokeOpacity="0.6" style={{ ["--len" as string]: 460 }} />
        <line x1={C} y1={C - 226} x2={C} y2={C + 226} strokeWidth="0.5" strokeOpacity="0.6" style={{ ["--len" as string]: 460 }} />
      </g>

      {/* Rotating degree ring */}
      <g className="origin-center [animation:dp-spin_140s_linear_infinite]" style={{ transformOrigin: "300px 300px" }}>
        {ticks.map((d) => {
          const long = d % 30 === 0;
          const mid = d % 10 === 0;
          const [x1, y1] = polar(276, d);
          const [x2, y2] = polar(long ? 258 : mid ? 264 : 270, d);
          return <line key={d} x1={x1} y1={y1} x2={x2} y2={y2} stroke="#afaea2" strokeWidth={long ? 1.2 : 0.6} strokeOpacity={long ? 0.95 : 0.6} />;
        })}
        {ROMAN.map((r, i) => {
          const [x, y] = polar(244, i * 30);
          return (
            <text key={r} x={x} y={y} fill="#afaea2" fontSize="13" fontFamily="var(--font-cormorant), Georgia, serif" textAnchor="middle" dominantBaseline="central" letterSpacing="1" transform={`rotate(${i * 30} ${x} ${y})`}>
              {r}
            </text>
          );
        })}
      </g>

      {/* Compass rose (hatched engraving) */}
      <g stroke="#afaea2" strokeWidth="0.7" strokeLinejoin="round">
        {[0, 90, 180, 270].map((d) => {
          const [tx, ty] = polar(212, d);
          const [lx, ly] = polar(22, d - 90);
          return <path key={d} d={`M${tx},${ty} L${lx},${ly} L${C},${C} Z`} fill="url(#dp-hatch)" />;
        })}
        {[0, 90, 180, 270].map((d) => {
          const [tx, ty] = polar(212, d);
          const [rx, ry] = polar(22, d + 90);
          return <path key={`r${d}`} d={`M${tx},${ty} L${rx},${ry} L${C},${C} Z`} fill="#afaea2" fillOpacity="0.08" />;
        })}
        {[45, 135, 225, 315].map((d) => {
          const [tx, ty] = polar(128, d);
          const [lx, ly] = polar(14, d - 90);
          const [rx, ry] = polar(14, d + 90);
          return <path key={d} d={`M${tx},${ty} L${lx},${ly} L${C},${C} L${rx},${ry} Z`} fill="#afaea2" fillOpacity="0.05" />;
        })}
      </g>

      {/* The constellation: a rising series */}
      <g>
        <polyline points={chartPts.map((p) => p.join(",")).join(" ")} fill="none" stroke="#efece4" strokeWidth="0.9" strokeOpacity="0.75" strokeDasharray="2 4" />
        {chartPts.map(([x, y], i) => (
          <g key={i} className="[animation:dp-pulse-dot_4s_ease-in-out_infinite]" style={{ animationDelay: `${i * 0.35}s`, transformOrigin: `${x}px ${y}px` }}>
            <circle cx={x} cy={y} r={i === chartPts.length - 1 ? 4.2 : 2.4} fill={i === chartPts.length - 1 ? "#d21319" : "#efece4"} />
            {i === chartPts.length - 1 && <circle cx={x} cy={y} r="10" fill="none" stroke="#d21319" strokeOpacity="0.6" strokeWidth="0.8" />}
          </g>
        ))}
      </g>

      {/* Crimson needle */}
      <g className="[animation:dp-float_7s_ease-in-out_infinite]">
        <line x1={C} y1={C} x2={needle[0]} y2={needle[1]} stroke="#d21319" strokeWidth="1.6" strokeLinecap="round" />
        <circle cx={needle[0]} cy={needle[1]} r="3.5" fill="#d21319" />
      </g>
      <circle cx={C} cy={C} r="7" fill="#1b1e4a" stroke="#afaea2" strokeWidth="1" />
      <circle cx={C} cy={C} r="2.2" fill="#d21319" />
    </svg>
  );
}
