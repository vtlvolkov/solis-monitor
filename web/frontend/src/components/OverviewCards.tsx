import type { Fields, HistoryPayload } from '../lib/api';
import type { DeviceInfo } from '../lib/devices';
import { ago, power, withUnit } from '../lib/format';
import { locale, t, tp, unit } from '../lib/i18n';
import type { EventItem, EventsPayload } from '../lib/poll';
import { formatHours, gridLost } from '../lib/runtime';
import { activeSlot, enabledSlots, minuteOf, minutesLeft, nextSlot } from '../lib/schedule';
import type { SettingsPayload } from '../lib/settings';
import { goTo } from '../lib/nav';
import { zoneRows, type CostPeriod, type RateNow } from '../lib/cost';
import { Card, Skeleton } from './common';

const go = (view: string) => () => goTo(view);

const range = (vals: (number | null)[] | undefined) => {
  const v = (vals ?? []).filter((x): x is number => x != null);
  return v.length ? [Math.min(...v), Math.max(...v)] as const : null;
};

const dur = (s: number | null) => (s == null ? '—' : s < 3600 ? t('{n} min', { n: Math.round(s / 60) }) : formatHours(s / 3600));

// ---------------------------------------------------------------- 5. status chips
export function StatusChips({ f, info, lost }: { f: Fields | null; info: DeviceInfo | null; lost: boolean }) {
  const alarms = info?.inverter.alarms ?? [];
  const temp = f?.inverter_temperature;
  const tempCls = temp == null ? '' : temp >= 70 ? 'bad' : temp >= 55 ? 'warn' : '';
  return (
    <div class="chips-row" data-testid="status-chips">
      <span class={`chip-s ${lost ? 'bad' : 'ok'}`}>{lost ? t('Grid down') : t('Grid OK')}</span>
      {info && <span class={`chip-s ${info.inverter.has_fault ? 'bad' : ''}`}>{t(info.inverter.status)}</span>}
      <span class={`chip-s ${tempCls}`}>{withUnit(temp, '°C', 1)}</span>
      <button type="button" class={`chip-s ${alarms.length ? 'bad' : 'ok'}`} onClick={go('events')}>
        {alarms.length ? `${alarms.length} ${tp(alarms.length, 'alarm|alarms')}` : t('No alarms')}
      </button>
    </div>
  );
}

// ---------------------------------------------------------------- 1. grid & backup
export function GridCard({ f, events, vmin, vmax }: {
  f: Fields | null; events: EventsPayload | null; vmin: HistoryPayload | null; vmax: HistoryPayload | null;
}) {
  const lost = gridLost(f) || !!events?.outage_now;
  const lo = range(vmin?.series.grid_voltage)?.[0];
  const hi = range(vmax?.series.grid_voltage)?.[1];
  const outages = events?.outages ?? [];
  const last: EventItem | undefined = outages[0];
  const total = outages.reduce((a, o) => a + (o.duration_s ?? 0), 0);
  return (
    <Card title="Grid & backup" aside={<button type="button" class="link" onClick={go('events')}>{t('History')}</button>}>
      <div class={`now-row ${lost ? 'bad' : 'ok'}`} data-testid="grid-card-state">
        <span class="now-dot" />
        <div>
          <div class="now-title">{lost ? t('Grid outage') : t('Grid connected')}</div>
          <div class="setting-hint">
            {last
              ? last.ongoing
                ? t('Since {time}', { time: new Date(last.start!).toLocaleTimeString(locale(), { hour: '2-digit', minute: '2-digit' }) })
                : t('Last outage {ago} ago, lasted {dur}', { ago: ago((Date.now() - Date.parse(last.end ?? last.start!)) / 1000), dur: dur(last.duration_s) })
              : events ? t('No outages recorded in the last 7 days') : '…'}
          </div>
        </div>
      </div>
      <dl class="kv kv-compact">
        <div><dt>{t('Grid voltage')}</dt><dd>{withUnit(f?.grid_voltage, 'V', 1)}{lo != null && hi != null && <span class="muted"> · {t('24 h {lo}–{hi} V', { lo: lo.toFixed(0), hi: hi.toFixed(0) })}</span>}</dd></div>
        <div><dt>{t('Backup output')}</dt><dd>{withUnit(f?.backup_voltage, 'V', 1)} · {power(f?.backup_load_power)}</dd></div>
        <div><dt>{t('Outages, 7 days')}</dt><dd>{events ? `${outages.length}${outages.length ? ` · ${t('{dur} total', { dur: dur(total) })}` : ''}` : '…'}</dd></div>
      </dl>
    </Card>
  );
}

// ---------------------------------------------------------------- 2. schedule
export function ScheduleCard({ s, error }: { s: SettingsPayload | null; error: string | null }) {
  if (!s) return <Card title="Schedule">{error ? <div class="setting-hint">{t(error)}</div> : <Skeleton h={90} />}</Card>;
  const minute = minuteOf(s.control.server_time);
  const slots = enabledSlots(s.tou);
  const act = activeSlot(slots, minute);
  const next = nextSlot(slots.filter((x) => x !== act), minute);
  const verb = (k: string) => (k === 'charge' ? t('Charging') : t('Discharging'));
  const left = act ? minutesLeft(act, minute) : 0;
  return (
    <Card title="Schedule" aside={<button type="button" class="link" onClick={go('settings')}>{t('Edit')}</button>}>
      <div class={`now-row ${act ? (act.kind === 'charge' ? 'charge' : 'discharge') : 'idle'}`} data-testid="schedule-now">
        <span class="now-dot" />
        <div>
          <div class="now-title">
            {act ? t('{verb} from grid schedule', { verb: verb(act.kind) }) : slots.length ? t('No slot active now') : t('No time slots enabled')}
          </div>
          <div class="setting-hint">
            {act
              ? t('Slot {n} {start}–{end} · {dir} {soc}% at ≤ {a} {au} · {left} left', {
                  n: act.slot, start: act.start, end: act.end, dir: act.kind === 'charge' ? t('up to') : t('down to'),
                  soc: act.soc, a: act.current_a, au: unit('A'), left: formatHours(left / 60) })
              : next
                ? t('Next: {kind} {start}–{end} (in {in})', { kind: t(next.slot.kind), start: next.slot.start, end: next.slot.end, in: formatHours(next.inMin / 60) })
                : t('The battery is not charged from the grid on a schedule')}
          </div>
          {act && next && <div class="setting-hint">{t('Then: {kind} {start}–{end}', { kind: t(next.slot.kind), start: next.slot.start, end: next.slot.end })}</div>}
        </div>
      </div>
      <dl class="kv kv-compact">
        <div><dt>{t('Grid charging')}</dt><dd>{s.grid_charging ? t('Allowed') : t('Forbidden')}</dd></div>
        <div><dt>{t('Reserve mode')}</dt><dd>{s.reserve_mode ? t('On · keeps {n}%', { n: s.battery.backup_soc }) : t('Off')}</dd></div>
        <div><dt>{t('Discharge stops at')}</dt><dd>{s.battery.overdischarge_soc}%</dd></div>
      </dl>
    </Card>
  );
}

// ---------------------------------------------------------------- 4. cost
export interface CostPayload {
  configured: boolean;
  tariff: { currency: string; day: number | null; night: number | null; night_start: string | null; night_end: string | null };
  today?: CostPeriod;
  month?: CostPeriod;
  now?: RateNow | null;
}

export function CostCard({ c }: { c: CostPayload | null }) {
  if (!c) return <Card title="Electricity cost"><Skeleton h={80} /></Card>;
  if (!c.configured) {
    return (
      <Card title="Electricity cost">
        <div class="setting-hint" data-testid="cost-unconfigured">
          {t('Add your tariff to')} <code>.env</code> {t('to see costs:')} <code>TARIFF_DAY</code> ({t('price per kWh')}), {t('and for a two-zone meter also')}{' '}
          <code>TARIFF_NIGHT</code>, <code>TARIFF_NIGHT_START</code>, <code>TARIFF_NIGHT_END</code>.
          {' '}{t('Then run')} <code>docker compose up -d web</code>.
        </div>
      </Card>
    );
  }
  const cur = c.tariff.currency;
  const money = (v: number | undefined) => (v == null ? '--' : `${v.toFixed(2)} ${cur}`);
  const tf = c.tariff;
  return (
    <Card title="Electricity cost" aside={<span class="card-aside"><span class="muted small">{tf.day} {cur}/{unit('kWh')}{tf.night != null ? ` · ${t('night')} ${tf.night}` : ''}</span>
      <button type="button" class="link" onClick={go('cost')} data-testid="cost-history-link">{t('History')}</button></span>}>
      <div class="cost-grid" data-testid="cost">
        <div><span class="cost-label">{t('Today')}</span><span class="cost-value">{money(c.today?.cost)}</span><span class="cost-sub">{t('{kwh} kWh imported', { kwh: c.today?.kwh.toFixed(1) ?? '--' })}</span></div>
        <div><span class="cost-label">{t('This month')}</span><span class="cost-value">{money(c.month?.cost)}</span><span class="cost-sub">{t('{kwh} kWh imported', { kwh: c.month?.kwh.toFixed(1) ?? '--' })}</span></div>
      </div>
      {tf.night != null && c.month && (
        <>
        {c.now && (
          <div class={`rate-now ${c.now.zone}`} data-testid="rate-now">
            <span class="now-dot" />
            <strong>{c.now.zone === 'night' ? t('Night rate now') : t('Day rate now')}</strong>
            <span>{t('{price} {cur}/{u} · until {time}', { price: c.now.price, cur, u: unit('kWh'), time: c.now.until })}</span>
          </div>
        )}
        <table class="zone-table" data-testid="zone-table">
          <thead><tr><th /><th>{t('Today')}</th><th>{t('This month')}</th></tr></thead>
          <tbody>
            {zoneRows(tf, c.today, c.month, c.now).map((r) => (
              <tr key={r.zone} class={r.active ? 'now' : ''} data-testid={`zone-${r.zone}`}>
                <th><i class={`zdot ${r.zone}`} />{r.zone === 'day' ? t('Day zone') : t('Night zone')}<small>{r.from}–{r.to}</small></th>
                <td>{r.today.kwh.toFixed(1)} {unit('kWh')}<small>{money(r.today.cost)}</small></td>
                <td>{r.month.kwh.toFixed(1)} {unit('kWh')}<small>{money(r.month.cost)}</small></td>
              </tr>
            ))}
          </tbody>
        </table>
        <div class="setting-hint">
          {t('Night rate {start}–{end}: {kwh} kWh this month, saving {saved}.', {
            start: tf.night_start ?? '', end: tf.night_end ?? '', kwh: c.month.night_kwh.toFixed(1), saved: money(c.month.saved) })}
        </div>
        </>
      )}
    </Card>
  );
}
