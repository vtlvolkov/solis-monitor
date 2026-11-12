import { useState } from 'preact/hooks';
import { Card, ErrorBanner, Header, Skeleton } from '../components/common';
import { DayPicker, useDayParam } from '../components/DayPicker';
import { eventsExportUrl, savedSeparator } from '../lib/export';
import { DayTimeline } from '../components/DayTimeline';
import { dayLabel, duration, outageBatteryKwh, spansInDay, withinDay } from '../lib/day';
import type { DeviceInfo } from '../lib/devices';
import { ago, clockTime } from '../lib/format';
import { locale, t, tp } from '../lib/i18n';
import type { LiveState } from '../lib/live';
import { usePoll, type EventItem, type EventsPayload } from '../lib/poll';
import { formatHours, gridLost, runtimeHours } from '../lib/runtime';
import { kwh } from '../lib/format';

const RANGES = [7, 30, 90] as const;

const when = (iso: string | null) =>
  iso ? new Date(iso).toLocaleString(locale(), { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }) : '—';

export function outageStats(outages: EventItem[]) {
  const done = outages.filter((o) => o.duration_s != null);
  const total = done.reduce((a, o) => a + (o.duration_s ?? 0), 0);
  const longest = done.reduce((a, o) => Math.max(a, o.duration_s ?? 0), 0);
  return { count: outages.length, total, longest };
}

type Bounds = { start: number; stop: number } | null;
/** Clock times only when the whole event is on the day being shown. */
const whenIn = (iso: string | null, e: EventItem, day: Bounds) =>
  iso && day && withinDay(e, day.start, day.stop)
    ? new Date(iso).toLocaleTimeString(locale(), { hour: '2-digit', minute: '2-digit' }) : when(iso);

function OutageRow({ o, day }: { o: EventItem; day: Bounds }) {
  const soc = o.soc_start != null
    ? `${Math.round(o.soc_start)}% → ${o.soc_end != null ? Math.round(o.soc_end) + '%' : o.ongoing ? t('now') : '—'}`
    : '—';
  return (
    <li class={`ev ${o.ongoing ? 'ev-live' : ''}`}>
      <span class="ev-dot outage" />
      <div class="ev-main">
        <div class="ev-title">{o.ongoing ? t('Outage in progress') : t('Grid outage')}</div>
        <div class="ev-sub">{whenIn(o.start, o, day)} → {o.ongoing ? t('now') : whenIn(o.end, o, day)} · {t('battery')} {soc}</div>
      </div>
      <span class="ev-dur">{duration(o.duration_s)}</span>
    </li>
  );
}

function AlarmRow({ a, day }: { a: EventItem; day: Bounds }) {
  return (
    <li class={`ev ${a.ongoing ? 'ev-live' : ''}`}>
      <span class={`ev-dot ${a.severity === 'fault' ? 'fault' : 'warning'}`} />
      <div class="ev-main">
        <div class="ev-title">{a.name ? t(a.name) : a.key}{a.ongoing && <span class="pill pill-warn ev-pill">{t('Active')}</span>}</div>
        <div class="ev-sub">{whenIn(a.start, a, day)} → {a.ongoing ? t('now') : whenIn(a.end, a, day)} · {t(a.severity ?? 'alarm')}</div>
      </div>
      <span class="ev-dur">{duration(a.duration_s)}</span>
    </li>
  );
}

export function Events({ live, now }: { live: LiveState; now: number }) {
  const [days, setDays] = useState<(typeof RANGES)[number]>(30);
  const [day, setDay] = useDayParam();
  const { data: raw, error } = usePoll<EventsPayload>(day ? `/api/day/events?date=${day}` : `/api/events?days=${days}`, 30_000);
  // The lists wait for the answer about this day or range; the Now card can use any answer, they all describe the present.
  const data = raw && (raw.date ?? null) === day && (day || raw.days === days) ? raw : null;
  const info = usePoll<DeviceInfo>('/api/devices', 60_000).data;
  const f = live.data?.fields;
  const lost = !!live.data?.time && (gridLost(f) || !!raw?.outage_now);
  const activeAlarms = info?.inverter.alarms ?? [];
  const floor = info?.backup.eps_dod_pct ?? info?.battery.overdischarge_soc;
  const h = runtimeHours(f?.battery_soc, floor, info?.battery.capacity_kwh, f?.total_load_power);
  const stats = data ? outageStats(data.outages) : null;
  const bounds: Bounds = day && data?.start != null && data.stop != null ? { start: data.start, stop: data.stop } : null;

  return (
    <div class="page events">
      <Header live={live} now={now} title="Events" />
      <div class="controls">
        <div class="controls-group">
          <div class="seg" role="group" aria-label={t('Period')}>
            {RANGES.map((d) => <button type="button" key={d} class={d === days && !day ? 'on' : ''} aria-pressed={d === days && !day} onClick={() => { setDays(d); setDay(null); }}>{t('{n} d', { n: d })}</button>)}
          </div>
          <DayPicker day={day} onChange={setDay} />
        </div>
        <div class="controls-group">
          {day && <button type="button" class="link" onClick={() => setDay(null)} data-testid="day-back">{t('Back to last {n} days', { n: days })}</button>}
          <a class="btn btn-small" href={eventsExportUrl(days, day, savedSeparator())} download
             title={t('Outages and alarms as rows: start, end, duration, battery before and after')} data-testid="events-export">{t('Export CSV')}</a>
        </div>
      </div>
      {error && <ErrorBanner message={error} stale={!!raw} />}

      <div class="events-grid">
        <Card title="Now" class="ev-top">
          <div class={`now-row ${lost ? 'bad' : 'ok'}`} data-testid="grid-state">
            <span class="now-dot" />
            <div>
              <div class="now-title">{lost ? t('Grid outage') : t('Grid connected')}</div>
              <div class="setting-hint">
                {lost
                  ? <>{t('On battery')}{raw?.outage_now?.start ? ` ${t('since {time} ({ago})', { time: clockTime(raw.outage_now.start), ago: ago((now - Date.parse(raw.outage_now.start)) / 1000) })}` : ''} · ≈ {formatHours(h)} {t('left')}</>
                  : <>{t('Battery would last ≈ {h} at the current load', { h: formatHours(h) })}{floor != null ? ` ${t('(down to {floor}%)', { floor })}` : ''}</>}
              </div>
            </div>
          </div>
          <div class={`now-row ${activeAlarms.length ? 'bad' : 'ok'}`} data-testid="alarm-state">
            <span class="now-dot" />
            <div>
              <div class="now-title">{activeAlarms.length ? `${activeAlarms.length} ${tp(activeAlarms.length, 'active alarm|active alarms')}` : t('No active alarms')}</div>
              {activeAlarms.length > 0 && <div class="setting-hint">{activeAlarms.map((a) => t(a.name)).join(', ')}</div>}
              {info && <div class="setting-hint">{t('Inverter state:')} {t(info.inverter.status)}</div>}
            </div>
          </div>
        </Card>

        {day && (
          <Card title={dayLabel(day)} class="ev-top">
            {!data || data.start == null || data.stop == null ? <Skeleton h={120} /> : (() => {
              const offGrid = spansInDay(data.outages, data.start, data.stop, now / 1000).reduce((a, [x, y]) => a + y - x, 0);
              return (
                <>
                  <div class="ev-stats ev-stats-4" data-testid="day-summary">
                    <div><span>{data.outages.length}</span>{t('outages')}</div>
                    <div><span>{duration(Math.round(offGrid))}</span>{t('without grid')}</div>
                    <div><span>{(() => { const b = outageBatteryKwh(data.outages, data.start, data.stop, info?.battery.capacity_kwh); return b == null ? '—' : `≈ ${kwh(b)}`; })()}</span>{t('from battery')}</div>
                    <div><span>{data.alarms.length}</span>{tp(data.alarms.length, 'alarm|alarms')}</div>
                  </div>
                  <DayTimeline outages={data.outages} alarms={data.alarms} start={data.start} stop={data.stop} now={now / 1000} />
                </>
              );
            })()}
          </Card>
        )}

        {!day && (
          <Card title={t('Last {n} days', { n: days })} class="ev-top">
            {!data ? <Skeleton h={120} /> : (
              <div class="ev-stats ev-stats-4 ev-stats-fill" data-testid="outage-stats">
                <div><span>{stats!.count}</span>{t('outages')}</div>
                <div><span>{duration(stats!.total)}</span>{t('total')}</div>
                <div><span>{duration(stats!.longest)}</span>{t('longest')}</div>
                <div><span>{data.alarms.length}</span>{tp(data.alarms.length, 'alarm|alarms')}</div>
              </div>
            )}
          </Card>
        )}

        <Card title="Power outages">
          {!data ? <Skeleton h={120} /> : (
            <>
              {data.outages.length === 0
                ? <div class="setting-hint">{day ? t('No outages on this day.') : t('No outages recorded in the last {days} days.', { days })}</div>
                : <ul class="ev-list">{data.outages.map((o) => <OutageRow key={`${o.start}-${o.end}`} o={o} day={bounds} />)}</ul>}
            </>
          )}
        </Card>

        <Card title="Alarm history">
          {!data ? <Skeleton h={120} /> : data.alarms.length === 0
            ? <div class="setting-hint">{day ? t('No alarms on this day.') : t('No alarms recorded in the last {days} days.', { days })}</div>
            : <ul class="ev-list">{data.alarms.map((a) => <AlarmRow key={`${a.key}-${a.start}-${a.end}`} a={a} day={bounds} />)}</ul>}
        </Card>
      </div>
      <div class="setting-hint detail-foot">
        {t('Outages and alarms are recorded by the poller from the inverter\'s fault registers (checked every 10 s, a change must persist for 2 readings). History starts when this feature was installed.')}
      </div>
    </div>
  );
}
