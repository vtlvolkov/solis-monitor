// Backup runtime estimate and grid-outage detection (pure, unit-tested).
import type { Fields } from './api';
import { t } from './i18n';

const ok = (v: number | null | undefined): v is number => typeof v === 'number' && Number.isFinite(v);

/**
 * Hours the battery can carry `loadW` from `socPct` down to `floorPct` (the SOC where the
 * inverter stops discharging into the backup output). null when inputs are missing or
 * the load is too small to give a meaningful number.
 */
export function runtimeHours(socPct: number | null | undefined, floorPct: number | null | undefined,
                             capacityKwh: number | null | undefined, loadW: number | null | undefined): number | null {
  if (!ok(socPct) || !ok(floorPct) || !ok(capacityKwh) || !ok(loadW) || capacityKwh <= 0) return null;
  if (loadW < 20) return null;
  const usableKwh = (capacityKwh * Math.max(0, socPct - floorPct)) / 100;
  return usableKwh / (loadW / 1000);
}

/** Average charging power over the last 15 minutes, leaving out the time it was not charging (just started, a pause). */
export function chargeRate(t: number[], watts: (number | null)[], nowS: number): number | null {
  return mean(watts.filter((w, i) => t[i] >= nowS - 900 && ok(w) && w >= 20));
}

/** Hours until `targetPct` at `chargeW`. null when inputs are missing, the rate is too small to mean anything, or it is already there. */
export function fullChargeHours(socPct: number | null | undefined, targetPct: number | null | undefined,
                                capacityKwh: number | null | undefined, chargeW: number | null | undefined): number | null {
  if (!ok(socPct) || !ok(targetPct) || !ok(capacityKwh) || !ok(chargeW) || capacityKwh <= 0) return null;
  if (chargeW < 50 || socPct >= targetPct) return null;
  return (capacityKwh * (targetPct - socPct)) / 100 / (chargeW / 1000);
}

export function formatHours(h: number | null): string {
  if (h == null) return '--';
  if (h <= 0) return t('{n} min', { n: 0 });
  if (h < 1 / 6) return t('< 10 min');
  if (h >= 72) return t('{n} days', { n: Math.round(h / 24) });
  const total = Math.round(h * 60 / 10) * 10;          // nearest 10 minutes
  const hh = Math.floor(total / 60), mm = total % 60;
  return hh === 0 ? t('{n} min', { n: mm }) : mm === 0 ? t('{n} h', { n: hh }) : t('{h} h {m} min', { h: hh, m: mm });
}

export const NO_GRID_STATUS = 0x1015;

/** Same rule as the poller: "No grid" fault bit, status 0x1015, or grid voltage below 100 V. */
export function gridLost(f: Fields | null | undefined): boolean {
  if (!f) return false;
  if (((f.fault_code_01 ?? 0) & 1) === 1) return true;
  if (f.status_code === NO_GRID_STATUS) return true;
  return ok(f.grid_voltage) && f.grid_voltage < 100;
}

export function mean(vals: (number | null)[]): number | null {
  const v = vals.filter(ok);
  return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null;
}
