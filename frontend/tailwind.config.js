/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      // Every colour resolves through a token so both themes stay in step.
      colors: {
        ground: "var(--ground)",
        surface: "var(--surface)",
        "surface-sunken": "var(--surface-sunken)",
        rule: "var(--rule)",
        "rule-strong": "var(--rule-strong)",
        ink: "var(--ink)",
        "ink-muted": "var(--ink-muted)",
        "ink-faint": "var(--ink-faint)",
        accent: "var(--accent)",
        "accent-hover": "var(--accent-hover)",
        "accent-soft": "var(--accent-soft)",
        "on-accent": "var(--on-accent)",
        "sev-critical": "var(--sev-critical)",
        "sev-critical-soft": "var(--sev-critical-soft)",
        "sev-high": "var(--sev-high)",
        "sev-high-soft": "var(--sev-high-soft)",
        "sev-medium": "var(--sev-medium)",
        "sev-medium-soft": "var(--sev-medium-soft)",
        "sev-low": "var(--sev-low)",
        "sev-low-soft": "var(--sev-low-soft)",
      },
      fontFamily: {
        display: "var(--font-display)",
        body: "var(--font-body)",
        mono: "var(--font-mono)",
      },
      borderRadius: {
        DEFAULT: "var(--radius)",
        lg: "var(--radius-lg)",
      },
    },
  },
  plugins: [],
};
