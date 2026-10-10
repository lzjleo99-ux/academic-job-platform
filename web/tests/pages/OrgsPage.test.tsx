import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { renderWithProviders } from "../testUtils";
import { FIXTURE_ORGS, mockFetch } from "../fixtures";

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(""),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => "/orgs",
  useParams: () => ({}),
}));

import OrgsPage from "@/app/orgs/page";

describe("OrgsPage (/orgs)", () => {
  let restore: () => void;
  beforeEach(() => {
    restore = mockFetch({ "/v1/orgs": FIXTURE_ORGS });
  });
  afterEach(() => restore());

  it("lists institutions with their open-position counts, linking to each org's profile", async () => {
    renderWithProviders(<OrgsPage />);
    await waitFor(() => expect(screen.getByText("Demo University", { exact: false })).toBeInTheDocument());
    expect(screen.getByText(/3 open position/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Demo University/ })).toHaveAttribute("href", "/orgs/org-1");
  });
});
