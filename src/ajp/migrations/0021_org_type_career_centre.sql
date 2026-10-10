-- T25/T27 (packet 016 PM ruling on razvojkarijere, 2026-09-28): University of Belgrade Career Centre
-- ('Centar za razvoj karijere i savetovanje studenata Univerziteta u Beogradu') is neither a university nor
-- a faculty/institute/lab/company/funding_body - a genuinely new, real org TYPE this project has not needed
-- before, not a mismatch to paper over with the nearest existing value (D6: never misrepresent what a real
-- thing actually is).
ALTER TABLE org_master DROP CONSTRAINT org_master_type_check;
ALTER TABLE org_master ADD CONSTRAINT org_master_type_check CHECK (type IN
  ('university','faculty','institute','lab','company','funding_body','career_centre'));
