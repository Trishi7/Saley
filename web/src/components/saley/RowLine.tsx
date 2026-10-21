// The one line under a row after a press: a refusal in red (the server's own line), or a calm
// line ("Already done by X at HH:MM", "Undo window closed at …") in the muted colour. Never the
// raw browser text (api.ts). `role="status"` so a screen reader hears it.
import { cn } from "@/lib/utils";

export type Line = { text: string; bad: boolean };

export function RowLine({ line, className }: { line: Line | null; className?: string }) {
  if (!line) return null;
  return (
    <span
      role="status"
      data-row-line
      data-row-error={line.bad || undefined}
      className={cn("text-xs text-pretty", line.bad ? "text-bad" : "text-muted-foreground", className)}
    >
      {line.text}
    </span>
  );
}
