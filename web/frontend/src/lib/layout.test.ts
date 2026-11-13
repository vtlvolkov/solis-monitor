import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import {
  addCard, CARD_IDS, defaultLayout, dropIndex, moveCard, nearestColumn, normalize, removeCard, sameLayout, stepCard, toSave,
} from './layout';

const LAYOUT_PY = join(__dirname, '..', '..', '..', 'backend', 'app', 'layout.py');

describe('the Overview layout', () => {
  it.skipIf(!existsSync(LAYOUT_PY))('knows exactly the cards the server accepts', () => {
    const py = readFileSync(LAYOUT_PY, 'utf8');
    const tuple = py.slice(py.indexOf('CARD_IDS = ('), py.indexOf(')', py.indexOf('CARD_IDS = (')));
    expect([...tuple.matchAll(/"([a-z]+)"/g)].map((m) => m[1])).toEqual(CARD_IDS);
  });

  it('starts as the page was, with Planned outages only when its setting showed it', () => {
    expect(defaultLayout()).toEqual({ left: ['flow', 'battery', 'gauges'], right: ['grid', 'outages', 'schedule', 'today', 'cost'] });
    expect(defaultLayout(false).right).not.toContain('outages');
  });

  it('drops unknown cards and repeats from a saved layout', () => {
    expect(normalize({ left: ['flow', 'nope', 'flow'], right: ['flow', 'heatmap', 3] })).toEqual({ left: ['flow'], right: ['heatmap'] });
    expect(normalize(null)).toBeNull();
    expect(normalize({ left: [] })).toBeNull();
  });

  it('moves a card within a column and to the other one', () => {
    const l = defaultLayout();
    expect(moveCard(l, 'gauges', 'left', 0).left).toEqual(['gauges', 'flow', 'battery']);
    const m = moveCard(l, 'battery', 'right', 1);
    expect(m.left).toEqual(['flow', 'gauges']);
    expect(m.right).toEqual(['grid', 'battery', 'outages', 'schedule', 'today', 'cost']);
    expect(moveCard(l, 'flow', 'right', 99).right.at(-1)).toBe('flow');
  });

  it('adds a card at the end of its usual column, once', () => {
    const l = addCard(defaultLayout(), 'heatmap');
    expect(l.left.at(-1)).toBe('heatmap');
    expect(addCard(l, 'heatmap')).toBe(l);
    expect(addCard(l, 'selfsufficiency').right.at(-1)).toBe('selfsufficiency');
    expect(removeCard(l, 'heatmap')).toEqual(defaultLayout());
  });

  it('steps a card with the arrow keys and stops at the ends', () => {
    const l = defaultLayout();
    expect(stepCard(l, 'flow', 'ArrowUp')).toBeNull();
    expect(stepCard(l, 'flow', 'ArrowDown')!.left).toEqual(['battery', 'flow', 'gauges']);
    expect(stepCard(l, 'cost', 'ArrowDown')).toBeNull();
    expect(stepCard(l, 'battery', 'ArrowRight')!.right.indexOf('battery')).toBe(1);
    expect(stepCard(l, 'battery', 'ArrowLeft')).toBeNull();
    expect(stepCard(l, 'grid', 'ArrowLeft')!.left[0]).toBe('grid');
  });

  it('finds the drop place from the cards’ middles and the nearest column', () => {
    expect(dropIndex([100, 300, 500], 50)).toBe(0);
    expect(dropIndex([100, 300, 500], 301)).toBe(2);
    expect(dropIndex([], 10)).toBe(0);
    const cols = [{ col: 'left' as const, left: 0, right: 500, top: 0, bottom: 900 }, { col: 'right' as const, left: 530, right: 1200, top: 0, bottom: 700 }];
    expect(nearestColumn(cols, 515, 100)).toBe('left');
    expect(nearestColumn(cols, 900, 800)).toBe('right');
    // a phone: one column under the other
    const phone = [{ col: 'left' as const, left: 0, right: 400, top: 0, bottom: 1200 }, { col: 'right' as const, left: 0, right: 400, top: 1214, bottom: 2600 }];
    expect(nearestColumn(phone, 200, 1300)).toBe('right');
  });

  it('saves the default as null, so a later default still applies', () => {
    expect(toSave(defaultLayout(), true)).toBeNull();
    expect(toSave(defaultLayout(false), false)).toBeNull();
    const mine = addCard(defaultLayout(), 'events');
    expect(toSave(mine, true)).toEqual(mine);
    expect(sameLayout(mine, defaultLayout())).toBe(false);
  });
});
