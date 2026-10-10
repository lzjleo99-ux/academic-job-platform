-- T18: 'persist detected ATS + tenant/site ids on source row' (packet 009's own architecture_ruling).
-- ats_family is the detector's own vocabulary (ats_workday_cxs, ats_smartrecruiters, ats_teamtailor,
-- ats_reachmee_varbi, ats_successfactors, ats_taleo, ats_icims, ats_greenhouse, ats_lever, ats_jobteaser,
-- gen_wordpress, gen_sitemap_html, board, channel, ...) - not constrained to a fixed enum here since new
-- families are added in code, not by migration.
ALTER TABLE source
  ADD COLUMN ats_family text,
  ADD COLUMN ats_tenant text,
  ADD COLUMN ats_site   text;

-- T18 new governance rule (t18_ats_corrections.json): a TDM/legal opt-out declared in robots.txt comments or
-- site terms (EU DSM Art.4 / section-44b-UrhG style 'automated access prohibited without permission') forces
-- deeplink_only even when every path is technically robots-allowed - academics.com is the fixture case.
-- 'declared' must never be set without the operator_actions.md entry the ruling also requires (enforced by
-- convention/review, not a DB constraint - the two live in different systems).
ALTER TABLE source ADD COLUMN tdm_reservation text NOT NULL DEFAULT 'none'
  CHECK (tdm_reservation IN ('none', 'declared'));

CREATE INDEX source_ats_family_idx ON source (ats_family);
