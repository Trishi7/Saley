// useApi: one GET view with a loading state, an error line and a reload (R5 T12).
// It goes through api.ts, so the D40 snapshot pin, the 401 sign-in and the 409 "changed" banner
// stay in one place. Loading is derived (the answer belongs to another path), never set inside
// the effect: React 19's effect rules forbid a synchronous setState there.
import { useCallback, useEffect, useState } from "react";
import { ApiError, getJson } from "@/api";
import { W } from "@/words";

export type ApiState<T> = {
  data: T | undefined;
  error: ApiError | null;
  loading: boolean;
  reload: () => void;
};

type Answer<T> = { key: string; data?: T; error: ApiError | null };

/** GET `path` (null = do not fetch yet). A new path or reload() fetches again. */
export function useApi<T>(path: string | null): ApiState<T> {
  const [tick, setTick] = useState(0);
  const [answer, setAnswer] = useState<Answer<T> | null>(null);
  const key = path === null ? null : `${tick}|${path}`;

  useEffect(() => {
    if (path === null || key === null) return undefined;
    let live = true;
    getJson<T>(path).then(
      (data) => live && setAnswer({ key, data, error: null }),
      (err: unknown) =>
        live &&
        setAnswer({
          key,
          error: err instanceof ApiError ? err : new ApiError(0, W.errGeneric),
        }),
    );
    return () => {
      live = false;
    };
  }, [path, key]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  const current = answer !== null && answer.key === key ? answer : null;
  return {
    data: current?.data,
    error: current?.error ?? null,
    loading: key !== null && current === null,
    reload,
  };
}
