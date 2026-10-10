-- Which extraction route produced a row, so the API/UI can down-rank weak ones (anchor + undated, document attachments).
ALTER TABLE listing ADD COLUMN extraction_family text
  CHECK (extraction_family IS NULL OR extraction_family <> '');
CREATE INDEX listing_extraction_family_idx ON listing (extraction_family);
