// Frontend linting. Scoped to eslint-plugin-sonarjs, which mirrors SonarCloud's
// JS/TS rules (same S#### keys) — so the linter lines up with what SonarCloud
// reports and catches these smells before code reaches the server.
import sonarjs from "eslint-plugin-sonarjs";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist/**", "node_modules/**"] },
  {
    files: ["src/**/*.{ts,tsx}"],
    extends: [sonarjs.configs.recommended],
    languageOptions: {
      parser: tseslint.parser,
      parserOptions: { ecmaFeatures: { jsx: true } },
    },
    rules: {
      // Refactor-heavy rules are surfaced as warnings for now (tracked for the
      // "grind" pass) so the lint gate stays green while we work through them.
      "sonarjs/no-nested-conditional": "warn",
      "sonarjs/no-nested-template-literals": "warn",
      "sonarjs/cognitive-complexity": "warn",
      "sonarjs/use-type-alias": "warn",
    },
  },
);
