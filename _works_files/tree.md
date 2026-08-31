```
src/
├── config/
│   ├── example_Popular_pages_config.json
│   └── wikis.yaml
├── messages/
│   ├── ar.json
│   └── en.json
├── popular_pages_dir/
│   ├── data/
│   │   └── views/
│   └── logs/
├── py_port/
│   ├── popularpages/
│   │   ├── db_analytics/
│   │   │   ├── __init__.py
│   │   │   ├── client.py
│   │   │   ├── maps.py
│   │   │   └── replica_db.py
│   │   ├── pageviews/
│   │   │   ├── __init__.py
│   │   │   ├── pageviews_cache.py
│   │   │   ├── pageviews_db.py
│   │   │   ├── pageviews_models.py
│   │   │   └── pageviews_repository.py
│   │   ├── report_updater/
│   │   │   ├── __init__.py
│   │   │   ├── index_updater.py
│   │   │   └── updater.py
│   │   ├── wiki_repository/
│   │   │   ├── __init__.py
│   │   │   └── repository.py
│   │   ├── __init__.py
│   │   ├── config.py
│   │   ├── i18n.py
│   │   ├── logger.py
│   │   ├── logger_config.py
│   │   ├── mapping.py
│   │   ├── utils.py
│   │   └── wiki_database_repository.py
│   ├── scripts/
│   │   ├── __init__.py
│   │   └── migrate_jsonl_to_sqlite.py
│   ├── __init__.py
│   ├── check_reports.py
│   ├── generate_index.py
│   └── generate_report.py
├── views/
│   ├── index.wikitext.jinja
│   └── report.wikitext.jinja
└── __init__.py

```