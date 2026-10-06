# Foundation verification

The template was integrated from revision `7ae2249` into the existing repository.
A fresh bootstrap installed the locked Python/frontend dependencies, created the
ignored local `.env` and SQLite database, migrated Django, compiled CSS and
passed system checks. Running bootstrap again preserved the environment and
existing database; migrations were already applied.

The 74 inherited setup, configuration, process-lifecycle and demo tests passed.
They live in `starter_tests/` to avoid colliding with the original pytest suite.
Catalogue behaviour is tested separately in `tests_web/`. The template's demo
browser script is retained as upstream reference until the catalogue browser
check replaces it under issue #5/#6.

Original email/MCP dependencies are an optional `legacy` extra. The default web
installation does not need Gmail credentials or an LLM key. Automatic digest
GitHub Actions were removed when adopting the application shell.
