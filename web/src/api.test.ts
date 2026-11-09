import { describe, expect, it, vi } from "vitest";

import { listFiles, pair } from "./api";

describe("same-origin API client", () => {
  it("pairs a browser without exposing a token", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ session: { expires_at: "2026-11-09T00:00:00Z", scope: "read_write" } }), { status: 201 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(pair("123456")).resolves.toMatchObject({ session: { scope: "read_write" } });
    expect(fetchMock).toHaveBeenCalledWith("/api/pairings", expect.objectContaining({ credentials: "same-origin", method: "POST" }));
    expect(fetchMock.mock.calls[0][1].body).not.toContain("access_token");
  });

  it("uses same-origin credentials for protected file listing", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ items: [], next_cursor: null }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(listFiles()).resolves.toEqual({ items: [], next_cursor: null });
    expect(fetchMock).toHaveBeenCalledWith("/api/files", expect.objectContaining({ credentials: "same-origin" }));
  });
});
