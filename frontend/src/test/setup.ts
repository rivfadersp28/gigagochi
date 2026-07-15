import "@testing-library/jest-dom/vitest";

globalThis.fetch = (() =>
  Promise.reject(
    new Error("External network is disabled in frontend tests; stub fetch explicitly."),
  )) as typeof fetch;
