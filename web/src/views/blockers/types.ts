// /api/blockers answer (schemas.BlockersOut).
import type { TileData } from "@/components/saley/Tile";

export type LateItem = {
  id: number;
  item: string;
  short: string;
  owner: string | null;
  due_label: string;
  late_by_days: number;
  status: string | null;
  blocks_goals: string[];
  blocks_motions: string[];
};

export type PackageItem = {
  id: number;
  name: string;
  purpose: string | null;
  pct: number | null;
  status: string | null;
};

export type BlockersData = {
  late: LateItem[];
  late_total: number;
  packages: PackageItem[];
  packages_total: number;
  money_tile: TileData;
  last_computed_label: string;
};
