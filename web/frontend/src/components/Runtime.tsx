import type { DeviceInfo } from '../lib/devices';
import { power } from '../lib/format';
import { t } from '../lib/i18n';
import type { LiveState } from '../lib/live';
import { formatHours, runtimeHours } from '../lib/runtime';

/** "Backup runtime ≈ 6 h at 0.5 kW": shown inside the Battery card (Overview and TV page). */
export function Runtime({ live, info, avgLoad }: { live: LiveState; info: DeviceInfo | null; avgLoad: number | null }) {
  const f = live.data?.fields ?? {};
  const cap = info?.battery.capacity_kwh ?? null;
  const floor = info?.backup.eps_dod_pct ?? info?.battery.overdischarge_soc ?? null;
  const load = avgLoad ?? f.total_load_power ?? null;
  const h = runtimeHours(f.battery_soc, floor, cap, load);
  if (cap == null || floor == null) return null;
  return (
    <div class="runtime" data-testid="runtime">
      <span class="runtime-label">{t('Backup runtime')}</span>
      <span class="runtime-value">≈ {formatHours(h)}</span>
      <span class="runtime-note">
        {t('at {load}{avg}, down to {floor}% · {cap} kWh battery', { load: power(load), avg: avgLoad != null ? ` ${t('(1 h average)')}` : '', floor, cap })}
      </span>
    </div>
  );
}
