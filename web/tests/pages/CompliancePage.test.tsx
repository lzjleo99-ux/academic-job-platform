import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { renderWithProviders } from "../testUtils";
import { FIXTURE_GOVERNANCE, FIXTURE_SOURCES, mockFetch } from "../fixtures";

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(""),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => "/compliance",
  useParams: () => ({}),
}));

import CompliancePage from "@/app/compliance/page";

describe("CompliancePage (/compliance)", () => {
  let restore: () => void;
  beforeEach(() => {
    restore = mockFetch({ "/v1/sources": FIXTURE_SOURCES, "/v1/governance": FIXTURE_GOVERNANCE });
  });
  afterEach(() => restore());

  it("lists every source's real robots state, scheduler status, requests/24h and blocked reason", async () => {
    renderWithProviders(<CompliancePage />);
    await waitFor(() => expect(screen.getByTestId("compliance-table")).toBeInTheDocument());
    const rsRow = screen.getByTestId("source-row-rs_org_boards");
    expect(rsRow.textContent).toContain("direct_org");
    expect(rsRow.textContent).toContain("120");                    // requests_24h
    const sljakaRow = screen.getByTestId("source-row-sljaka");
    expect(sljakaRow.textContent).toContain("degraded");
    expect(sljakaRow.textContent).toContain("unreachable_after_3_runs");
  });

  it("shows a real, summed total of denied attempts across every source - 0 when the platform is behaving", async () => {
    renderWithProviders(<CompliancePage />);
    await waitFor(() => expect(screen.getByTestId("denied-attempts-total")).toBeInTheDocument());
    expect(screen.getByTestId("denied-attempts-total").textContent).toContain("0");
  });

  it("shows the discovery per-host activity table from /v1/governance", async () => {
    renderWithProviders(<CompliancePage />);
    await waitFor(() => expect(screen.getByTestId("discovery-hosts-table")).toBeInTheDocument());
    expect(screen.getByTestId("discovery-hosts-table").textContent).toContain("example.ac.rs");
  });
});
