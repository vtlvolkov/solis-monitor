// Pure helpers for the Overview cards added in the layout editor: self-sufficiency, recent events, battery health
// and the load heatmap. The cards themselves are in components/MoreCards.tsx.
import type { EventItem, EventsPayload } from './poll';

/** The share of the energy that came in from PV: everything the house used or stored came from PV or the grid,
 *  so pv / (pv + import) stays right even when the battery charges from the grid at night. */
export function selfSufficiency(pv: number | null | undefined, imported: number | null | undefined): number | null {
  const p = Math.max(0, pv ?? 0), g = Math.max(0, imported ?? 0);
  if (pv == null && imported == null) return null;
  return p + g > 0 ? Math.round((p / (p + g)) * 100) : null;
}

/** The latest outages and alarms together, newest first. */
export function recentEvents(e: EventsPayload | null, n = 5): EventItem[] {
  if (!e) return [];
  const all = [...e.outages, ...e.alarms].filter((x) => x.start);
  all.sort((a, b) => Date.parse(b.start!) - Date.parse(a.start!));
  return all.slice(0, n);
}

/** Equivalent full cycles: everything the battery ever gave, divided by its capacity. */
export function fullCycles(dischargedKwh: number | null | undefined, capacityKwh: number | null | undefined): number | null {
  if (dischargedKwh == null || !capacityKwh || capacityKwh <= 0) return null;
  return Math.round(dischargedKwh / capacityKwh);
}

/** Energy out ÷ energy in over the battery's life (losses in the battery and the inverter). */
export function roundTrip(chargedKwh: number | null | undefined, dischargedKwh: number | null | undefined): number | null {
  if (!chargedKwh || dischargedKwh == null || chargedKwh < 10) return null;    // too little yet to mean anything
  return Math.min(100, Math.round((dischargedKwh / chargedKwh) * 100));
}

/** The first and the lowest of a series: the SOH a month ago and since. */
export function firstValue(vals: (number | null)[] | undefined): number | null {
  return vals?.find((v): v is number => v != null) ?? null;
}
export const minOf = (vals: (number | null)[] | undefined): number | null => {
  const v = (vals ?? []).filter((x): x is number => x != null);
  return v.length ? Math.min(...v) : null;
};
export const maxOf = (vals: (number | null)[] | undefined): number | null => {
  const v = (vals ?? []).filter((x): x is number => x != null);
  return v.length ? Math.max(...v) : null;
};

export interface HeatRow { date: string; day: Date; cells: (number | null)[] }
export interface Heat { rows: HeatRow[]; max: number; busiest: { hour: number; kwh: number } | null }

const dayKey = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;

/** Hourly mean load (W, the window's end time in epoch seconds) as kWh per local hour, a row per local day,
 *  the last `days` days. Each window is placed by its middle, so a clock change cannot shift it into the next hour. */
export function heatmap(t: number[], watts: (number | null)[], days = 7): Heat {
  const byDay = new Map<string, HeatRow>();
  t.forEach((ts, i) => {
    const w = watts[i];
    if (w == null) return;
    const d = new Date((ts - 1800) * 1000);
    const key = dayKey(d);
    let row = byDay.get(key);
    if (!row) { row = { date: key, day: new Date(d.getFullYear(), d.getMonth(), d.getDate()), cells: Array(24).fill(null) }; byDay.set(key, row); }
    row.cells[d.getHours()] = Math.max(0, w) / 1000;
  });
  const rows = [...byDay.values()].sort((a, b) => a.date.localeCompare(b.date)).slice(-days);
  let max = 0;
  const sum = Array(24).fill(0), cnt = Array(24).fill(0);
  for (const r of rows) {
    r.cells.forEach((v, h) => { if (v != null) { max = Math.max(max, v); sum[h] += v; cnt[h]++; } });
  }
  let busiest: Heat['busiest'] = null;
  sum.forEach((s, h) => {
    if (!cnt[h]) return;
    const avg = s / cnt[h];
    if (!busiest || avg > busiest.kwh) busiest = { hour: h, kwh: avg };
  });
  return { rows, max, busiest };
}
