import type { ComponentChildren } from 'preact';
import { t, unit } from '../lib/i18n';
import { computeFlows, DASH_PERIOD, flowDuration, isActive, type LineId } from '../lib/flow';
import { kw } from '../lib/format';
import { useTweened } from '../lib/motion';
import type { Fields } from '../lib/api';
import { Icon } from './icons';

// Layout in viewBox units (cropped to x 60..860 and y 16..750, just above the top circles), traced from the Solis app screenshot.
const R = 88;
const NODES = {
  pv: { x: 160, y: 110 },
  grid: { x: 760, y: 110 },
  inverter: { x: 460, y: 350 },
  gridLoad: { x: 760, y: 350 },
  battery: { x: 160, y: 588 },
  backup: { x: 760, y: 588 },
};

// Paths are drawn in the canonical direction from flow.ts LINES (a -> b).
const PATHS: Record<LineId, string> = {
  pv: 'M248,110 H400 A20,20 0 0 1 420,130 V271.6',
  grid: 'M672,110 H520 A20,20 0 0 0 500,130 V271.6',
  gridLoad: 'M500,236 H680 A20,20 0 0 1 700,256 V285.6',
  battery: 'M420,428.4 V568 A20,20 0 0 1 400,588 H248',
  backup: 'M500,428.4 V568 A20,20 0 0 0 520,588 H672',
};

// Arrowhead position just past each bend, and its heading (deg) for the a -> b direction.
const ARROWS: Record<LineId, { x: number; y: number; angle: number }> = {
  pv: { x: 420, y: 168, angle: 90 },
  grid: { x: 500, y: 168, angle: 90 },
  gridLoad: { x: 610, y: 236, angle: 0 },
  battery: { x: 372, y: 588, angle: 180 },
  backup: { x: 548, y: 588, angle: 0 },
};

// Line colour = colour of the line's outer node.
const LINE_COLOR: Record<LineId, string> = {
  pv: 'var(--c-pv)',
  grid: 'var(--c-grid)',
  gridLoad: 'var(--c-grid)',
  battery: 'var(--c-battery)',
  backup: 'var(--c-load)',
};

function FlowLine({ id, active, reversed, watts }: { id: LineId; active: boolean; reversed: boolean; watts: number }) {
  const color = active ? LINE_COLOR[id] : 'var(--c-inactive)';
  const a = ARROWS[id];
  const dur = flowDuration(watts);
  return (
    <g class={`flow-line ${active ? 'is-active' : 'is-idle'}`} data-line={id}
       data-direction={active ? (reversed ? 'reverse' : 'forward') : 'none'}>
      <path d={PATHS[id]} class="line-base" stroke={color} />
      {active && (
        <>
          <path d={PATHS[id]} class="line-dots"
                style={{ animationDuration: `${dur.toFixed(2)}s`, animationDirection: reversed ? 'reverse' : 'normal',
                         strokeDasharray: `0.1 ${DASH_PERIOD - 0.1}` }} />
          <path class="line-arrow" fill={color} d="M-13,-11 L7,0 L-13,11 L-8,0 Z"
                transform={`translate(${a.x} ${a.y}) rotate(${a.angle + (reversed ? 180 : 0)})`} />
        </>
      )}
    </g>
  );
}

function Node(props: {
  id: keyof typeof NODES; label?: string; watts: number | null; active: boolean; color: string;
  icon: ComponentChildren; testValue?: number | null;
}) {
  const { x, y } = NODES[props.id];
  const shown = useTweened(props.watts);
  const ring = props.active ? props.color : 'var(--c-inactive)';
  return (
    <g class="node" data-node={props.id} data-target-kw={kw(props.watts)}>
      <circle cx={x} cy={y} r={R} class="node-bg" stroke={ring} />
      <g transform={`translate(${x} ${y - 40})`} style={{ color: ring }}>{props.icon}</g>
      <text x={x} y={y + 22} class="node-value" data-testid={`value-${props.id}`}>{kw(shown)}</text>
      <text x={x} y={y + 56} class="node-unit">{unit('kW')}</text>
      {props.label && (
        <g class="chip">
          <rect x={x - t(props.label).length * 7.4 - 14} y={y + R + 16} width={t(props.label).length * 14.8 + 28} height={40} rx={8} />
          <text x={x} y={y + R + 44}>{t(props.label)}</text>
        </g>
      )}
    </g>
  );
}

export function PowerFlow({ fields }: { fields: Fields | null }) {
  const f = fields ?? {};
  const flows = computeFlows(f);
  const abs = (v: number | null | undefined) => (v == null ? null : Math.abs(v));
  const soc = f.battery_soc;
  const charging = flows.battery.active && !flows.battery.reversed;
  const { x: ix, y: iy } = NODES.inverter;

  return (
    <svg class="power-flow" viewBox="60 16 800 734" role="img"
         aria-label={t('Power flow: grid {grid} {u}, battery {battery} {u}, backup load {backup} {u}', { grid: kw(f.grid_power), battery: kw(f.battery_power_signed), backup: kw(f.backup_load_power), u: unit('kW') })}>
      {(Object.keys(PATHS) as LineId[]).map((id) => <FlowLine key={id} id={id} {...flows[id]} />)}

      <g class="node inverter" data-node="inverter">
        <circle cx={ix} cy={iy} r={R} class="node-bg" stroke="var(--c-inverter)" />
        <g transform={`translate(${ix} ${iy})`}><Icon.inverter /></g>
      </g>

      <Node id="pv" label="PV" watts={f.pv_power ?? null} active={flows.pv.active} color="var(--c-pv)" icon={<Icon.pv />} />
      <Node id="grid" label="Grid" watts={abs(f.grid_power)} active={flows.grid.active} color="var(--c-grid)" icon={<Icon.grid />} />
      <Node id="gridLoad" label="Grid Load" watts={f.house_load_power ?? null} active={isActive(f.house_load_power)}
            color="var(--c-load)" icon={<Icon.house />} />
      <Node id="battery" label="Battery" watts={abs(f.battery_power_signed)} active={flows.battery.active}
            color="var(--c-battery)" icon={<Icon.battery soc={soc} charging={charging} />} />
      <Node id="backup" label="Backup Load" watts={f.backup_load_power ?? null} active={flows.backup.active}
            color="var(--c-load)" icon={<Icon.backup />} />
    </svg>
  );
}
