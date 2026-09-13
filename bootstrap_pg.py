"""Download and start a portable PostgreSQL under ./pg (Windows-friendly)."""

from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PG_DIR = ROOT / "pg"
PG_BIN = PG_DIR / "bin"
DATA_DIR = ROOT / "pgdata"
# Official EDB binaries are large; use lightweight ZIP mirrors when available.
# Fallback: require Docker / system Postgres / DATABASE_URL.
CANDIDATE_URLS = [
    # Gareth Flowers portable PostgreSQL (Windows x64)
    "https://github.com/garethflowers/postgresql-portable/releases/download/14.5.1/postgresql-portable-14.5.1.zip",
]


def _find_pg_ctl() -> Path | None:
    for p in (PG_BIN / "pg_ctl.exe", PG_BIN / "pg_ctl"):
        if p.exists():
            return p
    # nested unzip
    matches = list(PG_DIR.rglob("pg_ctl.exe")) + list(PG_DIR.rglob("pg_ctl"))
    return matches[0] if matches else None


def download() -> Path:
    PG_DIR.mkdir(parents=True, exist_ok=True)
    ctl = _find_pg_ctl()
    if ctl:
        return ctl
    zip_path = ROOT / "_pg_portable.zip"
    last_err: Exception | None = None
    for url in CANDIDATE_URLS:
        try:
            print(f"Downloading {url} …")
            urllib.request.urlretrieve(url, zip_path)
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(PG_DIR)
            zip_path.unlink(missing_ok=True)
            ctl = _find_pg_ctl()
            if ctl:
                return ctl
        except Exception as e:  # noqa: BLE001
            last_err = e
            print(f"  failed: {e}")
    raise SystemExit(f"Could not fetch portable PostgreSQL: {last_err}")


def start(port: int = 55432) -> str:
    ctl = download()
    bindir = ctl.parent
    initdb = bindir / ("initdb.exe" if os.name == "nt" else "initdb")
    if not DATA_DIR.exists():
        subprocess.check_call(
            [str(initdb), "-D", str(DATA_DIR), "-U", "polymarket", "-A", "trust", "-E", "UTF8"],
            cwd=str(ROOT),
        )
    # stop if already running
    subprocess.run(
        [str(ctl), "-D", str(DATA_DIR), "stop", "-m", "fast"],
        cwd=str(ROOT),
        capture_output=True,
    )
    subprocess.check_call(
        [
            str(ctl),
            "-D",
            str(DATA_DIR),
            "-o",
            f"-p {port}",
            "-l",
            str(ROOT / "pg.log"),
            "start",
        ],
        cwd=str(ROOT),
    )
    time.sleep(1.5)
    createdb = bindir / ("createdb.exe" if os.name == "nt" else "createdb")
    subprocess.run(
        [str(createdb), "-h", "127.0.0.1", "-p", str(port), "-U", "polymarket", "polymarket"],
        cwd=str(ROOT),
        capture_output=True,
    )
    url = f"postgresql://polymarket@127.0.0.1:{port}/polymarket"
    print(url)
    return url


def stop() -> None:
    ctl = _find_pg_ctl()
    if not ctl:
        return
    subprocess.run(
        [str(ctl), "-D", str(DATA_DIR), "stop", "-m", "fast"],
        cwd=str(ROOT),
        capture_output=True,
    )


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "start"
    if cmd == "start":
        start()
    elif cmd == "stop":
        stop()
    else:
        raise SystemExit("usage: bootstrap_pg.py [start|stop]")
