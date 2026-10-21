// Links of the drawers. An external link (LinkedIn, website, paper) is a Button with an icon
// that opens a new tab with `rel="noopener noreferrer"`; its href is only a `safe_href` value
// from the API (people_model, D39), never built here, and nothing on the page fetches it.
// Long text that is cut to one line shows the whole text in a tooltip, also on keyboard focus.
import { ArrowUpRightIcon } from "lucide-react";
import type { ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

export function ExternalButton({
  href,
  label,
  children,
  icon,
}: {
  href: string;
  /** The accessible name, from W. */
  label: string;
  children: ReactNode;
  icon?: ReactNode;
}) {
  return (
    <Button asChild variant="outline" size="sm" className="max-w-full">
      <a href={href} target="_blank" rel="noopener noreferrer" aria-label={label} data-external>
        {icon}
        <span className="truncate">{children}</span>
        <ArrowUpRightIcon aria-hidden />
      </a>
    </Button>
  );
}

/** One line, cut with "…" by CSS; the full text in a tooltip. A dash when there is no text. */
export function OneLine({ text, className }: { text: string | null; className?: string }) {
  if (!text) return <span className="text-muted-foreground">—</span>;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span
          tabIndex={0}
          data-one-line
          className={cn(
            "focus-visible:ring-ring/50 block truncate rounded-sm outline-none focus-visible:ring-2",
            className,
          )}
        >
          {text}
        </span>
      </TooltipTrigger>
      <TooltipContent className="max-w-80 text-left">{text}</TooltipContent>
    </Tooltip>
  );
}
