"""Google Drive reader for the meeting-notes folder (D81, Q-R2-4 revised). READ ONLY.

Why: the team keeps meeting notes in one Drive folder, shared with the service account as
Viewer. This module lists that folder (and its subfolders) and reads file bytes. Nothing more:
there is no create, update, delete or share call in this file, and the token has the
`drive.readonly` scope, so Google would refuse a write anyway.

Every call goes through `saley.core.egress`: the API calls through an httpx client with the
egress transport, and the token request through a small `Request` that asks the egress policy
first. File text is DATA. It goes to the one note parser and is never followed as an instruction.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Protocol
from urllib.parse import quote, urlsplit

import httpx
from google.auth import exceptions as google_exceptions
from google.auth.transport import requests as google_requests
from google.oauth2 import service_account

from saley.core import egress
from saley.core.logging import get_logger
from saley.core.settings import MissingSettings, get_settings, is_missing
from saley.intel import notes

log = get_logger("saley.intel.drive")

API_ROOT = "https://www.googleapis.com/drive/v3"
READONLY_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
FOLDER_MIME = "application/vnd.google-apps.folder"
GDOC_MIME = "application/vnd.google-apps.document"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
LIST_FIELDS = "nextPageToken,files(id,name,mimeType,modifiedTime,size,trashed)"
PAGE_SIZE = 200
MAX_PAGES = 100
MAX_DEPTH = 5  # folders below the notes folder
MAX_FILES = 500  # note files kept per sync
MAX_ITEMS = 5000  # every non-folder item looked at, notes or not
API_OFF_REASONS = frozenset({"accessNotConfigured", "SERVICE_DISABLED"})
# A 403 with one of these is Google slowing us down, not a folder that is not shared (B10).
RATE_LIMIT_REASONS = frozenset(
    {
        "rateLimitExceeded",
        "userRateLimitExceeded",
        "dailyLimitExceeded",
        "sharingRateLimitExceeded",
        "quotaExceeded",
        "backendError",
        "internalError",
    }
)
# Only these (or a 404) mean "the folder is not shared with the service account". Any other
# 403 reason is a different failure and shows as `error` (silent failure 2).
PERMISSION_REASONS = frozenset(
    {
        "forbidden",
        "insufficientFilePermissions",
        "insufficientPermissions",
        "notFound",
        "appNotAuthorizedToFile",
        "http_403",
        "http_404",
    }
)
MAX_JSON_BYTES = 5_000_000  # a listing page or a folder record; far above a real one
URL = re.compile(r"https://[^\s\"'<>]+")
GOOGLE_HOST_ENDS = (".google.com", ".googleapis.com")


class DriveError(Exception):
    """A Drive call failed. `status` 0 is a network error. Never holds a token."""

    def __init__(
        self, status: int, reason: str, message: str = "", activation_url: str | None = None
    ) -> None:
        super().__init__(f"drive {status} {reason}")
        self.status = status
        self.reason = reason
        self.message = message
        self.activation_url = activation_url

    @property
    def api_disabled(self) -> bool:
        """The Drive API is not enabled in the service account's Google Cloud project."""
        return self.reason in API_OFF_REASONS

    @property
    def not_shared(self) -> bool:
        """404, or a 403 with a permission reason: the folder is not shared with Saley."""
        return self.status == 404 or (self.status == 403 and self.reason in PERMISSION_REASONS)

    @property
    def transient(self) -> bool:
        if self.status == 403 and self.reason in RATE_LIMIT_REASONS:
            return True
        return self.status in (0, 429) or self.status >= 500


@dataclass(frozen=True)
class DriveFile:
    id: str
    name: str
    mime: str
    modified: datetime
    size: int | None = None
    trashed: bool = False
    path: str = ""


class DriveClient(Protocol):
    """What the sync needs from Drive. Tests use a fake with these three methods."""

    def check_folder(self, folder_id: str) -> None: ...

    def list_folder(self, folder_id: str) -> list[DriveFile]: ...

    def read(self, file: DriveFile) -> bytes: ...


def note_kind(file: DriveFile) -> str | None:
    """gdoc, pdf, docx or text for a file Saley can read as a note. None for anything else."""
    name = file.name.lower()
    if file.mime == GDOC_MIME:
        return "gdoc"
    if name.endswith(".pdf") or file.mime == "application/pdf":
        return "pdf"
    if name.endswith(".docx") or file.mime == DOCX_MIME:
        return "docx"
    if name.endswith((".md", ".txt")) or file.mime in ("text/markdown", "text/plain"):
        return "text"
    return None


# ------------------------------------------------------------------ the walk


@dataclass
class Walk:
    files: list[DriveFile] = field(default_factory=list)
    skipped: int = 0  # items that are not notes (images, sheets, ...)
    problems: list[str] = field(default_factory=list)  # subfolders that could not be listed
    complete: bool = True  # False when a cap or an error cut the listing
    cut: str = ""  # why it was cut when no subfolder is named: "depth" or "file cap"


class _Walker:
    def __init__(self, client: DriveClient, root_id: str) -> None:
        self.client = client
        self.out = Walk()
        self.seen = {root_id}  # a folder is listed once, so a loop ends
        self.items = 0
        self.stop = False

    def visit(self, folder_id: str, prefix: str, depth: int) -> None:
        try:
            children = self.client.list_folder(folder_id)
        except DriveError as exc:
            if depth == 0 or exc.transient:
                raise
            self.out.problems.append(prefix.rstrip("/"))
            self.out.complete = False
            return
        for child in children:
            if self.stop:
                return
            if child.trashed:
                continue
            if child.mime == FOLDER_MIME:
                self.enter(child, prefix, depth)
            else:
                self.keep(child, prefix)

    def enter(self, folder: DriveFile, prefix: str, depth: int) -> None:
        if folder.id in self.seen:
            return
        if depth + 1 > MAX_DEPTH:
            self.out.complete = False
            self.out.cut = self.out.cut or "folders too deep"
            return
        self.seen.add(folder.id)
        self.visit(folder.id, f"{prefix}{folder.name}/", depth + 1)

    def keep(self, file: DriveFile, prefix: str) -> None:
        self.items += 1
        if self.items > MAX_ITEMS or (note_kind(file) and len(self.out.files) >= MAX_FILES):
            self.out.complete = False
            self.out.cut = self.out.cut or "too many files"
            self.stop = True
            return
        if note_kind(file) is None:
            self.out.skipped += 1
            return
        self.out.files.append(replace(file, path=f"{prefix}{file.name}"))


def walk(client: DriveClient, root_id: str) -> Walk:
    """Every note file under the folder, subfolders included, with a depth and size cap.

    A failure on the folder itself raises DriveError (403/404 = not shared). A failure on a
    subfolder is a problem line and marks the listing incomplete, so nothing is called gone.
    """
    client.check_folder(root_id)
    walker = _Walker(client, root_id)
    walker.visit(root_id, "", 0)
    return walker.out


# ------------------------------------------------------------------ errors


def _google_url(url: str | None) -> str | None:
    """Only an https Google address may be shown as the enable link (data is not trusted)."""
    if not url:
        return None
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme == "https" and host.endswith(GOOGLE_HOST_ENDS):
        return url
    return None


def error_from_response(response: httpx.Response) -> DriveError:
    """Turn an error answer into a DriveError, including the API-off reason and enable URL."""
    status = response.status_code
    error: dict[str, Any] = {}
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        error = body["error"]
    message = str(error.get("message") or "")[:300]
    reasons: list[str] = []
    url: str | None = None
    for item in error.get("errors") or []:
        if isinstance(item, dict) and isinstance(item.get("reason"), str):
            reasons.append(item["reason"])
    for item in error.get("details") or []:
        if not isinstance(item, dict):
            continue
        if isinstance(item.get("reason"), str):
            reasons.append(item["reason"])
        meta = item.get("metadata")
        if isinstance(meta, dict) and isinstance(meta.get("activationUrl"), str):
            url = url or meta["activationUrl"]
    api_off = any(r in API_OFF_REASONS for r in reasons)
    if api_off and url is None and (found := URL.search(message)):
        url = found.group(0).rstrip(".,;)")
    reason = "accessNotConfigured" if api_off else (reasons[0] if reasons else f"http_{status}")
    return DriveError(status, reason, message, _google_url(url) if api_off else None)


# ------------------------------------------------------------------ the real client


def _q_quote(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _parse_file(item: dict[str, Any]) -> DriveFile:
    """One listing item. A missing id or modifiedTime is a bad answer, not a crash (silent 3)."""
    try:
        size = item.get("size")
        return DriveFile(
            id=str(item["id"]),
            name=str(item.get("name") or item["id"]),
            mime=str(item.get("mimeType") or ""),
            modified=datetime.fromisoformat(str(item["modifiedTime"]).replace("Z", "+00:00")),
            size=int(size) if size is not None else None,
            trashed=bool(item.get("trashed")),
        )
    except (KeyError, ValueError, TypeError, AttributeError) as exc:
        raise DriveError(502, "bad_listing", "a file record is not complete") from exc


def _json(response: httpx.Response) -> dict[str, Any]:
    """The JSON object of a 200 answer. Anything else is a DriveError (silent failure 3)."""
    try:
        body = response.json()
    except ValueError as exc:
        raise DriveError(502, "bad_json", "the answer is not JSON") from exc
    if not isinstance(body, dict):
        raise DriveError(502, "bad_json", "the answer is not a JSON object")
    return body


class HttpDriveClient:
    """Drive API v3 over an egress-guarded httpx client. GET only."""

    def __init__(
        self, http: httpx.Client, token: Callable[[], str], account: str | None = None
    ) -> None:
        self._http = http
        self._token = token
        # The service account address, so "folder not shared" can name who to share it with.
        self.account = account

    def _get(
        self, url: str, params: dict[str, Any], max_bytes: int | None = None
    ) -> httpx.Response:
        # The body is streamed and cut at max_bytes (B1): a file of any size cannot
        # fill memory before the size is looked at.
        try:
            response = egress.request_capped(
                self._http,
                "GET",
                url,
                max_bytes=max_bytes or MAX_JSON_BYTES,
                params=params,
                headers={"Authorization": f"Bearer {self._token()}"},
            )
        except google_exceptions.RefreshError as exc:  # the key or the clock is wrong
            raise DriveError(401, "auth_failed", type(exc).__name__) from exc
        except google_exceptions.TransportError as exc:  # the token call had no network
            raise DriveError(0, "network", type(exc).__name__) from exc
        except egress.BodyTooLarge as exc:
            raise DriveError(502, "body_too_large", "answer over the size cap") from exc
        except httpx.TransportError as exc:
            raise DriveError(0, "network", type(exc).__name__) from exc
        if response.status_code >= 400:
            raise error_from_response(response)
        return response

    def check_folder(self, folder_id: str) -> None:
        """Fails with 403/404 when the folder is not shared with the service account."""
        params = {"fields": "id,mimeType", "supportsAllDrives": "true"}
        meta = _json(self._get(f"{API_ROOT}/files/{quote(folder_id, safe='')}", params))
        if meta.get("mimeType") != FOLDER_MIME:
            raise DriveError(400, "notAFolder", "The id is not a folder")

    def list_folder(self, folder_id: str) -> list[DriveFile]:
        files: list[DriveFile] = []
        token: str | None = None
        for _ in range(MAX_PAGES):
            params: dict[str, Any] = {
                "q": f"'{_q_quote(folder_id)}' in parents and trashed = false",
                "fields": LIST_FIELDS,
                "pageSize": PAGE_SIZE,
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
            }
            if token:
                params["pageToken"] = token
            page = _json(self._get(f"{API_ROOT}/files", params))
            files.extend(_parse_file(item) for item in page.get("files", []))
            token = page.get("nextPageToken")
            if not token:
                break
        return files

    def read(self, file: DriveFile) -> bytes:
        file_url = f"{API_ROOT}/files/{quote(file.id, safe='')}"
        cap = notes.MAX_FILE_BYTES
        try:
            if note_kind(file) == "gdoc":  # the export is capped the same way
                response = self._get(f"{file_url}/export", {"mimeType": "text/plain"}, cap)
            else:
                response = self._get(file_url, {"alt": "media", "supportsAllDrives": "true"}, cap)
        except DriveError as exc:
            if exc.reason == "body_too_large":
                raise notes.NoteUnreadable("too big") from exc
            raise
        return response.content


class GuardedRequest(google_requests.Request):
    """google-auth transport that asks the egress policy before the token call."""

    def __call__(  # type: ignore[override]
        self,
        url: str,
        method: str = "GET",
        body: Any = None,
        headers: Any = None,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> Any:
        policy = egress.get_policy()
        host = policy.guard(url)
        try:
            response = super().__call__(
                url,
                method,
                body,
                headers,
                timeout=timeout or get_settings().http_timeout_seconds,
                **kwargs,
            )
        except Exception:
            policy.record(host, ok=False)
            raise
        policy.record(host, ok=response.status < 400)
        return response


def token_provider(credentials: Any) -> Callable[[], str]:
    """A function that returns a valid access token, refreshing it through the egress door."""
    request = GuardedRequest()

    def token() -> str:
        if not credentials.valid:
            credentials.refresh(request)
        return str(credentials.token)

    return token


def build_client() -> HttpDriveClient:
    """The Drive client with the same service account file as the sheet, read-only scope."""
    settings = get_settings()
    if is_missing(settings.google_service_account_file):
        raise MissingSettings("notes needs these settings in .env: GOOGLE_SERVICE_ACCOUNT_FILE")
    credentials = service_account.Credentials.from_service_account_file(
        settings.google_service_account_file, scopes=[READONLY_SCOPE]
    )
    return HttpDriveClient(
        egress.make_client(),
        token_provider(credentials),
        getattr(credentials, "service_account_email", None),
    )
