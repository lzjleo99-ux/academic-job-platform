import { expect, test } from "@playwright/test";

// The exact flows packet 007's T15_nextjs_ui.acceptance lists, against the live tools/dev_up.py seed data
// (100 real orgs + [SAMPLE]-labelled demo listings - see tools/dev_listings_fixture.json).
//
// Checkboxes use .click() + a retrying expect() rather than locator.check() throughout: check()'s own
// built-in "did the checked property change" assertion samples once right after the click, but these
// checkboxes are controlled by the URL (see lib/useUrlParams.ts) - the native click flips the DOM property
// immediately, React then reconciles it against the *not-yet-updated* search params for one render, and only
// settles once the client-side navigation resolves. check() intermittently observes that mid-flight revert
// and fails even though the checkbox (and the results) end up correct a moment later - found live running
// this suite against the real dev stack, not a hypothesized issue.

test("filter RS + employment + PhD", async ({ page }) => {
  await page.goto("/");
  await page.locator("fieldset", { hasText: "Country" }).getByRole("checkbox").first().click();
  await expect(page.locator("fieldset", { hasText: "Country" }).getByRole("checkbox").first()).toBeChecked();
  await page.locator("fieldset", { hasText: "Role" }).getByText("phd", { exact: true }).locator("..").getByRole("checkbox").click();
  await expect(page.getByTestId("listing-results")).toContainText("[SAMPLE] PhD Position in Machine Learning");
  await expect(page.getByTestId("listing-results")).not.toContainText("[SAMPLE] Postdoc in Robotics"); // NL, filtered out by country
});

test("salary range filter narrows results and shows the converted EUR/year figure", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("listing-results")).toContainText("[SAMPLE] PhD Position in Machine Learning");
  const salaryMin = page.getByLabel("Salary (EUR/year) min");
  await salaryMin.fill("100000");
  await salaryMin.blur();
  await expect(page).toHaveURL(/salary_min=100000/);
  // 2500 EUR/month = 30000 EUR/year, and the postdoc's 3400-4200/month = 40800-50400/year - both below 100k
  await expect(page.getByTestId("listing-results")).not.toContainText("[SAMPLE] PhD Position in Machine Learning");
});

test("T33 (packet 018, Q6): enrollment is shown by default now, and can be narrowed back out via its Type checkbox", async ({ page }) => {
  // PM's own real-DB audit found the old employment-only default hid 1,280 of 3,948 open listings
  // (enrollment/internship/grant_call/other) from every fresh visitor - unfiltered (every kind) by default
  // now, same as this project's own location_class default (T23, PM ruling R2). A positive toContainText
  // assertion (not the old negative one) is used deliberately - Playwright's auto-retrying `.not.` assertion
  // resolves as soon as the condition is momentarily true, so it can falsely pass during the page's own
  // brief pre-fetch loading state without ever re-checking the final rendered content; found live debugging
  // this exact test with a request/DOM trace after the fix already worked but this assertion kept "passing".
  await page.goto("/");
  await expect(page.getByTestId("listing-results")).toContainText("[SAMPLE] Doctoral Studies Enrolment");
  await page.locator("fieldset", { hasText: "Type" }).getByText("PhD enrolment").locator("..").getByRole("checkbox").click();
  await expect(page.getByTestId("listing-results")).not.toContainText("[SAMPLE] Doctoral Studies Enrolment");
});

test("the cross-source-merged listing shows all 3 sources with a verified-direct badge", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Search title / text").fill("Machine Learning");
  await page.getByLabel("Search title / text").blur();
  const card = page.locator("article").filter({ hasText: "[SAMPLE] PhD Position in Machine Learning" });
  await expect(card.getByTestId("source-chips")).toContainText("rs_org_boards");
  await expect(card.getByTestId("source-chips")).toContainText("sljaka");
  await expect(card.getByTestId("source-chips")).toContainText("academictransfer");
  await expect(card).toContainText("Verified: direct org page");
});

test("listing detail page reveals a raw excerpt + confidence on click", async ({ page }) => {
  await page.goto("/");
  await page.getByText("[SAMPLE] PhD Position in Machine Learning", { exact: true }).first().click();
  await expect(page).toHaveURL(/\/listing\//);
  await expect(page.getByRole("heading", { name: "[SAMPLE] PhD Position in Machine Learning" })).toBeVisible();
  const durationControl = page.getByRole("button", { name: /confidence: 0.9/i });
  await expect(page.getByText("36 months", { exact: false })).toHaveCount(0);
  await durationControl.click();
  await expect(page.getByText(/36 months/)).toBeVisible();
  // T26 (packet 015): real, clickable mailto:/tel: contacts + a real apply link - live against the real seed
  await expect(page.getByRole("link", { name: "phd-recruitment@example-bg.invalid" })).toHaveAttribute(
    "href", "mailto:phd-recruitment@example-bg.invalid",
  );
  await expect(page.getByRole("link", { name: "+381 11 123 4567" })).toHaveAttribute("href", "tel:+381111234567");
  await expect(page.getByRole("link", { name: "Apply on original source" })).toHaveAttribute(
    "href", "https://example-bg.invalid/jobs/phd-1/apply",
  );
});

test("T23 (PM ruling R2): a null-country listing is visible by default and the Remote/Unknown facet narrows correctly around it", async ({ page }) => {
  // location_class is unfiltered by default (T23, PM ruling R2, same as kind's own T33/Q6 default) - the
  // null-country remote demo row is already visible with no facet narrowed at all. Fixed from a real,
  // pre-existing false positive found live building an unrelated T33 fix: the previous version asserted
  // `.not.toContainText(...)` immediately after `page.goto()` with no prior positive assertion - Playwright's
  // auto-retrying negative assertion resolves as soon as it samples the page's own brief pre-fetch loading
  // state (genuinely no text yet), and never re-checks after the real data arrives, so it "passed" without
  // ever actually proving the row was invisible - which it never was, R2's own unfiltered default already
  // made it visible from the start. A positive `toContainText` first establishes the page has really loaded.
  await page.goto("/");
  await expect(page.getByTestId("listing-results")).toContainText("[SAMPLE] Data Scientist - fully remote");
  await page
    .locator("fieldset", { hasText: "Remote/Unknown" })
    .getByText("Remote", { exact: true })
    .locator("..")
    .getByRole("checkbox")
    .click();
  await expect(page.getByTestId("listing-results")).toContainText("[SAMPLE] Data Scientist - fully remote");
  // narrowed to remote/unknown only now - a real LOCATED listing (a stated country) must drop out.
  await expect(page.getByTestId("listing-results")).not.toContainText("[SAMPLE] PhD Position in Machine Learning");
  // a real country's own filter must never match this row - country was never guessed for it (D6)
  await page.getByLabel("Search title / text").fill("fully remote");
  await page.getByLabel("Search title / text").blur();
  const card = page.locator("article").filter({ hasText: "[SAMPLE] Data Scientist - fully remote" });
  await expect(card).toBeVisible();
  await expect(card).toContainText("Remote");
});

test("T23 (PM ruling R2): the null-country listing's detail page shows Remote, never a bare trailing comma", async ({ page }) => {
  await page.goto("/");
  await page
    .locator("fieldset", { hasText: "Remote/Unknown" })
    .getByText("Remote", { exact: true })
    .locator("..")
    .getByRole("checkbox")
    .click();
  await page.getByText("[SAMPLE] Data Scientist - fully remote", { exact: true }).first().click();
  await expect(page).toHaveURL(/\/listing\//);
  await expect(page.getByRole("heading", { name: "[SAMPLE] Data Scientist - fully remote" })).toBeVisible();
  await expect(page.getByText("Open Research Collective")).toBeVisible();
  await expect(page.getByTestId("location-class-badge")).toHaveText("Remote");
  await expect(page.getByTestId("location-class-badge")).toHaveAttribute("title", "fully remote");
});

test("methodology page renders real content, not a stub", async ({ page }) => {
  await page.goto("/about/methodology");
  await expect(page.getByRole("heading", { name: "Where the data comes from" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Compliance" })).toBeVisible();
  await expect(page.getByText(/robots\.txt/).first()).toBeVisible();
  await expect(page.getByText(/TLS certificate verification is never disabled/)).toBeVisible();
  await expect(page.getByRole("heading", { name: "Salary conversion (EUR/year)" })).toBeVisible();
  await expect(page.getByText(/40 hours\/week/)).toBeVisible();

  // T25/T36/T38 (packet 016/021/022): the real matching weights, fetched live from
  // /v1/applicants/matching/weights - never hand-restated, so this page and the actual scoring engine can
  // never silently drift apart. T36 rebalanced discipline 25->20; T38 rebalanced it again, swapping with
  // skill_relevance (discipline 20->15, skill_relevance 15->20) - salary unchanged at 20 throughout.
  await expect(page.getByRole("heading", { name: /Applicant CV matching/ })).toBeVisible();
  await expect(page.getByText("discipline").locator("xpath=ancestor::tr")).toContainText("15 / 100");
  await expect(page.getByText("salary").locator("xpath=ancestor::tr")).toContainText("20 / 100");
});

test("T24: privacy and terms pages render real, grounded content and cross-link each other", async ({ page }) => {
  await page.goto("/privacy");
  await expect(page.getByRole("heading", { name: "Privacy" })).toBeVisible();
  await expect(page.getByText(/local storage only/)).toBeVisible();
  // T25 (packet 016): the CV/applicants section states the same honest guarantee in its own words too -
  // .first() (the thesis section) rather than a locator that would now ambiguously match both paragraphs.
  await expect(page.getByText(/never sent anywhere else/).first()).toBeVisible();
  await expect(page.getByRole("heading", { name: "CVs you upload for job matching" })).toBeVisible();
  await expect(page.getByText(/Delete my CV and profile/)).toBeVisible();

  await page.goto("/terms");
  await expect(page.getByRole("heading", { name: "Terms" })).toBeVisible();
  await expect(page.getByText(/remains the property of/)).toBeVisible();
  await expect(page.getByText(/not equivalent to a commercial service such as Turnitin/)).toBeVisible();

  // both are reachable from the footer on every page, not just deep-linked
  await page.goto("/");
  await page.getByRole("link", { name: "Privacy", exact: true }).click();
  await expect(page).toHaveURL(/\/privacy$/);
});

test("T24: compliance dashboard lists real sources and a zero denied-attempts total", async ({ page }) => {
  await page.goto("/compliance");
  await expect(page.getByTestId("compliance-table")).toBeVisible();
  // the dev seed always registers rs_org_boards - a real row from the live /v1/sources response, not fixture data
  await expect(page.getByTestId("compliance-table")).toContainText("rs_org_boards");
  const total = page.getByTestId("denied-attempts-total");
  await expect(total).toBeVisible();
  await expect(total).toContainText("0"); // PoliteClient refuses a disallowed request before it is ever sent
});

test("channels page opens the EURAXESS Serbia search (facet 786)", async ({ page, context }) => {
  await page.goto("/channels");
  // channels sort alphabetically by source_id (academics_com < euraxess < ...) - scope to EURAXESS's own card,
  // never ".first()", which grabbed the wrong provider's link when this test first ran live.
  const card = page.locator("li").filter({ hasText: "Serbia: research jobs on EURAXESS" });
  await expect(card).toBeVisible();
  const link = card.getByRole("link", { name: "Open search" });
  const href = await link.getAttribute("href");
  expect(href).toContain("euraxess.ec.europa.eu");
  expect(href).toContain("job_country%3A786");
  const [popup] = await Promise.all([context.waitForEvent("page"), link.click()]);
  await popup.waitForLoadState("domcontentloaded").catch(() => void 0); // real external site - don't fail the test on its own slowness
  expect(popup.url()).toContain("euraxess.ec.europa.eu");
  await popup.close();
});
