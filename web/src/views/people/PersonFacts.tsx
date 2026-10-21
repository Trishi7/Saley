// The person drawer facts (R5-P01, R5-P03): "Follow-up by" with the rule tooltip (D31), the
// research papers as "N papers" with a popover of short links, and "Working on" with its
// source (the sheet column, or "note <date>"). Link text comes only from the API (`papers[]
// .text`, stored data, never invented); every href is a `safe_href` value from the API.
// Hooks: `[data-follow-up]`, `[data-follow-up-tip]`, `[data-papers]`, `[data-papers-list]`,
// `[data-working-on]`, `[data-working-source]`.
import { ArrowUpRightIcon, FileTextIcon, InfoIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { fmt, W } from "@/words";
import type { FollowUpBy, Paper, WorkingOn } from "./types";

const RULE = "D31"; // the rule that names who follows up, if the API leaves it out

/** The label with its tooltip: "Follow-up by" (i) -> "from rule D31". */
export function FollowUpLabel({ rule }: { rule: string | undefined }) {
  const tip = fmt(W.followUpTip, { rule: rule || RULE });
  return (
    <span className="inline-flex items-center gap-1" data-follow-up>
      {W.dFollowUp}
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            type="button"
            data-follow-up-tip={tip}
            aria-label={tip}
            className="text-muted-foreground hover:text-foreground focus-visible:ring-ring/50 inline-flex size-6 items-center justify-center rounded-full outline-none focus-visible:ring-2"
          >
            <InfoIcon aria-hidden className="size-3.5" />
          </button>
        </TooltipTrigger>
        <TooltipContent>{tip}</TooltipContent>
      </Tooltip>
    </span>
  );
}

/** The name and where it came from; no name on file: "—". */
export function FollowUpValue({ value }: { value: FollowUpBy | null }) {
  if (!value?.name) return null;
  return (
    <span className="flex flex-col">
      <span className="font-medium">{value.name}</span>
      {value.source && (
        <span className="text-muted-foreground text-xs">
          {fmt(W.sourceLine, { s: value.source })}
        </span>
      )}
    </span>
  );
}

export function Papers({ papers, label }: { papers: Paper[]; label: string }) {
  if (papers.length === 0) {
    return (
      <span className="text-muted-foreground tabular" data-papers>
        {label || "—"}
      </span>
    );
  }
  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button type="button" variant="outline" size="sm" data-papers className="tabular">
          <FileTextIcon aria-hidden />
          {label}
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-80 max-w-[calc(100vw-2rem)] p-1.5">
        <ul aria-label={W.papersList} data-papers-list className="flex flex-col">
          {papers.map((paper, i) => (
            <li key={`${i}-${paper.href}`}>
              <a
                href={paper.href}
                target="_blank"
                rel="noopener noreferrer"
                className="hover:bg-accent focus-visible:ring-ring/50 flex items-center gap-2 rounded-sm px-2 py-1.5 text-sm no-underline outline-none focus-visible:ring-2"
              >
                <span className="min-w-0 flex-1 truncate">{paper.text}</span>
                <ArrowUpRightIcon aria-hidden className="text-muted-foreground size-3.5 shrink-0" />
              </a>
            </li>
          ))}
        </ul>
      </PopoverContent>
    </Popover>
  );
}

/** What the person works on, with the source next to it. Nothing on file: "—". */
export function WorkingOnValue({ value }: { value: WorkingOn }) {
  if (!value.found) return null;
  return (
    <span className="flex flex-col gap-0.5" data-working-on>
      <span className="text-pretty">{value.text}</span>
      <span className="flex flex-wrap items-center gap-2">
        {value.source && (
          <span className="text-muted-foreground text-xs" data-working-source>
            {fmt(W.sourceLine, { s: value.source })}
          </span>
        )}
        {value.href && (
          <Button asChild variant="ghost" size="sm" className="h-6 px-1.5">
            <a
              href={value.href}
              target="_blank"
              rel="noopener noreferrer"
              aria-label={W.openSource}
              title={W.openSource}
            >
              <ArrowUpRightIcon aria-hidden />
            </a>
          </Button>
        )}
      </span>
    </span>
  );
}
