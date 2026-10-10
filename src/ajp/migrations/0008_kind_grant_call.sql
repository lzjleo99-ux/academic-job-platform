-- packet 007 names the grant bucket 'grant_call' (packet 004 had 'grant'); align to the newer packet.
ALTER TABLE listing DROP CONSTRAINT listing_kind_check;
UPDATE listing SET kind = 'grant_call' WHERE kind = 'grant';
ALTER TABLE listing ADD CONSTRAINT listing_kind_check
  CHECK (kind IN ('employment','enrollment','grant_call','internship','other'));
