"""Package data + small data-bound modules.

This subpackage holds files we want to ship with the wheel (curated YAML
universes, lookup tables) and the loader code that turns them into typed
Python objects. Anything bigger or more dynamic — runtime quotes, ingested
NHC payloads — lives in ``models/`` (database) or is fetched on demand.
"""
