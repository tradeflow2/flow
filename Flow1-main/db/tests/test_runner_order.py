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

expected_migrations = ["001", "002", "003", "004", "005"]

check(
    "migrations are 001,002,003,004,005 in order",
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
    "fresh DB: all five pending in order",
    [
        migration.version
        for migration in runner.find_pending(ms, {})
    ] == expected_migrations,
)

ap = {
    migration.version: (migration.name, migration.checksum)
    for migration in ms[:2]
}

check(
    "001+002 applied: 003,004,005 pending",
    [
        migration.version
        for migration in runner.find_pending(ms, ap)
    ] == ["003", "004", "005"],
)

ap3 = {
    migration.version: (migration.name, migration.checksum)
    for migration in ms[:3]
}

check(
    "001-003 applied: 004 and 005 pending",
    [
        migration.version
        for migration in runner.find_pending(ms, ap3)
    ] == ["004", "005"],
)

ap4 = {
    migration.version: (migration.name, migration.checksum)
    for migration in ms[:4]
}

check(
    "001-004 applied: only 005 pending",
    [
        migration.version
        for migration in runner.find_pending(ms, ap4)
    ] == ["005"],
)

# ---------------------------------------------------------
# Migration names
# ---------------------------------------------------------

check(
    "004 is named pre_invoices",
    ms[3].name == "pre_invoices",
)

check(
    "005 is named cancel_constraints",
    ms[4].name == "cancel_constraints",
)

# ---------------------------------------------------------
# Out-of-order migration protection
# ---------------------------------------------------------

try:
    runner.find_pending(
        ms,
        {
            "001": ap["001"],
            "003": (ms[2].name, ms[2].checksum),
        },
    )
    check(
        "003 applied without 002 is refused",
        False,
    )

except Exception as exc:
    check(
        "003 applied without 002 -> 002 refused (no out-of-order): "
        + str(exc)[:60],
        True,
    )

# ---------------------------------------------------------
# Checksum validation
# ---------------------------------------------------------

try:
    runner.validate_applied_migrations(
        ms,
        {
            **ap,
            "003": (ms[2].name, "0" * 64),
        },
    )
    check(
        "edited 003 detected by checksum",
        False,
    )

except RuntimeError as exc:
    check(
        "edited 003 detected by checksum",
        "Checksum mismatch" in str(exc),
    )


try:
    runner.validate_applied_migrations(
        ms,
        {
            **ap3,
            "004": (ms[3].name, "0" * 64),
        },
    )
    check(
        "edited 004 detected by checksum",
        False,
    )

except RuntimeError as exc:
    check(
        "edited 004 detected by checksum",
        "Checksum mismatch" in str(exc),
    )


try:
    runner.validate_applied_migrations(
        ms,
        {
            **ap4,
            "005": (ms[4].name, "0" * 64),
        },
    )
    check(
        "edited 005 detected by checksum",
        False,
    )

except RuntimeError as exc:
    check(
        "edited 005 detected by checksum",
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
