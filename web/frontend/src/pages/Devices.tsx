import type { ComponentChildren } from 'preact';
import { useState } from 'preact/hooks';
import { usePoll } from '../lib/poll';
import { Card, ErrorBanner, Header, Skeleton } from '../components/common';
import { DeviceArt } from '../components/DeviceArt';
import type { Fields } from '../lib/api';
import { describeSource } from '../lib/capacity';
import { clockDrift, DEVICES, deviceStatus, type DeviceId, type DeviceInfo, type DeviceStatus } from '../lib/devices';
import { ago, clockTime, kwh, power, withUnit } from '../lib/format';
import { t, unit } from '../lib/i18n';
import { currentAge, isOnline, type LiveState } from '../lib/live';

const REFRESH_MS = 30_000;   // the poller refreshes device details every 60 s in the background

function useDeviceInfo() {
  const { data, error } = usePoll<DeviceInfo>('/api/devices', REFRESH_MS);
  return { info: data, error };
}

const STATUS_LABEL: Record<DeviceStatus, string> = { online: 'Online', offline: 'Offline', fault: 'Fault' };   // keys, see i18n.uk.ts
const StatusPill = ({ s }: { s: DeviceStatus }) => (
  <span class={`pill ${s === 'online' ? 'pill-ok' : s === 'fault' ? 'pill-warn' : 'pill-bad'}`} data-testid="device-status">
    {t(STATUS_LABEL[s])}
  </span>
);

function subtitle(id: DeviceId, info: DeviceInfo | null, f: Fields): string {
  switch (id) {
    case 'inverter': return info?.inverter.serial ? `SN:${info.inverter.serial}` : 'S6-EH1P hybrid';
    case 'battery': return t('48 V · SOC {n}', { n: f.battery_soc == null ? '--' : Math.round(f.battery_soc) + '%' });
    case 'logger': return info ? `${info.logger.host}:${info.logger.port}` : t('Modbus TCP link');
    case 'meter': return `${t('Grid meter')} · ${withUnit(info?.meter.voltage ?? f.grid_voltage, 'V', 1)}`;
  }
}

type Row = [label: string, value: ComponentChildren];

function Rows({ rows }: { rows: Row[] }) {
  return (
    <dl class="kv">
      {rows.map(([k, v]) => <div key={k}><dt>{t(k)}</dt><dd>{v}</dd></div>)}
    </dl>
  );
}

const codes = (list: string[]) => (list.every((c) => c === '0x0000') ? t('None') : list.filter((c) => c !== '0x0000').join(', '));

function detailSections(id: DeviceId, info: DeviceInfo | null, f: Fields, live: LiveState, now: number): [string, Row[] | null][] {
  const i = info;
  switch (id) {
    case 'inverter': {
      const drift = i ? clockDrift(i.inverter.clock, new Date(i.read_at)) : null;   // at read time, not render time
      return [
        ['Identity', i && [
          ['Serial number', i.inverter.serial ?? '--'],
          ['Model code', i.inverter.model_code],
          ['DSP firmware', i.inverter.dsp_version],
          ['HMI firmware', i.inverter.hmi_version],
        ]],
        ['Status', i && [
          ['State', t(i.inverter.status)],
          ['Alarms', i.inverter.alarms.length
            ? <span class="warn-text">{i.inverter.alarms.map((a) => t(a.name)).join(', ')}</span> : t('None')],
          ['Fault registers', <span class={i.inverter.has_fault ? 'warn-text' : 'muted'}>{codes(i.inverter.fault_codes)}</span>],
          ['Inverter clock', i.inverter.clock
            ? <>{i.inverter.clock.replace('T', ' ')}{drift != null && <span class="muted"> · {Math.abs(drift) < 120 ? t('in sync') : t('{sign}{ago} off', { sign: drift > 0 ? '+' : '−', ago: ago(Math.abs(drift)) })}</span>}</>
            : '--'],
          ['DC bus voltage', withUnit(i.inverter.dc_bus_voltage, 'V', 1)],
          ['Active power limit', withUnit(i.inverter.power_limit_pct, '%', 0)],
          ['Grid standard code', String(i.inverter.grid_standard_code)],
        ]],
        ['Backup output (EPS)', i && [
          ['Voltage', withUnit(i.backup.voltage, 'V', 1)],
          ['Current', withUnit(i.backup.current, 'A', 1)],
          ['Load', power(f.backup_load_power)],
          ['Discharge floor on backup', t('{n}% SOC', { n: i.backup.eps_dod_pct })],
          ['Off-grid floor', t('{n}% SOC', { n: i.backup.offgrid_dod_pct })],
          ['Switch-over time', t('{n} ms (as reported)', { n: i.backup.switch_time_ms })],
        ]],
        ['Live', [
          ['AC power', power(f.inverter_ac_power)],
          ['Temperature', withUnit(f.inverter_temperature, '°C', 1)],
          ['Grid voltage', withUnit(f.grid_voltage, 'V', 1)],
          ['Grid frequency', withUnit(f.grid_frequency, 'Hz', 2)],
          ['PV power', power(f.pv_power)],
          ['PV1', `${withUnit(f.pv1_voltage, 'V', 1)} · ${withUnit(f.pv1_current, 'A', 1)}`],
          ['PV2', `${withUnit(f.pv2_voltage, 'V', 1)} · ${withUnit(f.pv2_current, 'A', 1)}`],
          ['PV today', kwh(f.pv_energy_today)],
          ['PV total', kwh(f.pv_energy_total, 0)],
        ]],
      ];
    }
    case 'battery': {
      const p = f.battery_power_signed;
      const state = p == null ? '--' : p >= 20 ? t('Charging') : p <= -20 ? t('Discharging') : t('Idle');
      return [
        ['Live', [
          ['State', state],
          ['State of charge', withUnit(f.battery_soc, '%', 0)],
          ['State of health', withUnit(f.battery_soh, '%', 0)],
          ['Power', power(p, true)],
          ['Voltage', withUnit(f.battery_voltage, 'V', 1)],
          ['Current', withUnit(f.battery_current, 'A', 1)],
          ['Charged today', kwh(f.battery_charge_today)],
          ['Discharged today', kwh(f.battery_discharge_today)],
        ]],
        ['Battery', i && [
          ['Model', t(i.battery.model)],
          ['Capacity', i.battery.capacity_kwh != null
            ? <>{i.battery.capacity_kwh} {unit('kWh')} <span class="muted">({describeSource({ source: i.battery.capacity_source, rated_capacity_ah: i.battery.rated_capacity_ah })})</span></> : '--'],
          ['Max charge SOC', withUnit(i.battery.max_charge_soc, '%', 0)],
          ['Over-discharge SOC', withUnit(i.battery.overdischarge_soc, '%', 0)],
          ['Force-charge SOC', withUnit(i.battery.force_charge_soc, '%', 0)],
          ['Backup (reserve) SOC', withUnit(i.battery.backup_soc, '%', 0)],
        ]],
        ['BMS', i && [
          ['Communication', i.battery.connected ? t('Connected') : <span class="warn-text">{t('No BMS data')}</span>],
          ['BMS voltage', withUnit(i.battery.bms_voltage, 'V', 2)],
          ['BMS current', withUnit(i.battery.bms_current, 'A', 1)],
          ['Temperature', withUnit(i.battery.bms_temperature, '°C', 1)],
          ['Charge current limit', withUnit(i.battery.bms_charge_limit_a, 'A', 1)],
          ['Discharge current limit', withUnit(i.battery.bms_discharge_limit_a, 'A', 1)],
          ['Failure codes', <span class={i.battery.has_fault ? 'warn-text' : ''}>{codes(i.battery.bms_failure_codes)}</span>],
        ]],
      ];
    }
    case 'meter': {
      const m = i?.meter;
      return [
        ['Live', [
          ['Grid power', <>{power(f.grid_power, true)} <span class="muted">{(f.grid_power ?? 0) >= 20 ? t('importing') : (f.grid_power ?? 0) <= -20 ? t('exporting') : ''}</span></>],
          ['Imported today', kwh(f.grid_import_today)],
          ['Exported today', kwh(f.grid_export_today)],
        ]],
        ['Meter', m ? [
          ['Communication', m.connected ? t('Connected') : <span class="warn-text">{t('No meter data')}</span>],
          ['Position / type', `${t(m.location)} · ${t(m.type)}`],
          ['Voltage', withUnit(m.voltage, 'V', 1)],
          ['Current', withUnit(m.current, 'A', 2)],
          ['Active power', power(m.active_power)],
          ['Reactive power', `${m.reactive_power} ${unit('var')}`],
          ['Apparent power', `${m.apparent_power} ${unit('VA')}`],
          ['Power factor', m.power_factor.toFixed(2)],
          ['Frequency', withUnit(m.frequency, 'Hz', 2)],
          ['Total imported', kwh(m.import_total_kwh, 2)],
          ['Total exported', kwh(m.export_total_kwh, 2)],
        ] : null],
      ];
    }
    case 'logger': {
      const age = currentAge(live, now);
      return [
        ['Connection', [
          ['Address', i ? `${i.logger.host}:${i.logger.port}` : '--'],
          ['Modbus unit', i ? String(i.logger.unit) : '--'],
          ['Link', isOnline(live, now) ? t('Receiving data') : <span class="warn-text">{t('No recent data')}</span>],
          ['Last data', live.data?.time ? t('{time} ({ago} ago)', { time: clockTime(live.data.time), ago: ago(age ?? 0) }) : '--'],
        ]],
        ['About', [
          ['Role', t('Wi-Fi/LAN stick between the inverter and this dashboard')],
          ['Serial number', <span class="muted">{t('Not readable over Modbus; see SolisCloud')}</span>],
        ]],
      ];
    }
  }
}

function DeviceDetail({ id, info, infoError, live, now, onBack }: {
  id: DeviceId; info: DeviceInfo | null; infoError: string | null; live: LiveState; now: number; onBack: () => void;
}) {
  const f = live.data?.fields ?? {};
  const status = deviceStatus(id, isOnline(live, now), info);
  const name = t(DEVICES.find((d) => d.id === id)!.name);
  return (
    <div class="page device-detail" data-testid={`device-detail-${id}`}>
      <header class="top detail-top">
        <button type="button" class="back" onClick={onBack} aria-label={t('Back to devices')}>
          <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 5l-7 7 7 7" /></svg>
        </button>
        <h1>{name}</h1>
        <span class="back-spacer" />
      </header>
      {infoError && <ErrorBanner message={infoError} stale={!!info} />}
      <div class="detail-hero">
        <DeviceArt id={id} size={132} />
        <div>
          <StatusPill s={status} />
          <div class="detail-sub">{subtitle(id, info, f)}</div>
        </div>
      </div>
      <div class="detail-grid">
        {detailSections(id, info, f, live, now).map(([title, rows]) => (
          <Card key={title} title={title}>
            {rows ? <Rows rows={rows} /> : infoError ? <div class="setting-hint">{t('Not available right now.')}</div> : <Skeleton h={120} />}
          </Card>
        ))}
      </div>
      {info && <div class="setting-hint detail-foot">{t('Device details read {time}; refreshed every 30 s.', { time: clockTime(info.read_at) })}</div>}
    </div>
  );
}

type Filter = 'all' | DeviceId;

export function Devices({ live, now, device, onOpen }: {
  live: LiveState; now: number; device: DeviceId | null; onOpen: (d: DeviceId | null) => void;
}) {
  const { info, error } = useDeviceInfo();
  const [filter, setFilter] = useState<Filter>('all');
  const online = isOnline(live, now);
  const f = live.data?.fields ?? {};

  if (device) return <DeviceDetail id={device} info={info} infoError={error} live={live} now={now} onBack={() => onOpen(null)} />;

  const shown = DEVICES.filter((d) => filter === 'all' || d.id === filter);
  return (
    <div class="page devices">
      <Header live={live} now={now} title="Devices" />
      <div class="chips" role="group" aria-label={t('Filter devices')}>
        {(['all', ...DEVICES.map((d) => d.id)] as Filter[]).map((c) => (
          <button type="button" key={c} class={c === filter ? 'on' : ''} aria-pressed={c === filter} onClick={() => setFilter(c)}>
            {c === 'all' ? t('All') : t(DEVICES.find((d) => d.id === c)!.name)}
          </button>
        ))}
      </div>
      {error && !live.error && <div class="banner" role="status">{t('{error}. Live values are still shown.', { error: t(error) })}</div>}
      <div class="device-grid">
        {shown.map((d) => (
          <button type="button" key={d.id} class="device-card" onClick={() => onOpen(d.id)} data-testid={`device-${d.id}`}>
            <DeviceArt id={d.id} />
            <span class="device-name-row">
              <StatusPill s={deviceStatus(d.id, online, info)} />
              <span class="device-name">{t(d.name)}</span>
            </span>
            <span class="device-sub">{live.data || info ? subtitle(d.id, info, f) : '…'}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
