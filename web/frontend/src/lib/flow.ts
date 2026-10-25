// Power-flow direction logic for the Overview diagram (pure, unit-tested).

export const DEAD_BAND_W = 20;

export type NodeId = 'pv' | 'grid' | 'gridLoad' | 'battery' | 'backup' | 'inverter';
export type LineId = 'pv' | 'grid' | 'gridLoad' | 'battery' | 'backup';

/** Each line's SVG path is drawn from `a` to `b`; `reversed` means energy flows b -> a. */
export const LINES: Record<LineId, { a: NodeId; b: NodeId }> = {
  pv: { a: 'pv', b: 'inverter' },
  grid: { a: 'grid', b: 'inverter' },
  gridLoad: { a: 'grid', b: 'gridLoad' },
  battery: { a: 'inverter', b: 'battery' },
  backup: { a: 'inverter', b: 'backup' },
};

export interface FlowInput {
  pv_power?: number | null;
  grid_power?: number | null;            // + importing, - exporting
  house_load_power?: number | null;
  battery_power_signed?: number | null;  // + charging, - discharging
  backup_load_power?: number | null;
}

export interface Flow {
  line: LineId;
  active: boolean;
  /** Energy source and destination; null when inactive. */
  from: NodeId | null;
  to: NodeId | null;
  /** true when energy moves against the path's drawing direction (b -> a). */
  reversed: boolean;
  /** Absolute power on the line in W (0 when unknown). */
  watts: number;
}

const finite = (v: number | null | undefined): v is number => typeof v === 'number' && Number.isFinite(v);

export const isActive = (v: number | null | undefined) => finite(v) && Math.abs(v) >= DEAD_BAND_W;

/**
 * Direction of a line relative to its drawing direction: 1 = a -> b, -1 = b -> a, 0 = idle.
 * Unidirectional lines (PV, loads) ignore negative readings: those are sensor noise or
 * offsets, not energy flowing back out of a load.
 */
function direction(v: number | null | undefined, bidirectional: boolean): -1 | 0 | 1 {
  if (!isActive(v)) return 0;
  if ((v as number) > 0) return 1;
  return bidirectional ? -1 : 0;
}

function flow(line: LineId, v: number | null | undefined, bidirectional: boolean): Flow {
  const dir = direction(v, bidirectional);
  const { a, b } = LINES[line];
  return {
    line,
    active: dir !== 0,
    from: dir === 0 ? null : dir === 1 ? a : b,
    to: dir === 0 ? null : dir === 1 ? b : a,
    reversed: dir === -1,
    watts: finite(v) ? Math.abs(v) : 0,
  };
}

export function computeFlows(f: FlowInput): Record<LineId, Flow> {
  return {
    pv: flow('pv', f.pv_power, false),                     // PV -> inverter
    grid: flow('grid', f.grid_power, true),                // import: grid -> inverter, export: inverter -> grid
    gridLoad: flow('gridLoad', f.house_load_power, false), // grid -> grid-side load
    battery: flow('battery', f.battery_power_signed, true),// charge: inverter -> battery, discharge: battery -> inverter
    backup: flow('backup', f.backup_load_power, false),    // inverter -> backup/EPS load
  };
}

/** Animation: dots travel faster with more power. Returns seconds per dash period. */
export const DASH_PERIOD = 40;
export function flowDuration(watts: number): number {
  const speed = Math.min(320, Math.max(14, Math.abs(watts) * 0.06)); // viewBox units per second
  return DASH_PERIOD / speed;
}
