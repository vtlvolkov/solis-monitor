// How long the 10-second readings are kept: types and pure helpers for the Settings card (unit-tested).
import { locale, t } from './i18n';
import type { Parsed } from './tariff';

export type RetentionSource = 'saved' | 'RAW_RETENTION_DAYS' | 'default';

export interface RetentionPayload {
  days: number; source: RetentionSource; saved_days: number | null; env_days: number | null; default_days: number;
  min_days: number; max_days: number;
  raw_first: string | null; agg_first: string | null;     // the oldest 10-second reading and the oldest 1-minute average
  web: { can_change: boolean; reason: string | null };
  applied?: string;
}

/** "90 days" or "everything" (0). */
export const describeDays = (days: number): string => (days === 0 ? t('everything') : t('{n} days', { n: days }));

export function describeSource(p: Pick<RetentionPayload, 'source'>): string {
  if (p.source === 'saved') return t('set on this page');
  if (p.source === 'RAW_RETENTION_DAYS') return t('from .env');
  return t('default');
}

/** What removing the saved value falls back to. */
export const fallback = (p: Pick<RetentionPayload, 'env_days' | 'default_days'>): string =>
  p.env_days != null ? `${describeDays(p.env_days)} (${t('from .env')})` : `${describeDays(p.default_days)} (${t('default')})`;

/** The form: a whole number of days in [min, max], or 0 when "keep everything" is ticked. */
export function parseRetention(input: string, keepAll: boolean, p: Pick<RetentionPayload, 'min_days' | 'max_days' | 'days'>): Parsed<number> {
  const v = keepAll ? 0 : /^\d+$/.test(input.trim()) ? Number(input.trim()) : NaN;
  if (!keepAll && !(v >= p.min_days && v <= p.max_days)) {
    return { ok: false, error: t('Enter a whole number of days from {min} to {max}, or keep everything', { min: p.min_days, max: p.max_days }) };
  }
  if (v === p.days) return { ok: false, error: t('Nothing changed') };
  return { ok: true, value: v };
}

/** The first local day that is lost when keeping `days` (null = nothing is deleted). */
export function cutoffDay(days: number, now: Date): string | null {
  if (!days) return null;
  const d = new Date(now.getTime() - days * 86_400_000);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

/** Does going from `before` to `after` delete readings that are kept now? */
export const deletes = (before: number, after: number): boolean => after !== 0 && (before === 0 || after < before);

export const dateLabel = (iso: string | null): string =>
  iso ? new Date(iso.length === 10 ? `${iso}T12:00:00` : iso).toLocaleDateString(locale(), { day: 'numeric', month: 'long', year: 'numeric' }) : '--';
