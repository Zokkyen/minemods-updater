from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional


@dataclass(slots=True)
class LocalMod:
    path: Path
    mod_id: str
    name: str
    version: str
    loader_hint: str = "unknown"
    minecraft_hint: Optional[str] = None


@dataclass(slots=True)
class RemoteVersion:
    provider: str
    project_id: str
    version_id: str
    version_number: str
    published_at: datetime
    download_url: str
    filename: str
    game_versions: list[str] = field(default_factory=list)
    loaders: list[str] = field(default_factory=list)
    changelog: str = ""


@dataclass(slots=True)
class MatchCandidate:
    provider: str
    project_id: str
    title: str
    slug: str
    score: float
    confidence: float
    rank: int = 0
    downloads: int = 0
    accepted: bool = False
    note: str = ""


@dataclass(slots=True)
class UpdateInfo:
    local_mod: LocalMod
    status: str
    message: str
    provider: str = ""
    latest: Optional[RemoteVersion] = None
    intermediate_versions: list[RemoteVersion] = field(default_factory=list)
    match_score: float = 0.0
    match_confidence: float = 0.0
    matched_project_id: str = ""
    match_note: str = ""
    match_candidates: list[MatchCandidate] = field(default_factory=list)


@dataclass(slots=True)
class InstanceProfile:
    name: str = "Default"
    mods_directory: str = ""
    minecraft_version: str = "1.21.1"
    loader: str = "auto"
    use_modrinth: bool = True
    use_curseforge: bool = False
    curseforge_api_key: str = ""
    backup_suffix: str = ".old"
    modrinth_project_map: dict[str, str] = field(default_factory=dict)
    curseforge_project_map: dict[str, int] = field(default_factory=dict)


@dataclass(slots=True)
class AppSettings:
    mods_directory: str = ""
    minecraft_version: str = "1.21.1"
    loader: str = "auto"
    use_modrinth: bool = True
    use_curseforge: bool = False
    curseforge_api_key: str = ""
    backup_suffix: str = ".old"
    modrinth_project_map: dict[str, str] = field(default_factory=dict)
    curseforge_project_map: dict[str, int] = field(default_factory=dict)
    active_profile: str = "Default"
    profiles: dict[str, InstanceProfile] = field(default_factory=dict)
    changelog_filters: list[str] = field(default_factory=lambda: ["breaking", "fix", "performance", "other"])
    strict_matching: bool = True
    dry_run: bool = False
