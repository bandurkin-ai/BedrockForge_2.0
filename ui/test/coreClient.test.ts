import { describe, expect, it } from "vitest";
import { apiUrl, authHeaders, fetchHealth } from "../src/shell/coreClient";

const t = { baseUrl: "http://127.0.0.1:5555/", token: "tok" };

describe("core client", () => {
  it("builds /api/v1 urls", () => {
    expect(apiUrl(t, "health")).toBe("http://127.0.0.1:5555/api/v1/health");
    expect(apiUrl(t, "/health")).toBe("http://127.0.0.1:5555/api/v1/health");
  });

  it("sends the launch token", async () => {
    expect(authHeaders(t)["X-BF-Token"]).toBe("tok");
    let seen: Headers | null = null;
    const fake = (async (_u: string, init?: RequestInit) => {
      seen = new Headers(init?.headers);
      return new Response(JSON.stringify({ status: "ok", version: "0.0.1", mode: "desktop" }));
    }) as unknown as typeof fetch;
    const h = await fetchHealth(t, fake);
    expect(h.status).toBe("ok");
    expect(seen!.get("X-BF-Token")).toBe("tok");
  });
});
