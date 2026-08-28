import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createFetchMock, jsonResponse, noContentResponse } from "../testUtils";
import type { User } from "../api/types";
import { DashboardPage } from "./DashboardPage";

const user: User = {
  id: 1,
  github_id: 100,
  github_login: "octocat",
  created_at: "2024-01-01T00:00:00Z",
  updated_at: "2024-01-01T00:00:00Z",
};

const trackedRepo = {
  id: 5,
  github_id: 500,
  full_name: "fastapi/fastapi",
  created_at: "2024-01-01T00:00:00Z",
  updated_at: "2024-01-01T00:00:00Z",
  tracked_at: "2024-01-02T00:00:00Z",
};

afterEach(() => {
  vi.unstubAllGlobals();
});

function renderDashboard(fetchMock: ReturnType<typeof createFetchMock>) {
  vi.stubGlobal("fetch", fetchMock);
  const onLogout = vi.fn();
  render(
    <MemoryRouter>
      <DashboardPage user={user} onLogout={onLogout} />
    </MemoryRouter>
  );
  return { onLogout };
}

describe("DashboardPage", () => {
  it("renders tracked repositories", async () => {
    renderDashboard(
      createFetchMock([
        { method: "GET", path: "/me/repositories", respond: () => jsonResponse(200, [trackedRepo]) },
      ])
    );

    expect(await screen.findByText("fastapi/fastapi")).toBeInTheDocument();
  });

  it("shows an empty state when nothing is tracked", async () => {
    renderDashboard(
      createFetchMock([{ method: "GET", path: "/me/repositories", respond: () => jsonResponse(200, []) }])
    );

    expect(await screen.findByText(/aren.t tracking/i)).toBeInTheDocument();
  });

  it("tracks a repository and shows it in the list", async () => {
    let repos: unknown[] = [];
    renderDashboard(
      createFetchMock([
        { method: "GET", path: "/me/repositories", respond: () => jsonResponse(200, repos) },
        {
          method: "POST",
          path: "/me/repositories",
          respond: () => {
            repos = [trackedRepo];
            return jsonResponse(201, trackedRepo);
          },
        },
      ])
    );
    const userEventSession = userEvent.setup();

    await screen.findByText(/aren.t tracking/i);
    await userEventSession.type(screen.getByLabelText("Track a repository"), "fastapi/fastapi");
    await userEventSession.click(screen.getByRole("button", { name: "Track" }));

    expect(await screen.findByText("fastapi/fastapi")).toBeInTheDocument();
  });

  it("shows the backend's error message when tracking fails", async () => {
    renderDashboard(
      createFetchMock([
        { method: "GET", path: "/me/repositories", respond: () => jsonResponse(200, []) },
        {
          method: "POST",
          path: "/me/repositories",
          respond: () => jsonResponse(404, { detail: "GitHub repository not found." }),
        },
      ])
    );
    const userEventSession = userEvent.setup();

    await screen.findByText(/aren.t tracking/i);
    await userEventSession.type(screen.getByLabelText("Track a repository"), "nope/nope");
    await userEventSession.click(screen.getByRole("button", { name: "Track" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("GitHub repository not found.");
  });

  it("untracks a repository after confirmation", async () => {
    let repos: unknown[] = [trackedRepo];
    renderDashboard(
      createFetchMock([
        { method: "GET", path: "/me/repositories", respond: () => jsonResponse(200, repos) },
        {
          method: "DELETE",
          path: "/me/repositories/5",
          respond: () => {
            repos = [];
            return noContentResponse();
          },
        },
      ])
    );
    const userEventSession = userEvent.setup();

    await screen.findByText("fastapi/fastapi");
    await userEventSession.click(screen.getByRole("button", { name: "Untrack" }));
    // Confirmation step -- untrack must not happen on the first click alone.
    expect(screen.getByText(/stop tracking fastapi\/fastapi/i)).toBeInTheDocument();
    await userEventSession.click(screen.getByRole("button", { name: "Yes" }));

    await waitFor(() => expect(screen.queryByText("fastapi/fastapi")).not.toBeInTheDocument());
  });

  it("logs out, clearing state via onLogout", async () => {
    const { onLogout } = renderDashboard(
      createFetchMock([
        { method: "GET", path: "/me/repositories", respond: () => jsonResponse(200, []) },
        { method: "POST", path: "/auth/logout", respond: () => noContentResponse() },
      ])
    );
    const userEventSession = userEvent.setup();

    await screen.findByText(/aren.t tracking/i);
    await userEventSession.click(screen.getByRole("button", { name: "Sign out" }));

    await waitFor(() => expect(onLogout).toHaveBeenCalledTimes(1));
  });
});
