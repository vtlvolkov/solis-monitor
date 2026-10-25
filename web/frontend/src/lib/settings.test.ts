import { describe, expect, it } from 'vitest';
import {
  blockedReason, clockOffset, describeChange, describeSlot, parseAmps, parseSoc, socBounds, validateSlot,
  type SettingsPayload, type TouSlot,
} from './settings';

const base: SettingsPayload = {
  grid_charging: true, time_of_use_mode: false, reserve_mode: false, storage_switch_raw: 33, storage_flags: {},
  battery: { max_charge_soc: 100, overdischarge_soc: 20, force_charge_soc: 10, backup_soc: 80,
             offgrid_overdischarge_soc: 30, eps_dod: 20, rated_capacity_ah: 126, float_voltage: 53.5, equalize_voltage: 55 },
  clock: '2026-10-18T21:01:37', tou: { switch_raw: 1, charge: [], discharge: [] },
  max_charge_current_raw: 1000, max_discharge_current_raw: 1000,
  max_charge_current_a: 100, max_discharge_current_a: 100,
  control: { enabled: true, current_scale: 0.1, limits: { max_charge_current_a: 100, max_discharge_current_a: 0 },
             min_interval_s: 60, next_change_in_s: 0, server_time: '2026-10-18T21:03:41' },
  web: { auth_enabled: true, can_change: true, reason: null },
};

describe('describeChange', () => {
  it('grid charging', () => {
    expect(describeChange('grid_charging', true, false)).toBe('Grid charging: allowed → forbidden');
    expect(describeChange('grid_charging', false, true)).toBe('Grid charging: forbidden → allowed');
  });
  it('currents', () => {
    expect(describeChange('max_charge_current_a', 100, 30)).toBe('Max charge current: 100 A → 30 A');
    expect(describeChange('max_discharge_current_a', 100, 0)).toBe('Max discharge current: 100 A → 0 A (battery will not power loads)');
    expect(describeChange('max_charge_current_a', 0, 12.5)).toBe('Max charge current: 0 A → 12.5 A');
  });
});

describe('parseAmps', () => {
  it.each([
    ['30', true, 30], ['0', true, 0], ['12.5', true, 12.5], ['12,5', true, 12.5], ['100', true, 100],
  ])('accepts %s', (input, ok, value) => {
    expect(parseAmps(input as string, 100, 50)).toEqual({ ok, value });
  });
  it.each([
    ['', 'Enter a value'], ['abc', 'at most one decimal'], ['-5', 'at most one decimal'], ['1.25', 'at most one decimal'],
    ['1e2', 'at most one decimal'], ['100.1', 'Maximum is 100 A'], ['50', 'current value'],
  ])('rejects %s', (input, msg) => {
    const r = parseAmps(input as string, 100, 50);
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error).toContain(msg);
  });
  it('rejects everything without a limit', () => {
    expect(parseAmps('10', 0, 50)).toEqual({ ok: false, error: 'No limit configured on the server' });
  });
});

describe('blockedReason', () => {
  it('passes server reason through', () => {
    const s = { ...base, web: { auth_enabled: false, can_change: false, reason: 'Set WEB_BASIC_AUTH' } };
    expect(blockedReason(s, 'grid_charging', 0)).toBe('Set WEB_BASIC_AUTH');
  });
  it('needs a configured limit for currents only', () => {
    expect(blockedReason(base, 'max_discharge_current_a', 0)).toContain('MAX_DISCHARGE_CURRENT_A');
    expect(blockedReason(base, 'max_charge_current_a', 0)).toBeNull();
    expect(blockedReason(base, 'grid_charging', 0)).toBeNull();
  });
  it('rate limit', () => {
    expect(blockedReason(base, 'grid_charging', 42)).toBe('Next change allowed in 42 s');
  });
});

describe('new settings', () => {
  it('describes flags and SOC', () => {
    expect(describeChange('reserve_mode', false, true)).toBe('Reserve battery mode: off → on');
    expect(describeChange('backup_soc', 80, 40)).toBe('Backup (reserve) SOC: 80% → 40%');
    expect(describeChange('max_charge_soc', 100, 90)).toBe('Max charge SOC: 100% → 90%');
    expect(describeChange('force_charge_soc', 10, 15)).toBe('Force-charge SOC: 10% → 15%');
  });
  it('socBounds mirrors the poller', () => {
    const b = base.battery;   // max 100, over-discharge 20, force 10, backup 80, off-grid 30, backup output 20
    expect(socBounds(b, 'overdischarge_soc')).toEqual([11, 99]);
    expect(socBounds(b, 'backup_soc')).toEqual([20, 100]);
    expect(socBounds(b, 'max_charge_soc')).toEqual([80, 100]);
    expect(socBounds(b, 'force_charge_soc')).toEqual([5, 19]);
    expect(socBounds({ ...b, backup_soc: 30 }, 'max_charge_soc')).toEqual([70, 100]);
    expect(socBounds({ ...b, eps_dod: 12 }, 'force_charge_soc')).toEqual([5, 11]);
  });
  it('blocks currents and ToU slots without limits', () => {
    expect(blockedReason(base, 'tou_slot', 0, 'max_discharge_current_a')).toContain('MAX_DISCHARGE_CURRENT_A');
    expect(blockedReason(base, 'tou_slot', 0, 'max_charge_current_a')).toBeNull();
    expect(blockedReason(base, 'backup_soc', 0)).toBeNull();
  });
  it('parseSoc', () => {
    expect(parseSoc('40', 20, 100, 80)).toEqual({ ok: true, value: 40 });
    expect(parseSoc('40%', 20, 100, 80)).toEqual({ ok: true, value: 40 });
    expect(parseSoc('19', 20, 100, 80).ok).toBe(false);
    expect(parseSoc('80', 20, 100, 80).ok).toBe(false);
    expect(parseSoc('4.5', 20, 100, 80).ok).toBe(false);
  });
});

describe('Time-of-Use slots', () => {
  const slot: TouSlot = { slot: 2, enabled: false, soc: 50, current_a: 50, voltage: 49, start: '00:00', end: '00:00' };
  const form = { enabled: true, start: '23:00', end: '07:00', current: '30', soc: '100' };
  it('accepts an overnight slot', () => {
    const r = validateSlot('charge', slot, form, 100, 20);
    expect(r).toEqual({ ok: true, value: { kind: 'charge', slot: 2, enabled: true, start: '23:00', end: '07:00', current_a: 30, soc: 100 } });
    if (r.ok) expect(describeSlot('charge', slot, r.value))
      .toBe('Charge slot 2: off, 00:00–00:00, 50 A, up to 50% → on, 23:00–07:00, 30 A, up to 100%');
  });
  it.each([
    [{ start: '24:00' }, 'HH:MM'], [{ start: '07:00' }, 'differ'], [{ current: '101' }, 'Maximum'],
    [{ soc: '5' }, '10–100'], [{ soc: 'x' }, '10–100'],
  ])('rejects %j', (patch, msg) => {
    const r = validateSlot('charge', slot, { ...form, ...patch }, 100, 20);
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error).toContain(msg);
  });
  it('discharge SOC floor is the over-discharge SOC', () => {
    expect(validateSlot('discharge', slot, { ...form, soc: '15' }, 100, 20).ok).toBe(false);
    expect(validateSlot('discharge', slot, { ...form, soc: '20' }, 100, 20).ok).toBe(true);
  });
  it('nothing changed', () => {
    const same = { enabled: false, start: '00:00', end: '00:01', current: '50', soc: '50' };
    expect(validateSlot('charge', { ...slot, end: '00:01' }, same, 100, 20)).toEqual({ ok: false, error: 'Nothing changed' });
  });
});

describe('clockOffset', () => {
  it('inverter minus server', () => {
    expect(clockOffset('2026-10-18T21:01:37', '2026-10-18T21:03:41')).toBe(-124);
    expect(clockOffset(null, '2026-10-18T21:03:41')).toBeNull();
  });
});
