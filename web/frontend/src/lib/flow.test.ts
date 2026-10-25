import { describe, expect, it } from 'vitest';
import { computeFlows, DEAD_BAND_W, flowDuration, isActive, LINES, type FlowInput, type LineId } from './flow';
import { ago, kw, power, socSegments } from './format';

const INPUT_OF: Record<LineId, keyof FlowInput> = {
  pv: 'pv_power',
  grid: 'grid_power',
  gridLoad: 'house_load_power',
  battery: 'battery_power_signed',
  backup: 'backup_load_power',
};

describe('dead-band', () => {
  it.each([
    [0, false], [19.9, false], [-19.9, false], [20, true], [-20, true], [20.1, true],
    [5000, true], [-5000, true], [null, false], [undefined, false], [NaN, false], [Infinity, false],
  ])('isActive(%s) = %s', (v, expected) => {
    expect(isActive(v as number)).toBe(expected);
  });

  it('threshold is 20 W', () => expect(DEAD_BAND_W).toBe(20));
});

describe('grid line (bidirectional)', () => {
  it('importing: grid -> inverter', () => {
    const f = computeFlows({ grid_power: 1350 }).grid;
    expect(f).toMatchObject({ active: true, from: 'grid', to: 'inverter', reversed: false, watts: 1350 });
  });
  it('exporting: inverter -> grid', () => {
    const f = computeFlows({ grid_power: -800 }).grid;
    expect(f).toMatchObject({ active: true, from: 'inverter', to: 'grid', reversed: true, watts: 800 });
  });
  it.each([0, 19, -19, null, NaN])('idle at %s', (v) => {
    expect(computeFlows({ grid_power: v as number }).grid).toMatchObject({ active: false, from: null, to: null, reversed: false });
  });
});

describe('battery line (bidirectional)', () => {
  it('charging: inverter -> battery', () => {
    expect(computeFlows({ battery_power_signed: 520 }).battery)
      .toMatchObject({ active: true, from: 'inverter', to: 'battery', reversed: false, watts: 520 });
  });
  it('discharging: battery -> inverter', () => {
    expect(computeFlows({ battery_power_signed: -1086 }).battery)
      .toMatchObject({ active: true, from: 'battery', to: 'inverter', reversed: true, watts: 1086 });
  });
  it.each([0, 19.99, -19.99, null, undefined])('idle at %s', (v) => {
    expect(computeFlows({ battery_power_signed: v as number }).battery.active).toBe(false);
  });
});

describe('unidirectional lines ignore negative readings', () => {
  it.each([
    ['pv', 'pv_power', 'pv', 'inverter'],
    ['gridLoad', 'house_load_power', 'grid', 'gridLoad'],
    ['backup', 'backup_load_power', 'inverter', 'backup'],
  ] as const)('%s', (line, key, from, to) => {
    expect(computeFlows({ [key]: 740 })[line]).toMatchObject({ active: true, from, to, reversed: false, watts: 740 });
    expect(computeFlows({ [key]: 20 })[line].active).toBe(true);
    expect(computeFlows({ [key]: 19 })[line].active).toBe(false);
    expect(computeFlows({ [key]: 0 })[line].active).toBe(false);
    expect(computeFlows({ [key]: -500 })[line]).toMatchObject({ active: false, from: null, to: null, reversed: false });
  });
});

describe('every sign combination of all five inputs', () => {
  const values = { pos: 1500, small: 10, zero: 0, smallNeg: -10, neg: -1500, missing: null } as const;
  const keys = Object.keys(values) as (keyof typeof values)[];
  const lines = Object.keys(INPUT_OF) as LineId[];
  const bidirectional = new Set<LineId>(['grid', 'battery']);

  // 6^5 = 7776 combinations; lines must be independent of each other.
  const combos: Record<LineId, keyof typeof values>[] = [];
  const walk = (i: number, acc: Partial<Record<LineId, keyof typeof values>>) => {
    if (i === lines.length) return void combos.push(acc as Record<LineId, keyof typeof values>);
    for (const k of keys) walk(i + 1, { ...acc, [lines[i]]: k });
  };
  walk(0, {});

  it(`covers ${combos.length} combinations`, () => {
    expect(combos.length).toBe(7776);
    for (const combo of combos) {
      const input: FlowInput = {};
      for (const line of lines) (input as Record<string, number | null>)[INPUT_OF[line]] = values[combo[line]];
      const flows = computeFlows(input);
      for (const line of lines) {
        const f = flows[line];
        const kind = combo[line];
        const { a, b } = LINES[line];
        if (kind === 'pos') {
          expect(f, `${line} ${kind}`).toMatchObject({ active: true, from: a, to: b, reversed: false });
        } else if (kind === 'neg' && bidirectional.has(line)) {
          expect(f, `${line} ${kind}`).toMatchObject({ active: true, from: b, to: a, reversed: true });
        } else {
          expect(f, `${line} ${kind}`).toMatchObject({ active: false, from: null, to: null, reversed: false });
        }
      }
    }
  });
});

describe('typical scenes', () => {
  it('reference screenshot: grid import feeds backup load and charges battery', () => {
    const f = computeFlows({ pv_power: 0, grid_power: 1350, house_load_power: 0, battery_power_signed: 520, backup_load_power: 790 });
    expect(f.pv.active).toBe(false);
    expect(f.gridLoad.active).toBe(false);
    expect([f.grid.from, f.grid.to]).toEqual(['grid', 'inverter']);
    expect([f.battery.from, f.battery.to]).toEqual(['inverter', 'battery']);
    expect([f.backup.from, f.backup.to]).toEqual(['inverter', 'backup']);
  });
  it('grid outage: battery discharges into backup load', () => {
    const f = computeFlows({ grid_power: 0, battery_power_signed: -900, backup_load_power: 880 });
    expect(f.grid.active).toBe(false);
    expect([f.battery.from, f.battery.to]).toEqual(['battery', 'inverter']);
  });
  it('PV surplus: export and charge', () => {
    const f = computeFlows({ pv_power: 4000, grid_power: -1500, battery_power_signed: 2000, backup_load_power: 500 });
    expect([f.pv.from, f.pv.to]).toEqual(['pv', 'inverter']);
    expect([f.grid.from, f.grid.to]).toEqual(['inverter', 'grid']);
    expect([f.battery.from, f.battery.to]).toEqual(['inverter', 'battery']);
  });
});

describe('animation speed', () => {
  it('is faster (shorter period) for more power and bounded', () => {
    expect(flowDuration(2000)).toBeLessThan(flowDuration(200));
    expect(flowDuration(-2000)).toBe(flowDuration(2000));
    expect(flowDuration(1e9)).toBeGreaterThan(0);
    expect(Number.isFinite(flowDuration(0))).toBe(true);
  });
});

describe('format', () => {
  it('kw always 2 decimals', () => {
    expect(kw(1350)).toBe('1.35');
    expect(kw(0)).toBe('0.00');
    expect(kw(null)).toBe('--');
  });
  it('power switches to kW at 1 kW', () => {
    expect(power(999)).toBe('999 W');
    expect(power(-999.4)).toBe('-999 W');
    expect(power(1000)).toBe('1.00 kW');
    expect(power(-1500)).toBe('-1.50 kW');
    expect(power(250, true)).toBe('+250 W');
  });
  it('SOC segments', () => {
    expect(socSegments(99)).toBe(9);
    expect(socSegments(100)).toBe(10);
    expect(socSegments(5)).toBe(1);
    expect(socSegments(0)).toBe(0);
    expect(socSegments(null)).toBe(0);
  });
  it('ago', () => {
    expect(ago(12)).toBe('12 s');
    expect(ago(185)).toBe('3 min');
    expect(ago(7300)).toBe('2 h');
  });
});
