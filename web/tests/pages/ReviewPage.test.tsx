import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { renderWithProviders } from "../testUtils";
import { mockFetch } from "../fixtures";

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(""),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => "/review",
  useParams: () => ({}),
}));

import ReviewPage from "@/app/review/page";

const FIXTURE_REVIEW = {
  items: [
    {
      id: "b2634921-bda5-4f31-870f-ee83f2b73a87",
      listing_id: "011bddfa-dafa-4fed-8156-999fa62e9407",
      field: "kind",
      listing_title: "Chemical and Process Engineering | Faculty Openings",
      listing_url: "https://www.timeshighereducation.com/unijobs/listing/416332/",
      listing_org: "INDIAN INSTITUTE OF TECHNOLOGY DELHI - ABU DHABI",
      classified_as: "other",
      conf: null,
      raw: null,
      rule: null,
      zone: null,
      guards: [],
      model: "ajp-rule-prior/1",
      reason: "unclassified_academic",
      status: "pending",
      human_kind: null,
      reviewer: null,
      reviewed_at: null,
      notes: null,
      created_at: "2026-10-13T01:34:33.916105+02:00",
    },
    {
      id: "aaa11111-0000-0000-0000-000000000001",
      listing_id: "02222222-0000-0000-0000-000000000001",
      field: "kind",
      listing_title: "Research Grant Call",
      listing_url: "https://example.invalid/a",
      listing_org: "Example University",
      classified_as: "grant_call",
      conf: 0.6,
      raw: "projekat",
      rule: "kind.grant_call",
      zone: "body",
      guards: ["narrative_past"],
      model: "ajp-rule-prior/1",
      reason: "low_confidence",
      status: "pending",
      human_kind: null,
      reviewer: null,
      reviewed_at: null,
      notes: null,
      created_at: "2026-10-13T01:34:33.916105+02:00",
    },
  ],
  total: 2,
  by_status: { pending: 2 },
  pending_by_reason: { low_confidence: 1, excluded_by_rule: 0, unclassified_academic: 1 },
  threshold: 0.7,
  model: "ajp-rule-prior1",
};

describe("ReviewPage (/review)", () => {
  let restore: () => void;
  beforeEach(() => {
    restore = mockFetch({ "/v1/classification-review": FIXTURE_REVIEW });
  });
  afterEach(() => restore());

  it("renders each queued classification as a readable row, worst reason first", async () => {
    renderWithProviders(<ReviewPage />);
    await waitFor(() => expect(screen.getByTestId("review-list")).toBeInTheDocument());
    const items = screen.getAllByTestId("review-item");
    expect(items).toHaveLength(2);
    // ordered by the API worst-first: the possible MISSED ad before the merely-weak rule
    expect(items[0].textContent).toContain("Chemical and Process Engineering");
    expect(items[0].textContent).toContain("Possibly a missed ad");
    expect(items[1].textContent).toContain("Research Grant Call");
    expect(items[1].textContent).toContain("Weak rule matched");
  });

  it("shows the EVIDENCE, not just the verdict - raw excerpt, rule, zone and guards", async () => {
    // The whole point of the queue: a reviewer judges the decision on the span it was made on. A page
    // that showed only 'grant_call' would be a guess with extra steps.
    renderWithProviders(<ReviewPage />);
    await waitFor(() => expect(screen.getByTestId("review-list")).toBeInTheDocument());
    const row = screen.getAllByTestId("review-item")[1];
    expect(row.textContent).toContain('"projekat"');
    expect(row.textContent).toContain("kind.grant_call");
    expect(row.textContent).toContain("body");
    expect(row.textContent).toContain("narrative_past");
    expect(row.textContent).toContain("0.6");
  });

  it("says plainly that no rule matched, rather than inventing an excerpt", async () => {
    renderWithProviders(<ReviewPage />);
    await waitFor(() => expect(screen.getByTestId("review-list")).toBeInTheDocument());
    expect(screen.getAllByTestId("review-item")[0].textContent).toContain("no excerpt (no rule matched)");
  });

  it("states that decisions are operator-only - this page never writes", async () => {
    // The browser cannot hold AJP_ADMIN_TOKEN, so a decision button here would be a lie or a token leak.
    renderWithProviders(<ReviewPage />);
    await waitFor(() => expect(screen.getByTestId("review-list")).toBeInTheDocument());
    // one notice per row, and never a decision button: the browser cannot hold AJP_ADMIN_TOKEN
    expect(screen.getAllByText(/Decisions are recorded by an operator/).length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: /confirm|correct|dismiss/i })).not.toBeInTheDocument();
  });

  it("surfaces the total and the confidence model, so a number is never read unversioned", async () => {
    renderWithProviders(<ReviewPage />);
    await waitFor(() => expect(screen.getByTestId("review-list")).toBeInTheDocument());
    expect(screen.getByText("2")).toBeInTheDocument();
    expect(screen.getByText("ajp-rule-prior1")).toBeInTheDocument();
  });

  it("offers a filter per reason with real counts", async () => {
    renderWithProviders(<ReviewPage />);
    await waitFor(() => expect(screen.getByTestId("review-list")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: /Possibly a missed ad \(1\)/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Weak rule matched \(1\)/ })).toBeInTheDocument();
  });
});
