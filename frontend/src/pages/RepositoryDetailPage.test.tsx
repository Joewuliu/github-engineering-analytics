import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { RepositoryMetrics } from "../api/types";
import { createFetchMock, jsonResponse } from "../testUtils";
import { RepositoryDetailPage } from "./RepositoryDetailPage";

const metrics: RepositoryMetrics = {
  repository_id: 5,
  full_name: "fastapi/fastapi",
  total_pull_requests: 25,
  merged_pull_requests: 18,
  merge_rate: 0.72,
  median_pr_cycle_time_hours: 19.4,
  median_time_to_first_review_hours: 3.7,
};

const emptyMetrics: RepositoryMetrics = {
  repository_id: 5,
  full_name: "fastapi/fastapi",
  total_pull_requests: 0,
  merged_pull_requests: 0,
  merge_rate: null,
  median_pr_cycle_time_hours: null,
  median_time_to_first_review_hours: null,
};

function renderDetail(fetchMock: ReturnType<typeof createFetchMock>) {
  vi.stubGlobal("fetch", fetchMock);
  render(
    <MemoryRouter initialEntries={["/repositories/5"]}>
      <Routes>
        <Route path="/repositories/:id" element={<RepositoryDetailPage />} />
      </Routes>
    </MemoryRouter>
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("RepositoryDetailPage metrics", () => {
  it("renders metric values", async () => {
    renderDetail(
      createFetchMock([
        { method: "GET", path: "/me/repositories/5/metrics", respond: () => jsonResponse(200, metrics) },
      ])
    );

    expect(await screen.findByText("25")).toBeInTheDocument();
    expect(screen.getByText("18")).toBeInTheDocument();
    expect(screen.getByText("72%")).toBeInTheDocument();
  });

  it("renders 'Not enough data' for null metrics, never null/NaN/undefined", async () => {
    renderDetail(
      createFetchMock([
        { method: "GET", path: "/me/repositories/5/metrics", respond: () => jsonResponse(200, emptyMetrics) },
      ])
    );

    await screen.findByText("fastapi/fastapi");
    expect(screen.getAllByText("Not enough data")).toHaveLength(3);
    expect(screen.queryByText("null", { exact: false })).not.toBeInTheDocument();
    expect(screen.queryByText("NaN", { exact: false })).not.toBeInTheDocument();
    expect(screen.queryByText("undefined", { exact: false })).not.toBeInTheDocument();
  });

  it("shows a not-found message for a 404", async () => {
    renderDetail(
      createFetchMock([
        {
          method: "GET",
          path: "/me/repositories/5/metrics",
          respond: () => jsonResponse(404, { detail: "Repository not found." }),
        },
      ])
    );

    expect(await screen.findByText("Repository not found.")).toBeInTheDocument();
  });
});

describe("RepositoryDetailPage sync", () => {
  // The queued->running->succeeded polling cycle and the failed-job case
  // are covered in SyncPanel.test.tsx, using an injectable short poll
  // interval instead of fake timers (fake timers interact badly with
  // Testing Library's findBy*/waitFor, which poll via real setTimeout).
  // This page's own sync tests stick to outcomes that resolve on the
  // initial POST alone, with no polling involved.

  it("shows an informational message on 409 (already active)", async () => {
    renderDetail(
      createFetchMock([
        { method: "GET", path: "/me/repositories/5/metrics", respond: () => jsonResponse(200, emptyMetrics) },
        {
          method: "POST",
          path: "/me/repositories/5/sync",
          respond: () => jsonResponse(409, { detail: "Repository sync already in progress." }),
        },
      ])
    );
    const userEventSession = userEvent.setup({ delay: null });

    await screen.findByText("fastapi/fastapi");
    await userEventSession.click(screen.getByRole("button", { name: "Sync now" }));

    expect(await screen.findByText(/already in progress/i)).toBeInTheDocument();
  });

  it("shows the calm hosted-demo message on the disabled-sync 503", async () => {
    renderDetail(
      createFetchMock([
        { method: "GET", path: "/me/repositories/5/metrics", respond: () => jsonResponse(200, emptyMetrics) },
        {
          method: "POST",
          path: "/me/repositories/5/sync",
          respond: () =>
            jsonResponse(503, {
              detail: "Background synchronization is unavailable in this deployment.",
            }),
        },
      ])
    );
    const userEventSession = userEvent.setup({ delay: null });

    await screen.findByText("fastapi/fastapi");
    await userEventSession.click(screen.getByRole("button", { name: "Sync now" }));

    expect(await screen.findByText("Live synchronization is disabled on the hosted demo.")).toBeInTheDocument();
    expect(screen.getByText(/full background worker architecture is implemented/i)).toBeInTheDocument();
    expect(screen.queryByText(/render/i)).not.toBeInTheDocument();
  });

  it("shows a genuine error (not the calm demo message) for an enqueue-failure 503", async () => {
    renderDetail(
      createFetchMock([
        { method: "GET", path: "/me/repositories/5/metrics", respond: () => jsonResponse(200, emptyMetrics) },
        {
          method: "POST",
          path: "/me/repositories/5/sync",
          respond: () =>
            jsonResponse(503, { detail: "Could not schedule the sync job. Try again later." }),
        },
      ])
    );
    const userEventSession = userEvent.setup({ delay: null });

    await screen.findByText("fastapi/fastapi");
    await userEventSession.click(screen.getByRole("button", { name: "Sync now" }));

    expect(await screen.findByText("Could not schedule the sync job. Try again later.")).toBeInTheDocument();
    expect(screen.queryByText("Live synchronization is disabled on the hosted demo.")).not.toBeInTheDocument();
  });
});
