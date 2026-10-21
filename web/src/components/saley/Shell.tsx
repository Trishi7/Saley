// The shell (docs/08 Shell, R5 T12): rail on the left, top bar, then one view.
// At phone width (below 768 px) the rail folds into a menu Button that opens it in a Sheet,
// so the page never scrolls sideways (docs/08: "Phone readable"; D198).
import { useState, type ReactNode } from "react";
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";
import { useMe } from "@/hooks/use-me";
import { useStatus } from "@/hooks/use-status";
import { fmt, W } from "@/words";
import { RailNav } from "./ShellRail";
import { ShellTopBar } from "./ShellTopBar";
import { ChangedBanner } from "./states";

function Brand() {
  return (
    <p className="flex items-baseline gap-2 px-2.5">
      <span className="text-lg font-semibold tracking-tight">{W.appName}</span>
      <span className="text-muted-foreground text-xs">{W.appTagline}</span>
    </p>
  );
}

export function Shell({
  view,
  q,
  children,
}: {
  view: string;
  q: string;
  children: ReactNode;
}) {
  const [menu, setMenu] = useState(false);
  const { status, error, retry } = useStatus();
  const { me } = useMe();
  const blockers = status?.late_total ?? null;
  return (
    <div className="flex min-h-screen">
      {/* The aside stretches with the page, so its colour and border run to the foot of a long
          page (R5 T15 bug: the rail stopped after the first screen). The inner box sticks to
          the top and scrolls on its own when the rail is taller than the window. */}
      <aside data-rail className="bg-rail hidden w-56 shrink-0 self-stretch border-r md:block">
        <div className="sticky top-0 flex h-dvh flex-col gap-6 overflow-y-auto px-2 py-4">
          <Brand />
          <RailNav current={view} blockers={blockers} />
        </div>
      </aside>
      <Sheet open={menu} onOpenChange={setMenu}>
        <SheetContent side="left" className="bg-rail w-72 gap-5 overflow-y-auto px-2 py-4">
          <SheetTitle asChild>
            <div>
              <Brand />
            </div>
          </SheetTitle>
          <SheetDescription className="px-2.5">
            {me ? fmt(W.signedInAs, { name: me.email }) : W.loading}
          </SheetDescription>
          <RailNav current={view} blockers={blockers} onPick={() => setMenu(false)} />
        </SheetContent>
      </Sheet>
      <div className="flex min-w-0 flex-1 flex-col">
        <ShellTopBar
          q={q}
          status={status}
          statusError={error}
          onRetry={retry}
          onMenu={() => setMenu(true)}
        />
        <ChangedBanner />
        <main id="main" tabIndex={-1} className="mx-auto w-full max-w-6xl flex-1 px-4 py-6 outline-none sm:px-6 sm:py-8">
          {children}
        </main>
      </div>
    </div>
  );
}
