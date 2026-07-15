from yt_agent.catalog import CatalogStore
from yt_agent.comments import index_comments, search_comments


def _payload():
    return {
        "id": "abc123def45",
        "title": "Demo",
        "channel": "Channel",
        "extractor_key": "youtube",
        "comments": [
            {
                "id": "comment-1",
                "author": "Alice\x1b[31m",
                "text": "Great\nprivate tutorial",
                "like_count": 4,
                "timestamp": 1_700_000_000,
            },
            {"id": "comment-2", "author": "Bob", "text": "Second", "like_count": 1},
        ],
    }


def test_index_comments_is_bounded_sanitized_and_searchable(settings) -> None:
    report = index_comments(
        settings,
        "abc123def45",
        limit=1,
        fetch_fn=lambda target, limit: _payload(),
    )

    assert report.fetched == 2
    assert report.indexed == 1
    rows = search_comments(settings, "private")
    assert len(rows) == 1
    assert rows[0]["author"] == "Alice"
    assert rows[0]["text"] == "Great private tutorial"


def test_comments_dry_run_does_not_create_catalog(settings) -> None:
    report = index_comments(
        settings,
        "abc123def45",
        limit=10,
        dry_run=True,
        fetch_fn=lambda target, limit: _payload(),
    )

    assert report.dry_run is True
    assert not settings.catalog_file.exists()


def test_replacing_comments_removes_stale_fts_rows(settings) -> None:
    index_comments(settings, "abc123def45", fetch_fn=lambda target, limit: _payload())
    empty = {**_payload(), "comments": []}
    index_comments(settings, "abc123def45", fetch_fn=lambda target, limit: empty)

    assert CatalogStore(settings.catalog_file, readonly=True).search_comments("private") == []


def test_comment_index_tolerates_non_finite_remote_numbers(settings) -> None:
    payload = _payload()
    payload["comments"] = [
        {
            "id": "hostile",
            "author": "Remote",
            "text": "numeric edge",
            "like_count": float("inf"),
            "timestamp": float("inf"),
        }
    ]

    index_comments(settings, "abc123def45", fetch_fn=lambda target, limit: payload)

    row = search_comments(settings, "numeric")[0]
    assert row["like_count"] == 0
    assert row["published_at"] is None
