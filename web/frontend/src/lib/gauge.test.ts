import { describe, expect, it } from 'vitest';
import { chargeWatts, currentLimitW, dischargeWatts, fraction, gridWatts, zone } from './gauge';

describe('gauge scale', () => {
  it('maps watts to the scale and clamps', () => {
    expect(fraction(0)).toBe(0);
    expect(fraction(3000)).toBe(0.5);
    expect(fraction(6000)).toBe(1);
    expect(fraction(9000)).toBe(1);          // over the inverter limit: the bar stops at the end
    expect(fraction(-50)).toBe(0);
    expect(fraction(null)).toBe(0);
    expect(fraction(NaN)).toBe(0);
  });

  it('changes colour near the maximum', () => {
    expect(zone(0)).toBe('ok');
    expect(zone(3599)).toBe('ok');
    expect(zone(3600)).toBe('warn');          // 60% of 6 kW
    expect(zone(5099)).toBe('warn');
    expect(zone(5100)).toBe('danger');        // 85% of 6 kW
    expect(zone(6500)).toBe('danger');
    expect(zone(null)).toBe('ok');
  });

  it('scales to another maximum, e.g. the battery discharge limit', () => {
    const max = currentLimitW(50);                         // 50 A at 51.2 V
    expect(max).toBeCloseTo(2560);
    expect(fraction(1280, max)).toBe(0.5);
    expect(zone(1500, max)).toBe('ok');
    expect(zone(0.6 * max, max)).toBe('warn');
    expect(zone(0.9 * max, max)).toBe('danger');
  });

  it('turns signed battery power into discharge power', () => {
    expect(dischargeWatts(-1200)).toBe(1200);
    expect(dischargeWatts(900)).toBe(0);                   // charging
    expect(dischargeWatts(0)).toBe(0);
    expect(dischargeWatts(null)).toBeNull();
    expect(dischargeWatts(NaN)).toBeNull();
  });

  it('turns signed battery and grid power into charge power and grid size', () => {
    expect(chargeWatts(900)).toBe(900);
    expect(chargeWatts(-1200)).toBe(0);                    // discharging
    expect(chargeWatts(null)).toBeNull();
    expect(gridWatts(1500)).toBe(1500);                    // importing
    expect(gridWatts(-800)).toBe(800);                     // exporting
    expect(gridWatts(undefined)).toBeNull();
    expect(currentLimitW(40)).toBeCloseTo(2048);           // 40 A charge limit
  });

  it('uses a reference voltage for the battery limits, falling back to the nominal 51.2 V', () => {
    expect(currentLimitW(40, 55)).toBeCloseTo(2200);       // charge: equalize voltage
    expect(currentLimitW(60, 53.5)).toBeCloseTo(3210);     // discharge: float voltage
    expect(currentLimitW(40, null)).toBeCloseTo(2048);
    expect(currentLimitW(40, 0)).toBeCloseTo(2048);
    expect(currentLimitW(40, NaN)).toBeCloseTo(2048);
    expect(fraction(2250, currentLimitW(40, 55))).toBeCloseTo(1);   // 2.25 kW at the 40 A limit fills the dial
  });
});
