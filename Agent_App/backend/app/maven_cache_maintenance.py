"""Inspect and prune persistent Maven caches without giving build containers Docker access."""

from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

try:
    import fcntl
except ImportError:  # pragma: no cover - the maintenance container is Linux-only.
    fcntl = None


DEFAULT_CACHE_DIRS = ("/caches/sandbox", "/caches/worker")


@dataclass(frozen=True)
class CacheFile:
    path: Path
    size: int
    mtime: float


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def cache_files(cache_dir: Path) -> Iterable[CacheFile]:
    if not cache_dir.exists():
        return

    for root, directories, filenames in os.walk(cache_dir, followlinks=False):
        directories[:] = [name for name in directories if not (Path(root) / name).is_symlink()]
        for filename in filenames:
            path = Path(root) / filename
            if path.name == ".a3-cache-maintenance.lock" or path.is_symlink():
                continue
            try:
                stat = path.stat(follow_symlinks=False)
            except OSError:
                continue
            if path.is_file():
                yield CacheFile(path=path, size=stat.st_size, mtime=stat.st_mtime)


def collect(cache_dir: Path) -> list[CacheFile]:
    return list(cache_files(cache_dir))


def cache_summary(cache_dir: Path) -> dict:
    files = collect(cache_dir)
    total = sum(item.size for item in files)
    return {
        "path": str(cache_dir),
        "files": len(files),
        "bytes": total,
        "human_size": human_size(total),
    }


def remove_file(cache_file: CacheFile, dry_run: bool) -> int:
    if not dry_run:
        try:
            cache_file.path.unlink()
        except FileNotFoundError:
            return 0
    return cache_file.size


def remove_empty_directories(cache_dir: Path) -> None:
    for root, directories, _ in os.walk(cache_dir, topdown=False):
        for directory in directories:
            path = Path(root) / directory
            try:
                path.rmdir()
            except OSError:
                pass


def prune_cache(cache_dir: Path, max_size_bytes: int, max_age_days: int, dry_run: bool) -> dict:
    cache_dir.mkdir(parents=True, exist_ok=True)
    lock_path = cache_dir / ".a3-cache-maintenance.lock"
    with lock_path.open("w", encoding="utf-8") as lock_file:
        if fcntl is not None:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError(f"Cache is already being maintained: {cache_dir}") from exc

        before = collect(cache_dir)
        remaining = {item.path: item for item in before}
        reclaimed = 0
        removed_files = 0
        cutoff = time.time() - max_age_days * 24 * 60 * 60

        # Maven leaves these after failed downloads. They are safe to recreate.
        stale_markers = [item for item in before if item.path.name.endswith(".lastUpdated")]
        expired = [item for item in before if item.mtime < cutoff and item not in stale_markers]
        for item in stale_markers + expired:
            if item.path not in remaining:
                continue
            reclaimed += remove_file(item, dry_run)
            removed_files += 1
            remaining.pop(item.path, None)

        current_size = sum(item.size for item in remaining.values())
        if current_size > max_size_bytes:
            for item in sorted(remaining.values(), key=lambda entry: entry.mtime):
                if current_size <= max_size_bytes:
                    break
                reclaimed += remove_file(item, dry_run)
                removed_files += 1
                current_size -= item.size
                remaining.pop(item.path, None)

        if not dry_run:
            remove_empty_directories(cache_dir)

        after_bytes = sum(item.size for item in remaining.values())
        return {
            "path": str(cache_dir),
            "before_bytes": sum(item.size for item in before),
            "before_human_size": human_size(sum(item.size for item in before)),
            "after_bytes": after_bytes,
            "after_human_size": human_size(after_bytes),
            "reclaimed_bytes": reclaimed,
            "reclaimed_human_size": human_size(reclaimed),
            "removed_files": removed_files,
            "max_size_bytes": max_size_bytes,
            "max_age_days": max_age_days,
            "dry_run": dry_run,
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect or prune the A3 Maven dependency caches.")
    parser.add_argument("action", choices=("status", "prune"))
    parser.add_argument(
        "--cache-dir",
        action="append",
        dest="cache_dirs",
        help="Cache directory to inspect. Repeat to manage more than one cache.",
    )
    parser.add_argument(
        "--max-size-mb",
        type=int,
        default=int(os.getenv("MAVEN_CACHE_MAX_SIZE_MB", "1024")),
        help="Maximum size retained per cache after pruning (default: 1024).",
    )
    parser.add_argument(
        "--max-age-days",
        type=int,
        default=int(os.getenv("MAVEN_CACHE_MAX_AGE_DAYS", "30")),
        help="Remove files not modified within this many days (default: 30).",
    )
    parser.add_argument("--dry-run", action="store_true", help="Show what prune would reclaim without deleting files.")
    arguments = parser.parse_args()
    if arguments.max_size_mb < 0 or arguments.max_age_days < 0:
        parser.error("--max-size-mb and --max-age-days must be non-negative")
    return arguments


def main() -> None:
    arguments = parse_args()
    cache_dirs = [Path(value) for value in (arguments.cache_dirs or DEFAULT_CACHE_DIRS)]
    if arguments.action == "status":
        summaries = [cache_summary(cache_dir) for cache_dir in cache_dirs]
        print(json.dumps({"action": "status", "caches": summaries}, ensure_ascii=True))
        return

    max_size_bytes = arguments.max_size_mb * 1024 * 1024
    summaries = [
        prune_cache(
            cache_dir,
            max_size_bytes=max_size_bytes,
            max_age_days=arguments.max_age_days,
            dry_run=arguments.dry_run,
        )
        for cache_dir in cache_dirs
    ]
    print(json.dumps({"action": "prune", "caches": summaries}, ensure_ascii=True))


if __name__ == "__main__":
    main()
