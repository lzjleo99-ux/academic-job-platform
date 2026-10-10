-- T21: scheduler columns on source. A 'channel' source is a deeplink, never scheduled/fetched (the
-- listing_rejects_channel_source trigger from 0006 already refuses any listing insert from one) - defaulting
-- enabled=true here is harmless for a channel row since the scheduler's own due-source query (T21,
-- ajp.scheduler) additionally excludes kind='channel' outright, but it is set false explicitly for every
-- existing channel row below so a future reader of the table never has to know that second rule to get the
-- right answer just by looking at this column.
ALTER TABLE source ADD COLUMN enabled boolean NOT NULL DEFAULT true;
ALTER TABLE source ADD COLUMN cadence_hours integer NOT NULL DEFAULT 24 CHECK (cadence_hours > 0);
-- Per-source board configuration the generic connector factory (T21, ajp.scheduler.build_connector) reads
-- to reconstruct the right WordPressBoardConfig/SitemapBoardConfig/etc instance from a plain DB row - the
-- SAME config shape each family's own dataclass already accepts, stored as-is rather than re-derived.
ALTER TABLE source ADD COLUMN config jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(config) = 'object');

UPDATE source SET enabled = false WHERE kind = 'channel';
