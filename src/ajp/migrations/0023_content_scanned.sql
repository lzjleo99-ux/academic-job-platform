-- T31 (packet 019): rs_org_boards' own many document-linked rows (.pdf/.docx/.doc) get their body text
-- extracted now (pdfplumber/python-docx), not just the listing anchor's own title/teaser. Some of those real
-- documents are scanned images with no extractable text at all (PM's own real recon: all 4 open PDFs from
-- medf.kg.ac.rs) - OCR is explicitly out of scope (D6: never fabricate a value this project cannot actually
-- read), so these rows must be flagged honestly rather than silently left looking like every other row.
ALTER TABLE listing ADD COLUMN content_scanned boolean NOT NULL DEFAULT false;
-- Every row already ingested before this migration was built from listing-page text only (title/teaser),
-- never a downloaded document at all - false (its own DEFAULT) is already the correct, honest value for
-- every one of them; no backfill needed here (unlike 0020's location_class, which had a real prior state to
-- reclassify).

CREATE INDEX listing_content_scanned_idx ON listing (content_scanned) WHERE content_scanned;
