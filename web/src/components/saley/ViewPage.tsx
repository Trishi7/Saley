// One page of the dashboard: the section with the Appendix B hook `section.view.on#v-<name>`,
// the title, the one-line subtitle (docs/08 Shell), the legend at the top (R5-Q01), the
// "coming soon" badges of the page's off features (R5-Q03), then the body.
import type { ReactNode } from "react";
import { SoonStrip } from "./ComingSoon";
import { Legend } from "./Legend";
import type { ViewDef } from "./views";

export function ViewPage({
  view,
  title,
  sub,
  children,
}: {
  view: ViewDef;
  /** Overrides for pages whose heading depends on the data (Search: "Results for …"). */
  title?: string;
  sub?: string;
  children: ReactNode;
}) {
  return (
    <section
      id={`v-${view.id}`}
      data-features={view.features.join(" ")}
      className="view on flex flex-col gap-5"
      aria-labelledby="view-title"
    >
      <header className="flex flex-col gap-1">
        <h1 id="view-title" className="text-2xl font-semibold tracking-tight">
          {title ?? view.title}
        </h1>
        {(sub ?? view.sub) && <p className="text-muted-foreground text-sm">{sub ?? view.sub}</p>}
      </header>
      <Legend items={view.legend} />
      <SoonStrip features={view.features} />
      {children}
    </section>
  );
}
