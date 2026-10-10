import path from "path";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    setupFiles: ["./tests/setup.ts"],
    globals: true,
    exclude: ["**/node_modules/**", "**/e2e/**"], // e2e/ is Playwright's own suite, run separately
  },
  resolve: {
    alias: { "@": path.resolve(__dirname, "./") },
  },
});
