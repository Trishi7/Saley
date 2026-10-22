// "Run news now" (D150, D151, D178; R5-N09). Admin only (the page is admin only, and the
// server checks the session and the SQL function again). One open request at a time and a
// 10 minute cooldown live in `saley_request_news_run`; this card only shows them:
// - while a run is queued or running, the Button is off and the state shows;
// - a press is one POST /api/admin/news/run (`data-act="run-news"`, X-Saley-Act);
// - the answer line is the server's own ("News run queued.", "Wait N min before the next news
//   run.", "A news run is already waiting or running."), shown calmly, never in red.
// The worker of the main dev slot (or `saley news`) takes the request. The gate's worker
// slot never registers that job, so the gate never fetches news (D151).
// Hooks: `#news-run`, `[data-act="run-news"]`, `[data-news-state]`, `[data-news-line]`.
import { NewspaperIcon } from "lucide-react";
import { useState } from "react";
import { postJson } from "@/api";
import { ActButton } from "@/components/saley/ActButton";
import { RowLine, type Line } from "@/components/saley/RowLine";
import { ErrorLine } from "@/components/saley/states";
import { Tag } from "@/components/saley/Tag";
import { useApi } from "@/hooks/use-api";
import { useKept } from "@/hooks/use-kept";
import { usePress } from "@/hooks/use-press";
import { istTime, newsRunOf } from "@/lib/team-rules";
import { fmt, W } from "@/words";
import { STATUS_TAGS, type NewsRunAnswer, type NewsRunState } from "./types";

const RUN_PATH = "/api/admin/news/run";
const CALM = new Set([409, 429]); // busy and cooldown are calm server lines

export function NewsRun() {
  const state = useApi<NewsRunState>(RUN_PATH);
  const { busy, press } = usePress();
  const [line, setLine] = useState<Line | null>(null);
  const known = useKept(state.data); // the last state stays while it loads again (no flicker)
  const run = newsRunOf(known);
  const latest = known?.latest ?? null;
  const tag = run.state ? STATUS_TAGS[run.state] : undefined;

  const go = async () => {
    setLine(null);
    const err = await press(async () => {
      const answer = await postJson<NewsRunAnswer>(RUN_PATH);
      setLine({ text: answer.label, bad: false });
    });
    if (err) setLine({ text: err.message, bad: !CALM.has(err.status) });
    state.reload();
  };

  return (
    <section
      id="news-run"
      aria-labelledby="news-run-t"
      className="bg-card flex flex-col gap-3 rounded-lg border p-4 sm:flex-row sm:items-center sm:justify-between"
    >
      <div className="flex min-w-0 flex-col gap-1">
        <h2 id="news-run-t" className="text-base font-semibold">
          {W.runNewsTitle}
        </h2>
        <p className="text-muted-foreground text-xs text-pretty">{W.runNewsHint}</p>
        {state.error && <ErrorLine what={W.whatNewsRun} error={state.error} onRetry={state.reload} />}
        <div className="flex flex-wrap items-center gap-2 text-xs" data-news-state={run.state ?? "none"}>
          {tag && (
            <Tag tag={tag.tag} tone={tag.tone}>
              {tag.name}
            </Tag>
          )}
          {latest && (
            <span className="text-muted-foreground nums">
              {fmt(W.runNewsAsked, { t: istTime(latest.requested_at), u: latest.requested_by })}
            </span>
          )}
          {!latest && known?.label && <span className="text-muted-foreground">{known.label}</span>}
        </div>
        <span data-news-line>
          <RowLine line={line} />
        </span>
      </div>
      <ActButton
        act="run-news"
        endpoint={RUN_PATH}
        busy={busy}
        off={run.busy}
        variant="default"
        onPress={() => void go()}
        className="shrink-0"
      >
        <NewspaperIcon aria-hidden />
        {W.runNews}
      </ActButton>
    </section>
  );
}
