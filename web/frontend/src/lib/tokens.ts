// The one set of colour tokens. main.tsx publishes them as CSS variables (--c-<name>)
// so the SVG diagram, CSS and uPlot charts all read the same values.
export const colors = {
  grid: '#5fd3ee',
  gridAlt: '#2f8fae',
  battery: '#25c28a',
  batteryAlt: '#9be8c6',
  load: '#b863e0',
  loadAlt: '#d8a6f0',
  pv: '#f4c430',
  inverter: '#e5a823',
  inactive: '#4b4e55',
  bg: '#141517',
  surface: '#1d1e21',
  surface2: '#26282c',
  border: '#33363b',
  text: '#f2f3f5',
  muted: '#9a9da5',
  ok: '#6fcf46',
  okBg: '#20361d',
  warn: '#f39c32',
  danger: '#ef5350',
  dangerBg: '#3d1c1c',
} as const;

export type ColorToken = keyof typeof colors;

export function applyColorTokens(root: HTMLElement = document.documentElement) {
  for (const [name, value] of Object.entries(colors)) {
    root.style.setProperty(`--c-${name.replace(/[A-Z]/g, (c) => '-' + c.toLowerCase())}`, value);
  }
}
