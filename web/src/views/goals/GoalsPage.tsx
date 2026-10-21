// The Q4 goals page of the dashboard v2 (R5 T14; docs/08 view 2). The legend sits at the
// top (ViewPage), then one uniform card per goal (GoalCard). No page search: the global search
// in the top bar stays (brief, Q4 Goals). One GET /api/goals feeds the page.
import { EmptyLine, ErrorLine, LoadingLine } from "@/components/saley/states";
import type { ViewDef } from "@/components/saley/views";
import { ViewPage } from "@/components/saley/ViewPage";
import { useApi } from "@/hooks/use-api";
import { W } from "@/words";
import { GoalCard } from "./GoalCard";
import type { GoalsData } from "./types";

export function GoalsPage({ view }: { view: ViewDef }) {
  const { data, error, loading, reload } = useApi<GoalsData>("/api/goals");
  return (
    <ViewPage view={view}>
      {error && <ErrorLine what={W.whatGoals} error={error} onRetry={reload} />}
      {!data && loading && <LoadingLine />}
      {data && data.items.length === 0 && <EmptyLine computed={data.last_computed_label} />}
      {data && data.items.length > 0 && (
        <div
          data-content
          id="goals"
          className="grid grid-cols-1 items-start gap-5 xl:grid-cols-2"
        >
          {data.items.map((goal) => (
            <GoalCard key={goal.id} goal={goal} />
          ))}
        </div>
      )}
    </ViewPage>
  );
}
