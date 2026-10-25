// Electricity cost history: types and pure helpers (unit-tested).

export interface CostTariff {
  currency: string; day: number | null; night: number | null; night_start: string | null; night_end: string | null;
}

export interface CostDay {
  date: string;                       // YYYY-MM-DD, local
  has_data: boolean;
  day_kwh?: number; night_kwh?: number; kwh?: number;
  day_cost?: number; night_cost?: number; cost?: number; saved?: number;
  load_kwh?: number | null; export_kwh?: number | null; pv_kwh?: number | null;
  charge_kwh?: number | null; discharge_kwh?: number | null;
}

export interface CostTotal {
  days: number; day_kwh: number; night_kwh: number; kwh: number; day_cost: number; night_cost: number; cost: number;
  saved: number; load_kwh: number; export_kwh: number; pv_kwh: number; charge_kwh: number; discharge_kwh: number;
  avg_cost: number | null; avg_kwh: number | null;
}

export interface CostHistoryPayload {
  configured: boolean;
  tariff: CostTariff;
  month?: string; current_month?: string; first_day?: string | null; tz?: string;
  /** The prices in force during the month: the one at its start, then each later change (from = null: from the start). */
  tariff_changes?: (CostTariff & { from: string | null })[];
  days?: CostDay[];
  total?: CostTotal;
}

export type Metric = 'cost' | 'kwh';

const MONTH = /^(\d{4})-(0[1-9]|1[0-2])$/;
const DAY = /^\d{4}-\d{2}-\d{2}$/;

export const isMonth = (s: string | null | undefined): s is string => !!s && MONTH.test(s);
export const isDay = (s: string | null | undefined): s is string => !!s && DAY.test(s);
export const monthOfDay = (day: string): string => day.slice(0, 7);

/** '2026-10' moved by `delta` months. */
export function shiftMonth(month: string, delta: number): string {
  const [y, m] = month.split('-').map(Number);
  const n = y * 12 + (m - 1) + delta;
  return `${Math.floor(n / 12)}-${String((n % 12) + 1).padStart(2, '0')}`;
}

/** Every month from the one containing `firstDay` up to `current`, oldest first. */
export function monthsBetween(firstDay: string | null | undefined, current: string): string[] {
  const start = firstDay ? monthOfDay(firstDay) : current;
  const out: string[] = [];
  for (let m = start > current ? current : start; m <= current; m = shiftMonth(m, 1)) out.push(m);
  return out;
}

/** The days to list in the table: all of them, or only those with readings. */
export const withData = (days: CostDay[]): CostDay[] => days.filter((d) => d.has_data);

/** Largest stack in the chart (kWh or money), at least `min` so an empty month still has an axis. */
export function stackMax(days: CostDay[], metric: Metric, min = 1): number {
  return Math.max(min, ...days.map((d) => (d.has_data ? (metric === 'cost' ? d.cost : d.kwh) ?? 0 : 0)));
}

/** A round axis maximum at or above `v`, with 4 even steps (1, 2, 2.5, 5 × 10^n). */
export function niceMax(v: number): number {
  const mag = Math.pow(10, Math.floor(Math.log10(v)));
  for (const k of [1, 1.2, 1.6, 2, 2.4, 3, 4, 5, 6, 8, 10]) if (k * mag >= v) return k * mag;
  return 10 * mag;
}

export const money = (v: number | null | undefined, currency: string, digits = 2): string =>
  v == null ? '--' : `${v.toFixed(digits)} ${currency}`;


/** One period (today or this month) of the Electricity cost card, as /api/cost sends it. */
export interface CostPeriod {
  kwh: number; day_kwh: number; night_kwh: number; day_cost: number; night_cost: number; cost: number; saved: number;
}

/** The tariff zone that is active right now (server clock), or null for a single-rate tariff. */
export interface RateNow { zone: 'day' | 'night'; price: number; until: string }

export interface ZoneRow {
  zone: 'day' | 'night';
  from: string; to: string;               // the zone's hours, e.g. 07:00 and 23:00
  today: { kwh: number; cost: number };
  month: { kwh: number; cost: number };
  active: boolean;
}

/** Day zone, then night zone, for the cost card's table; empty when there is no night tariff. */
export function zoneRows(tariff: CostTariff, today: CostPeriod | undefined, month: CostPeriod | undefined, now: RateNow | null | undefined): ZoneRow[] {
  if (tariff.night == null || !tariff.night_start || !tariff.night_end || !today || !month) return [];
  const row = (zone: 'day' | 'night'): ZoneRow => ({
    zone,
    from: zone === 'day' ? tariff.night_end! : tariff.night_start!,
    to: zone === 'day' ? tariff.night_start! : tariff.night_end!,
    today: { kwh: zone === 'day' ? today.day_kwh : today.night_kwh, cost: zone === 'day' ? today.day_cost : today.night_cost },
    month: { kwh: zone === 'day' ? month.day_kwh : month.night_kwh, cost: zone === 'day' ? month.day_cost : month.night_cost },
    active: now?.zone === zone,
  });
  return [row('day'), row('night')];
}
