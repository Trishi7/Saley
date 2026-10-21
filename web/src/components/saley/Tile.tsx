// A number tile (docs/08 shell: "Every number tile is a link"). Hook (Appendix B):
// `a.tile[data-tile] .v`. A value that is not known shows "—", never 0. The tooltip names the
// source it counts (the R1.1 tiles check reads the title).
import { hrefFor, num, type ViewLink } from "@/lib/format";
import { cn } from "@/lib/utils";
import { fmt, W } from "@/words";

export type TileData = {
  key: string;
  label: string;
  value: number | string | null;
  detail: string;
  source: string;
  link: ViewLink;
};

export function Tile({ tile, className }: { tile: TileData; className?: string }) {
  const unknown = tile.value === null || tile.value === undefined || tile.value === ""; // as num()
  return (
    <a
      href={hrefFor(tile.link)}
      data-tile={tile.key}
      title={fmt(W.sourceTip, { s: tile.source })}
      className={cn(
        "tile bg-card group/tile flex min-w-0 flex-col gap-1 rounded-lg border p-4 outline-none",
        "transition-[border-color,background-color,box-shadow] duration-150 ease-out",
        "hover:border-primary/35 hover:bg-accent/40 focus-visible:ring-ring/60 focus-visible:border-ring focus-visible:ring-[3px]",
        unknown && "unknown",
        className,
      )}
    >
      <span className="l text-muted-foreground text-sm leading-snug">{tile.label}</span>
      <span
        className={cn(
          "v tabular text-3xl font-semibold tracking-tight",
          unknown && "text-muted-foreground",
        )}
      >
        {num(tile.value)}
      </span>
      {tile.detail && <span className="d text-muted-foreground text-xs">{tile.detail}</span>}
    </a>
  );
}
