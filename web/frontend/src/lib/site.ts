import { useEffect, useState } from 'preact/hooks';

export interface SiteInfo { name: string; raw_retention_days: number | null }
let cached: SiteInfo | null = null;
let pending: Promise<SiteInfo> | null = null;
const subscribers = new Set<(s: SiteInfo) => void>();

function load(): Promise<SiteInfo> {
  pending ??= fetch('/api/site', { headers: { Accept: 'application/json' } })
    .then((r) => (r.ok ? r.json() : {}))
    .catch(() => ({}))
    .then((b: { name?: string; raw_retention_days?: number }) => {
      cached = { name: b.name ?? '', raw_retention_days: typeof b.raw_retention_days === 'number' ? b.raw_retention_days : null };
      if (cached.name) document.title = `${cached.name} · Solis`;
      subscribers.forEach((f) => f(cached!));
      return cached;
    });
  return pending;
}

function useSiteInfo(): SiteInfo | null {
  const [info, setInfo] = useState(cached);
  useEffect(() => {
    if (cached) return;
    subscribers.add(setInfo);
    load();
    return () => { subscribers.delete(setInfo); };
  }, []);
  return info;
}

/** SITE_NAME from the server ('' when unset). Fetched once; also sets the browser tab title. */
export const useSite = (): string => useSiteInfo()?.name ?? '';

/** Days of 10-second readings the server keeps (0 = all, null = not known yet). */
export const useRawRetention = (): number | null => useSiteInfo()?.raw_retention_days ?? null;
