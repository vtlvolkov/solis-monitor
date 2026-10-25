import { describe, expect, it } from 'vitest';
import type { Fields } from './api';
import { allGauges, batteryRow, gridRow, loadRow, type GaugeEntry, type GaugeRow } from './gaugeRows';
import type { SettingsPayload } from './settings';

const settings = (charge = 40, discharge = 60) => ({
  max_charge_current_a: charge, max_discharge_current_a: discharge, battery: { equalize_voltage: 55, float_voltage: 53.5 },
}) as unknown as SettingsPayload;

const ready = (e: GaugeEntry): GaugeRow => {
  if (e.state !== 'ready') throw new Error(`expected a ready gauge, got ${e.state}`);
  return e.row;
};
const f = (battery_power_signed: number, battery_soc = 99) => ({ battery_power_signed, battery_soc }) as Fields;

describe('grid and load rows', () => {
  it('shows the size of the grid power with a direction note', () => {
    const imp = ready(gridRow(1500));
    expect(imp.watts).toBe(1500);
    expect(imp.note).toBe('Importing');
    expect(imp.okColor).toBe('var(--c-grid)');
    const exp = ready(gridRow(-800));
    expect(exp.watts).toBe(800);
    expect(exp.note).toBe('Exporting');
    expect(exp.okColor).toBe('var(--c-grid-alt)');
    expect(ready(gridRow(5)).note).toBeUndefined();                    // inside the dead band
    expect(ready(gridRow(null)).watts).toBeNull();
  });

  it('uses the 6 kW dial for the backup load', () => {
    const r = ready(loadRow(2400));
    expect(r.watts).toBe(2400);
    expect(r.maxW).toBe(6000);
    expect(r.ariaLabel).toBe('Backup load 2.40 kW');
  });
});

describe('battery rows', () => {
  it('scales charge by the charge limit and equalize voltage, discharge by the discharge limit and float voltage', () => {
    expect(ready(batteryRow('charge', f(900), settings())).maxW).toBeCloseTo(2200);
    expect(ready(batteryRow('discharge', f(-900), settings())).maxW).toBeCloseTo(3210);
  });

  it('shows power in the matching direction, and a note when the battery goes the other way', () => {
    const charging = f(1300);
    expect(ready(batteryRow('charge', charging, settings())).watts).toBe(1300);
    const idleDischarge = ready(batteryRow('discharge', charging, settings()));
    expect(idleDischarge.watts).toBe(0);
    expect(idleDischarge.note).toBe('Charging 1.30 kW');
    const discharging = f(-1200);
    expect(ready(batteryRow('discharge', discharging, settings())).watts).toBe(1200);
    expect(ready(batteryRow('charge', discharging, settings())).note).toBe('Discharging 1.20 kW');
    expect(ready(batteryRow('charge', f(5), settings())).note).toBeUndefined();   // idle: no note
  });

  it('carries the charge level for the dial header', () => {
    expect(ready(batteryRow('charge', f(100, 76), settings())).soc).toBe(76);
  });

  it('waits for the settings, and explains a 0 A limit', () => {
    expect(batteryRow('charge', f(900), null).state).toBe('loading');
    expect(batteryRow('discharge', f(900), undefined).state).toBe('loading');
    const off = batteryRow('discharge', f(-900), settings(40, 0));
    expect(off.state === 'off' && off.text).toBe('Max discharge current is 0 A: the battery does not power loads.');
    expect(batteryRow('charge', f(900), settings(0, 60)).state).toBe('off');
  });
});

describe('allGauges', () => {
  it('lists Grid, Backup load, Battery charge, Battery discharge', () => {
    const rows = allGauges({ ...f(-1200), grid_power: 2460, backup_load_power: 2400 } as Fields, settings());
    expect(rows.map((r) => r.title)).toEqual(['Grid', 'Backup load', 'Battery charge', 'Battery discharge']);
    expect(rows.every((r) => r.state === 'ready')).toBe(true);
    expect(allGauges(null, null).map((r) => r.state)).toEqual(['ready', 'ready', 'loading', 'loading']);
  });
});
