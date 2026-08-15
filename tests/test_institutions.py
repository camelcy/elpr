from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

import yaml

from backend.config import ServiceConfig
from backend.institutions import InstitutionMetadataService, TRANSLATION_PROMPT
from backend.literature import LiteratureCardStore
from backend.store import MappingStore


FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures"
LITERATURE_FOLDER = "20 - 工作学习/文献/Literature"
DOI = "10.1234/fixture.2026.001"


def fixture(name: str) -> dict:
    return json.loads((FIXTURE_ROOT / name).read_text(encoding="utf-8"))


def work_with(*indexes: int) -> dict:
    source = fixture("openalex_work_institutions.fixture.json")
    institutions = source["authorships"][0]["institutions"]
    return {"authorships": [{"institutions": [institutions[index] for index in indexes]}]}


class FixtureHttp:
    def __init__(self, work: dict, *, translation: dict | None = None) -> None:
        self.work = work
        self.translation = translation
        self.calls: list[tuple[str, str, dict[str, str], bytes | None, float]] = []

    def __call__(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        data: bytes | None,
        timeout: float,
    ) -> dict:
        self.calls.append((method, url, headers, data, timeout))
        if "/works/" in url:
            return self.work
        if "/institutions/I100000001" in url:
            return fixture("openalex_institution.fixture.json")
        if "/institutions/I100000002" in url:
            return {"ids": {}}
        if "Q100000001.json" in url:
            return fixture("wikidata_institution.fixture.json")
        if url.endswith("/chat/completions") and self.translation is not None:
            return self.translation
        raise AssertionError(f"unexpected mocked request: {method} {url}")


class InstitutionMetadataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def config(self, **changes: object) -> ServiceConfig:
        values: dict[str, object] = {
            "institution_metadata_enabled": True,
            "institution_cache_file": self.root / "institution_cache.json",
            "institution_overrides_file": self.root / "institution_translations.json",
            "institution_translation_mode": "wikidata_only",
            "institution_request_timeout_seconds": 3.0,
            "vault_path": self.root / "vault",
            "literature_folder": LITERATURE_FOLDER,
        }
        values.update(changes)
        return ServiceConfig(**values)

    def service(self, http: FixtureHttp, **changes: object) -> InstitutionMetadataService:
        return InstitutionMetadataService(self.config(**changes), http_json=http)

    def literature_card(self, name: str, institutions: list[str]) -> Path:
        root = self.root / "vault" / Path(LITERATURE_FOLDER)
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"{name}.md"
        values = "\n".join(f"  - {json.dumps(value, ensure_ascii=False)}" for value in institutions)
        path.write_text(
            f"---\ntype: literature\ninstitutions:\n{values}\n---\n\n待填写\n",
            encoding="utf-8",
        )
        return path

    def test_openalex_keeps_only_the_first_institution(self) -> None:
        http = FixtureHttp(fixture("openalex_work_institutions.fixture.json"))
        service = self.service(http, institution_translation_mode="manual_only")

        result = service.resolve(DOI)

        self.assertEqual(
            result,
            [
                "Institute for Quantum Optics and Quantum Information, Austrian Academy of Sciences（待填写）",
            ],
        )
        self.assertEqual(sum("/works/" in call[1] for call in http.calls), 1)
        self.assertTrue(all(call[4] == 3.0 for call in http.calls))
        self.assertTrue(
            all(call[2].get("User-Agent", "").startswith("zotero-excalidraw-sync/") for call in http.calls)
        )

    def test_pending_translation_is_learned_from_existing_card_and_added_to_overrides(self) -> None:
        english_name = "Aix-Marseille University"
        chinese_name = "艾克斯-马赛大学"
        self.literature_card("Existing", [f"{english_name}（{chinese_name}）"])
        http = FixtureHttp(work_with(1))
        service = self.service(http, institution_translation_mode="manual_only")

        result = service.resolve(DOI)

        self.assertEqual(result, [f"{english_name}（{chinese_name}）"])
        overrides = json.loads(self.config().institution_overrides_file.read_text(encoding="utf-8"))
        self.assertEqual(overrides, {english_name: chinese_name})

    def test_no_existing_translation_stays_pending_without_creating_mapping(self) -> None:
        http = FixtureHttp(work_with(1))
        service = self.service(http, institution_translation_mode="manual_only")

        result = service.resolve(DOI)

        self.assertEqual(result, ["Aix-Marseille University（待填写）"])
        self.assertFalse(self.config().institution_overrides_file.exists())

    def test_conflicting_existing_translations_are_not_learned(self) -> None:
        english_name = "Aix-Marseille University"
        self.literature_card("First", [f"{english_name}（译名甲大学）"])
        self.literature_card("Second", [f"{english_name}（译名乙大学）"])
        http = FixtureHttp(work_with(1))
        service = self.service(http, institution_translation_mode="manual_only")

        result = service.resolve(DOI)

        self.assertEqual(result, [f"{english_name}（待填写）"])
        self.assertFalse(self.config().institution_overrides_file.exists())

    def test_cached_pending_translation_can_later_be_learned_from_existing_card(self) -> None:
        english_name = "Aix-Marseille University"
        chinese_name = "艾克斯-马赛大学"
        http = FixtureHttp(work_with(1))
        service = self.service(http, institution_translation_mode="manual_only")
        self.assertEqual(service.resolve(DOI), [f"{english_name}（待填写）"])
        self.literature_card("Added Later", [f"{english_name}（{chinese_name}）"])

        result = service.resolve(DOI)

        self.assertEqual(result, [f"{english_name}（{chinese_name}）"])
        self.assertEqual(sum("/works/" in call[1] for call in http.calls), 1)

    def test_manual_override_precedes_wikidata_and_translation_service(self) -> None:
        self.config().institution_overrides_file.write_text(
            json.dumps({"https://ror.org/00fixture1": "人工维护的量子研究所"}, ensure_ascii=False),
            encoding="utf-8",
        )
        http = FixtureHttp(work_with(0))
        service = self.service(
            http,
            institution_translation_mode="wikidata_then_openai",
            institution_translation_base_url="https://translator.example/v1",
            institution_translation_model="fixture-model",
        )

        result = service.resolve(DOI)

        self.assertEqual(
            result,
            [
                "Institute for Quantum Optics and Quantum Information, Austrian Academy of Sciences"
                "（人工维护的量子研究所）"
            ],
        )
        self.assertEqual(len(http.calls), 1)

    def test_wikidata_chinese_label_is_used(self) -> None:
        http = FixtureHttp(work_with(0))

        result = self.service(http).resolve(DOI)

        self.assertEqual(
            result,
            [
                "Institute for Quantum Optics and Quantum Information, Austrian Academy of Sciences"
                "（奥地利科学院量子光学与量子信息研究所）"
            ],
        )
        self.assertTrue(any("Q100000001.json" in call[1] for call in http.calls))

    def test_openai_compatible_translation_accepts_strict_json(self) -> None:
        http = FixtureHttp(
            work_with(1),
            translation=fixture("openai_institution_translation.fixture.json"),
        )
        service = self.service(
            http,
            institution_translation_mode="wikidata_then_openai",
            institution_translation_base_url="https://translator.example/v1",
            institution_translation_model="fixture-model",
        )

        with patch.dict(os.environ, {"INSTITUTION_TRANSLATION_API_KEY": "test-secret"}, clear=False):
            result = service.resolve(DOI)

        self.assertEqual(result, ["Aix-Marseille University（艾克斯-马赛大学）"])
        translation_call = next(call for call in http.calls if call[1].endswith("/chat/completions"))
        request_body = json.loads((translation_call[3] or b"").decode("utf-8"))
        self.assertEqual(request_body["messages"][0]["content"], TRANSLATION_PROMPT)
        self.assertNotIn("test-secret", json.dumps(request_body))
        self.assertEqual(translation_call[2]["Authorization"], "Bearer test-secret")

    def test_invalid_translation_json_falls_back_to_pending(self) -> None:
        http = FixtureHttp(
            work_with(1),
            translation={"choices": [{"message": {"content": "```json\n[]\n```"}}]},
        )
        service = self.service(
            http,
            institution_translation_mode="wikidata_then_openai",
            institution_translation_base_url="https://translator.example/v1",
            institution_translation_model="fixture-model",
        )

        with patch.dict(os.environ, {"INSTITUTION_TRANSLATION_API_KEY": "test-secret"}, clear=False):
            result = service.resolve(DOI)

        self.assertEqual(result, ["Aix-Marseille University（待填写）"])

    def test_translation_configuration_is_optional(self) -> None:
        http = FixtureHttp(work_with(1))
        service = self.service(http, institution_translation_mode="wikidata_then_openai")

        result = service.resolve(DOI)

        self.assertEqual(result, ["Aix-Marseille University（待填写）"])
        self.assertFalse(any(call[0] == "POST" for call in http.calls))

    def test_doi_and_translation_results_are_cached(self) -> None:
        http = FixtureHttp(
            work_with(1),
            translation=fixture("openai_institution_translation.fixture.json"),
        )
        service = self.service(
            http,
            institution_translation_mode="wikidata_then_openai",
            institution_translation_base_url="https://translator.example/v1",
            institution_translation_model="fixture-model",
        )
        with patch.dict(os.environ, {"INSTITUTION_TRANSLATION_API_KEY": "test-secret"}, clear=False):
            first = service.resolve(DOI)
            second = service.resolve(DOI)

        self.assertEqual(second, first)
        self.assertEqual(sum("/works/" in call[1] for call in http.calls), 1)
        self.assertEqual(sum(call[0] == "POST" for call in http.calls), 1)
        cached = json.loads((self.root / "institution_cache.json").read_text(encoding="utf-8"))
        self.assertIn(DOI, cached["dois"])
        self.assertEqual(len(cached["translations"]), 1)

    def test_missing_doi_and_disabled_feature_make_no_requests(self) -> None:
        http = FixtureHttp(work_with(0))
        self.assertEqual(self.service(http).resolve(""), ["待填写"])
        disabled = self.service(http, institution_metadata_enabled=False)
        self.assertEqual(disabled.resolve(DOI), ["待填写"])
        self.assertEqual(http.calls, [])

    def test_openalex_errors_and_empty_data_do_not_block_card_creation(self) -> None:
        failures = [
            TimeoutError("fixture timeout"),
            HTTPError("https://api.openalex.org", 404, "not found", {}, None),
            HTTPError("https://api.openalex.org", 429, "rate limited", {}, None),
            None,
        ]
        for index, failure in enumerate(failures):
            with self.subTest(failure=type(failure).__name__ if failure else "empty"):
                case_root = self.root / str(index)
                mapping = MappingStore(case_root / "map.json")

                def request(
                    method: str,
                    url: str,
                    headers: dict[str, str],
                    data: bytes | None,
                    timeout: float,
                ) -> dict:
                    if failure:
                        raise failure
                    return {"authorships": []}

                config = self.config(
                    institution_cache_file=case_root / "cache.json",
                    institution_overrides_file=case_root / "overrides.json",
                )
                resolver = InstitutionMetadataService(config, http_json=request)
                vault = case_root / "vault"
                vault.mkdir(parents=True)
                store = LiteratureCardStore(vault, LITERATURE_FOLDER, mapping, resolver)
                result = store.create_or_open(
                    {
                        "parentItemKey": f"TEST{index:04d}",
                        "title": f"Failure {index}",
                        "authors": [],
                        "year": "2026",
                        "citekey": "",
                        "doi": DOI,
                    }
                )
                text = vault.joinpath(*result["cardPath"].split("/")).read_text(encoding="utf-8")
                frontmatter = yaml.safe_load(text.split("---", 2)[1])
                self.assertTrue(result["created"])
                self.assertEqual(frontmatter["institutions"], ["待填写"])


class InstitutionConfigTests(unittest.TestCase):
    def test_loads_supported_configuration_without_reading_api_keys(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(
                json.dumps(
                    {
                        "institutionMetadataEnabled": True,
                        "institutionSource": "openalex",
                        "openAlexApiKeyEnv": "FIXTURE_OPENALEX_KEY",
                        "institutionRequestTimeoutSeconds": 99,
                        "institutionCacheFile": str(Path(directory) / "cache.json"),
                        "institutionOverridesFile": str(Path(directory) / "overrides.json"),
                        "institutionTranslationMode": "manual_only",
                        "institutionTranslationBaseUrl": "https://translator.example/v1/",
                        "institutionTranslationApiKeyEnv": "FIXTURE_TRANSLATION_KEY",
                        "institutionTranslationModel": "fixture-model",
                    }
                ),
                encoding="utf-8",
            )

            config = ServiceConfig.load(path)

            self.assertTrue(config.institution_metadata_enabled)
            self.assertEqual(config.institution_translation_mode, "manual_only")
            self.assertEqual(config.institution_request_timeout_seconds, 15.0)
            self.assertEqual(config.open_alex_api_key_env, "FIXTURE_OPENALEX_KEY")
            self.assertEqual(config.institution_translation_api_key_env, "FIXTURE_TRANSLATION_KEY")

    def test_rejects_unknown_translation_mode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps({"institutionTranslationMode": "guess"}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "institutionTranslationMode"):
                ServiceConfig.load(path)


if __name__ == "__main__":
    unittest.main()
