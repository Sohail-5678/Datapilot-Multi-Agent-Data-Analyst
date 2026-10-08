import "@testing-library/jest-dom/vitest";

if (!globalThis.crypto?.randomUUID) {
  // jsdom in older Node lacks randomUUID
  Object.defineProperty(globalThis, "crypto", { value: { randomUUID: () => Math.random().toString(36).slice(2) } });
}
