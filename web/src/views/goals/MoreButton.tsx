// "+N more" (brief, Q4 goals): a Button that opens the hidden rows in place, and "Show less"
// closes them again. `[data-more]` carries the hidden count for the browser check.
import { ChevronDownIcon, ChevronUpIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { fmt, W } from "@/words";

export function MoreButton({
  hidden,
  open,
  onToggle,
  controls,
}: {
  hidden: number;
  open: boolean;
  onToggle: () => void;
  /** The id of the list it opens. */
  controls: string;
}) {
  if (hidden <= 0) return null;
  return (
    <Button
      type="button"
      variant="ghost"
      size="sm"
      data-more={hidden}
      aria-expanded={open}
      aria-controls={controls}
      onClick={onToggle}
      className="text-primary hover:text-primary w-fit"
    >
      {open ? <ChevronUpIcon aria-hidden /> : <ChevronDownIcon aria-hidden />}
      {open ? W.showLess : fmt(W.showMore, { n: hidden })}
    </Button>
  );
}
