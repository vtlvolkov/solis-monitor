// One exact day on the Charts and Events tabs: types and pure helpers (unit-tested).
import { locale, t } from './i18n';
import type { EventItem } from './poll';
import { formatHours } from './runtime';

export interface CalendarPayload {
  month: string; today: string; first_day: string | null;
  days: Record<string, { outages: number; alarms: number }>;
}

export type DayEnergy = { date: string; tz: string } & Record<string, number | string | null>;

/** '2026-10-24' moved by `delta` days (calendar arithmetic: no clock, so no daylight-saving surprises). */
export function shiftDay(day: string, delta: number): string {
  const [y, m, d] = day.split('-').map(Number);
  return new Date(Date.UTC(y, m - 1, d + delta)).toISOString().slice(0, 10);
}

/** A month's cells for a Monday-first grid: null for the blanks before the 1st, then each date. */
export function calendarCells(month: string): (string | null)[] {
  const [y, m] = month.split('-').map(Number);
  const blanks = (new Date(Date.UTC(y, m - 1, 1)).getUTCDay() + 6) % 7;
  const count = new Date(Date.UTC(y, m, 0)).getUTCDate();
  const cells: (string | null)[] = Array.from({ length: blanks }, () => null);
  for (let d = 1; d <= count; d++) cells.push(`${month}-${String(d).padStart(2, '0')}`);
  return cells;
}

/** Whether the picker may offer this day: not in the future, not before the first reading. */
export const dayAllowed = (day: string, today: string, first: string | null | undefined): boolean =>
  day <= today && (!first || day >= first);

const at = (noon: string) => new Date(`${noon}T12:00:00`);
export const dayLabel = (day: string): string =>
  at(day).toLocaleDateString(locale(), { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' });
export const monthTitle = (month: string): string =>
  at(`${month}-01`).toLocaleDateString(locale(), { month: 'long', year: 'numeric' });

export function duration(s: number | null): string {
  if (s == null) return '—';
  if (s < 60) return t('{n} s', { n: s });
  if (s < 3600) return t('{n} min', { n: Math.round(s / 60) });
  return formatHours(s / 3600);
}

const seconds = (iso: string) => Date.parse(iso) / 1000;

/** Where each event lies within the day, in epoch seconds: an unknown start, an ongoing end and anything past the day are cut to fit. */
export function spansInDay(items: EventItem[], start: number, stop: number, now: number): [number, number][] {
  return items.map((e): [number, number] => [
    Math.max(start, e.start ? seconds(e.start) : start),
    Math.min(stop, e.end ? seconds(e.end) : now),
  ]).filter(([a, b]) => b > a);
}

/** Both ends of the event fall on this day (so times alone are enough, and its SOC change belongs to the day). */
export const withinDay = (e: EventItem, start: number, stop: number): boolean =>
  !!e.start && !!e.end && seconds(e.start) >= start && seconds(e.end) < stop;

/** Battery energy the outages took, estimated from their SOC change; null if that is not known for all of them. */
export function outageBatteryKwh(outages: EventItem[], start: number, stop: number, capacityKwh: number | null | undefined): number | null {
  if (!capacityKwh || outages.length === 0) return null;
  let sum = 0;
  for (const o of outages) {
    if (!withinDay(o, start, stop) || o.soc_start == null || o.soc_end == null) return null;
    sum += (Math.max(0, o.soc_start - o.soc_end) / 100) * capacityKwh;
  }
  return sum;
}

/** Lowest, highest and last value of a series, skipping gaps. */
export function seriesStats(vals: (number | null)[] | undefined): { min: number; max: number; last: number } | null {
  const ok = (vals ?? []).filter((v): v is number => v != null);
  return ok.length ? { min: Math.min(...ok), max: Math.max(...ok), last: ok[ok.length - 1] } : null;
}
