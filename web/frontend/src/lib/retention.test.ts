import { describe, expect, it } from 'vitest';
import { cutoffDay, deletes, describeDays, describeSource, fallback, parseRetention } from './retention';

const P = { min_days: 35, max_days: 3650, days: 90 };

describe('retention of the 10-second readings', () => {
  it('accepts whole days in range, or keeping everything', () => {
    expect(parseRetention('180', false, P)).toEqual({ ok: true, value: 180 });
    expect(parseRetention(' 35 ', false, P)).toEqual({ ok: true, value: 35 });
    expect(parseRetention('', true, P)).toEqual({ ok: true, value: 0 });
  });
  it('refuses the rest', () => {
    for (const bad of ['', '34', '3651', '90.5', '-1', 'abc', '1e2']) expect(parseRetention(bad, false, P).ok, bad).toBe(false);
    expect(parseRetention('90', false, P)).toMatchObject({ ok: false });            // nothing changed
    expect(parseRetention('', true, { ...P, days: 0 })).toMatchObject({ ok: false });
  });
  it('knows when a change deletes readings', () => {
    expect(deletes(90, 60)).toBe(true); expect(deletes(0, 90)).toBe(true);
    expect(deletes(90, 180)).toBe(false); expect(deletes(90, 0)).toBe(false); expect(deletes(60, 60)).toBe(false);
  });
  it('names things', () => {
    expect(describeDays(0)).toBe('everything'); expect(describeDays(90)).toBe('90 days');
    expect(describeSource({ source: 'saved' })).toBe('set on this page');
    expect(fallback({ env_days: 120, default_days: 90 })).toBe('120 days (from .env)');
    expect(fallback({ env_days: null, default_days: 90 })).toBe('90 days (default)');
    expect(cutoffDay(0, new Date(2026, 10, 13, 12))).toBeNull();
    expect(cutoffDay(90, new Date(2026, 10, 13, 12))).toBe('2026-08-15');
  });
});
