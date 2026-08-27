Popular Pages
=============

A tool for generating monthly "most popular pages" reports for WikiProjects.

This is the Python port of [MrIbrahem/popularpages](https://github.com/MrIbrahem/popularpages)
(originally PHP). See [the tool's homepage](https://wikitech.wikimedia.org/wiki/Tool:Popular_Pages)
for more information.

##### Setting up the bot
* Copy `config.ini.example` to `config.ini` and add the bot's username and password.
* Run `pip install -e ".[dev]"` (or `uv pip install -e ".[dev]"`) from the command line.
* Either run the bot manually or set up a cron job to run it once a month.

##### How does the bot work?
* Fetches config from [an on-wiki config page](https://en.wikipedia.org/wiki/User:Community_Tech_bot/Popular_pages_config.json) (example for English Wikipedia).
* Runs on all projects listed in the config, compiling pageviews statistics for the previous month.
* Updates [the info page on wiki](https://en.wikipedia.org/wiki/User:Community_Tech_bot/Popular_pages) with the timestamp of the page update.

##### App structure:
* **`src/popularpages/cli/check_reports.py`** (`popularpages-check`): Starting point for a new bot run. Gets config info for all projects not already updated for the past month and then passes it to `ReportUpdater`.
* **`src/popularpages/cli/generate_report.py`** (`popularpages-report`): Script to manually regenerate a report for a single project.
* **`src/popularpages/cli/generate_index.py`** (`popularpages-index`): Script for generating the index page.
* **`src/popularpages/report_updater.py`**: The module that actually updates projects.
* **`src/popularpages/wiki_repository.py`**: Contains all helper functions for dealing with the API and Database (bit of a misnomer).
* **`src/popularpages/pageviews_repository.py`**: Contains all helper functions for dealing with the Pageviews API.
* **`src/popularpages/logger.py`**: Responsible for logging updates to the files in the `logs` directory.
* **`src/popularpages/i18n.py`**: Minimal replacement for the Intuition translation service.

##### Dependencies
| PHP dependency | Python replacement |
|---|---|
| `addwiki/mediawiki-api-base` | `mwclient` |
| `krinkle/intuition` | `src/popularpages/i18n.py` |
| `twig/twig` | `Jinja2` |
| `symfony/yaml` | `PyYAML` |
| `ext-mysqli` | `PyMySQL` |
| `caseyamcl/guzzle_retry_middleware` | `tenacity` |
| Guzzle async/promises | `httpx.AsyncClient` + `asyncio.gather` |
| `phpunit` | `pytest` |
| `mediawiki-codesniffer` | `ruff` |

##### Setting up a new wiki
* Make sure the translations for the language are in the `messages` directory.
* Add the configuration for the project in `config/wikis.yaml`. This indicates where the WikiProjects configuration and index pages live.
* Add your WikiProjects configuration on the corresponding on-wiki JSON page.
* Add a new cron job for the wiki, such as `0 0 1 * * popularpages-check en.wikipedia`.
