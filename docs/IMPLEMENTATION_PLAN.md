# Event Finder implementation plan

Build a generic Django event catalogue, initially populated with London events.
Cities, categories, source endpoints, time zones and refresh intervals are data.
Source-specific formats live in reusable connectors. Public pages query SQLite;
imports run separately and never send email.

## Working agreement

- Every feature/requirement belongs to a GitHub issue with 4–7 focused commits.
- Push immediately after every commit, before starting the next change.
- Work on `main`. If a branch is needed, first create a separate merge-back issue.
- Record discovered bugs and unintended gaps as separate issues in this plan.
- Never commit `.env`, the SQLite catalogue or its journal/WAL files.
- Preserve migrations, deterministic fixtures and source provenance.

## Delivery sequence

- [Foundation: integrate the refreshed Django/Tailwind starter](https://github.com/pranman/event_finder/issues/1) — four planned commits; see issue for acceptance criteria.
- [Catalogue: configurable cities, categories, events, and source administration](https://github.com/pranman/event_finder/issues/2) — four planned commits; see issue for acceptance criteria.
- [Ingestion: repeatable imports with stable identity and source health](https://github.com/pranman/event_finder/issues/3) — four planned commits; see issue for acceptance criteria.
- [London sources: working configurable event connectors and live catalogue](https://github.com/pranman/event_finder/issues/4) — four planned commits; see issue for acceptance criteria.
- [Browser: responsive event discovery by city and date](https://github.com/pranman/event_finder/issues/5) — four planned commits; see issue for acceptance criteria.
- [Operations: scheduled refreshes, CI, and integration handover](https://github.com/pranman/event_finder/issues/6) — four planned commits; see issue for acceptance criteria.

## Architecture

`Source configuration → connector → normalized payload → catalogue → Django views`

Django owns cities, categories, events, source listings and import runs. Connectors
raise failures rather than treating them as empty successful imports. Provider IDs
are stable within a source; cross-source matches must respect city, venue and
session time. Unknown times and prices remain unknown.

## Foundation

Template: https://github.com/pranman/django-tailwind-daisyui-template
Pinned revision: `7ae2249ed852fbf57a604a1c2f879d22578c764f`.
Integrate its Django 5.2 / Tailwind 4 / daisyUI 5 structure and locked setup workflow
into this repository. Retain the original collection code as a legacy interface;
the web app uses the new configurable import service.

## Verification

Check fresh setup and safe reruns; migrations; generic city/model validation;
repeat and changed imports; failed sources; local date boundaries; catalogue
filters and escaping; CSS build; browser layouts and keyboard access. Verify at
least one real public London source independently of mocked fixtures. Keep any
credential-required platform setup explicit in the integration handover.

## Discovered issues

None recorded yet. Add each discovered bug/gap here with its own issue and plan.
