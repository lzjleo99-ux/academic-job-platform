"""GET /v1/channels - external deeplink search channels (EURAXESS, academicpositions Wave B, Jobbnorge,
blocked hosts, ...). packet 007: 'the UI never proxies these' - the API only ever returns the curated URL,
it never fetches a channel host itself (those sources have kind='channel'; ingestion refuses them outright)."""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends
from psycopg import Connection

from ajp.api.deps import get_conn
from ajp.api.schemas import ChannelLinkOut

router = APIRouter(prefix="/v1", tags=["channels"])


@router.get("/channels", response_model=List[ChannelLinkOut])
def list_channels(conn: Connection = Depends(get_conn)) -> List[ChannelLinkOut]:
    rows = conn.execute(
        """SELECT cl.source_id, cl.slug, cl.label, cl.url, cl.country_iso2, cl.description
           FROM channel_link cl JOIN source s ON s.id = cl.source_id
           WHERE s.kind = 'channel' ORDER BY cl.source_id, cl.slug"""
    ).fetchall()
    return [ChannelLinkOut(**dict(r)) for r in rows]
