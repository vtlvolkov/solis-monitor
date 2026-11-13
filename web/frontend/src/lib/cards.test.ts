import { describe, expect, it } from 'vitest';
import { firstValue, fullCycles, heatmap, maxOf, minOf, recentEvents, roundTrip, selfSufficiency } from './cards';
import type { EventItem, EventsPayload } from './poll';

const ev = (kind: 'outage' | 'alarm', start: string, extra: Partial<EventItem> = {}): EventItem => ({
  kind, key: kind === 'outage' ? 'grid' : 'a1', name: kind === 'alarm' ? 'Battery over-temperature' : null, severity: kind === 'alarm' ? 'warning' : null,
  start, end: null, ongoing: false, duration_s: 600, soc_start: null, soc_end: null, load_start: null, ...extra,
});

describe('Overview card helpers', () => {
  it('counts self-sufficiency from PV against the grid, even with night charging from the grid', () => {
    expect(selfSufficiency(18.7, 4.6)).toBe(80);
    expect(selfSufficiency(0, 9)).toBe(0);
    expect(selfSufficiency(5, 0)).toBe(100);
    expect(selfSufficiency(0, 0)).toBeNull();
    expect(selfSufficiency(null, null)).toBeNull();
    expect(selfSufficiency(null, 3)).toBe(0);
  });

  it('lists outages and alarms together, newest first', () => {
    const e: EventsPayload = {
      outages: [ev('outage', '2026-11-12T16:00:00Z'), ev('outage', '2026-11-10T08:00:00Z')],
      alarms: [ev('alarm', '2026-11-11T13:40:00Z'), ev('alarm', '2026-11-13T09:00:00Z', { ongoing: true })],
      active: [], outage_now: null,
    };
    expect(recentEvents(e, 3).map((x) => x.start)).toEqual(['2026-11-13T09:00:00Z', '2026-11-12T16:00:00Z', '2026-11-11T13:40:00Z']);
    expect(recentEvents(null)).toEqual([]);
  });

  it('works out full cycles and the round trip from the lifetime counters', () => {
    expect(fullCycles(1374, 6.45)).toBe(213);
    expect(fullCycles(1374, null)).toBeNull();
    expect(fullCycles(null, 6.45)).toBeNull();
    expect(roundTrip(1480, 1374)).toBe(93);
    expect(roundTrip(5, 4)).toBeNull();            // too little to mean anything
    expect(roundTrip(100, 120)).toBe(100);
  });

  it('takes the first, lowest and highest values of a series', () => {
    expect(firstValue([null, 99, 100])).toBe(99);
    expect(minOf([null, 14.2, 9.8])).toBe(9.8);
    expect(maxOf([31, null, 12])).toBe(31);
    expect(minOf([])).toBeNull();
  });

  it('places hourly means by local day and hour, in kWh, and finds the busiest hour', () => {
    // windows end on the hour: the 18:00–19:00 mean is stamped 19:00
    const at = (d: number, h: number) => new Date(2026, 10, d, h).getTime() / 1000;
    const t = [at(12, 19), at(12, 20), at(13, 1), at(13, 20)];
    const w = [1200, 1800, null, 1400];
    const heat = heatmap(t, w);
    expect(heat.rows.map((r) => r.date)).toEqual(['2026-11-12', '2026-11-13']);
    expect(heat.rows[0].cells[18]).toBeCloseTo(1.2);
    expect(heat.rows[0].cells[19]).toBeCloseTo(1.8);
    expect(heat.rows[1].cells[0]).toBeNull();
    expect(heat.max).toBeCloseTo(1.8);
    expect(heat.busiest!.hour).toBe(19);
    expect(heat.busiest!.kwh).toBeCloseTo(1.6);
  });

  it('keeps only the last days', () => {
    const t = Array.from({ length: 10 }, (_, i) => new Date(2026, 10, 1 + i, 12).getTime() / 1000);
    expect(heatmap(t, t.map(() => 500), 7).rows.map((r) => r.date)).toEqual(
      ['2026-11-04', '2026-11-05', '2026-11-06', '2026-11-07', '2026-11-08', '2026-11-09', '2026-11-10']);
  });
});
