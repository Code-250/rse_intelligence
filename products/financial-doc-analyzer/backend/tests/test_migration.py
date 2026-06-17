"""
DB-free sanity checks for the FDA-002 initial migration.

These assert the migration is wired correctly and reversible in shape (both
upgrade and downgrade defined, correct revision chain) without needing a live
PostgreSQL connection. Full apply/rollback is exercised in integration CI where
a database is available.
"""
import importlib.util
from pathlib import Path

MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "0001_initial_schema.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("fda_initial_migration", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_file_exists():
    assert MIGRATION.exists(), "Initial migration file is missing"


def test_revision_chain_is_base():
    mod = _load_migration()
    assert mod.revision == "0001_initial_schema"
    assert mod.down_revision is None  # first migration in the chain


def test_upgrade_and_downgrade_defined():
    mod = _load_migration()
    assert callable(mod.upgrade)
    assert callable(mod.downgrade)


def test_all_four_tables_are_created_and_dropped():
    """The migration source must create and drop all four fda_ tables."""
    source = MIGRATION.read_text(encoding="utf-8")
    for table in ("fda_users", "fda_documents", "fda_analyses", "fda_usage"):
        assert f'create_table(\n        "{table}"' in source or f'"{table}"' in source
        assert f'drop_table("{table}")' in source
