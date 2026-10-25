import { describe, expect, it } from 'vitest';
import { clockDrift, deviceStatus, parseView, type DeviceInfo } from './devices';

const info = (patch: { inv?: object; bat?: object; met?: object } = {}): DeviceInfo => ({
  inverter: { serial: '1234567890123456', model_code: '0x3173', dsp_version: '0x0013', hmi_version: '0x0026',
              clock: '2026-10-18T20:37:10', status_code: 3, status: 'Generating', fault_codes: [], has_fault: false,
              alarms: [], no_grid: false, dc_bus_voltage: 480.6, power_limit_pct: 100, grid_standard_code: 110, ...patch.inv },
  backup: { voltage: 214.8, current: 3.7, eps_dod_pct: 20, offgrid_dod_pct: 30, switch_time_ms: 2000 },
  battery: { bms_voltage: 53.1, bms_current: 1, bms_charge_limit_a: 20, bms_discharge_limit_a: 100,
             bms_failure_codes: [], has_fault: false, connected: true, model: 'Code 0x0004', rated_capacity_ah: 126,
             capacity_kwh: 6.45, capacity_source: 'inverter', max_charge_soc: 100, overdischarge_soc: 20,
             force_charge_soc: 10, backup_soc: 80, ...patch.bat },
  meter: { voltage: 210.7, current: 3.94, active_power: -679, reactive_power: 412, apparent_power: 786,
           power_factor: -0.85, frequency: 50, import_total_kwh: 9.56, export_total_kwh: 0, connected: true,
           location: 'Grid', type: 'Eastron 1-phase', ...patch.met },
  logger: { host: '192.168.1.50', port: 502, unit: 1 },
  read_at: '2026-10-18T17:37:10Z',
});

describe('deviceStatus', () => {
  it('everything offline when live data is stale', () => {
    for (const id of ['inverter', 'battery', 'logger', 'meter'] as const) {
      expect(deviceStatus(id, false, info())).toBe('offline');
      expect(deviceStatus(id, false, null)).toBe('offline');
    }
  });
  it('online from live data alone when details are unavailable', () => {
    for (const id of ['inverter', 'battery', 'logger', 'meter'] as const) expect(deviceStatus(id, true, null)).toBe('online');
  });
  it('healthy system', () => {
    for (const id of ['inverter', 'battery', 'logger', 'meter'] as const) expect(deviceStatus(id, true, info())).toBe('online');
  });
  it('faults', () => {
    expect(deviceStatus('inverter', true, info({ inv: { has_fault: true } }))).toBe('fault');
    expect(deviceStatus('battery', true, info({ bat: { has_fault: true } }))).toBe('fault');
    expect(deviceStatus('logger', true, info({ inv: { has_fault: true } }))).toBe('online');
  });
  it('disconnected BMS / meter', () => {
    expect(deviceStatus('battery', true, info({ bat: { connected: false, has_fault: true } }))).toBe('offline');
    expect(deviceStatus('meter', true, info({ met: { connected: false } }))).toBe('offline');
  });
});

describe('clockDrift', () => {
  it('compares local inverter time with the browser clock', () => {
    const now = new Date(2026, 9, 18, 20, 37, 0);
    expect(clockDrift('2026-10-18T20:37:10', now)).toBe(10);
    expect(clockDrift('2026-10-18T20:30:00', now)).toBe(-420);
    expect(clockDrift(null, now)).toBeNull();
    expect(clockDrift('garbage', now)).toBeNull();
  });
});

describe('parseView', () => {
  it('accepts known devices only', () => {
    expect(parseView('?view=devices&device=battery')).toBe('battery');
    expect(parseView('?view=devices&device=x')).toBeNull();
    expect(parseView('?view=devices')).toBeNull();
  });
});
