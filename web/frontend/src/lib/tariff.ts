// Electricity tariff settings: types and pure helpers for the Settings card (unit-tested).
import { locale, t, unit } from './i18n';

export interface TariffValues { day: number; night: number | null; night_start: string | null; night_end: string | null }
export interface TariffVersion extends TariffValues { from: string | null }   // from = null: valid since the beginning

export interface TariffPayload {
  currency: string; tz: string; configured: boolean; current: TariffValues | null; source: 'env' | 'saved';
  versions: TariffVersion[]; upcoming: TariffVersion[];
  next_change_at: string;                       // when a change saved now would start (next quarter-hour)
  can_change: boolean; reason: string | null;
  effective_from?: string;                      // only in the answer to a save
}

export interface TariffForm { day: string; night: string; start: string; end: string; when: 'now' | 'date'; date: string }
export interface TariffRequest { day: number; night: number | null; night_start: string | null; night_end: string | null; from: string | null }

export type Parsed<T> = { ok: true; value: T } | { ok: false; error: string };

const PRICE = /^\d+([.,]\d{1,4})?$/;
const HHMM = /^([01]\d|2[0-3]):[0-5]\d$/;
const DATE = /^\d{4}-\d{2}-\d{2}$/;
export const MAX_PRICE = 10_000;

const price = (s: string): number => (PRICE.test(s.trim()) ? Number(s.trim().replace(',', '.')) : NaN);

/** A form filled with the current prices (or empty if there are none yet). */
export function formFor(v: TariffValues | null): TariffForm {
  return {
    day: v ? String(v.day) : '', night: v?.night != null ? String(v.night) : '',
    start: v?.night_start ?? '23:00', end: v?.night_end ?? '07:00', when: 'now', date: '',
  };
}

/** Validates the form like the server does; `today` is the site's date (YYYY-MM-DD), `current` what is in force now. */
export function parseTariffForm(f: TariffForm, today: string, current: TariffValues | null): Parsed<TariffRequest> {
  const day = price(f.day);
  if (!(day > 0 && day <= MAX_PRICE)) return { ok: false, error: t('Enter the day price, a number above 0') };
  let night: number | null = null;
  if (f.night.trim() !== '') {
    night = price(f.night);
    if (!(night > 0 && night <= MAX_PRICE)) return { ok: false, error: t('The night price must be a number above 0, or empty') };
    if (!HHMM.test(f.start) || !HHMM.test(f.end)) return { ok: false, error: t('Times must be HH:MM') };
    if (f.start === f.end) return { ok: false, error: t('Start and end must differ') };
  }
  let from: string | null = null;
  if (f.when === 'date') {
    if (!DATE.test(f.date)) return { ok: false, error: t('Pick the date the new prices start on') };
    if (f.date < today) return { ok: false, error: t('The date cannot be in the past: earlier consumption keeps its price') };
    from = f.date;
  }
  const value: TariffRequest = { day, night, night_start: night != null ? f.start : null, night_end: night != null ? f.end : null, from };
  const same = current && current.day === day && current.night === night
    && current.night_start === value.night_start && current.night_end === value.night_end;
  if (same && from === null) return { ok: false, error: t('Nothing changed') };
  return { ok: true, value };
}

/** "Day 4.32 UAH/kWh · night 2.16 UAH/kWh, 23:00–07:00", or "4.32 UAH/kWh, one price all day". */
export function describeTariff(v: TariffValues, currency: string): string {
  const u = `${currency}/${unit('kWh')}`;
  return v.night == null
    ? t('{day} {u}, one price all day', { day: v.day, u })
    : t('Day {day} {u} · night {night} {u}, {start}–{end}', { day: v.day, night: v.night, u, start: v.night_start ?? '', end: v.night_end ?? '' });
}

/** A moment in the site's time zone, e.g. "22.10.26, 15:15". */
export function formatMoment(iso: string, tz: string): string {
  return new Date(iso).toLocaleString(locale(), { timeZone: tz, day: '2-digit', month: '2-digit', year: '2-digit', hour: '2-digit', minute: '2-digit' });
}

/** The site's calendar date for `d`, as YYYY-MM-DD (for the date input's minimum). */
export function dateIn(tz: string, d: Date = new Date()): string {
  return d.toLocaleDateString('en-CA', { timeZone: tz });
}

/** Local midnight of `date` in the site's zone is what the server uses; this is only for showing it. */
export const startOfDay = (date: string): string => `${date.slice(8)}.${date.slice(5, 7)}.${date.slice(2, 4)}, 00:00`;
