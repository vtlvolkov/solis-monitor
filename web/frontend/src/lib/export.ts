// CSV export: what the dialog offers and the download URL it builds (pure, unit-tested).
import type { Range } from './api';
import { t, tx } from './i18n';

export type Resolution = '10s' | '1m' | '15m' | '1h' | '1d';
export type Separator = 'comma' | 'semicolon';     // semicolon + decimal comma is what Excel expects in a Ukrainian locale

export const RESOLUTIONS: { key: Resolution; label: string; step: number }[] = [
  { key: '10s', label: tx('10 s'), step: 10 },
  { key: '1m', label: tx('1 min'), step: 60 },
  { key: '15m', label: tx('15 min'), step: 900 },
  { key: '1h', label: tx('1 h'), step: 3600 },
  { key: '1d', label: tx('1 day'), step: 86400 },
];

/** Groups of columns; the server knows the fields (web/backend/app/validation.py). */
export const COLUMN_GROUPS: { key: string; label: string; hint: string; fields: string[] }[] = [
  { key: 'power', label: tx('Power'), hint: tx('grid, battery, load, PV (W)'),
    fields: ['grid_power', 'battery_power_signed', 'total_load_power', 'house_load_power', 'backup_load_power', 'pv_power'] },
  { key: 'battery', label: tx('Battery'), hint: tx('SOC, voltage, current, temperature'),
    fields: ['battery_soc', 'battery_voltage', 'battery_current', 'battery_temperature'] },
  { key: 'grid', label: tx('Grid'), hint: tx('voltage, frequency, backup voltage'),
    fields: ['grid_voltage', 'grid_frequency', 'backup_voltage'] },
  { key: 'energy', label: tx('Energy counters'), hint: tx('kWh today: import, export, charge, discharge, load, PV'),
    fields: ['grid_import_today', 'grid_export_today', 'battery_charge_today', 'battery_discharge_today', 'load_consumption_today', 'pv_energy_today'] },
  { key: 'status', label: tx('Status & faults'), hint: tx('status code, fault registers'),
    fields: ['status_code', 'fault_code_01', 'fault_code_02', 'fault_code_03', 'fault_code_04', 'fault_code_05', 'inverter_temperature'] },
];

export const RANGE_SECONDS: Record<Range, number> = { '1h': 3600, '6h': 6 * 3600, '24h': 86400, '7d': 7 * 86400, '30d': 30 * 86400 };

export interface ExportRequest {
  period: { range: Range } | { from: string; to: string };     // a live range, or local days (inclusive)
  resolution: Resolution;
  groups: string[];
  separator: Separator;
}

/** Seconds covered by the period. */
export function periodSeconds(p: ExportRequest['period']): number {
  if ('range' in p) return RANGE_SECONDS[p.range];
  const a = Date.parse(`${p.from}T00:00:00Z`), b = Date.parse(`${p.to}T00:00:00Z`);
  if (Number.isNaN(a) || Number.isNaN(b) || b < a) return 0;
  return (b - a) / 1000 + 86400;
}

/** Resolutions that make sense for a period: raw 10 s only up to a day, 1 min up to a week; never more than ~100k rows. */
export function allowedResolutions(seconds: number): Resolution[] {
  return RESOLUTIONS.filter((r) => r.step >= 86400 || seconds / r.step <= 100_000)
    .filter((r) => !(r.key === '10s' && seconds > 86400) && !(r.key === '1m' && seconds > 7 * 86400))
    .map((r) => r.key);
}

export function estimateRows(seconds: number, resolution: Resolution): number {
  const step = RESOLUTIONS.find((r) => r.key === resolution)?.step ?? 60;
  return Math.max(1, Math.ceil(seconds / step));
}

/** The CSV endpoint for a request (the server validates every value again). */
export function exportUrl(req: ExportRequest): string {
  const p = new URLSearchParams();
  if ('range' in req.period) p.set('range', req.period.range);
  else { p.set('from', req.period.from); p.set('to', req.period.to); }
  p.set('every', req.resolution);
  p.set('fields', COLUMN_GROUPS.filter((g) => req.groups.includes(g.key)).flatMap((g) => g.fields).join(','));
  p.set('sep', req.separator === 'semicolon' ? ';' : ',');
  return `/api/export.csv?${p}`;
}

/** The cost history month as CSV: one row per day with import, zones and cost. */
export const costExportUrl = (month: string, separator: Separator): string =>
  `/api/export/cost.csv?month=${encodeURIComponent(month)}&sep=${separator === 'semicolon' ? '%3B' : ','}`;

/** The Events page's outages and alarms: one exact day, or the last N days. */
export const eventsExportUrl = (days: number, day: string | null, separator: Separator): string =>
  `/api/export/events.csv?${day ? `date=${day}` : `days=${days}`}&sep=${separator === 'semicolon' ? '%3B' : ','}`;

/** The separator the export dialog last used (so the one-click exports match). */
export function savedSeparator(): Separator {
  try { return localStorage.getItem('solis-csv-sep') === 'semicolon' ? 'semicolon' : 'comma'; } catch { return 'comma'; }
}

export function describePeriod(p: ExportRequest['period']): string {
  if ('range' in p) return t('the shown range ({range})', { range: t(p.range) });
  return p.from === p.to ? p.from : t('{from} to {to}', { from: p.from, to: p.to });
}

export function formatRows(n: number): string {
  return n >= 1000 ? t('about {n} thousand rows', { n: Math.round(n / 1000) }) : t('about {n} rows', { n });
}
