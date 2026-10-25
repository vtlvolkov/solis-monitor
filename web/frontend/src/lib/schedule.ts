// What the Time-of-Use schedule is doing right now (pure, unit-tested).
import type { TouKind, TouSlot } from './settings';

export interface SlotRef extends TouSlot { kind: TouKind }

const mins = (hhmm: string) => { const [h, m] = hhmm.split(':').map(Number); return h * 60 + m; };

/** [start, end) with midnight crossing, like the inverter. */
export function inSlot(minute: number, s: { start: string; end: string }): boolean {
  const a = mins(s.start), b = mins(s.end);
  if (a === b) return false;
  return a < b ? minute >= a && minute < b : minute >= a || minute < b;
}

export function enabledSlots(tou: { charge: TouSlot[]; discharge: TouSlot[] }): SlotRef[] {
  return (['charge', 'discharge'] as const).flatMap((kind) =>
    tou[kind].filter((s) => s.enabled && s.start !== s.end).map((s) => ({ ...s, kind })));
}

export function activeSlot(slots: SlotRef[], minute: number): SlotRef | null {
  return slots.find((s) => inSlot(minute, s)) ?? null;
}

/** The next slot to start after `minute` (within 24 h), with minutes until it starts. */
export function nextSlot(slots: SlotRef[], minute: number): { slot: SlotRef; inMin: number } | null {
  let best: { slot: SlotRef; inMin: number } | null = null;
  for (const s of slots) {
    const d = (mins(s.start) - minute + 1440) % 1440 || 1440;
    if (!best || d < best.inMin) best = { slot: s, inMin: d };
  }
  return best;
}

/** Minutes until the active slot ends. */
export function minutesLeft(s: { end: string }, minute: number): number {
  return (mins(s.end) - minute + 1440) % 1440 || 1440;
}

/** "2026-10-18T21:03:41" -> minute of day 1263 */
export function minuteOf(iso: string): number {
  const t = iso.split('T')[1] ?? '00:00';
  return mins(t.slice(0, 5));
}
