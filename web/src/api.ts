// The one door to the Saley API for the dashboard v2. It mirrors the legacy static/app.js
// and static/act.js so the server sees the same calls (plan §3.1):
// - same origin only, cookie session (`credentials: "same-origin"`, D114);
// - D40: after the first answer every GET pins `sync_id`; a 409 means the sheet changed, and
//   the page shows the "changed" banner instead of mixing two snapshots;
// - a 401 means the session ended: go to /signin;
// - every POST carries `X-Saley-Act: 1` and goes only to an allowed path (gate step 9);
// - a refusal shows the server's own fixed line (words.py), else a calm line from W;
// - a GET the server does not answer (fetch throws, e.g. "Failed to fetch") is status 0 with
//   W.noServer: the page names the data and offers Retry, never the raw browser text.
import { W } from "./words";

const STATUS_PATH = "/api/status";
const SEEN_PATH = "/api/seen";
const ASK_PATH = "/api/ask";
// The control POSTs of gate step 9 (scripts/verify.py ALLOWED_POST): the admin "Run news now"
// (D150) is one of them since R5 T16a.
const CONTROL_PATH =
  /^\/api\/(act|undo\/\d+|decision\/\d+\/confirm|proposal\/\d+\/(dismiss|edit)|commitment\/\d+\/done|admin\/news\/run)$/;
// 429: the news run cooldown ("Wait N min before the next news run.") is a server line too.
const SERVER_LINE_STATUSES = new Set([403, 404, 409, 422, 429]);
const ERROR_LINE: Record<number, string> = {
  403: W.errForbidden,
  404: W.errGone,
  409: W.errConflict,
  422: W.errInvalid,
};
const MAX_LINE = 120;

/** An API failure with a line that is safe to show as it is. */
export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, line: string) {
    super(line);
    this.status = status;
  }
}

let syncId: number | null = null;
const changedListeners = new Set<() => void>();

/** Run `listener` when the API says the sheet snapshot changed (409, D40). */
export function onChanged(listener: () => void): () => void {
  changedListeners.add(listener);
  return () => changedListeners.delete(listener);
}

function announceChanged(): void {
  changedListeners.forEach((listener) => listener());
}

function pinned(path: string): string {
  if (syncId === null || path.startsWith(STATUS_PATH)) return path;
  return `${path}${path.includes("?") ? "&" : "?"}sync_id=${syncId}`;
}

async function lineFor(response: Response): Promise<string> {
  const body: unknown = await response.json().catch(() => ({}));
  const detail = (body as { detail?: unknown }).detail;
  if (
    SERVER_LINE_STATUSES.has(response.status) &&
    typeof detail === "string" &&
    detail.length <= MAX_LINE
  ) {
    return detail;
  }
  return ERROR_LINE[response.status] ?? W.errGeneric;
}

// As the legacy app: a 409 on a GET means the snapshot moved (banner); a 409 on a POST is
// the server's own line ("Already done by X at HH:MM"), shown by the caller.
function handleSession(response: Response, isGet: boolean): void {
  if (response.status === 401) window.location.assign("/signin");
  if (response.status === 409 && isGet) announceChanged();
}

/** GET a JSON view. The first answer with a sync_id pins the snapshot for the next calls. */
export async function getJson<T>(path: string): Promise<T> {
  let response: Response;
  try {
    response = await fetch(pinned(path), {
      headers: { Accept: "application/json" },
      credentials: "same-origin",
    });
  } catch {
    throw new ApiError(0, W.noServer);
  }
  handleSession(response, true);
  if (!response.ok) throw new ApiError(response.status, await lineFor(response));
  const body = (await response.json()) as T & { sync_id?: number | null };
  if (syncId === null && body.sync_id != null && !path.startsWith(STATUS_PATH)) {
    syncId = body.sync_id;
  }
  return body;
}

function allowedPost(path: string): boolean {
  return CONTROL_PATH.test(path) || path === SEEN_PATH || path === ASK_PATH;
}

/** POST from a control. Any path outside the allow list is refused before it is sent. */
export async function postJson<T>(path: string, body?: unknown): Promise<T> {
  if (!allowedPost(path)) throw new ApiError(0, W.errInvalid);
  const headers: Record<string, string> = { Accept: "application/json", "X-Saley-Act": "1" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  let response: Response;
  try {
    response = await fetch(path, {
      method: "POST",
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      credentials: "same-origin",
    });
  } catch {
    throw new ApiError(0, W.errNetwork);
  }
  handleSession(response, false);
  if (!response.ok) throw new ApiError(response.status, await lineFor(response));
  return (await response.json()) as T;
}
