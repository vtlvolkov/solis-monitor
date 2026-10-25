// Tiny translation layer: the English text is the key, so English needs no dictionary, an
// untranslated string falls back to English, and the code stays readable. Dictionary: i18n.uk.ts.
import { useEffect, useState } from 'preact/hooks';
import { UK, UK_PATTERNS } from './i18n.uk';

export type Lang = 'en' | 'uk';
export const LANGS: readonly Lang[] = ['en', 'uk'];
const STORE = 'solis-lang';

/** `?lang=uk` in the URL, else the saved choice, else the browser language. */
function detect(): Lang {
  try {
    const q = new URLSearchParams(location.search).get('lang');
    if (q === 'en' || q === 'uk') return q;
  } catch { /* no window (unit tests) */ }
  try {
    const s = localStorage.getItem(STORE);
    if (s === 'en' || s === 'uk') return s;
  } catch { /* storage blocked */ }
  try {
    if (navigator.language?.toLowerCase().startsWith('uk')) return 'uk';
  } catch { /* no navigator */ }
  return 'en';
}

let lang: Lang = detect();
const subscribers = new Set<() => void>();

export const getLang = (): Lang => lang;

export function setLang(next: Lang): void {
  if (next === lang) return;
  lang = next;
  try { localStorage.setItem(STORE, next); } catch { /* storage blocked */ }
  try { document.documentElement.lang = next; } catch { /* no document */ }
  subscribers.forEach((f) => f());
}

/** Re-renders the calling component (use it once, at the app root) when the language changes. */
export function useLang(): Lang {
  const [current, setCurrent] = useState<Lang>(lang);
  useEffect(() => {
    const f = () => setCurrent(lang);
    subscribers.add(f);
    f();
    return () => { subscribers.delete(f); };
  }, []);
  return current;
}

function fromPattern(key: string): string {
  for (const [re, out] of UK_PATTERNS) if (re.test(key)) return key.replace(re, out);
  return key;
}

/** Translate `key` (English text); `{name}` placeholders are filled from `vars`. */
export function t(key: string, vars?: Record<string, string | number>): string {
  const s = lang === 'uk' ? UK[key] ?? fromPattern(key) : key;
  return vars ? s.replace(/\{(\w+)\}/g, (_m, k: string) => (k in vars ? String(vars[k]) : '')) : s;
}

/** Marks a string for translation where it is defined; render it later with t(). */
export const tx = (key: string): string => key;

/** Plural form of "one|many" (English) or "one|few|many" (Ukrainian) without the number. */
export function tp(n: number, key: string): string {
  const forms = t(key).split('|');
  if (lang === 'uk' && forms.length === 3) {
    const a = Math.abs(n) % 100, b = a % 10;
    return forms[b === 1 && a !== 11 ? 0 : b >= 2 && b <= 4 && (a < 12 || a > 14) ? 1 : 2];
  }
  return forms[Math.abs(n) === 1 ? 0 : forms.length - 1];
}

/** Locale for toLocale*String: undefined = the browser's own. */
export const locale = (): string | undefined => (lang === 'uk' ? 'uk-UA' : undefined);

const UNITS_UK: Record<string, string> = {
  W: 'Вт', kW: 'кВт', kWh: 'кВт·год', V: 'В', A: 'А', Hz: 'Гц', Ah: 'А·год', VA: 'ВА', var: 'вар',
};
/** Physical unit as written in the current language (°C and % are the same). */
export const unit = (u: string): string => (lang === 'uk' ? UNITS_UK[u] ?? u : u);
