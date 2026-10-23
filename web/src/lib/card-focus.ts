// Deep links from a Discord card (docs/09 §7, D261): `/#people?open=412&card=m5-c412-20261024`.
// The page opens the item (the People and Accounts drawers read `open`); this finds it, scrolls
// to it and outlines it for 3 s, so the eye lands on the card the link was about.

const ID = /^[A-Za-z0-9-]{1,40}$/;
const CARD = /^[a-z0-9:-]{1,100}$/;
export const OUTLINE_MS = 3000;
const WAIT_MS = 5000;
const STEP_MS = 100;

/** The element to outline: the open drawer or the item's row, else the page content.
 *  null when the link carries no valid card id (a typed or broken link outlines nothing). */
export function focusSelector(card?: string, open?: string): string | null {
  if (!card || !CARD.test(card)) return null;
  if (open && ID.test(open)) {
    return [
      `[data-drawer].on`,
      `[data-person="${open}"]`,
      `[data-account="${open}"]`,
      `[data-id="${open}"]`,
    ].join(", ");
  }
  return "[data-content]";
}

/** Wait for the element (pages load their data first), then outline it. Returns a cancel. */
export function focusCard(card?: string, open?: string): () => void {
  const selector = focusSelector(card, open);
  if (selector === null) return () => {};
  let timer = 0;
  let undo = 0;
  const started = Date.now();
  const look = () => {
    const found = document.querySelector<HTMLElement>(selector);
    if (found) {
      found.scrollIntoView({ block: "center", behavior: "smooth" });
      found.setAttribute("data-card-focus", "");
      undo = window.setTimeout(() => found.removeAttribute("data-card-focus"), OUTLINE_MS);
    } else if (Date.now() - started < WAIT_MS) {
      timer = window.setTimeout(look, STEP_MS);
    }
  };
  look();
  return () => {
    window.clearTimeout(timer);
    window.clearTimeout(undo);
  };
}

/** A numeric item id from the link, for a page that opens a drawer by id. */
export function openId(open?: string): number | null {
  return open && /^[0-9]{1,9}$/.test(open) ? Number(open) : null;
}
