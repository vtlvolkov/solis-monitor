import { useCallback, useEffect, useState } from 'preact/hooks';
import { CostBars } from '../charts/CostBars';
import { Card, ErrorBanner, Header, Skeleton } from '../components/common';
import {
  isDay, isMonth, money, monthOfDay, monthsBetween, shiftMonth, type CostDay, type CostHistoryPayload, type Metric,
} from '../lib/cost';
import { kwh } from '../lib/format';
import { getLang, t, unit } from '../lib/i18n';
import { describeTariff, formatMoment } from '../lib/tariff';
import type { LiveState } from '../lib/live';
import { goTo } from '../lib/nav';
import { useFetch } from './Charts';

const loc = () => (getLang() === 'uk' ? 'uk-UA' : 'en-US');
const monthLabel = (m: string) =>
  new Date(Number(m.slice(0, 4)), Number(m.slice(5)) - 1, 1).toLocaleDateString(loc(), { month: 'long', year: 'numeric' });
const monthName = (m: string) => new Date(Number(m.slice(0, 4)), Number(m.slice(5)) - 1, 1).toLocaleDateString(loc(), { month: 'long' });
const dayLabel = (d: string) => new Date(`${d}T12:00:00`).toLocaleDateString(loc(), { weekday: 'short', day: 'numeric', month: 'short' });
const dayLong = (d: string) => new Date(`${d}T12:00:00`).toLocaleDateString(loc(), { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });

/** URL state: ?view=cost&month=2026-10&day=2026-10-18 */
function readUrl(): { month: string | null; day: string | null } {
  const p = new URLSearchParams(location.search);
  const day = p.get('day'), month = p.get('month');
  return { month: isMonth(month) ? month : isDay(day) ? monthOfDay(day) : null, day: isDay(day) ? day : null };
}

export function CostHistory({ live, now }: { live: LiveState; now: number }) {
  const initial = readUrl();
  const [month, setMonth] = useState<string | null>(initial.month);       // null = the current month, as the server sees it
  const [day, setDay] = useState<string | null>(initial.day);
  const [metric, setMetric] = useState<Metric>('cost');
  const { data, error, loading } = useFetch<CostHistoryPayload>(`/api/cost/history${month ? `?month=${month}` : ''}`, 300_000);

  const shown = data?.month ?? month;
  useEffect(() => {                                                          // keep the URL shareable, without growing the history
    if (!shown) return;
    const p = new URLSearchParams(location.search);
    p.set('view', 'cost'); p.set('month', shown);
    if (day && monthOfDay(day) === shown) p.set('day', day); else p.delete('day');
    history.replaceState(null, '', `?${p}`);
  }, [shown, day]);

  const pick = useCallback((m: string) => { setMonth(m); setDay(null); }, []);
  const toggleDay = (d: string) => setDay((cur) => (cur === d ? null : d));

  const cfg = data?.configured !== false;
  const current = data?.current_month ?? shown ?? '';
  const months = data?.configured ? monthsBetween(data.first_day, current) : [];
  const years = [...new Set(months.map((m) => m.slice(0, 4)))];
  const cur = data?.tariff.currency ?? '';
  const days: CostDay[] = data?.days ?? [];
  const rows = days.filter((d) => !data?.first_day || d.date >= data.first_day);     // no rows for days before the first reading ever
  const total = data?.total;
  const selected = day ? days.find((d) => d.date === day) ?? null : null;

  return (
    <div class="page cost-history">
      <Header live={live} now={now} title="Electricity cost" />
      <div class="cost-nav">
        <button type="button" class="link" onClick={() => goTo('overview')} data-testid="cost-back">← {t('Overview')}</button>
        {data?.configured && shown && (
          <div class="month-picker" role="group" aria-label={t('Month')}>
            <button type="button" class="btn btn-small" disabled={months.indexOf(shown) <= 0} onClick={() => pick(shiftMonth(shown, -1))}
                    aria-label={t('Previous month')}>‹</button>
            <select value={shown.slice(0, 4)} aria-label={t('Year')} data-testid="cost-year"
                    onChange={(e) => {
                      const y = (e.target as HTMLSelectElement).value;
                      const same = months.filter((m) => m.startsWith(y));
                      pick(same.includes(`${y}-${shown.slice(5)}`) ? `${y}-${shown.slice(5)}` : same[same.length - 1]);
                    }}>
              {years.map((y) => <option key={y} value={y}>{y}</option>)}
            </select>
            <select value={shown} aria-label={t('Month')} data-testid="cost-month"
                    onChange={(e) => pick((e.target as HTMLSelectElement).value)}>
              {months.filter((m) => m.startsWith(shown.slice(0, 4))).map((m) => <option key={m} value={m}>{monthName(m)}</option>)}
            </select>
            <button type="button" class="btn btn-small" disabled={shown >= current} onClick={() => pick(shiftMonth(shown, 1))}
                    aria-label={t('Next month')}>›</button>
          </div>
        )}
      </div>
      {error && <ErrorBanner message={error} stale={!!data} />}

      {data && !cfg && (
        <Card title="Electricity cost">
          <div class="setting-hint">{t('Add your tariff to')} <code>.env</code> {t('to see costs:')} <code>TARIFF_DAY</code>.</div>
        </Card>
      )}

      {cfg && (
        <>
          <div class="cost-summary">
            <Card title={shown ? monthLabel(shown) : t('Month')}>
              {!total ? <Skeleton h={90} /> : (
                <div class="cost-grid" data-testid="cost-total">
                  <div><span class="cost-label">{t('Cost')}</span><span class="cost-value">{money(total.cost, cur)}</span>
                    <span class="cost-sub">{t('avg {v} per day', { v: money(total.avg_cost, cur) })}</span></div>
                  <div><span class="cost-label">{t('Grid import')}</span><span class="cost-value">{kwh(total.kwh)}</span>
                    <span class="cost-sub">{t('{n} days with data', { n: total.days })}</span></div>
                  <div><span class="cost-label">{t('Day zone')}</span><span class="cost-value">{kwh(total.day_kwh)}</span>
                    <span class="cost-sub">{money(total.day_cost, cur)}</span></div>
                  <div><span class="cost-label">{t('Night zone')}</span><span class="cost-value">{kwh(total.night_kwh)}</span>
                    <span class="cost-sub">{money(total.night_cost, cur)}{total.saved > 0 ? ` · ${t('saved {v}', { v: money(total.saved, cur) })}` : ''}</span></div>
                </div>
              )}
              {data?.tariff_changes && data.tariff_changes.length > 0 && (
                <div class="setting-hint" data-testid="cost-prices">
                  <div>{data.tariff_changes.length > 1 ? t('Prices in this month') : t('Prices used')}:</div>
                  <ul class="price-list">
                    {data.tariff_changes.map((v) => (
                      <li key={v.from ?? 'start'}>
                        {data.tariff_changes!.length > 1 && <time>{v.from ? t('from {time}', { time: formatMoment(v.from, data.tz ?? 'UTC') }) : t('from the start of the month')}</time>}
                        {describeTariff({ day: v.day ?? 0, night: v.night, night_start: v.night_start, night_end: v.night_end }, cur)}
                      </li>
                    ))}
                  </ul>
                  {t('Each day is priced with the prices that were in force at the time.')}
                </div>
              )}
            </Card>

            <Card title="Day detail" aside={selected && <button type="button" class="link" onClick={() => setDay(null)}>{t('Clear')}</button>}>
              {!data ? <Skeleton h={90} /> : !selected ? (
                <div class="setting-hint" data-testid="cost-day-hint">{t('Select a day in the chart or the table to see its details.')}</div>
              ) : !selected.has_data ? (
                <div><strong>{dayLong(selected.date)}</strong><div class="setting-hint">{t('No readings were recorded on this day.')}</div></div>
              ) : (
                <div data-testid="cost-day-detail">
                  <strong>{dayLong(selected.date)}</strong>
                  <dl class="kv kv-compact">
                    <div><dt>{t('Day zone')}</dt><dd>{kwh(selected.day_kwh)} · {money(selected.day_cost, cur)}</dd></div>
                    <div><dt>{t('Night zone')}</dt><dd>{kwh(selected.night_kwh)} · {money(selected.night_cost, cur)}</dd></div>
                    <div><dt>{t('Total from the grid')}</dt><dd><strong>{kwh(selected.kwh)} · {money(selected.cost, cur)}</strong></dd></div>
                    {(selected.saved ?? 0) > 0 && <div><dt>{t('Saved by the night rate')}</dt><dd>{money(selected.saved, cur)}</dd></div>}
                    <div><dt>{t('House consumption')}</dt><dd>{kwh(selected.load_kwh)}</dd></div>
                    <div><dt>{t('Exported to the grid')}</dt><dd>{kwh(selected.export_kwh)}</dd></div>
                    <div><dt>PV</dt><dd>{kwh(selected.pv_kwh)}</dd></div>
                    <div><dt>{t('Battery charged / discharged')}</dt><dd>{kwh(selected.charge_kwh)} / {kwh(selected.discharge_kwh)}</dd></div>
                  </dl>
                </div>
              )}
            </Card>
          </div>

          <Card title="Daily grid import" aside={
            <div class="seg seg-small" role="group" aria-label={t('Show')}>
              <button type="button" class={metric === 'cost' ? 'on' : ''} aria-pressed={metric === 'cost'} onClick={() => setMetric('cost')}>{t('Cost')}</button>
              <button type="button" class={metric === 'kwh' ? 'on' : ''} aria-pressed={metric === 'kwh'} onClick={() => setMetric('kwh')}>{t('Energy')}</button>
            </div>}>
            {!data ? <Skeleton h={220} /> : (
              <>
                <div class="cost-legend"><span><i class="swatch cost-day" />{t('Day zone')}</span><span><i class="swatch cost-night" />{t('Night zone')}</span></div>
                <CostBars days={days} metric={metric} selected={day} onSelect={toggleDay} currency={cur} />
                {total && total.days === 0 && <div class="setting-hint">{t('No readings in this month.')}</div>}
              </>
            )}
          </Card>

          <Card title="Days">
            {!data ? <Skeleton h={200} /> : (
              <div class="table-scroll">
                <table class="cost-table" data-testid="cost-table">
                  <thead>
                    <tr><th>{t('Date')}</th><th class="opt">{t('Day zone')}, {unit('kWh')}</th><th class="opt">{t('Night zone')}, {unit('kWh')}</th><th>{t('Total')}, {unit('kWh')}</th>
                      <th class="opt">{t('Day zone')}, {cur}</th><th class="opt">{t('Night zone')}, {cur}</th><th>{t('Total')}, {cur}</th></tr>
                  </thead>
                  <tbody>
                    {rows.slice().reverse().map((d) => (
                      <tr key={d.date} class={`${day === d.date ? 'on' : ''} ${d.has_data ? '' : 'muted'}`} onClick={() => toggleDay(d.date)} data-testid={`cost-row-${d.date}`}>
                        <td>{dayLabel(d.date)}</td>
                        {d.has_data ? (
                          <>
                            <td class="opt">{d.day_kwh?.toFixed(2)}</td><td class="opt">{d.night_kwh?.toFixed(2)}</td><td><strong>{d.kwh?.toFixed(2)}</strong></td>
                            <td class="opt">{d.day_cost?.toFixed(2)}</td><td class="opt">{d.night_cost?.toFixed(2)}</td><td><strong>{d.cost?.toFixed(2)}</strong></td>
                          </>
                        ) : <td colSpan={6}>{t('no data')}</td>}
                      </tr>
                    ))}
                  </tbody>
                  {total && total.days > 0 && (
                    <tfoot>
                      <tr><th>{t('Month total')}</th><th class="opt">{total.day_kwh.toFixed(2)}</th><th class="opt">{total.night_kwh.toFixed(2)}</th><th>{total.kwh.toFixed(2)}</th>
                        <th class="opt">{total.day_cost.toFixed(2)}</th><th class="opt">{total.night_cost.toFixed(2)}</th><th>{total.cost.toFixed(2)}</th></tr>
                    </tfoot>
                  )}
                </table>
              </div>
            )}
          </Card>
          {loading && <div class="setting-hint" aria-live="polite">{t('Loading')}…</div>}
        </>
      )}
    </div>
  );
}
