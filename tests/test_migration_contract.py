from telegram_phone_number_checker.database import POSTGRES_MIGRATIONS


def test_postgres_migration_versions_are_unique_and_ordered():
    versions = [version for version, _ in POSTGRES_MIGRATIONS]
    assert len(versions) == len(set(versions))
    numeric = [int(version.split("_", 1)[0]) for version in versions]
    assert numeric == sorted(numeric)
    assert numeric == list(range(numeric[0], numeric[-1] + 1))


def test_postgres_migrations_are_nonempty():
    assert all(version and statements for version, statements in POSTGRES_MIGRATIONS)
    assert all(statement.strip() for _, statements in POSTGRES_MIGRATIONS for statement in statements)
