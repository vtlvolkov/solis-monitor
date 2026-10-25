// Line icons for the diagram, drawn around (0,0) in ~60x60 viewBox units, stroke = currentColor.
const common = { fill: 'none', stroke: 'currentColor', 'stroke-width': 3, 'stroke-linecap': 'round', 'stroke-linejoin': 'round' } as const;

export const Icon = {
  pv: () => (
    <g {...common}>
      <circle cx={-12} cy={-12} r={7} />
      <path d="M-12,-26 v4 M-26,-12 h4 M-22,-22 l3,3 M-2,-22 l-3,3 M-22,-2 l3,-3" />
      <path d="M-8,0 L22,0 L28,22 L-14,22 Z M-11,11 H25 M7,0 L7,22" />
    </g>
  ),
  grid: () => (
    <g {...common}>
      <path d="M0,-26 L-12,24 M0,-26 L12,24 M-20,-14 H20 M-16,-2 H16 M-22,-14 v6 M22,-14 v6 M-8,10 L8,24 M8,10 L-8,24 M-6,-14 L6,-2 M6,-14 L-6,-2" />
    </g>
  ),
  house: () => (
    <g {...common}>
      <path d="M-22,-2 L0,-22 L22,-2 M-17,-6 V22 H17 V-6" />
      <path d="M3,-6 L-5,7 H5 L-3,18" />
    </g>
  ),
  backup: () => (
    <g {...common}>
      <path d="M-22,-2 L0,-22 L22,-2 M-17,-6 V22 H17 V-6" />
      <path d="M-7,16 C-9,4 0,-3 9,-3 C10,8 3,15 -7,16 Z M-7,16 L3,6" />
    </g>
  ),
  battery: ({ soc, charging }: { soc: number | null | undefined; charging: boolean }) => (
    <g class="soc-badge">
      <rect x={-38} y={-16} width={58} height={30} rx={4} fill="none" stroke="currentColor" stroke-width={3} />
      <rect x={21} y={-6} width={5} height={10} rx={1} fill="currentColor" />
      <text x={-9} y={7} class="soc-text" data-testid="soc-badge">{soc == null ? '--' : `${Math.round(soc)}%`}</text>
      {charging && <path d="M36,-14 L29,1 H36 L31,14" fill="none" stroke="currentColor" stroke-width={3}
                          stroke-linecap="round" stroke-linejoin="round" data-testid="charging-bolt" />}
    </g>
  ),
  inverter: () => (
    <g fill="none" stroke="var(--c-inverter)" stroke-width={4} stroke-linecap="round" stroke-linejoin="round">
      <rect x={-30} y={-40} width={60} height={76} rx={6} />
      <path d="M4,-26 L-7,-6 H6 L-4,10" />
      <path d="M-16,22 H16" />
      <path d="M-20,36 v6 M20,36 v6" />
    </g>
  ),
};
