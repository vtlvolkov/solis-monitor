import { describe, expect, it } from 'vitest';
import { connectionLine, guessNetwork, networkFrom, parseAddress } from './inverter';

const ok = (host: string, port = '502', unit = '1') => parseAddress(host, port, unit);

describe('address form', () => {
  it('accepts an IP address or host name and normalises it', () => {
    expect(ok(' 192.168.0.105 ')).toEqual({ ok: true, value: { host: '192.168.0.105', port: 502, unit: 1 } });
    expect(ok('Solis-Logger.local', '1502', '3')).toEqual({ ok: true, value: { host: 'solis-logger.local', port: 1502, unit: 3 } });
  });
  it('rejects what the poller would reject', () => {
    for (const bad of ['', '   ', '999.1.1.1', '1.2.3', '127.0.0.1', '0.0.0.0', '224.0.0.1', '169.254.1.1', 'http://192.168.0.1', '192.168.0.1:502', 'a b', '-x.local']) {
      expect(ok(bad).ok, bad).toBe(false);
    }
  });
  it('checks the port and the Modbus unit', () => {
    for (const port of ['0', '65536', '-1', '5.5', 'abc', '']) expect(ok('192.168.0.1', port).ok, port).toBe(false);
    for (const unit of ['0', '248', '1.5', 'x', '']) expect(ok('192.168.0.1', '502', unit).ok, unit).toBe(false);
    expect(ok('192.168.0.1', '65535', '247').ok).toBe(true);
  });
});

describe('network to scan', () => {
  it('turns an address into its /24 and refuses anything but home networks', () => {
    expect(networkFrom('192.168.0')).toBe('192.168.0');
    expect(networkFrom('192.168.0.0/24')).toBe('192.168.0');
    expect(networkFrom('10.0.5.77')).toBe('10.0.5');
    expect(networkFrom('172.20.1')).toBe('172.20.1');
    for (const bad of ['8.8.8.0', '172.32.0', '192.168.300', '169.254.1', '127.0.0', '192.168', 'umbrel.local', '']) expect(networkFrom(bad), bad).toBeNull();
  });
  it('prefers the server hint, then the address the page was opened on', () => {
    expect(guessNetwork('192.168.7', 'umbrel.local')).toBe('192.168.7');
    expect(guessNetwork(null, '192.168.0.110')).toBe('192.168.0');
    expect(guessNetwork(null, 'umbrel.local')).toBe('');
  });
});

describe('connection line', () => {
  const base = { configured: true, connected: false, last_ok_age_s: null, error: null, failures: 0 };
  it('says what is going on', () => {
    expect(connectionLine({ ...base, configured: false })).toEqual({ text: 'No address yet. The dashboard waits until you enter one.', tone: 'warn' });
    expect(connectionLine({ ...base, connected: true, last_ok_age_s: 4.4 })).toEqual({ text: 'Connected · last reading 4 s ago', tone: 'ok' });
    expect(connectionLine({ ...base, failures: 3, error: 'cannot connect to 192.168.0.105:502' })).toEqual({ text: 'Not reachable: cannot connect to 192.168.0.105:502', tone: 'bad' });
    expect(connectionLine(base)).toEqual({ text: 'Connecting…', tone: 'info' });
  });
});
