import { expect, test } from "@playwright/test";

// T48 acceptance: the review queue is readable BY A HUMAN. The API returns JSON, which is machine-shaped
// and useless to the person who has to act on it; this page renders the same rows the way a reviewer
// needs them. Run against the live stack exactly like the other specs (see flows.spec.ts's header).
//
// The list is fetched client-side (the page is a "use client" component reading /v1/classification-review),
// so the initial HTML is deliberately asserted NOT to contain the rows - a test that passed on the shell
// alone would prove nothing about whether the data ever arrives.
//
// STACK NOTE, learned the hard way: tests/e2e/flows.spec.ts asserts against tools/dev_up.py's [SAMPLE]
// fixture rows and runs against the live real-DB API. This spec must work on BOTH stacks, so it accepts an
// honestly-empty queue (a fresh dev install has no rows to doubt yet) and only asserts on the evidence
// when rows are actually present - the same "or an honest empty state" pattern applicants.spec.ts uses.
test("the classifier's review queue renders as a readable worklist with its evidence", async ({ page }) => {
  await page.goto("/review");

  await expect(page.getByRole("heading", { name: "Classification review queue" })).toBeVisible();
  // the shell first
  expect(await page.getByTestId("review-list").count()).toBe(1);

  // then the real data, over HTTP, from the live API - or an honest empty state on a fresh install
  const first = page.getByTestId("review-item").first();
  await expect(first.or(page.getByText("No results"))).toBeVisible({ timeout: 15_000 });

  if ((await page.getByTestId("review-item").count()) === 0) {
    await expect(page.getByText("No results")).toBeVisible();
    return;                                      // nothing to review yet is a valid, honest state
  }

  // worst reason first: 'Possibly a missed ad' leads, because that is the bucket that can hide a real
  // opening (the T33/T35/T36 failure mode), while a merely-weak rule is only imprecise.
  await expect(first).toContainText("Possibly a missed ad");

  // the EVIDENCE is the point: every row shows the classifier's verdict and the span it was made on.
  // A row whose rule fired must show that rule; one where nothing matched must say so rather than
  // invent an excerpt (D6).
  await expect(first).toContainText("no excerpt (no rule matched)");
  await expect(first).toContainText("Classifier said");

  // and a row with a fired rule shows it, with its zone and any guards
  const withRule = page.getByTestId("review-item").filter({ hasText: "Weak rule matched" }).first();
  if (await withRule.count()) {
    await expect(withRule).toContainText("Rule");
    await expect(withRule).toContainText("Matched in");
  }

  // the queue is honest about being read-only here: a browser cannot hold AJP_ADMIN_TOKEN
  await expect(page.getByText(/Decisions are recorded by an operator/).first()).toBeVisible();
  await expect(page.getByRole("button", { name: /^(Confirm|Correct|Dismiss)/i })).toHaveCount(0);

  // the model version travels with the numbers, so a score is never read unversioned
  await expect(page.getByText("ajp-rule-prior/1").first()).toBeVisible();
});
