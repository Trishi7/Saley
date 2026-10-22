// The top bar on every page (docs/08 Shell): the signed-in person, global search, the
// "Updated HH:MM IST · sheet HH:MM · news HH:MM" line (red per D43), the theme toggle (D163).
// D207: no "View as" and no person picker in any mode; every page is the signed-in person's.
// A failed status load names the data, gives the reason and a Retry Button (`data-retry`).
// Hooks kept from the legacy page: #me, #gsearch, #updated, #pending, #signout.
import { LogOutIcon, MenuIcon, MoonIcon, SearchIcon, SunIcon } from "lucide-react";
import { useState, type KeyboardEvent } from "react";
import { loadErrorText, RetryButton } from "@/components/saley/states";
import { Button } from "@/components/ui/button";
import { useMe } from "@/hooks/use-me";
import type { Status } from "@/hooks/use-status";
import { go } from "@/lib/route";
import { cn } from "@/lib/utils";
import { currentTheme, saveTheme, type Theme } from "@/theme";
import { fmt, W } from "@/words";

const SEARCH_MAX = 100; // docs/08 Appendix A: q is at most 100 characters

function GlobalSearch({ q }: { q: string }) {
  const [draft, setDraft] = useState<{ for: string; text: string }>({ for: q, text: q });
  // A new route q (Back, a pasted link) replaces the box text; typing keeps its own text.
  const text = draft.for === q ? draft.text : q;
  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" && text.trim()) go("search", { q: text.trim() });
  };
  return (
    <label className="relative flex min-w-0 flex-1 items-center sm:max-w-md">
      <SearchIcon
        aria-hidden
        className="text-muted-foreground pointer-events-none absolute left-2.5 size-4"
      />
      <input
        id="gsearch"
        type="search"
        maxLength={SEARCH_MAX}
        autoComplete="off"
        aria-label={W.searchLabel}
        placeholder={W.searchAll}
        value={text}
        onChange={(e) => setDraft({ for: q, text: e.target.value })}
        onKeyDown={onKey}
        className="bg-background placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-ring/50 h-9 w-full min-w-0 rounded-md border pr-3 pl-8 text-sm outline-none focus-visible:ring-[3px]"
      />
    </label>
  );
}

function StatusLine({
  status,
  error,
  onRetry,
}: {
  status: Status | null;
  error: string | null;
  onRetry: () => void;
}) {
  const bad = error !== null || (status !== null && status.state !== "ok");
  const line = error !== null ? loadErrorText(W.whatStatus, error) : (status?.message ?? W.loading);
  return (
    <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 text-xs">
      <span
        id="updated"
        role="status"
        data-state={error !== null ? "error" : (status?.state ?? "loading")}
        className={cn("status nums flex min-w-0 items-center gap-1.5", bad ? "bad text-bad" : "ok text-muted-foreground")}
      >
        <span
          aria-hidden
          className={cn("size-1.5 shrink-0 rounded-full", bad ? "bg-bad" : "bg-good")}
        />
        <span className="min-w-0">{line}</span>
      </span>
      {error !== null && <RetryButton onRetry={onRetry} />}
      {status?.write_note && (
        <span
          id="pending"
          role="status"
          className={cn("status", status.write_bad ? "bad text-bad" : "pending text-warn")}
        >
          {status.write_note}
        </span>
      )}
    </div>
  );
}

function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(currentTheme);
  const next: Theme = theme === "dark" ? "light" : "dark";
  return (
    <Button
      type="button"
      variant="ghost"
      size="icon"
      data-theme-toggle
      data-theme-now={theme}
      aria-label={next === "dark" ? W.themeToDark : W.themeToLight}
      title={W.themeToggle}
      onClick={() => {
        saveTheme(next);
        setTheme(next);
      }}
    >
      {theme === "dark" ? <SunIcon aria-hidden /> : <MoonIcon aria-hidden />}
    </Button>
  );
}

function signOut(): void {
  // As the legacy page: the session cookie is cleared by the server, then the sign-in page.
  void fetch("/auth/signout", { method: "POST", credentials: "same-origin" }).finally(() =>
    window.location.assign("/signin"),
  );
}

export function ShellTopBar({
  q,
  status,
  statusError,
  onRetry,
  onMenu,
}: {
  q: string;
  status: Status | null;
  statusError: string | null;
  onRetry: () => void;
  onMenu: () => void;
}) {
  const { me } = useMe();
  return (
    <header className="bg-background/90 sticky top-0 z-40 flex flex-col gap-2 border-b px-4 py-2.5 backdrop-blur-sm sm:px-6">
      <div className="flex items-center gap-2">
        <Button
          type="button"
          variant="ghost"
          size="icon"
          data-menu
          className="md:hidden"
          aria-label={W.menuOpen}
          onClick={onMenu}
        >
          <MenuIcon aria-hidden />
        </Button>
        <GlobalSearch q={q} />
        <div className="ml-auto flex shrink-0 items-center gap-1">
          <span id="me" className="text-muted-foreground hidden max-w-56 truncate text-sm sm:inline">
            {me ? fmt(W.signedInAs, { name: me.email }) : ""}
          </span>
          <ThemeToggle />
          <Button
            type="button"
            id="signout"
            variant="ghost"
            size="icon"
            aria-label={W.signOut}
            title={W.signOut}
            onClick={signOut}
          >
            <LogOutIcon aria-hidden />
          </Button>
        </div>
      </div>
      <StatusLine status={status} error={statusError} onRetry={onRetry} />
    </header>
  );
}
