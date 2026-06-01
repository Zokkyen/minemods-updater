from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

import requests

from .app_meta import APP_USER_AGENT
from .models import AppSettings, LocalMod, UpdateInfo
from .providers import CurseForgeProvider, ModrinthProvider, resolve_updates_for_mod


DOWNLOAD_CHUNK_SIZE = 1024 * 128
DOWNLOAD_TIMEOUT = 45
CHANGELOG_CATEGORY_ORDER = ["breaking", "fix", "performance", "other"]

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


def check_updates(mods: list[LocalMod], settings: AppSettings) -> list[UpdateInfo]:
    modrinth = ModrinthProvider()
    curseforge = CurseForgeProvider(settings.curseforge_api_key) if settings.use_curseforge else None

    updates: list[UpdateInfo] = []
    for local_mod in mods:
        update_info = resolve_updates_for_mod(
            local_mod=local_mod,
            settings=settings,
            modrinth=modrinth,
            curseforge=curseforge,
        )
        updates.append(update_info)

    return updates


def apply_updates(items: list[UpdateInfo], settings: AppSettings, dry_run: bool = False) -> tuple[list[AppliedUpdate], list[str]]:
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


def build_changelog_text(update_info: UpdateInfo, enabled_filters: set[str] | None = None) -> str:
    lines: list[str] = []
    filters = enabled_filters or set(CHANGELOG_CATEGORY_ORDER)

    local = update_info.local_mod
    lines.append(f"Mod: {local.name} ({local.mod_id})")
    lines.append(f"Version locale: {local.version}")
    lines.append(f"Statut: {update_info.status}")
    lines.append(f"Provider: {update_info.provider or '-'}")
    if update_info.match_confidence > 0 or update_info.match_score > 0:
        lines.append(f"Confiance matching: {update_info.match_confidence:.2f} (score {update_info.match_score:.2f})")
    if update_info.match_note:
        lines.append(f"Note matching: {update_info.match_note}")
    lines.append(f"Filtres changelog: {', '.join(sorted(filters)) if filters else 'none'}")
    lines.append("")

    if update_info.latest is None:
        lines.append(update_info.message)
        return "\n".join(lines)

    lines.append(f"Derniere version: {update_info.latest.version_number}")
    lines.append(f"Publiee: {update_info.latest.published_at.isoformat()}")
    lines.append("")

    versions_to_describe = list(reversed(update_info.intermediate_versions))
    if not versions_to_describe and update_info.latest is not None:
        versions_to_describe = [update_info.latest]

    if not versions_to_describe:
        lines.append(update_info.message)
        return "\n".join(lines)

    category_summary = summarize_changelog_categories(update_info)
    lines.append("Resume categories detectees:")
    for category in CHANGELOG_CATEGORY_ORDER:
        lines.append(f"- {category}: {category_summary.get(category, 0)}")

    lines.append("")
    lines.append("Versions intermediaires detectees:")
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
                lines.append("  Aucun element du changelog ne correspond aux filtres actifs.")
        else:
            lines.append("  Changelog non fourni par le provider.")

    return "\n".join(lines)
