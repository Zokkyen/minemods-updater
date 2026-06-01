"""Report generation helpers for JSON/CSV export.

Reports are built as before/after snapshots so users can review check results,
applied updates, and errors in a deterministic structure.
"""

from __future__ import annotations

import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from .app_meta import APP_NAME, APP_SLUG
from .models import AppSettings, LocalMod, MatchCandidate, UpdateInfo
from .update_service import AppliedUpdate


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_slug(value: str) -> str:
    return re.sub(r"[^a-z0-9_-]+", "-", value.lower()).strip("-") or "default"


def _candidate_to_dict(candidate: MatchCandidate) -> dict:
    return {
        "provider": candidate.provider,
        "project_id": candidate.project_id,
        "title": candidate.title,
        "slug": candidate.slug,
        "score": candidate.score,
        "confidence": candidate.confidence,
        "rank": candidate.rank,
        "downloads": candidate.downloads,
        "accepted": candidate.accepted,
        "note": candidate.note,
    }


def _update_to_dict(local_mod: LocalMod, update_info: UpdateInfo | None) -> dict:
    latest_version = update_info.latest.version_number if update_info and update_info.latest else ""
    return {
        "name": local_mod.name,
        "mod_id": local_mod.mod_id,
        "file_name": local_mod.path.name,
        "path": str(local_mod.path),
        "loader_hint": local_mod.loader_hint,
        "local_version": local_mod.version,
        "status": update_info.status if update_info else "scanned",
        "provider": update_info.provider if update_info else "",
        "latest_version": latest_version,
        "message": update_info.message if update_info else "",
        "match_score": update_info.match_score if update_info else 0.0,
        "match_confidence": update_info.match_confidence if update_info else 0.0,
        "matched_project_id": update_info.matched_project_id if update_info else "",
        "match_note": update_info.match_note if update_info else "",
        "match_candidates": [
            _candidate_to_dict(candidate)
            for candidate in (update_info.match_candidates if update_info else [])
        ],
    }


def build_before_snapshot(local_mods: list[LocalMod], update_infos: list[UpdateInfo]) -> dict:
    """Serialize scanned mods and update-check outcomes before apply/simulate."""
    by_path = {info.local_mod.path: info for info in update_infos}
    rows = [_update_to_dict(local_mod, by_path.get(local_mod.path)) for local_mod in local_mods]

    return {
        "captured_at": _iso_now(),
        "mods_count": len(rows),
        "mods": rows,
    }


def build_after_snapshot(
    post_mods: list[LocalMod],
    applied: list[AppliedUpdate],
    errors: list[str],
    dry_run: bool,
) -> dict:
    """Serialize post-operation state, including simulated/applied actions."""
    updates = [
        {
            "mod_name": item.mod_name,
            "mod_id": item.mod_id,
            "old_version": item.old_version,
            "new_version": item.new_version,
            "provider": item.provider,
            "old_path": str(item.old_path),
            "new_path": str(item.new_path),
            "backup_path": str(item.backup_path),
            "simulated": item.simulated,
        }
        for item in applied
    ]

    mods_rows = [
        {
            "name": local_mod.name,
            "mod_id": local_mod.mod_id,
            "file_name": local_mod.path.name,
            "path": str(local_mod.path),
            "loader_hint": local_mod.loader_hint,
            "local_version": local_mod.version,
        }
        for local_mod in post_mods
    ]

    return {
        "captured_at": _iso_now(),
        "dry_run": dry_run,
        "applied_count": len(updates),
        "error_count": len(errors),
        "updates": updates,
        "errors": errors,
        "mods_after": mods_rows,
    }


def build_full_report(settings: AppSettings, before_snapshot: dict | None, after_snapshot: dict | None) -> dict:
    """Assemble the final report document with context and both snapshots."""
    return {
        "report_version": "1.0",
        "generated_at": _iso_now(),
        "app": {
            "name": APP_NAME,
            "slug": APP_SLUG,
        },
        "context": {
            "mods_directory": settings.mods_directory,
            "minecraft_version": settings.minecraft_version,
            "loader": settings.loader,
            "providers": {
                "modrinth": settings.use_modrinth,
                "curseforge": settings.use_curseforge,
            },
            "strict_matching": settings.strict_matching,
            "dry_run": settings.dry_run,
            "changelog_filters": list(settings.changelog_filters),
        },
        "before": before_snapshot,
        "after": after_snapshot,
    }


def make_report_basename(_settings: AppSettings) -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{APP_SLUG}-{stamp}"


def export_report_json(report: dict, output_dir: Path, basename: str) -> Path:
    """Write the full report as UTF-8 JSON."""
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{basename}.json"
    output_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return output_path


def export_report_csv(report: dict, output_dir: Path, basename: str) -> Path:
    """Write a flattened CSV view for quick spreadsheet analysis."""
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{basename}.csv"

    fieldnames = [
        "phase",
        "mod_name",
        "mod_id",
        "file_name",
        "local_version",
        "latest_version",
        "status",
        "provider",
        "action",
        "simulated",
        "match_confidence",
        "match_score",
        "message",
        "backup_path",
        "new_path",
    ]

    rows: list[dict] = []
    before = report.get("before") or {}
    for item in before.get("mods", []):
        rows.append(
            {
                "phase": "before",
                "mod_name": item.get("name", ""),
                "mod_id": item.get("mod_id", ""),
                "file_name": item.get("file_name", ""),
                "local_version": item.get("local_version", ""),
                "latest_version": item.get("latest_version", ""),
                "status": item.get("status", ""),
                "provider": item.get("provider", ""),
                "action": "check",
                "simulated": "",
                "match_confidence": item.get("match_confidence", ""),
                "match_score": item.get("match_score", ""),
                "message": item.get("message", ""),
                "backup_path": "",
                "new_path": "",
            }
        )

    after = report.get("after") or {}
    for item in after.get("updates", []):
        rows.append(
            {
                "phase": "after-update",
                "mod_name": item.get("mod_name", ""),
                "mod_id": item.get("mod_id", ""),
                "file_name": Path(item.get("new_path", "")).name if item.get("new_path") else "",
                "local_version": item.get("old_version", ""),
                "latest_version": item.get("new_version", ""),
                "status": "updated",
                "provider": item.get("provider", ""),
                "action": "simulate" if item.get("simulated") else "apply",
                "simulated": str(bool(item.get("simulated"))),
                "match_confidence": "",
                "match_score": "",
                "message": "",
                "backup_path": item.get("backup_path", ""),
                "new_path": item.get("new_path", ""),
            }
        )

    for error in after.get("errors", []):
        rows.append(
            {
                "phase": "after-error",
                "mod_name": "",
                "mod_id": "",
                "file_name": "",
                "local_version": "",
                "latest_version": "",
                "status": "error",
                "provider": "",
                "action": "apply",
                "simulated": str(bool(after.get("dry_run", False))),
                "match_confidence": "",
                "match_score": "",
                "message": str(error),
                "backup_path": "",
                "new_path": "",
            }
        )

    with output_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    return output_path
