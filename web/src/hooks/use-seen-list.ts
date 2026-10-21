// The Gmail model of Signals, Suggestions and Events (R5 T15, D157): a "New" tab with what this
// person has not seen, 10 a page, and a "Seen earlier" tab with the rest. Both counts ride on
// every answer (`new_total`, `seen_total`), so the tab labels show them.
// - After a New page is drawn, the page tells Saley which items it showed: one POST /api/seen
//   with the same body as the legacy page (`{item_type, ids}`, at most 200 ids; D75). It is not
//   a control (no button, no `data-act`); the gate counts it apart (`seen_posts`).
// - The items stay on screen for this visit. "Next" on the New tab waits for that mark, then
//   asks the New tab again from offset 0: the shown items moved to Seen earlier, so an offset
//   of 10 would skip the next 10 new items (plan T15, T7 review).
// - The Seen earlier tab pages by offset, as any list.
// - A mark is tried twice. When both fail, the New tab pages by offset for the rest of this
//   visit and `markFailed` lets the page say so (lib/seen-paging.ts; T15 review): never the
//   same page again, never a loop, no item lost.
import { useCallback, useEffect, useRef, useState } from "react";
import { postJson } from "@/api";
import { PAGE_SIZE } from "@/components/saley/Pager";
import { useApi } from "@/hooks/use-api";
import { useKeptPage, useOffset } from "@/hooks/use-kept";
import {
  afterMark,
  FRESH,
  markWithRetry,
  moveNew,
  newOffset,
  type NewPaging,
} from "@/lib/seen-paging";

export type SeenTab = "new" | "seen";
export type SeenItem = { id: number; seen: boolean };
export type SeenAnswer = {
  items: SeenItem[];
  total: number;
  new_total: number;
  seen_total: number;
  last_computed_label: string;
};

const SEEN_PATH = "/api/seen";
const SEEN_MAX = 200; // the server takes at most 200 ids in one call (schemas.SeenIn)

/** `/api/signals?tab=all` + the tab, the page size and the offset. */
export function seenPath(base: string, tab: SeenTab, offset: number): string {
  const sep = base.includes("?") ? "&" : "?";
  return `${base}${sep}view=${tab}&limit=${PAGE_SIZE}&offset=${offset}`;
}

/** The ids to mark seen: the shown items this person had not seen yet (never 0 or below). */
export function unseenIds(items: readonly SeenItem[]): number[] {
  return items
    .filter((item) => !item.seen && item.id > 0)
    .map((item) => item.id)
    .slice(0, SEEN_MAX);
}

export function useSeenList<T extends SeenAnswer>(base: string, tab: SeenTab, itemType: string) {
  const [seenOffset, setSeenOffset] = useOffset(`${base}|seen`);
  // The New tab's paging belongs to one list (base): a new filter starts fresh.
  const [newState, setNewState] = useState<{ key: string; paging: NewPaging }>({
    key: base,
    paging: FRESH,
  });
  const paging = newState.key === base ? newState.paging : FRESH;
  const offset = tab === "seen" ? seenOffset : newOffset(paging);
  const { data, error, loading, reload } = useApi<T>(seenPath(base, tab, offset));
  const kept = useKeptPage(data, offset);
  const pending = useRef<Promise<boolean>>(Promise.resolve(true));
  const marked = useRef<T | undefined>(undefined);

  // After the New page is drawn (an effect runs after the commit), once per answer.
  useEffect(() => {
    if (tab !== "new" || data === undefined || marked.current === data) return;
    marked.current = data;
    const ids = unseenIds(data.items);
    if (ids.length === 0) return;
    pending.current = markWithRetry(() => postJson(SEEN_PATH, { item_type: itemType, ids })).then(
      (ok) => {
        if (!ok) setNewState((s) => ({ key: base, paging: afterMark(s.key === base ? s.paging : FRESH, false) }));
        return ok;
      },
    );
  }, [tab, data, itemType, base]);

  const move = useCallback(
    (next: number) => {
      if (tab === "seen") {
        setSeenOffset(next);
        return;
      }
      if (paging.failed) {
        setNewState({ key: base, paging: moveNew(paging, next) });
        return;
      }
      if (next <= 0) return;
      void pending.current.then((ok) => {
        // A good mark: ask New again from 0. A failed one: afterMark switched to offset paging.
        if (ok) reload();
        else setNewState({ key: base, paging: moveNew({ failed: true, offset: 0 }, next) });
      });
    },
    [tab, setSeenOffset, reload, paging, base],
  );

  return {
    data: kept?.page,
    offset: kept?.offset ?? offset,
    error,
    loading,
    reload,
    move,
    markFailed: paging.failed,
  };
}
