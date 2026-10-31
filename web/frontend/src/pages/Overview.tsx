import { BatteryCard, Card, ErrorBanner, Header, Skeleton, TodayRow } from '../components/common';
import { CostCard, GridCard, ScheduleCard, StatusChips, type CostPayload } from '../components/OverviewCards';
import type { SettingsPayload } from '../lib/settings';
import { GaugeBars } from '../components/GaugeBars';
import { Runtime, useFullCharge } from '../components/Runtime';
import { PowerFlow } from '../components/PowerFlow';
import type { HistoryPayload } from '../lib/api';
import type { DeviceInfo } from '../lib/devices';
import { ago, clockTime } from '../lib/format';
import { t } from '../lib/i18n';
import type { LiveState } from '../lib/live';
import { usePoll, type EventsPayload } from '../lib/poll';
import { formatHours, gridLost, mean, runtimeHours } from '../lib/runtime';

export function Overview({ live, now }: { live: LiveState; now: number }) {
  const fields = live.data?.fields ?? null;
  const hasPoint = !!live.data?.time;
  const info = usePoll<DeviceInfo>('/api/devices', 60_000).data;
  const events = usePoll<EventsPayload>('/api/events?days=7', 60_000).data;
  const hist = usePoll<HistoryPayload>('/api/history?fields=total_load_power&range=1h&every=auto&agg=mean', 300_000).data;
  const avgLoad = hist ? mean(hist.series.total_load_power ?? []) : null;
  const vmin = usePoll<HistoryPayload>('/api/history?fields=grid_voltage&range=24h&every=1h&agg=min', 300_000).data;
  const vmax = usePoll<HistoryPayload>('/api/history?fields=grid_voltage&range=24h&every=1h&agg=max', 300_000).data;
  const sched = usePoll<SettingsPayload>('/api/schedule', 120_000);
  const cost = usePoll<CostPayload>('/api/cost', 300_000).data;
  const fullIn = useFullCharge(fields, info);
  const outage = hasPoint && (gridLost(fields) || !!events?.outage_now);
  const since = events?.outage_now?.start;
  const h = runtimeHours(fields?.battery_soc, info?.backup.eps_dod_pct ?? info?.battery.overdischarge_soc,
                         info?.battery.capacity_kwh, fields?.total_load_power);

  return (
    <div class="page overview">
      <Header live={live} now={now} />
      {hasPoint && <StatusChips f={fields} info={info} lost={outage} />}
      {live.error && <ErrorBanner message={live.error} stale={hasPoint} />}
      {outage && (
        <div class="banner banner-bad outage-banner" role="alert" data-testid="outage-banner">
          <strong>{since ? t('Grid outage since {time} ({ago})', { time: clockTime(since), ago: ago((now - Date.parse(since)) / 1000) }) : t('Grid outage')}</strong>
          <span>{t('Running on battery')} · {fields?.battery_soc == null ? '--' : Math.round(fields.battery_soc) + '%'} ·
            {' '}{t('≈ {h} left at the current load', { h: formatHours(h) })}</span>
        </div>
      )}
      {live.data && !hasPoint && !live.error && (
        <div class="banner" role="status" data-testid="empty">{t('No data in InfluxDB yet. Is the poller running?')}</div>
      )}
      <div class="overview-grid">
        <div class="overview-left">
          <div class="diagram-wrap">
            {fields ? <PowerFlow fields={fields} /> : live.error
              ? <div class="diagram-error"><PowerFlow fields={null} /></div>
              : <Skeleton h={420} />}
          </div>
          <div class="battery-wrap">
            {fields ? <BatteryCard fields={fields} fullIn={fullIn} extra={<Runtime live={live} info={info} avgLoad={avgLoad} />} />
                    : <Card title="Battery"><Skeleton h={56} /></Card>}
          </div>
          <GaugeBars fields={fields} settings={sched.data} error={sched.error} />
        </div>
        <div class="overview-side">
          <GridCard f={fields} events={events} vmin={vmin} vmax={vmax} />
          <ScheduleCard s={sched.data} error={sched.error} />
          {fields ? <TodayRow fields={fields} /> : <Card title="Today"><Skeleton h={96} /></Card>}
          <CostCard c={cost} />
        </div>
      </div>
    </div>
  );
}
