// Loading, error, empty and "changed" lines, the same on every page (docs/08 shared rules:
// never a blank panel). `.err` is the hook the gate's no_error_box reads (R1.1 checks).
import { RotateCwIcon } from "lucide-react";
import { useEffect, useState } from "react";
import { onChanged, type ApiError } from "@/api";
import { Button } from "@/components/ui/button";
import { fmt, W } from "@/words";

export function LoadingLine() {
  return (
    <p role="status" className="text-muted-foreground animate-pulse text-sm">
      {W.loading}
    </p>
  );
}

/** "Could not load {what}. {reason}": the data that failed, then why. The reason is the
 * server's own line, or W.noServer when the server did not answer (api.ts), never the raw
 * browser text such as "Failed to fetch". */
export function loadErrorText(what: string, reason: string): string {
  return `${fmt(W.loadFailed, { what })} ${reason}`;
}

/** The Retry Button of a failed load (`data-retry`, a button, never a link). */
export function RetryButton({ onRetry }: { onRetry: () => void }) {
  return (
    <Button type="button" variant="outline" size="sm" data-retry onClick={onRetry}>
      <RotateCwIcon aria-hidden />
      {W.retry}
    </Button>
  );
}

export function ErrorLine({
  what,
  error,
  onRetry,
}: {
  what: string;
  error: ApiError;
  onRetry?: () => void;
}) {
  return (
    <div role="alert" className="err text-bad flex flex-wrap items-center gap-3 text-sm">
      <span>{loadErrorText(what, error.message)}</span>
      {onRetry && <RetryButton onRetry={onRetry} />}
    </div>
  );
}

/** "No items. Last computed HH:MM." or "No data yet." (docs/08 shared rules). */
export function EmptyLine({ computed }: { computed?: string | null }) {
  return (
    <p className="empty text-muted-foreground text-sm">
      {computed ? fmt(W.noItems, { t: computed }) : W.noDataYet}
    </p>
  );
}

/** D40: a 409 on a GET means the sheet snapshot moved. Ask for a reload, never mix two. */
export function ChangedBanner() {
  const [changed, setChanged] = useState(false);
  useEffect(() => onChanged(() => setChanged(true)), []);
  if (!changed) return null;
  return (
    <div
      id="changed"
      role="status"
      className="bg-warn-soft text-warn flex flex-wrap items-center justify-between gap-3 px-4 py-2 text-sm sm:px-6"
    >
      <span>{W.changed}</span>
      <Button type="button" size="sm" variant="outline" onClick={() => window.location.reload()}>
        {W.reload}
      </Button>
    </div>
  );
}
