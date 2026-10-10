import path from "path";
import { expect, test } from "@playwright/test";

// T25 acceptance: 'Playwright E2E upload->edit->matches'. Run against the live stack exactly like
// tests/e2e/flows.spec.ts/thesis.spec.ts (see flows.spec.ts's own header comment for the two commands).
//
// Like thesis.spec.ts's own committed sample, this real .docx is committed rather than regenerated before
// every Playwright run (a Node-only test suite otherwise needs no Python at all). Rebuild it with:
// `python3 -c "import sys; sys.path.insert(0,'tests'); from cv_fixtures.build_fixtures import build_docx; from pathlib import Path; build_docx(Path('tests/cv_fixtures/e2e_sample_cv.docx'))"`
// from the repo root if tests/cv_fixtures/build_fixtures.py's own SAMPLE_CV_TEXT ever changes.
const CV_FIXTURE = path.resolve(__dirname, "../../../tests/cv_fixtures/e2e_sample_cv.docx");

test("upload a CV, review/edit the extracted profile, then see ranked matches (or an honest empty state)", async ({ page }) => {
  await page.goto("/applicants");
  await expect(page.getByRole("note")).toContainText("never sent anywhere else");

  await page.setInputFiles("#cv-file", CV_FIXTURE);
  await page.getByRole("button", { name: "Upload and extract profile" }).click();

  await expect(page.getByTestId("applicants-review")).toBeVisible({ timeout: 15_000 });
  // real, non-fabricated extraction from the committed fixture's own text (see build_fixtures.py's SAMPLE_CV_TEXT)
  await expect(page.getByLabel("Disciplines")).toHaveValue("Natural sciences, Engineering and technology");
  await expect(page.getByLabel("Skills")).toHaveValue("Python, MATLAB, machine learning, data analysis, LaTeX");

  // edit: correct the desired countries field, save, and confirm the correction persists
  const countriesField = page.getByLabel("Desired countries");
  await countriesField.fill("RS");
  await page.getByRole("button", { name: "Save changes" }).click();
  await expect(page.getByLabel("Desired countries")).toHaveValue("RS", { timeout: 10_000 });

  await page.getByRole("button", { name: "Find matching positions" }).click();
  await expect(page.getByTestId("applicants-matches")).toBeVisible({ timeout: 15_000 });

  // Honest either way: real ranked match cards with a clickable listing link and a score, OR the genuine
  // empty state - never a fabricated placeholder match. Assert on whichever the real seed data produced.
  const matchList = page.getByTestId("applicants-match-list");
  const noMatches = page.getByTestId("applicants-no-matches");
  await expect(matchList.or(noMatches)).toBeVisible();
  if (await matchList.locator('[data-testid="applicant-match-card"]').count() > 0) {
    const first = matchList.locator('[data-testid="applicant-match-card"]').first();
    await expect(first).toContainText("/100");
    await expect(first.getByRole("link").first()).toHaveAttribute("href", /.+/);
  }

  // self-service delete (the Privacy page's own retention promise)
  page.once("dialog", (d) => d.accept());
  await page.getByRole("button", { name: "Delete my CV and profile" }).click();
  await expect(page.getByTestId("applicants-deleted-note")).toBeVisible({ timeout: 10_000 });
});
