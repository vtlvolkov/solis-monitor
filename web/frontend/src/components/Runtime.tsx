import type { Fields, HistoryPayload } from '../lib/api';
import type { DeviceInfo } from '../lib/devices';
import { power } from '../lib/format';
import { t } from '../lib/i18n';
import type { LiveState } from '../lib/live';
import { usePoll } from '../lib/poll';
import { chargeRate, formatHours, fullChargeHours, recentLoad, recentUrl, runtimeHours } from '../lib/runtime';

/** Average load over the last 15 minutes for the runtime estimate, refreshed every minute. */
export function useAvgLoad(): number | null {
  const hist = usePoll<HistoryPayload>(recentUrl('total_load_power'), 60_000).data;
  return hist ? recentLoad(hist.t, hist.series.total_load_power ?? [], Date.now() / 1000) : null;
}

/** Hours until the battery reaches its max charge SOC, at the recent charging power; null unless it is charging. */
export function useFullCharge(f: Fields | null | undefined, info: DeviceInfo | null): number | null {
  const charging = (f?.battery_power_signed ?? 0) >= 20;
  const hist = usePoll<HistoryPayload>(charging ? recentUrl('battery_power_signed') : null, 60_000).data;
  if (!charging || !hist) return null;
  const rate = chargeRate(hist.t, hist.series.battery_power_signed ?? [], Date.now() / 1000);
  return fullChargeHours(f?.battery_soc, info?.battery.max_charge_soc, info?.battery.capacity_kwh, rate);
}

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
        {t('at {load}{avg}, down to {floor}% · {cap} kWh battery', { load: power(load), avg: avgLoad != null ? ` ${t('(15 min average)')}` : '', floor, cap })}
      </span>
    </div>
  );
}
