import { describe, expect, it } from 'vitest';
import { asText, parseAddress, parseRead, rows, type DebugReply } from './debug';

describe('address as typed', () => {
  it('takes decimal and 0x hexadecimal', () => {
    expect(parseAddress('33049')).toBe(33049);
    expect(parseAddress(' 0x8119 ')).toBe(0x8119);
    expect(parseAddress('0XFFFF')).toBe(65535);
    expect(parseAddress('0')).toBe(0);
  });
  it('refuses everything else', () => {
    for (const bad of ['', 'abc', '65536', '-1', '1.5', '0x10000', '0x', '8119h', '33 049', '1e3']) expect(parseAddress(bad), bad).toBeNull();
  });
});

describe('read form', () => {
  it('accepts function 3 and 4 within the limits', () => {
    expect(parseRead(4, '33000', '50')).toEqual({ ok: true, value: { function: 4, address: 33000, count: 50 } });
    expect(parseRead(3, '0x0', '1')).toEqual({ ok: true, value: { function: 3, address: 0, count: 1 } });
    expect(parseRead(4, '65535', '1').ok).toBe(true);
  });
  it('refuses the rest, writes included', () => {
    for (const fn of [0, 1, 5, 6, 16]) expect(parseRead(fn, '1', '1').ok, String(fn)).toBe(false);
    for (const c of ['', '0', '51', '-1', '1.5', 'x', '1000']) expect(parseRead(4, '1', c).ok, c).toBe(false);
    expect(parseRead(4, 'nope', '1').ok).toBe(false);
    expect(parseRead(4, '65535', '2')).toEqual({ ok: false, error: 'The range goes past register 65535.' });
  });
});

describe('decoded rows', () => {
  it('shows hex, signed and unsigned values, ASCII and 32-bit pairs (high word first)', () => {
    const r = rows(33000, [0x4142, 0xffff, 0x0001, 0x86a0]);
    expect(r[0]).toMatchObject({ address: 33000, hex: '0x4142', u16: 16706, s16: 16706, text: 'AB' });
    expect(r[1]).toMatchObject({ hex: '0xFFFF', u16: 65535, s16: -1, text: '··' });
    expect(r[2]).toMatchObject({ u32: 0x000186a0, s32: 100000 });
    expect(r[3]).toMatchObject({ u32: null, s32: null });
  });
  it('reads a pair that is negative as 32 bits', () => {
    expect(rows(0, [0xffff, 0xfffe])[0]).toMatchObject({ u32: 4294967294, s32: -2 });
  });
});

describe('text copy', () => {
  const ok: DebugReply = { ok: true, function: 4, function_name: 'Read input registers', address: 33049, count: 2, unit: 1, ms: 412,
    request: '04 81 19 00 02', response: '04 04 08 FC FF FF', registers: [2300, 65535] };
  it('lists the frames and the rows', () => {
    const text = asText(ok).split('\n');
    expect(text[0]).toBe('FC04 unit 1 33049..33050 (412 ms)');
    expect(text.slice(1, 3)).toEqual(['> 04 81 19 00 02', '< 04 04 08 FC FF FF']);
    expect(text[4]).toBe('33049\t0x08FC\t2300\t2300\t··\t150798335\t150798335');
  });
  it('names a Modbus exception', () => {
    const bad: DebugReply = { ...ok, ok: false, registers: undefined, exception_code: 2, exception: 'Illegal data address', response: '84 02' };
    expect(asText(bad)).toContain('exception 2: Illegal data address');
  });
});
