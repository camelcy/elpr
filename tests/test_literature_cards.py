from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.request import Request, urlopen

from backend.config import ServiceConfig
from backend.literature import (
    DuplicateLiteratureCardError,
    LiteratureCardStore,
    _zotero_key_from_text,
    clean_windows_filename,
)
from backend.server import SyncHTTPServer
from backend.store import MappingStore, StateStore
from backend.sync import SyncEngine


LITERATURE_FOLDER = "20 - 工作学习/文献/Literature"


def payload(key: str = "TEST0001", title: str = "Fixture / Literature: Card?") -> dict:
    return {
        "parentItemKey": key,
        "title": title,
        "authors": ["Ada Lovelace", "Alan Turing"],
        "year": "2026",
        "citekey": "lovelaceFixtureLiterature2026",
    }


def existing_card(key: str, *, title: str = "Existing", body: str = "用户正文不得覆盖。") -> str:
    return f"""---
type: literature
title: {title}
authors: []
year:
citekey:
zotero_key: {key}
reading_stage: dr
topics:
  - "[[用户主题]]"
questions:
  - "[[用户问题]]"
one_sentence: 用户的一句话
importance: 5
confidence: 4
last_reviewed: 2026-08-01
next_review: 2026-09-01
zotero_link:
excalidraw:
date_created: 2026-08-01
date_modified: 2026-08-02
cssclasses:
  - literature-card
---

# 用户正文

{body}
"""


class LiteratureCardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.vault = Path(self.temporary.name) / "vault"
        self.vault.mkdir()
        self.mapping = MappingStore(Path(self.temporary.name) / "map.json")
        self.store = LiteratureCardStore(self.vault, LITERATURE_FOLDER, self.mapping)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def card_path(self, relative: str) -> Path:
        return self.vault.joinpath(*relative.split("/"))

    def test_creates_complete_markdown_without_inventing_user_fields(self) -> None:
        result = self.store.create_or_open(payload())
        text = self.card_path(result["cardPath"]).read_text(encoding="utf-8")

        self.assertTrue(result["created"])
        self.assertEqual(result["cardPath"], f"{LITERATURE_FOLDER}/Fixture Literature Card.md")
        self.assertIn("type: literature", text)
        self.assertIn('title: "Fixture / Literature: Card?"', text)
        self.assertIn('authors:\n  - "Ada Lovelace"\n  - "Alan Turing"', text)
        self.assertIn("year: 2026", text)
        self.assertIn('citekey: "lovelaceFixtureLiterature2026"', text)
        self.assertIn('zotero_key: "TEST0001"', text)
        self.assertIn("reading_stage: captured", text)
        self.assertIn("topics: []", text)
        self.assertIn("questions: []", text)
        self.assertIn("one_sentence:\nimportance:\nconfidence:", text)
        self.assertIn('zotero_link: "zotero://select/library/items/TEST0001"', text)
        self.assertIn("excalidraw:\n", text)
        self.assertIn("cssclasses:\n  - literature-card", text)
        self.assertIn("> 待填写", text)
        for heading in (
            "# 一句话记忆",
            "# 为什么读它",
            "# 问题—方法—发现",
            "## 研究问题",
            "## 方法与数据",
            "## 关键发现",
            "## 局限性",
            "# 与其他文献的关系",
            "## 支持",
            "## 反驳",
            "## 扩展",
            "## 方法相似",
            "# 可复用证据",
            "# 尚未解决的问题",
        ):
            self.assertIn(heading, text)
        self.assertNotIn("自动生成", text)

    def test_repeated_request_is_idempotent(self) -> None:
        first = self.store.create_or_open(payload())
        second = self.store.create_or_open(payload())
        cards = list((self.vault / Path(LITERATURE_FOLDER)).glob("*.md"))

        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(second["cardPath"], first["cardPath"])
        self.assertEqual(len(cards), 1)

    def test_finds_existing_card_by_frontmatter_key_not_filename(self) -> None:
        path = self.store.root / "完全不同的文件名.md"
        path.parent.mkdir(parents=True)
        path.write_text(existing_card("TEST0001"), encoding="utf-8")

        status = self.store.status("test0001")

        self.assertTrue(status["exists"])
        self.assertEqual(status["cardPath"], f"{LITERATURE_FOLDER}/完全不同的文件名.md")

    def test_filename_collision_uses_key_suffix_without_overwriting(self) -> None:
        self.store.root.mkdir(parents=True)
        collision = self.store.root / "Shared Title.md"
        original = existing_card("OTHER001", title="Shared Title")
        collision.write_text(original, encoding="utf-8")

        result = self.store.create_or_open(payload("TEST0002", "Shared: Title"))

        self.assertEqual(result["cardPath"], f"{LITERATURE_FOLDER}/Shared Title - TEST0002.md")
        self.assertEqual(collision.read_text(encoding="utf-8"), original)
        self.assertEqual(clean_windows_filename("CON.txt", "Zotero TEST0002"), "CON.txt - Zotero TEST0002")

    def test_existing_canvas_mapping_is_written_as_wikilink(self) -> None:
        self.mapping.bind("TEST0001", "Excalidraw/Literature/Fixture.excalidraw.md")

        result = self.store.create_or_open(payload())
        text = self.card_path(result["cardPath"]).read_text(encoding="utf-8")

        link = "[[Excalidraw/Literature/Fixture.excalidraw.md|Excalidraw 证据画布]]"
        self.assertIn(f'excalidraw: "{link}"', text)
        self.assertIn(f"- Excalidraw 证据画布：{link}", text)

    def test_missing_canvas_mapping_leaves_excalidraw_empty(self) -> None:
        result = self.store.create_or_open(payload())
        text = self.card_path(result["cardPath"]).read_text(encoding="utf-8")

        self.assertIn("excalidraw:\ndate_created:", text)
        self.assertNotIn(".excalidraw.md|Excalidraw", text)

    def test_missing_citekey_authors_and_year_still_produces_legal_yaml_shapes(self) -> None:
        sparse = payload()
        sparse.update({"authors": [], "year": "", "citekey": ""})

        result = self.store.create_or_open(sparse)
        text = self.card_path(result["cardPath"]).read_text(encoding="utf-8")
        frontmatter = text.split("---", 2)[1]

        self.assertIn("authors: []", frontmatter)
        self.assertRegex(frontmatter, r"(?m)^year:$")
        self.assertRegex(frontmatter, r"(?m)^citekey:$")
        self.assertEqual(_zotero_key_from_text(text), "TEST0001")

    def test_existing_user_fields_body_and_nonempty_machine_fields_are_preserved(self) -> None:
        path = self.store.root / "Existing.md"
        path.parent.mkdir(parents=True)
        before = existing_card("TEST0001", title="用户保留的标题", body="用户关系：[[另一篇文献]]")
        path.write_text(before, encoding="utf-8")
        self.mapping.bind("TEST0001", "Excalidraw/Fixture.excalidraw.md")

        result = self.store.create_or_open(payload(title="Zotero 新标题"))
        after = path.read_text(encoding="utf-8")

        self.assertFalse(result["created"])
        self.assertEqual(set(result["updatedFields"]), {"authors", "year", "citekey", "zotero_link", "excalidraw"})
        self.assertIn("title: 用户保留的标题", after)
        self.assertIn("reading_stage: dr", after)
        self.assertIn('topics:\n  - "[[用户主题]]"', after)
        self.assertIn("one_sentence: 用户的一句话", after)
        self.assertIn("用户关系：[[另一篇文献]]", after)
        self.assertIn("date_modified: 2026-08-02", after)

    def test_duplicate_zotero_key_stops_before_writing(self) -> None:
        self.store.root.mkdir(parents=True)
        first = self.store.root / "First.md"
        second = self.store.root / "Second.md"
        first_text = existing_card("TEST0001", title="First")
        second_text = existing_card("TEST0001", title="Second")
        first.write_text(first_text, encoding="utf-8")
        second.write_text(second_text, encoding="utf-8")

        with self.assertRaisesRegex(DuplicateLiteratureCardError, "对应多张文献卡片"):
            self.store.create_or_open(payload())

        self.assertEqual(first.read_text(encoding="utf-8"), first_text)
        self.assertEqual(second.read_text(encoding="utf-8"), second_text)

    def test_created_card_matches_literature_base_quick_view_filters(self) -> None:
        result = self.store.create_or_open(payload())
        text = self.card_path(result["cardPath"]).read_text(encoding="utf-8")

        self.assertTrue(result["cardPath"].startswith(f"{LITERATURE_FOLDER}/"))
        self.assertTrue(result["cardPath"].endswith(".md"))
        self.assertRegex(text, r"(?m)^type: literature$")
        self.assertRegex(text, r"(?m)^reading_stage: captured$")
        self.assertNotRegex(text, r"(?m)^reading_stage: archived$")


class LiteratureCardHTTPFlowTests(unittest.TestCase):
    def test_fixture_flows_through_status_create_and_open_response(self) -> None:
        fixture_path = Path(__file__).parents[1] / "fixtures" / "literature_card_item.fixture.json"
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = ServiceConfig(
                vault_path=root / "vault",
                literature_folder=LITERATURE_FOLDER,
                mapping_file=root / "map.json",
                state_file=root / "state.json",
                log_file=root / "log.txt",
            )
            config.vault_path.mkdir()
            engine = SyncEngine(
                config,
                client=object(),  # The literature-card path does not call Zotero's annotation API.
                state=StateStore(config.state_file),
                mappings=MappingStore(config.mapping_file),
            )
            server = SyncHTTPServer(("127.0.0.1", 0), engine)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base_url = f"http://127.0.0.1:{server.server_address[1]}"

            def post(path: str, value: dict) -> dict:
                request = Request(
                    base_url + path,
                    data=json.dumps(value).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=2) as response:
                    return json.loads(response.read().decode("utf-8"))

            try:
                self.assertFalse(post("/literature-card-status", fixture)["exists"])
                created = post("/literature-card", fixture)
                self.assertTrue(created["created"])
                self.assertTrue(post("/literature-card-status", fixture)["exists"])
                reopened = post("/literature-card", fixture)
                self.assertFalse(reopened["created"])
                self.assertEqual(reopened["cardPath"], created["cardPath"])
                self.assertTrue((config.vault_path / Path(created["cardPath"])).exists())
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
