import js from "@eslint/js";
import tsPlugin from "@typescript-eslint/eslint-plugin";
import tsParser from "@typescript-eslint/parser";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";

// Globals available in both runtimes. Declared rather than pulled from the
// `globals` package, which would be a dependency for a handful of names.
const SHARED_GLOBALS = {
  URL: "readonly",
  URLSearchParams: "readonly",
  console: "readonly",
  fetch: "readonly",
  setTimeout: "readonly",
  clearTimeout: "readonly",
  setInterval: "readonly",
  clearInterval: "readonly",
};

const BROWSER_GLOBALS = {
  ...SHARED_GLOBALS,
  document: "readonly",
  window: "readonly",
  localStorage: "readonly",
  sessionStorage: "readonly",
  EventSource: "readonly",
  File: "readonly",
  FormData: "readonly",
  HTMLElement: "readonly",
  HTMLInputElement: "readonly",
};

const NODE_GLOBALS = {
  ...SHARED_GLOBALS,
  process: "readonly",
};

const TS_LANGUAGE_OPTIONS = {
  parser: tsParser,
  ecmaVersion: 2022,
  sourceType: "module",
  parserOptions: { ecmaFeatures: { jsx: true } },
};

export default [
  { ignores: ["dist", "node_modules"] },
  js.configs.recommended,

  // Application code, which runs in the browser.
  {
    files: ["src/**/*.{ts,tsx}"],
    languageOptions: { ...TS_LANGUAGE_OPTIONS, globals: BROWSER_GLOBALS },
    plugins: {
      "@typescript-eslint": tsPlugin,
      "react-hooks": reactHooks,
      "react-refresh": reactRefresh,
    },
    rules: {
      ...tsPlugin.configs.recommended.rules,
      ...reactHooks.configs.recommended.rules,
      "react-refresh/only-export-components": ["warn", { allowConstantExport: true }],
      "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_" }],
    },
  },

  // Build and tooling config, which runs in Node.
  {
    files: ["*.config.{ts,js}", "*.config.mjs"],
    languageOptions: { ...TS_LANGUAGE_OPTIONS, globals: NODE_GLOBALS },
    plugins: { "@typescript-eslint": tsPlugin },
    rules: { ...tsPlugin.configs.recommended.rules },
  },
];
