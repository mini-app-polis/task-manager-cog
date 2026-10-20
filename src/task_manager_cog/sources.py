"""Where tasks come from, and how each source's tasks get their id.

One prefix per source, and one writer per prefix. This cog is the only
writer for every prefix listed here, and its queue delivers one job at a
time, so minting "the highest id in use plus one" cannot hand out the same
id twice. Ids written by hand (in the board's own UI) use a prefix that is
deliberately absent from this table, so the two never meet.

A source that already has an id of its own — an issue's ``repo#n`` — brings
it, and nothing is minted. The request contract
(:data:`mini_app_polis.api.contract.GIVEN_ID_SOURCES`) says which sources must
carry an id; this table says what to do with the rest. A test holds the two
in step.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Source:
    """How one source's tasks are named.

    ``prefix`` is the id prefix this cog mints for the source, or ``None``
    for a source that brings its own id.
    """

    name: str
    prefix: str | None


SOURCES: dict[str, Source] = {
    "voicenote": Source("voicenote", prefix="VN"),
    "github-issue": Source("github-issue", prefix=None),
}
