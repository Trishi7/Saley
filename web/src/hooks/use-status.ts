// The top bar status (docs/08 Shell, D43), as the legacy loadStatus(): read /api/status every
// minute, and every 3 s while a write waits for the worker. /api/status never pins sync_id
// (api.ts): the bar always shows the newest snapshot. After a network failure (the server did
// not answer) it looks again in 30 s, so a server restart heals the bar by itself; `retry`
// looks again at once (the Retry Button of the bar).
import { useCallback, useEffect, useState } from "react";
import { ApiError, getJson } from "@/api";
import { W } from "@/words";

export type Status = {
  state: string; // ok · partial · worker_down · sync_failing · never
  message: string; // "Updated HH:MM IST · sheet HH:MM · news HH:MM", or the red reason (D43)
  late_total: number;
  pending_writes: number;
  write_note: string | null;
  write_bad: boolean;
};

const POLL_MS = 60_000;
const PENDING_POLL_MS = 3_000;
const NETWORK_RETRY_MS = 30_000;

export function useStatus(): { status: Status | null; error: string | null; retry: () => void } {
  const [status, setStatus] = useState<Status | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let live = true;
    let timer: number | undefined;
    const load = () => {
      getJson<Status>("/api/status").then(
        (s) => {
          if (!live) return;
          setStatus(s);
          setError(null);
          timer = window.setTimeout(load, s.pending_writes ? PENDING_POLL_MS : POLL_MS);
        },
        (err: unknown) => {
          if (!live) return;
          const network = err instanceof ApiError && err.status === 0;
          setError(err instanceof ApiError ? err.message : W.errGeneric);
          timer = window.setTimeout(load, network ? NETWORK_RETRY_MS : POLL_MS);
        },
      );
    };
    load();
    return () => {
      live = false;
      window.clearTimeout(timer);
    };
  }, [attempt]);
  const retry = useCallback(() => setAttempt((n) => n + 1), []);
  return { status, error, retry };
}
