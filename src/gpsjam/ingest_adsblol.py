"""Bronze-Stufe: lädt einen Tages-Dump von adsb.lol (globe_history) und
extrahiert nur die Flugspuren (traces/**/trace_full_*.json).

Die Dumps liegen als GitHub-Releases vor und sind in mehrere Tar-Teile
(.tar.aa, .tar.ab, ...) gesplittet. Die Teile werden fortsetzbar geladen
(Downloads von mehreren GB brechen bei GitHub gelegentlich ab), dann
hintereinander in den Tar-Entpacker gestreamt und anschließend gelöscht.

    python -m gpsjam.ingest_adsblol 2026-10-28
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
import tarfile
from pathlib import Path

import requests
import urllib3

GITHUB_API = "https://api.github.com/repos/adsblol/globe_history_{year}/releases/tags/{tag}"
POD_PREFERENCE = ("prod-0", "staging-0")


class _ConcatStream:
    """Datei-ähnliches Objekt, das mehrere lokale Dateien aneinanderhängt."""

    def __init__(self, paths: list[Path]):
        self._paths = iter(paths)
        self._f = None
        self.bytes_read = 0

    def read(self, n: int = -1) -> bytes:
        while True:
            if self._f is None:
                path = next(self._paths, None)
                if path is None:
                    return b""
                self._f = open(path, "rb")
            chunk = self._f.read(n)
            if chunk:
                self.bytes_read += len(chunk)
                return chunk
            self._f.close()
            self._f = None


def download(url: str, target: Path, attempts: int = 20) -> Path:
    """Lädt mit HTTP-Range fortsetzbar – bricht die Verbindung ab, geht es an derselben Stelle weiter."""
    for _ in range(attempts):
        have = target.stat().st_size if target.exists() else 0
        try:
            with requests.get(url, stream=True, timeout=60, headers={"Range": f"bytes={have}-"}) as r:
                if r.status_code == 416:  # schon vollständig
                    return target
                r.raise_for_status()
                total = (have if r.status_code == 206 else 0) + int(r.headers.get("Content-Length", 0))
                with open(target, "ab" if r.status_code == 206 else "wb") as f:
                    for chunk in r.iter_content(chunk_size=8 << 20):
                        f.write(chunk)
            if target.stat().st_size >= total:
                return target
        except (requests.RequestException, urllib3.exceptions.HTTPError) as e:
            print(f"  Abbruch bei {target.stat().st_size / 2**30:.2f} GiB ({type(e).__name__}), setze fort",
                  file=sys.stderr)
    raise SystemExit(f"Download von {url} endgültig fehlgeschlagen")


def release_assets(day: dt.date) -> list[str]:
    for pod in POD_PREFERENCE:
        tag = f"v{day:%Y.%m.%d}-planes-readsb-{pod}"
        r = requests.get(GITHUB_API.format(year=day.year, tag=tag), timeout=30)
        if r.status_code == 200:
            assets = sorted(r.json()["assets"], key=lambda a: a["name"])
            return [a["browser_download_url"] for a in assets if ".tar" in a["name"]]
    raise SystemExit(f"Kein Release für {day} gefunden")


def ingest_day(day: dt.date, out_root: Path) -> Path:
    out_dir = out_root / f"{day:%Y-%m-%d}"
    done_marker = out_dir / "_SUCCESS"
    if done_marker.exists():
        print(f"{day}: bereits vorhanden", file=sys.stderr)
        return out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    parts_dir = out_root / "_parts"
    parts_dir.mkdir(parents=True, exist_ok=True)
    parts = []
    for url in release_assets(day):
        print(f"  -> {url.rsplit('/', 1)[-1]}", file=sys.stderr)
        parts.append(download(url, parts_dir / url.rsplit("/", 1)[-1]))

    stream = _ConcatStream(parts)
    n = 0
    with tarfile.open(fileobj=stream, mode="r|") as tar:
        for member in tar:
            name = member.name.lstrip("./")
            if not (member.isfile() and name.startswith("traces/")):
                continue
            target = out_dir / Path(name).name
            src = tar.extractfile(member)
            target.write_bytes(src.read())
            n += 1
            if n % 20000 == 0:
                print(f"  {n:>7} Spuren, {stream.bytes_read / 2**30:.2f} GiB gelesen", file=sys.stderr)
    done_marker.write_text(f"{n}\n")
    for part in parts:
        part.unlink()
    print(f"{day}: {n} Flugspuren -> {out_dir}", file=sys.stderr)
    return out_dir


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("start", type=dt.date.fromisoformat)
    p.add_argument("end", type=dt.date.fromisoformat, nargs="?")
    p.add_argument("--out", type=Path, default=Path("data/raw"))
    a = p.parse_args()
    day = a.start
    while day <= (a.end or a.start):
        ingest_day(day, a.out)
        day += dt.timedelta(days=1)


if __name__ == "__main__":
    main()
