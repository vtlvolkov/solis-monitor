// Debug tab: types and pure helpers for reading registers by hand (unit-tested). Reads only; nothing here writes.
import { t, tx } from './i18n';
import type { Parsed } from './tariff';

export const MAX_COUNT = 50;                    // the Solis protocol's limit per request
export type FunctionCode = 3 | 4;
export const FUNCTIONS: { code: FunctionCode; label: string }[] = [
  { code: 4, label: tx('Input registers (function 04)') },
  { code: 3, label: tx('Holding registers (function 03)') },
];

export interface ReadRequest { function: FunctionCode; address: number; count: number }

export interface DebugReply {
  ok: boolean; function: number; function_name: string; address: number; count: number; unit: number; ms: number;
  request: string; response: string;
  registers?: number[]; exception_code?: number; exception?: string;
}

/** Messages the poller sends in English; listed so each has a Ukrainian text. */
export const SERVER_TEXTS = [
  tx('The function must be 3 (holding registers) or 4 (input registers).'),
  tx('The address must be a whole number from 0 to 65535.'),
  tx('The count must be a whole number from 1 to 50.'),
  tx('The range goes past register 65535.'),
  tx('Changes need WEB_BASIC_AUTH to be set.'),                   // from the web service, when no dashboard password is configured
  // Modbus exception names, as the poller sends them
  tx('Illegal function'), tx('Illegal data address'), tx('Illegal data value'), tx('Server device failure'), tx('Acknowledge'),
  tx('Server device busy'), tx('Memory parity error'), tx('Gateway path unavailable'), tx('Gateway target device failed to respond'),
];

/** An address as typed: decimal (33049) or hexadecimal (0x8119). */
export function parseAddress(text: string): number | null {
  const s = text.trim();
  const n = /^0x[0-9a-f]{1,4}$/i.test(s) ? parseInt(s, 16) : /^\d{1,5}$/.test(s) ? Number(s) : NaN;
  return n >= 0 && n <= 65535 ? n : null;
}

/** Validates the form like the poller does (function 3 or 4, address 0-65535, count 1-50, the range inside 0-65535). */
export function parseRead(fn: number, address: string, count: string): Parsed<ReadRequest> {
  if (fn !== 3 && fn !== 4) return { ok: false, error: t('The function must be 3 (holding registers) or 4 (input registers).') };
  const a = parseAddress(address);
  if (a === null) return { ok: false, error: t('The address must be a whole number from 0 to 65535.') };
  const n = /^\d{1,3}$/.test(count.trim()) ? Number(count.trim()) : 0;
  if (n < 1 || n > MAX_COUNT) return { ok: false, error: t('The count must be a whole number from 1 to 50.') };
  if (a + n > 65536) return { ok: false, error: t('The range goes past register 65535.') };
  return { ok: true, value: { function: fn, address: a, count: n } };
}

export interface Row {
  address: number; hex: string; u16: number; s16: number;
  text: string;                                  // the two bytes as ASCII, "·" for anything unprintable
  u32: number | null; s32: number | null;        // with the next register, high word first (null on the last row)
}

const ascii = (v: number) => [v >> 8, v & 0xff].map((b) => (b >= 0x20 && b < 0x7f ? String.fromCharCode(b) : '·')).join('');

export function rows(address: number, regs: number[]): Row[] {
  return regs.map((v, i) => {
    const next = regs[i + 1];
    const u32 = next === undefined ? null : v * 65536 + next;
    return {
      address: address + i, hex: `0x${v.toString(16).toUpperCase().padStart(4, '0')}`, u16: v, s16: v >= 0x8000 ? v - 0x10000 : v,
      text: ascii(v), u32, s32: u32 === null ? null : u32 >= 0x80000000 ? u32 - 0x100000000 : u32,
    };
  });
}

/** The rows as tab-separated text, for pasting into a message or a spreadsheet. */
export function asText(reply: DebugReply): string {
  const head = `FC${String(reply.function).padStart(2, '0')} unit ${reply.unit} ${reply.address}..${reply.address + reply.count - 1} (${reply.ms} ms)`;
  const lines = [head, `> ${reply.request}`, `< ${reply.response}`];
  if (reply.ok && reply.registers) {
    lines.push(['address', 'hex', 'u16', 's16', 'text', 'u32', 's32'].join('\t'));
    for (const r of rows(reply.address, reply.registers)) lines.push([r.address, r.hex, r.u16, r.s16, r.text, r.u32 ?? '', r.s32 ?? ''].join('\t'));
  } else {
    lines.push(`exception ${reply.exception_code}: ${reply.exception}`);
  }
  return lines.join('\n');
}
