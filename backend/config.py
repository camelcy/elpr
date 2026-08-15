from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ServiceConfig:
    zotero_api_url: str = "http://127.0.0.1:23119/api/users/0"
    listen_host: str = "127.0.0.1"
    listen_port: int = 27119
    poll_interval_seconds: int = 10
    fetch_limit: int = 100
    bootstrap_limit: int = 100
    vault_path: Path = Path(r"D:\Obsidian\Steins Gate")
    images_path: str = "Excalidraw/Images"
    literature_folder: str = "20 - 工作学习/文献/Literature"
    data_path: Path = Path(r"D:\elpr\data")
    mapping_file: Path = Path(r"D:\elpr\data\paper_canvas_map.json")
    state_file: Path = Path(r"D:\elpr\data\sync_state.json")
    log_file: Path = Path(r"D:\elpr\data\service.log")
    image_scale: float = 4.0
    deletion_check_interval_seconds: int = 300
    annotation_allowlist: tuple[str, ...] = field(default_factory=tuple)
    institution_metadata_enabled: bool = False
    institution_source: str = "openalex"
    open_alex_api_key_env: str = "OPENALEX_API_KEY"
    institution_request_timeout_seconds: float = 5.0
    institution_cache_file: Path = Path(r"D:\elpr\data\institution_cache.json")
    institution_overrides_file: Path = Path(r"D:\elpr\config\institution_translations.json")
    institution_translation_mode: str = "wikidata_then_openai"
    institution_translation_base_url: str = ""
    institution_translation_api_key_env: str = "INSTITUTION_TRANSLATION_API_KEY"
    institution_translation_model: str = ""

    @classmethod
    def load(cls, path: Path) -> "ServiceConfig":
        raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        defaults = cls()
        institution_source = str(raw.get("institutionSource", defaults.institution_source)).strip().lower()
        if institution_source != "openalex":
            raise ValueError("institutionSource must be openalex")
        translation_mode = str(
            raw.get("institutionTranslationMode", defaults.institution_translation_mode)
        ).strip().lower()
        if translation_mode not in {"wikidata_only", "wikidata_then_openai", "manual_only"}:
            raise ValueError(
                "institutionTranslationMode must be wikidata_only, wikidata_then_openai, or manual_only"
            )
        return cls(
            zotero_api_url=str(raw.get("zoteroApiUrl", defaults.zotero_api_url)).rstrip("/"),
            listen_host=str(raw.get("listenHost", defaults.listen_host)),
            listen_port=int(raw.get("listenPort", defaults.listen_port)),
            poll_interval_seconds=max(2, int(raw.get("pollIntervalSeconds", defaults.poll_interval_seconds))),
            fetch_limit=max(1, min(100, int(raw.get("fetchLimit", defaults.fetch_limit)))),
            bootstrap_limit=max(1, min(100, int(raw.get("bootstrapLimit", defaults.bootstrap_limit)))),
            vault_path=Path(raw.get("vaultPath", defaults.vault_path)),
            images_path=str(raw.get("imagesPath", defaults.images_path)).strip("/\\"),
            literature_folder=str(raw.get("literatureFolder", defaults.literature_folder)).strip("/\\"),
            data_path=Path(raw.get("dataPath", defaults.data_path)),
            mapping_file=Path(raw.get("mappingFile", defaults.mapping_file)),
            state_file=Path(raw.get("stateFile", defaults.state_file)),
            log_file=Path(raw.get("logFile", defaults.log_file)),
            image_scale=float(raw.get("imageScale", defaults.image_scale)),
            deletion_check_interval_seconds=max(
                30,
                int(raw.get("deletionCheckIntervalSeconds", defaults.deletion_check_interval_seconds)),
            ),
            annotation_allowlist=tuple(str(k) for k in raw.get("annotationAllowlist", [])),
            institution_metadata_enabled=bool(
                raw.get("institutionMetadataEnabled", defaults.institution_metadata_enabled)
            ),
            institution_source=institution_source,
            open_alex_api_key_env=str(raw.get("openAlexApiKeyEnv", defaults.open_alex_api_key_env)).strip(),
            institution_request_timeout_seconds=max(
                1.0,
                min(
                    15.0,
                    float(
                        raw.get(
                            "institutionRequestTimeoutSeconds",
                            defaults.institution_request_timeout_seconds,
                        )
                    ),
                ),
            ),
            institution_cache_file=Path(
                raw.get("institutionCacheFile", defaults.institution_cache_file)
            ),
            institution_overrides_file=Path(
                raw.get("institutionOverridesFile", defaults.institution_overrides_file)
            ),
            institution_translation_mode=translation_mode,
            institution_translation_base_url=str(
                raw.get("institutionTranslationBaseUrl", defaults.institution_translation_base_url)
            ).strip(),
            institution_translation_api_key_env=str(
                raw.get(
                    "institutionTranslationApiKeyEnv",
                    defaults.institution_translation_api_key_env,
                )
            ).strip(),
            institution_translation_model=str(
                raw.get("institutionTranslationModel", defaults.institution_translation_model)
            ).strip(),
        )
