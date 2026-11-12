import { describe, expect, it } from 'vitest';
import { allowedResolutions, costExportUrl, estimateRows, eventsExportUrl, exportUrl, periodSeconds } from './export';

describe('CSV export', () => {
  it('measures the period', () => {
    expect(periodSeconds({ range: '6h' })).toBe(6 * 3600);
    expect(periodSeconds({ from: '2026-11-01', to: '2026-11-03' })).toBe(3 * 86400);
    expect(periodSeconds({ from: '2026-11-03', to: '2026-11-01' })).toBe(0);
    expect(periodSeconds({ from: 'nope', to: '2026-11-01' })).toBe(0);
  });
  it('offers 10 s up to a day, 1 min up to a week, and never a huge file', () => {
    expect(allowedResolutions(3600)).toEqual(['10s', '1m', '15m', '1h', '1d']);
    expect(allowedResolutions(86400)).toEqual(['10s', '1m', '15m', '1h', '1d']);
    expect(allowedResolutions(2 * 86400)).toEqual(['1m', '15m', '1h', '1d']);
    expect(allowedResolutions(30 * 86400)).toEqual(['15m', '1h', '1d']);
    expect(allowedResolutions(400 * 86400)).toEqual(['15m', '1h', '1d']);   // 15 min over 400 days is 38k rows: fine; 1 min would not be
  });
  it('estimates rows', () => {
    expect(estimateRows(6 * 3600, '1m')).toBe(360);
    expect(estimateRows(86400, '10s')).toBe(8640);
    expect(estimateRows(10, '1d')).toBe(1);
  });
  it('builds the URLs the server expects', () => {
    expect(exportUrl({ period: { range: '6h' }, resolution: '1m', groups: ['power'], separator: 'comma' }))
      .toBe('/api/export.csv?range=6h&every=1m&fields=grid_power%2Cbattery_power_signed%2Ctotal_load_power%2Chouse_load_power%2Cbackup_load_power%2Cpv_power&sep=%2C');
    const u = exportUrl({ period: { from: '2026-11-01', to: '2026-11-02' }, resolution: '1d', groups: ['energy', 'grid'], separator: 'semicolon' });
    expect(u).toContain('from=2026-11-01&to=2026-11-02&every=1d');
    expect(u).toContain('grid_import_today');
    expect(u).toContain('grid_voltage');
    expect(u.endsWith('sep=%3B')).toBe(true);
    expect(costExportUrl('2026-11', 'semicolon')).toBe('/api/export/cost.csv?month=2026-11&sep=%3B');
    expect(eventsExportUrl(30, null, 'comma')).toBe('/api/export/events.csv?days=30&sep=,');
    expect(eventsExportUrl(30, '2026-11-12', 'comma')).toBe('/api/export/events.csv?date=2026-11-12&sep=,');
  });
});
