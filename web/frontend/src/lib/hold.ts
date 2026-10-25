// "Battery hold": is an all-day grid charge slot keeping the battery topped up? (pure, unit-tested)
import type { SettingsPayload, TouSlot } from './settings';

/** A charge slot this long, or longer, effectively runs around the clock. */
export const HOLD_MIN_MINUTES = 20 * 60;

const mins = (hhmm: string) => { const [h, m] = hhmm.split(':').map(Number); return h * 60 + m; };

/** Length of a slot in minutes; may cross midnight. Start = end means off (0). */
export const slotMinutes = (s: { start: string; end: string }): number =>
  s.start === s.end ? 0 : (mins(s.end) - mins(s.start) + 1440) % 1440;

/** The enabled charge slot that holds the battery at its target from the grid, or null. */
export function holdInfo(s: SettingsPayload): { slot: TouSlot; minutes: number } | null {
  if (!s.grid_charging) return null;
  let best: { slot: TouSlot; minutes: number } | null = null;
  for (const slot of s.tou.charge) {
    if (!slot.enabled) continue;
    const minutes = slotMinutes(slot);
    if (minutes >= HOLD_MIN_MINUTES && (!best || minutes > best.minutes)) best = { slot, minutes };
  }
  return best;
}
