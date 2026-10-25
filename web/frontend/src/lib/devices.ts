// Devices tab: device list and status logic (pure, unit-tested).

export type DeviceId = 'inverter' | 'battery' | 'logger' | 'meter';
export type DeviceStatus = 'online' | 'offline' | 'fault';

export interface DeviceInfo {
  inverter: {
    serial: string | null; model_code: string; dsp_version: string; hmi_version: string;
    clock: string | null; status_code: number; status: string; fault_codes: string[]; has_fault: boolean;
    alarms: { key: string; name: string; severity: 'fault' | 'warning' }[]; no_grid: boolean;
    dc_bus_voltage: number; power_limit_pct: number; grid_standard_code: number;
  };
  backup: { voltage: number; current: number; eps_dod_pct: number; offgrid_dod_pct: number; switch_time_ms: number };
  battery: {
    bms_voltage: number; bms_current: number; bms_charge_limit_a: number; bms_discharge_limit_a: number;
    bms_failure_codes: string[]; has_fault: boolean; connected: boolean;
    model: string; rated_capacity_ah: number | null; capacity_kwh: number | null; capacity_source: string;
    max_charge_soc: number | null; overdischarge_soc: number | null; force_charge_soc: number | null; backup_soc: number | null;
  };
  meter: {
    voltage: number; current: number; active_power: number; reactive_power: number; apparent_power: number;
    power_factor: number; frequency: number; import_total_kwh: number; export_total_kwh: number; connected: boolean;
    location: string; type: string;
  };
  logger: { host: string; port: number; unit: number };
  read_at: string;
}

export const DEVICES: { id: DeviceId; name: string }[] = [
  { id: 'inverter', name: 'Inverter' },
  { id: 'battery', name: 'Battery' },
  { id: 'logger', name: 'Logger' },
  { id: 'meter', name: 'Meter' },
];

/**
 * The logger is the Modbus TCP link, so fresh live data means it is up. Inverter,
 * BMS and meter are reached through it, so they are offline whenever it is.
 * Without device details (poller unreachable) live data decides alone.
 */
export function deviceStatus(id: DeviceId, liveOnline: boolean, info: DeviceInfo | null): DeviceStatus {
  if (!liveOnline) return 'offline';
  if (!info) return 'online';
  switch (id) {
    case 'inverter': return info.inverter.has_fault ? 'fault' : 'online';   // a grid outage is not an inverter fault
    case 'battery': return !info.battery.connected ? 'offline' : info.battery.has_fault ? 'fault' : 'online';
    case 'meter': return info.meter.connected ? 'online' : 'offline';
    case 'logger': return 'online';
  }
}

/** Inverter clock drift in seconds (inverter minus browser), or null. The clock has no zone: it is local time. */
export function clockDrift(clock: string | null, now: Date): number | null {
  if (!clock) return null;
  const t = new Date(clock);              // parsed as local time (no offset in the string)
  return Number.isNaN(t.getTime()) ? null : Math.round((t.getTime() - now.getTime()) / 1000);
}

export const parseView = (search: string): DeviceId | null => {
  const d = new URLSearchParams(search).get('device');
  return DEVICES.some((x) => x.id === d) ? (d as DeviceId) : null;
};
