import { describe, expect, it } from 'vitest';
import { STANDARD, fmtMinutes, isStandard, limitsError, outOfBand, qualityUrl, verdict } from './grid';

describe('grid quality helpers', () => {
  it('formats minutes', () => {
    expect(fmtMinutes(0)).toBe('0 min'); expect(fmtMinutes(0.4)).toBe('< 1 min'); expect(fmtMinutes(42.6)).toBe('43 min');
    expect(fmtMinutes(131)).toBe('2 h 11 min'); expect(fmtMinutes(120)).toBe('2 h');
  });
  it('gives a verdict', () => {
    expect(verdict(0.1, 0).tone).toBe('ok'); expect(verdict(0.1, 1).tone).toBe('warn'); expect(verdict(1.3, 0).tone).toBe('warn'); expect(verdict(7, 0).tone).toBe('bad');
  });
  it('marks bars outside the band', () => {
    expect(outOfBand(206, 1, 207, 253)).toBe(true); expect(outOfBand(207, 1, 207, 253)).toBe(false);
    expect(outOfBand(252, 1, 207, 253)).toBe(false); expect(outOfBand(253, 1, 207, 253)).toBe(true);
  });
  it('checks limits the way the server does', () => {
    expect(limitsError(STANDARD)).toBeNull();
    expect(limitsError({ ...STANDARD, v_min: 100 })).toMatch(/Lowest voltage/);
    expect(limitsError({ ...STANDARD, hz_max: 'x' })).toMatch(/Highest frequency/);
    expect(isStandard({ v_min: 207, v_max: 253, hz_min: 49.8, hz_max: 50.2 })).toBe(true);
  });
  it('sends limits only when they differ from the standard', () => {
    expect(qualityUrl('7d', STANDARD)).toBe('/api/grid/quality?range=7d');
    expect(qualityUrl('24h', { v_min: 200, v_max: 250, hz_min: 49.5, hz_max: 50.5 })).toBe('/api/grid/quality?range=24h&v_min=200&v_max=250&hz_min=49.5&hz_max=50.5');
  });
});
