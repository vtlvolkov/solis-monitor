import { useState } from 'preact/hooks';
import { Card, ErrorBanner, Header, Skeleton } from '../components/common';
import type { DeviceInfo } from '../lib/devices';
import { ago, clockTime } from '../lib/format';
import { locale, t, tp } from '../lib/i18n';
import type { LiveState } from '../lib/live';
import { usePoll, type EventItem, type EventsPayload } from '../lib/poll';
import { formatHours, gridLost, runtimeHours } from '../lib/runtime';

const RANGES = [7, 30, 90] as const;

const when = (iso: string | null) =>
  iso ? new Date(iso).toLocaleString(locale(), { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }) : '—';

function duration(s: number | null): string {
  if (s == null) return '—';
  if (s < 60) return t('{n} s', { n: s });
  if (s < 3600) return t('{n} min', { n: Math.round(s / 60) });
  return formatHours(s / 3600);
}

export function outageStats(outages: EventItem[]) {
  const done = outages.filter((o) => o.duration_s != null);
  const total = done.reduce((a, o) => a + (o.duration_s ?? 0), 0);
  const longest = done.reduce((a, o) => Math.max(a, o.duration_s ?? 0), 0);
  return { count: outages.length, total, longest };
}

function OutageRow({ o }: { o: EventItem }) {
  const soc = o.soc_start != null
    ? `${Math.round(o.soc_start)}% → ${o.soc_end != null ? Math.round(o.soc_end) + '%' : o.ongoing ? t('now') : '—'}`
    : '—';
  return (
    <li class={`ev ${o.ongoing ? 'ev-live' : ''}`}>
      <span class="ev-dot outage" />
      <div class="ev-main">
        <div class="ev-title">{o.ongoing ? t('Outage in progress') : t('Grid outage')}</div>
        <div class="ev-sub">{when(o.start)} → {o.ongoing ? t('now') : when(o.end)} · {t('battery')} {soc}</div>
      </div>
      <span class="ev-dur">{duration(o.duration_s)}</span>
    </li>
  );
}

function AlarmRow({ a }: { a: EventItem }) {
  return (
    <li class={`ev ${a.ongoing ? 'ev-live' : ''}`}>
      <span class={`ev-dot ${a.severity === 'fault' ? 'fault' : 'warning'}`} />
      <div class="ev-main">
        <div class="ev-title">{a.name ? t(a.name) : a.key}{a.ongoing && <span class="pill pill-warn ev-pill">{t('Active')}</span>}</div>
        <div class="ev-sub">{when(a.start)} → {a.ongoing ? t('now') : when(a.end)} · {t(a.severity ?? 'alarm')}</div>
      </div>
      <span class="ev-dur">{duration(a.duration_s)}</span>
    </li>
  );
}

export function Events({ live, now }: { live: LiveState; now: number }) {
  const [days, setDays] = useState<(typeof RANGES)[number]>(30);
  const { data, error } = usePoll<EventsPayload>(`/api/events?days=${days}`, 30_000);
  const info = usePoll<DeviceInfo>('/api/devices', 60_000).data;
  const f = live.data?.fields;
  const lost = !!live.data?.time && (gridLost(f) || !!data?.outage_now);
  const activeAlarms = info?.inverter.alarms ?? [];
  const floor = info?.backup.eps_dod_pct ?? info?.battery.overdischarge_soc;
  const h = runtimeHours(f?.battery_soc, floor, info?.battery.capacity_kwh, f?.total_load_power);
  const stats = data ? outageStats(data.outages) : null;

  return (
    <div class="page events">
      <Header live={live} now={now} title="Events" />
      {error && <ErrorBanner message={error} stale={!!data} />}

      <div class="events-grid">
        <Card title="Now">
          <div class={`now-row ${lost ? 'bad' : 'ok'}`} data-testid="grid-state">
            <span class="now-dot" />
            <div>
              <div class="now-title">{lost ? t('Grid outage') : t('Grid connected')}</div>
              <div class="setting-hint">
                {lost
                  ? <>{t('On battery')}{data?.outage_now?.start ? ` ${t('since {time} ({ago})', { time: clockTime(data.outage_now.start), ago: ago((now - Date.parse(data.outage_now.start)) / 1000) })}` : ''} · ≈ {formatHours(h)} {t('left')}</>
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

        <Card title="Power outages" aside={
          <div class="seg seg-small" role="group" aria-label={t('Period')}>
            {RANGES.map((d) => <button type="button" key={d} class={d === days ? 'on' : ''} aria-pressed={d === days} onClick={() => setDays(d)}>{t('{n} d', { n: d })}</button>)}
          </div>
        }>
          {!data ? <Skeleton h={120} /> : (
            <>
              <div class="ev-stats" data-testid="outage-stats">
                <div><span>{stats!.count}</span>{t('outages')}</div>
                <div><span>{duration(stats!.total)}</span>{t('total')}</div>
                <div><span>{duration(stats!.longest)}</span>{t('longest')}</div>
              </div>
              {data.outages.length === 0
                ? <div class="setting-hint">{t('No outages recorded in the last {days} days.', { days })}</div>
                : <ul class="ev-list">{data.outages.map((o) => <OutageRow key={`${o.start}-${o.end}`} o={o} />)}</ul>}
            </>
          )}
        </Card>

        <Card title="Alarm history" class="span-2">
          {!data ? <Skeleton h={120} /> : data.alarms.length === 0
            ? <div class="setting-hint">{t('No alarms recorded in the last {days} days.', { days })}</div>
            : <ul class="ev-list">{data.alarms.map((a) => <AlarmRow key={`${a.key}-${a.start}-${a.end}`} a={a} />)}</ul>}
        </Card>
      </div>
      <div class="setting-hint detail-foot">
        {t('Outages and alarms are recorded by the poller from the inverter\'s fault registers (checked every 10 s, a change must persist for 2 readings). History starts when this feature was installed.')}
      </div>
    </div>
  );
}
