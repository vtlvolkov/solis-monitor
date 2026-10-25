import { describe, expect, it } from 'vitest';
import { isDay, isMonth, money, monthOfDay, monthsBetween, niceMax, shiftMonth, stackMax, zoneRows, type CostDay, type CostPeriod, type CostTariff } from './cost';

describe('months', () => {
  it('validates and shifts year-months', () => {
    expect(isMonth('2026-10')).toBe(true);
    for (const bad of ['2026-13', '2026-1', '26-10', '', null, undefined, '2026-10-01']) expect(isMonth(bad as string)).toBe(false);
    expect(isDay('2026-10-18')).toBe(true);
    expect(isDay('2026-10')).toBe(false);
    expect(monthOfDay('2026-10-18')).toBe('2026-10');
    expect(shiftMonth('2026-10', 1)).toBe('2026-11');
    expect(shiftMonth('2026-12', 1)).toBe('2027-01');
    expect(shiftMonth('2026-01', -1)).toBe('2025-12');
    expect(shiftMonth('2026-03', -14)).toBe('2025-01');
  });

  it('lists the months from the first reading to now', () => {
    expect(monthsBetween('2026-10-18', '2026-10')).toEqual(['2026-10']);
    expect(monthsBetween('2025-11-30', '2026-02')).toEqual(['2025-11', '2025-12', '2026-01', '2026-02']);
    expect(monthsBetween(null, '2026-10')).toEqual(['2026-10']);
    expect(monthsBetween('2027-01-01', '2026-10')).toEqual(['2026-10']);        // a clock skew never gives an empty list
  });
});

describe('chart scale', () => {
  const day = (cost: number, kwh: number, has = true): CostDay => ({ date: '2026-10-01', has_data: has, cost, kwh });
  it('finds the biggest stack and rounds the axis up', () => {
    expect(stackMax([day(10, 3), day(42.12, 10.5), day(0, 0, false)], 'cost')).toBe(42.12);
    expect(stackMax([day(10, 3), day(42.12, 10.5)], 'kwh')).toBe(10.5);
    expect(stackMax([], 'cost')).toBe(1);
    expect(niceMax(42.12)).toBe(50);
    expect(niceMax(10.5)).toBe(12);
    expect(niceMax(0.7)).toBe(0.8);
    expect(niceMax(100)).toBe(100);
  });

  it('formats money', () => {
    expect(money(42.123, 'UAH')).toBe('42.12 UAH');
    expect(money(null, 'UAH')).toBe('--');
  });
});

describe('zoneRows', () => {
  const tariff: CostTariff = { currency: 'UAH', day: 4.32, night: 2.16, night_start: '23:00', night_end: '07:00' };
  const today: CostPeriod = { kwh: 6.4, day_kwh: 4.9, night_kwh: 1.5, day_cost: 21.17, night_cost: 3.24, cost: 24.41, saved: 3.24 };
  const month: CostPeriod = { kwh: 30.9, day_kwh: 17.6, night_kwh: 13.3, day_cost: 76.03, night_cost: 28.73, cost: 104.76, saved: 28.73 };

  it('lists the day zone, then the night zone, with their hours, energy and cost', () => {
    const rows = zoneRows(tariff, today, month, { zone: 'night', price: 2.16, until: '07:00' });
    expect(rows.map((r) => r.zone)).toEqual(['day', 'night']);
    expect(rows[0]).toMatchObject({ from: '07:00', to: '23:00', today: { kwh: 4.9, cost: 21.17 }, month: { kwh: 17.6, cost: 76.03 } });
    expect(rows[1]).toMatchObject({ from: '23:00', to: '07:00', today: { kwh: 1.5, cost: 3.24 }, month: { kwh: 13.3, cost: 28.73 } });
  });

  it('marks the zone that is active now', () => {
    expect(zoneRows(tariff, today, month, { zone: 'night', price: 2.16, until: '07:00' }).map((r) => r.active)).toEqual([false, true]);
    expect(zoneRows(tariff, today, month, { zone: 'day', price: 4.32, until: '23:00' }).map((r) => r.active)).toEqual([true, false]);
    expect(zoneRows(tariff, today, month, null).map((r) => r.active)).toEqual([false, false]);
  });

  it('has no rows without a night tariff or without data', () => {
    expect(zoneRows({ ...tariff, night: null, night_start: null, night_end: null }, today, month, null)).toEqual([]);
    expect(zoneRows(tariff, undefined, month, null)).toEqual([]);
  });
});
