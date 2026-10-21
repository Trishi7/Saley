// Shared drawer behaviour (R5 T14 review): the last subject stays rendered while the sheet
// slides out (no empty sheet on close), and focus goes back to the control that opened it.
// Radix restores focus to the element focused before the sheet opened; that is <body> after
// a row click, and a closed sheet after the company -> person hand-off, so the pages name
// the control themselves.
import { useState } from "react";

/** The value while it is set, else the last value it had (for the exit animation). */
export function useLastShown<T>(value: T | null): T | null {
  const [last, setLast] = useState<T | null>(value);
  if (value !== null && value !== last) setLast(value);
  return value ?? last;
}

/** onCloseAutoFocus: `undefined` keeps the Radix default; `null` leaves focus where it is
 *  (another drawer takes over); an element in the page gets the focus. */
export function returnFocus(event: Event, target: HTMLElement | null | undefined): void {
  if (target === undefined) return;
  if (target === null) {
    event.preventDefault();
    return;
  }
  if (!target.isConnected) return;
  event.preventDefault();
  target.focus();
}
