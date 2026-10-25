import { useEffect, useState } from 'preact/hooks';

let cached: string | null = null;

/** SITE_NAME from the server ('' when unset). Fetched once; also sets the browser tab title. */
export function useSite(): string {
  const [name, setName] = useState(cached ?? '');
  useEffect(() => {
    if (cached !== null) return;
    fetch('/api/site', { headers: { Accept: 'application/json' } })
      .then((r) => (r.ok ? r.json() : { name: '' }))
      .catch(() => ({ name: '' }))
      .then((b: { name?: string }) => {
        cached = b.name ?? '';
        setName(cached);
        if (cached) document.title = `${cached} · Solis`;
      });
  }, []);
  return name;
}
