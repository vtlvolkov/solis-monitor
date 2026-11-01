import { Stat } from '../charts/widgets';
import type { HistoryPayload } from '../lib/api';
import { duration, seriesStats, spansInDay, type DayEnergy } from '../lib/day';
import { kwh, power } from '../lib/format';
import { t, tp, tx } from '../lib/i18n';
import type { EventsPayload } from '../lib/poll';

const num = (v: number | string | null | undefined) => (typeof v === 'number' ? v : null);

/** Totals of one exact day, in place of the live tiles. */
export function DayStats({ energy, hist, events, now }: { energy: DayEnergy | null; hist: HistoryPayload | null; events: EventsPayload | null; now: number }) {
  const pv = seriesStats(hist?.series.pv_power);
  const load = seriesStats(hist?.series.total_load_power);
  const soc = seriesStats(hist?.series.battery_soc);
  const outages = events?.outages.length ?? null;
  const alarms = events?.alarms.length ?? 0;
  const offGrid = events?.start != null && events.stop != null
    ? spansInDay(events.outages, events.start, events.stop, now / 1000).reduce((a, [x, y]) => a + y - x, 0) : null;
  return (
    <div class="stat-row" data-testid="day-stats">
      <Stat testId="day-pv" title={tx('PV generated')} value={kwh(num(energy?.pv_energy_today))} color="var(--c-pv)"
            sub={pv ? t('peak {value}', { value: power(pv.max) }) : ''} />
      <Stat testId="day-load" title={tx('Load')} value={kwh(num(energy?.load_consumption_today))}
            sub={load ? t('peak {value}', { value: power(load.max) }) : ''} />
      <Stat testId="day-import" title={tx('Grid import')} value={kwh(num(energy?.grid_import_today))} color="var(--c-grid)"
            sub={t('export {value}', { value: kwh(num(energy?.grid_export_today)) })} />
      <Stat testId="day-charge" title={tx('Battery charged')} value={kwh(num(energy?.battery_charge_today))} color="var(--c-battery)"
            sub={t('discharged {value}', { value: kwh(num(energy?.battery_discharge_today)) })} />
      <Stat testId="day-soc" title={tx('Battery SOC')} value={soc ? `${Math.round(soc.min)}–${Math.round(soc.max)} %` : '--'} color="var(--c-battery)"
            sub={soc ? t('end of day {value}', { value: `${Math.round(soc.last)} %` }) : ''} />
      <Stat testId="day-outages" title={tx('Outages')} value={outages == null ? '--' : String(outages)} color={outages ? 'var(--c-grid)' : undefined}
            sub={events ? `${outages ? `${duration(Math.round(offGrid ?? 0))} · ` : ''}${alarms} ${tp(alarms, 'alarm|alarms')}` : ''} />
    </div>
  );
}
