"""
Tests for the standalone CLI entry point (``src/load_pageviews_dump.py``).

These exercise the CLI's argument parsing, exit codes, and wiki-filtering
behavior -- as opposed to ``test_pageviews_dump_loader.py``, which tests the
underlying pipeline functions directly. Uses the same real-bz2-fixture
approach so this actually runs the full path: CLI args -> wikis.yaml ->
dump file -> SQLite cache.
"""

from __future__ import annotations

import bz2
from pathlib import Path

import pytest

from src.py_port.load_pageviews_dump import EXIT_DUMP_NOT_FOUND, EXIT_ERROR, EXIT_OK, main
from src.py_port.popularpages.dumps_parser.pageviews_dump_loader import PageviewsDumpLoader
from src.py_port.popularpages.pageviews.pageviews_db import PageviewsDb

FIXTURE_LINES = [
    "ar.wikipedia ! 199256 desktop 5 A1S1V1Y1^1",
    "ar.wikipedia ! 496583 desktop 5 A1B1D1I1\\1",
    "ar.wikipedia ! 199256 mobile-web 2 J2",
    "en.wikipedia Main_Page 15580374 desktop 1000 A100B200",
    "en.wikipedia Main_Page 15580374 mobile-web 500 A50B50",
    "aa.wikipedia Special:WantedPages null desktop 3 A1Q1^1",
]

WIKIS_YAML_CONTENT = """\
en.wikipedia:
    database: enwiki
    index: "User:Community Tech bot/Popular pages"
    config: "Wikipedia:WikiProject/Popular pages config.json"
    category: "Category:Lists of popular pages by WikiProject"

ar.wikipedia:
    database: arwiki
    index: "test index"
    config: "test config"
    category: "test category"
"""


@pytest.fixture
def project(tmp_path: Path):
    """Set up a small self-contained project layout: wikis.yaml + dump + views dir."""
    wikis_yaml = tmp_path / "wikis.yaml"
    wikis_yaml.write_text(WIKIS_YAML_CONTENT, encoding="utf-8")

    views_dir = tmp_path / "data" / "views"

    dumps_root = tmp_path / "dumps"

    loader = PageviewsDumpLoader(views_dir=views_dir, dumps_root=dumps_root)
    dump_file = loader._dump_path_for_month(2026, 7)

    dump_file.parent.mkdir(parents=True, exist_ok=True)
    with bz2.open(dump_file, "wt", encoding="utf-8") as f:
        for line in FIXTURE_LINES:
            f.write(line + "\n")  # pyright: ignore[reportArgumentType]

    return {
        "wikis_yaml": wikis_yaml,
        "dumps_root": dumps_root,
        "views_dir": views_dir,
    }


def _run(project, extra_args: list[str] | None = None) -> int:
    argv = [
        "--year",
        "2026",
        "--month",
        "7",
        # "--wikis-yaml", str(project["wikis_yaml"]),
        "--dumps-root",
        str(project["dumps_root"]),
        "--views-dir",
        str(project["views_dir"]),
    ]
    if extra_args:
        argv.extend(extra_args)
    return main(argv)


def test_cli_happy_path_writes_all_configured_wikis(project):
    exit_code = _run(project)
    assert exit_code == EXIT_OK

    ar_db = project["views_dir"] / "ar.wikipedia" / "2026-07.sqlite3"
    en_db = project["views_dir"] / "en.wikipedia" / "2026-07.sqlite3"
    assert ar_db.exists()
    assert en_db.exists()
    # aa.wikipedia is not in wikis.yaml -> must never be written.
    assert not (project["views_dir"] / "aa.wikipedia").exists()

    db = PageviewsDb(ar_db)
    try:
        assert db.get_views("!", []) == 12
    finally:
        db.close_db()


def test_cli_single_wiki_filter_writes_only_that_wiki(project):
    exit_code = _run(project, ["--wiki", "ar.wikipedia"])
    assert exit_code == EXIT_OK

    assert (project["views_dir"] / "ar.wikipedia" / "2026-07.sqlite3").exists()
    assert not (project["views_dir"] / "en.wikipedia").exists()


def test_cli_multiple_wiki_flags(project):
    exit_code = _run(project, ["--wiki", "ar.wikipedia", "--wiki", "en.wikipedia"])
    assert exit_code == EXIT_OK
    assert (project["views_dir"] / "ar.wikipedia" / "2026-07.sqlite3").exists()
    assert (project["views_dir"] / "en.wikipedia" / "2026-07.sqlite3").exists()


def test_cli_unknown_wiki_flag_errors_without_writing_anything(project):
    exit_code = _run(project, ["--wiki", "fr.wikipedia"])
    assert exit_code == EXIT_ERROR
    assert not project["views_dir"].exists()


def test_cli_missing_dump_returns_distinct_exit_code(project):
    exit_code = main(
        [
            "--year",
            "2099",
            "--month",
            "1",
            # "--wikis-yaml", str(project["wikis_yaml"]),
            "--dumps-root",
            str(project["dumps_root"]),
            "--views-dir",
            str(project["views_dir"]),
        ]
    )
    assert exit_code == EXIT_DUMP_NOT_FOUND
    # Nothing should have been written for a dump that doesn't exist.
    assert not project["views_dir"].exists()


def test_cli_invalid_month_rejected_by_argparse(project, capsys):
    with pytest.raises(SystemExit) as exc_info:
        main(
            [
                "--year",
                "2026",
                "--month",
                "13",
                # "--wikis-yaml", str(project["wikis_yaml"]),
                "--dumps-root",
                str(project["dumps_root"]),
                "--views-dir",
                str(project["views_dir"]),
            ]
        )
    # argparse itself exits with code 2 on invalid choice.
    assert exc_info.value.code == 2


def test_cli_rerun_is_idempotent(project):
    """Running the CLI twice must not double-count views (upsert, not insert)."""
    assert _run(project) == EXIT_OK
    assert _run(project) == EXIT_OK

    db = PageviewsDb(project["views_dir"] / "ar.wikipedia" / "2026-07.sqlite3")
    try:
        assert db.get_views("!", []) == 12
        assert db.one_title_views("!") == 12
    finally:
        db.close_db()
