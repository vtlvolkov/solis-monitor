import { describe, expect, it } from 'vitest';
import { formatHours, gridLost, mean, runtimeHours } from './runtime';

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
