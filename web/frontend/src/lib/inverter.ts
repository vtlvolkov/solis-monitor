// Where the inverter's data logger is: types and pure helpers for the Settings card (unit-tested).
import { t, tx } from './i18n';
import type { Parsed } from './tariff';

export interface InverterPayload {
  host: string; port: number; unit: number;
  source: 'saved' | 'env' | 'unset';
  configured: boolean; env_host: string; saved: boolean;
  connected: boolean; last_ok_age_s: number | null; error: string | null; failures: number;
  web: { can_change: boolean; reason: string | null };
  scan_hint: string | null;                    // a network to suggest, from the logger's address or the browser's
  applied?: string;                            // only in the answer to a save
}
export interface ScanResult { network: string; found: string[] }

export interface Address { host: string; port: number; unit: number }

/** Messages the poller sends in English; listed so each has a Ukrainian text. */
export const SERVER_TEXTS = [
  tx("Enter the logger's IP address or host name."),
  tx('That is not a valid IPv4 address.'),
  tx('That address cannot be the logger (use its address on your home network).'),
  tx('Use just the IP address or host name, without http://, a port or spaces.'),
  tx('The port must be between 1 and 65535.'),
  tx('The Modbus unit must be between 1 and 247.'),
  tx('Enter the network to scan, like 192.168.0.'),
  tx('That is not a valid network.'),
  tx('Only home networks can be scanned (192.168.x.x, 10.x.x.x or 172.16-31.x.x).'),
  tx('A scan is already running.'),
  tx('The poller is still starting.'),
];

const IPV4 = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/;
const LABEL = '[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?';
const HOSTNAME = new RegExp(`^${LABEL}(\\.${LABEL})*$`);
const octets = (m: RegExpMatchArray) => m.slice(1, 5).map(Number);

/** Validates the address form like the poller does (an IPv4 address or host name, port 1-65535, unit 1-247). */
export function parseAddress(host: string, port: string, unit: string): Parsed<Address> {
  const h = host.trim();
  if (!h) return { ok: false, error: t("Enter the logger's IP address or host name.") };
  const m = h.match(IPV4);
  if (/^[0-9.]+$/.test(h)) {
    if (!m || octets(m).some((n) => n > 255)) return { ok: false, error: t('That is not a valid IPv4 address.') };
    const [a, b] = octets(m);
    if (a === 0 || a === 127 || a >= 224 || (a === 169 && b === 254)) {
      return { ok: false, error: t('That address cannot be the logger (use its address on your home network).') };
    }
  } else if (h.length > 253 || !HOSTNAME.test(h)) {
    return { ok: false, error: t('Use just the IP address or host name, without http://, a port or spaces.') };
  }
  const p = Number(port.trim()), u = Number(unit.trim());
  if (!/^\d+$/.test(port.trim()) || p < 1 || p > 65535) return { ok: false, error: t('The port must be between 1 and 65535.') };
  if (!/^\d+$/.test(unit.trim()) || u < 1 || u > 247) return { ok: false, error: t('The Modbus unit must be between 1 and 247.') };
  return { ok: true, value: { host: h.toLowerCase(), port: p, unit: u } };
}

/** "192.168.0", "192.168.0.0/24" or any address in it -> "192.168.0"; null if it is not a home network. */
export function networkFrom(text: string): string | null {
  const m = text.trim().match(/^(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?:\.(\d{1,3})(?:\/24)?)?$/);
  if (!m) return null;
  const [a, b, c] = m.slice(1, 4).map(Number);
  if ([a, b, c].some((n) => n > 255)) return null;
  const home = a === 10 || (a === 192 && b === 168) || (a === 172 && b >= 16 && b <= 31);
  return home ? `${a}.${b}.${c}` : null;
}

/** The network to prefill: the server's hint, else the one the page was opened on (when that is an address). */
export function guessNetwork(hint: string | null, pageHost: string): string {
  return hint ?? networkFrom(pageHost) ?? '';
}

export type Tone = 'ok' | 'warn' | 'bad' | 'info';

/** One sentence about the connection, with a tone for its colour. */
export function connectionLine(p: Pick<InverterPayload, 'configured' | 'connected' | 'last_ok_age_s' | 'error' | 'failures'>): { text: string; tone: Tone } {
  if (!p.configured) return { text: t('No address yet. The dashboard waits until you enter one.'), tone: 'warn' };
  if (p.connected) return { text: t('Connected · last reading {n} s ago', { n: Math.round(p.last_ok_age_s ?? 0) }), tone: 'ok' };
  if (p.failures > 0) return { text: t('Not reachable: {error}', { error: p.error ?? '?' }), tone: 'bad' };
  return { text: t('Connecting…'), tone: 'info' };
}
