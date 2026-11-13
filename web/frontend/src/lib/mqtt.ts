// MQTT (Home Assistant, Node-RED, relays): types and pure helpers for the Settings card. The poller publishes; this
// only shows and changes where to. Nothing on MQTT can change the inverter.
import { t, tx } from './i18n';
import type { Parsed } from './tariff';
import type { Tone } from './inverter';

export interface MqttStatus {
  state: 'off' | 'connecting' | 'connected' | 'error';
  error: string | null;
  last_publish_age_s: number | null;
  sent: number;
  dropped: number;
}
export interface MqttPayload {
  enabled: boolean;
  host: string; port: number; username: string; password_set: boolean; tls: boolean;
  prefix: string;               // topics start with it: <prefix>/state, <prefix>/event, <prefix>/status
  interval_s: number;
  discovery: boolean;           // Home Assistant MQTT discovery
  source: 'saved' | 'env' | 'unset';
  status: MqttStatus;
  topics: { state: string; event: string; availability: string; discovery: string | null };
  last_message: Record<string, number | string | boolean | null> | null;
  web: { can_change: boolean; reason: string | null };
}

export interface MqttForm { host: string; port: string; username: string; password: string; tls: boolean; prefix: string; interval_s: number; discovery: boolean }
export interface MqttChange {
  host: string; port: number; username: string;
  password: string | null;      // null = keep the saved one
  tls: boolean; prefix: string; interval_s: number; discovery: boolean;
}

export const INTERVALS = [10, 30, 60] as const;

/** Messages the poller sends in English; listed so each has a Ukrainian text. */
export const SERVER_TEXTS = [
  tx('Enter the broker’s address.'),
  tx('Use just the IP address or host name of the broker, without mqtt://, a port or spaces.'),
  tx('The port must be between 1 and 65535.'),
  tx('The topic prefix may use letters, digits, - _ and /, without + or #.'),
  tx('Send every 10, 30 or 60 seconds.'),
  tx('Connection refused'),
  tx('Wrong username or password'),
  tx('Broker not reachable'),
];

const LABEL = '[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?';
const HOST = new RegExp(`^${LABEL}(\\.${LABEL})*$`);
const PREFIX = /^[A-Za-z0-9_-]+(\/[A-Za-z0-9_-]+)*$/;

/** "Home" -> "solis/home": the default prefix for a site name. */
export function defaultPrefix(site: string): string {
  const slug = site.toLowerCase().normalize('NFKD').replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
  return `solis/${slug || 'home'}`;
}

export const formOf = (p: MqttPayload, site: string): MqttForm => ({
  host: p.host, port: String(p.port || (p.tls ? 8883 : 1883)), username: p.username, password: '', tls: p.tls,
  prefix: p.prefix || defaultPrefix(site), interval_s: p.interval_s || 10, discovery: p.discovery,
});

/** Validates the form like the poller does. An empty password keeps the saved one. */
export function parseMqtt(f: MqttForm): Parsed<MqttChange> {
  const host = f.host.trim();
  if (!host) return { ok: false, error: t('Enter the broker’s address.') };
  if (host.length > 253 || !HOST.test(host)) return { ok: false, error: t('Use just the IP address or host name of the broker, without mqtt://, a port or spaces.') };
  const port = Number(f.port.trim());
  if (!/^\d+$/.test(f.port.trim()) || port < 1 || port > 65535) return { ok: false, error: t('The port must be between 1 and 65535.') };
  const prefix = f.prefix.trim().replace(/^\/+|\/+$/g, '');
  if (!PREFIX.test(prefix) || prefix.length > 100) return { ok: false, error: t('The topic prefix may use letters, digits, - _ and /, without + or #.') };
  if (!(INTERVALS as readonly number[]).includes(f.interval_s)) return { ok: false, error: t('Send every 10, 30 or 60 seconds.') };
  return { ok: true, value: { host: host.toLowerCase(), port, username: f.username.trim(), password: f.password ? f.password : null,
                              tls: f.tls, prefix, interval_s: f.interval_s, discovery: f.discovery } };
}

/** Whether the form differs from what is saved (a typed password always counts). */
export const changed = (f: MqttForm, p: MqttPayload, site: string): boolean => {
  const s = formOf(p, site);
  return !p.enabled || !!f.password || f.host.trim().toLowerCase() !== s.host || f.port.trim() !== s.port || f.username.trim() !== s.username
    || f.tls !== s.tls || f.prefix.trim() !== s.prefix || f.interval_s !== s.interval_s || f.discovery !== s.discovery;
};

/** One sentence about the connection, with a tone for its colour. */
export function statusLine(p: Pick<MqttPayload, 'enabled' | 'host' | 'port' | 'status'>): { text: string; tone: Tone } {
  const s = p.status;
  if (!p.enabled || s.state === 'off') return { text: t('Off. Nothing is sent.'), tone: 'info' };
  if (s.state === 'connected') {
    return {
      text: s.last_publish_age_s == null ? t('Connected to {broker}', { broker: `${p.host}:${p.port}` })
        : t('Connected to {broker} · last message {n} s ago · {sent} sent', { broker: `${p.host}:${p.port}`, n: Math.round(s.last_publish_age_s), sent: s.sent.toLocaleString() }),
      tone: 'ok',
    };
  }
  if (s.state === 'error') return { text: t('Not connected: {error}. Retrying.', { error: s.error ? t(s.error) : '?' }), tone: 'bad' };
  return { text: t('Connecting to {broker}…', { broker: `${p.host}:${p.port}` }), tone: 'info' };
}
