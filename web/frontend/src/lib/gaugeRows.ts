// What each of the four power gauges shows, independent of how it is drawn (dial or bar). Unit-tested.
import type { Fields } from './api';
import { kw } from './format';
import { chargeWatts, currentLimitW, dischargeWatts, GAUGE_GRID_MAX_W, GAUGE_MAX_W, gridWatts } from './gauge';
import { t, unit } from './i18n';
import type { SettingsPayload } from './settings';

export type GaugeKind = 'grid' | 'load' | 'charge' | 'discharge';

export interface GaugeRow {
  kind: GaugeKind;
  title: string;                      // English text, translated where it is drawn
  watts: number | null;
  maxW: number;
  okColor: string;                    // colour below the amber zone
  warnText: string;                   // English text shown in the amber zone
  ariaLabel: string;
  note?: string;                      // e.g. "Importing", "Charging 1.30 kW"
  soc?: number | null;                // battery rows only
}

/** ready: draw it; loading: the settings it depends on are not known yet; off: the current limit is 0 A. */
export type GaugeEntry =
  | { kind: GaugeKind; title: string; state: 'ready'; row: GaugeRow }
  | { kind: GaugeKind; title: string; state: 'loading' }
  | { kind: GaugeKind; title: string; state: 'off'; text: string };

const label = (key: string, watts: number | null) => t(key, { n: watts != null ? kw(watts) : '--', u: unit('kW') });

export function loadRow(watts: number | null | undefined): GaugeEntry {
  const w = watts ?? null;
  return { kind: 'load', title: 'Backup load', state: 'ready', row: {
    kind: 'load', title: 'Backup load', watts: w, maxW: GAUGE_MAX_W, okColor: 'var(--c-load)', warnText: 'High load',
    ariaLabel: label('Backup load {n} {u}', w) } };
}

/** The size of the grid power in either direction, with a note whether it is importing or exporting. */
export function gridRow(power: number | null | undefined): GaugeEntry {
  const size = gridWatts(power);
  const importing = (power ?? 0) >= 20, exporting = (power ?? 0) <= -20;
  return { kind: 'grid', title: 'Grid', state: 'ready', row: {
    kind: 'grid', title: 'Grid', watts: size, maxW: GAUGE_GRID_MAX_W, okColor: exporting ? 'var(--c-grid-alt)' : 'var(--c-grid)',
    warnText: 'High grid power', ariaLabel: label('Grid {n} {u}', size),
    note: importing ? t('Importing') : exporting ? t('Exporting') : undefined } };
}

/**
 * Battery charge or discharge power. The maximum is the matching "Max ... current" setting times a reference voltage:
 * the equalize voltage for charging (the pack's highest charge voltage) and the float voltage for discharging, or the
 * nominal 51.2 V when the inverter doesn't report them.
 */
export function batteryRow(kind: 'charge' | 'discharge', fields: Fields | null, settings: SettingsPayload | null | undefined): GaugeEntry {
  const charge = kind === 'charge';
  const title = charge ? 'Battery charge' : 'Battery discharge';
  const maxA = settings == null ? null : charge ? settings.max_charge_current_a : settings.max_discharge_current_a;
  if (maxA == null) return { kind, title, state: 'loading' };
  if (maxA <= 0) {
    return { kind, title, state: 'off', text: charge ? t('Max charge current is 0 A: the battery is not charged.')
                                                    : t('Max discharge current is 0 A: the battery does not power loads.') };
  }
  const signed = fields?.battery_power_signed;
  const watts = charge ? chargeWatts(signed) : dischargeWatts(signed);
  const other = signed != null && (charge ? signed <= -20 : signed >= 20);        // the battery is going the other way
  const note = !other ? undefined
    : charge ? t('Discharging {n} {u}', { n: kw(signed == null ? null : -signed), u: unit('kW') })
             : t('Charging {n} {u}', { n: kw(signed), u: unit('kW') });
  return { kind, title, state: 'ready', row: {
    kind, title, watts, okColor: charge ? 'var(--c-battery-alt)' : 'var(--c-battery)', warnText: charge ? 'High charge rate' : 'High load',
    maxW: currentLimitW(maxA, charge ? settings!.battery.equalize_voltage : settings!.battery.float_voltage),
    ariaLabel: label(charge ? 'Battery charge {n} {u}' : 'Battery discharge {n} {u}', watts), note, soc: fields?.battery_soc ?? null } };
}

/** All four, in the order Grid, Backup load, Battery charge, Battery discharge. */
export const allGauges = (fields: Fields | null, settings: SettingsPayload | null | undefined): GaugeEntry[] => [
  gridRow(fields?.grid_power), loadRow(fields?.backup_load_power),
  batteryRow('charge', fields, settings), batteryRow('discharge', fields, settings),
];
