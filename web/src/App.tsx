// The dashboard v2 (R5). T12 builds the shell and the design system; T13–T16 port the pages
// into it (T13: Today; T14: Q4 goals, Accounts, People; T15: Suggestions, Signals, Events,
// Blockers, Money blocked, and Search hits that open the drawers). The DOM hooks follow docs/08
// Appendix B (plan §3.4): `.rail a[data-v]`, `section.view.on#v-<name>`, `#toast`, `#gsearch`,
// `#search-results`.
import { useEffect, type ReactNode } from "react";
import { Shell } from "@/components/saley/Shell";
import { SearchView } from "@/components/saley/SearchView";
import { Toaster } from "@/components/saley/Toaster";
import { ErrorLine, LoadingLine } from "@/components/saley/states";
import { resolveView, VIEWS, type ViewDef } from "@/components/saley/views";
import { NotPortedYet, ViewPage } from "@/components/saley/ViewPage";
import { TooltipProvider } from "@/components/ui/tooltip";
import { isAdmin, MeProvider, useMe } from "@/hooks/use-me";
import { useRoute } from "@/hooks/use-route";
import { AccountsPage } from "@/views/accounts/AccountsPage";
import { BlockersPage } from "@/views/blockers/BlockersPage";
import { EventsPage } from "@/views/events/EventsPage";
import { GoalsPage } from "@/views/goals/GoalsPage";
import { MoneyPage } from "@/views/money/MoneyPage";
import { PeoplePage } from "@/views/people/PeoplePage";
import { SignalsPage } from "@/views/signals/SignalsPage";
import { SuggestPage } from "@/views/suggest/SuggestPage";
import { TodayPage } from "@/views/today/TodayPage";
import { W } from "@/words";

// The pages ported to the new design so far; the others show NotPortedYet (D147).
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
      {!waitsForRole && view.id !== "search" && !Page && (
        <ViewPage view={view}>
          <NotPortedYet view={view} />
        </ViewPage>
      )}
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
