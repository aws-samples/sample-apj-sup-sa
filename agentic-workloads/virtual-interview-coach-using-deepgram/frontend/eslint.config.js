import js from "@eslint/js";

export default [
  js.configs.recommended,
  {
    ignores: ["dist/", "build/", "node_modules/"],
  },
  {
    files: ["**/*.{ts,tsx}"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
    },
  },
];
