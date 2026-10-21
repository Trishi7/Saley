// The dashboard v2 (R5), served at `/` since the cut-over (T16b, D224). T12 built the shell and
// the design system; T13–T16a ported every page into it. The DOM hooks follow docs/08
// Appendix B (plan §3.4): `.rail a[data-v]`, `section.view.on#v-<name>`, `#toast`, `#gsearch`,
// `#search-results`.
import { useEffect, type ReactNode } from "react";
import { Shell } from "@/components/saley/Shell";
import { SearchView } from "@/components/saley/SearchView";
import { Toaster } from "@/components/saley/Toaster";
import { ErrorLine, LoadingLine } from "@/components/saley/states";
import { resolveView, VIEWS, type ViewDef } from "@/components/saley/views";
import { TooltipProvider } from "@/components/ui/tooltip";
import { isAdmin, MeProvider, useMe } from "@/hooks/use-me";
import { useRoute } from "@/hooks/use-route";
import { AccountsPage } from "@/views/accounts/AccountsPage";
import { BlockersPage } from "@/views/blockers/BlockersPage";
import { CommitmentsPage } from "@/views/commitments/CommitmentsPage";
import { DecisionsPage } from "@/views/decisions/DecisionsPage";
import { EventsPage } from "@/views/events/EventsPage";
import { GoalsPage } from "@/views/goals/GoalsPage";
import { InboxPage } from "@/views/inbox/InboxPage";
import { MoneyPage } from "@/views/money/MoneyPage";
import { PackPage } from "@/views/pack/PackPage";
import { PeoplePage } from "@/views/people/PeoplePage";
import { ScorecardPage } from "@/views/scorecard/ScorecardPage";
import { SignalsPage } from "@/views/signals/SignalsPage";
import { SourcesPage } from "@/views/sources/SourcesPage";
import { SuggestPage } from "@/views/suggest/SuggestPage";
import { TodayPage } from "@/views/today/TodayPage";
import { W } from "@/words";

// Every page of the rail, plus Money blocked (Search has its own view below).
const PAGES: Record<string, (props: { view: ViewDef }) => ReactNode> = {
  today: TodayPage,
  goals: GoalsPage,
  accounts: AccountsPage,
  people: PeoplePage,
  suggest: SuggestPage,
  signals: SignalsPage,
  events: EventsPage,
  blockers: BlockersPage,
  money: MoneyPage,
  asks: InboxPage,
  commit: CommitmentsPage,
  decisions: DecisionsPage,
  pack: PackPage,
  scorecard: ScorecardPage,
  sources: SourcesPage,
};

function CurrentView() {
  const route = useRoute();
  const { me, error, reload } = useMe();
  // An admin-only page waits for /api/me before it decides (never a flash of Today). When
  // /api/me fails, the role is unknown: admin pages are not available and the error shows.
  const adminPage = VIEWS.some((v) => v.id === route.view && v.group === "admin");
  const waitsForRole = me === undefined && error === null && adminPage;
  const view = resolveView(route.view, isAdmin(me));
  const Page = PAGES[view.id];
  useEffect(() => {
    window.scrollTo(0, 0);
  }, [view.id]);
  return (
    <Shell view={view.id} q={route.params.q ?? ""}>
      {error !== null && <ErrorLine what={W.whatMe} error={error} onRetry={reload} />}
      {waitsForRole && <LoadingLine />}
      {!waitsForRole && view.id === "search" && <SearchView view={view} q={route.params.q ?? ""} />}
      {!waitsForRole && Page && <Page view={view} />}
    </Shell>
  );
}

export default function App() {
  return (
    <MeProvider>
      <TooltipProvider delayDuration={150}>
        <Toaster>
          <CurrentView />
        </Toaster>
      </TooltipProvider>
    </MeProvider>
  );
}
