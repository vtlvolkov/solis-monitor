import { useEffect, useState } from 'preact/hooks';
import { ApiError, getJson, type LivePayload } from './api';

export const POLL_MS = 10_000;
const SSE_SILENCE_MS = 30_000;   // no event for this long -> fall back to polling
const SSE_RETRY_MS = 60_000;     // while polling, try SSE again this often
export const OFFLINE_AFTER_S = 45;

export interface LiveState {
  data: LivePayload | null;
  receivedAt: number;
  error: string | null;
  transport: 'connecting' | 'sse' | 'poll';
}

/**
 * Latest inverter point. Prefers Server-Sent Events (/api/stream); falls back to
 * polling /api/live when EventSource is missing, errors, or goes quiet.
 * `?transport=poll` in the page URL forces polling.
 */
export function useLive(): LiveState {
  const [state, setState] = useState<LiveState>({ data: null, receivedAt: 0, error: null, transport: 'connecting' });

  useEffect(() => {
    let es: EventSource | null = null;
    let pollTimer: number | undefined;
    let retryTimer: number | undefined;
    let watchdog: number | undefined;
    let disposed = false;
    let polling = false;
    const forcePoll = new URLSearchParams(location.search).get('transport') === 'poll';

    const accept = (data: LivePayload, transport: LiveState['transport']) =>
      setState({ data, receivedAt: Date.now(), error: null, transport });
    const fail = (error: string, transport: LiveState['transport']) =>
      setState((s) => ({ ...s, error, transport }));

    const poll = async () => {
      try {
        accept(await getJson<LivePayload>('/api/live'), 'poll');
      } catch (e) {
        fail(e instanceof ApiError ? e.message : 'Connection error', 'poll');
      }
      if (!disposed && polling) pollTimer = window.setTimeout(poll, POLL_MS);
    };

    const startPolling = () => {
      if (disposed) return;
      if (!polling) {
        polling = true;
        poll();
      }
      window.clearTimeout(retryTimer);
      if (!forcePoll && 'EventSource' in window) retryTimer = window.setTimeout(startSse, SSE_RETRY_MS);
    };

    const armWatchdog = () => {
      window.clearTimeout(watchdog);
      watchdog = window.setTimeout(() => { stopSse(); startPolling(); }, SSE_SILENCE_MS);
    };

    const stopSse = () => {
      es?.close();
      es = null;
      window.clearTimeout(watchdog);
    };

    function startSse() {
      if (disposed) return;
      window.clearTimeout(retryTimer);
      es = new EventSource('/api/stream');
      armWatchdog();
      es.onmessage = (ev) => {
        armWatchdog();
        // SSE works again: stop polling.
        if (polling) { polling = false; window.clearTimeout(pollTimer); }
        try { accept(JSON.parse(ev.data), 'sse'); } catch { /* ignore malformed */ }
      };
      es.addEventListener('influx-error', () => { armWatchdog(); fail('InfluxDB unavailable', 'sse'); });
      es.onerror = () => {
        stopSse();
        startPolling();
      };
    }

    if (forcePoll || !('EventSource' in window)) startPolling();
    else startSse();

    return () => {
      disposed = true;
      polling = false;
      stopSse();
      window.clearTimeout(pollTimer);
      window.clearTimeout(retryTimer);
    };
  }, []);

  return state;
}

/** Re-render every `ms` (for "last seen ... ago"). */
export function useNow(ms = 1000): number {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), ms);
    return () => window.clearInterval(id);
  }, [ms]);
  return now;
}

/** Age of the latest point right now, extrapolated from the server-reported age. */
export function currentAge(s: LiveState, now: number): number | null {
  if (!s.data || s.data.age_s == null) return null;
  return s.data.age_s + Math.max(0, now - s.receivedAt) / 1000;
}

export function isOnline(s: LiveState, now: number): boolean {
  const age = currentAge(s, now);
  return age != null && age <= OFFLINE_AFTER_S;
}
