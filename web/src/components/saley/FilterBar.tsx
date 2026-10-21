// The list filters of Accounts and People (the legacy tabs) and their page search box.
// Filters are a group of toggle Buttons (`aria-pressed`, `[data-filter]`), never links. The
// search box asks the API (`q=`) after a short pause, as the legacy page did; Today and Q4
// goals have no page search (brief), so only these two pages use it.
import { SearchIcon } from "lucide-react";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export type FilterOption = { id: string; label: string };

const SEARCH_PAUSE_MS = 300;
const SEARCH_MAX = 100; // queries.MAX_Q

export function FilterBar({
  options,
  current,
  onPick,
  label,
}: {
  options: FilterOption[];
  current: string;
  onPick: (id: string) => void;
  label: string;
}) {
  return (
    <div role="group" aria-label={label} className="flex flex-wrap gap-1.5" data-filters>
      {options.map((option) => {
        const on = option.id === current;
        return (
          <Button
            key={option.id}
            type="button"
            size="sm"
            variant={on ? "secondary" : "ghost"}
            aria-pressed={on}
            data-filter={option.id}
            className={cn("tabular", on && "ring-border ring-1")}
            onClick={() => onPick(option.id)}
          >
            {option.label}
          </Button>
        );
      })}
    </div>
  );
}

/** A search input that reports its text after a pause (one API call per pause, not per key). */
export function SearchBox({
  id,
  label,
  hint,
  onSearch,
}: {
  id: string;
  label: string;
  hint: string;
  onSearch: (q: string) => void;
}) {
  const [text, setText] = useState("");
  useEffect(() => {
    const timer = window.setTimeout(() => onSearch(text.trim()), SEARCH_PAUSE_MS);
    return () => window.clearTimeout(timer);
  }, [text, onSearch]);
  return (
    <div className="relative w-full sm:w-72">
      <SearchIcon
        aria-hidden
        className="text-muted-foreground pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2"
      />
      <input
        id={id}
        type="search"
        aria-label={label}
        placeholder={hint}
        maxLength={SEARCH_MAX}
        autoComplete="off"
        spellCheck={false}
        value={text}
        onChange={(e) => setText(e.target.value)}
        className={cn(
          "border-input bg-background h-10 w-full rounded-md border pr-3 pl-9 text-sm",
          "placeholder:text-muted-foreground outline-none",
          "focus-visible:border-ring focus-visible:ring-ring/50 focus-visible:ring-[3px]",
        )}
      />
    </div>
  );
}
