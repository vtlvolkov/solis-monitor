// The Overview cards that only show when added with Customize: Power today, Self-sufficiency, Recent events,
// Battery health and Load by hour. Each polls its own data, and only while it is on the page.
import { TimeChart, type SeriesDef } from '../charts/TimeChart';
import { fullCycles, heatmap, maxOf, minOf, firstValue, recentEvents, roundTrip, selfSufficiency } from '../lib/cards';
import type { Fields, HistoryPayload, SummaryPayload } from '../lib/api';
import { duration } from '../lib/day';
import type { DeviceInfo } from '../lib/devices';
import { kwh, withUnit } from '../lib/format';
import { locale, t, tx, unit } from '../lib/i18n';
import { goTo } from '../lib/nav';
import { usePoll, type EventsPayload } from '../lib/poll';
import { colors } from '../lib/tokens';
import { Card, Skeleton } from './common';

const link = (label: string, view: string) => <button type="button" class="link" onClick={() => goTo(view)}>{t(label)}</button>;
const todayIso = () => new Date().toLocaleDateString('sv');      // YYYY-MM-DD in local time

const POWER_TODAY: SeriesDef[] = [
  { key: 'pv_power', label: tx('PV'), color: colors.pv, unit: 'W', fill: true },
  { key: 'total_load_power', label: tx('Load'), color: colors.load, unit: 'W' },
];

export function PowerTodayCard() {
  const h = usePoll<HistoryPayload>(`/api/day/history?date=${todayIso()}&fields=pv_power,total_load_power&every=10m&agg=mean`, 120_000);
  const d = h.data;
  return (
    <Card title="Power today" class="chart-card power-today" aside={link('Charts', 'charts')}>
      {d ? <TimeChart data={d} series={POWER_TODAY} height={190} inline xRange={d.start != null && d.stop != null ? [d.start, d.stop] : undefined} />
        : h.error ? <div class="chart-error">{t(h.error)}</div> : <Skeleton h={190} />}
    </Card>
  );
}

function Ring({ pct }: { pct: number | null }) {
  const r = 46, c = 2 * Math.PI * r, v = pct ?? 0;
  return (
    <svg viewBox="0 0 112 112" class="ss-ring" role="img" aria-label={pct == null ? '--' : `${pct}%`}>
      <circle cx="56" cy="56" r={r} class="ss-track" />
      <circle cx="56" cy="56" r={r} class="ss-fill" style={{ strokeDasharray: `${(c * v) / 100} ${c}` }} transform="rotate(-90 56 56)" />
      <text x="56" y="63" class="ss-text">{pct == null ? '--' : `${pct}%`}</text>
    </svg>
  );
}

export function SelfSufficiencyCard({ fields }: { fields: Fields | null }) {
  const sum = usePoll<SummaryPayload>('/api/energy/summary', 300_000).data;
  const pv = fields?.pv_energy_today, imp = fields?.grid_import_today;
  const pct = selfSufficiency(pv, imp);
  const month = sum ? selfSufficiency(sum.month.pv_energy_today, sum.month.grid_import_today) : null;
  return (
    <Card title="Self-sufficiency" class="ss-card">
      {!fields ? <Skeleton h={112} /> : (
        <div class="ss-row">
          <Ring pct={pct} />
          <div class="ss-text-col">
            <div class="ss-lead">{pct == null ? t('No energy counted yet today.')
              : t('{pct}% of today’s energy came from your PV', { pct })}</div>
            <div class="ss-split">
              <span><i style={{ background: colors.pv }} />{t('PV')} <b>{kwh(pv)}</b></span>
              <span><i style={{ background: colors.grid }} />{t('Grid')} <b>{kwh(imp)}</b></span>
            </div>
            <div class="setting-hint">{month != null ? t('This month: {pct}%', { pct: month }) : ' '}</div>
          </div>
        </div>
      )}
    </Card>
  );
}

const when = (iso: string) => new Date(iso).toLocaleString(locale(), { weekday: 'short', hour: '2-digit', minute: '2-digit' });

export function RecentEventsCard({ events }: { events: EventsPayload | null }) {
  const items = recentEvents(events, 5);
  return (
    <Card title="Recent events" class="recent-card" aside={link('Events', 'events')}>
      {!events ? <Skeleton h={150} /> : !items.length ? <div class="setting-hint">{t('No outages or alarms in the last 7 days.')}</div> : (
        <ul class="recent">
          {items.map((e) => (
            <li key={`${e.kind}-${e.key}-${e.start}`} class={e.ongoing ? 'live' : ''}>
              <span class={`ev-dot ${e.kind === 'outage' ? 'outage' : e.severity === 'fault' ? 'fault' : 'warning'}`} />
              <span class="recent-name">{e.kind === 'outage' ? (e.ongoing ? t('Outage in progress') : t('Grid outage')) : (e.name ? t(e.name) : e.key)}</span>
              <span class="recent-dur">{e.ongoing ? t('now') : duration(e.duration_s)}</span>
              <time class="recent-when">{when(e.start!)}</time>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

export function BatteryHealthCard({ fields, info }: { fields: Fields | null; info: DeviceInfo | null }) {
  const lo = usePoll<HistoryPayload>('/api/history?fields=battery_soh,battery_temperature&range=30d&every=1d&agg=min', 3_600_000).data;
  const hi = usePoll<HistoryPayload>('/api/history?fields=battery_temperature&range=30d&every=1d&agg=max', 3_600_000).data;
  const soh = fields?.battery_soh;
  const sohThen = firstValue(lo?.series.battery_soh);
  const cap = info?.battery.capacity_kwh ?? null;
  const cycles = fullCycles(fields?.battery_discharge_total, cap);
  const rt = roundTrip(fields?.battery_charge_total, fields?.battery_discharge_total);
  const tMin = minOf(lo?.series.battery_temperature), tMax = maxOf(hi?.series.battery_temperature);
  const sohNote = soh == null || sohThen == null ? '' : Math.round(soh) === Math.round(sohThen)
    ? t('unchanged in 30 days') : t('{v}% 30 days ago', { v: Math.round(sohThen) });
  return (
    <Card title="Battery health" class="health-card">
      {!fields ? <Skeleton h={150} /> : (
        <dl class="kv">
          <div><dt>{t('State of health')}</dt><dd>{soh == null ? '--' : `${Math.round(soh)}%`}{sohNote && <span class="muted"> · {sohNote}</span>}</dd></div>
          <div><dt>{t('Full cycles')}</dt><dd>{cycles == null ? '--' : `≈ ${cycles}`}<span class="muted"> · {kwh(fields.battery_discharge_total, 0)} {t('discharged')}</span></dd></div>
          <div><dt>{t('Round trip, lifetime')}</dt><dd>{rt == null ? '--' : `${rt}%`}<span class="muted"> · {t('out ÷ in')}</span></dd></div>
          <div><dt>{t('Temperature, 30 days')}</dt><dd>{tMin == null || tMax == null ? '--' : `${tMin.toFixed(0)}–${withUnit(tMax, '°C', 0)}`}</dd></div>
          <div><dt>{t('Usable capacity')}</dt><dd>{cap == null ? '--' : kwh(cap, 2)}</dd></div>
        </dl>
      )}
    </Card>
  );
}

const CW = 22, CH = 20, LEFT = 38;

export function LoadHeatmapCard() {
  const h = usePoll<HistoryPayload>('/api/history?fields=total_load_power&range=7d&every=1h&agg=mean', 900_000);
  const heat = h.data ? heatmap(h.data.t, h.data.series.total_load_power ?? []) : null;
  const W = LEFT + 24 * CW, H = (heat?.rows.length ?? 7) * CH + 20;
  return (
    <Card title="Load by hour" class="heat-card" aside={<span class="muted">{t('7 days')}</span>}>
      {!heat ? (h.error ? <div class="chart-error">{t(h.error)}</div> : <Skeleton h={160} />) : !heat.rows.length ? <div class="setting-hint">{t('No readings yet.')}</div> : (
        <>
          <svg viewBox={`0 0 ${W} ${H}`} class="heat" role="img" aria-label={t('Load by hour over the last 7 days')}>
            {heat.rows.map((r, y) => (
              <g key={r.date}>
                <text x={LEFT - 8} y={y * CH + 14} class="heat-axis" text-anchor="end">{r.day.toLocaleDateString(locale(), { weekday: 'short' })}</text>
                {r.cells.map((v, x) => (
                  <rect key={x} x={LEFT + x * CW} y={y * CH} width={CW - 3} height={CH - 3} rx={3}
                        class={v == null ? 'heat-none' : 'heat-cell'} style={v == null ? undefined : { fillOpacity: 0.1 + 0.9 * (heat.max ? v / heat.max : 0) }}>
                    <title>{`${r.day.toLocaleDateString(locale(), { weekday: 'short', day: 'numeric', month: 'short' })} ${String(x).padStart(2, '0')}:00 · ${v == null ? '--' : kwh(v, 2)}`}</title>
                  </rect>
                ))}
              </g>
            ))}
            {[0, 6, 12, 18].map((hr) => <text key={hr} x={LEFT + hr * CW} y={H - 4} class="heat-axis">{`${String(hr).padStart(2, '0')}:00`}</text>)}
          </svg>
          <div class="setting-hint">
            {heat.busiest ? t('Busiest hour: {from}–{to}, ≈ {v} on average · darker = more', {
              from: `${String(heat.busiest.hour).padStart(2, '0')}:00`, to: `${String((heat.busiest.hour + 1) % 24).padStart(2, '0')}:00`,
              v: `${heat.busiest.kwh.toFixed(2)} ${unit('kWh')}` }) : ''}
          </div>
        </>
      )}
    </Card>
  );
}
