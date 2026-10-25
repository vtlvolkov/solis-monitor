import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { setLang, t, tp, unit } from './i18n';
import { UK } from './i18n.uk';

const SRC = join(__dirname, '..');
// The web Docker image is built from web/ only, so the poller's source is absent there (CI and a checkout have it).
const FAULTS_PY = join(SRC, '..', '..', '..', 'poller', 'faults.py');
const files = (dir: string): string[] =>
  readdirSync(dir).flatMap((n) => {
    const p = join(dir, n);
    return statSync(p).isDirectory() ? files(p) : /\.tsx?$/.test(n) && !/\.test\.ts$|^i18n/.test(n) ? [p] : [];
  });

const placeholders = (s: string) => [...s.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort().join(',');
const unescape = (s: string) => s.replace(/\\'/g, "'").replace(/\\"/g, '"');

/** English strings the code passes to t() / tx() / tp(), plus static card titles and device-row labels. */
function usedKeys(): Map<string, string> {
  const keys = new Map<string, string>();
  const call = /\b(?:t|tx|tp)\(\s*(?:[A-Za-z_.!?\s()[\]]+,\s*)?(['"])((?:(?!\1)[^\\]|\\.)*)\1/g;
  const title = /\btitle="([^"]+)"/g;
  const row = /\[\s*'([A-Z][^']*)',/g;
  for (const f of files(SRC)) {
    const src = readFileSync(f, 'utf8');
    for (const m of src.matchAll(call)) keys.set(unescape(m[2]), f);
    for (const m of src.matchAll(title)) keys.set(m[1], f);
    if (f.endsWith('Devices.tsx')) for (const m of src.matchAll(row)) keys.set(m[1], f);
  }
  return keys;
}

describe('Ukrainian dictionary', () => {
  it('has every string the code asks to translate', () => {
    const missing = [...usedKeys()].filter(([k]) => !(k in UK)).map(([k, f]) => `${k}  (${f.replace(SRC, 'src')})`);
    expect(missing).toEqual([]);
  });

  it('keeps the same {placeholders} as the English key, and is never empty', () => {
    for (const [k, v] of Object.entries(UK)) {
      expect(v.trim(), k).not.toBe('');
      expect(placeholders(v), k).toBe(placeholders(k));
    }
  });

  it('has three plural forms for each plural key', () => {
    for (const [k, v] of Object.entries(UK)) {
      if (k.includes('|')) expect(v.split('|').length, k).toBe(3);
    }
  });

  it.skipIf(!existsSync(FAULTS_PY))('translates every poller fault and status name', () => {
    const py = readFileSync(FAULTS_PY, 'utf8');
    const names = [...py.matchAll(/"([A-Za-z0-9][^"]*)"/g)].map((m) => m[1])
      .filter((n) => !n.includes('\n') && /^[A-Z0-9]/.test(n) && !/^(Code|Fault \d|BMS fault|key|kind|alarm|outage|start|end|fault|warning)/.test(n) && !n.includes('{'))
      .filter((n) => !/^(f\d|bms\d|op\d)/.test(n) && !['Grid outage'].includes(n));
    const missing = [...new Set(names)].filter((n) => !(n in UK));
    expect(missing).toEqual([]);
  });
});

describe('t()', () => {
  afterEach(() => setLang('en'));

  it('is the identity in English and fills placeholders', () => {
    setLang('en');
    expect(t('Battery')).toBe('Battery');
    expect(t('{n} min', { n: 5 })).toBe('5 min');
    expect(unit('kWh')).toBe('kWh');
  });

  it('translates in Ukrainian, falls back to English, and handles patterns', () => {
    setLang('uk');
    expect(t('Battery')).toBe('Батарея');
    expect(t('{n} min', { n: 5 })).toBe('5 хв');
    expect(t('Something nobody translated')).toBe('Something nobody translated');
    expect(t('Code 0x1234')).toBe('Код 0x1234');
    expect(t('Fault 3 bit 5')).toBe('Помилка 3, біт 5');
    expect(unit('kWh')).toBe('кВт·год');
    expect(unit('°C')).toBe('°C');
  });

  it('picks Ukrainian plural forms and English singular/plural', () => {
    setLang('en');
    expect([1, 2].map((n) => tp(n, 'alarm|alarms'))).toEqual(['alarm', 'alarms']);
    setLang('uk');
    const forms = [1, 2, 4, 5, 11, 12, 14, 21, 22, 25, 101].map((n) => tp(n, 'alarm|alarms'));
    expect(forms).toEqual(['тривога', 'тривоги', 'тривоги', 'тривог', 'тривог', 'тривог', 'тривог',
                           'тривога', 'тривоги', 'тривог', 'тривога']);
  });
});
