import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { renderWithProviders } from "../testUtils";
import { FIXTURE_LISTING, mockFetch } from "../fixtures";

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(""),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => "/listing/11111111-1111-1111-1111-111111111111",
  useParams: () => ({ id: "11111111-1111-1111-1111-111111111111" }),
}));

import ListingDetailPage from "@/app/listing/[id]/page";

describe("ListingDetailPage (/listing/[id])", () => {
  let restore: () => void;
  beforeEach(() => {
    restore = mockFetch({ "/v1/listings/11111111": FIXTURE_LISTING });
  });
  afterEach(() => restore());

  it("shows the full structured view including sources and a raw-excerpt reveal control", async () => {
    renderWithProviders(<ListingDetailPage />);
    await waitFor(() => expect(screen.getByRole("heading", { name: "PhD Position in Robotics" })).toBeInTheDocument());
    // all 3 T13-merged sources are listed, not just the canonical one
    expect(screen.getByText(/rs_org_boards/)).toBeInTheDocument();
    expect(screen.getByText(/sljaka/)).toBeInTheDocument();
    expect(screen.getByText(/academictransfer/)).toBeInTheDocument();
    // duration_months carries {v:12, conf:0.9, raw:"12 months"} - rendered via ConfidenceValue as a clickable control
    const durationControl = screen.getByRole("button", { name: /confidence: 0\.9/i });
    expect(durationControl).toHaveTextContent("12 mo");
    expect(screen.queryByText(/12 months/)).not.toBeInTheDocument(); // raw excerpt not shown until revealed
    durationControl.click();
    expect(await screen.findByText(/12 months/)).toBeInTheDocument(); // now revealed on click
    expect(screen.getByRole("link", { name: "Apply on original source" })).toHaveAttribute(
      "href", "https://demo.invalid/apply/1",
    );
  });

  it("T26 (packet 015): renders contacts as real clickable mailto:/tel: links, the full ad text, and language", async () => {
    restore();
    restore = mockFetch({
      "/v1/listings/11111111": {
        ...FIXTURE_LISTING,
        contact_emails: ["hr@demo.invalid"],
        contact_phones: ["+381601234567"],
        contact_name: "Jane Doe",
        raw_text: "Full original ad body, longer than the requirements excerpt alone.",
      },
    });
    renderWithProviders(<ListingDetailPage />);
    await waitFor(() => expect(screen.getByTestId("listing-contacts")).toBeInTheDocument());
    expect(screen.getByRole("link", { name: "hr@demo.invalid" })).toHaveAttribute("href", "mailto:hr@demo.invalid");
    expect(screen.getByRole("link", { name: "+381601234567" })).toHaveAttribute("href", "tel:+381601234567");
    expect(screen.getByText("Jane Doe")).toBeInTheDocument();
    expect(screen.getByTestId("listing-full-text")).toHaveTextContent("Full original ad body");
    expect(screen.getByText(/en \(C1\)/)).toBeInTheDocument();
  });

  it("T26 (packet 015): shows an honest 'not stated' when a listing has no contact info at all", async () => {
    restore();
    restore = mockFetch({
      "/v1/listings/11111111": { ...FIXTURE_LISTING, contact_emails: [], contact_phones: [], contact_name: null },
    });
    renderWithProviders(<ListingDetailPage />);
    await waitFor(() => expect(screen.getByRole("heading", { name: "PhD Position in Robotics" })).toBeInTheDocument());
    expect(screen.getByText("Not stated in the ad")).toBeInTheDocument();
  });

  it("T31 (packet 019): shows an honest scanned-document notice with a link to the original PDF, never a silently-empty section", async () => {
    restore();
    restore = mockFetch({
      "/v1/listings/11111111": { ...FIXTURE_LISTING, content_scanned: true, requirements_raw: null, raw_text: null },
    });
    renderWithProviders(<ListingDetailPage />);
    await waitFor(() => expect(screen.getByTestId("listing-scanned-notice")).toBeInTheDocument());
    expect(screen.getByTestId("listing-scanned-notice")).toHaveTextContent(/scanned\/image document/i);
    expect(screen.getByRole("link", { name: FIXTURE_LISTING.url })).toHaveAttribute("href", FIXTURE_LISTING.url);
  });

  it("T31 (packet 019): a normal, non-scanned listing never shows the scanned-document notice", async () => {
    renderWithProviders(<ListingDetailPage />);
    await waitFor(() => expect(screen.getByRole("heading", { name: "PhD Position in Robotics" })).toBeInTheDocument());
    expect(screen.queryByTestId("listing-scanned-notice")).not.toBeInTheDocument();
  });
});
