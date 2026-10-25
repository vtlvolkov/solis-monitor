import { useCallback, useEffect, useRef, useState } from 'preact/hooks';
import { Card, ErrorBanner, Header, Skeleton } from '../components/common';
import { TimeChart, type SeriesDef } from '../charts/TimeChart';
import { DailyChart, EnergyBars, EnergySummary, StatRow } from '../charts/widgets';
import { usePoll } from '../lib/poll';
import { ApiError, getJson, RANGES, type DailyPayload, type HistoryPayload, type Range, type SummaryPayload } from '../lib/api';
import type { LiveState } from '../lib/live';
import { t, tx } from '../lib/i18n';
import { colors } from '../lib/tokens';

export const POWER: SeriesDef[] = [
  { key: 'grid_power', label: tx('Grid (+import)'), color: colors.grid, unit: 'W' },
  { key: 'battery_power_signed', label: tx('Battery (+charge)'), color: colors.battery, unit: 'W' },
  { key: 'total_load_power', label: tx('Load'), color: colors.load, unit: 'W' },
  { key: 'pv_power', label: tx('PV'), color: colors.pv, unit: 'W' },
];
export const SOC: SeriesDef[] = [{ key: 'battery_soc', label: tx('SOC'), color: colors.battery, unit: '%', fill: true }];
const BATT_VI: SeriesDef[] = [
  { key: 'battery_voltage', label: tx('Voltage'), color: colors.battery, unit: 'V' },
  { key: 'battery_current', label: tx('Current'), color: colors.batteryAlt, unit: 'A', right: true },
];
export const GRID_V: SeriesDef[] = [
  { key: 'grid_voltage', label: tx('Grid'), color: colors.grid, unit: 'V' },
  { key: 'backup_voltage', label: tx('Backup output'), color: colors.load, unit: 'V' },
];
export const GRID_F: SeriesDef[] = [{ key: 'grid_frequency', label: tx('Frequency'), color: colors.grid, unit: 'Hz' }];

const FIELDS = [...POWER, ...SOC, ...BATT_VI, ...GRID_V, ...GRID_F].map((s) => s.key).join(',');
// Refresh about once per aggregation window (every=auto: 1h->10s, 6h->1m, 24h->5m, 7d->30m, 30d->3h).
export const REFRESH_MS: Record<Range, number> = { '1h': 10_000, '6h': 60_000, '24h': 300_000, '7d': 600_000, '30d': 900_000 };

export function readRange(): Range {
  const r = new URLSearchParams(location.search).get('range');
  return (RANGES as readonly string[]).includes(r ?? '') ? (r as Range) : '6h';
}

export function useFetch<T>(url: string, refreshMs: number | null) {
  const [state, setState] = useState<{ data: T | null; error: string | null; loading: boolean }>({ data: null, error: null, loading: true });
  const ctl = useRef<AbortController>();
  const load = useCallback(async () => {
    ctl.current?.abort();
    const c = (ctl.current = new AbortController());
    setState((s) => ({ ...s, loading: true }));
    try {
      const data = await getJson<T>(url, c.signal);
      setState({ data, error: null, loading: false });
    } catch (e) {
      if ((e as Error).name === 'AbortError') return;
      setState((s) => ({ ...s, error: e instanceof ApiError ? e.message : 'Request failed', loading: false }));
    }
  }, [url]);
  useEffect(() => {
    setState((s) => ({ ...s, data: null }));   // new range: show skeletons, not stale shapes
    load();
    return () => ctl.current?.abort();
  }, [load]);
  useEffect(() => {
    if (!refreshMs) return;
    const id = window.setInterval(load, refreshMs);
    return () => window.clearInterval(id);
  }, [load, refreshMs]);
  return state;
}

export function Charts({ live, now }: { live: LiveState; now: number }) {
  const [range, setRange] = useState<Range>(readRange);
  const [auto, setAuto] = useState(true);

  useEffect(() => {
    const p = new URLSearchParams(location.search);
    p.set('range', range);
    history.replaceState(null, '', `?${p}`);
  }, [range]);

  const hist = useFetch<HistoryPayload>(`/api/history?fields=${FIELDS}&range=${range}&every=auto&agg=mean`,
                                        auto ? REFRESH_MS[range] : null);
  const summary = usePoll<SummaryPayload>('/api/energy/summary', 300_000);
  const daily = useFetch<DailyPayload>('/api/energy/daily?days=30', auto ? 300_000 : null);
  const h = hist.data;
  const f = live.data?.fields;
  const err = hist.error ?? live.error;

  const chart = (title: string, series: SeriesDef[], opts: { zeroLine?: boolean; minY?: number; maxY?: number; height?: number } = {}) => (
    <Card title={title} class="chart-card">
      {h ? <TimeChart data={h} series={series} {...opts} /> : hist.error ? <div class="chart-error">{t(hist.error)}</div> : <Skeleton h={opts.height ?? 220} />}
    </Card>
  );

  return (
    <div class="page charts">
      <Header live={live} now={now} title="Charts" />
      <div class="controls">
        <div class="seg" role="group" aria-label={t('Time range')}>
          {RANGES.map((r) => (
            <button type="button" key={r} class={r === range ? 'on' : ''} aria-pressed={r === range}
                    onClick={() => setRange(r)} data-testid={`range-${r}`}>{t(r)}</button>
          ))}
        </div>
        <label class="toggle">
          <input type="checkbox" checked={auto} onChange={(e) => setAuto((e.target as HTMLInputElement).checked)} />
          <span>{t('Auto-refresh')}</span>
          {hist.loading && <span class="spinner" aria-label={t('Loading')} />}
        </label>
      </div>
      {err && <ErrorBanner message={err} stale={!!h || !!live.data?.time} />}

      {f ? <StatRow f={f} /> : <div class="stat-row">{Array.from({ length: 6 }, (_, i) => <Skeleton key={i} h={96} />)}</div>}

      <div class="chart-grid">
        <div class="span-2">{chart('Power flows', POWER, { zeroLine: true, height: 280 })}</div>
        {chart('Battery SOC', SOC, { minY: 0, maxY: 100 })}
        {chart('Battery voltage & current', BATT_VI)}
        {chart('Grid & backup voltage', GRID_V)}
        {chart('Grid frequency', GRID_F)}
        <div class="span-2">
          <Card title="Energy today">{f ? <EnergyBars f={f} /> : <Skeleton h={180} />}</Card>
        </div>
        <div class="span-2">
          <Card title="Energy summary">{f ? <EnergySummary f={f} summary={summary.data} /> : <Skeleton h={200} />}</Card>
        </div>
        <div class="span-2">
          <Card title="Daily energy · last 30 days">
            {daily.data ? <DailyChart data={daily.data} /> : daily.error ? <div class="chart-error">{t(daily.error)}</div> : <Skeleton h={240} />}
          </Card>
        </div>
      </div>
    </div>
  );
}
