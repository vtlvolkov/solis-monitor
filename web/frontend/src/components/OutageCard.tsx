import type { ComponentChildren } from 'preact';
import { formatHours } from '../lib/runtime';
import { locale, t } from '../lib/i18n';
import { goTo } from '../lib/nav';
import { describeDay, hhmm, outageSlots, slotText, verdictText, type OutagePayload, type ScheduleDay } from '../lib/outages';
import { Card, Skeleton } from './common';

const time = (iso: string) => new Date(iso).toLocaleTimeString(locale(), { hour: '2-digit', minute: '2-digit' });
const dur = (s: number) => formatHours(s / 3600);

/** One day as a 24-hour bar: outages in red (possible ones striped), the current time as a line. */
function DayBar({ label, day, nowMin }: { label: string; day: ScheduleDay; nowMin: number | null }) {
  const slots = outageSlots(day);
  return (
    <div class="outage-day">
      <div class="outage-day-head"><span>{label}</span><span class="muted">{describeDay(day)}</span></div>
      <div class={`outage-bar ${day.status !== 'ScheduleApplies' ? 'waiting' : ''}`} role="img"
           aria-label={slots.map((s) => `${hhmm(s.start)}–${hhmm(s.end)}`).join(', ') || describeDay(day)}>
        {slots.map((s) => (
          <span key={s.start} class={`outage-slot ${s.type === 'Possible' ? 'possible' : ''}`}
                style={{ left: `${(s.start / 1440) * 100}%`, width: `${((s.end - s.start) / 1440) * 100}%` }}
                title={`${hhmm(s.start)}–${hhmm(s.end)}`} />
        ))}
        {nowMin != null && <span class="outage-now" style={{ left: `${(nowMin / 1440) * 100}%` }} />}
      </div>
      {slots.length > 0 && <div class="outage-times">{slots.map(slotText).join(' · ')}</div>}
    </div>
  );
}

/** Overview: the next planned outage, whether the battery will last through it, and today/tomorrow at a glance. */
export function OutageCard({ o, error, now }: { o: OutagePayload | null; error: string | null; now: number }) {
  if (!o) return <Card title="Planned outages">{error ? <div class="setting-hint">{t(error)}</div> : <Skeleton h={160} />}</Card>;
  if (!o.configured) {
    return (
      <Card title="Planned outages">
        <div class="setting-hint">{t('Show the planned outage schedule for your address here, and whether the battery will last.')}</div>
        <button type="button" class="link" onClick={() => goTo('settings', { tab: 'general' })}>{t('Set up')} →</button>
      </Card>
    );
  }
  const d = new Date(now);
  const nowMin = d.getHours() * 60 + d.getMinutes();
  const n = o.next;
  const v = o.battery && n ? verdictText(o.battery, dur) : null;
  const aside: ComponentChildren = <span class="muted outage-group">{t('Group {g}', { g: o.group ?? '--' })}</span>;
  return (
    <Card title="Planned outages" aside={aside} class="outage-card">
      <div class={`now-row ${n?.ongoing ? 'bad' : n ? 'warn' : 'ok'}`} data-testid="outage-next">
        <span class="now-dot" />
        <div>
          <div class="now-title">
            {n?.ongoing ? t('Planned outage now, until {end}', { end: time(n.end) })
              : n ? t('Next: {start}–{end} ({d})', { start: time(n.start), end: time(n.end), d: dur(n.duration_s) })
                : t('No outages planned today or tomorrow')}
          </div>
          {n && !n.ongoing && <div class="setting-hint">{t('in {d}', { d: dur(n.in_s) })}</div>}
        </div>
      </div>
      {v && <div class={`update-result tone-${v.tone} outage-verdict`} data-testid="outage-verdict">{v.text}</div>}
      {o.days[0] && <DayBar label={t('Today')} day={o.days[0]} nowMin={nowMin} />}
      {o.days[1] && <DayBar label={t('Tomorrow')} day={o.days[1]} nowMin={null} />}
      <div class="setting-hint outage-foot">
        {!o.fetched_at ? (o.error ? t(o.error) : t('Fetching the schedule…'))
          : o.stale ? t('Could not reach {source}; showing the schedule from {time}.', { source: o.source ?? 'Yasno', time: time(o.fetched_at) })
          : t('{source} · changed by the operator {time}', { source: o.source ?? '', time: o.updated_on ? time(o.updated_on) : '--' })}
      </div>
    </Card>
  );
}
