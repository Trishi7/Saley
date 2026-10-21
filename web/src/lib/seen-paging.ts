// Paging rules of the New tab (R5 T15, D216; T15 review). Pure: no DOM, no fetch, no imports,
// so `npm test` (node --test) runs them as they are.
// - The page marks the shown New items seen (POST /api/seen). While marks work, "Next" asks the
//   New tab again from offset 0: the shown items moved to Seen earlier.
// - A mark that fails twice (one retry) would make "Next" show the same page again, for ever.
//   So for the rest of this visit the New tab pages by offset, as any list, and the page says
//   so in one calm line. No item is lost and nothing loops.

export type NewPaging = { failed: boolean; offset: number };

export const FRESH: NewPaging = { failed: false, offset: 0 };

/** The offset the New tab asks for. */
export function newOffset(paging: NewPaging): number {
  return paging.failed ? paging.offset : 0;
}

/** After the mark of the shown page: a failed mark switches this visit to offset paging. */
export function afterMark(paging: NewPaging, ok: boolean): NewPaging {
  return ok || paging.failed ? paging : { failed: true, offset: paging.offset };
}

/** "Next" or "Previous" to `next`: offset 0 again while marks work (refetch), else `next`. */
export function moveNew(paging: NewPaging, next: number): NewPaging {
  return paging.failed ? { failed: true, offset: Math.max(0, next) } : FRESH;
}

/** Send one mark, and once more when the first try fails. True when one try worked. */
export async function markWithRetry(send: () => Promise<unknown>, tries = 2): Promise<boolean> {
  for (let at = 0; at < tries; at += 1) {
    try {
      await send();
      return true;
    } catch {
      // A lost mark only means the items show as new again next time; try once more.
    }
  }
  return false;
}
