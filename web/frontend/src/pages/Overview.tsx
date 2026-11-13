import { useState } from 'preact/hooks';
import { BatteryCard, Card, ErrorBanner, Header, Skeleton, TodayRow } from '../components/common';
import { CostCard, GridCard, ScheduleCard, StatusChips, type CostPayload } from '../components/OverviewCards';
import { OutageCard } from '../components/OutageCard';
import { BatteryHealthCard, LoadHeatmapCard, PowerTodayCard, RecentEventsCard, SelfSufficiencyCard } from '../components/MoreCards';
import { AddCardsDialog, EditBar, LayoutColumns, useLayoutEditor, useSavedLayout } from '../components/OverviewEditor';
import type { OutagePayload } from '../lib/outages';
import type { SettingsPayload } from '../lib/settings';
import { GaugeBars } from '../components/GaugeBars';
import { Runtime, useAvgLoad, useFullCharge } from '../components/Runtime';
import { PowerFlow } from '../components/PowerFlow';
import type { HistoryPayload } from '../lib/api';
import type { DeviceInfo } from '../lib/devices';
import { ago, clockTime } from '../lib/format';
import { t } from '../lib/i18n';
import { addCard, defaultLayout, onPage } from '../lib/layout';
import type { LiveState } from '../lib/live';
import { usePoll, type EventsPayload } from '../lib/poll';
import { formatHours, gridLost, runtimeHours } from '../lib/runtime';

export function Overview({ live, now }: { live: LiveState; now: number }) {
  const fields = live.data?.fields ?? null;
  const hasPoint = !!live.data?.time;
  const info = usePoll<DeviceInfo>('/api/devices', 60_000).data;
  const events = usePoll<EventsPayload>('/api/events?days=7', 60_000).data;
  const avgLoad = useAvgLoad();
  const planned = usePoll<OutagePayload>('/api/outage-schedule', 300_000);
  const outagesByDefault = planned.data?.show ?? false;          // before the layout was ever saved: the card's own setting
  const saved = useSavedLayout();
  const editor = useLayoutEditor(saved.layout ?? defaultLayout(outagesByDefault), outagesByDefault, saved.reload);
  const [adding, setAdding] = useState(false);
  const layout = editor.edit?.layout ?? saved.layout ?? defaultLayout(outagesByDefault);
  const shown = onPage(layout);

  // Each card's data is asked for only while the card is on the page.
  const has = (id: string) => shown.has(id);
  const vmin = usePoll<HistoryPayload>(has('grid') ? '/api/history?fields=grid_voltage&range=24h&every=1h&agg=min' : null, 300_000).data;
  const vmax = usePoll<HistoryPayload>(has('grid') ? '/api/history?fields=grid_voltage&range=24h&every=1h&agg=max' : null, 300_000).data;
  const sched = usePoll<SettingsPayload>(has('schedule') || has('gauges') ? '/api/schedule' : null, 120_000);
  const cost = usePoll<CostPayload>(has('cost') ? '/api/cost' : null, 300_000).data;
  const fullIn = useFullCharge(fields, info);
  const outage = hasPoint && (gridLost(fields) || !!events?.outage_now);
  const since = events?.outage_now?.start;
  const h = runtimeHours(fields?.battery_soc, info?.backup.eps_dod_pct ?? info?.battery.overdischarge_soc,
                         info?.battery.capacity_kwh, fields?.total_load_power);

  const render = (id: string) => {
    switch (id) {
      case 'flow':
        return (
          <div class="diagram-wrap">
            {fields ? <PowerFlow fields={fields} /> : live.error
              ? <div class="diagram-error"><PowerFlow fields={null} /></div>
              : <Skeleton h={420} />}
          </div>
        );
      case 'battery':
        return fields ? <BatteryCard fields={fields} fullIn={fullIn} extra={<Runtime live={live} info={info} avgLoad={avgLoad} />} />
          : <Card title="Battery"><Skeleton h={56} /></Card>;
      case 'gauges': return <GaugeBars fields={fields} settings={sched.data} error={sched.error} />;
      case 'grid': return <GridCard f={fields} events={events} vmin={vmin} vmax={vmax} />;
      case 'outages': return <OutageCard o={planned.data} error={planned.error} now={now} />;
      case 'schedule': return <ScheduleCard s={sched.data} error={sched.error} />;
      case 'today': return fields ? <TodayRow fields={fields} /> : <Card title="Today"><Skeleton h={96} /></Card>;
      case 'cost': return <CostCard c={cost} />;
      case 'power': return <PowerTodayCard />;
      case 'selfsufficiency': return <SelfSufficiencyCard fields={fields} />;
      case 'events': return <RecentEventsCard events={events} />;
      case 'health': return <BatteryHealthCard fields={fields} info={info} />;
      case 'heatmap': return <LoadHeatmapCard />;
      default: return null;
    }
  };

  const customize = (
    <button type="button" class="link" onClick={editor.start} disabled={editor.state.busy || !!editor.edit} data-testid="customize">
      {t('Customize')}
    </button>
  );

  return (
    <div class="page overview">
      <Header live={live} now={now} extra={editor.edit ? undefined : customize} />
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
      {!editor.edit && editor.state.error && (
        <div class="banner" role="alert">
          <strong>{t(editor.state.error)}</strong>
          {editor.state.signIn && <button type="button" class="link" onClick={editor.start}>{t('Sign in')}</button>}
          <button type="button" class="link" onClick={editor.dismiss}>{t('Close')}</button>
        </div>
      )}
      {editor.edit && (
        <EditBar edit={editor.edit} state={editor.state} outages={outagesByDefault} onAdd={() => setAdding(true)}
                 onReset={() => editor.set(defaultLayout(outagesByDefault))} onCancel={editor.cancel} onSave={editor.save} />
      )}
      {!editor.edit && !shown.size && (
        <div class="banner" role="status">{t('The Overview has no cards. Press Customize to add some.')}</div>
      )}
      <LayoutColumns layout={layout} editing={!!editor.edit} onChange={editor.set} render={render} />
      {adding && editor.edit && (
        <AddCardsDialog layout={layout} onClose={() => setAdding(false)}
                        onAdd={(id) => { editor.set(addCard(layout, id)); setAdding(false); scrollToCard(id); }} />
      )}
    </div>
  );
}

/** After adding: bring the new card into view and flash it once. */
function scrollToCard(id: string) {
  requestAnimationFrame(() => {
    const el = document.querySelector<HTMLElement>(`[data-card="${id}"]`);
    if (!el) return;
    el.scrollIntoView({ block: 'center', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
    el.classList.add('flash');
    setTimeout(() => el.classList.remove('flash'), 1600);
  });
}
