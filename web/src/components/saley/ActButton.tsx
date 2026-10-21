// A control that changes something (docs/08 Appendix B: `[data-act]` on every state-changing
// control). Always `type="button"` (no form can submit) with `data-endpoint` = the one POST
// path it calls, so gate step 9 can audit it without pressing it. While its POST waits it is
// `aria-disabled`, not `disabled`: a disabled button drops the focus to <body> (T13 review).
import type { ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export function ActButton({
  act,
  endpoint,
  busy,
  onPress,
  children,
  label,
  variant = "outline",
  off = false,
  className,
}: {
  /** The `data-act` kind (legacy names: primary, dismiss, confirm, undo, commitment-done …). */
  act: string;
  endpoint: string;
  busy: boolean;
  onPress: () => void;
  children: ReactNode;
  /** The accessible name when the visible text alone is not clear (from W). */
  label?: string;
  variant?: "outline" | "secondary" | "default" | "ghost";
  /** Off for a reason other than a POST in flight (a news run is queued): really disabled. */
  off?: boolean;
  className?: string;
}) {
  return (
    <Button
      type="button"
      size="sm"
      variant={variant}
      data-act={act}
      data-endpoint={endpoint}
      aria-label={label}
      aria-busy={busy || undefined}
      aria-disabled={busy || undefined}
      disabled={off}
      onClick={() => {
        if (!busy && !off) onPress();
      }}
      className={cn("h-10 aria-disabled:cursor-wait aria-disabled:opacity-60 sm:h-8", className)}
    >
      {children}
    </Button>
  );
}
