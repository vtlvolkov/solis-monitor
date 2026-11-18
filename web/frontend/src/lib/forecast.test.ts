import { describe, expect, it } from 'vitest';
import type { HistoryPayload } from './api';
import { compass, configToForm, formToConfig, parsePlace, share, tomorrowTip, weatherIcon, withForecast, type ForecastPayload } from './forecast';

const P: ForecastPayload = {
  configured: true, show: true, error: null, fetched_at: null, stale: false, kwp: 4.2, arrays: [{ kwp: 4.2, tilt: 35, azimuth: 180 }],
  losses: 14, correct: true, factor: 0.9, factor_hours: 40,
  days: [{ date: '2026-11-18', kwh: 8.4, rest_kwh: 1.5, cloud_pct: 40 }, { date: '2026-11-19', kwh: 2.9, rest_kwh: null, cloud_pct: 95 }],
  usual_pv_kwh: 8.7, usual_load_kwh: 11, load_by_hour: null, below_pct: 50, recent: [], miss_pct: null,
};

describe('solar forecast helpers', () => {
  it('reads a location as Google Maps copies it, and refuses nonsense', () => {
    expect(parsePlace('50.4501, 30.5234')).toEqual({ lat: 50.4501, lon: 30.5234 });
    expect(parsePlace(' 50.45 30.52 ')).toEqual({ lat: 50.45, lon: 30.52 });
    expect(parsePlace('50.45;30.52')).toEqual({ lat: 50.45, lon: 30.52 });
    expect(parsePlace('-33.9, 151.2')).toEqual({ lat: -33.9, lon: 151.2 });
    expect(parsePlace('95, 30')).toBeNull();
    expect(parsePlace('Kyiv')).toBeNull();
  });

  it('turns the form into settings, with a decimal comma, or says what is wrong', () => {
    const f = { ...configToForm(null), place: '50.45, 30.52', arrays: [{ kwp: '4,2', tilt: '35', azimuth: '360' }] };
    expect(formToConfig(f)).toEqual({ lat: 50.45, lon: 30.52, arrays: [{ kwp: 4.2, tilt: 35, azimuth: 0 }], losses: 14,
                                      correct: true, telegram: false, below_pct: 50, overview: true });
    expect(typeof formToConfig({ ...f, place: '' })).toBe('string');
    expect(formToConfig({ ...f, arrays: [{ kwp: '0', tilt: '35', azimuth: '180' }] })).toMatch(/kWp/);
    expect(formToConfig({ ...f, arrays: [{ kwp: '4', tilt: '95', azimuth: '180' }] })).toMatch(/Tilt/);
    expect(formToConfig({ ...f, below_pct: '5' })).toMatch(/Telegram/);
    const back = configToForm(formToConfig(f) as never);
    expect(back.place).toBe('50.45, 30.52');
  });

  it('names directions and weather', () => {
    expect([180, 90, 270, 0, 135, 359].map(compass)).toEqual(['south', 'east', 'west', 'north', 'south-east', 'north']);
    expect([10, 40, 70, 95, null].map(weatherIcon)).toEqual(['☀️', '🌤️', '⛅', '☁️', '']);
    expect(share(2.9, 8.7)).toBe(33);
    expect(share(2.9, null)).toBeNull();
  });

  it('warns about a weak tomorrow only under the threshold', () => {
    expect(tomorrowTip(P)).toMatch(/^Tomorrow the panels give ≈ 2\.9 kWh, 33% of a usual day\. The house usually uses ≈ 11\.0 kWh/);
    expect(tomorrowTip({ ...P, below_pct: 30 })).toBeNull();
    expect(tomorrowTip({ ...P, usual_load_kwh: null })).toBe('Tomorrow the panels give ≈ 2.9 kWh, 33% of a usual day.');
    expect(tomorrowTip({ ...P, usual_pv_kwh: null })).toBeNull();
  });

  it('puts the hourly forecast on the chart’s own times, and runs on to the end of the day', () => {
    const h: HistoryPayload = { every: '5m', agg: 'mean', t: [3600, 3900, 7200], series: { pv_power: [10, 20, 30] }, stop: 9000 };
    const hours: [number, number][] = [[3600, 1000], [7200, 2000]];          // middles at 5400 and 9000
    const out = withForecast(h, hours, 14000);
    expect(out.t).toEqual([3600, 3900, 7200, 10500, 13800]);                  // on in the last step (3300 s) to the end
    expect(out.series.pv_forecast).toEqual([1000, 1000, 1500, 2000, null]);
    expect(out.series.pv_power).toEqual([10, 20, 30, null, null]);
    expect(withForecast(h, hours).t).toEqual(h.t);                            // the live ranges end now
    expect(withForecast(h, []).series.pv_forecast).toBeUndefined();
    const gap = withForecast({ ...h, t: [5400, 12600, 16200], series: { pv_power: [1, 2, 3] } }, [[3600, 1000], [14400, 3000]]);
    expect(gap.series.pv_forecast).toEqual([1000, null, 3000]);               // nothing drawn across a missing hour
  });
});
