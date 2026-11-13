// The Overview's cards: which ones it shows and in which order, in two columns (one on a phone: left, then right).
// The layout is kept on the server (web/backend/app/layout.py has the same card list); these are the pure rules.
import { tx } from './i18n';

export type Col = 'left' | 'right';
export interface Layout { left: string[]; right: string[] }

export interface CardInfo { id: string; title: string; desc: string; col: Col }

/** Every card, in the order the Add cards list shows them. */
export const CARDS: CardInfo[] = [
  { id: 'flow', title: tx('Power flows'), desc: tx('The live diagram of PV, grid, battery and loads.'), col: 'left' },
  { id: 'battery', title: tx('Battery'), desc: tx('Charge, voltage, current, temperature and the backup runtime.'), col: 'left' },
  { id: 'gauges', title: tx('Power gauges'), desc: tx('Grid, backup load, battery charge and discharge against their limits.'), col: 'left' },
  { id: 'grid', title: tx('Grid & backup'), desc: tx('Grid state, voltage, backup output and the recent outages.'), col: 'right' },
  { id: 'outages', title: tx('Planned outages'), desc: tx('The next planned outage for your group and whether the battery lasts.'), col: 'right' },
  { id: 'schedule', title: tx('Schedule'), desc: tx('The active charge slot and the battery settings in force.'), col: 'right' },
  { id: 'today', title: tx('Today'), desc: tx('Energy imported, exported, charged, discharged, used and produced today.'), col: 'right' },
  { id: 'cost', title: tx('Electricity cost'), desc: tx('Today and this month, and the price in force now.'), col: 'right' },
  { id: 'power', title: tx('Power today'), desc: tx('A chart of PV and load since midnight.'), col: 'left' },
  { id: 'selfsufficiency', title: tx('Self-sufficiency'), desc: tx('How much of the energy came from your own PV today and this month.'), col: 'right' },
  { id: 'events', title: tx('Recent events'), desc: tx('The last outages and alarms.'), col: 'right' },
  { id: 'health', title: tx('Battery health'), desc: tx('State of health, full cycles, round-trip efficiency and temperature.'), col: 'left' },
  { id: 'heatmap', title: tx('Load by hour'), desc: tx('When the house uses the most power, over the last 7 days.'), col: 'left' },
];
export const CARD_IDS = CARDS.map((c) => c.id);
export const cardInfo = (id: string): CardInfo | undefined => CARDS.find((c) => c.id === id);

/** The page as it was before it could be changed (Planned outages only when its own setting showed it). */
export function defaultLayout(outages = true): Layout {
  return {
    left: ['flow', 'battery', 'gauges'],
    right: ['grid', ...(outages ? ['outages'] : []), 'schedule', 'today', 'cost'],
  };
}

/** Drops unknown cards and repeats, so an old or hand-edited layout still draws. */
export function normalize(raw: unknown): Layout | null {
  if (!raw || typeof raw !== 'object') return null;
  const r = raw as Record<string, unknown>;
  if (!Array.isArray(r.left) || !Array.isArray(r.right)) return null;
  const seen = new Set<string>();
  const keep = (ids: unknown[]) => ids.filter((i): i is string => typeof i === 'string' && CARD_IDS.includes(i) && !seen.has(i) && !!seen.add(i));
  return { left: keep(r.left), right: keep(r.right) };
}

export const sameLayout = (a: Layout, b: Layout): boolean =>
  a.left.join() === b.left.join() && a.right.join() === b.right.join();

export const onPage = (l: Layout): Set<string> => new Set([...l.left, ...l.right]);
export const columnOf = (l: Layout, id: string): Col | null => (l.left.includes(id) ? 'left' : l.right.includes(id) ? 'right' : null);

/** `id` taken out of wherever it is and put at `index` of `col` (the index counts the column without it). */
export function moveCard(l: Layout, id: string, col: Col, index: number): Layout {
  const out: Layout = { left: l.left.filter((i) => i !== id), right: l.right.filter((i) => i !== id) };
  out[col].splice(Math.max(0, Math.min(index, out[col].length)), 0, id);
  return out;
}

export const removeCard = (l: Layout, id: string): Layout => ({ left: l.left.filter((i) => i !== id), right: l.right.filter((i) => i !== id) });

/** A new card goes to the end of its usual column. */
export function addCard(l: Layout, id: string): Layout {
  const info = cardInfo(id);
  if (!info || onPage(l).has(id)) return l;
  return moveCard(l, id, info.col, l[info.col].length);
}

/** The arrow keys on a card's handle: up/down within the column, left/right to the same place in the other one. */
export function stepCard(l: Layout, id: string, key: string): Layout | null {
  const col = columnOf(l, id);
  if (!col) return null;
  const i = l[col].indexOf(id);
  if (key === 'ArrowUp') return i > 0 ? moveCard(l, id, col, i - 1) : null;
  if (key === 'ArrowDown') return i < l[col].length - 1 ? moveCard(l, id, col, i + 1) : null;
  if ((key === 'ArrowLeft' && col === 'right') || (key === 'ArrowRight' && col === 'left')) return moveCard(l, id, col === 'left' ? 'right' : 'left', i);
  return null;
}

/** Where a card dropped at height `y` goes: the number of the other cards whose middle is above it. */
export const dropIndex = (mids: number[], y: number): number => mids.filter((m) => m < y).length;

/** The column nearest to a point (0 inside it): on a phone both columns share x, so y decides. */
export function nearestColumn(rects: { col: Col; left: number; right: number; top: number; bottom: number }[], x: number, y: number): Col | null {
  let best: Col | null = null, dist = Infinity;
  for (const r of rects) {
    const dx = x < r.left ? r.left - x : x > r.right ? x - r.right : 0;
    const dy = y < r.top ? r.top - y : y > r.bottom ? y - r.bottom : 0;
    const d = Math.hypot(dx, dy);
    if (d < dist) { dist = d; best = r.col; }
  }
  return best;
}

/** What Save sends: null when the result is the default, so a later release's default still applies. */
export const toSave = (l: Layout, outages: boolean): Layout | null => (sameLayout(l, defaultLayout(outages)) ? null : l);
