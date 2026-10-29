import type { ComponentChildren } from 'preact';
import { ago, clockTime, kwh, socSegments, withUnit } from '../lib/format';
import { currentAge, isOnline, type LiveState } from '../lib/live';
import type { Fields } from '../lib/api';
import { getLang, setLang, t, tx } from '../lib/i18n';
import { useSite } from '../lib/site';
import { UpdateBadge } from './UpdateBadge';

export function StatusPill({ live, now }: { live: LiveState; now: number }) {
  if (!live.data && !live.error) return <span class="pill pill-wait" data-testid="status">{t('Connecting…')}</span>;
  const age = currentAge(live, now);
  if (live.data?.time && isOnline(live, now)) return <span class="pill pill-ok" data-testid="status">{t('Online')}</span>;
  const label = age == null ? t('Offline · no data') : t('Offline · last seen {ago} ago', { ago: ago(age) });
  return <span class="pill pill-bad" data-testid="status">{label}</span>;
}

/** EN / УК toggle: shows the language you would switch to. */
export function LangSwitch({ tabIndex }: { tabIndex?: number }) {
  const next = getLang() === 'uk' ? 'en' : 'uk';
  return (
    <button type="button" class="link lang-switch" tabIndex={tabIndex} data-testid="lang-switch"
            aria-label={next === 'uk' ? 'Українська' : 'English'} onClick={() => setLang(next)}>
      {next === 'uk' ? 'УК' : 'EN'}
    </button>
  );
}

export function Header({ live, now, title }: { live: LiveState; now: number; title?: string }) {
  const site = useSite();
  return (
    <header class="top">
      <h1>{title ? t(title) : site || t('Home')}</h1>
      <div class="top-meta">
        <StatusPill live={live} now={now} />
        <UpdateBadge />
        <span class="updated" data-testid="updated">
          {live.data?.time ? t('Updated {time}', { time: clockTime(live.data.time) }) : ''}
        </span>
        <a class="link" href="?view=tv" data-testid="tv-link">{t('TV view')}</a>
        <LangSwitch />
      </div>
    </header>
  );
}

export function ErrorBanner({ message, stale }: { message: string; stale: boolean }) {
  return (
    <div class="banner banner-bad" role="alert" data-testid="error">
      <strong>{t(message)}</strong>
      <span>{stale ? t('Showing the last values received. Retrying…') : t('Retrying…')}</span>
    </div>
  );
}

export function Card({ title, children, class: cls = '', aside }: {
  title?: string; children: ComponentChildren; class?: string; aside?: ComponentChildren;
}) {
  return (
    <section class={`card ${cls}`}>
      {title && <div class="card-head"><h2>{t(title)}</h2>{aside}</div>}
      {children}
    </section>
  );
}

export function Skeleton({ h = 120 }: { h?: number }) {
  return <div class="skeleton" style={{ height: `${h}px` }} aria-busy="true" />;
}

export function socColor(soc: number | null | undefined): string {
  if (soc == null) return 'var(--c-inactive)';
  if (soc < 20) return 'var(--c-danger)';
  if (soc < 50) return 'var(--c-warn)';
  return 'var(--c-battery)';
}

export function BatteryCard({ fields, extra }: { fields: Fields; extra?: ComponentChildren }) {
  const soc = fields.battery_soc;
  const filled = socSegments(soc);
  const color = socColor(soc);
  const p = fields.battery_power_signed;
  const state = p == null ? '' : p >= 20 ? t('Charging') : p <= -20 ? t('Discharging') : t('Idle');
  return (
    <Card title="Battery" class="battery-card" aside={<span class="muted">{state}</span>}>
      <div class="soc-row">
        <div class="soc-bars" role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={soc ?? undefined}
             aria-label={t('State of charge')} data-filled={filled}>
          {Array.from({ length: 10 }, (_, i) => (
            <span key={i} class={i < filled ? 'on' : ''} style={i < filled ? { background: color } : undefined} />
          ))}
        </div>
        <span class="soc-pct" data-testid="soc-pct">{soc == null ? '--' : `${Math.round(soc)}%`}</span>
      </div>
      <dl class="mini">
        <div><dt>{t('Voltage')}</dt><dd>{withUnit(fields.battery_voltage, 'V', 1)}</dd></div>
        <div><dt>{t('Current')}</dt><dd>{withUnit(fields.battery_current, 'A', 1)}</dd></div>
        <div><dt>{t('SOH')}</dt><dd>{fields.battery_soh == null ? '--' : `${Math.round(fields.battery_soh)}%`}</dd></div>
        <div><dt>{t('Temperature')}</dt><dd>{withUnit(fields.battery_temperature, '°C', 1)}</dd></div>
      </dl>
      {extra}
    </Card>
  );
}

export const TODAY = [
  { key: 'grid_import_today', label: tx('Import'), color: 'var(--c-grid)' },
  { key: 'grid_export_today', label: tx('Export'), color: 'var(--c-grid-alt)' },
  { key: 'battery_charge_today', label: tx('Charged'), color: 'var(--c-battery)' },
  { key: 'battery_discharge_today', label: tx('Discharged'), color: 'var(--c-battery-alt)' },
  { key: 'load_consumption_today', label: tx('Load'), color: 'var(--c-load)' },
  { key: 'pv_energy_today', label: tx('PV'), color: 'var(--c-pv)' },
] as const;

export function TodayRow({ fields }: { fields: Fields }) {
  return (
    <Card title="Today" class="today-card">
      <div class="today">
        {TODAY.map((row) => (
          <div key={row.key} class="today-item" data-testid={`today-${row.key}`}>
            <span class="dot" style={{ background: row.color }} />
            <span class="today-label">{t(row.label)}</span>
            <span class="today-value">{kwh(fields[row.key])}</span>
          </div>
        ))}
      </div>
    </Card>
  );
}

export type View = 'overview' | 'devices' | 'charts' | 'events' | 'settings' | 'tv' | 'cost';

export function NavBar({ view, onChange }: { view: View; onChange: (v: View) => void }) {
  const item = (v: View, label: string, icon: ComponentChildren) => (
    <button type="button" class={view === v ? 'active' : ''} aria-current={view === v ? 'page' : undefined}
            onClick={() => onChange(v)} data-testid={`nav-${v}`}>
      {icon}<span>{t(label)}</span>
    </button>
  );
  const stroke = { fill: 'none', stroke: 'currentColor', 'stroke-width': 2, 'stroke-linecap': 'round', 'stroke-linejoin': 'round' } as const;
  return (
    <nav class="navbar" aria-label={t('Pages')}>
      {item('overview', 'Overview',
        <svg viewBox="0 0 24 24" width="20" height="20" {...stroke}><circle cx="12" cy="12" r="3" /><circle cx="5" cy="5" r="2" /><circle cx="19" cy="5" r="2" /><circle cx="5" cy="19" r="2" /><circle cx="19" cy="19" r="2" /><path d="M7 5h3v4M17 5h-3v4M7 19h3v-4M17 19h-3v-4" /></svg>)}
      {item('devices', 'Devices',
        <svg viewBox="0 0 24 24" width="20" height="20" {...stroke}><rect x="4" y="4" width="7" height="7" rx="1.5" /><rect x="13" y="4" width="7" height="7" rx="1.5" /><rect x="4" y="13" width="7" height="7" rx="1.5" /><rect x="13" y="13" width="7" height="7" rx="1.5" /></svg>)}
      {item('charts', 'Charts',
        <svg viewBox="0 0 24 24" width="20" height="20" {...stroke}><path d="M3 20h18M6 16l4-6 4 3 5-8" /></svg>)}
      {item('events', 'Events',
        <svg viewBox="0 0 24 24" width="20" height="20" {...stroke}><path d="M6 16V11a6 6 0 0 1 12 0v5l2 2H4z" /><path d="M10 20a2 2 0 0 0 4 0" /></svg>)}
      {item('cost', 'Cost',
        <svg viewBox="0 0 24 24" width="20" height="20" {...stroke}><rect x="3" y="6" width="18" height="12" rx="2" /><circle cx="12" cy="12" r="2.6" /><path d="M6.5 9.5h.01M17.5 14.5h.01" /></svg>)}
      {item('settings', 'Settings',
        <svg viewBox="0 0 24 24" width="20" height="20" {...stroke}><path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0" /><circle cx="16" cy="6" r="2" /><circle cx="10" cy="12" r="2" /><circle cx="18" cy="18" r="2" /></svg>)}
    </nav>
  );
}
