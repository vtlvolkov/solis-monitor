import { useEffect, useRef, useState } from 'preact/hooks';
import { kwh, power, withUnit } from '../lib/format';
import { t, tx, unit } from '../lib/i18n';
import { socColor, TODAY } from '../components/common';
import type { DailyPayload, Fields, SummaryPayload } from '../lib/api';

/** Semicircle SOC gauge: red < 20, orange < 50, green otherwise (as in Grafana). */
export function SocGauge({ soc }: { soc: number | null | undefined }) {
  const v = soc == null ? 0 : Math.min(100, Math.max(0, soc));
  const r = 50, c = Math.PI * r;
  return (
    <div class="stat gauge" data-testid="stat-soc">
      <span class="stat-title">{t('Battery SOC')}</span>
      <svg viewBox="0 0 120 70" class="gauge-svg" role="meter" aria-valuenow={soc ?? undefined} aria-valuemin={0} aria-valuemax={100}>
        <path d="M10,62 A50,50 0 0 1 110,62" class="gauge-track" />
        <path d="M10,62 A50,50 0 0 1 110,62" class="gauge-fill" stroke={socColor(soc)}
              style={{ strokeDasharray: `${(v / 100) * c} ${c}` }} />
        <text x="60" y="58" class="gauge-text">{soc == null ? '--' : `${Math.round(soc)}%`}</text>
      </svg>
    </div>
  );
}

export function Stat({ title, value, color, testId }: { title: string; value: string; color?: string; testId: string }) {
  return (
    <div class="stat" data-testid={testId}>
      <span class="stat-title">{t(title)}</span>
      <span class="stat-value" style={color ? { color } : undefined}>{value}</span>
    </div>
  );
}

/** Grafana "signed" thresholds: blue <= -20, neutral in between, orange >= 20. */
export function signedColor(v: number | null | undefined) {
  if (v == null) return undefined;
  if (v <= -20) return 'var(--c-grid)';
  if (v >= 20) return 'var(--c-warn)';
  return undefined;
}

export function tempColor(v: number | null | undefined) {
  if (v == null) return undefined;
  if (v >= 70) return 'var(--c-danger)';
  if (v >= 55) return 'var(--c-warn)';
  return 'var(--c-battery)';
}

export function StatRow({ f }: { f: Fields }) {
  return (
    <div class="stat-row">
      <SocGauge soc={f.battery_soc} />
      <Stat testId="stat-battery" title={tx('Battery (+chg / −dis)')} value={power(f.battery_power_signed, true)} color={signedColor(f.battery_power_signed)} />
      <Stat testId="stat-grid" title={tx('Grid (+imp / −exp)')} value={power(f.grid_power, true)} color={signedColor(f.grid_power)} />
      <Stat testId="stat-load" title={tx('Load')} value={power(f.total_load_power)} />
      <Stat testId="stat-pv" title={tx('PV')} value={power(f.pv_power)} color={(f.pv_power ?? 0) >= 50 ? 'var(--c-pv)' : undefined} />
      <Stat testId="stat-temp" title={tx('Inverter temp')} value={withUnit(f.inverter_temperature, '°C', 1)} color={tempColor(f.inverter_temperature)} />
    </div>
  );
}

/** "Energy today" horizontal bar gauge. */
export function EnergyBars({ f }: { f: Fields }) {
  const max = Math.max(1, ...TODAY.map((row) => f[row.key] ?? 0));
  return (
    <div class="ebars">
      {TODAY.map((row) => {
        const v = f[row.key];
        return (
          <div key={row.key} class="ebar" data-testid={`ebar-${row.key}`}>
            <span class="ebar-label">{t(row.label)}</span>
            <span class="ebar-track"><span class="ebar-fill" style={{ width: `${((v ?? 0) / max) * 100}%`, background: row.color }} /></span>
            <span class="ebar-value">{kwh(v)}</span>
          </div>
        );
      })}
    </div>
  );
}

// Energy rows: today counter, yesterday register, lifetime register (inverter), and the month/year
// key in /api/energy/summary (our daily maxima). PV month/year come straight from the inverter.
const SUMMARY_ROWS = [
  { label: tx('Grid import'), color: 'var(--c-grid)', today: 'grid_import_today', yesterday: 'grid_import_yesterday', total: 'grid_import_total', period: 'grid_import_today' },
  { label: tx('Grid export'), color: 'var(--c-grid-alt)', today: 'grid_export_today', yesterday: 'grid_export_yesterday', total: 'grid_export_total', period: 'grid_export_today' },
  { label: tx('Battery charged'), color: 'var(--c-battery)', today: 'battery_charge_today', yesterday: 'battery_charge_yesterday', total: 'battery_charge_total', period: 'battery_charge_today' },
  { label: tx('Battery discharged'), color: 'var(--c-battery-alt)', today: 'battery_discharge_today', yesterday: 'battery_discharge_yesterday', total: 'battery_discharge_total', period: 'battery_discharge_today' },
  { label: tx('Load'), color: 'var(--c-load)', today: 'load_consumption_today', yesterday: 'load_yesterday', total: 'load_total', period: 'load_consumption_today' },
  { label: tx('PV'), color: 'var(--c-pv)', today: 'pv_energy_today', yesterday: 'pv_energy_yesterday', total: 'pv_energy_total', period: null },
] as const;

const num = (v: number | null | undefined, d = 1) => (v == null ? '--' : v.toFixed(d));

export function EnergySummary({ f, summary }: { f: Fields; summary: SummaryPayload | null }) {
  const partial = summary?.first_day && summary.first_day > `${new Date().getFullYear()}-01-01`;
  return (
    <div class="esum">
      <div class="table-scroll">
        <table class="esum-table">
          <thead><tr><th /><th>{t('Today')}</th><th>{t('Yesterday')}</th><th>{t('Month')}</th><th>{t('Year')}</th><th>{t('Lifetime')}</th></tr></thead>
          <tbody>
            {SUMMARY_ROWS.map((r) => (
              <tr key={r.label}>
                <td><span class="swatch" style={{ background: r.color }} />{t(r.label)}</td>
                <td>{num(f[r.today])}</td>
                <td>{num(f[r.yesterday])}</td>
                <td>{r.period ? num(summary?.month[r.period]) : num(f.pv_energy_month, 0)}</td>
                <td>{r.period ? num(summary?.year[r.period]) : num(f.pv_energy_year, 0)}</td>
                <td>{num(f[r.total], 0)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div class="setting-hint">
        {t('kWh. Today, yesterday and lifetime come from the inverter\'s own counters (lifetime in whole kWh).')}{' '}
        {t('Month and year are summed from this dashboard\'s daily records')}{partial ? t(', which start on {day}', { day: summary!.first_day ?? '' }) : ''}.
      </div>
    </div>
  );
}

const DAILY_SERIES = [
  { key: 'load_consumption_today', label: tx('Load'), color: 'var(--c-load)' },
  { key: 'grid_import_today', label: tx('Import'), color: 'var(--c-grid)' },
  { key: 'grid_export_today', label: tx('Export'), color: 'var(--c-grid-alt)' },
  { key: 'battery_charge_today', label: tx('Charged'), color: 'var(--c-battery)' },
  { key: 'battery_discharge_today', label: tx('Discharged'), color: 'var(--c-battery-alt)' },
  { key: 'pv_energy_today', label: tx('PV'), color: 'var(--c-pv)' },
] as const;

/** Grouped daily kWh bars, hand-built SVG. Series can be toggled from the legend. */
export function DailyChart({ data }: { data: DailyPayload }) {
  const [on, setOn] = useState<Record<string, boolean>>({
    load_consumption_today: true, grid_import_today: true, grid_export_today: false,
    battery_charge_today: false, battery_discharge_today: false, pv_energy_today: false,
  });
  const [hover, setHover] = useState<number | null>(null);
  const scroller = useRef<HTMLDivElement>(null);
  useEffect(() => {            // narrow screens: start at the most recent days
    const el = scroller.current;
    if (el) el.scrollLeft = el.scrollWidth;
  }, [data]);
  const shown = DAILY_SERIES.filter((s) => on[s.key]);
  const days = data.days;
  const max = Math.max(1, ...days.flatMap((d) => shown.map((s) => d[s.key] ?? 0)));
  const step = [0.5, 1, 2, 5, 10, 20, 50, 100].find((st) => st * 4 >= max) ?? 200;
  const nice = step * 4;
  const W = 900, H = 240, L = 66, B = 26, T = 8;
  const pw = W - L - 8, ph = H - B - T;
  const slot = pw / Math.max(1, days.length);
  const bw = Math.max(1.5, (slot * 0.8) / Math.max(1, shown.length));
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((k) => k * nice);
  const label = (iso: string) => iso.slice(8, 10) + '.' + iso.slice(5, 7);
  const every = Math.ceil(days.length / 10);
  const hd = hover != null ? days[hover] : null;

  return (
    <div class="daily">
      <div class="daily-legend">
        {DAILY_SERIES.map((s) => (
          <button type="button" key={s.key} class={on[s.key] ? 'on' : ''} aria-pressed={on[s.key]}
                  onClick={() => setOn({ ...on, [s.key]: !on[s.key] })}>
            <span class="swatch" style={{ background: s.color }} />{t(s.label)}
          </button>
        ))}
      </div>
      <div class="daily-scroll" ref={scroller}>
        <svg viewBox={`0 0 ${W} ${H}`} class="daily-svg" onMouseLeave={() => setHover(null)} role="img"
             aria-label={t('Daily energy for the last {n} days', { n: days.length })}>
          {ticks.map((tick) => {
            const y = T + ph - (tick / nice) * ph;
            return (
              <g key={tick}>
                <line x1={L} x2={W - 8} y1={y} y2={y} class="grid-line" />
                <text x={L - 6} y={y + 4} class="axis-text" text-anchor="end">{tick} {unit('kWh')}</text>
              </g>
            );
          })}
          {days.map((d, i) => (
            <g key={d.date} onMouseEnter={() => setHover(i)}>
              <rect x={L + i * slot} y={T} width={slot} height={ph} fill={hover === i ? 'rgba(255,255,255,0.05)' : 'transparent'} />
              {shown.map((s, j) => {
                const v = d[s.key] ?? 0;
                const h = (v / nice) * ph;
                return <rect key={s.key} x={L + i * slot + slot * 0.1 + j * bw} y={T + ph - h} width={bw - 0.5} height={h}
                             fill={s.color} rx={1} />;
              })}
              {i % every === 0 && <text x={L + i * slot + slot / 2} y={H - 8} class="axis-text" text-anchor="middle">{label(d.date)}</text>}
            </g>
          ))}
        </svg>
      </div>
      <div class="daily-readout">
        {hd ? (
          <>
            <strong>{hd.date}</strong>
            {shown.map((s) => <span key={s.key}><span class="swatch" style={{ background: s.color }} />{t(s.label)} {kwh(hd[s.key])}</span>)}
          </>
        ) : <span class="muted">{t('Hover a day for values · {tz} days', { tz: data.tz })}</span>}
      </div>
    </div>
  );
}
