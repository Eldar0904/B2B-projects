"""Diagnose the database connection and tell the two PostgreSQL servers apart.

    cd backend
    python -m scripts.check_db              # diagnose
    python -m scripts.check_db --create     # also create the missing database

--- The problem this exists to solve ------------------------------------

Two things go wrong together on a Russian/Kazakh Windows machine:

1. There are often TWO PostgreSQL servers: a native Windows install (which
   pgAdmin4 is usually registered against, holding your older databases)
   and the Docker container from docker-compose. Only one can own port
   5432. So `docker ps` shows product_matching_db running, pgAdmin4 shows
   your old databases, and neither is lying - they are different servers.

2. PostgreSQL emits errors in the system codepage (cp1251). psycopg2
   decodes them as UTF-8, fails, and raises

       'utf-8' codec can't decode byte 0xc2 in position 61

   INSTEAD of the real message, so "database does not exist" or "password
   authentication failed" never reaches you.

This script connects to the `postgres` maintenance database (which always
exists) rather than to product_matching, so it can get far enough to list
what is actually there and identify which server answered.
"""

from __future__ import annotations

import argparse
import os
import sys

# Ask libpq for untranslated messages BEFORE psycopg2 loads.
os.environ.setdefault("LC_ALL", "C")
os.environ.setdefault("LC_MESSAGES", "C")
os.environ.setdefault("PGCLIENTENCODING", "UTF8")

CANDIDATE_PORTS = (5432, 5433, 5434)


def _raw_bytes(value: object) -> bytes | None:
    """Dig the original undecodable bytes out of an exception.

    A UnicodeDecodeError's own str() is plain ASCII ("'utf-8' codec can't
    decode byte 0xc2..."), so printing the exception tells you nothing.
    The bytes that actually failed are on `.object` - that is the message
    PostgreSQL really sent, and the only place the true cause exists.
    """
    if isinstance(value, bytes):
        return value
    if isinstance(value, UnicodeDecodeError):
        return value.object
    for attr in ("__cause__", "__context__"):
        inner = getattr(value, attr, None)
        if isinstance(inner, UnicodeDecodeError):
            return inner.object
    for arg in getattr(value, "args", ()):
        if isinstance(arg, bytes):
            return arg
        if isinstance(arg, UnicodeDecodeError):
            return arg.object
    return None


def _safe(value: object) -> str:
    """Render an exception without ever raising a second error."""
    raw = _raw_bytes(value)
    if raw is None:
        try:
            return str(value)
        except Exception:  # noqa: BLE001
            return "<undecodable message>"
    for encoding in ("utf-8", "cp1251", "cp866", "cp1252"):
        try:
            return raw.decode(encoding).strip() + f"   [decoded as {encoding}]"
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace").strip() + "   [lossy]"


def _is_auth_failure(message: str) -> bool:
    """Recognise a password rejection in any language PostgreSQL ships.

    Matching on the English text alone is not enough here: on this machine
    the server replies in Russian, and treating that as a generic failure
    would send the user hunting for a missing database when the real
    problem is one wrong word in .env.
    """
    lowered = message.lower()
    markers = (
        "password authentication failed",   # English
        "authentication failed",
        "proverka podlinnosti",
        "proverku podlinnosti",
        "подлинности",                      # Russian: "проверку подлинности"
        "аутентификации",
        "пароль",
    )
    return any(m in lowered for m in markers)


def _looks_russian(message: str) -> bool:
    """True if the server is emitting localized Russian diagnostics.

    Useful as a fingerprint: the official postgres Docker image is not
    localized, so a Russian message means a native Windows install.
    """
    return any("Ѐ" <= ch <= "ӿ" for ch in message)


def _mask(password: str) -> str:
    if not password:
        return "<empty>"
    if len(password) <= 2:
        return "*" * len(password)
    return f"{password[0]}{'*' * (len(password) - 2)}{password[-1]} ({len(password)} chars)"


def _parse(url: str) -> dict:
    """Pull user/password/host/port/dbname out of a SQLAlchemy URL."""
    rest = url.split("//", 1)[1]
    creds, hostpart = rest.split("@", 1)
    user, _, password = creds.partition(":")
    hostport, _, dbname = hostpart.partition("/")
    host, _, port = hostport.partition(":")
    return {
        "user": user,
        "password": password,
        "host": host or "localhost",
        "port": int(port or 5432),
        "dbname": dbname.split("?")[0] or "postgres",
    }


def _identify(conn) -> dict:
    """Work out WHICH server answered.

    The give-away is the data directory: the Docker image stores data in
    /var/lib/postgresql/data (a POSIX path), while a native Windows install
    uses something like C:/Program Files/PostgreSQL/16/data.
    """
    info = {}
    with conn.cursor() as cur:
        cur.execute("SELECT version()")
        info["version"] = cur.fetchone()[0]
        cur.execute("SHOW server_encoding")
        info["server_encoding"] = cur.fetchone()[0]
        try:
            cur.execute("SHOW data_directory")
            info["data_directory"] = cur.fetchone()[0]
        except Exception:  # noqa: BLE001 - needs superuser on some setups
            info["data_directory"] = "<not visible>"
        cur.execute("SELECT datname FROM pg_database WHERE datistemplate = false ORDER BY datname")
        info["databases"] = [r[0] for r in cur.fetchall()]

    dd = info["data_directory"]
    if dd.startswith("/var/lib/postgresql"):
        info["kind"] = "DOCKER container"
    elif ":" in dd and "\\" in dd.replace("/", "\\"):
        info["kind"] = "NATIVE Windows install"
    else:
        info["kind"] = "unknown"
    return info


def _try_connect(psycopg2, cfg: dict, dbname: str, port: int | None = None):
    return psycopg2.connect(
        user=cfg["user"],
        password=cfg["password"],
        host=cfg["host"],
        port=port or cfg["port"],
        dbname=dbname,
        client_encoding="UTF8",
        connect_timeout=5,
    )


def _scan_ports(psycopg2, cfg: dict) -> None:
    """Find every PostgreSQL reachable on the usual ports.

    This is what makes the two-server situation visible instead of
    something you have to infer.
    """
    import socket

    print("--- Scanning for PostgreSQL servers ---")
    found_any = False
    for port in CANDIDATE_PORTS:
        try:
            with socket.create_connection((cfg["host"], port), timeout=2):
                pass
        except OSError:
            continue
        found_any = True
        print(f"\n  port {port}: something is listening")
        try:
            conn = _try_connect(psycopg2, cfg, "postgres", port=port)
        except Exception as exc:  # noqa: BLE001
            print(f"    could not log in: {_safe(exc)}")
            print("    (a server is here, but these credentials were rejected)")
            continue
        info = _identify(conn)
        conn.close()
        print(f"    kind          : {info['kind']}")
        print(f"    data_directory: {info['data_directory']}")
        print(f"    version       : {info['version'].split(',')[0]}")
        print(f"    encoding      : {info['server_encoding']}")
        print(f"    databases     : {', '.join(info['databases'])}")
        if "product_matching" in info["databases"]:
            print("    >>> product_matching EXISTS on this server <<<")
    if not found_any:
        print("  Nothing is listening on any of", CANDIDATE_PORTS)
        print("  PostgreSQL is not running. Run: docker compose up -d")
    print()


def main(create: bool = False) -> int:
    from app.config import settings

    url = settings.database_url
    if url.startswith("sqlite"):
        print("DATABASE_URL is a SQLite URL:", url)
        print()
        print("pgAdmin4 cannot open SQLite files - it is a PostgreSQL client.")
        print("Set DATABASE_URL to a postgresql+psycopg2:// URL in backend/.env.")
        return 0

    cfg = _parse(url)
    print(f"DATABASE_URL : {cfg['user']}:***@{cfg['host']}:{cfg['port']}/{cfg['dbname']}")
    print()

    try:
        import psycopg2
    except ImportError:
        print("psycopg2 is not installed. Run: pip install -r requirements.txt")
        return 1

    _scan_ports(psycopg2, cfg)

    # Now the specific question: can we reach the configured database?
    print(f"--- Connecting to '{cfg['dbname']}' on port {cfg['port']} ---")
    try:
        conn = _try_connect(psycopg2, cfg, cfg["dbname"])
    except Exception as exc:  # noqa: BLE001
        print("  FAILED:", _safe(exc))
        raw = _raw_bytes(exc)
        if raw is not None:
            print(f"  raw bytes: {raw[:200]!r}")
        print()

        # Can we at least reach the maintenance database on that port?
        try:
            admin = _try_connect(psycopg2, cfg, "postgres")
        except Exception as exc2:  # noqa: BLE001
            message = _safe(exc2)
            print("  Could not reach the 'postgres' maintenance database either:")
            print("   ", message)
            print()
            if _is_auth_failure(message):
                print("  This is an AUTHENTICATION failure, not a missing database.")
                print("  The server is running and reachable; the password is wrong.")
                print()
                print(f"  DATABASE_URL currently uses password: {_mask(cfg['password'])}")
                print()
                if _looks_russian(message):
                    print("  The error came back in Russian, which means this is your")
                    print("  NATIVE Windows PostgreSQL - the official postgres Docker")
                    print("  image emits English. So port 5432 belongs to the native")
                    print("  install (the one pgAdmin4 shows), not to the container.")
                    print()
                print("  Fix: put the NATIVE server's password into backend/.env, the")
                print("  same one you use to log into pgAdmin4:")
                print("      DATABASE_URL=postgresql+psycopg2://postgres:<password>"
                      f"@{cfg['host']}:{cfg['port']}/{cfg['dbname']}")
                print()
                print("  If the password contains @ : / or #, percent-encode it")
                print("  (@ -> %40, : -> %3A, / -> %2F, # -> %23).")
                print()
                print("  Forgotten it? Reset from an elevated Command Prompt:")
                print('      "C:\\Program Files\\PostgreSQL\\16\\bin\\psql" -U postgres')
                print("  or edit pg_hba.conf, set the method to 'trust', restart the")
                print("  PostgreSQL service, then ALTER USER postgres PASSWORD '...';")
            else:
                print("  So the credentials or the port are wrong. Compare the scan above")
                print("  with DATABASE_URL in backend/.env and fix whichever is wrong.")
            return 1

        info = _identify(admin)
        print(f"  The server on port {cfg['port']} is reachable, and it is a {info['kind']}.")
        print(f"  data_directory: {info['data_directory']}")
        print(f"  It contains   : {', '.join(info['databases'])}")
        print()
        if "product_matching" not in info["databases"]:
            print(f"  '{cfg['dbname']}' does NOT exist on this server.")
            if create:
                admin.set_isolation_level(0)  # CREATE DATABASE needs autocommit
                with admin.cursor() as cur:
                    cur.execute(
                        f'CREATE DATABASE "{cfg["dbname"]}" '
                        "WITH ENCODING 'UTF8' TEMPLATE template0"
                    )
                print(f"  [ok] created database '{cfg['dbname']}' (UTF8).")
                print("  Now run: python -m scripts.migrate_add_projects")
                admin.close()
                return 0
            print()
            print("  Fix it in one of two ways:")
            print()
            print(f"  A) Create it on THIS server (the one pgAdmin4 already shows):")
            print("       python -m scripts.check_db --create")
            print("     or in pgAdmin4: right-click Databases -> Create -> Database")
            print(f"     -> name it '{cfg['dbname']}', encoding UTF8")
            print()
            print("  B) Point DATABASE_URL at the Docker server instead. If the scan")
            print("     above found product_matching on another port, edit backend/.env:")
            print(f"       DATABASE_URL=postgresql+psycopg2://postgres:postgres@localhost:<port>/product_matching")
        admin.close()
        return 1

    info = _identify(conn)
    print("  OK")
    print(f"  server kind   : {info['kind']}")
    print(f"  data_directory: {info['data_directory']}")
    print(f"  encoding      : {info['server_encoding']}")
    if info["server_encoding"].upper() not in ("UTF8", "UTF-8"):
        print("  WARNING: not UTF8. Cyrillic product names will not round-trip.")
        print("  Recreate with: docker compose down -v && docker compose up -d")
    print()

    with conn.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' ORDER BY table_name"
        )
        tables = [r[0] for r in cur.fetchall()]
    print("--- Tables ---")
    if not tables:
        print("  (none yet - start the backend once and they will be created)")
    else:
        for t in tables:
            with conn.cursor() as cur:
                cur.execute(f'SELECT COUNT(*) FROM "{t}"')
                print(f"  {t:24} {cur.fetchone()[0]} rows")
        if "projects" not in tables:
            print()
            print("  'projects' is missing - run: python -m scripts.migrate_add_projects")
    conn.close()
    print()
    print("Connection is healthy.")
    print()
    print("To see this database in pgAdmin4, register THIS server:")
    print(f"  Host {cfg['host']}   Port {cfg['port']}   User {cfg['user']}   Database {cfg['dbname']}")
    print("  (If pgAdmin4 shows different databases, it is connected to a different")
    print("   server - check the port on its connection properties.)")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Diagnose the database connection.")
    parser.add_argument(
        "--create",
        action="store_true",
        help="Create the configured database if it is missing.",
    )
    args = parser.parse_args()
    try:
        raise SystemExit(main(create=args.create))
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print("Unexpected failure:", _safe(exc), file=sys.stderr)
        raise SystemExit(1)
