from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

import yaml

from .config import ServiceConfig
from .store import atomic_write_json


PENDING_VALUE = "待填写"
MAX_INSTITUTION_NAME_LENGTH = 500
MAX_CHINESE_NAME_LENGTH = 300
MAX_INSTITUTIONS_PER_WORK = 500
MAX_CACHE_DOIS = 5000
MAX_CACHE_TRANSLATIONS = 10000
MAX_CACHE_FILE_BYTES = 10_000_000
MAX_OVERRIDES_FILE_BYTES = 1_000_000
MAX_RESPONSE_BYTES = 1_000_000
MAX_LITERATURE_CARD_BYTES = 1_000_000
MAX_LITERATURE_CARDS = 20_000
EXTERNAL_API_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "zotero-excalidraw-sync/0.1.3 (local desktop integration)",
}
OPENALEX_INSTITUTION_ID_PATTERN = re.compile(r"^I\d+$")
WIKIDATA_ID_PATTERN = re.compile(r"^Q\d+$")
HAN_CHARACTER_PATTERN = re.compile(r"[\u3400-\u9fff]")

TRANSLATION_PROMPT = """你是学术机构名称规范化与中文翻译器。

输入是机构记录数组，每项包含 id、ror、name、country。
请为每个英文机构名称返回简体中文名称。

规则：
1. 优先使用广泛认可的官方或通行中文名称。
2. 保留大学、研究所、科学院、实验室、医院等组织层级。
3. 不添加输入中不存在的院系、城市或国家。
4. 不翻译人名、缩写和专有项目名称，除非存在明确通行译名。
5. 不确定时将 chinese_name 返回 null，禁止猜测。
6. 只返回严格 JSON，不要解释，不要 Markdown。

输出格式：
[
  {
    "id": "原始 id",
    "chinese_name": "中文名称或 null"
  }
]"""

HttpJson = Callable[[str, str, dict[str, str], bytes | None, float], Any]


def _compact_text(value: Any, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def _normalized_name(value: str) -> str:
    return re.sub(r"[^\w]+", " ", value, flags=re.UNICODE).strip().casefold()


def _doi_key(value: str) -> str:
    doi = re.sub(r"\s+", "", value).strip()
    doi = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", doi, flags=re.IGNORECASE)
    return doi.casefold() if re.fullmatch(r"10\.\d{4,9}/\S+", doi, flags=re.IGNORECASE) else ""


def _valid_chinese_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    name = _compact_text(value, MAX_CHINESE_NAME_LENGTH + 1)
    if not name or len(name) > MAX_CHINESE_NAME_LENGTH or not HAN_CHARACTER_PATTERN.search(name):
        return None
    return name


def _error_type(error: Exception) -> str:
    return f"HTTPError{error.code}" if isinstance(error, HTTPError) else type(error).__name__


def _default_http_json(
    method: str,
    url: str,
    headers: dict[str, str],
    data: bytes | None,
    timeout: float,
) -> Any:
    request = Request(url, data=data, headers=headers, method=method)
    with urlopen(request, timeout=timeout) as response:
        payload = response.read(MAX_RESPONSE_BYTES + 1)
    if len(payload) > MAX_RESPONSE_BYTES:
        raise ValueError("response_too_large")
    return json.loads(payload.decode("utf-8"))


class InstitutionCache:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.RLock()
        self.data = self._load()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"schemaVersion": 1, "dois": {}, "translations": {}}
        try:
            if self.path.stat().st_size > MAX_CACHE_FILE_BYTES:
                raise ValueError("cache_too_large")
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
            return {"schemaVersion": 1, "dois": {}, "translations": {}}
        if not isinstance(value, dict):
            return {"schemaVersion": 1, "dois": {}, "translations": {}}
        dois = value.get("dois")
        translations = value.get("translations")
        result = {
            "schemaVersion": 1,
            "dois": dois if isinstance(dois, dict) else {},
            "translations": translations if isinstance(translations, dict) else {},
        }
        while len(result["dois"]) > MAX_CACHE_DOIS:
            result["dois"].pop(next(iter(result["dois"])))
        while len(result["translations"]) > MAX_CACHE_TRANSLATIONS:
            result["translations"].pop(next(iter(result["translations"])))
        return result

    def doi(self, doi: str) -> list[dict[str, str]] | None:
        with self.lock:
            value = self.data["dois"].get(doi)
            if not isinstance(value, list):
                return None
            return [record for item in value if (record := _institution_record(item)) is not None]

    def set_doi(self, doi: str, institutions: list[dict[str, str]]) -> None:
        with self.lock:
            self.data["dois"][doi] = institutions[:MAX_INSTITUTIONS_PER_WORK]
            self._trim("dois", MAX_CACHE_DOIS)

    def translation(self, key: str) -> dict[str, str] | None:
        with self.lock:
            value = self.data["translations"].get(key)
            if not isinstance(value, dict):
                return None
            chinese_name = value.get("chineseName")
            source = _compact_text(value.get("source"), 30)
            if chinese_name is None and source == "pending":
                return {"chineseName": "", "source": source}
            valid_name = _valid_chinese_name(chinese_name)
            return {"chineseName": valid_name, "source": source} if valid_name and source else None

    def set_translation(self, key: str, chinese_name: str | None, source: str) -> None:
        with self.lock:
            self.data["translations"][key] = {
                "chineseName": chinese_name,
                "source": source,
            }
            self._trim("translations", MAX_CACHE_TRANSLATIONS)

    def save(self) -> None:
        with self.lock:
            atomic_write_json(self.path, self.data)

    def _trim(self, field: str, maximum: int) -> None:
        values = self.data[field]
        while len(values) > maximum:
            values.pop(next(iter(values)))


def _institution_record(value: Any) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    name = _compact_text(value.get("name") or value.get("display_name"), MAX_INSTITUTION_NAME_LENGTH + 1)
    if not name or len(name) > MAX_INSTITUTION_NAME_LENGTH:
        return None
    return {
        "id": _compact_text(value.get("id"), 200),
        "ror": _compact_text(value.get("ror"), 200),
        "name": name,
        "country": _compact_text(value.get("country") or value.get("country_code"), 20),
    }


class InstitutionMetadataService:
    def __init__(
        self,
        config: ServiceConfig,
        *,
        http_json: HttpJson = _default_http_json,
        logger: logging.Logger | None = None,
    ) -> None:
        self.enabled = config.institution_metadata_enabled
        self.source = config.institution_source
        self.timeout = config.institution_request_timeout_seconds
        self.openalex_api_key_env = config.open_alex_api_key_env
        self.translation_mode = config.institution_translation_mode
        self.translation_base_url = config.institution_translation_base_url.rstrip("/")
        self.translation_api_key_env = config.institution_translation_api_key_env
        self.translation_model = config.institution_translation_model
        self.cache = InstitutionCache(config.institution_cache_file)
        self.http_json = http_json
        self.logger = logger or logging.getLogger("zotero-excalidraw-sync")
        self.overrides_path = config.institution_overrides_file
        self.overrides_lock = threading.RLock()
        self.overrides = self._load_overrides(self.overrides_path)
        literature_parts = config.literature_folder.replace("\\", "/").strip("/").split("/")
        self.literature_root = config.vault_path.joinpath(*literature_parts)

    def resolve(self, doi_value: str) -> list[str]:
        doi = _doi_key(doi_value)
        if not self.enabled or not doi:
            return [PENDING_VALUE]

        institutions = self.cache.doi(doi)
        if institutions is None:
            try:
                institutions = self._openalex_institutions(doi)
                self.cache.set_doi(doi, institutions)
                self.logger.info("institution doi=%s source=openalex cache=miss", doi)
            except Exception as error:  # External metadata must never block card creation.
                institutions = []
                self.cache.set_doi(doi, institutions)
                self.logger.warning(
                    "institution doi=%s source=openalex error=%s",
                    doi,
                    _error_type(error),
                )
        else:
            self.logger.info("institution doi=%s source=openalex cache=hit", doi)

        institutions = institutions[:1]
        self.cache.set_doi(doi, institutions)

        try:
            resolved = self._resolve_names(institutions)
        except Exception as error:
            resolved = []
            self.logger.warning(
                "institution doi=%s source=resolution error=%s",
                doi,
                _error_type(error),
            )
        finally:
            try:
                self.cache.save()
            except OSError as error:
                self.logger.warning("institution doi=%s source=cache error=%s", doi, _error_type(error))
        return resolved or [PENDING_VALUE]

    def _openalex_institutions(self, doi: str) -> list[dict[str, str]]:
        if self.source != "openalex":
            raise ValueError("unsupported_institution_source")
        query: dict[str, str] = {"select": "authorships"}
        api_key = os.environ.get(self.openalex_api_key_env, "") if self.openalex_api_key_env else ""
        if api_key:
            query["api_key"] = api_key
        url = f"https://api.openalex.org/works/https://doi.org/{quote(doi, safe='/():')}"
        url += "?" + urlencode(query)
        value = self.http_json("GET", url, EXTERNAL_API_HEADERS, None, self.timeout)
        authorships = value.get("authorships") if isinstance(value, dict) else None
        if not isinstance(authorships, list):
            raise ValueError("invalid_openalex_work")

        for authorship in authorships:
            embedded = authorship.get("institutions") if isinstance(authorship, dict) else None
            if not isinstance(embedded, list):
                continue
            for value in embedded:
                institution = _institution_record(value)
                if institution is None:
                    continue
                return [institution]
        return []

    def _resolve_names(self, institutions: list[dict[str, str]]) -> list[str]:
        results: list[str | None] = [None] * len(institutions)
        unresolved: list[tuple[int, dict[str, str], str]] = []

        for index, institution in enumerate(institutions):
            manual = self._manual_name(institution)
            identifier = self._log_identifier(institution)
            if manual:
                results[index] = self._display(institution["name"], manual)
                self.logger.info("institution id=%s source=manual cache=hit", identifier)
                continue
            cache_key = self._cache_key(institution)
            cached = self.cache.translation(cache_key)
            if cached is not None:
                chinese_name = cached["chineseName"] or None
                if chinese_name:
                    results[index] = self._display(institution["name"], chinese_name)
                    self.logger.info("institution id=%s source=%s cache=hit", identifier, cached["source"])
                else:
                    results[index] = self._vault_fallback(institution, cache_key)
                continue
            unresolved.append((index, institution, cache_key))

        if self.translation_mode != "manual_only":
            remaining: list[tuple[int, dict[str, str], str]] = []
            for index, institution, cache_key in unresolved:
                chinese_name = self._wikidata_name(institution)
                if chinese_name:
                    results[index] = self._display(institution["name"], chinese_name)
                    self.cache.set_translation(cache_key, chinese_name, "wikidata")
                else:
                    remaining.append((index, institution, cache_key))
            unresolved = remaining

        if self.translation_mode == "wikidata_then_openai" and unresolved:
            translations = self._openai_names([institution for _, institution, _ in unresolved])
            for index, institution, cache_key in unresolved:
                translated = translations.get(self._translation_id(institution))
                if translated:
                    results[index] = self._display(institution["name"], translated)
                    self.cache.set_translation(cache_key, translated, "openai")
                else:
                    results[index] = self._vault_fallback(institution, cache_key)
        else:
            for index, institution, cache_key in unresolved:
                results[index] = self._vault_fallback(institution, cache_key)

        return [value for value in results if value]

    def _wikidata_name(self, institution: dict[str, str]) -> str | None:
        identifier = self._log_identifier(institution)
        openalex_id = institution["id"].rstrip("/").rsplit("/", 1)[-1]
        if not OPENALEX_INSTITUTION_ID_PATTERN.fullmatch(openalex_id):
            return None
        query: dict[str, str] = {"select": "ids"}
        api_key = os.environ.get(self.openalex_api_key_env, "") if self.openalex_api_key_env else ""
        if api_key:
            query["api_key"] = api_key
        url = f"https://api.openalex.org/institutions/{openalex_id}"
        url += "?" + urlencode(query)
        try:
            record = self.http_json("GET", url, EXTERNAL_API_HEADERS, None, self.timeout)
            ids = record.get("ids") if isinstance(record, dict) else None
            wikidata_url = ids.get("wikidata") if isinstance(ids, dict) else ""
            wikidata_id = _compact_text(wikidata_url, 200).rstrip("/").rsplit("/", 1)[-1]
            if not WIKIDATA_ID_PATTERN.fullmatch(wikidata_id):
                return None
            entity_url = f"https://www.wikidata.org/wiki/Special:EntityData/{wikidata_id}.json"
            entity_data = self.http_json("GET", entity_url, EXTERNAL_API_HEADERS, None, self.timeout)
            entities = entity_data.get("entities") if isinstance(entity_data, dict) else None
            entity = entities.get(wikidata_id) if isinstance(entities, dict) else None
            labels = entity.get("labels") if isinstance(entity, dict) else None
            for language in ("zh-cn", "zh-hans", "zh"):
                label = labels.get(language) if isinstance(labels, dict) else None
                chinese_name = _valid_chinese_name(label.get("value") if isinstance(label, dict) else None)
                if chinese_name:
                    self.logger.info("institution id=%s source=wikidata cache=miss", identifier)
                    return chinese_name
        except Exception as error:
            self.logger.warning(
                "institution id=%s source=wikidata error=%s",
                identifier,
                _error_type(error),
            )
        return None

    def _openai_names(self, institutions: list[dict[str, str]]) -> dict[str, str]:
        if not self.translation_base_url or not self.translation_model or not self.translation_api_key_env:
            return {}
        api_key = os.environ.get(self.translation_api_key_env, "")
        if not api_key:
            return {}
        records = [
            {
                "id": self._translation_id(institution),
                "ror": institution["ror"] or None,
                "name": institution["name"],
                "country": institution["country"] or None,
            }
            for institution in institutions
        ]
        body = json.dumps(
            {
                "model": self.translation_model,
                "temperature": 0,
                "messages": [
                    {"role": "system", "content": TRANSLATION_PROMPT},
                    {"role": "user", "content": json.dumps(records, ensure_ascii=False)},
                ],
            },
            ensure_ascii=False,
        ).encode("utf-8")
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        try:
            response = self.http_json(
                "POST",
                self.translation_base_url + "/chat/completions",
                headers,
                body,
                self.timeout,
            )
            parsed = self._translation_response(response, {record["id"] for record in records})
            for identifier in parsed:
                self.logger.info("institution id=%s source=openai cache=miss", identifier)
            return parsed
        except Exception as error:
            self.logger.warning("institution source=openai error=%s", _error_type(error))
            return {}

    @staticmethod
    def _translation_response(response: Any, expected_ids: set[str]) -> dict[str, str]:
        if not isinstance(response, dict):
            raise ValueError("invalid_translation_response")
        choices = response.get("choices")
        if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
            raise ValueError("invalid_translation_response")
        message = choices[0].get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or len(content) > MAX_RESPONSE_BYTES:
            raise ValueError("invalid_translation_content")
        values = json.loads(content)
        if not isinstance(values, list) or len(values) > len(expected_ids):
            raise ValueError("invalid_translation_json")
        result: dict[str, str] = {}
        returned_ids: set[str] = set()
        for value in values:
            if not isinstance(value, dict) or set(value) != {"id", "chinese_name"}:
                raise ValueError("invalid_translation_item")
            identifier = value["id"]
            if not isinstance(identifier, str) or identifier not in expected_ids or identifier in returned_ids:
                raise ValueError("invalid_translation_id")
            returned_ids.add(identifier)
            chinese_name = value["chinese_name"]
            if chinese_name is not None:
                valid_name = _valid_chinese_name(chinese_name)
                if valid_name is None:
                    raise ValueError("invalid_chinese_name")
                result[identifier] = valid_name
        if returned_ids != expected_ids:
            raise ValueError("missing_translation_id")
        return result

    def _load_overrides(self, path: Path) -> dict[str, str]:
        if not path.exists():
            return {}
        try:
            if path.stat().st_size > MAX_OVERRIDES_FILE_BYTES:
                raise ValueError("overrides_too_large")
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict) or len(value) > MAX_CACHE_TRANSLATIONS:
                raise ValueError("invalid_overrides")
            result: dict[str, str] = {}
            for key, name in value.items():
                override_key = _compact_text(key, 1001)
                chinese_name = _valid_chinese_name(name)
                if override_key and len(override_key) <= 1000 and chinese_name:
                    result[override_key] = chinese_name
                    result[override_key.casefold()] = chinese_name
                    result[_normalized_name(override_key)] = chinese_name
            return result
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
            self.logger.warning("institution source=manual error=%s", _error_type(error))
            return {}

    def _manual_name(self, institution: dict[str, str]) -> str | None:
        for value in (institution["ror"], institution["id"], institution["name"]):
            if not value:
                continue
            for key in (value, value.casefold(), _normalized_name(value)):
                if key in self.overrides:
                    return self.overrides[key]
        return None

    def _vault_fallback(self, institution: dict[str, str], cache_key: str) -> str:
        identifier = self._log_identifier(institution)
        chinese_name = self._vault_chinese_name(institution["name"], identifier)
        if chinese_name:
            chinese_name = self._remember_override(institution["name"], chinese_name, identifier)
            self.cache.set_translation(cache_key, chinese_name, "literature_card")
            self.logger.info("institution id=%s source=literature_card cache=miss", identifier)
            return self._display(institution["name"], chinese_name)
        self.cache.set_translation(cache_key, None, "pending")
        return self._display(institution["name"], None)

    def _vault_chinese_name(self, english_name: str, identifier: str) -> str | None:
        if not self.literature_root.exists():
            return None
        normalized_target = _normalized_name(english_name)
        candidates: set[str] = set()
        paths = sorted(self.literature_root.rglob("*.md"), key=lambda path: path.as_posix().casefold())
        for path in paths[:MAX_LITERATURE_CARDS]:
            try:
                if path.stat().st_size > MAX_LITERATURE_CARD_BYTES:
                    continue
                text = path.read_text(encoding="utf-8-sig")
                match = re.match(r"^---[ \t]*\r?\n(.*?)^---[ \t]*(?:\r?\n|$)", text, re.DOTALL | re.MULTILINE)
                if not match:
                    continue
                frontmatter = yaml.safe_load(match.group(1))
            except (OSError, UnicodeError, yaml.YAMLError):
                continue
            if not isinstance(frontmatter, dict) or frontmatter.get("type") != "literature":
                continue
            institutions = frontmatter.get("institutions")
            if not isinstance(institutions, list):
                continue
            for value in institutions:
                if not isinstance(value, str):
                    continue
                display = _compact_text(value, MAX_INSTITUTION_NAME_LENGTH + MAX_CHINESE_NAME_LENGTH + 2)
                parsed = re.fullmatch(r"(.+)（(.+)）", display)
                if not parsed or _normalized_name(parsed.group(1)) != normalized_target:
                    continue
                chinese_name = _valid_chinese_name(parsed.group(2))
                if chinese_name and chinese_name != PENDING_VALUE:
                    candidates.add(chinese_name)
        if len(candidates) == 1:
            return next(iter(candidates))
        if len(candidates) > 1:
            self.logger.warning(
                "institution id=%s source=literature_card error=ConflictingTranslations count=%s",
                identifier,
                len(candidates),
            )
        return None

    def _remember_override(self, english_name: str, chinese_name: str, identifier: str) -> str:
        with self.overrides_lock:
            raw: dict[str, Any] = {}
            if self.overrides_path.exists():
                try:
                    if self.overrides_path.stat().st_size > MAX_OVERRIDES_FILE_BYTES:
                        raise ValueError("overrides_too_large")
                    value = json.loads(self.overrides_path.read_text(encoding="utf-8"))
                    if not isinstance(value, dict) or len(value) >= MAX_CACHE_TRANSLATIONS:
                        raise ValueError("invalid_overrides")
                    raw = value
                except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
                    self.logger.warning(
                        "institution id=%s source=manual error=%s",
                        identifier,
                        _error_type(error),
                    )
                    return chinese_name
            existing = _valid_chinese_name(raw.get(english_name))
            if existing:
                chinese_name = existing
            else:
                raw[english_name] = chinese_name
                try:
                    atomic_write_json(self.overrides_path, raw)
                except OSError as error:
                    self.logger.warning(
                        "institution id=%s source=manual error=%s",
                        identifier,
                        _error_type(error),
                    )
            self._index_override(english_name, chinese_name)
            return chinese_name

    def _index_override(self, key: str, chinese_name: str) -> None:
        self.overrides[key] = chinese_name
        self.overrides[key.casefold()] = chinese_name
        self.overrides[_normalized_name(key)] = chinese_name

    @staticmethod
    def _cache_key(institution: dict[str, str]) -> str:
        if institution["ror"]:
            return "ror:" + institution["ror"].casefold().rstrip("/")
        if institution["id"]:
            return "openalex:" + institution["id"].casefold().rstrip("/")
        return "name:" + _normalized_name(institution["name"])

    @staticmethod
    def _translation_id(institution: dict[str, str]) -> str:
        if institution["id"]:
            return institution["id"]
        if institution["ror"]:
            return institution["ror"]
        digest = hashlib.sha256(_normalized_name(institution["name"]).encode("utf-8")).hexdigest()[:24]
        return f"name:{digest}"

    @staticmethod
    def _log_identifier(institution: dict[str, str]) -> str:
        return InstitutionMetadataService._translation_id(institution)

    @staticmethod
    def _display(english_name: str, chinese_name: str | None) -> str:
        return f"{english_name}（{chinese_name or PENDING_VALUE}）"
