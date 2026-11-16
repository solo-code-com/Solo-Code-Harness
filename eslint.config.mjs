import globals from "globals";

export default [
  {
    files: [".kilo/hooks/**/*.js"
    ],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
      globals: {
        ...globals.node,
      },
    },
    rules: {
      "no-unused-vars": ["warn", { argsIgnorePattern: "^_" }],
      "no-console": ["warn", { allow: ["warn", "error"] }],
      "no-debugger": "error",
      "no-eval": "error",
      "no-implied-eval": "error",
      "prefer-const": "error",
      "eqeqeq": ["error", "always"],
      "no-var": "error",
    },
  },
];
