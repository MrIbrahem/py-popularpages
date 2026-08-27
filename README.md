# Popular Pages (Python)

![CI](https://github.com/wikimedia/popularpages/workflows/CI/badge.svg)

A tool for generating monthly "most popular pages" reports for WikiProjects.

See [the tool's homepage](https://wikitech.wikimedia.org/wiki/Tool:Popular_Pages) for more information.

This is a Python port of [the original PHP implementation](https://github.com/MrIbrahem/popularpages), using
[`mwclient`](https://github.com/mwclient/mwclient) for MediaWiki API access
and [Jinja2](https://jinja.palletsprojects.com/) for report templating.

##### Setting up the bot

-   Copy `config.ini.example` to `config.ini` and add the bot's username and password.
-   Run `pip install -e ".[dev]"` (or `uv pip install -e ".[dev]"`) from the command line.
-   Either run the bot manually or set up a cron job to run it once a month.

##### How does the bot work?

-   Fetches config from [on wiki config page](https://en.wikipedia.org/wiki/User:Community_Tech_bot/Popular_pages_config.json) (example for English Wikipedia).
-   Runs on all projects listed in the config, compiling pageviews statistics for the previous month.
-   Updates [the info page on wiki](https://en.wikipedia.org/wiki/User:Community_Tech_bot/Popular_pages) with the timestamp of the page update.

##### App structure:

-   **`src/popularpages/cli/check_reports.py`**: Starting point for a new bot run. Gets config info for all projects not already updated for past month and then passes it to `ReportUpdater`. Also available as the `popularpages-check` console script.
-   **`src/popularpages/cli/generate_report.py`**: Script to manually regenerate a report for a single project. Also available as `popularpages-report`.
-   **`src/popularpages/cli/generate_index.py`**: Script for generating the index page. Also available as `popularpages-index`.
-   **`src/popularpages/report_updater.py`**: The module that actually updates projects.
-   **`src/popularpages/wiki_repository.py`**: Contains all helper functions for dealing with the MediaWiki API (via `mwclient`) and the replica database.
-   **`src/popularpages/pageviews_repository.py`**: Contains all helper functions for dealing with the Pageviews API.
-   **`src/popularpages/logger.py`**: Responsible for logging updates to the files in the `logs` directory.
-   **`src/popularpages/i18n.py`**: Minimal message-translation layer reading `messages/*.json`.

##### Dependencies

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

##### Setting up a new wiki

-   Make sure the translations for the language are in the `/messages` directory.
-   Add the configuration for the project in `config/wikis.yaml`. This indicates where the WikiProjects configuration and index pages live.
-   Add your WikiProjects configuration on the corresponding on-wiki JSON page.
-   Add a new cron job for the wiki, such as `0 0 1 * * popularpages-check --wiki en.wikipedia`.

##### Running tests

```sh
pip install -e ".[dev]"
pytest
```

Note: most tests in `tests/test_wiki_repository.py` hit the live English
Wikipedia API (and, where applicable, the replica database) and require a
valid `config.ini`, matching the behavior of the original PHP test suite.
