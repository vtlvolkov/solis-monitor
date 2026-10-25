import { describe, expect, it } from 'vitest';
import { holdInfo, slotMinutes } from './hold';
import type { SettingsPayload, TouSlot } from './settings';

const slot = (n: number, start: string, end: string, enabled = true, soc = 100): TouSlot =>
  ({ slot: n, enabled, soc, current_a: 40, voltage: 49, start, end });

const settings = (charge: TouSlot[], gridCharging = true) =>
  ({ grid_charging: gridCharging, tou: { switch_raw: 1, charge, discharge: [] } }) as unknown as SettingsPayload;

describe('slotMinutes', () => {
  it('measures slots, including across midnight', () => {
    expect(slotMinutes({ start: '00:00', end: '23:59' })).toBe(1439);
    expect(slotMinutes({ start: '23:00', end: '07:00' })).toBe(480);
    expect(slotMinutes({ start: '08:00', end: '09:30' })).toBe(90);
    expect(slotMinutes({ start: '00:00', end: '00:00' })).toBe(0);
  });
});

describe('holdInfo', () => {
  it('finds the all-day slot', () => {
    const h = holdInfo(settings([slot(1, '00:00', '23:59')]));
    expect(h?.slot.slot).toBe(1);
    expect(h?.minutes).toBe(1439);
  });

  it('is null for a night-only slot, a disabled slot, or when grid charging is forbidden', () => {
    expect(holdInfo(settings([slot(1, '23:00', '07:00')]))).toBeNull();
    expect(holdInfo(settings([slot(1, '00:00', '23:59', false)]))).toBeNull();
    expect(holdInfo(settings([slot(1, '00:00', '23:59')], false))).toBeNull();
    expect(holdInfo(settings([]))).toBeNull();
  });

  it('picks the longest of several slots', () => {
    const h = holdInfo(settings([slot(1, '23:00', '07:00'), slot(2, '06:00', '05:00'), slot(3, '08:00', '09:00')]));
    expect(h?.slot.slot).toBe(2);
  });
});
