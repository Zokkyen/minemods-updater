"""Update orchestration for scanning results.

This module handles provider checks, optional dry-run simulation, file
replacement with backups, and changelog categorization for UI display.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from dataclasses import dataclass
import os
from pathlib import Path
import re
import threading
import time

import requests

from .app_meta import APP_USER_AGENT
from .models import AppSettings, LocalMod, UpdateInfo
from .providers import CurseForgeProvider, ModrinthProvider, resolve_updates_for_mod


DOWNLOAD_CHUNK_SIZE = 1024 * 128
DOWNLOAD_TIMEOUT = 45
CHECK_UPDATES_PARALLEL_THRESHOLD = 4
CHECK_UPDATES_MAX_WORKERS = 6
CHECK_UPDATES_MAX_WORKERS_MODRINTH = 3
CHECK_UPDATES_CACHE_TTL_SECONDS = 90
CHECK_UPDATES_CACHE_MAX_ENTRIES = 500
CHANGELOG_CATEGORY_ORDER = ["breaking", "fix", "performance", "other"]

_CHECK_CACHE_LOCK = threading.Lock()
_CHECK_CACHE: dict[str, tuple[float, UpdateInfo]] = {}

_CHECK_STATS_LOCK = threading.Lock()
_LAST_CHECK_STATS = {
    "cache_hits": 0,
    "cache_misses": 0,
    "workers": 0,
}

BREAKING_KEYWORDS = {
    "breaking",
    "incompatible",
    "removed",
    "remove",
    "migration",
    "deprecated",
    "renamed",
    "rewrite",
}

FIX_KEYWORDS = {
    "fix",
    "fixed",
    "bug",
    "issue",
    "resolve",
    "resolved",
    "patch",
    "hotfix",
    "crash",
}

PERFORMANCE_KEYWORDS = {
    "performance",
    "optimize",
    "optimized",
    "faster",
    "speed",
    "latency",
    "memory",
    "cpu",
    "throughput",
}


@dataclass(slots=True)
class AppliedUpdate:
    mod_name: str
    mod_id: str
    old_version: str
    new_version: str
    provider: str
    old_path: Path
    new_path: Path
    backup_path: Path
    simulated: bool = False


def get_last_check_stats() -> dict[str, int]:
    """Expose lightweight runtime metrics for the latest check-updates run."""
    with _CHECK_STATS_LOCK:
        return dict(_LAST_CHECK_STATS)


def check_updates(mods: list[LocalMod], settings: AppSettings) -> list[UpdateInfo]:
    """Resolve update status for every scanned local mod."""
    if not mods:
        _set_last_check_stats(cache_hits=0, cache_misses=0, workers=0)
        return []

    workers = _compute_check_workers(len(mods), settings)
    ordered_results: list[UpdateInfo | None] = [None] * len(mods)
    pending: list[tuple[int, LocalMod, str]] = []

    for index, local_mod in enumerate(mods):
        cache_key = _build_check_cache_key(local_mod, settings)
        cached = _get_cached_update(cache_key)
        if cached is not None:
            ordered_results[index] = cached
            continue
        pending.append((index, local_mod, cache_key))

    cache_hits = len(mods) - len(pending)
    cache_misses = len(pending)

    if not pending:
        _set_last_check_stats(cache_hits=cache_hits, cache_misses=cache_misses, workers=0)
        return [item for item in ordered_results if item is not None]

    if workers <= 1:
        modrinth = ModrinthProvider()
        curseforge = CurseForgeProvider(settings.curseforge_api_key) if settings.use_curseforge else None

        for index, local_mod, cache_key in pending:
            update_info = resolve_updates_for_mod(
                local_mod=local_mod,
                settings=settings,
                modrinth=modrinth,
                curseforge=curseforge,
            )
            ordered_results[index] = update_info
            _store_cached_update(cache_key, update_info)

        _set_last_check_stats(cache_hits=cache_hits, cache_misses=cache_misses, workers=1)
        return [item for item in ordered_results if item is not None]

    thread_state = threading.local()

    def _resolve_one(index: int, local_mod: LocalMod, cache_key: str) -> tuple[int, str, UpdateInfo]:
        try:
            modrinth, curseforge = _thread_providers(thread_state, settings)
            update_info = resolve_updates_for_mod(
                local_mod=local_mod,
                settings=settings,
                modrinth=modrinth,
                curseforge=curseforge,
            )
            return index, cache_key, update_info
        except Exception as exc:
            return (
                index,
                cache_key,
                UpdateInfo(
                    local_mod=local_mod,
                    status="error",
                    message=f"Check error: {exc}",
                    provider="",
                ),
            )

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="mod-check") as executor:
        futures = [
            executor.submit(_resolve_one, index, local_mod, cache_key)
            for index, local_mod, cache_key in pending
        ]
        for future in as_completed(futures):
            index, cache_key, update_info = future.result()
            ordered_results[index] = update_info
            _store_cached_update(cache_key, update_info)

    _set_last_check_stats(cache_hits=cache_hits, cache_misses=cache_misses, workers=workers)

    return [item for item in ordered_results if item is not None]


def _build_check_cache_key(local_mod: LocalMod, settings: AppSettings) -> str:
    resolved_loader = local_mod.loader_hint if settings.loader == "auto" and local_mod.loader_hint != "unknown" else settings.loader

    try:
        stat = local_mod.path.stat()
        file_fingerprint = f"{stat.st_size}:{stat.st_mtime_ns}"
    except OSError:
        file_fingerprint = "missing"

    mapped_modrinth = settings.modrinth_project_map.get(local_mod.mod_id, "") if settings.use_modrinth else ""
    mapped_curseforge = str(settings.curseforge_project_map.get(local_mod.mod_id, "")) if settings.use_curseforge else ""

    return "|".join(
        [
            str(local_mod.path).lower(),
            local_mod.mod_id.strip().lower(),
            local_mod.version.strip().lower(),
            file_fingerprint,
            settings.minecraft_version.strip().lower(),
            resolved_loader.strip().lower(),
            "mr1" if settings.use_modrinth else "mr0",
            "cf1" if settings.use_curseforge else "cf0",
            "strict1" if settings.strict_matching else "strict0",
            mapped_modrinth,
            mapped_curseforge,
        ]
    )


def _get_cached_update(cache_key: str) -> UpdateInfo | None:
    now = time.monotonic()
    with _CHECK_CACHE_LOCK:
        entry = _CHECK_CACHE.get(cache_key)
        if not entry:
            return None

        stored_at, update_info = entry
        if now - stored_at > CHECK_UPDATES_CACHE_TTL_SECONDS:
            _CHECK_CACHE.pop(cache_key, None)
            return None

        return deepcopy(update_info)


def _store_cached_update(cache_key: str, update_info: UpdateInfo) -> None:
    # Keep transient network failures outside cache to allow immediate retry.
    if update_info.status == "error":
        return

    now = time.monotonic()
    with _CHECK_CACHE_LOCK:
        _prune_expired_cache_locked(now)
        _CHECK_CACHE[cache_key] = (now, deepcopy(update_info))

        if len(_CHECK_CACHE) <= CHECK_UPDATES_CACHE_MAX_ENTRIES:
            return

        overflow = len(_CHECK_CACHE) - CHECK_UPDATES_CACHE_MAX_ENTRIES
        oldest_keys = [
            key
            for key, _ in sorted(_CHECK_CACHE.items(), key=lambda item: item[1][0])[:overflow]
        ]
        for key in oldest_keys:
            _CHECK_CACHE.pop(key, None)


def _prune_expired_cache_locked(now: float) -> None:
    expired_keys = [
        key
        for key, (stored_at, _) in _CHECK_CACHE.items()
        if now - stored_at > CHECK_UPDATES_CACHE_TTL_SECONDS
    ]
    for key in expired_keys:
        _CHECK_CACHE.pop(key, None)


def _set_last_check_stats(cache_hits: int, cache_misses: int, workers: int) -> None:
    with _CHECK_STATS_LOCK:
        _LAST_CHECK_STATS["cache_hits"] = max(0, int(cache_hits))
        _LAST_CHECK_STATS["cache_misses"] = max(0, int(cache_misses))
        _LAST_CHECK_STATS["workers"] = max(0, int(workers))


def _compute_check_workers(mod_count: int, settings: AppSettings) -> int:
    if mod_count < CHECK_UPDATES_PARALLEL_THRESHOLD:
        return 1

    curseforge_penalty = 1 if settings.use_curseforge else 0
    cpu_budget = max(2, (os.cpu_count() or 4))
    burst_cap = CHECK_UPDATES_MAX_WORKERS - curseforge_penalty
    if settings.use_modrinth:
        burst_cap = min(burst_cap, CHECK_UPDATES_MAX_WORKERS_MODRINTH)

    recommended = min(burst_cap, cpu_budget)
    return max(1, min(mod_count, recommended))


def _thread_providers(
    thread_state: threading.local,
    settings: AppSettings,
) -> tuple[ModrinthProvider, CurseForgeProvider | None]:
    modrinth = getattr(thread_state, "modrinth", None)
    if modrinth is None:
        modrinth = ModrinthProvider()
        setattr(thread_state, "modrinth", modrinth)

    if not hasattr(thread_state, "curseforge"):
        curseforge = CurseForgeProvider(settings.curseforge_api_key) if settings.use_curseforge else None
        setattr(thread_state, "curseforge", curseforge)

    curseforge = getattr(thread_state, "curseforge")
    return modrinth, curseforge


def apply_updates(items: list[UpdateInfo], settings: AppSettings, dry_run: bool = False) -> tuple[list[AppliedUpdate], list[str]]:
    """Apply or simulate selected updates and collect per-mod errors."""
    applied: list[AppliedUpdate] = []
    errors: list[str] = []

    session = requests.Session()
    session.headers.update({"User-Agent": APP_USER_AGENT})

    for item in items:
        if item.status != "update_available" or item.latest is None:
            continue

        try:
            result = _apply_single_update(item, settings, session, dry_run=dry_run)
            applied.append(result)
        except Exception as exc:
            errors.append(f"{item.local_mod.name}: {exc}")

    return applied, errors


def _apply_single_update(
    update: UpdateInfo,
    settings: AppSettings,
    session: requests.Session,
    dry_run: bool = False,
) -> AppliedUpdate:
    """Download and atomically replace one mod file, or simulate the action."""
    local_path = update.local_mod.path
    if not local_path.exists():
        raise FileNotFoundError(f"Local mod file missing: {local_path}")

    if update.latest is None:
        raise ValueError("No target version available")

    mods_dir = local_path.parent
    target_name = update.latest.filename or local_path.name
    target_path = mods_dir / target_name

    temp_path = mods_dir / f".{target_name}.download"
    backup_path = _next_backup_path(local_path, settings.backup_suffix)

    if dry_run:
        return AppliedUpdate(
            mod_name=update.local_mod.name,
            mod_id=update.local_mod.mod_id,
            old_version=update.local_mod.version,
            new_version=update.latest.version_number,
            provider=update.provider,
            old_path=local_path,
            new_path=target_path,
            backup_path=backup_path,
            simulated=True,
        )

    _download_file(update.latest.download_url, temp_path, session)

    try:
        local_path.rename(backup_path)

        if target_path.exists():
            target_path.unlink()

        temp_path.rename(target_path)
    except Exception:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)

        if backup_path.exists() and not local_path.exists():
            backup_path.rename(local_path)
        raise

    return AppliedUpdate(
        mod_name=update.local_mod.name,
        mod_id=update.local_mod.mod_id,
        old_version=update.local_mod.version,
        new_version=update.latest.version_number,
        provider=update.provider,
        old_path=local_path,
        new_path=target_path,
        backup_path=backup_path,
        simulated=False,
    )


def _next_backup_path(original_file: Path, suffix: str) -> Path:
    candidate = original_file.with_name(original_file.name + suffix)
    if not candidate.exists():
        return candidate

    for index in range(1, 500):
        indexed = original_file.with_name(f"{original_file.name}{suffix}.{index}")
        if not indexed.exists():
            return indexed

    raise RuntimeError(f"Too many backups for {original_file.name}")


def _download_file(url: str, destination: Path, session: requests.Session) -> None:
    if not url:
        raise ValueError("Empty download URL")

    response = session.get(url, timeout=DOWNLOAD_TIMEOUT, stream=True)
    response.raise_for_status()

    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as handle:
        for chunk in response.iter_content(chunk_size=DOWNLOAD_CHUNK_SIZE):
            if chunk:
                handle.write(chunk)


def _normalize_changelog_line(line: str) -> str:
    cleaned = line.strip()
    cleaned = re.sub(r"^[\-\*\d\.\)\s]+", "", cleaned)
    cleaned = cleaned.replace("`", "").strip()
    return cleaned


def _categorize_line(line: str) -> str:
    lowered = line.lower()
    words = set(re.findall(r"[a-z0-9]+", lowered))

    if words & BREAKING_KEYWORDS:
        return "breaking"
    if words & FIX_KEYWORDS:
        return "fix"
    if words & PERFORMANCE_KEYWORDS:
        return "performance"
    return "other"


def _extract_changelog_lines(changelog: str) -> list[str]:
    raw_lines = [line for line in changelog.splitlines() if line.strip()]
    lines: list[str] = []
    for raw in raw_lines:
        cleaned = _normalize_changelog_line(raw)
        if not cleaned:
            continue
        lines.append(cleaned)
    return lines[:120]


def summarize_changelog_categories(update_info: UpdateInfo) -> dict[str, int]:
    summary = {key: 0 for key in CHANGELOG_CATEGORY_ORDER}
    versions = update_info.intermediate_versions or ([update_info.latest] if update_info.latest else [])

    for version in versions:
        if version is None:
            continue
        for line in _extract_changelog_lines(version.changelog or ""):
            category = _categorize_line(line)
            summary[category] = summary.get(category, 0) + 1

    return summary


def _status_label(status: str) -> str:
    labels = {
        "update_available": "Mise à jour disponible",
        "up_to_date": "À jour",
        "not_found": "Introuvable",
        "error": "Erreur",
    }
    return labels.get(status, status)


def build_changelog_text(update_info: UpdateInfo, enabled_filters: set[str] | None = None) -> str:
    """Build a readable changelog summary for the details panel in the UI."""
    lines: list[str] = []
    filters = enabled_filters or set(CHANGELOG_CATEGORY_ORDER)

    local = update_info.local_mod
    lines.append(f"Mod: {local.name} ({local.mod_id})")
    lines.append(f"Version locale: {local.version}")
    lines.append(f"Statut: {_status_label(update_info.status)}")
    lines.append(f"Source: {update_info.provider or '-'}")
    if update_info.matched_project_url:
        lines.append(f"Page provider: {update_info.matched_project_url}")
    lines.append(f"Filtres changelog: {', '.join(sorted(filters)) if filters else 'aucun'}")
    lines.append("")

    if update_info.latest is None:
        lines.append(update_info.message)
        return "\n".join(lines)

    lines.append(f"Dernière version: {update_info.latest.version_number}")
    lines.append(f"Publiée: {update_info.latest.published_at.isoformat()}")
    lines.append("")

    versions_to_describe = list(reversed(update_info.intermediate_versions))
    if not versions_to_describe and update_info.latest is not None:
        versions_to_describe = [update_info.latest]

    if not versions_to_describe:
        lines.append(update_info.message)
        return "\n".join(lines)

    category_summary = summarize_changelog_categories(update_info)
    lines.append("Résumé des catégories détectées:")
    for category in CHANGELOG_CATEGORY_ORDER:
        lines.append(f"- {category}: {category_summary.get(category, 0)}")

    lines.append("")
    lines.append("Versions intermédiaires détectées:")
    for version in versions_to_describe:
        lines.append("")
        lines.append(f"- {version.version_number} ({version.published_at.date().isoformat()})")
        changelog = (version.changelog or "").strip()
        if changelog:
            categorized: dict[str, list[str]] = {key: [] for key in CHANGELOG_CATEGORY_ORDER}
            for line in _extract_changelog_lines(changelog):
                category = _categorize_line(line)
                categorized.setdefault(category, []).append(line)

            matched_any = False
            for category in CHANGELOG_CATEGORY_ORDER:
                if category not in filters:
                    continue
                entries = categorized.get(category, [])
                if not entries:
                    continue

                matched_any = True
                lines.append(f"  [{category}]")
                for entry in entries[:25]:
                    lines.append(f"    - {entry}")

            if not matched_any:
                lines.append("  Aucun élément du changelog ne correspond aux filtres actifs.")
        else:
            lines.append("  Changelog non fourni par le provider.")

    return "\n".join(lines)
