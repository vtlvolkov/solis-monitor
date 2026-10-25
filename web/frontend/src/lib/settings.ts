// Settings page logic (pure, unit-tested).
import { t, tx, unit } from './i18n';

export type SettingKey = 'grid_charging' | 'time_of_use_mode' | 'reserve_mode'
  | 'max_charge_current_a' | 'max_discharge_current_a' | 'overdischarge_soc' | 'backup_soc'
  | 'max_charge_soc' | 'force_charge_soc'
  | 'tou_slot' | 'clock_sync';

export interface TouSlot {
  slot: number; enabled: boolean; soc: number; current_a: number; voltage: number; start: string; end: string;
}
export type TouKind = 'charge' | 'discharge';
export interface TouValue {
  kind: TouKind; slot: number; enabled: boolean; start: string; end: string; current_a: number; soc: number;
}

export interface SettingsPayload {
  grid_charging: boolean;
  time_of_use_mode: boolean;
  reserve_mode: boolean;
  battery: {
    max_charge_soc: number; overdischarge_soc: number; force_charge_soc: number; backup_soc: number;
    offgrid_overdischarge_soc: number; eps_dod: number; rated_capacity_ah: number;
    float_voltage: number; equalize_voltage: number;
  };
  clock: string | null;
  tou: { switch_raw: number; charge: TouSlot[]; discharge: TouSlot[] };
  storage_switch_raw: number;
  storage_flags: Record<string, boolean>;
  max_charge_current_raw: number;
  max_discharge_current_raw: number;
  max_charge_current_a: number;
  max_discharge_current_a: number;
  control: {
    enabled: boolean;
    current_scale: number;
    limits: Record<'max_charge_current_a' | 'max_discharge_current_a', number>;
    min_interval_s: number;
    next_change_in_s: number;
    server_time: string;
  };
  web: { auth_enabled: boolean; can_change: boolean; reason: string | null };
}

export interface ChangeResult {
  applied?: string;
  summary?: string;
  no_change?: boolean;
  verified?: boolean;
  warning?: string;
  written_raw?: number;
  read_back_raw?: number;
}

export interface ChangeLogEntry {
  time: string;
  action: string;
  summary?: string | null;
  verified?: boolean | null;
}

export const LABELS: Record<SettingKey, string> = {
  grid_charging: tx('Grid charging'),
  time_of_use_mode: tx('Time-of-Use mode'),
  reserve_mode: tx('Reserve battery mode'),
  max_charge_current_a: tx('Max charge current'),
  max_discharge_current_a: tx('Max discharge current'),
  overdischarge_soc: tx('Over-discharge SOC'),
  backup_soc: tx('Backup (reserve) SOC'),
  max_charge_soc: tx('Max charge SOC'),
  force_charge_soc: tx('Force-charge SOC'),
  tou_slot: tx('Time-of-Use slot'),
  clock_sync: tx('Inverter clock'),
};

const amps = (v: number) => `${+v.toFixed(1)} ${unit('A')}`;
/** Translated setting name. */
export const label = (key: SettingKey) => t(LABELS[key]);

export function describeChange(key: SettingKey, from: boolean | number, to: boolean | number): string {
  const name = label(key);
  if (key === 'grid_charging') return `${name}: ${from ? t('allowed') : t('forbidden')} → ${to ? t('allowed') : t('forbidden')}`;
  if (key === 'time_of_use_mode' || key === 'reserve_mode') return `${name}: ${from ? t('on') : t('off')} → ${to ? t('on') : t('off')}`;
  if (SOC_KEYS.includes(key as SocKey)) return `${name}: ${from}% → ${to}%`;
  const note = key === 'max_discharge_current_a' && to === 0 ? ` (${t('battery will not power loads')})` : '';
  return `${name}: ${amps(from as number)} → ${amps(to as number)}${note}`;
}

export type Parsed = { ok: true; value: number } | { ok: false; error: string };

/** Validates the amps typed into a current field against the server's hard limit. */
export function parseAmps(input: string, limit: number, current: number): Parsed {
  const txt = input.trim().replace(',', '.');
  if (txt === '') return { ok: false, error: t('Enter a value in A') };
  if (!/^\d+(\.\d)?$/.test(txt)) return { ok: false, error: t('Use a number with at most one decimal') };
  const value = Number(txt);
  if (limit <= 0) return { ok: false, error: t('No limit configured on the server') };
  if (value > limit) return { ok: false, error: t('Maximum is {max}', { max: amps(limit) }) };
  if (Math.abs(value - current) < 0.05) return { ok: false, error: t('That is the current value') };
  return { ok: true, value };
}

/** Why a specific setting can't be changed right now, or null. `limitKey` names the current limit it needs. */
export function blockedReason(s: SettingsPayload, key: SettingKey, waitS: number,
                              limitKey: 'max_charge_current_a' | 'max_discharge_current_a' | null = null): string | null {
  if (!s.web.can_change) return t(s.web.reason ?? 'Changes are disabled');
  const need = key === 'max_charge_current_a' || key === 'max_discharge_current_a' ? key : limitKey;
  if (need && (s.control.limits[need] ?? 0) <= 0) {
    return t('{name} is not set in .env', { name: need === 'max_charge_current_a' ? 'MAX_CHARGE_CURRENT_A' : 'MAX_DISCHARGE_CURRENT_A' });
  }
  if (waitS > 0) return t('Next change allowed in {n} s', { n: waitS });
  return null;
}

export type SocKey = 'overdischarge_soc' | 'backup_soc' | 'max_charge_soc' | 'force_charge_soc';
export const SOC_KEYS: SocKey[] = ['overdischarge_soc', 'backup_soc', 'max_charge_soc', 'force_charge_soc'];

/** Allowed [lo, hi] for a SOC setting. Mirrors the poller's checks (which have the final say). */
export function socBounds(b: SettingsPayload['battery'], key: SocKey): [number, number] {
  const floors = [b.offgrid_overdischarge_soc, b.eps_dod];
  switch (key) {
    case 'overdischarge_soc': return [Math.max(5, b.force_charge_soc + 1), b.max_charge_soc - 1];
    case 'backup_soc': return [b.overdischarge_soc, b.max_charge_soc];
    case 'max_charge_soc': return [Math.max(70, b.overdischarge_soc + 1, b.backup_soc, ...floors), 100];
    case 'force_charge_soc': return [5, Math.min(b.overdischarge_soc, ...floors) - 1];
  }
}

export function parseSoc(input: string, lo: number, hi: number, current: number): Parsed {
  const txt = input.trim().replace('%', '');
  if (!/^\d{1,3}$/.test(txt)) return { ok: false, error: t('Enter a whole percentage') };
  const value = Number(txt);
  if (value < lo || value > hi) return { ok: false, error: t('Allowed {lo}–{hi}%', { lo, hi }) };
  if (value === current) return { ok: false, error: t('That is the current value') };
  return { ok: true, value };
}

const HHMM = /^([01]\d|2[0-3]):([0-5]\d)$/;

export interface SlotForm { enabled: boolean; start: string; end: string; current: string; soc: string }

/** Validates a Time-of-Use slot edit. Bounds mirror the poller's (which has the final say). */
export function validateSlot(kind: TouKind, slot: TouSlot, f: SlotForm, limitA: number, floorSoc: number):
    { ok: true; value: TouValue } | { ok: false; error: string } {
  if (!HHMM.test(f.start) || !HHMM.test(f.end)) return { ok: false, error: t('Times must be HH:MM') };
  if (f.start === f.end) return { ok: false, error: t('Start and end must differ') };
  const cur = parseAmps(f.current, limitA, Number.NaN);
  if (!cur.ok) return cur;
  const lo = kind === 'charge' ? 10 : floorSoc;
  if (!/^\d{1,3}$/.test(f.soc.trim()) || +f.soc < lo || +f.soc > 100) return { ok: false, error: t('SOC must be {lo}–100%', { lo }) };
  const value: TouValue = { kind, slot: slot.slot, enabled: f.enabled, start: f.start, end: f.end, current_a: cur.value, soc: +f.soc };
  if (value.enabled === slot.enabled && value.start === slot.start && value.end === slot.end
      && Math.abs(value.current_a - slot.current_a) < 0.05 && value.soc === slot.soc) {
    return { ok: false, error: t('Nothing changed') };
  }
  return { ok: true, value };
}

export function describeSlot(kind: TouKind, s: TouSlot, v: TouValue): string {
  const fmt = (on: boolean, a: string, b: string, cur: number, soc: number) =>
    `${on ? t('on') : t('off')}, ${a}–${b}, ${amps(cur)}, ${kind === 'charge' ? t('up to') : t('down to')} ${soc}%`;
  return `${kind === 'charge' ? t('Charge slot {n}', { n: s.slot }) : t('Discharge slot {n}', { n: s.slot })}: ${fmt(s.enabled, s.start, s.end, s.current_a, s.soc)} → `
    + fmt(v.enabled, v.start, v.end, v.current_a, v.soc);
}

/** Inverter clock minus server clock, in seconds (both are local wall-clock times without zone). */
export function clockOffset(inverter: string | null, server: string | null): number | null {
  if (!inverter || !server) return null;
  const a = Date.parse(inverter + 'Z'), b = Date.parse(server + 'Z');
  return Number.isNaN(a) || Number.isNaN(b) ? null : Math.round((a - b) / 1000);
}
