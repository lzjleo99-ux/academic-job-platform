import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { renderWithProviders } from "../testUtils";
import { mockFetch, statusResponse } from "../fixtures";

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(""),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => "/thesis",
  useParams: () => ({ id: "22222222-2222-2222-2222-222222222222" }),
}));

import ThesisUploadPage from "@/app/thesis/page";
import ThesisReportPage from "@/app/thesis/[id]/page";

describe("ThesisUploadPage (/thesis)", () => {
  let restore: () => void;
  beforeEach(() => {
    restore = mockFetch({ "/v1/thesis/templates": ["rs_phd_generic"] });
  });
  afterEach(() => restore());

  it("states the local-analysis-only note before any upload", async () => {
    renderWithProviders(<ThesisUploadPage />);
    await waitFor(() => expect(screen.getByRole("note")).toBeInTheDocument());
    expect(screen.getByRole("note")).toHaveTextContent(/never sent to any external service/);
    expect(screen.getByLabelText(/Upload a \.docx or \.pdf file/)).toBeInTheDocument();
  });
});

describe("ThesisReportPage (/thesis/[id])", () => {
  let restore: () => void;
  const task = {
    id: "22222222-2222-2222-2222-222222222222", status: "done", error: null,
    original_filename: "dissertation.docx", source_format: "docx", template_name: "rs_phd_generic",
    report: {
      template_name: "rs_phd_generic", summary: { pass: 10, warn: 1, fail: 2 },
      findings: [
        { check: "required_sections", severity: "fail", message: "required section missing: zakljucak", page: null, excerpt: null, conf: null },
        { check: "abstracts", severity: "pass", message: "exactly one 'sr' abstract found", page: null, excerpt: null, conf: null },
      ],
    },
    similarity: null, created_at: "2026-09-23T00:00:00Z",
  };
  beforeEach(() => {
    // more specific routes first - mockFetch matches on the first substring hit, and the bare task route's
    // path is itself a substring of the /similarity and /report routes below it.
    restore = mockFetch({
      "/v1/thesis/similarity/sources": ["internal_corpus", "openalex", "copyleaks"],
      "/v1/thesis/22222222-2222-2222-2222-222222222222/similarity": statusResponse(404),
      "/v1/thesis/22222222-2222-2222-2222-222222222222": task,
    });
  });
  afterEach(() => restore());

  it("renders the pass/warn/fail summary and grouped findings from the fixture API", async () => {
    renderWithProviders(<ThesisReportPage />);
    await waitFor(() => expect(screen.getByText("dissertation.docx")).toBeInTheDocument());
    const summary = screen.getByTestId("thesis-summary");
    expect(summary).toHaveTextContent("10");
    expect(summary).toHaveTextContent("2");
    expect(screen.getByText(/required section missing: zakljucak/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Download formatting report \(PDF\)/ })).toHaveAttribute(
      "href", expect.stringContaining("/v1/thesis/22222222-2222-2222-2222-222222222222/report?format=pdf"),
    );
  });

  it("shows the similarity source toggles and the 'not run yet' state", async () => {
    renderWithProviders(<ThesisReportPage />);
    await waitFor(() => expect(screen.getByText("openalex")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: /Run similarity check/ })).toBeInTheDocument();
    expect(screen.getByText(/has not been run yet/)).toBeInTheDocument();
    // copyleaks toggle exists but is never pre-checked
    const copyleaksCheckbox = screen.getByText("copyleaks").querySelector("input") as HTMLInputElement;
    expect(copyleaksCheckbox.checked).toBe(false);
  });

  it("deleting the document shows a confirmation note (T25, closes RC1's own delete gap)", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderWithProviders(<ThesisReportPage />);
    await waitFor(() => expect(screen.getByText("dissertation.docx")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /Delete this document/ }));
    await waitFor(() => expect(screen.getByTestId("thesis-deleted-note")).toBeInTheDocument());
  });
});
