from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from datetime import datetime, timezone
from typing import Iterable

import requests

from .app_meta import APP_USER_AGENT
from .models import AppSettings, LocalMod, MatchCandidate, RemoteVersion, UpdateInfo


REQUEST_TIMEOUT_SECONDS = 20


def _parse_iso_datetime(value: str) -> datetime:
    if not value:
        return datetime.min.replace(tzinfo=timezone.utc)
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _normalize_version(value: str) -> str:
    cleaned = value.strip().lower()
    cleaned = cleaned.removeprefix("v")
    cleaned = re.sub(r"\s+", "", cleaned)
    return cleaned


def _normalized_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.strip().lower())


def _token_set(value: str) -> set[str]:
    tokens = re.split(r"[^a-z0-9]+", value.lower())
    return {token for token in tokens if len(token) >= 2}


def _string_similarity(left: str, right: str) -> float:
    a = _normalized_text(left)
    b = _normalized_text(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def _token_similarity(left: str, right: str) -> float:
    left_tokens = _token_set(left)
    right_tokens = _token_set(right)
    if not left_tokens or not right_tokens:
        return 0.0
    union = left_tokens | right_tokens
    if not union:
        return 0.0
    return len(left_tokens & right_tokens) / len(union)


def _match_confidence(local_mod: LocalMod, candidate_slug: str, candidate_title: str) -> float:
    local_values = [local_mod.mod_id, local_mod.name, local_mod.path.stem]
    remote_values = [candidate_slug, candidate_title]

    best_string = max(
        (_string_similarity(local, remote) for local in local_values for remote in remote_values),
        default=0.0,
    )
    best_token = max(
        (_token_similarity(local, remote) for local in local_values for remote in remote_values),
        default=0.0,
    )

    # Weight token overlap higher to avoid matching stylistically similar but semantically different mods.
    return max(best_string * 0.9, best_token * 1.05)


def _version_matches(local_version: str, remote_version: str) -> bool:
    if _normalize_version(local_version) == _normalize_version(remote_version):
        return True

    # Some jars expose versions like 1.2.3+mc1.21 while remote uses 1.2.3.
    local_short = _normalize_version(local_version).split("+")[0]
    remote_short = _normalize_version(remote_version).split("+")[0]
    return bool(local_short and remote_short and local_short == remote_short)


def _rank_versions(local_version: str, versions: list[RemoteVersion]) -> tuple[str, str, RemoteVersion | None, list[RemoteVersion]]:
    if not versions:
        return "not_found", "No compatible version found.", None, []

    latest = versions[0]
    for idx, version in enumerate(versions):
        if _version_matches(local_version, version.version_number):
            if idx == 0:
                return "up_to_date", "Mod is already up to date.", latest, []
            return "update_available", f"{idx} newer version(s) found.", latest, versions[:idx]

    if _version_matches(local_version, latest.version_number):
        return "up_to_date", "Mod is already up to date.", latest, []

    # Local version missing on provider: propose latest compatible version.
    intermediate = versions[: min(8, len(versions))]
    return (
        "update_available",
        "Local version not found on provider, latest compatible version proposed.",
        latest,
        intermediate,
    )


class ModrinthProvider:
    name = "Modrinth"
    base_url = "https://api.modrinth.com/v2"

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": APP_USER_AGENT,
                "Accept": "application/json",
            }
        )

    def fetch_minecraft_versions(self) -> list[str]:
        url = f"{self.base_url}/tag/game_version"
        try:
            response = self.session.get(url, timeout=REQUEST_TIMEOUT_SECONDS)
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException:
            return [
                "1.21.1",
                "1.21",
                "1.20.6",
                "1.20.4",
                "1.20.1",
                "1.19.4",
                "1.18.2",
                "1.16.5",
            ]

        releases = [
            item.get("version")
            for item in payload
            if isinstance(item, dict)
            and item.get("version_type") in {"release", "snapshot"}
            and item.get("version")
        ]
        # Keep unique values while preserving order.
        return list(dict.fromkeys(releases))[:120]

    def resolve_update(
        self,
        local_mod: LocalMod,
        minecraft_version: str,
        loader: str,
        project_map: dict[str, str],
        strict_matching: bool,
    ) -> UpdateInfo:
        try:
            project_id = project_map.get(local_mod.mod_id)
            match_candidates: list[MatchCandidate] = []
            match_note = ""

            if project_id and not self._project_exists(project_id):
                project_id = None

            if project_id:
                match_candidates = [
                    MatchCandidate(
                        provider=self.name,
                        project_id=project_id,
                        title=local_mod.name,
                        slug=local_mod.mod_id,
                        score=999.0,
                        confidence=1.0,
                        rank=1,
                        accepted=True,
                        note="Mapping local deja valide.",
                    )
                ]

            if not project_id:
                hit, match_candidates, match_note = self._search_best_project(
                    local_mod,
                    minecraft_version,
                    loader,
                    strict_matching,
                )
                if not hit:
                    return UpdateInfo(
                        local_mod=local_mod,
                        status="not_found",
                        message="Aucun matching fiable trouve sur Modrinth.",
                        provider=self.name,
                        match_note=match_note,
                        match_candidates=match_candidates,
                    )
                project_id = hit.get("project_id")

            if not project_id:
                return UpdateInfo(
                    local_mod=local_mod,
                    status="not_found",
                    message="Modrinth project not found.",
                    provider=self.name,
                    match_note=match_note,
                    match_candidates=match_candidates,
                )

            versions = self._fetch_versions(project_id, minecraft_version, loader)
            status, message, latest, intermediate = _rank_versions(local_mod.version, versions)

            if status != "not_found":
                project_map[local_mod.mod_id] = project_id

            selected_candidate = next((candidate for candidate in match_candidates if candidate.accepted), None)

            return UpdateInfo(
                local_mod=local_mod,
                status=status,
                message=message,
                provider=self.name,
                latest=latest,
                intermediate_versions=intermediate,
                match_score=selected_candidate.score if selected_candidate else 0.0,
                match_confidence=selected_candidate.confidence if selected_candidate else 0.0,
                matched_project_id=str(project_id),
                match_note=match_note or (selected_candidate.note if selected_candidate else ""),
                match_candidates=match_candidates,
            )
        except requests.RequestException as exc:
            return UpdateInfo(
                local_mod=local_mod,
                status="error",
                message=f"Modrinth network error: {exc}",
                provider=self.name,
            )
        except Exception as exc:  # Defensive guard for malformed provider responses.
            return UpdateInfo(
                local_mod=local_mod,
                status="error",
                message=f"Modrinth error: {exc}",
                provider=self.name,
            )

    def _project_exists(self, project_id: str) -> bool:
        response = self.session.get(f"{self.base_url}/project/{project_id}", timeout=REQUEST_TIMEOUT_SECONDS)
        if response.status_code == 404:
            return False
        response.raise_for_status()
        return True

    def _search_best_project(
        self,
        local_mod: LocalMod,
        minecraft_version: str,
        loader: str,
        strict_matching: bool,
    ) -> tuple[dict | None, list[MatchCandidate], str]:
        candidates: list[dict] = []
        for query in [local_mod.mod_id, local_mod.name]:
            if not query:
                continue
            hits = self._search_projects(query=query, minecraft_version=minecraft_version, loader=loader)
            candidates.extend(hits)

        if not candidates:
            return None, [], "Aucun resultat de recherche provider."

        unique_by_project = {item.get("project_id"): item for item in candidates if item.get("project_id")}
        scored: list[tuple[float, float, dict]] = []
        for item in unique_by_project.values():
            score, confidence = self._score_hit(item, local_mod)
            scored.append((score, confidence, item))

        scored.sort(key=lambda item: item[0], reverse=True)
        if not scored:
            return None, [], "Aucun candidat score apres filtrage."

        ranked_candidates: list[MatchCandidate] = []
        for rank, (score, confidence, item) in enumerate(scored[:12], start=1):
            ranked_candidates.append(
                MatchCandidate(
                    provider=self.name,
                    project_id=str(item.get("project_id", "")),
                    title=str(item.get("title", "")),
                    slug=str(item.get("slug", "")),
                    score=round(float(score), 3),
                    confidence=round(float(confidence), 4),
                    rank=rank,
                    downloads=int(item.get("downloads") or 0),
                )
            )

        best_score, best_confidence, best_item = scored[0]
        confidence_threshold = 0.62 if strict_matching else 0.48
        if best_confidence < confidence_threshold:
            if ranked_candidates:
                ranked_candidates[0].note = f"Confiance {best_confidence:.2f} < seuil {confidence_threshold:.2f}"
            return None, ranked_candidates, "Confiance de matching insuffisante."

        if strict_matching and len(scored) > 1:
            second_score, second_confidence, _ = scored[1]
            too_close = (best_score - second_score) < 8 and second_confidence >= 0.58
            if too_close:
                if ranked_candidates:
                    ranked_candidates[0].note = "Ambigu avec un autre projet proche."
                if len(ranked_candidates) > 1:
                    ranked_candidates[1].note = "Ambigu avec le meilleur candidat."
                return None, ranked_candidates, "Resultat ambigu entre plusieurs projets."

        selected_project = str(best_item.get("project_id", ""))
        for candidate in ranked_candidates:
            if candidate.project_id == selected_project:
                candidate.accepted = True
                candidate.note = candidate.note or "Projet selectionne."
                break

        return best_item, ranked_candidates, ""

    def _search_projects(self, query: str, minecraft_version: str, loader: str) -> list[dict]:
        facets = [["project_type:mod"]]
        if loader and loader not in {"auto", "unknown"}:
            facets.append([f"categories:{loader}"])
        if minecraft_version:
            facets.append([f"versions:{minecraft_version}"])

        params = {
            "query": query,
            "limit": 10,
            "index": "relevance",
            "facets": json.dumps(facets),
        }

        response = self.session.get(f"{self.base_url}/search", params=params, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
        payload = response.json()
        return payload.get("hits", []) if isinstance(payload, dict) else []

    def _score_hit(self, hit: dict, local_mod: LocalMod) -> tuple[float, float]:
        score = 0
        slug = str(hit.get("slug", "")).lower()
        title = str(hit.get("title", "")).lower()
        mod_id = local_mod.mod_id.lower()
        name = local_mod.name.lower()

        if slug == mod_id:
            score += 100
        if title == mod_id or title == name:
            score += 80
        if mod_id and mod_id in slug:
            score += 40
        if name and name in title:
            score += 30

        score += int(hit.get("downloads", 0) / 10000)
        confidence = _match_confidence(local_mod, slug, title)
        score += confidence * 120
        return score, confidence

    def _fetch_versions(self, project_id: str, minecraft_version: str, loader: str) -> list[RemoteVersion]:
        params: dict[str, str] = {}
        if minecraft_version:
            params["game_versions"] = json.dumps([minecraft_version])
        if loader and loader not in {"auto", "unknown"}:
            params["loaders"] = json.dumps([loader])

        response = self.session.get(
            f"{self.base_url}/project/{project_id}/version",
            params=params,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()

        payload = response.json()
        versions: list[RemoteVersion] = []
        for item in payload if isinstance(payload, list) else []:
            files = item.get("files", []) if isinstance(item, dict) else []
            chosen_file = _select_primary_file(files)
            if not chosen_file:
                continue

            versions.append(
                RemoteVersion(
                    provider=self.name,
                    project_id=project_id,
                    version_id=str(item.get("id", "")),
                    version_number=str(item.get("version_number", "unknown")),
                    published_at=_parse_iso_datetime(str(item.get("date_published", ""))),
                    download_url=str(chosen_file.get("url", "")),
                    filename=str(chosen_file.get("filename", "")),
                    game_versions=[str(v) for v in item.get("game_versions", [])],
                    loaders=[str(v) for v in item.get("loaders", [])],
                    changelog=str(item.get("changelog") or ""),
                )
            )

        versions.sort(key=lambda version: version.published_at, reverse=True)
        return versions


class CurseForgeProvider:
    name = "CurseForge"
    base_url = "https://api.curseforge.com/v1"
    game_id = 432
    class_id = 6

    loader_map = {
        "forge": 1,
        "fabric": 4,
        "quilt": 5,
        "neoforge": 6,
    }

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key.strip()
        self.session = requests.Session()
        self.session.headers.update(
            {
                "x-api-key": self.api_key,
                "Accept": "application/json",
                "User-Agent": APP_USER_AGENT,
            }
        )

    def resolve_update(
        self,
        local_mod: LocalMod,
        minecraft_version: str,
        loader: str,
        project_map: dict[str, int],
        strict_matching: bool,
    ) -> UpdateInfo:
        if not self.api_key:
            return UpdateInfo(
                local_mod=local_mod,
                status="not_found",
                message="CurseForge API key is missing.",
                provider=self.name,
                match_note="API key CurseForge absente.",
            )

        try:
            project_id = project_map.get(local_mod.mod_id)
            match_candidates: list[MatchCandidate] = []
            match_note = ""

            if project_id:
                match_candidates = [
                    MatchCandidate(
                        provider=self.name,
                        project_id=str(project_id),
                        title=local_mod.name,
                        slug=local_mod.mod_id,
                        score=999.0,
                        confidence=1.0,
                        rank=1,
                        accepted=True,
                        note="Mapping local deja valide.",
                    )
                ]

            if not project_id:
                project, match_candidates, match_note = self._search_best_project(local_mod, strict_matching)
                if not project:
                    return UpdateInfo(
                        local_mod=local_mod,
                        status="not_found",
                        message="Aucun matching fiable trouve sur CurseForge.",
                        provider=self.name,
                        match_note=match_note,
                        match_candidates=match_candidates,
                    )
                project_id = int(project.get("id"))

            versions = self._fetch_files(project_id, minecraft_version, loader)
            status, message, latest, intermediate = _rank_versions(local_mod.version, versions)

            if status != "not_found":
                project_map[local_mod.mod_id] = project_id

            selected_candidate = next((candidate for candidate in match_candidates if candidate.accepted), None)

            return UpdateInfo(
                local_mod=local_mod,
                status=status,
                message=message,
                provider=self.name,
                latest=latest,
                intermediate_versions=intermediate,
                match_score=selected_candidate.score if selected_candidate else 0.0,
                match_confidence=selected_candidate.confidence if selected_candidate else 0.0,
                matched_project_id=str(project_id),
                match_note=match_note or (selected_candidate.note if selected_candidate else ""),
                match_candidates=match_candidates,
            )
        except requests.RequestException as exc:
            return UpdateInfo(
                local_mod=local_mod,
                status="error",
                message=f"CurseForge network error: {exc}",
                provider=self.name,
            )
        except Exception as exc:
            return UpdateInfo(
                local_mod=local_mod,
                status="error",
                message=f"CurseForge error: {exc}",
                provider=self.name,
            )

    def _search_best_project(self, local_mod: LocalMod, strict_matching: bool) -> tuple[dict | None, list[MatchCandidate], str]:
        candidates: list[dict] = []
        for slug in _slug_candidates(local_mod):
            params = {
                "gameId": self.game_id,
                "classId": self.class_id,
                "slug": slug,
                "pageSize": 10,
            }
            response = self.session.get(f"{self.base_url}/mods/search", params=params, timeout=REQUEST_TIMEOUT_SECONDS)
            response.raise_for_status()
            payload = response.json().get("data", [])
            candidates.extend(payload)

        if not candidates:
            return None, [], "Aucun resultat de recherche provider."

        unique_by_project: dict[int, dict] = {}
        for item in candidates:
            project_id = int(item.get("id") or 0)
            if project_id <= 0:
                continue
            previous = unique_by_project.get(project_id)
            if not previous:
                unique_by_project[project_id] = item
                continue
            if int(item.get("downloadCount") or 0) > int(previous.get("downloadCount") or 0):
                unique_by_project[project_id] = item

        scored: list[tuple[float, float, dict]] = []
        for item in unique_by_project.values():
            score, confidence = self._score_hit(item, local_mod)
            scored.append((score, confidence, item))

        scored.sort(key=lambda item: item[0], reverse=True)
        if not scored:
            return None, [], "Aucun candidat score apres filtrage."

        ranked_candidates: list[MatchCandidate] = []
        for rank, (score, confidence, item) in enumerate(scored[:12], start=1):
            ranked_candidates.append(
                MatchCandidate(
                    provider=self.name,
                    project_id=str(item.get("id", "")),
                    title=str(item.get("name", "")),
                    slug=str(item.get("slug", "")),
                    score=round(float(score), 3),
                    confidence=round(float(confidence), 4),
                    rank=rank,
                    downloads=int(item.get("downloadCount") or 0),
                )
            )

        best_score, best_confidence, best_item = scored[0]
        confidence_threshold = 0.62 if strict_matching else 0.48
        if best_confidence < confidence_threshold:
            if ranked_candidates:
                ranked_candidates[0].note = f"Confiance {best_confidence:.2f} < seuil {confidence_threshold:.2f}"
            return None, ranked_candidates, "Confiance de matching insuffisante."

        if strict_matching and len(scored) > 1:
            second_score, second_confidence, _ = scored[1]
            too_close = (best_score - second_score) < 8 and second_confidence >= 0.58
            if too_close:
                if ranked_candidates:
                    ranked_candidates[0].note = "Ambigu avec un autre projet proche."
                if len(ranked_candidates) > 1:
                    ranked_candidates[1].note = "Ambigu avec le meilleur candidat."
                return None, ranked_candidates, "Resultat ambigu entre plusieurs projets."

        selected_project = str(best_item.get("id", ""))
        for candidate in ranked_candidates:
            if candidate.project_id == selected_project:
                candidate.accepted = True
                candidate.note = candidate.note or "Projet selectionne."
                break

        return best_item, ranked_candidates, ""

    def _score_hit(self, hit: dict, local_mod: LocalMod) -> tuple[float, float]:
        score = 0
        slug = str(hit.get("slug", "")).lower()
        name = str(hit.get("name", "")).lower()
        mod_id = local_mod.mod_id.lower()

        if slug == mod_id:
            score += 100
        if mod_id and mod_id in slug:
            score += 45
        if local_mod.name.lower() in name:
            score += 30
        score += int(hit.get("downloadCount", 0) / 50000)
        confidence = _match_confidence(local_mod, slug, name)
        score += confidence * 120
        return score, confidence

    def _fetch_files(self, project_id: int, minecraft_version: str, loader: str) -> list[RemoteVersion]:
        params: dict[str, str | int] = {
            "pageSize": 50,
        }
        if minecraft_version:
            params["gameVersion"] = minecraft_version

        loader_type = self.loader_map.get(loader)
        if loader_type:
            params["modLoaderType"] = loader_type

        response = self.session.get(
            f"{self.base_url}/mods/{project_id}/files",
            params=params,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()

        payload = response.json().get("data", [])
        versions: list[RemoteVersion] = []
        for item in payload:
            download_url = str(item.get("downloadUrl") or "")
            if not download_url:
                continue

            published_raw = str(item.get("fileDate") or "")
            if not published_raw:
                continue

            versions.append(
                RemoteVersion(
                    provider=self.name,
                    project_id=str(project_id),
                    version_id=str(item.get("id", "")),
                    version_number=str(item.get("displayName") or item.get("fileName") or "unknown"),
                    published_at=_parse_iso_datetime(published_raw),
                    download_url=download_url,
                    filename=str(item.get("fileName") or f"{project_id}-{item.get('id')}.jar"),
                    game_versions=[str(v) for v in item.get("gameVersions", [])],
                    loaders=[loader] if loader else [],
                    changelog="",
                )
            )

        versions.sort(key=lambda version: version.published_at, reverse=True)
        return versions


def _select_primary_file(files: Iterable[dict]) -> dict | None:
    files = list(files)
    if not files:
        return None

    for item in files:
        if item.get("primary"):
            return item

    return files[0]


def _slug_candidates(local_mod: LocalMod) -> list[str]:
    raw = [local_mod.mod_id, local_mod.name]
    candidates: list[str] = []
    for value in raw:
        slug = re.sub(r"[^a-z0-9-]", "-", value.lower())
        slug = re.sub(r"-+", "-", slug).strip("-")
        if slug and slug not in candidates:
            candidates.append(slug)
    return candidates


def pick_best_update(local_mod: LocalMod, updates: list[UpdateInfo]) -> UpdateInfo:
    if not updates:
        return UpdateInfo(local_mod=local_mod, status="not_found", message="No active provider.")

    update_candidates = [item for item in updates if item.status == "update_available" and item.latest]
    if update_candidates:
        # Prefer the newest candidate when both providers match.
        update_candidates.sort(
            key=lambda item: item.latest.published_at if item.latest else datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        selected = update_candidates[0]
        _attach_matching_diagnostics(selected, updates)
        return selected

    up_to_date = [item for item in updates if item.status == "up_to_date"]
    if up_to_date:
        selected = up_to_date[0]
        _attach_matching_diagnostics(selected, updates)
        return selected

    errors = [item for item in updates if item.status == "error"]
    if errors:
        combined_message = " | ".join(item.message for item in errors)
        result = UpdateInfo(
            local_mod=local_mod,
            status="error",
            message=combined_message,
            provider=", ".join(sorted({item.provider for item in errors if item.provider})),
        )
        _attach_matching_diagnostics(result, updates)
        return result

    selected = updates[0]
    _attach_matching_diagnostics(selected, updates)
    return selected


def _attach_matching_diagnostics(selected: UpdateInfo, updates: list[UpdateInfo]) -> None:
    combined_candidates: list[MatchCandidate] = []
    for item in updates:
        combined_candidates.extend(item.match_candidates)

    combined_candidates.sort(
        key=lambda candidate: (1 if candidate.accepted else 0, candidate.score, candidate.confidence),
        reverse=True,
    )

    if combined_candidates:
        selected.match_candidates = combined_candidates[:20]

    preferred = next(
        (
            candidate
            for candidate in combined_candidates
            if candidate.accepted and (not selected.provider or candidate.provider == selected.provider)
        ),
        None,
    )
    if preferred is None and combined_candidates:
        preferred = combined_candidates[0]

    if preferred and selected.match_score <= 0:
        selected.match_score = preferred.score
    if preferred and selected.match_confidence <= 0:
        selected.match_confidence = preferred.confidence
    if preferred and not selected.matched_project_id:
        selected.matched_project_id = preferred.project_id

    if not selected.match_note:
        notes = [f"{item.provider}: {item.match_note}" for item in updates if item.match_note]
        selected.match_note = " | ".join(notes)


def resolve_updates_for_mod(
    local_mod: LocalMod,
    settings: AppSettings,
    modrinth: ModrinthProvider,
    curseforge: CurseForgeProvider | None,
) -> UpdateInfo:
    loader = local_mod.loader_hint if settings.loader == "auto" and local_mod.loader_hint != "unknown" else settings.loader
    minecraft_version = settings.minecraft_version

    provider_results: list[UpdateInfo] = []

    if settings.use_modrinth:
        provider_results.append(
            modrinth.resolve_update(
                local_mod=local_mod,
                minecraft_version=minecraft_version,
                loader=loader,
                project_map=settings.modrinth_project_map,
                strict_matching=settings.strict_matching,
            )
        )

    if settings.use_curseforge and curseforge is not None:
        provider_results.append(
            curseforge.resolve_update(
                local_mod=local_mod,
                minecraft_version=minecraft_version,
                loader=loader,
                project_map=settings.curseforge_project_map,
                strict_matching=settings.strict_matching,
            )
        )

    return pick_best_update(local_mod, provider_results)
