import { spansInDay } from '../lib/day';
import { t } from '../lib/i18n';
import type { EventItem } from '../lib/poll';

const HOURS = ['00:00', '06:00', '12:00', '18:00', '24:00'];

/** One bar for the day: when the grid was out and when alarms were active. */
export function DayTimeline({ outages, alarms, start, stop, now }: { outages: EventItem[]; alarms: EventItem[]; start: number; stop: number; now: number }) {
  const pos = ([a, b]: [number, number]) => ({ left: `${((a - start) / (stop - start)) * 100}%`, width: `${Math.max(0.3, ((b - a) / (stop - start)) * 100)}%` });
  return (
    <div class="day-timeline" data-testid="day-timeline">
      <div class="day-bar" role="img" aria-label={t('Grid outage')}>
        {spansInDay(outages, start, stop, now).map((s) => <i key={s[0]} class="outage" style={pos(s)} />)}
        {spansInDay(alarms, start, stop, now).map((s) => <i key={s[0]} class="alarm" style={pos(s)} />)}
      </div>
      <div class="day-hours">{HOURS.map((h) => <span key={h}>{h}</span>)}</div>
      <div class="day-pop-foot day-legend">
        <span><i class="ok" />{t('Grid connected')}</span>
        <span><i class="outage" />{t('Grid outage')}</span>
        <span><i class="alarm" />{t('alarm')}</span>
      </div>
    </div>
  );
}
