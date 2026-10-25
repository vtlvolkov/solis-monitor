// Gauge scale and colour zones shared by the power gauges (pure, unit-tested).

/** The inverter's maximum output, the right-hand end of the backup-load gauge. */
export const GAUGE_MAX_W = 6000;
/** Right-hand end of the grid gauge. Not a setting we can read from the inverter: change it if your grid supply is smaller. */
export const GAUGE_GRID_MAX_W = 6000;
/** Fractions of the scale where the colour changes: amber from 60% (3.6 kW), red from 85% (5.1 kW). */
export const WARN_AT = 0.6;
export const DANGER_AT = 0.85;

export type Zone = 'ok' | 'warn' | 'danger';

/** 0..1 position on the scale; missing or negative readings sit at 0, above the maximum at 1. */
export function fraction(watts: number | null | undefined, max = GAUGE_MAX_W): number {
  if (watts == null || !Number.isFinite(watts) || watts <= 0) return 0;
  return Math.min(1, watts / max);
}

export function zone(watts: number | null | undefined, max = GAUGE_MAX_W): Zone {
  const f = fraction(watts, max);
  return f >= DANGER_AT ? 'danger' : f >= WARN_AT ? 'warn' : 'ok';
}

export const ZONE_COLOR: Record<Zone, string> = {
  ok: 'var(--c-load)', warn: 'var(--c-warn)', danger: 'var(--c-danger)',
};

/** Nominal voltage of the LV LiFePO4 pack (poller/device.py NOMINAL_CELL_V), the fallback for turning amps into watts. */
export const NOMINAL_PACK_V = 51.2;

/**
 * Dial maximum for battery charge or discharge: the "Max ... current" setting times a reference voltage
 * (the pack's nominal voltage when none is given or it is not a sensible number).
 */
export const currentLimitW = (amps: number, volts?: number | null): number =>
  amps * (volts != null && Number.isFinite(volts) && volts > 0 ? volts : NOMINAL_PACK_V);

/** Charge power from the signed battery power (+ charging, − discharging): 0 while discharging, null if unknown. */
export function chargeWatts(signedW: number | null | undefined): number | null {
  return signedW == null || !Number.isFinite(signedW) ? null : Math.max(0, signedW);
}

/** Size of the grid power in either direction (+ import, − export), or null if unknown. */
export const gridWatts = (w: number | null | undefined): number | null =>
  w == null || !Number.isFinite(w) ? null : Math.abs(w);

/** Discharge power from the signed battery power (+ charging, − discharging): 0 while charging, null if unknown. */
export function dischargeWatts(signedW: number | null | undefined): number | null {
  return signedW == null || !Number.isFinite(signedW) ? null : Math.max(0, -signedW);
}
