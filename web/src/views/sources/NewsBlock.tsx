// News today on Sources and health (docs/08 view 14, D73). The item steps that are a part of
// the step before (pulled → stored → scored → scored 4 or 5 → about us) are the vendored
// Funnel (one hue: the steps are one series, not categories). The other steps (sources tried
// and answered, the three drops, sent) are a short list beside it, because they count other
// things (sources, dropped items) and would bend the funnel. Each number opens its list
// through the API's own `href` (never built here). Hooks: `#news-funnel`, `#news-other`,
// `#news-failed`.
import { Funnel, type FunnelStep } from "@/components/saley/Funnel";
import { num } from "@/lib/format";
import { W } from "@/words";
import { FUNNEL_KEYS, type HealthData } from "./types";

export function NewsBlock({ news, note }: { news: HealthData["news"]; note: string }) {
  const funnel: FunnelStep[] = FUNNEL_KEYS.flatMap((key) => {
    const step = news.steps.find((s) => s.key === key);
    return step ? [{ key: step.key, label: step.step, n: step.n, href: step.href }] : [];
  });
  const other = news.steps.filter((s) => !FUNNEL_KEYS.includes(s.key));
  return (
    <section aria-labelledby="news-t" className="bg-card flex min-w-0 flex-col gap-4 rounded-lg border p-4">
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="news-t" className="text-base font-semibold">
          {W.newsTitle}{" "}
          <span className="text-muted-foreground text-sm font-normal">{news.label}</span>
        </h2>
        <span className="text-muted-foreground text-xs">{note}</span>
      </header>
      <div id="news-funnel">
        <Funnel steps={funnel} label={W.newsFunnelLabel} />
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <div className="flex min-w-0 flex-col gap-2">
          <h3 className="text-sm font-medium">{W.newsOther}</h3>
          <ul id="news-other" className="divide-y text-sm">
            {other.map((s) => (
              <li key={s.key} data-step={s.key} className="flex items-baseline justify-between gap-3 py-1.5">
                <span className="min-w-0">
                  {s.step}
                  {s.note && <span className="text-muted-foreground ml-1 text-xs">{s.note}</span>}
                </span>
                <span className="tabular font-medium">{num(s.n)}</span>
              </li>
            ))}
          </ul>
        </div>
        <div className="flex min-w-0 flex-col gap-2">
          <h3 className="text-sm font-medium">{W.newsFailedTitle}</h3>
          <ul id="news-failed" className="divide-y text-sm">
            {news.failed_sources.length === 0 && (
              <li className="empty text-muted-foreground py-1.5">{W.newsNoFailed}</li>
            )}
            {news.failed_sources.map((s) => (
              <li key={s.name} data-source={s.name} className="py-1.5">
                <span className="font-medium">{s.name}</span>
                {s.problem && <span className="text-muted-foreground ml-1 text-xs">{s.problem}</span>}
              </li>
            ))}
          </ul>
          {news.watch.length > 0 && (
            <ul className="text-muted-foreground flex flex-col gap-1 text-xs">
              {news.watch.map((line, i) => (
                <li key={`${line.kind}-${i}`} data-kind={line.kind}>
                  {line.text}
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </section>
  );
}
