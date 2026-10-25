import { RANGES, type Range } from './api';

/** The TV page is laid out on a fixed 1920x1080 stage that is scaled to fit the window. */
export const STAGE_W = 1920;
export const STAGE_H = 1080;

export const stageFit = (w: number, h: number) => {
  const scale = Math.min(w / STAGE_W, h / STAGE_H);
  return { scale, x: (w - STAGE_W * scale) / 2, y: (h - STAGE_H * scale) / 2 };
};

/** Remote control: left/right steps the chart range. */
export const stepRange = (r: Range, dir: 1 | -1): Range =>
  RANGES[Math.min(RANGES.length - 1, Math.max(0, RANGES.indexOf(r) + dir))];
