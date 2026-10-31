import { describe, expect, it } from 'vitest';
import { chargeRate, formatHours, fullChargeHours, gridLost, mean, recentLoad, runtimeHours } from './runtime';

describe('runtimeHours', () => {
  it('uses only the energy above the floor', () => {
    // 6.45 kWh, 98% -> 20% = 5.031 kWh at 650 W
    expect(runtimeHours(98, 20, 6.45, 650)).toBeCloseTo(7.74, 2);
    expect(runtimeHours(20, 20, 6.45, 650)).toBe(0);
    expect(runtimeHours(10, 20, 6.45, 650)).toBe(0);
  });
  it('null for missing inputs or tiny loads', () => {
    expect(runtimeHours(null, 20, 6.45, 650)).toBeNull();
    expect(runtimeHours(90, 20, null, 650)).toBeNull();
    expect(runtimeHours(90, 20, 0, 650)).toBeNull();
    expect(runtimeHours(90, 20, 6.45, 5)).toBeNull();
  });
});

describe('fullChargeHours', () => {
  it('uses the energy up to the target', () => {
    // 10.24 kWh, 62% -> 100% = 3.89 kWh at 1.8 kW
    expect(fullChargeHours(62, 100, 10.24, 1800)).toBeCloseTo(2.16, 2);
    expect(fullChargeHours(62, 80, 10.24, 1800)).toBeCloseTo(1.02, 2);
  });
  it('null when missing, at the target, or barely charging', () => {
    expect(fullChargeHours(null, 100, 10.24, 1800)).toBeNull();
    expect(fullChargeHours(62, null, 10.24, 1800)).toBeNull();
    expect(fullChargeHours(62, 100, null, 1800)).toBeNull();
    expect(fullChargeHours(62, 100, 0, 1800)).toBeNull();
    expect(fullChargeHours(62, 100, 10.24, null)).toBeNull();
    expect(fullChargeHours(62, 100, 10.24, 49)).toBeNull();
    expect(fullChargeHours(100, 100, 10.24, 1800)).toBeNull();
    expect(fullChargeHours(90, 80, 10.24, 1800)).toBeNull();
  });
});

describe('recentLoad', () => {
  it('averages only the last 15 minutes', () => {
    expect(recentLoad([0, 199, 200, 1000, 1100], [5000, 5000, 300, null, 500], 1100)).toBe(400);
  });
  it('null without recent data, so the live load is used', () => {
    expect(recentLoad([0], [5000], 1100)).toBeNull();
    expect(recentLoad([1000], [null], 1100)).toBeNull();
  });
});

describe('chargeRate', () => {
  it('averages the last 15 minutes while charging', () => {
    expect(chargeRate([0, 100, 200, 1000, 1100], [5000, 1000, null, -300, 2000], 1100)).toBe(2000);
    expect(chargeRate([200, 300, 1000], [1000, 10, 2000], 1100)).toBe(1500);
  });
  it('null when it was not charging', () => {
    expect(chargeRate([1000], [-300], 1100)).toBeNull();
    expect(chargeRate([], [], 1100)).toBeNull();
  });
});

describe('formatHours', () => {
  it.each([
    [null, '--'], [0, '0 min'], [0.1, '< 10 min'], [0.5, '30 min'], [7.74, '7 h 40 min'], [2, '2 h'],
    [1.999, '2 h'], [100, '4 days'],
  ])('%s -> %s', (h, s) => expect(formatHours(h as number | null)).toBe(s));
});

describe('gridLost', () => {
  const okGrid = { fault_code_01: 0, status_code: 3, grid_voltage: 230 };
  it.each([
    [okGrid, false],
    [{ ...okGrid, fault_code_01: 1 }, true],
    [{ ...okGrid, fault_code_01: 2 }, false],
    [{ ...okGrid, status_code: 0x1015 }, true],
    [{ ...okGrid, grid_voltage: 3 }, true],
    [{ ...okGrid, grid_voltage: null }, false],
    [null, false],
  ])('%j -> %s', (f, lost) => expect(gridLost(f)).toBe(lost));
});

describe('mean', () => {
  it('ignores nulls', () => {
    expect(mean([1, null, 3])).toBe(2);
    expect(mean([null])).toBeNull();
  });
});
