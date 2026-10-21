// One press is one POST (gate step 9): a control that is still waiting for its answer ignores
// a second press. The error comes back as an ApiError, so the row can say the server's own line
// (words.py) and never the raw browser text. Shared by the team pages (R5 T16a).
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "@/api";
import { nextWake, type Tick } from "@/lib/team-rules";
import { W } from "@/words";

export type Press = (run: () => Promise<void>) => Promise<ApiError | null>;

export function usePress(): { busy: boolean; press: Press } {
  const busyRef = useRef(false);
  const [busy, setBusy] = useState(false);
  const press = useCallback<Press>(async (run) => {
    if (busyRef.current) return null;
    busyRef.current = true;
    setBusy(true);
    try {
      await run();
      return null;
    } catch (err) {
      return err instanceof ApiError ? err : new ApiError(0, W.errGeneric);
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }, []);
  return { busy, press };
}

/** The Undo windows of one list, by row id. `null` means "undone here": the row is open again
 *  even while an older answer still shows it done. The clock wakes once when the next window
 *  ends, so that row turns into its closed state; nothing counts down on the page (brief). */
export function useTicks(): {
  ticks: Record<string, Tick | null>;
  now: number;
  setTick: (key: string, tick: Tick | null) => void;
} {
  const [ticks, setTicks] = useState<Record<string, Tick | null>>({});
  const [now, setNow] = useState(() => Date.now());
  const wake = nextWake(Object.values(ticks), now);
  useEffect(() => {
    if (!Number.isFinite(wake)) return undefined;
    const timer = window.setTimeout(() => setNow(Date.now()), Math.max(0, wake - Date.now()));
    return () => window.clearTimeout(timer);
  }, [wake]);
  const setTick = useCallback((key: string, tick: Tick | null) => {
    setNow(Date.now());
    setTicks((all) => ({ ...all, [key]: tick }));
  }, []);
  return { ticks, now, setTick };
}
