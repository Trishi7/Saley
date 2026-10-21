// Blockers (R5 T15; docs/08 view 7). Numbers first: late items, packages not ready, and the
// Money blocked tile (a link to #money). Then the late items, most late first, and the packages
// that are not ready, each 10 a page. Both lists come from /api/blockers, which pages them with
// one offset: the page asks once while both offsets match, else once per list.
// Hooks: `#blk-table`, `#pkg-table`, `[data-row][data-late]`, `[data-row][data-package]`,
// `[data-stat]`, `#tiles a.tile[data-tile="money"]`.
import { useApi } from "@/hooks/use-api";
import { useKeptPage, useOffset } from "@/hooks/use-kept";
import { OneLine } from "@/components/saley/Links";
import { Pager, PAGE_SIZE } from "@/components/saley/Pager";
import { Stat } from "@/components/saley/Stat";
import { EmptyLine, ErrorLine, LoadingLine } from "@/components/saley/states";
import { Tag } from "@/components/saley/Tag";
import { Tile } from "@/components/saley/Tile";
import type { ViewDef } from "@/components/saley/views";
import { ViewPage } from "@/components/saley/ViewPage";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { lateTag } from "@/views/goals/goal-rules";
import { fmt, W } from "@/words";
import type { BlockersData, LateItem, PackageItem } from "./types";

const ALL_GOALS_AT = 5; // legacy blocksText: five or more goals read "All N goals"

const path = (offset: number) => `/api/blockers?limit=${PAGE_SIZE}&offset=${offset}`;

/** "Goals G1, G3 · 2 motions", "All 5 goals", or "—". */
function blocksText(item: Pick<LateItem, "blocks_goals" | "blocks_motions">): string {
  const parts: string[] = [];
  const goals = item.blocks_goals;
  if (goals.length) {
    parts.push(
      goals.length >= ALL_GOALS_AT
        ? fmt(W.blkAllGoals, { n: goals.length })
        : fmt(W.blkGoals, { g: goals.join(", ") }),
    );
  }
  const motions = item.blocks_motions.length;
  if (motions) parts.push(motions > 1 ? fmt(W.blkMotions, { n: motions }) : W.blkMotion);
  return parts.join(" · ") || "—";
}

function LateTable({ items }: { items: LateItem[] }) {
  return (
    <Table id="blk-table" className="min-w-[46rem] table-fixed">
      <TableHeader>
        <TableRow className="hover:bg-transparent">
          <TableHead>{W.colItem}</TableHead>
          <TableHead className="w-[14%]">{W.colOwner}</TableHead>
          <TableHead className="w-[13%]">{W.colDue}</TableHead>
          <TableHead className="w-[14%]">{W.colLateBy}</TableHead>
          <TableHead className="w-[17%]">{W.colBlocks}</TableHead>
          <TableHead className="w-[14%]">{W.colStatus}</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {items.map((b) => {
          const late = lateTag(b.late_by_days);
          return (
            <TableRow key={b.id} data-row data-late={b.id}>
              <TableCell className="py-2.5 font-medium">
                <OneLine text={b.item} />
              </TableCell>
              <TableCell className="py-2.5">
                <OneLine text={b.owner} />
              </TableCell>
              <TableCell className="tabular py-2.5 text-xs">{b.due_label}</TableCell>
              <TableCell className="py-2.5">
                {late ? (
                  <Tag tag={late.tag} tone={late.tone} className="tabular">
                    {late.text}
                  </Tag>
                ) : (
                  "—"
                )}
              </TableCell>
              <TableCell className="text-muted-foreground py-2.5">
                <OneLine text={blocksText(b)} />
              </TableCell>
              <TableCell className="text-muted-foreground py-2.5">
                <OneLine text={b.status || W.noStatus} />
              </TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}

function Complete({ pct }: { pct: number | null }) {
  if (pct === null) return <span className="text-muted-foreground">—</span>;
  const width = Math.min(100, Math.max(0, pct));
  return (
    <span className="flex items-center gap-2" data-tag-ref="complete">
      <span
        className="bg-muted h-1.5 w-16 overflow-hidden rounded-full"
        role="img"
        aria-label={fmt(W.pctValue, { n: pct })}
      >
        <span className="bg-primary block h-full rounded-full" style={{ width: `${width}%` }} />
      </span>
      <span className="tabular text-xs">{fmt(W.pctValue, { n: pct })}</span>
    </span>
  );
}

function PackageTable({ items }: { items: PackageItem[] }) {
  return (
    <Table id="pkg-table" className="min-w-[36rem] table-fixed">
      <TableHeader>
        <TableRow className="hover:bg-transparent">
          <TableHead className="w-[28%]">{W.colPackage}</TableHead>
          <TableHead>{W.colPurpose}</TableHead>
          <TableHead className="w-[20%]">{W.lgComplete}</TableHead>
          <TableHead className="w-[16%]">{W.colStatus}</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {items.map((p) => (
          <TableRow key={p.id} data-row data-package={p.id}>
            <TableCell className="py-2.5 font-medium">
              <OneLine text={p.name} />
            </TableCell>
            <TableCell className="text-muted-foreground py-2.5">
              <OneLine text={p.purpose} />
            </TableCell>
            <TableCell className="py-2.5">
              <Complete pct={p.pct} />
            </TableCell>
            <TableCell className="text-muted-foreground py-2.5">
              <OneLine text={p.status} />
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

export function BlockersPage({ view }: { view: ViewDef }) {
  const [lateOffset, setLateOffset] = useOffset("late");
  const [pkgOffset, setPkgOffset] = useOffset("packages");
  const main = useApi<BlockersData>(path(lateOffset));
  const extra = useApi<BlockersData>(pkgOffset === lateOffset ? null : path(pkgOffset));
  const lateKept = useKeptPage(main.data, lateOffset);
  const pkgKept = useKeptPage(pkgOffset === lateOffset ? main.data : extra.data, pkgOffset);
  const late = lateKept?.page;
  const packages = pkgKept?.page;
  const error = main.error ?? extra.error;
  const retry = () => {
    main.reload();
    extra.reload();
  };

  return (
    <ViewPage view={view}>
      {error && <ErrorLine what={W.whatBlockers} error={error} onRetry={retry} />}
      {!late && main.loading && <LoadingLine />}
      {late && (
        <div data-content className="flex flex-col gap-6">
          <div id="tiles" className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <Stat id="late" label={W.blkLate} value={late.late_total} />
            <Stat id="packages" label={W.blkPackages} value={late.packages_total} />
            <Tile tile={late.money_tile} />
          </div>
          <section className="flex flex-col gap-2" aria-labelledby="blk-title">
            <h2 id="blk-title" className="text-base font-semibold">
              {W.blkLate} <span className="text-muted-foreground text-sm font-normal">{W.blkLateHint}</span>
            </h2>
            <div className="bg-card min-w-0 rounded-lg border" aria-busy={main.loading || undefined}>
              <Pager
                total={late.late_total}
                offset={lateKept?.offset ?? lateOffset}
                shown={late.late.length}
                onMove={setLateOffset}
                label={W.blkLate}
                className="pb-3 [&>nav]:px-4"
              >
                {late.late.length === 0 ? (
                  <div className="px-4 py-6">
                    <EmptyLine computed={late.last_computed_label} />
                  </div>
                ) : (
                  <LateTable items={late.late} />
                )}
              </Pager>
            </div>
          </section>
          {packages && (
            <section className="flex flex-col gap-2" aria-labelledby="pkg-title">
              <h2 id="pkg-title" className="text-base font-semibold">
                {W.blkPackages}{" "}
                <span className="text-muted-foreground text-sm font-normal">{W.blkPackagesHint}</span>
              </h2>
              <div className="bg-card min-w-0 rounded-lg border" aria-busy={extra.loading || undefined}>
                <Pager
                  total={packages.packages_total}
                  offset={pkgKept?.offset ?? pkgOffset}
                  shown={packages.packages.length}
                  onMove={setPkgOffset}
                  label={W.blkPackages}
                  className="pb-3 [&>nav]:px-4"
                >
                  {packages.packages.length === 0 ? (
                    <div className="px-4 py-6">
                      <EmptyLine computed={packages.last_computed_label} />
                    </div>
                  ) : (
                    <PackageTable items={packages.packages} />
                  )}
                </Pager>
              </div>
            </section>
          )}
        </div>
      )}
    </ViewPage>
  );
}
