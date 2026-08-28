import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createFetchMock, jsonResponse } from "../testUtils";
import { SyncPanel } from "./SyncPanel";

// A tiny real interval (not fake timers -- Testing Library's findBy*/waitFor
// poll via real setTimeout internally, which fake timers don't drive on
// their own) keeps these tests fast without the fragility of mixing fake
// timers with async queries.
const FAST_POLL_MS = 10;

afterEach(() => {
  vi.unstubAllGlobals();
});

function renderPanel(fetchMock: ReturnType<typeof createFetchMock>, onSucceeded = vi.fn()) {
  vi.stubGlobal("fetch", fetchMock);
  render(<SyncPanel repositoryId={5} onSucceeded={onSucceeded} pollIntervalMs={FAST_POLL_MS} />);
  return { onSucceeded };
}

describe("SyncPanel", () => {
  it("goes queued -> running -> succeeded and calls onSucceeded", async () => {
    let pollCount = 0;
    const { onSucceeded } = renderPanel(
      createFetchMock([
        {
          method: "POST",
          path: "/me/repositories/5/sync",
          respond: () => jsonResponse(202, { job_id: "job-1", repository_id: 5, status: "queued" }),
        },
        {
          method: "GET",
          path: "/me/sync-jobs/job-1",
          respond: () => {
            pollCount += 1;
            if (pollCount === 1) {
              return jsonResponse(200, {
                job_id: "job-1",
                repository_id: 5,
                status: "running",
                pull_requests_processed: null,
                reviews_processed: null,
                created_at: "2024-01-01T00:00:00Z",
                started_at: "2024-01-01T00:00:01Z",
                finished_at: null,
                safe_error_code: null,
                safe_error_message: null,
              });
            }
            return jsonResponse(200, {
              job_id: "job-1",
              repository_id: 5,
              status: "succeeded",
              pull_requests_processed: 25,
              reviews_processed: 40,
              created_at: "2024-01-01T00:00:00Z",
              started_at: "2024-01-01T00:00:01Z",
              finished_at: "2024-01-01T00:00:05Z",
              safe_error_code: null,
              safe_error_message: null,
            });
          },
        },
      ])
    );
    const userEventSession = userEvent.setup();

    await userEventSession.click(screen.getByRole("button", { name: "Sync now" }));
    expect(await screen.findByText("Queued")).toBeInTheDocument();

    expect(await screen.findByText("Running")).toBeInTheDocument();
    expect(await screen.findByText("Succeeded")).toBeInTheDocument();
    expect(screen.getByText(/25 pull requests, 40 reviews processed/)).toBeInTheDocument();
    expect(onSucceeded).toHaveBeenCalledTimes(1);
  });

  it("shows the safe error message on a failed job", async () => {
    renderPanel(
      createFetchMock([
        {
          method: "POST",
          path: "/me/repositories/5/sync",
          respond: () => jsonResponse(202, { job_id: "job-2", repository_id: 5, status: "queued" }),
        },
        {
          method: "GET",
          path: "/me/sync-jobs/job-2",
          respond: () =>
            jsonResponse(200, {
              job_id: "job-2",
              repository_id: 5,
              status: "failed",
              pull_requests_processed: null,
              reviews_processed: null,
              created_at: "2024-01-01T00:00:00Z",
              started_at: "2024-01-01T00:00:01Z",
              finished_at: "2024-01-01T00:00:02Z",
              safe_error_code: "github_rate_limited",
              safe_error_message: "GitHub API rate limit exceeded.",
            }),
        },
      ])
    );
    const userEventSession = userEvent.setup();

    await userEventSession.click(screen.getByRole("button", { name: "Sync now" }));

    expect(await screen.findByText("GitHub API rate limit exceeded.")).toBeInTheDocument();
  });
});
