import { describe, expect, it } from 'vitest';
import { dateIn, describeTariff, formFor, parseTariffForm, startOfDay, type TariffForm, type TariffValues } from './tariff';

const CUR: TariffValues = { day: 4.32, night: 2.16, night_start: '23:00', night_end: '07:00' };
const form = (over: Partial<TariffForm> = {}): TariffForm => ({ ...formFor(CUR), ...over });
const TODAY = '2026-10-22';

describe('tariff form', () => {
  it('starts from the current prices, or empty with the usual night hours', () => {
    expect(formFor(CUR)).toEqual({ day: '4.32', night: '2.16', start: '23:00', end: '07:00', when: 'now', date: '' });
    expect(formFor(null)).toEqual({ day: '', night: '', start: '23:00', end: '07:00', when: 'now', date: '' });
    expect(formFor({ day: 5, night: null, night_start: null, night_end: null }).night).toBe('');
  });

  it('turns a valid form into a request, accepting a comma decimal', () => {
    const r = parseTariffForm(form({ day: '6,5', night: '3', start: '22:00', end: '06:00' }), TODAY, CUR);
    expect(r).toEqual({ ok: true, value: { day: 6.5, night: 3, night_start: '22:00', night_end: '06:00', from: null } });
  });

  it('an empty night price means one price all day, and drops the hours', () => {
    const r = parseTariffForm(form({ day: '5', night: '' }), TODAY, CUR);
    expect(r).toEqual({ ok: true, value: { day: 5, night: null, night_start: null, night_end: null, from: null } });
  });

  it('refuses bad prices, hours and dates with a message', () => {
    for (const bad of ['', '0', '-1', 'abc', '4.32.1', '1e3', '4.123456', '10001']) {
      const r = parseTariffForm(form({ day: bad }), TODAY, CUR);
      expect(r.ok, `day "${bad}"`).toBe(false);
    }
    expect(parseTariffForm(form({ night: '0' }), TODAY, CUR).ok).toBe(false);
    expect(parseTariffForm(form({ night: 'x' }), TODAY, CUR).ok).toBe(false);
    expect(parseTariffForm(form({ day: '5', start: '24:00' }), TODAY, CUR).ok).toBe(false);
    expect(parseTariffForm(form({ day: '5', start: '07:00', end: '07:00' }), TODAY, CUR).ok).toBe(false);
    expect(parseTariffForm(form({ day: '5', when: 'date', date: '' }), TODAY, CUR).ok).toBe(false);
    expect(parseTariffForm(form({ day: '5', when: 'date', date: '2026-10-21' }), TODAY, CUR).ok).toBe(false);   // yesterday
  });

  it('accepts today or a later date, and a change that only starts later even if the prices look the same', () => {
    expect(parseTariffForm(form({ day: '5', when: 'date', date: '2026-10-22' }), TODAY, CUR))
      .toMatchObject({ ok: true, value: { from: '2026-10-22' } });
    expect(parseTariffForm(form({ when: 'date', date: '2026-11-01' }), TODAY, CUR)).toMatchObject({ ok: true });
  });

  it('refuses a change that would not change anything now', () => {
    expect(parseTariffForm(form(), TODAY, CUR)).toEqual({ ok: false, error: 'Nothing changed' });
    expect(parseTariffForm(form({ day: '4.320' }), TODAY, CUR).ok).toBe(false);
  });

  it('works when no tariff exists yet', () => {
    expect(parseTariffForm(formFor(null), TODAY, null).ok).toBe(false);                      // the day price is required
    expect(parseTariffForm({ ...formFor(null), day: '4.32' }, TODAY, null)).toMatchObject({ ok: true, value: { day: 4.32, night: null } });
  });
});

describe('describing a tariff', () => {
  it('names both zones with their hours, or says one price', () => {
    expect(describeTariff(CUR, 'UAH')).toBe('Day 4.32 UAH/kWh · night 2.16 UAH/kWh, 23:00–07:00');
    expect(describeTariff({ day: 5, night: null, night_start: null, night_end: null }, 'UAH')).toBe('5 UAH/kWh, one price all day');
  });

  it('uses the site time zone for the calendar date and shows a start-of-day date', () => {
    const lateUtc = new Date('2026-10-22T22:30:00Z');             // already the 23rd in Kyiv (UTC+3)
    expect(dateIn('Europe/Kyiv', lateUtc)).toBe('2026-10-23');
    expect(dateIn('UTC', lateUtc)).toBe('2026-10-22');
    expect(startOfDay('2026-11-01')).toBe('01.11.26, 00:00');
  });
});
