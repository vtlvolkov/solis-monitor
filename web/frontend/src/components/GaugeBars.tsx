import type { Fields } from '../lib/api';
import { kw } from '../lib/format';
import { DANGER_AT, fraction, WARN_AT, zone, ZONE_COLOR } from '../lib/gauge';
import { allGauges, type GaugeEntry } from '../lib/gaugeRows';
import { t, unit } from '../lib/i18n';
import type { SettingsPayload } from '../lib/settings';

function Bar({ e, error }: { e: GaugeEntry; error?: string | null }) {
  if (e.state !== 'ready') {
    return (
      <div class="gbar">
        <div class="gbar-top"><span>{t(e.title)}</span></div>
        {e.state === 'off' ? <div class="gbar-sub">{e.text}</div>
          : error ? <div class="gbar-sub">{t(error)}</div> : <div class="skeleton" style={{ height: '12px', marginTop: '8px' }} aria-busy="true" />}
      </div>
    );
  }
  const r = e.row;
  const z = zone(r.watts, r.maxW), f = fraction(r.watts, r.maxW);
  const color = r.watts == null ? 'var(--c-inactive)' : z === 'ok' ? r.okColor : ZONE_COLOR[z];
  const status = z === 'danger' ? t('Near the limit') : z === 'warn' ? t(r.warnText) : r.note ?? '';
  return (
    <div class="gbar" data-testid={`gbar-${r.kind}`} data-zone={z}>
      <div class="gbar-top">
        <span>{t(r.title)}</span>
        <strong style={{ color }}>{r.watts == null ? '--' : kw(r.watts)} <small>{unit('kW')}</small></strong>
      </div>
      <div class="gbar-track" role="meter" aria-label={r.ariaLabel} aria-valuemin={0} aria-valuemax={Math.round(r.maxW)}
           aria-valuenow={r.watts == null ? undefined : Math.round(r.watts)}>
        <i class="gbar-zone" style={{ left: `${WARN_AT * 100}%`, width: `${(DANGER_AT - WARN_AT) * 100}%`, background: 'var(--c-warn)' }} />
        <i class="gbar-zone" style={{ left: `${DANGER_AT * 100}%`, width: `${(1 - DANGER_AT) * 100}%`, background: 'var(--c-danger)' }} />
        <i class="gbar-fill" style={{ width: `${f * 100}%`, background: color }} />
      </div>
      <div class="gbar-sub">
        <span>{t('{pct}% of {max} {u}', { pct: r.watts == null ? '--' : Math.round(f * 100), max: +(r.maxW / 1000).toFixed(2), u: unit('kW') })}</span>
        <span class={z === 'ok' ? '' : `gbar-${z}`}>{status}</span>
      </div>
    </div>
  );
}

/** The four power gauges as horizontal bars: the compact version for phones and tablets. */
export function GaugeBars({ fields, settings, error }: { fields: Fields | null; settings: SettingsPayload | null | undefined; error?: string | null }) {
  return (
    <div class="gbars" data-testid="gauge-bars">
      {allGauges(fields, settings).map((e) => <Bar key={e.kind} e={e} error={error} />)}
    </div>
  );
}
