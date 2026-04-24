"""Dev-only utilities — seed scripts, manual fixtures, debugging helpers.

Nothing in this package is imported from production paths. It exists so
dashboard developers can see a populated map during the April-through-May
off-season without pointing at the live NHC feed. Any function here that
writes to the database should refuse to run against a Postgres URL — see
``_require_sqlite`` in each script.
"""
