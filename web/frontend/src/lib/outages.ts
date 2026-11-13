// Planned outage schedule (графік відключень): payload types and pure helpers for the Overview card and the
// Settings card. The server fetches the schedule (Yasno for now) and works out whether the battery will last.
import { t, tx } from './i18n';

export type SlotType = 'Definite' | 'Possible';
export type DayStatus = 'ScheduleApplies' | 'WaitingForSchedule' | 'EmergencyShutdowns';

export interface Slot { start: number; end: number; type: SlotType }          // minutes from local midnight
export interface ScheduleDay { date: string; status: DayStatus; slots: Slot[] }
export interface NextOutage { start: string; end: string; duration_s: number; in_s: number; ongoing: boolean }
export interface BatteryVerdict {
  enough: boolean;
  lasts_s: number | null;       // how long the battery would carry the expected load
  short_s: number | null;       // how much of the outage it would not cover
  runs_out_at: string | null;
  load_w: number | null;        // the load expected for those hours (from the history)
  soc_at_start: number | null;  // SOC expected when the outage begins
}
export interface OutageAlerts { before: boolean; short: boolean; changes: boolean }
export interface OutageConfig {
  source: string | null; group: string | null; alerts: OutageAlerts; error: string | null; fetched_at: string | null;
  sources: { key: string; label: string; region: string; provider: string }[];
  web: { can_change: boolean; reason: string | null };
}

export const isGroup = (g: string): boolean => /^\d{1,3}\.\d{1,2}$/.test(g);

export interface OutagePayload {
  configured: boolean;
  error: string | null;         // the last fetch's problem, if it failed
  source: string | null; region: string | null; provider: string | null; group: string | null;
  updated_on: string | null;    // when the operator last changed the schedule
  fetched_at: string | null;    // when the server last got it
  stale: boolean;               // the last fetch failed: this is the last good copy
  days: ScheduleDay[];          // today, tomorrow
  next: NextOutage | null;
  battery: BatteryVerdict | null;
}

export const SOURCES: { key: string; label: string; region: string; provider: string }[] = [
  { key: 'yasno-kyiv', label: tx('Yasno: Kyiv'), region: 'Київ', provider: 'ДТЕК Київські електромережі' },
  { key: 'yasno-dnipro-dnem', label: tx('Yasno: Dnipro (DnEM)'), region: 'Дніпро', provider: 'ДнЕМ' },
  { key: 'yasno-dnipro-cek', label: tx('Yasno: Dnipro (CEK)'), region: 'Дніпро', provider: 'ЦЕК' },
];

export const hhmm = (min: number): string => `${String(Math.floor(min / 60)).padStart(2, '0')}:${String(min % 60).padStart(2, '0')}`;

/** "08:00–14:00", with "(possible)" for a possible outage. */
export const slotText = (s: Slot): string => `${hhmm(s.start)}–${hhmm(s.end)}${s.type === 'Possible' ? ` (${t('possible')})` : ''}`;

/** The outage slots of a day (NotPlanned ones are not sent), merged where they touch. */
export function outageSlots(day: ScheduleDay): Slot[] {
  const out: Slot[] = [];
  for (const s of [...day.slots].sort((a, b) => a.start - b.start)) {
    const last = out[out.length - 1];
    if (last && last.type === s.type && last.end >= s.start) last.end = Math.max(last.end, s.end);
    else out.push({ ...s });
  }
  return out;
}

/** Hours without power in a day, definite ones only. */
export const offHours = (day: ScheduleDay): number =>
  outageSlots(day).filter((s) => s.type === 'Definite').reduce((a, s) => a + (s.end - s.start), 0) / 60;

export function describeDay(day: ScheduleDay): string {
  if (day.status === 'WaitingForSchedule') return t('waiting for the schedule');
  if (day.status === 'EmergencyShutdowns') return t('emergency outages: the schedule does not apply');
  const h = offHours(day);
  return h ? t('{h} h without power', { h: Number.isInteger(h) ? h : h.toFixed(1) }) : t('no outages planned');
}

export function verdictText(b: BatteryVerdict, fmt: (s: number) => string): { text: string; tone: 'ok' | 'bad' } {
  if (b.enough) return { text: t('Battery is enough: ≈ {d} at about {w} W', { d: b.lasts_s != null ? fmt(b.lasts_s) : '--', w: b.load_w ?? '--' }), tone: 'ok' };
  return { text: t('Battery runs out ≈ {at}, {short} short', { at: b.runs_out_at ?? '--', short: b.short_s != null ? fmt(b.short_s) : '--' }), tone: 'bad' };
}
