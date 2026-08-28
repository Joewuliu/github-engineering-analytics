import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { createFetchMock, jsonResponse } from "./testUtils";

const user = {
  id: 1,
  github_id: 100,
  github_login: "octocat",
  created_at: "2024-01-01T00:00:00Z",
  updated_at: "2024-01-01T00:00:00Z",
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("App", () => {
  it("shows the landing page when /auth/me returns 401", async () => {
    vi.stubGlobal(
      "fetch",
      createFetchMock([
        { method: "GET", path: "/auth/me", respond: () => jsonResponse(401, { detail: "Not authenticated." }) },
      ])
    );

    render(<App />);

    expect(await screen.findByText("Sign in with GitHub")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Sign in with GitHub" })).toHaveAttribute(
      "href",
      "/auth/github/login"
    );
  });

  it("shows the dashboard when /auth/me returns 200", async () => {
    vi.stubGlobal(
      "fetch",
      createFetchMock([
        { method: "GET", path: "/auth/me", respond: () => jsonResponse(200, user) },
        { method: "GET", path: "/me/repositories", respond: () => jsonResponse(200, []) },
      ])
    );

    render(<App />);

    expect(await screen.findByText("octocat")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
  });
});
