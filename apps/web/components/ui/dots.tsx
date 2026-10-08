export function Dots({ className = "" }: { className?: string }) {
  return (
    <span className={`inline-flex items-center gap-1 ${className}`} aria-hidden>
      {[0, 1, 2].map((i) => (
        <span key={i} className="size-1.5 rounded-full bg-current [animation:dp-pulse-dot_1.2s_ease-in-out_infinite]" style={{ animationDelay: `${i * 0.18}s` }} />
      ))}
    </span>
  );
}
