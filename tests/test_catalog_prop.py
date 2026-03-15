from __future__ import annotations

import re
import sqlite3

from hypothesis import given
from hypothesis import strategies as st

from yt_agent.catalog import _fts_query

TEXT = st.text(alphabet=st.characters(blacklist_categories=("Cs",)), max_size=80)


def _sanitized_tokens(query: str) -> list[str]:
    return [re.sub(r"[^\w\-]", "", token) for token in query.split() if re.sub(r"[^\w\-]", "", token)]


@given(query=TEXT)
def test_fts_query_only_emits_quoted_safe_tokens(query: str) -> None:
    result = _fts_query(query)
    expected = _sanitized_tokens(query)

    assert result.split() == [f'"{token}"' for token in expected]
    assert all(re.fullmatch(r'"[\w\-]+"', token) for token in result.split())


@given(query=TEXT)
def test_fts_query_can_be_used_in_sqlite_fts_match(query: str) -> None:
    fts_query = _fts_query(query)
    if not fts_query:
        assert fts_query == ""
        return

    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE VIRTUAL TABLE docs USING fts5(content)")
        conn.executemany(
            "INSERT INTO docs (content) VALUES (?)",
            [
                ("hello world chapter-title prefix",),
                ("another row for unicode terms",),
            ],
        )

        row = conn.execute("SELECT COUNT(*) FROM docs WHERE docs MATCH ?", (fts_query,)).fetchone()
    finally:
        conn.close()

    assert row is not None
    assert isinstance(row[0], int)
