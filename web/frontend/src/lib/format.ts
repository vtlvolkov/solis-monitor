// Number/unit formatting shared by the diagram and charts.
import { locale, t, unit } from './i18n';

const ok = (v: number | null | undefined): v is number => typeof v === 'number' && Number.isFinite(v);

/** Diagram values: always kW, 2 decimals. */
export function kw(w: number | null | undefined): string {
  return ok(w) ? (w / 1000).toFixed(2) : '--';
}

/** Charts: W below 1 kW, kW at 1 kW or more. */
export function power(w: number | null | undefined, signed = false): string {
  if (!ok(w)) return '--';
  const sign = signed && w > 0 ? '+' : '';
  if (Math.abs(w) < 1000) return `${sign}${Math.round(w)} ${unit('W')}`;
  return `${sign}${(w / 1000).toFixed(2)} ${unit('kW')}`;
}

export function kwh(v: number | null | undefined, digits = 1): string {
  return ok(v) ? `${v.toFixed(digits)} ${unit('kWh')}` : '--';
}

export function withUnit(v: number | null | undefined, u: string, digits = 1): string {
  return ok(v) ? `${v.toFixed(digits)} ${unit(u)}` : '--';
}

export function ago(seconds: number): string {
  if (seconds < 60) return t('{n} s', { n: Math.max(0, Math.round(seconds)) });
  if (seconds < 3600) return t('{n} min', { n: Math.floor(seconds / 60) });
  if (seconds < 86400) return t('{n} h', { n: Math.floor(seconds / 3600) });
  return t('{n} d', { n: Math.floor(seconds / 86400) });
}

/** Filled segments of a 10-bar SOC indicator (99% -> 9, like the Solis app). */
export function socSegments(soc: number | null | undefined, n = 10): number {
  if (!ok(soc)) return 0;
  const s = Math.min(100, Math.max(0, soc));
  return s > 0 ? Math.max(1, Math.floor((s / 100) * n)) : 0;
}

export function clockTime(iso: string | null | undefined): string {
  if (!iso) return '--:--';
  return new Date(iso).toLocaleTimeString(locale(), { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}
