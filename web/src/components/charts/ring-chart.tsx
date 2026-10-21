// Progress ring. D148 swap: the bklit ring and gauge use @number-flow/react, a custom element
// that writes its own styles into a shadow root. That path is not covered by the page nonce,
// so the CSP may block it (zero console errors is the rule). T12 drew this ring with Recharts
// (RadialBar); R5 T14 (D211, proposed) draws the same shape as two plain SVG circles: the
// Recharts bundle was over 300 kB for one arc. SVG attributes only, so the CSP has nothing to
// block. The track is the muted token, the arc the chart-1 token, round caps, no animation.
import { cn } from "@/lib/utils";

const FULL = 100;
const RADIUS = 42;
const STROKE = 11;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;

export function RingChart({
  value,
  label,
  center,
  className,
}: {
  /** 0–100. null means "no target": the ring shows empty and the centre text says why. */
  value: number | null;
  label: string;
  center: string;
  className?: string;
}) {
  const shown = value === null ? 0 : Math.max(0, Math.min(FULL, value));
  const arc = (CIRCUMFERENCE * shown) / FULL;
  return (
    <div className={cn("relative", className)}>
      <svg viewBox="0 0 100 100" aria-label={label} role="img" className="size-full -rotate-90">
        <circle cx="50" cy="50" r={RADIUS} fill="none" stroke="var(--muted)" strokeWidth={STROKE} />
        {arc > 0 && (
          <circle
            data-ring-arc
            cx="50"
            cy="50"
            r={RADIUS}
            fill="none"
            stroke="var(--chart-1)"
            strokeWidth={STROKE}
            strokeLinecap="round"
            strokeDasharray={`${arc} ${CIRCUMFERENCE}`}
          />
        )}
      </svg>
      <span className="tabular absolute inset-0 flex items-center justify-center text-lg font-semibold">
        {center}
      </span>
    </div>
  );
}
