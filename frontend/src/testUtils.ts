import { vi } from "vitest";

export function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

export function noContentResponse(): Response {
  return new Response(null, { status: 204 });
}

interface FetchHandler {
  method: string;
  path: string | RegExp;
  respond: (init?: RequestInit) => Response | Promise<Response>;
}

/** A small, declarative `fetch` mock: routes on (method, path) so tests read
 * as "what does the backend return for this call" rather than asserting on
 * fetch's call arguments directly. */
export function createFetchMock(handlers: FetchHandler[]) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    const handler = handlers.find(
      (candidate) =>
        candidate.method === method &&
        (typeof candidate.path === "string" ? candidate.path === url : candidate.path.test(url))
    );
    if (!handler) {
      throw new Error(`Unmocked request: ${method} ${url}`);
    }
    return handler.respond(init);
  });
}
