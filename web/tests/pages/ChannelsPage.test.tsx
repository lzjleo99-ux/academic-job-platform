import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { renderWithProviders } from "../testUtils";
import { FIXTURE_CHANNELS, mockFetch } from "../fixtures";

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(""),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => "/channels",
  useParams: () => ({}),
}));

import ChannelsPage from "@/app/channels/page";

describe("ChannelsPage (/channels)", () => {
  let restore: () => void;
  beforeEach(() => {
    restore = mockFetch({ "/v1/channels": FIXTURE_CHANNELS });
  });
  afterEach(() => restore());

  it("lists external deeplink channels with an outbound link, never as if they were our own listings", async () => {
    renderWithProviders(<ChannelsPage />);
    await waitFor(() => expect(screen.getByText(/Serbia: research jobs on EURAXESS/)).toBeInTheDocument());
    const link = screen.getByRole("link", { name: "Open search" });
    expect(link).toHaveAttribute("href", expect.stringContaining("euraxess.ec.europa.eu"));
    expect(link).toHaveAttribute("target", "_blank");
  });
});
