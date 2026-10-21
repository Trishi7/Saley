// /api/money-blocked answer (schemas.MoneyOut).
export type MoneyItem = {
  contact_id: number;
  name: string;
  company: string | null;
  stage: string;
  size_usd: number | null;
  value_label: string;
  deliverable: string;
  short: string;
  owner: string | null;
  late_by_days: number;
  more_blocking: number;
};

export type MoneyData = {
  items: MoneyItem[];
  total: number;
  total_usd: number;
  no_value: number;
  last_computed_label: string;
};
