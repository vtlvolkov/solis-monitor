import { useEffect, useRef, useState } from 'preact/hooks';
import { isDay, monthOfDay, shiftMonth } from '../lib/cost';
import { calendarCells, dayAllowed, dayLabel, monthTitle, shiftDay, type CalendarPayload } from '../lib/day';
import { locale, t } from '../lib/i18n';
import { usePoll } from '../lib/poll';

/** The exact day being looked at, kept in the URL (?day=2026-10-24); null = the normal time ranges. */
export function useDayParam(): [string | null, (day: string | null) => void] {
  const [day, setDay] = useState<string | null>(() => {
    const d = new URLSearchParams(location.search).get('day');
    return isDay(d) ? d : null;
  });
  useEffect(() => {
    const p = new URLSearchParams(location.search);
    if (day) p.set('day', day); else p.delete('day');
    history.replaceState(null, '', `?${p}`);
  }, [day]);
  return [day, setDay];
}

const WEEK = ['2026-10-05', '2026-10-06', '2026-10-07', '2026-10-08', '2026-10-09', '2026-10-10', '2026-10-11'];   // a Monday to Sunday

/** ‹ Sat, 24 Oct 2026 ▾ › with a month calendar that marks the days with outages and alarms. */
/** `min`: the oldest day this page can show (Grid quality: the 10-second readings kept), on top of the oldest reading. */
export function DayPicker({ day, onChange, min }: { day: string | null; onChange: (day: string) => void; min?: string | null }) {
  const [open, setOpen] = useState(false);
  const [month, setMonth] = useState<string | null>(day && monthOfDay(day));      // null = the server's current month
  const { data } = usePoll<CalendarPayload>(`/api/events/calendar${month ? `?month=${month}` : ''}`, 300_000);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => { if (day) setMonth(monthOfDay(day)); }, [day]);
  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => { if (!box.current?.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', away);
    document.addEventListener('keydown', esc);
    return () => { document.removeEventListener('mousedown', away); document.removeEventListener('keydown', esc); };
  }, [open]);

  const today = data?.today ?? null;
  const first = min && (!data?.first_day || min > data.first_day) ? min : data?.first_day;
  const prev = today && (day ? shiftDay(day, -1) : shiftDay(today, -1));
  const next = today && day ? shiftDay(day, 1) : null;
  const shown = month ?? data?.month ?? null;
  const pick = (d: string) => { onChange(d); setOpen(false); };

  return (
    <div class={`day-picker ${day ? 'on' : ''}`} ref={box} role="group" aria-label={t('Day')}>
      <button type="button" disabled={!prev || !dayAllowed(prev, today!, first)} onClick={() => pick(prev!)}
              aria-label={t('Previous day')} data-testid="day-prev">‹</button>
      <button type="button" class="day-current" aria-expanded={open} onClick={() => { if (!open && day) setMonth(monthOfDay(day)); setOpen(!open); }} data-testid="day-open">
        {day ? dayLabel(day) : t('Pick a day')} <span aria-hidden="true">▾</span>
      </button>
      <button type="button" disabled={!next || !dayAllowed(next, today!, first)} onClick={() => pick(next!)}
              aria-label={t('Next day')} data-testid="day-next">›</button>

      {open && shown && today && (
        <div class="day-pop" data-testid="day-calendar">
          <div class="day-pop-head">
            <button type="button" disabled={!!first && shown <= monthOfDay(first)} onClick={() => setMonth(shiftMonth(shown, -1))} aria-label={t('Previous month')}>‹</button>
            <strong>{monthTitle(shown)}</strong>
            <button type="button" disabled={shown >= monthOfDay(today)} onClick={() => setMonth(shiftMonth(shown, 1))} aria-label={t('Next month')}>›</button>
          </div>
          <div class="day-grid">
            {WEEK.map((d) => <span key={d} class="day-week">{new Date(`${d}T12:00:00`).toLocaleDateString(locale(), { weekday: 'short' })}</span>)}
            {calendarCells(shown).map((d, i) => {
              if (!d) return <span key={`blank-${i}`} />;
              const mark = data?.month === shown ? data.days[d] : undefined;
              return (
                <button type="button" key={d} class={`day-cell ${d === day ? 'sel' : ''}`} disabled={!dayAllowed(d, today, first)}
                        aria-pressed={d === day} onClick={() => pick(d)} data-testid={`day-${d}`}>
                  {Number(d.slice(8))}
                  <span class="day-marks">
                    {mark?.outages ? <i class="outage" title={t('Grid outage')} /> : null}
                    {mark?.alarms ? <i class="alarm" title={t('alarm')} /> : null}
                  </span>
                </button>
              );
            })}
          </div>
          <div class="day-pop-foot">
            <span><i class="outage" />{t('Grid outage')}</span>
            <span><i class="alarm" />{t('alarm')}</span>
            <button type="button" class="link" onClick={() => pick(today)}>{t('Today')}</button>
          </div>
        </div>
      )}
    </div>
  );
}
