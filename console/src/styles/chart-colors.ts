/**
 * Google Design Library chart color palettes.
 * Use these arrays for ECharts, Recharts, or any custom CSS/SVG data visualization.
 */

export const chartColorsLight = [
  '#4285f4',
  '#ea4335',
  '#fbbc05',
  '#0043ad',
  '#34a853',
];

export const chartColorsDark = [
  '#2dccd3',
  '#f1204a',
  '#edbbe8',
  '#fbeb35',
  '#baf6f0',
];

/**
 * Returns the appropriate palette for the current resolved theme.
 * Detects the `.dark` class on documentElement by default.
 */
export function getChartColors(isDark?: boolean): string[] {
  const dark = isDark ?? document.documentElement.classList.contains('dark');
  return dark ? chartColorsDark : chartColorsLight;
}

/**
 * Named semantic chart colors for explicit use.
 */
export const chartColorNames = {
  primary: chartColorsLight[0],
  danger: chartColorsLight[1],
  warning: chartColorsLight[2],
  accent: chartColorsLight[3],
  success: chartColorsLight[4],
} as const;
