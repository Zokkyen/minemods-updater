from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path

from .app_meta import APP_DIR_NAME, LEGACY_APP_DIR_NAME
from .models import AppSettings, InstanceProfile


SETTINGS_FILE_NAME = "settings.json"
VALID_CHANGELOG_FILTERS = {"breaking", "fix", "performance", "other"}


def _settings_dir(app_dir_name: str = APP_DIR_NAME) -> Path:
    appdata = os.getenv("APPDATA")
    if appdata:
        return Path(appdata) / app_dir_name
    return Path.home() / f".{app_dir_name.lower()}"


def _legacy_settings_file_path() -> Path:
    return _settings_dir(LEGACY_APP_DIR_NAME) / SETTINGS_FILE_NAME


def settings_file_path() -> Path:
    return _settings_dir() / SETTINGS_FILE_NAME


def _profile_from_payload(name: str, payload: dict, fallback: InstanceProfile) -> InstanceProfile:
    return InstanceProfile(
        name=name,
        mods_directory=str(payload.get("mods_directory", fallback.mods_directory)),
        minecraft_version=str(payload.get("minecraft_version", fallback.minecraft_version)),
        loader=str(payload.get("loader", fallback.loader)),
        use_modrinth=bool(payload.get("use_modrinth", fallback.use_modrinth)),
        use_curseforge=bool(payload.get("use_curseforge", fallback.use_curseforge)),
        curseforge_api_key=str(payload.get("curseforge_api_key", fallback.curseforge_api_key)),
        backup_suffix=str(payload.get("backup_suffix", fallback.backup_suffix)),
        modrinth_project_map=dict(payload.get("modrinth_project_map", fallback.modrinth_project_map)),
        curseforge_project_map=dict(payload.get("curseforge_project_map", fallback.curseforge_project_map)),
    )


def _profile_from_settings_fields(settings: AppSettings, name: str) -> InstanceProfile:
    return InstanceProfile(
        name=name,
        mods_directory=settings.mods_directory,
        minecraft_version=settings.minecraft_version,
        loader=settings.loader,
        use_modrinth=settings.use_modrinth,
        use_curseforge=settings.use_curseforge,
        curseforge_api_key=settings.curseforge_api_key,
        backup_suffix=settings.backup_suffix,
        modrinth_project_map=dict(settings.modrinth_project_map),
        curseforge_project_map=dict(settings.curseforge_project_map),
    )


def _apply_profile_to_fields(settings: AppSettings, profile_name: str) -> None:
    profile = settings.profiles.get(profile_name)
    if profile is None:
        return

    settings.mods_directory = profile.mods_directory
    settings.minecraft_version = profile.minecraft_version
    settings.loader = profile.loader
    settings.use_modrinth = profile.use_modrinth
    settings.use_curseforge = profile.use_curseforge
    settings.curseforge_api_key = profile.curseforge_api_key
    settings.backup_suffix = profile.backup_suffix
    settings.modrinth_project_map = dict(profile.modrinth_project_map)
    settings.curseforge_project_map = dict(profile.curseforge_project_map)


def _ensure_profiles(settings: AppSettings) -> None:
    if not settings.profiles:
        default_name = settings.active_profile or "Default"
        settings.profiles = {default_name: _profile_from_settings_fields(settings, default_name)}

    if settings.active_profile not in settings.profiles:
        settings.active_profile = next(iter(settings.profiles.keys()))

    _apply_profile_to_fields(settings, settings.active_profile)


def write_back_active_profile(settings: AppSettings) -> None:
    _ensure_profiles(settings)
    settings.profiles[settings.active_profile] = _profile_from_settings_fields(settings, settings.active_profile)


def set_active_profile(settings: AppSettings, profile_name: str) -> None:
    _ensure_profiles(settings)
    if profile_name not in settings.profiles:
        raise KeyError(f"Unknown profile: {profile_name}")
    # Persist current form-backed fields to current active profile first.
    write_back_active_profile(settings)
    settings.active_profile = profile_name
    _apply_profile_to_fields(settings, profile_name)


def add_profile(settings: AppSettings, profile_name: str, clone_current: bool = True) -> None:
    clean_name = profile_name.strip()
    if not clean_name:
        raise ValueError("Profile name cannot be empty")

    _ensure_profiles(settings)
    if clean_name in settings.profiles:
        raise ValueError("Profile name already exists")

    if clone_current:
        base = _profile_from_settings_fields(settings, clean_name)
    else:
        base = InstanceProfile(name=clean_name)

    settings.profiles[clean_name] = base
    settings.active_profile = clean_name
    _apply_profile_to_fields(settings, clean_name)


def remove_profile(settings: AppSettings, profile_name: str) -> str:
    _ensure_profiles(settings)
    if profile_name not in settings.profiles:
        raise KeyError(f"Unknown profile: {profile_name}")
    if len(settings.profiles) == 1:
        raise ValueError("At least one profile must remain")

    del settings.profiles[profile_name]
    if settings.active_profile == profile_name:
        settings.active_profile = next(iter(settings.profiles.keys()))

    _apply_profile_to_fields(settings, settings.active_profile)
    return settings.active_profile


def load_settings() -> AppSettings:
    path = settings_file_path()
    if not path.exists():
        legacy_path = _legacy_settings_file_path()
        if legacy_path.exists():
            path = legacy_path

    if not path.exists():
        settings = AppSettings()
        _ensure_profiles(settings)
        return settings

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        settings = AppSettings()
        _ensure_profiles(settings)
        return settings

    defaults = AppSettings()

    raw_filters = payload.get("changelog_filters", defaults.changelog_filters)
    if isinstance(raw_filters, list):
        changelog_filters = [item for item in raw_filters if isinstance(item, str) and item in VALID_CHANGELOG_FILTERS]
    else:
        changelog_filters = list(defaults.changelog_filters)

    if not changelog_filters:
        changelog_filters = list(defaults.changelog_filters)

    settings = AppSettings(
        mods_directory=str(payload.get("mods_directory", defaults.mods_directory)),
        minecraft_version=str(payload.get("minecraft_version", defaults.minecraft_version)),
        loader=str(payload.get("loader", defaults.loader)),
        use_modrinth=bool(payload.get("use_modrinth", defaults.use_modrinth)),
        use_curseforge=bool(payload.get("use_curseforge", defaults.use_curseforge)),
        curseforge_api_key=str(payload.get("curseforge_api_key", defaults.curseforge_api_key)),
        backup_suffix=str(payload.get("backup_suffix", defaults.backup_suffix)),
        modrinth_project_map=dict(payload.get("modrinth_project_map", defaults.modrinth_project_map)),
        curseforge_project_map=dict(payload.get("curseforge_project_map", defaults.curseforge_project_map)),
        active_profile=str(payload.get("active_profile", defaults.active_profile)),
        profiles={},
        changelog_filters=changelog_filters,
        strict_matching=bool(payload.get("strict_matching", defaults.strict_matching)),
        dry_run=bool(payload.get("dry_run", defaults.dry_run)),
    )

    profiles_payload = payload.get("profiles")
    if isinstance(profiles_payload, dict):
        for profile_name, profile_payload in profiles_payload.items():
            if not isinstance(profile_name, str) or not profile_name.strip():
                continue
            if not isinstance(profile_payload, dict):
                continue
            fallback = _profile_from_settings_fields(settings, profile_name)
            settings.profiles[profile_name] = _profile_from_payload(profile_name, profile_payload, fallback)

    if not settings.profiles:
        # Backward compatibility: create a profile from top-level fields.
        profile_name = settings.active_profile or "Default"
        settings.profiles[profile_name] = _profile_from_settings_fields(settings, profile_name)

    _ensure_profiles(settings)
    return settings


def save_settings(settings: AppSettings) -> None:
    write_back_active_profile(settings)
    path = settings_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = asdict(settings)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
