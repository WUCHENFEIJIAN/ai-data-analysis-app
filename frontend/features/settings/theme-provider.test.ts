import { describe, expect, it } from "vitest";

import { resolveTheme } from "./theme-provider";

describe("resolveTheme", () => {
  it("uses the selected light or dark mode", () => {
    expect(resolveTheme("light", true)).toBe("light");
    expect(resolveTheme("dark", false)).toBe("dark");
  });

  it("follows the operating system when system mode is selected", () => {
    expect(resolveTheme("system", true)).toBe("dark");
    expect(resolveTheme("system", false)).toBe("light");
  });
});
