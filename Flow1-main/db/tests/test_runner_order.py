"""Exercises the REAL runner.py discovery/validation code.
psycopg import is stubbed; no database is required.
"""

import sys
import types
from pathlib import Path


# Stub psycopg so runner.py can be imported without installing psycopg.
stub = types.ModuleType("psycopg")
stub.Connection = type("Connection", (), {})
stub.Error = type("Error", (Exception,), {})
sys.modules["psycopg"] = stub


if len(sys.argv) < 2:
    print("Usage: python test_runner_order.py <migrations_directory>")
    sys.exit(2)


sql = Path(sys.argv[1]).resolve()

if not sql.is_dir():
    print(f"Migration directory does not exist: {sql}")
    sys.exit(2)


sys.path.insert(0, str(sql))

import runner


ms, seeds = runner.discover_files(sql)

fails = []


def check(name, condition):
    print(("ok   " if condition else "FAIL ") + name)
    if not condition:
        fails.append(name)


# ---------------------------------------------------------
# Migration discovery and ordering
# ---------------------------------------------------------

expected_migrations = ["001", "002", "003", "004", "005", "006"]

check(
    "migrations are 001,002,003,004,005,006 in order",
    [migration.version for migration in ms] == expected_migrations,
)

# ---------------------------------------------------------
# Seed discovery and ordering
# ---------------------------------------------------------

check(
    "seeds are 998,999 in numeric order (998 first)",
    [seed.version for seed in seeds] == ["998", "999"],
)

check(
    "998 is a seed (not recorded as migration)",
    all(migration.version != "998" for migration in ms),
)

# ---------------------------------------------------------
# Pending migrations
# ---------------------------------------------------------

check(
    "fresh DB: all six pending in order",
    [
        migration.version
        for migration in runner.find_pending(ms, {})
    ] == expected_migrations,
)

by_version = {migration.version: migration for migration in ms}


def applied_through(version):
    versions = expected_migrations[:expected_migrations.index(version) + 1]
    return {
        item: (by_version[item].name, by_version[item].checksum)
        for item in versions
    }


ap = applied_through("002")
ap3 = applied_through("003")
ap4 = applied_through("004")
ap5 = applied_through("005")
ap6 = applied_through("006")

check(
    "001+002 applied: 003,004,005,006 pending",
    [
        migration.version
        for migration in runner.find_pending(ms, ap)
    ] == ["003", "004", "005", "006"],
)

check(
    "001-003 applied: 004,005,006 pending",
    [
        migration.version
        for migration in runner.find_pending(ms, ap3)
    ] == ["004", "005", "006"],
)

check(
    "001-004 applied: 005,006 pending",
    [
        migration.version
        for migration in runner.find_pending(ms, ap4)
    ] == ["005", "006"],
)

check(
    "001-005 applied: only 006 pending",
    [
        migration.version
        for migration in runner.find_pending(ms, ap5)
    ] == ["006"],
)

check(
    "001-006 applied: nothing pending",
    runner.find_pending(ms, ap6) == [],
)

# ---------------------------------------------------------
# Migration names
# ---------------------------------------------------------

expected_names = {
    "003": "org_code_and_username",
    "004": "pre_invoices",
    "005": "cancel_constraints",
    "006": "request_cancel_guard",
}

for version, expected_name in expected_names.items():
    migration = by_version.get(version)
    check(
        f"{version} is named {expected_name}",
        migration is not None and migration.name == expected_name,
    )

# ---------------------------------------------------------
# Out-of-order migration protection
# ---------------------------------------------------------

try:
    runner.find_pending(
        ms,
        {
            "001": ap["001"],
            "003": (
                by_version["003"].name,
                by_version["003"].checksum,
            ),
        },
    )
    check(
        "003 applied without 002 is refused",
        False,
    )

except RuntimeError as exc:
    check(
        "003 applied without 002 is refused with out-of-order error",
        "Out-of-order migration(s)" in str(exc),
    )

# ---------------------------------------------------------
# Checksum validation
# ---------------------------------------------------------

for version, applied in (
    ("003", ap3),
    ("004", ap4),
    ("005", ap5),
    ("006", ap6),
):
    migration = by_version[version]
    corrupted = {
        **applied,
        version: (migration.name, "0" * 64),
    }

    try:
        runner.validate_applied_migrations(ms, corrupted)
        check(
            f"edited {version} detected by checksum",
            False,
        )

    except RuntimeError as exc:
        check(
            f"edited {version} detected by checksum",
            "Checksum mismatch" in str(exc),
        )

# ---------------------------------------------------------
# Final result
# ---------------------------------------------------------

print()

if fails:
    print(f"FAILED: {len(fails)} check(s)")
    for failure in fails:
        print(f" - {failure}")
    sys.exit(1)

print("PASSED: all migration runner checks")
sys.exit(0)