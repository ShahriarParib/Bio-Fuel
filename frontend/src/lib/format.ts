/** Number/label formatting used everywhere (22_UI_SPEC.md §1.4). */
export const liters = (v: number | null | undefined): string =>
  v == null || Number.isNaN(v) ? "—" : `${Math.round(v).toLocaleString("en-US")} L`;

export const pct = (v: number | null | undefined, digits = 1): string =>
  v == null || Number.isNaN(v) ? "—" : `${(v * 100).toFixed(digits)}%`;

export const hours = (v: number | null | undefined): string =>
  v == null || !Number.isFinite(v) ? "> horizon" : v < 1 ? `${Math.round(v * 60)} min` : `${v.toFixed(1)} h`;

/** "Day 2 11:00 · t=140" — must match fsip_common.timeutil.format_sim_clock */
export const simClock = (tick: number, tickMinutes = 15): string => {
  const minutes = tick * tickMinutes;
  const day = Math.floor(minutes / 1440);
  const rem = minutes % 1440;
  const hh = String(Math.floor(rem / 60)).padStart(2, "0");
  const mm = String(rem % 60).padStart(2, "0");
  return `Day ${day + 1} ${hh}:${mm} · t=${tick}`;
};
