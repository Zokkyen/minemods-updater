from __future__ import annotations

import json
import re
import zipfile
from collections import Counter
from pathlib import Path

from .models import LocalMod


MINECRAFT_VERSION_PATTERN = re.compile(r"1\.\d{1,2}(?:\.\d+)?")
METADATA_PLACEHOLDER_VERSION = "${file.jarVersion}"


def scan_mods(mods_directory: str) -> list[LocalMod]:
    mods_path = Path(mods_directory)
    if not mods_path.exists() or not mods_path.is_dir():
        raise FileNotFoundError(f"Le dossier mods n'existe pas: {mods_directory}")

    mods: list[LocalMod] = []
    for path in sorted(mods_path.glob("*.jar")):
        try:
            mod = _parse_mod_jar(path)
        except Exception:
            mod = _fallback_mod(path)
        mods.append(mod)
    return mods


def autodetect_loader_and_minecraft(mods: list[LocalMod]) -> tuple[str, str]:
    loader_counter = Counter(m.loader_hint for m in mods if m.loader_hint and m.loader_hint != "unknown")
    mc_counter = Counter(m.minecraft_hint for m in mods if m.minecraft_hint)

    loader = loader_counter.most_common(1)[0][0] if loader_counter else "unknown"
    minecraft_version = mc_counter.most_common(1)[0][0] if mc_counter else ""
    return loader, minecraft_version


def _parse_mod_jar(jar_path: Path) -> LocalMod:
    with zipfile.ZipFile(jar_path, "r") as archive:
        names = set(archive.namelist())

        if "fabric.mod.json" in names:
            return _parse_fabric_like(jar_path, archive, "fabric.mod.json", "fabric")

        if "quilt.mod.json" in names:
            return _parse_fabric_like(jar_path, archive, "quilt.mod.json", "quilt")

        if "META-INF/mods.toml" in names:
            return _parse_forge_toml(jar_path, archive)

        if "mcmod.info" in names:
            return _parse_mcmod_info(jar_path, archive)

    return _fallback_mod(jar_path)


def _parse_fabric_like(jar_path: Path, archive: zipfile.ZipFile, metadata_name: str, loader: str) -> LocalMod:
    content = archive.read(metadata_name).decode("utf-8", errors="replace")
    payload = json.loads(content)

    mod_id = str(payload.get("id") or payload.get("quilt_loader", {}).get("id") or jar_path.stem)
    name = str(payload.get("name") or payload.get("quilt_loader", {}).get("metadata", {}).get("name") or mod_id)
    version = str(payload.get("version") or payload.get("quilt_loader", {}).get("version") or "unknown")

    depends = payload.get("depends") or payload.get("quilt_loader", {}).get("depends") or {}
    minecraft_hint = _extract_minecraft_from_mapping(depends)

    return LocalMod(
        path=jar_path,
        mod_id=mod_id,
        name=name,
        version=version,
        loader_hint=loader,
        minecraft_hint=minecraft_hint,
    )


def _parse_forge_toml(jar_path: Path, archive: zipfile.ZipFile) -> LocalMod:
    content = archive.read("META-INF/mods.toml").decode("utf-8", errors="replace")
    mod_id = _extract_toml_key(content, "modId") or jar_path.stem
    name = _extract_toml_key(content, "displayName") or mod_id
    version = _extract_toml_key(content, "version") or "unknown"
    if version == METADATA_PLACEHOLDER_VERSION:
        manifest_version = _read_manifest_version(archive)
        if manifest_version:
            version = manifest_version

    minecraft_hint = _extract_minecraft_from_text(content)
    loader = "forge"
    if "neoforge" in content.lower():
        loader = "neoforge"

    return LocalMod(
        path=jar_path,
        mod_id=mod_id,
        name=name,
        version=version,
        loader_hint=loader,
        minecraft_hint=minecraft_hint,
    )


def _parse_mcmod_info(jar_path: Path, archive: zipfile.ZipFile) -> LocalMod:
    content = archive.read("mcmod.info").decode("utf-8", errors="replace").strip()

    mod_id = jar_path.stem
    name = mod_id
    version = "unknown"
    minecraft_hint = None

    try:
        payload = json.loads(content)
        data = payload[0] if isinstance(payload, list) and payload else payload
        if isinstance(data, dict):
            mod_id = str(data.get("modid") or data.get("modId") or mod_id)
            name = str(data.get("name") or mod_id)
            version = str(data.get("version") or version)
            minecraft_hint = str(data.get("mcversion")) if data.get("mcversion") else None
    except json.JSONDecodeError:
        minecraft_hint = _extract_minecraft_from_text(content)

    return LocalMod(
        path=jar_path,
        mod_id=mod_id,
        name=name,
        version=version,
        loader_hint="forge",
        minecraft_hint=minecraft_hint,
    )


def _extract_toml_key(content: str, key: str) -> str | None:
    match = re.search(rf"^\s*{re.escape(key)}\s*=\s*\"([^\"]+)\"", content, flags=re.MULTILINE)
    if not match:
        return None
    return match.group(1).strip()


def _read_manifest_version(archive: zipfile.ZipFile) -> str | None:
    manifest_name = "META-INF/MANIFEST.MF"
    if manifest_name not in archive.namelist():
        return None

    content = archive.read(manifest_name).decode("utf-8", errors="replace")
    for line in content.splitlines():
        if line.lower().startswith("implementation-version:"):
            return line.split(":", 1)[1].strip()
    return None


def _extract_minecraft_from_mapping(depends: object) -> str | None:
    if not isinstance(depends, dict):
        return None

    minecraft_value = depends.get("minecraft")
    if minecraft_value is None:
        return None

    if isinstance(minecraft_value, (list, tuple)):
        for item in minecraft_value:
            hit = _extract_minecraft_from_text(str(item))
            if hit:
                return hit
        return None

    return _extract_minecraft_from_text(str(minecraft_value))


def _extract_minecraft_from_text(text: str) -> str | None:
    versions = MINECRAFT_VERSION_PATTERN.findall(text)
    if not versions:
        return None

    # We assume the latest explicit version in metadata is the most representative hint.
    return versions[-1]


def _fallback_mod(jar_path: Path) -> LocalMod:
    stem = jar_path.stem
    clean = re.sub(r"[+_]", "-", stem)
    parts = clean.split("-")

    mod_id = parts[0] if parts else stem
    version = "unknown"

    for index, token in enumerate(parts[1:], start=1):
        if re.match(r"^\d+(?:\.\d+){1,}", token):
            mod_id = "-".join(parts[:index])
            version = "-".join(parts[index:])
            break

    return LocalMod(
        path=jar_path,
        mod_id=mod_id.strip() or stem,
        name=mod_id.strip() or stem,
        version=version,
        loader_hint="unknown",
        minecraft_hint=_extract_minecraft_from_text(stem),
    )
