// Grid quality page: the payload shape and the pure helpers (limits, labels), unit-tested.
import { t, tx } from './i18n';

/** EN 50160 / ДСТУ: 230 V ±10 % and 50 Hz ±0.2 Hz are "within the standard". Other limits can be set on the page;
 *  they are kept in this browser and sent to the server, which checks them against the same bounds. */
export interface Limits { v_min: number; v_max: number; hz_min: number; hz_max: number }
export const STANDARD: Limits = { v_min: 207, v_max: 253, hz_min: 49.8, hz_max: 50.2 };
export const LIMIT_BOUNDS: Record<keyof Limits, [number, number]> = { v_min: [150, 229], v_max: [231, 300], hz_min: [45, 49.99], hz_max: [50.01, 55] };
export const V_NOMINAL = 230;
const LIMITS_KEY = 'solis-grid-limits';

export function loadLimits(): Limits {
  try {
    const raw = JSON.parse(localStorage.getItem(LIMITS_KEY) ?? 'null');
    return raw && limitsError(raw) === null ? { v_min: +raw.v_min, v_max: +raw.v_max, hz_min: +raw.hz_min, hz_max: +raw.hz_max } : STANDARD;
  } catch { return STANDARD; }
}
export function saveLimits(l: Limits): void {
  try { if (isStandard(l)) localStorage.removeItem(LIMITS_KEY); else localStorage.setItem(LIMITS_KEY, JSON.stringify(l)); } catch { /* storage blocked */ }
}
export const isStandard = (l: Limits): boolean => (Object.keys(STANDARD) as (keyof Limits)[]).every((k) => l[k] === STANDARD[k]);

/** Why a set of limits is not acceptable (the same bounds as the server), or null. */
export function limitsError(l: Partial<Record<keyof Limits, unknown>>): string | null {
  for (const k of Object.keys(LIMIT_BOUNDS) as (keyof Limits)[]) {
    const v = Number(l[k]);
    const [lo, hi] = LIMIT_BOUNDS[k];
    if (!Number.isFinite(v) || v < lo || v > hi) return t('{name} must be between {lo} and {hi}', { name: LIMIT_NAMES[k], lo, hi });
  }
  return null;
}
export const LIMIT_NAMES: Record<keyof Limits, string> = { v_min: tx('Lowest voltage'), v_max: tx('Highest voltage'), hz_min: tx('Lowest frequency'), hz_max: tx('Highest frequency') };

export const qualityUrl = (range: GridRange, l: Limits): string =>
  `/api/grid/quality?range=${range}` + (isStandard(l) ? '' : `&v_min=${l.v_min}&v_max=${l.v_max}&hz_min=${l.hz_min}&hz_max=${l.hz_max}`);

export const vBand = (l: Limits): string => `${l.v_min}–${l.v_max} ${t('V')}`;
export const hzBand = (l: Limits): string => `${l.hz_min.toFixed(1)}–${l.hz_max.toFixed(1)} ${t('Hz')}`;

export type GridRange = '24h' | '7d' | '30d';
export const GRID_RANGES: GridRange[] = ['24h', '7d', '30d'];

export interface Bin { from: number; count: number }          // a histogram bar: [from, from + width)
export interface HourRow { hour: number; mean: number | null; min: number | null; max: number | null; out_pct: number | null }
export interface DayRow {
  date: string; readings: number; min_v: number | null; max_v: number | null; mean_v: number | null;
  out_min: number; sags: number; surges: number; hz_out_min: number; outages: number; outage_s: number;
}
export interface GridQualityPayload {
  range: GridRange; step_s: number; tz: string; limits: Limits;
  voltage: { mean: number | null; min: number | null; max: number | null; p05: number | null; p95: number | null; bins: Bin[]; bin_width: number };
  frequency: { mean: number | null; min: number | null; max: number | null; bins: Bin[]; bin_width: number };
  out: { v_pct: number; hz_pct: number; v_min: number; hz_min: number; sags: number; surges: number };   // minutes outside, and counts of excursions
  outages: { count: number; total_s: number; longest_s: number };
  hours: HourRow[];
  days: DayRow[];
}

export const fmtV = (v: number | null | undefined, digits = 0): string => (v == null ? '--' : `${v.toFixed(digits)} ${t('V')}`);
export const fmtHz = (v: number | null | undefined): string => (v == null ? '--' : `${v.toFixed(2)} ${t('Hz')}`);
export const fmtPct = (v: number | null | undefined): string => (v == null ? '--' : v < 0.05 && v > 0 ? '<0.1 %' : `${v.toFixed(1)} %`);

export function fmtMinutes(min: number): string {
  if (min <= 0) return t('{n} min', { n: 0 });
  if (min < 1) return t('< 1 min');
  if (min < 60) return t('{n} min', { n: Math.round(min) });
  const h = Math.floor(min / 60), m = Math.round(min % 60);
  return m ? t('{h} h {m} min', { h, m }) : t('{n} h', { n: h });
}

/** A one-line verdict for the period, from the time outside the voltage band. */
export function verdict(outPct: number, outages: number): { text: string; tone: 'ok' | 'warn' | 'bad' } {
  if (outPct < 0.5 && outages === 0) return { text: t('Within the standard almost all the time'), tone: 'ok' };
  if (outPct < 5) return { text: t('Mostly within the standard, with some excursions'), tone: 'warn' };
  return { text: t('Often outside the standard'), tone: 'bad' };
}

/** Bars outside the band are drawn in the warning colour. */
export const outOfBand = (from: number, width: number, lo: number, hi: number): boolean => from + width <= lo || from >= hi;

export const RANGE_LABELS: Record<GridRange, string> = { '24h': tx('24h'), '7d': tx('7d'), '30d': tx('30d') };
