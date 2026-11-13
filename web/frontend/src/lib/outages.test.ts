import { describe, expect, it } from 'vitest';
import { describeDay, isGroup, offHours, outageSlots, slotText, verdictText } from './outages';

const day = (slots: { start: number; end: number; type: 'Definite' | 'Possible' }[], status: 'ScheduleApplies' | 'WaitingForSchedule' = 'ScheduleApplies') =>
  ({ date: '2026-11-13T00:00:00+02:00', status, slots });

describe('planned outages', () => {
  it('merges touching slots of the same kind and counts definite hours only', () => {
    const d = day([{ start: 480, end: 600, type: 'Definite' }, { start: 600, end: 840, type: 'Definite' }, { start: 960, end: 1080, type: 'Possible' }]);
    expect(outageSlots(d)).toEqual([{ start: 480, end: 840, type: 'Definite' }, { start: 960, end: 1080, type: 'Possible' }]);
    expect(offHours(d)).toBe(6);
    expect(describeDay(d)).toBe('6 h without power');
    expect(describeDay(day([], 'WaitingForSchedule'))).toBe('waiting for the schedule');
    expect(describeDay(day([]))).toBe('no outages planned');
  });
  it('writes the times, the possible ones marked', () => {
    expect(slotText({ start: 1200, end: 1440, type: 'Definite' })).toBe('20:00–24:00');
    expect(slotText({ start: 720, end: 960, type: 'Possible' })).toBe('12:00–16:00 (possible)');
  });
  it('says whether the battery lasts', () => {
    const fmt = (s: number) => `${s / 3600} h`;
    expect(verdictText({ enough: true, lasts_s: 7200, short_s: null, runs_out_at: null, load_w: 500, soc_at_start: 80 }, fmt))
      .toEqual({ text: 'Battery is enough: ≈ 2 h at about 500 W', tone: 'ok' });
    expect(verdictText({ enough: false, lasts_s: 3600, short_s: 3600, runs_out_at: '20:40', load_w: 700, soc_at_start: 40 }, fmt).text)
      .toBe('Battery runs out ≈ 20:40, 1 h short');
  });
  it('knows a group when it sees one', () => {
    expect(isGroup('3.1')).toBe(true); expect(isGroup('20.1')).toBe(true);
    expect(isGroup('3')).toBe(false); expect(isGroup('3.1.1')).toBe(false); expect(isGroup('a.b')).toBe(false);
  });
});
