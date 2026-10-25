export type Fields = Record<string, number | null>;

export interface LivePayload {
  time: string | null;
  age_s: number | null;
  online: boolean;
  fields: Fields;
}

export interface HistoryPayload {
  range: string;
  every: string;
  agg: string;
  t: number[];
  series: Record<string, (number | null)[]>;
}

export interface DailyPayload {
  tz: string;
  unit: 'kWh';
  days: ({ date: string } & Fields)[];
}

export interface SummaryPayload {
  tz: string;
  unit: 'kWh';
  month: Fields;
  year: Fields;
  first_day: string | null;
}

export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

export async function getJson<T>(url: string, signal?: AbortSignal): Promise<T> {
  let res: Response;
  try {
    res = await fetch(url, { signal, headers: { Accept: 'application/json' } });
  } catch (e) {
    if ((e as Error).name === 'AbortError') throw e;
    throw new ApiError('Dashboard server unreachable', 0);
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch { /* not JSON */ }
    throw new ApiError(res.status === 503 ? 'InfluxDB unavailable' : String(detail), res.status);
  }
  return res.json() as Promise<T>;
}

/** POST a JSON body; resolves with the JSON answer, rejects with an ApiError carrying the server's `detail`. */
export async function postJson<T>(url: string, body: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'application/json' }, body: JSON.stringify(body) });
  } catch {
    throw new ApiError('Dashboard server unreachable', 0);
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(data.detail ?? res.statusText, res.status);
  return data as T;
}

export const RANGES = ['1h', '6h', '24h', '7d', '30d'] as const;
export type Range = (typeof RANGES)[number];
