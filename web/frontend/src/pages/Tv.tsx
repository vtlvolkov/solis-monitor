import { useEffect, useState } from 'preact/hooks';
import { BatteryCard, Card, ErrorBanner, LangSwitch, Skeleton, StatusPill, TodayRow } from '../components/common';
import { GaugeBars } from '../components/GaugeBars';
import { PowerFlow } from '../components/PowerFlow';
import { Runtime } from '../components/Runtime';
import { TimeChart } from '../charts/TimeChart';
import { POWER, REFRESH_MS, readRange, useFetch } from './Charts';
import { RANGES, type HistoryPayload, type Range } from '../lib/api';
import { locale, t } from '../lib/i18n';
import { useSite } from '../lib/site';
import { stageFit, stepRange } from '../lib/tv';
import { clockTime, power } from '../lib/format';
import type { LiveState } from '../lib/live';
import { usePoll, type EventsPayload } from '../lib/poll';
import { formatHours, gridLost, mean, runtimeHours } from '../lib/runtime';
import type { DeviceInfo } from '../lib/devices';
import type { SettingsPayload } from '../lib/settings';

// Overview + Charts on one screen for a TV; see lib/tv.ts for the stage scaling.
const FIELDS = POWER.map((s) => s.key).join(',');

function useStage() {
  const [fit, setFit] = useState(() => stageFit(innerWidth, innerHeight));
  useEffect(() => {
    const on = () => setFit(stageFit(innerWidth, innerHeight));
    addEventListener('resize', on);
    document.documentElement.classList.add('tv-mode');
    return () => { removeEventListener('resize', on); document.documentElement.classList.remove('tv-mode'); };
  }, []);
  return fit;
}

/** Back to the normal dashboard (a full page load, so the wall display's state is dropped). */
const exitTv = () => location.assign(location.pathname);

export function Tv({ live, now }: { live: LiveState; now: number }) {
  const fit = useStage();
  const site = useSite();
  const [range, setRange] = useState<Range>(readRange);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') return exitTv();
      if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
      e.preventDefault();
      setRange((r) => stepRange(r, e.key === 'ArrowRight' ? 1 : -1));
    };
    addEventListener('keydown', onKey);
    return () => removeEventListener('keydown', onKey);
  }, []);

  const fields = live.data?.fields ?? null;
  const hasPoint = !!live.data?.time;
  const info = usePoll<DeviceInfo>('/api/devices', 60_000).data;
  const load1h = usePoll<HistoryPayload>('/api/history?fields=total_load_power&range=1h&every=auto&agg=mean', 300_000);   // for the runtime estimate
  const events = usePoll<EventsPayload>('/api/events?days=7', 60_000).data;
  const avgLoad = (() => { const s = load1h.data?.series.total_load_power; return s ? mean(s) : null; })();
  const sched = usePoll<SettingsPayload>('/api/schedule', 120_000);   // the current limits, for the battery gauges
  const hist = useFetch<HistoryPayload>(`/api/history?fields=${FIELDS}&range=${range}&every=auto&agg=mean`, REFRESH_MS[range]);
  const h = hist.data;
  const outage = hasPoint && (gridLost(fields) || !!events?.outage_now);
  const hoursLeft = runtimeHours(fields?.battery_soc, info?.backup.eps_dod_pct ?? info?.battery.overdischarge_soc,
                                 info?.battery.capacity_kwh, fields?.total_load_power);
  const chart = (title: string, series: typeof POWER, height: number, opts: { zeroLine?: boolean; minY?: number; maxY?: number; fill?: boolean } = {}) => (
    <Card title={title} class={`chart-card ${opts.fill ? 'chart-fill' : ''}`}>
      {h ? <TimeChart data={h} series={series} height={height} font={18} inline {...opts} />
        : hist.error ? <div class="chart-error">{hist.error}</div> : <Skeleton h={height} />}
    </Card>
  );

  const diagram = <div class="tv-flow">{fields ? <PowerFlow fields={fields} /> : <Skeleton h={560} />}</div>;
  const bars = <GaugeBars fields={fields} settings={sched.data} error={sched.error} />;
  const battery = fields
    ? <BatteryCard fields={fields} extra={<Runtime live={live} info={info} avgLoad={avgLoad} />} />
    : <Card title="Battery"><Skeleton h={100} /></Card>;
  const today = <div class="tv-today">{fields ? <TodayRow fields={fields} /> : <Card title="Today"><Skeleton h={60} /></Card>}</div>;
  const power_ = chart('Power flows', POWER, 400, { zeroLine: true, fill: true });     // fills the rest of the column, so it ends level with the left one

  return (
    <div class="tv-viewport">
      <div class="tv-stage" style={{ transform: `translate(${fit.x}px, ${fit.y}px) scale(${fit.scale})` }}>
        <header class="tv-top">
          <h1>{site || 'Solis'}</h1>
          <div class="tv-top-meta">
            <div class="seg" role="group" aria-label={t('Chart range')} data-testid="tv-range">
              {RANGES.map((r) => (
                <button type="button" key={r} class={r === range ? 'on' : ''} aria-pressed={r === range} tabIndex={-1}
                        onClick={() => setRange(r)}>{t(r)}</button>
              ))}
            </div>
            <StatusPill live={live} now={now} />
            <span class="tv-clock">{new Date(now).toLocaleTimeString(locale(), { hour: '2-digit', minute: '2-digit' })}</span>
            <span class="updated">{live.data?.time ? t('Updated {time}', { time: clockTime(live.data.time) }) : ''}</span>
            {/* tabIndex -1: the TV remote's arrow keys can't land on it, so the app can't be left by accident */}
            <button type="button" class="tv-exit" tabIndex={-1} onClick={exitTv} data-testid="tv-exit">{t('Exit TV view')}</button>
            <LangSwitch tabIndex={-1} />
          </div>
        </header>
        {live.error && <ErrorBanner message={live.error} stale={hasPoint} />}
        {outage && (
          <div class="banner banner-bad outage-banner" role="alert" data-testid="outage-banner">
            <strong>{t('Grid outage')}</strong>
            <span>{t('Running on battery')} · {fields?.battery_soc == null ? '--' : Math.round(fields.battery_soc) + '%'} ·
              {' '}{t('≈ {h} left at {load}', { h: formatHours(hoursLeft), load: power(fields?.total_load_power) })}</span>
          </div>
        )}
        <div class="tv-grid">
          <div class="tv-col">{diagram}{battery}</div>
          <div class="tv-col">{bars}{power_}{today}</div>
        </div>
      </div>
    </div>
  );
}
