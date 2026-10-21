// A whole card that is one control (brief: "whole card clickable, no Open link"; never an
// underlined URL). It is a real <a> (with href) or <button type="button"> (with onClick), so
// keyboard, focus ring and screen readers work without extra code. `kind` becomes `data-kind`
// (Needs a look cards: news, event, blocker, waiting). Other data-* props pass through.
import type { ComponentProps, ReactNode } from "react";
import { cn } from "@/lib/utils";

const CARD =
  "group/card bg-card text-card-foreground block w-full rounded-lg border p-4 text-left shadow-card outline-none " +
  "transition-[background-color,border-color,box-shadow,transform] duration-150 ease-out " +
  "hover:border-primary/35 hover:bg-accent/40 hover:shadow-card-hover " +
  "focus-visible:ring-ring/60 focus-visible:border-ring focus-visible:ring-[3px] active:scale-[0.995]";

type Common = { kind?: string; children: ReactNode; className?: string };
type AsLink = Common & { href: string } & Omit<ComponentProps<"a">, "href" | "className">;
type AsButton = Common & { href?: undefined } & Omit<ComponentProps<"button">, "className" | "type">;

export function ClickCard(props: AsLink | AsButton) {
  if (props.href !== undefined) {
    const { kind, children, className, ...rest } = props as AsLink;
    return (
      <a data-click-card data-kind={kind} className={cn(CARD, className)} {...rest}>
        {children}
      </a>
    );
  }
  const { kind, children, className, ...rest } = props as AsButton;
  return (
    <button
      type="button"
      data-click-card
      data-kind={kind}
      className={cn(CARD, className)}
      {...rest}
    >
      {children}
    </button>
  );
}
