// The dashboard v2 (R5), served at `/` since the cut-over (T16b, D224). T12 built the shell and
// the design system; T13–T16a ported every page into it. The DOM hooks follow docs/08
// Appendix B (plan §3.4): `.rail a[data-v]`, `section.view.on#v-<name>`, `#toast`, `#gsearch`,
// `#search-results`.
import { lazy, Suspense, useEffect, type ComponentType } from "react";
import { ChunkBoundary } from "@/components/saley/ChunkBoundary";
import { Shell } from "@/components/saley/Shell";
import { SearchView } from "@/components/saley/SearchView";
import { Toaster } from "@/components/saley/Toaster";
import { ErrorLine, LoadingLine } from "@/components/saley/states";
import { resolveView, VIEWS, type ViewDef } from "@/components/saley/views";
import { TooltipProvider } from "@/components/ui/tooltip";
import { isAdmin, MeProvider, useMe } from "@/hooks/use-me";
import { useRoute } from "@/hooks/use-route";
import { focusCard } from "@/lib/card-focus";
import { W } from "@/words";

type PageProps = { view: ViewDef };
type PageModule = Record<string, ComponentType<PageProps>>;

/** One chunk per page (R5 T17): the first load fetches the shell and the page it opens, not
 *  all 15 pages (the single chunk was 684 kB). Vite emits each chunk as an external module
 *  file, so the CSP stays `script-src 'self'` (D146). */
function page(load: () => Promise<PageModule>, name: string) {
  return lazy(() => load().then((m) => ({ default: m[name] })));
}

// Every page of the rail, plus Money blocked (Search has its own view below).
const PAGES: Record<string, ComponentType<PageProps>> = {
  today: page(() => import("@/views/today/TodayPage"), "TodayPage"),
  goals: page(() => import("@/views/goals/GoalsPage"), "GoalsPage"),
  accounts: page(() => import("@/views/accounts/AccountsPage"), "AccountsPage"),
  people: page(() => import("@/views/people/PeoplePage"), "PeoplePage"),
  suggest: page(() => import("@/views/suggest/SuggestPage"), "SuggestPage"),
  signals: page(() => import("@/views/signals/SignalsPage"), "SignalsPage"),
  events: page(() => import("@/views/events/EventsPage"), "EventsPage"),
  blockers: page(() => import("@/views/blockers/BlockersPage"), "BlockersPage"),
  money: page(() => import("@/views/money/MoneyPage"), "MoneyPage"),
  asks: page(() => import("@/views/inbox/InboxPage"), "InboxPage"),
  commit: page(() => import("@/views/commitments/CommitmentsPage"), "CommitmentsPage"),
  decisions: page(() => import("@/views/decisions/DecisionsPage"), "DecisionsPage"),
  pack: page(() => import("@/views/pack/PackPage"), "PackPage"),
  scorecard: page(() => import("@/views/scorecard/ScorecardPage"), "ScorecardPage"),
  sources: page(() => import("@/views/sources/SourcesPage"), "SourcesPage"),
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
  // D261: a link from a Discord card outlines the item it was about.
  const { card, open } = route.params;
  useEffect(() => focusCard(card, open), [view.id, card, open]);
  return (
    <Shell view={view.id} q={route.params.q ?? ""}>
      {error !== null && <ErrorLine what={W.whatMe} error={error} onRetry={reload} />}
      {waitsForRole && <LoadingLine />}
      {!waitsForRole && view.id === "search" && <SearchView view={view} q={route.params.q ?? ""} />}
      {!waitsForRole && Page && (
        <ChunkBoundary key={view.id} what={view.nav}>
          <Suspense fallback={<LoadingLine />}>
            <Page view={view} />
          </Suspense>
        </ChunkBoundary>
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
