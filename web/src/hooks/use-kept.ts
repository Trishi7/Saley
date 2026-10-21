// Keep the last answer on screen while the next one loads (a new page, filter or search), so
// a list never blanks between two answers (R5 T13 pattern, shared by T14). React: state from
// the previous render, set during render, never inside an effect.
import { useCallback, useMemo, useState } from "react";

export function useKept<T>(data: T | undefined): T | undefined {
  const [kept, setKept] = useState<T | undefined>(data);
  if (data !== undefined && data !== kept) setKept(data);
  return data ?? kept;
}

/** An offset that belongs to one list key (filter + search). A new key starts at 0, so a new
 *  filter never opens on page 3 of the old one. No effect needed. */
export function useOffset(key: string): [number, (offset: number) => void] {
  const [paging, setPaging] = useState({ key, offset: 0 });
  const offset = paging.key === key ? paging.offset : 0;
  // Stable per key, so a Pager effect that lists it does not run again on every render.
  const move = useCallback((next: number) => setPaging({ key, offset: next }), [key]);
  return [offset, move];
}

/** One page answer with the offset it was asked for. While the next page loads, the rows on
 *  screen and the pager's range text both stay those of the last answer. */
export function useKeptPage<T>(
  data: T | undefined,
  offset: number,
): { page: T; offset: number } | undefined {
  const answer = useMemo(() => (data === undefined ? undefined : { page: data, offset }), [data, offset]);
  return useKept(answer);
}
