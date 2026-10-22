// Long lists: 10 per page with an "N more" count (brief; R5-Q07). The list is the children;
// each row in it carries `data-row`, so the browser check counts rows the same way on every page.
// Hooks: `[data-pager]` (with data-total, data-offset, data-shown), `[data-pager-list]`,
// `[data-pager-more]`. Previous and Next are Buttons (keyboard reachable), never links.
import { ChevronLeftIcon, ChevronRightIcon } from "lucide-react";
import { useEffect, type ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { fmt, W } from "@/words";

export const PAGE_SIZE = 10;

export type PagerState = {
  /** The offset to show: an offset past the end moves back to the last page. */
  offset: number;
  more: number;
  canPrev: boolean;
  canNext: boolean;
  prevOffset: number;
  nextOffset: number;
};

/** An offset at or past the end (the list got shorter, e.g. items moved to Seen earlier)
 *  becomes the start of the last page; a negative offset becomes 0. */
export function clampOffset(total: number, offset: number, size: number = PAGE_SIZE): number {
  if (offset <= 0 || total <= 0) return 0;
  if (offset < total) return offset;
  return Math.floor((total - 1) / size) * size;
}

/** Pure paging maths: `more` is never below 0, offsets stay inside the list. */
export function pagerState(
  total: number,
  offset: number,
  shown: number,
  size: number = PAGE_SIZE,
): PagerState {
  const at = clampOffset(total, offset, size);
  const more = Math.max(0, total - at - shown);
  return {
    offset: at,
    more,
    canPrev: at > 0,
    canNext: more > 0,
    prevOffset: Math.max(0, at - size),
    nextOffset: at + size,
  };
}

export function Pager({
  total,
  offset,
  shown,
  onMove,
  label,
  children,
  className,
}: {
  total: number;
  offset: number;
  /** Rows on this page (at most PAGE_SIZE). */
  shown: number;
  onMove: (offset: number) => void;
  /** What the list holds, for screen readers ("Your tasks"). */
  label: string;
  children: ReactNode;
  className?: string;
}) {
  const state = pagerState(total, offset, shown);
  const paged = state.canPrev || state.canNext;
  // The caller fetched past the end: ask it for the last page instead (once per change).
  useEffect(() => {
    if (state.offset !== offset) onMove(state.offset);
  }, [state.offset, offset, onMove]);
  return (
    <div
      data-pager
      data-total={total}
      data-offset={state.offset}
      data-shown={shown}
      className={cn("flex flex-col gap-3", className)}
    >
      <div data-pager-list>{children}</div>
      {paged && (
        <nav aria-label={label} className="flex flex-wrap items-center justify-between gap-2">
          <span data-pager-more className="text-muted-foreground nums text-sm">
            {state.more > 0
              ? fmt(W.pagerMore, { n: state.more })
              : fmt(W.pagerRange, { a: state.offset + 1, b: state.offset + shown, n: total })}
          </span>
          <span className="flex gap-1">
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={!state.canPrev}
              onClick={() => onMove(state.prevOffset)}
            >
              <ChevronLeftIcon aria-hidden />
              {W.pagePrevious}
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={!state.canNext}
              onClick={() => onMove(state.nextOffset)}
            >
              {W.pageNext}
              <ChevronRightIcon aria-hidden />
            </Button>
          </span>
        </nav>
      )}
    </div>
  );
}
