import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { renderWithProviders } from "../testUtils";
import { mockFetch } from "../fixtures";

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(""),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => "/applicants",
  useParams: () => ({}),
}));

import ApplicantsPage from "@/app/applicants/page";

function renderPage() {
  return renderWithProviders(<ApplicantsPage />);
}

const APPLICANT_ID = "33333333-3333-3333-3333-333333333333";
const APPLICANT = {
  id: APPLICANT_ID, status: "done", error: null, cv_filename: "cv.docx", source_format: "docx",
  profile: {
    disciplines: ["Natural sciences"], degree: { v: "postdoc", raw: "Postdoctoral" },
    skills: ["Python", "MATLAB"], languages: [{ lang: "English", level: "fluent" }],
    desired_countries: ["DE", "NL"], desired_cities: [], remote_ok: true, ft_pt: "full",
    years_experience: 5, min_salary_eur_year: 45000, foreigner_visa_note: "Serbian citizen",
  },
  created_at: "2026-09-28T00:00:00Z",
};
const MATCHES = {
  total: 1,
  results: [
    {
      listing_id: "44444444-4444-4444-4444-444444444444", score: 87.5,
      factors: {
        discipline: { weight: 25, fraction: 1.0, points: 25.0, reason: "overlaps on Natural sciences", state: "positive" },
        salary: { weight: 20, fraction: 0.5, points: 10.0, reason: "not enough stated data on the applicant's or the listing's own side to evaluate this criterion", state: "unknown" },
      },
      counts: { positive: 1, negative: 0, unknown: 1 }, coverage: 0.25, total_weight: 100,
      title_raw: "Postdoc in Quantum Physics", org_raw: "Demo University", url: "https://demo.invalid/1",
      apply_url: "https://demo.invalid/apply/1", contact_emails: ["hr@demo.invalid"], contact_phones: ["+381111234567"],
      country_iso2: "DE", city: "Berlin", location_class: "located", computed_at: "2026-09-28T00:00:00Z",
    },
  ],
};

async function uploadCv() {
  const file = new File(["fake cv content"], "cv.docx", { type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document" });
  const input = screen.getByLabelText(/Upload your CV/) as HTMLInputElement;
  fireEvent.change(input, { target: { files: [file] } });
  fireEvent.click(screen.getByRole("button", { name: /Upload and extract profile/ }));
}

describe("ApplicantsPage (/applicants)", () => {
  let restore: () => void;
  afterEach(() => restore());

  it("states the local-analysis-only note before any upload, and requires a file", async () => {
    restore = mockFetch({});
    renderPage();
    expect(screen.getByRole("note")).toHaveTextContent(/never sent anywhere else/);
    fireEvent.click(screen.getByRole("button", { name: /Upload and extract profile/ }));
    await waitFor(() => expect(screen.getByText(/Choose a CV file first/)).toBeInTheDocument());
  });

  it("uploads a CV and shows the extracted profile for review", async () => {
    restore = mockFetch({ "/v1/applicants": APPLICANT });
    renderPage();
    await uploadCv();
    await waitFor(() => expect(screen.getByTestId("applicants-review")).toBeInTheDocument());
    expect(screen.getByDisplayValue("Natural sciences")).toBeInTheDocument();
    expect(screen.getByDisplayValue("Python, MATLAB")).toBeInTheDocument();
  });

  it("finds matches and shows ranked results with score, reasons, and clickable contacts", async () => {
    restore = mockFetch({
      "/v1/applicants/33333333-3333-3333-3333-333333333333/matches": MATCHES,
      "/v1/applicants": APPLICANT,
    });
    renderPage();
    await uploadCv();
    await waitFor(() => expect(screen.getByTestId("applicants-review")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /Find matching positions/ }));
    await waitFor(() => expect(screen.getByTestId("applicants-matches")).toBeInTheDocument());
    expect(screen.getByText("Postdoc in Quantum Physics")).toBeInTheDocument();
    expect(screen.getByText("88/100")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "hr@demo.invalid" })).toHaveAttribute("href", "mailto:hr@demo.invalid");
    expect(screen.getByRole("link", { name: "+381111234567" })).toHaveAttribute("href", "tel:+381111234567");
  });

  it("shows an honest empty state when no matches come back", async () => {
    restore = mockFetch({
      "/v1/applicants/33333333-3333-3333-3333-333333333333/matches": { total: 0, results: [] },
      "/v1/applicants": APPLICANT,
    });
    renderPage();
    await uploadCv();
    await waitFor(() => expect(screen.getByTestId("applicants-review")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /Find matching positions/ }));
    await waitFor(() => expect(screen.getByTestId("applicants-no-matches")).toBeInTheDocument());
  });

  it("T33 (packet 018, Q4): shows coverage, per-factor state, and an honest note when the page is a top-N slice of a larger total", async () => {
    restore = mockFetch({
      "/v1/applicants/33333333-3333-3333-3333-333333333333/matches": { ...MATCHES, total: 250 },
      "/v1/applicants": APPLICANT,
    });
    renderPage();
    await uploadCv();
    await waitFor(() => expect(screen.getByTestId("applicants-review")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /Find matching positions/ }));
    await waitFor(() => expect(screen.getByTestId("applicants-matches")).toBeInTheDocument());
    expect(screen.getByTestId("applicant-match-coverage")).toHaveTextContent("25%");
    expect(screen.getByTestId("applicants-matches-total-note")).toHaveTextContent("250");
    fireEvent.click(screen.getByText(/Why this score/));
    expect(screen.getByTestId("applicant-match-factor-discipline")).toHaveTextContent("positive");
    expect(screen.getByTestId("applicant-match-factor-salary")).toHaveTextContent("unknown");
  });

  it("deleting the CV returns to the upload step with a confirmation note", async () => {
    restore = mockFetch({ "/v1/applicants": APPLICANT });
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderPage();
    await uploadCv();
    await waitFor(() => expect(screen.getByTestId("applicants-review")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /Delete my CV and profile/ }));
    await waitFor(() => expect(screen.getByTestId("applicants-deleted-note")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: /Upload and extract profile/ })).toBeInTheDocument();
  });
});
