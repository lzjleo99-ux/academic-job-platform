import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { renderWithProviders } from "../testUtils";
import { FIXTURE_META, mockFetch } from "../fixtures";

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(""),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => "/about/methodology",
  useParams: () => ({}),
}));

import MethodologyPage from "@/app/about/methodology/page";

describe("MethodologyPage (/about/methodology)", () => {
  let restore: () => void;
  beforeEach(() => {
    restore = mockFetch({
      "/v1/meta": FIXTURE_META,
      "/health": { status: "ok", db: "ok", fx_as_of: "2026-09-22", fx_currencies: 2, sources_last_run: [], degraded_sources: [] },
      // T38 (packet 022) real weights: discipline 15, skill_relevance 20, location 20, salary 20, ft_pt 10,
      // language 10, degree_level 5 (swaps T36's own discipline/skill_relevance numbers; sums to 100).
      "/v1/applicants/matching/weights": { discipline: 15, skill_relevance: 20, location: 20, salary: 20, ft_pt: 10, language: 10, degree_level: 5 },
    });
  });
  afterEach(() => restore());

  it("states the compliance approach and surfaces known degraded features rather than hiding them", async () => {
    renderWithProviders(<MethodologyPage />);
    await waitFor(() => expect(screen.getByText(/robots\.txt/)).toBeInTheDocument());
    expect(screen.getByText(/discipline_hierarchy/)).toBeInTheDocument();
    expect(screen.getByText(/no EuroSciVoc tree loaded yet/)).toBeInTheDocument();
  });

  it("renders the real, live matching weights - never restated by hand, so they cannot silently drift", async () => {
    renderWithProviders(<MethodologyPage />);
    await waitFor(() => expect(screen.getByText("discipline")).toBeInTheDocument());
    expect(screen.getByText("discipline").closest("tr")).toHaveTextContent("15 / 100");
    // "skill_relevance" also appears in this page's own explanatory prose now (T36c) - scope to the table row.
    const skillRelevanceCell = screen.getAllByText("skill_relevance").map((el) => el.closest("tr")).find(Boolean);
    expect(skillRelevanceCell).toHaveTextContent("20 / 100");
    expect(screen.getByText("salary").closest("tr")).toHaveTextContent("20 / 100");
    expect(screen.getByText("location").closest("tr")).toHaveTextContent("20 / 100");
    expect(screen.getByText("degree_level").closest("tr")).toHaveTextContent("5 / 100");
  });
});
