import { useEffect, useRef, useState } from 'preact/hooks';

export function prefersReducedMotion(): boolean {
  return typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;
}

/** Smoothly animates towards `target` (ease-out), or jumps when reduced motion is preferred. */
export function useTweened(target: number | null | undefined, ms = 900): number | null {
  const [value, setValue] = useState<number | null>(target ?? null);
  const fromRef = useRef<number | null>(target ?? null);
  const frame = useRef<number>();

  useEffect(() => {
    if (target == null || !Number.isFinite(target)) {
      fromRef.current = null;
      setValue(null);
      return;
    }
    const from = fromRef.current;
    if (from == null || prefersReducedMotion()) {
      fromRef.current = target;
      setValue(target);
      return;
    }
    const start = performance.now();
    const step = (t: number) => {
      const k = Math.min(1, (t - start) / ms);
      const v = from + (target - from) * (1 - Math.pow(1 - k, 3));
      fromRef.current = v;
      setValue(v);
      if (k < 1) frame.current = requestAnimationFrame(step);
    };
    frame.current = requestAnimationFrame(step);
    return () => cancelAnimationFrame(frame.current!);
  }, [target, ms]);

  return value;
}
