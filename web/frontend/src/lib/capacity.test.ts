import { describe, expect, it } from 'vitest';
import { describeSource, fallback, parseCapacity } from './capacity';

describe('battery capacity', () => {
  it('accepts kWh with a dot or comma, up to two decimals', () => {
    expect(parseCapacity('10.24', 1000, 6.45)).toEqual({ ok: true, value: 10.24 });
    expect(parseCapacity(' 5,12 ', 1000, null)).toEqual({ ok: true, value: 5.12 });
    expect(parseCapacity('1000', 1000, null)).toEqual({ ok: true, value: 1000 });
  });

  it('refuses empty, zero, too large, malformed or unchanged values', () => {
    for (const bad of ['', '0', '-1', 'abc', '1e3', '5.123', '1000.01', '10.2.4']) {
      expect(parseCapacity(bad, 1000, null).ok, `"${bad}"`).toBe(false);
    }
    expect(parseCapacity('6.45', 1000, 6.45)).toMatchObject({ ok: false });
  });

  it('names the source and the fallback', () => {
    expect(describeSource({ source: 'saved', rated_capacity_ah: 126 })).toBe('set on this page');
    expect(describeSource({ source: 'BATTERY_CAPACITY_KWH', rated_capacity_ah: 126 })).toBe('from .env');
    expect(describeSource({ source: 'inverter', rated_capacity_ah: 126 })).toBe('126 Ah setting');
    expect(fallback({ env_kwh: 10, inverter_kwh: 6.45, rated_capacity_ah: 126 })).toBe('10 kWh (from .env)');
    expect(fallback({ env_kwh: null, inverter_kwh: 6.45, rated_capacity_ah: 126 })).toBe('6.45 kWh (126 Ah setting)');
    expect(fallback({ env_kwh: null, inverter_kwh: null, rated_capacity_ah: null })).toBe('the inverter setting');
  });
});
