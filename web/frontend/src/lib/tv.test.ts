import { describe, expect, it } from 'vitest';
import { stageFit, stepRange } from './tv';

describe('TV stage', () => {
  it('fits the 1920x1080 stage into the window and centres it', () => {
    expect(stageFit(1920, 1080)).toEqual({ scale: 1, x: 0, y: 0 });
    expect(stageFit(960, 540)).toEqual({ scale: 0.5, x: 0, y: 0 });
    const tall = stageFit(1920, 800);           // height-limited: bars left and right
    expect(tall.scale).toBeCloseTo(800 / 1080);
    expect(tall.x).toBeCloseTo((1920 - 1920 * (800 / 1080)) / 2);
    expect(tall.y).toBe(0);
  });

  it('steps the chart range with the remote and stops at the ends', () => {
    expect(stepRange('6h', 1)).toBe('24h');
    expect(stepRange('6h', -1)).toBe('1h');
    expect(stepRange('1h', -1)).toBe('1h');
    expect(stepRange('30d', 1)).toBe('30d');
  });
});
