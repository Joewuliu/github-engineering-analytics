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

/** A promise this test controls the resolution of, so a poll response only
 * resolves once the assertion for the *previous* rendered state has already
 * passed -- the state can never advance to the next one before we're ready
 * to observe it, regardless of how slow or fast the real setTimeout-driven
 * poll actually fires. */
function deferredResponse(): { promise: Promise<Response>; resolve: (response: Response) => void } {
  let resolve!: (response: Response) => void;
  const promise = new Promise<Response>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

describe("SyncPanel", () => {
  it("goes queued -> running -> succeeded and calls onSucceeded", async () => {
    // Each poll's GET response is held pending until the test explicitly
    // resolves it below. This is what makes every assertion deterministic:
    // e.g. "Queued" can only ever stop being the rendered state once we
    // resolve `runningResponse`, so there is no window in which a fast (or
    // slow, loaded-CI-runner) poll tick can skip past it unobserved -- the
    // old version raced the real 10ms setTimeout against the test's own
    // async overhead (click handling, microtask flushing), and CI was slow
    // enough to lose that race, jumping straight to "Succeeded".
    let pollCount = 0;
    const runningResponse = deferredResponse();
    const succeededResponse = deferredResponse();
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
            return pollCount === 1 ? runningResponse.promise : succeededResponse.promise;
          },
        },
      ])
    );
    const userEventSession = userEvent.setup();

    await userEventSession.click(screen.getByRole("button", { name: "Sync now" }));
    expect(await screen.findByText("Queued")).toBeInTheDocument();

    runningResponse.resolve(
      jsonResponse(200, {
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
      })
    );
    expect(await screen.findByText("Running")).toBeInTheDocument();

    succeededResponse.resolve(
      jsonResponse(200, {
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
      })
    );
    expect(await screen.findByText("Succeeded")).toBeInTheDocument();
    expect(screen.getByText(/25 pull requests, 40 reviews processed/)).toBeInTheDocument();
    expect(onSucceeded).toHaveBeenCalledTimes(1);
    expect(pollCount).toBe(2);
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
