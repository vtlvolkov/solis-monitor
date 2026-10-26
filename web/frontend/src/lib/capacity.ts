// Battery capacity used for the runtime estimate: types and pure helpers for the Settings card (unit-tested).
import { t, unit } from './i18n';
import type { Parsed } from './tariff';

export type CapacitySource = 'saved' | 'BATTERY_CAPACITY_KWH' | 'inverter';

export interface CapacityPayload {
  capacity_kwh: number | null; source: CapacitySource;
  saved_kwh: number | null; env_kwh: number | null; inverter_kwh: number | null; rated_capacity_ah: number | null;
  max_kwh: number;
  web: { can_change: boolean; reason: string | null };
  applied?: string;                              // only in the answer to a save
}

const KWH = /^\d+([.,]\d{1,2})?$/;

/** Validates the kWh field like the server does (above 0, at most `max`, two decimals). */
export function parseCapacity(input: string, max: number, current: number | null): Parsed<number> {
  const s = input.trim();
  const v = KWH.test(s) ? Number(s.replace(',', '.')) : NaN;
  if (!(v > 0 && v <= max)) return { ok: false, error: t('Enter the usable capacity in kWh, a number above 0 and at most {max}', { max }) };
  if (v === current) return { ok: false, error: t('Nothing changed') };
  return { ok: true, value: v };
}

/** Where the value in use comes from, e.g. "set on this page" or "inverter setting 126 Ah". */
export function describeSource(p: Pick<CapacityPayload, 'source' | 'rated_capacity_ah'>): string {
  if (p.source === 'saved') return t('set on this page');
  if (p.source === 'BATTERY_CAPACITY_KWH') return t('from .env');
  return t('{n} Ah setting', { n: p.rated_capacity_ah ?? '--' });
}

/** What removing the saved value falls back to: the .env value, else the inverter's estimate. */
export function fallback(p: Pick<CapacityPayload, 'env_kwh' | 'inverter_kwh' | 'rated_capacity_ah'>): string {
  if (p.env_kwh != null) return `${p.env_kwh} ${unit('kWh')} (${t('from .env')})`;
  if (p.inverter_kwh != null) return `${p.inverter_kwh} ${unit('kWh')} (${t('{n} Ah setting', { n: p.rated_capacity_ah ?? '--' })})`;
  return t('the inverter setting');
}
