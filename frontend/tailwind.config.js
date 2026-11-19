/** Design tokens: see 22_UI_SPEC.md §1. Keep colours in sync with that file. */
/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        bg: { DEFAULT: "#0B1220", panel: "#111A2E", raised: "#17223A", border: "#24314F" },
        ink: { DEFAULT: "#E6EDF7", muted: "#93A4C3", faint: "#5B6B8C" },
        brand: { DEFAULT: "#3B82F6", soft: "#1E3A6B" },
        risk: { low: "#22C55E", medium: "#EAB308", high: "#F97316", critical: "#EF4444" },
        fuel: { diesel: "#38BDF8", petrol: "#A78BFA", octane: "#F472B6" },
        state: { healthy: "#22C55E", degraded: "#EAB308", down: "#EF4444", unknown: "#64748B" },
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "Segoe UI", "Roboto", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "SFMono-Regular", "monospace"],
      },
    },
  },
  plugins: [],
};
