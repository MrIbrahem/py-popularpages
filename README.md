# Popular Pages (Python)

![CI](https://github.com/wikimedia/popularpages/workflows/CI/badge.svg)

A tool for generating monthly "most popular pages" reports for WikiProjects.

This is a Python port of [the original PHP implementation](https://github.com/MrIbrahem/popularpages).
See [the tool's homepage](https://wikitech.wikimedia.org/wiki/Tool:Popular_Pages) for more information.

## Quick start

```sh
# 1. Install (from the repo root)
pip install -e ".[dev]"

# 2. Add your bot credentials
cp config.ini.example config.ini
#    then edit config.ini with your bot username/password (from Special:BotPasswords)

# 3. Run a full update cycle
python -m popularpages.cli.check_reports --wiki en.wikipedia
```

## Usage

All commands run as `python -m <module>` and accept a `--dry-run` flag to print
output instead of writing to the wiki.

### Update all stale reports (`check_reports`)

Checks every configured wiki (or just `--wiki`) for WikiProjects not yet updated
this month and regenerates their reports. This is what the monthly cron job calls.

```sh
# All wikis
python -m popularpages.cli.check_reports

# One wiki
python -m popularpages.cli.check_reports --wiki en.wikipedia

# Preview only, no wiki edits
python -m popularpages.cli.check_reports --wiki en.wikipedia --dry-run
```

### Regenerate one project (`generate_report`)

Manually rebuild the report for a single WikiProject (e.g. to re-run a failed
project or for testing).

```sh
python -m popularpages.cli.generate_report --wiki en.wikipedia --project Dinosaurs
python -m popularpages.cli.generate_report --wiki en.wikipedia --project Dinosaurs --dry-run
```

### Regenerate the index page (`generate_index`)

Rebuild only the wiki's index page that lists all its WikiProject reports.

```sh
python -m popularpages.cli.generate_index --wiki en.wikipedia
python -m popularpages.cli.generate_index --wiki en.wikipedia --dry-run
```

## How it works

- Fetches config from the on-wiki JSON config page (e.g.
  [English Wikipedia's config](https://en.wikipedia.org/wiki/User:Community_Tech_bot/Popular_pages_config.json)).
- Runs on all projects listed in the config, compiling pageviews statistics for
  the previous month.
- Updates [the info page on wiki](https://en.wikipedia.org/wiki/User:Community_Tech_bot/Popular_pages)
  with the timestamp of the page update.

Typically you run it once a month via cron, e.g.:

```cron
0 0 1 * * python -m popularpages.cli.check_reports --wiki en.wikipedia
```

## Setting up a new wiki

- Make sure the translations for the language are in the `messages/` directory.
- Add the project's configuration in `config/wikis.yaml`, indicating where the
  WikiProjects config and index pages live.
- Add your WikiProjects configuration on the corresponding on-wiki JSON page.
- Add a cron job for the wiki, e.g.
  `0 0 1 * * python -m popularpages.cli.check_reports --wiki en.wikipedia`.

## Project layout

- `src/popularpages/cli/check_reports.py` — Entry point for a full bot run.
- `src/popularpages/cli/generate_report.py` — Manually regenerate one project.
- `src/popularpages/cli/generate_index.py` — Generate the index page.
- `src/popularpages/report_updater.py` — The module that updates projects.
- `src/popularpages/wiki_repository.py` — MediaWiki API + replica DB helpers.
- `src/popularpages/pageviews_repository.py` — Pageviews API helpers.
- `src/popularpages/logger.py` — Logging updates to the `logs/` directory.
- `src/popularpages/i18n.py` — Minimal message-translation layer (`messages/*.json`).

## Dependencies

| PHP dependency                      | Python replacement                     |
| ----------------------------------- | -------------------------------------- |
| `addwiki/mediawiki-api-base`        | `mwclient`                             |
| `krinkle/intuition`                 | `src/popularpages/i18n.py`             |
| `twig/twig`                         | `Jinja2`                               |
| `symfony/yaml`                      | `PyYAML`                               |
| `ext-mysqli`                        | `PyMySQL`                              |
| `caseyamcl/guzzle_retry_middleware` | `tenacity`                             |
| Guzzle async/promises               | `httpx.AsyncClient` + `asyncio.gather` |
| `phpunit`                           | `pytest`                               |
| `mediawiki-codesniffer`             | `ruff`                                 |

## Running tests

```sh
pip install -e ".[dev]"
pytest
```

Note: most tests in `tests/test_wiki_repository.py` hit the live English
Wikipedia API (and, where applicable, the replica database) and require a
valid `config.ini`, matching the behavior of the original PHP test suite.
