import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, apiBaseUrl, apiRequest } from "./api";

afterEach(() => vi.restoreAllMocks());

describe("apiRequest", () => {
  it("returns typed JSON responses", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ status: "ok" }), { status: 200 }));
    await expect(apiRequest<{ status: string }>("/health")).resolves.toEqual({ status: "ok" });
  });

  it("uses the safe API error message", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ error: { message: "bad input" } }), { status: 422 }));
    await expect(apiRequest("/broken")).rejects.toEqual(new ApiError(422, "bad input"));
  });

  it("accepts successful empty responses", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(null, { status: 204 }));
    await expect(apiRequest<void>("/projects/id", { method: "DELETE" })).resolves.toBeUndefined();
  });
});

describe("apiBaseUrl", () => {
  it("uses the browser hostname for local development", () => {
    expect(apiBaseUrl({ protocol: "http:", hostname: "127.0.0.1" })).toBe(
      "http://127.0.0.1:8000/api",
    );
    expect(apiBaseUrl({ protocol: "http:", hostname: "localhost" })).toBe(
      "http://localhost:8000/api",
    );
  });

  it("uses the same origin API route outside local development", () => {
    expect(apiBaseUrl({ protocol: "https:", hostname: "example.vercel.app" })).toBe(
      "https://example.vercel.app/api",
    );
  });
});
