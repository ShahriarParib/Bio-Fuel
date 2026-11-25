/** Design tokens.
 *
 * A charcoal, near-monochrome console. The chrome - backgrounds, panels, borders, type, the accent - is built
 * entirely from greys, so the screen reads like a precision instrument rather than a dashboard.
 *
 * Colour is reserved for meaning. An operator at 3 a.m. has to tell "fine" from "about to run dry" at a glance,
 * so the risk and state tokens keep their hue, but desaturated far enough to sit inside a grey field without
 * shouting: they register as signals on an instrument, not as decoration. The three fuels are near-grey tints,
 * distinguishable side by side in a bar chart without turning the network view into confetti.
 */
/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        bg: {
          DEFAULT: "#0A0A0B",   // the room
          panel: "#121214",     // a card on it
          raised: "#1A1A1E",    // something inside the card
          border: "#2A2A30",    // hairline
        },
        ink: {
          DEFAULT: "#F4F4F5",
          muted: "#9E9EA7",
          faint: "#6A6A74",
        },
        // the accent is white: on charcoal, the brightest thing on screen is the thing you act on
        brand: { DEFAULT: "#E8E8EC", soft: "#26262C" },
        risk: {
          low: "#7FA98C",
          medium: "#C9B069",
          high: "#C78D63",
          critical: "#D4717A",
        },
        fuel: { diesel: "#E6E6EA", petrol: "#9C9CA6", octane: "#5E5E6A" },
        state: { healthy: "#7FA98C", degraded: "#C9B069", down: "#D4717A", unknown: "#6A6A74" },
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "Segoe UI", "Roboto", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "SFMono-Regular", "Consolas", "monospace"],
      },
      boxShadow: {
        panel: "0 1px 0 0 rgba(255,255,255,0.03) inset, 0 8px 24px -16px rgba(0,0,0,0.9)",
      },
      letterSpacing: {
        label: "0.08em",
      },
    },
  },
  plugins: [],
};
