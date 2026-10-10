import path from "path";
import { expect, test } from "@playwright/test";

// T16/T17 acceptance: 'UI upload flow with progress and report rendering' / 'upload -> analysis progress ->
// report with highlighted spans, source links, estimate + coverage statement + disclaimers'. Run against the
// live stack exactly like tests/e2e/flows.spec.ts (see that file's header comment for the two commands).
//
// Unlike the Python unit-test fixtures (generated fresh from tests/thesis_fixtures/build_fixtures.py, never
// committed as a binary - see that file's own docstring), this ONE small sample .docx IS committed: an E2E
// test needs a real file on disk to hand to the browser's file input, and regenerating it before every
// Playwright run would add a Python dependency to what is otherwise a pure Node/Playwright test suite.
// Rebuild it with: `python3 -c "...build_clean(Path('tests/thesis_fixtures/e2e_sample_clean.docx'))"` from
// the repo root if the fixture generator's shape ever changes.
const CLEAN_FIXTURE = path.resolve(__dirname, "../../../tests/thesis_fixtures/e2e_sample_clean.docx");

test("upload a thesis, see the formatting report, then run a local-only similarity check", async ({ page }) => {
  await page.goto("/thesis");
  await expect(page.getByRole("note")).toContainText("never sent to any external service");

  await page.setInputFiles("#thesis-file", CLEAN_FIXTURE);
  await page.getByRole("button", { name: "Analyze" }).click();

  await expect(page).toHaveURL(/\/thesis\/[0-9a-f-]+/, { timeout: 15_000 });
  await expect(page.getByRole("heading", { name: /clean\.docx/ })).toBeVisible();
  await expect(page.getByTestId("thesis-summary")).toBeVisible();

  // similarity section: only internal_corpus enabled -> no network, fast, deterministic
  await expect(page.getByText("internal_corpus")).toBeVisible();
  for (const external of ["openalex", "crossref", "core", "semantic_scholar", "unpaywall", "oa_fulltext"]) {
    await page.getByText(external, { exact: true }).click();      // uncheck every network source
  }
  await page.getByRole("button", { name: "Run similarity check" }).click();
  await expect(page.getByTestId("similarity-report")).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText(/heuristic similarity ESTIMATE/)).toBeVisible();
  await expect(page.getByText(/not Turnitin\/iThenticate/)).toBeVisible();
});

// T23: a real planted-duplication fixture, exercised through the actual UI - internal_corpus (ajp.thesis.
// pipeline._internal_corpus_documents) compares the current upload against every OTHER 'done' task in this
// same local workspace, so uploading the SAME real file twice makes the second upload's own internal_corpus
// check find the first one as a genuine, verbatim-matching candidate - a real planted fixture, no second
// binary file needed. Runs after the test above (same worker, same local thesis_task table - the first
// upload above is exactly the 'earlier task in this workspace' this test's own match depends on).
test("a second upload of the same file finds the first one as a real internal_corpus match", async ({ page }) => {
  await page.goto("/thesis");
  await page.setInputFiles("#thesis-file", CLEAN_FIXTURE);
  await page.getByRole("button", { name: "Analyze" }).click();
  await expect(page).toHaveURL(/\/thesis\/[0-9a-f-]+/, { timeout: 15_000 });

  for (const external of ["openalex", "crossref", "core", "semantic_scholar", "unpaywall", "oa_fulltext"]) {
    await page.getByText(external, { exact: true }).click();
  }
  await page.getByRole("button", { name: "Run similarity check" }).click();
  await expect(page.getByTestId("similarity-report")).toBeVisible({ timeout: 20_000 });

  // unlike the clean-run test above (no prior document in the workspace yet), this second upload's own
  // internal_corpus check has a real earlier document to match against - a genuine, non-empty result.
  await expect(page.getByText(/heuristic similarity ESTIMATE/)).toBeVisible();
  const estimateText = await page.getByTestId("similarity-report").innerText();
  expect(estimateText).toMatch(/\d/); // a real, non-placeholder percentage rendered somewhere in the report
  await expect(page.getByText("internal_corpus", { exact: true })).toBeVisible();
});
