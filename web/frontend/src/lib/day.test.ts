import { describe, expect, it } from 'vitest';
import { calendarCells, dayAllowed, outageBatteryKwh, seriesStats, shiftDay, spansInDay, withinDay } from './day';
import type { EventItem } from './poll';

const ev = (start: string | null, end: string | null, soc_start: number | null = null, soc_end: number | null = null): EventItem => ({
  kind: 'outage', key: 'grid', name: null, severity: null, start, end, ongoing: end === null, duration_s: null, soc_start, soc_end, load_start: null,
});
const S = Date.parse('2026-10-24T00:00:00Z') / 1000, E = S + 86400;

describe('shiftDay', () => {
  it('crosses months and years', () => {
    expect(shiftDay('2026-11-01', -1)).toBe('2026-10-31');
    expect(shiftDay('2026-12-31', 1)).toBe('2027-01-01');
  });
  it('is not thrown by a daylight-saving change', () => {
    expect(shiftDay('2026-10-25', 1)).toBe('2026-10-26');
    expect(shiftDay('2026-03-29', -1)).toBe('2026-03-28');
  });
});

describe('calendarCells', () => {
  it('starts on the right weekday, Monday first', () => {
    const c = calendarCells('2026-10');               // 1 October 2026 is a Thursday
    expect(c.slice(0, 4)).toEqual([null, null, null, '2026-10-01']);
    expect(c[c.length - 1]).toBe('2026-10-31');
  });
  it('knows month lengths', () => {
    expect(calendarCells('2028-02').filter(Boolean)).toHaveLength(29);
    expect(calendarCells('2026-06')[0]).toBe('2026-06-01');   // starts on a Monday: no blanks
  });
});

it('dayAllowed stops at today and at the first reading', () => {
  expect(dayAllowed('2026-11-02', '2026-11-01', null)).toBe(false);
  expect(dayAllowed('2026-11-01', '2026-11-01', null)).toBe(true);
  expect(dayAllowed('2026-09-30', '2026-11-01', '2026-10-01')).toBe(false);
  expect(dayAllowed('2026-10-01', '2026-11-01', '2026-10-01')).toBe(true);
});

describe('spansInDay', () => {
  it('cuts events that run over the day', () => {
    expect(spansInDay([ev('2026-10-23T22:00:00Z', '2026-10-24T01:00:00Z')], S, E, E + 999)).toEqual([[S, S + 3600]]);
    expect(spansInDay([ev('2026-10-24T23:00:00Z', '2026-10-25T01:00:00Z')], S, E, E + 999)).toEqual([[E - 3600, E]]);
  });
  it('starts an unknown start at midnight and ends an ongoing one now', () => {
    expect(spansInDay([ev(null, '2026-10-24T01:00:00Z')], S, E, E)).toEqual([[S, S + 3600]]);
    expect(spansInDay([ev('2026-10-24T10:00:00Z', null)], S, E, S + 11 * 3600)).toEqual([[S + 10 * 3600, S + 11 * 3600]]);
  });
  it('drops what does not touch the day', () => {
    expect(spansInDay([ev('2026-10-25T01:00:00Z', '2026-10-25T02:00:00Z')], S, E, E + 99999)).toEqual([]);
  });
});

describe('outageBatteryKwh', () => {
  const inDay = ev('2026-10-24T18:00:00Z', '2026-10-24T20:00:00Z', 85, 47);
  it('estimates from the SOC change', () => {
    expect(outageBatteryKwh([inDay], S, E, 10)).toBeCloseTo(3.8);
    expect(outageBatteryKwh([inDay, inDay], S, E, 10)).toBeCloseTo(7.6);
  });
  it('gives no number rather than a wrong one', () => {
    expect(outageBatteryKwh([], S, E, 10)).toBeNull();
    expect(outageBatteryKwh([inDay], S, E, null)).toBeNull();
    expect(outageBatteryKwh([ev('2026-10-24T18:00:00Z', null, 85)], S, E, 10)).toBeNull();                    // still going
    expect(outageBatteryKwh([ev('2026-10-23T18:00:00Z', '2026-10-24T02:00:00Z', 85, 40)], S, E, 10)).toBeNull();   // partly another day
    expect(outageBatteryKwh([ev('2026-10-24T18:00:00Z', '2026-10-24T20:00:00Z')], S, E, 10)).toBeNull();     // SOC not recorded
  });
  it('never goes negative when the sun charged the battery during the outage', () => {
    expect(outageBatteryKwh([ev('2026-10-24T10:00:00Z', '2026-10-24T11:00:00Z', 50, 60)], S, E, 10)).toBe(0);
  });
});

it('withinDay needs both ends on the day', () => {
  expect(withinDay(ev('2026-10-24T10:00:00Z', '2026-10-24T11:00:00Z'), S, E)).toBe(true);
  expect(withinDay(ev('2026-10-24T23:00:00Z', '2026-10-25T00:00:00Z'), S, E)).toBe(false);
  expect(withinDay(ev(null, '2026-10-24T11:00:00Z'), S, E)).toBe(false);
});

it('seriesStats skips gaps and handles an empty day', () => {
  expect(seriesStats([null, 40, 90, null, 23, null])).toEqual({ min: 23, max: 90, last: 23 });
  expect(seriesStats([null])).toBeNull();
  expect(seriesStats(undefined)).toBeNull();
});
