import { describe, expect, it } from 'vitest';
import { activeSlot, enabledSlots, inSlot, minuteOf, minutesLeft, nextSlot } from './schedule';
import type { TouSlot } from './settings';

const slot = (n: number, start: string, end: string, enabled = true): TouSlot =>
  ({ slot: n, enabled, soc: 100, current_a: 40, voltage: 49, start, end });

describe('inSlot', () => {
  it.each([
    [[0, '00:00', '23:59'], true], [[23 * 60 + 59, '00:00', '23:59'], false],
    [[23 * 60, '23:00', '07:00'], true], [[3 * 60, '23:00', '07:00'], true], [[7 * 60, '23:00', '07:00'], false],
    [[12 * 60, '23:00', '07:00'], false], [[0, '00:00', '00:00'], false],
  ] as const)('%j -> %s', ([m, a, b], r) => expect(inSlot(m, { start: a, end: b })).toBe(r));
});

describe('schedule', () => {
  const tou = {
    charge: [slot(1, '23:00', '07:00'), slot(2, '13:00', '14:00', false), slot(3, '00:00', '00:00')],
    discharge: [slot(1, '17:00', '20:00')],
  };
  const slots = enabledSlots(tou);
  it('ignores disabled and empty slots', () => {
    expect(slots.map((s) => `${s.kind}${s.slot}`)).toEqual(['charge1', 'discharge1']);
  });
  it('active and next', () => {
    expect(activeSlot(slots, 2 * 60)?.slot).toBe(1);
    expect(activeSlot(slots, 18 * 60)?.kind).toBe('discharge');
    expect(activeSlot(slots, 12 * 60)).toBeNull();
    expect(nextSlot(slots, 12 * 60)).toMatchObject({ slot: { kind: 'discharge' }, inMin: 300 });
    expect(nextSlot(slots, 21 * 60)).toMatchObject({ slot: { kind: 'charge' }, inMin: 120 });
    expect(nextSlot([], 0)).toBeNull();
  });
  it('minutes left and minute parsing', () => {
    expect(minutesLeft({ end: '07:00' }, 23 * 60)).toBe(480);
    expect(minuteOf('2026-10-18T21:03:41')).toBe(21 * 60 + 3);
  });
});
