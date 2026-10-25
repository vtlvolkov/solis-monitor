import { useCallback, useEffect, useState } from 'preact/hooks';
import { ApiError, getJson } from './api';

/** GET `url` now and every `ms`; retries a failed load after 10 s; keeps the last good data meanwhile. */
export function usePoll<T>(url: string | null, ms: number) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async (): Promise<boolean> => {
    if (!url) return true;
    try {
      setData(await getJson<T>(url));
      setError(null);
      return true;
    } catch (e) {
      setError(e instanceof ApiError ? (e.status === 502 ? 'Inverter or poller not reachable' : e.message) : 'Request failed');
      return false;
    }
  }, [url]);
  useEffect(() => {
    let timer: number | undefined;
    let alive = true;
    const tick = async () => {
      const ok = await load();
      if (alive) timer = window.setTimeout(tick, ok ? ms : Math.min(ms, 10_000));
    };
    tick();
    return () => { alive = false; window.clearTimeout(timer); };
  }, [load, ms]);
  return { data, error, reload: load };
}

export interface EventItem {
  kind: 'outage' | 'alarm'; key: string; name: string | null; severity: 'fault' | 'warning' | null;
  start: string | null; end: string | null; ongoing: boolean; duration_s: number | null;
  soc_start: number | null; soc_end: number | null; load_start: number | null;
}
export interface EventsPayload { days: number; outages: EventItem[]; alarms: EventItem[]; active: EventItem[]; outage_now: EventItem | null }
