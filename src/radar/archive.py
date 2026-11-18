"""Helpers for the Binance public data archive (data.binance.vision).

* S3 listing (works for delisted symbols too), paginated with ``marker``.
* Downloads with retries; every zip is verified against its ``.CHECKSUM`` file before it is kept.
* Every kept zip is logged in data/MANIFEST.csv (file, source_url, downloaded_at_utc, rows, sha256).

Reruns are cheap: a zip that is already on disk AND listed in the manifest with a matching
sha256 is not downloaded again.
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import random
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote
from xml.etree import ElementTree as ET

import requests

from .config import MANIFEST, RAW, WORKERS

S3_LIST = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
DL_BASE = "https://data.binance.vision/"
NS = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
MANIFEST_FIELDS = ["file", "source_url", "downloaded_at_utc", "rows", "sha256"]

_local = threading.local()
_manifest_lock = threading.Lock()
_manifest_cache: dict[str, dict] | None = None


def session() -> requests.Session:
    s = getattr(_local, "s", None)
    if s is None:
        s = requests.Session()
        s.headers["User-Agent"] = "quant-desk-radar/1.0 (research; public archive)"
        _local.s = s
    return s


def _get(url: str, params: dict | None = None, tries: int = 5) -> requests.Response:
    """GET with retries on network errors, 429 and 5xx. 404 is returned to the caller."""
    last: Exception | None = None
    for i in range(tries):
        try:
            r = session().get(url, params=params, timeout=60)
            if r.status_code == 451:
                # Geo-restriction: never try to bypass (RULES.md #7).
                raise RuntimeError(f"HTTP 451 for {url}; not bypassing")
            if r.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"HTTP {r.status_code}")
            return r
        except RuntimeError:
            raise
        except Exception as e:  # noqa: BLE001 - network errors are retried
            last = e
            time.sleep(min(30, 1.5 ** i + random.random()))
    raise RuntimeError(f"GET failed after {tries} tries: {url}: {last}")


# --------------------------------------------------------------------------------------------
# S3 listing
# --------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class S3Key:
    key: str
    size: int
    last_modified: str


def s3_list(prefix: str, delimiter: str | None = "/") -> tuple[list[str], list[S3Key]]:
    """Return (common_prefixes, keys) under ``prefix``, following pagination."""
    marker = ""
    prefixes: list[str] = []
    keys: list[S3Key] = []
    while True:
        params = {"prefix": prefix, "marker": marker}
        if delimiter:
            params["delimiter"] = delimiter
        r = _get(S3_LIST, params=params)
        r.raise_for_status()
        root = ET.fromstring(r.content)
        page_last = None
        for cp in root.findall("s3:CommonPrefixes/s3:Prefix", NS):
            prefixes.append(cp.text)
            page_last = cp.text
        for c in root.findall("s3:Contents", NS):
            k = S3Key(c.find("s3:Key", NS).text, int(c.find("s3:Size", NS).text),
                      c.find("s3:LastModified", NS).text)
            keys.append(k)
            page_last = max(page_last or "", k.key)
        if root.find("s3:IsTruncated", NS).text != "true":
            break
        nm = root.find("s3:NextMarker", NS)
        marker = nm.text if nm is not None and nm.text else page_last
        if not marker:
            break
    return prefixes, keys


def list_zip_keys(prefix: str) -> list[str]:
    """All .zip keys (not .CHECKSUM) under a prefix, sorted."""
    _, keys = s3_list(prefix, delimiter=None)
    return sorted(k.key for k in keys if k.key.endswith(".zip"))


def parallel_map(fn, items, workers: int = WORKERS):
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(fn, items))


# --------------------------------------------------------------------------------------------
# Manifest
# --------------------------------------------------------------------------------------------
def load_manifest() -> dict[str, dict]:
    global _manifest_cache
    with _manifest_lock:
        if _manifest_cache is None:
            _manifest_cache = {}
            if MANIFEST.exists():
                with MANIFEST.open(newline="", encoding="utf-8") as f:
                    for row in csv.DictReader(f):
                        _manifest_cache[row["file"]] = row
        return _manifest_cache


def _append_manifest(row: dict) -> None:
    load_manifest()
    with _manifest_lock:
        new_file = not MANIFEST.exists() or MANIFEST.stat().st_size == 0
        with MANIFEST.open("a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=MANIFEST_FIELDS)
            if new_file:
                w.writeheader()
            w.writerow(row)
        _manifest_cache[row["file"]] = row


def rewrite_manifest_sorted() -> None:
    """Rewrite the manifest sorted by file path, one row per file (latest download wins)."""
    rows = load_manifest()
    with _manifest_lock:
        tmp = MANIFEST.with_suffix(".tmp")
        with tmp.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=MANIFEST_FIELDS)
            w.writeheader()
            for k in sorted(rows):
                w.writerow({fld: rows[k][fld] for fld in MANIFEST_FIELDS})
        tmp.replace(MANIFEST)


# --------------------------------------------------------------------------------------------
# Download + verify
# --------------------------------------------------------------------------------------------
def local_path(key: str) -> Path:
    """data/futures/um/... -> <repo>/data/raw/futures/um/..."""
    return RAW / key.removeprefix("data/")


def rel_file(key: str) -> str:
    return "data/raw/" + key.removeprefix("data/")


def _is_header(line: str) -> bool:
    """Archive CSVs sometimes have a header line; data lines start with a digit (ms or a date)."""
    first = line.split(",")[0].strip().strip('"')
    return bool(first) and first[0].isalpha()


def count_csv_rows(content: bytes) -> int:
    """Data rows in the (single) CSV inside a zip; a header line is not counted."""
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        name = z.namelist()[0]
        n, first = 0, None
        with z.open(name) as f:
            for line in io.TextIOWrapper(f, encoding="utf-8"):
                if not line.strip():
                    continue
                if first is None:
                    first = line
                n += 1
    if first is not None and _is_header(first):
        n -= 1  # header line
    return n


@dataclass
class FetchResult:
    key: str
    status: str            # "ok" (downloaded), "cached", "missing" (404), "bad_checksum", "error"
    detail: str = ""


def fetch(key: str) -> FetchResult:
    """Download one archive zip, verify its CHECKSUM, store it and log it in the manifest."""
    path = local_path(key)
    rel = rel_file(key)
    man = load_manifest()
    if path.exists() and rel in man:
        if hashlib.sha256(path.read_bytes()).hexdigest() == man[rel]["sha256"]:
            return FetchResult(key, "cached")
    url = DL_BASE + quote(key, safe="/")
    try:
        for attempt in range(3):
            r = _get(url)
            if r.status_code == 404:
                return FetchResult(key, "missing")
            r.raise_for_status()
            content = r.content
            c = _get(url + ".CHECKSUM")
            if c.status_code == 404:
                return FetchResult(key, "error", "no CHECKSUM file")
            c.raise_for_status()
            expected = c.text.split()[0].strip().lower()
            actual = hashlib.sha256(content).hexdigest()
            if expected == actual:
                break
            time.sleep(2 + attempt)
        else:
            return FetchResult(key, "bad_checksum", f"expected {expected} got {actual}")
        rows = count_csv_rows(content)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        tmp.write_bytes(content)
        tmp.replace(path)
        path.with_name(path.name + ".CHECKSUM").write_text(c.text, encoding="utf-8")
        _append_manifest({
            "file": rel,
            "source_url": url,
            "downloaded_at_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "rows": rows,
            "sha256": actual,
        })
        return FetchResult(key, "ok")
    except Exception as e:  # noqa: BLE001
        return FetchResult(key, "error", str(e)[:300])


def fetch_many(keys: list[str], label: str = "", workers: int = WORKERS) -> list[FetchResult]:
    keys = list(dict.fromkeys(keys))
    t0 = time.time()
    results = parallel_map(fetch, keys, workers)
    counts: dict[str, int] = {}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    print(f"[fetch {label}] {len(keys)} keys in {time.time() - t0:.0f}s: {counts}", flush=True)
    for r in results:
        if r.status in ("bad_checksum", "error"):
            print(f"  !! {r.status} {r.key} {r.detail}", flush=True)
    return results


def read_zip_csv(path: Path, columns: list[str]):
    """Read the CSV inside an archive zip into a DataFrame, with or without a header line."""
    import pandas as pd

    with zipfile.ZipFile(path) as z:
        name = z.namelist()[0]
        raw = z.read(name)
    first = raw.split(b"\n", 1)[0].decode("utf-8", "replace")
    has_header = _is_header(first)
    df = pd.read_csv(io.BytesIO(raw), header=0 if has_header else None)
    if not has_header:
        df.columns = columns[: df.shape[1]]
    return df
