import { describe, expect, it } from 'vitest';
import { changed, defaultPrefix, formOf, parseMqtt, statusLine, type MqttForm, type MqttPayload } from './mqtt';

const P: MqttPayload = {
  enabled: true, host: '192.168.0.20', port: 1883, username: 'solis', password_set: true, tls: false, prefix: 'solis/home', interval_s: 10,
  discovery: true, source: 'saved', status: { state: 'connected', error: null, last_publish_age_s: 3.6, sent: 1284, dropped: 0 },
  topics: { state: 'solis/home/state', event: 'solis/home/event', availability: 'solis/home/status', discovery: null }, last_message: null,
  web: { can_change: true, reason: null },
};
const F: MqttForm = { host: '192.168.0.20', port: '1883', username: 'solis', password: '', tls: false, prefix: 'solis/home', interval_s: 10, discovery: true };

describe('MQTT settings', () => {
  it('names the topics after the site', () => {
    expect(defaultPrefix('Home')).toBe('solis/home');
    expect(defaultPrefix('Romankiv 2')).toBe('solis/romankiv-2');
    expect(defaultPrefix('Дача')).toBe('solis/home');
    expect(formOf({ ...P, prefix: '', port: 0 }, 'Home')).toMatchObject({ prefix: 'solis/home', port: '1883', password: '' });
  });

  it('checks the form like the poller does', () => {
    expect(parseMqtt(F)).toEqual({ ok: true, value: { host: '192.168.0.20', port: 1883, username: 'solis', password: null, tls: false, prefix: 'solis/home', interval_s: 10, discovery: true } });
    expect(parseMqtt({ ...F, password: 'x', prefix: '/solis/home/' })).toMatchObject({ ok: true, value: { password: 'x', prefix: 'solis/home' } });
    for (const bad of [{ host: '' }, { host: 'mqtt://b' }, { port: '0' }, { port: 'x' }, { prefix: 'solis/#' }, { interval_s: 5 }]) {
      expect(parseMqtt({ ...F, ...bad }).ok).toBe(false);
    }
  });

  it('only offers Save for a real change, and always to turn it on', () => {
    expect(changed(F, P, 'Home')).toBe(false);
    expect(changed({ ...F, password: 'new' }, P, 'Home')).toBe(true);
    expect(changed({ ...F, interval_s: 30 }, P, 'Home')).toBe(true);
    expect(changed(F, { ...P, enabled: false }, 'Home')).toBe(true);
  });

  it('says what the connection is doing', () => {
    expect(statusLine(P)).toEqual({ text: 'Connected to 192.168.0.20:1883 · last message 4 s ago · 1,284 sent', tone: 'ok' });
    expect(statusLine({ ...P, enabled: false }).tone).toBe('info');
    expect(statusLine({ ...P, status: { ...P.status, state: 'error', error: 'Wrong username or password' } }))
      .toEqual({ text: 'Not connected: Wrong username or password. Retrying.', tone: 'bad' });
    expect(statusLine({ ...P, status: { ...P.status, state: 'connecting' } }).text).toBe('Connecting to 192.168.0.20:1883…');
  });
});
