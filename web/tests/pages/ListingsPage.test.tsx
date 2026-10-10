import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import { renderWithProviders } from "../testUtils";
import { FIXTURE_FACETS, FIXTURE_LISTINGS, FIXTURE_META, mockFetch } from "../fixtures";

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(""),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => "/",
  useParams: () => ({}),
}));

import ListingsPage from "@/components/pages/ListingsPage";

describe("ListingsPage (/)", () => {
  let restore: () => void;
  beforeEach(() => {
    restore = mockFetch({
      "/v1/listings": FIXTURE_LISTINGS, "/v1/facets": FIXTURE_FACETS, "/v1/meta": FIXTURE_META,
    });
  });
  afterEach(() => restore());

  it("renders listing cards, the filter sidebar and the result count from the fixture API", async () => {
    renderWithProviders(<ListingsPage />);
    await waitFor(() => expect(screen.getByText("PhD Position in Robotics")).toBeInTheDocument());
    const resultsGrid = screen.getByTestId("listing-results");
    expect(within(resultsGrid).getByText(/Demo University/)).toBeInTheDocument();
    expect(within(resultsGrid).getByText(/Novi Sad/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Filters" })).toBeInTheDocument();
    expect(screen.getByText(/^1 results$/)).toBeInTheDocument();
    // T13's dedupe: this fixture listing carries 3 sources - the verified-direct badge must show.
    expect(screen.getByText("Verified: direct org page")).toBeInTheDocument();
  });
});
