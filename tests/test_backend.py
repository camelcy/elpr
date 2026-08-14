from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backend.config import ServiceConfig
from backend.crop import crop_image_annotation, zotero_rect_to_mupdf
from backend.store import MappingStore, StateStore
from backend.sync import SyncEngine, annotation_sort_key, snapshot_hash, source_snapshot
from backend.zotero import ZoteroNotFound, ZoteroResponse


def annotation(key: str, kind: str = "highlight", version: int = 10) -> dict:
    return {
        "key": key,
        "version": version,
        "data": {
            "key": key,
            "version": version,
            "itemType": "annotation",
            "parentItem": "ATTACH01",
            "annotationType": kind,
            "annotationText": "source text",
            "annotationComment": "comment",
            "annotationColor": "#ffd400",
            "annotationPageLabel": "8",
            "annotationSortIndex": "00007|000001|00001",
            "annotationPosition": '{"pageIndex":0,"rects":[[1,2,3,4]]}',
            "dateAdded": "2026-01-01T00:00:00Z",
            "dateModified": "2026-01-01T00:00:00Z",
        },
    }


class FakeClient:
    def __init__(self, items: list[dict]) -> None:
        self.items = items
        self.attachment = {
            "data": {"key": "ATTACH01", "parentItem": "PAPER001"},
            "links": {"enclosure": {"href": "file:///C:/tmp/paper.pdf"}},
        }

    def annotations(self, since: int, limit: int) -> ZoteroResponse:
        return ZoteroResponse(self.items, 42)

    def item(self, key: str) -> dict:
        return self.attachment

    def deleted(self, since: int) -> ZoteroResponse:
        return ZoteroResponse({"items": []}, 42)


class MissingClient(FakeClient):
    def item(self, key: str) -> dict:
        if key == "ANN00001":
            raise ZoteroNotFound("missing")
        return super().item(key)


class BackendTests(unittest.TestCase):
    def test_coordinate_conversion(self) -> None:
        self.assertEqual(zotero_rect_to_mupdf([10, 20, 30, 40], 100), (10.0, 60.0, 30.0, 80.0))

    def test_zotero_link_uses_physical_page_not_printed_page_label(self) -> None:
        snapshot = {
            "key": "KZ9MWXAM",
            "pageLabel": "8",
            "position": '{"pageIndex":16,"rects":[]}',
        }
        self.assertEqual(
            SyncEngine._zotero_link("5N7QUKFQ", snapshot),
            "zotero://open-pdf/library/items/5N7QUKFQ?page=17&annotation=KZ9MWXAM",
        )

    def test_sorting_uses_page_then_sort_index(self) -> None:
        items = [
            {"source": {"pageLabel": "10", "sortIndex": "1", "dateAdded": "a"}},
            {"source": {"pageLabel": "2", "sortIndex": "9", "dateAdded": "b"}},
        ]
        self.assertEqual(sorted(items, key=annotation_sort_key)[0]["source"]["pageLabel"], "2")

    def test_import_ack_preserves_first_snapshot_and_update_does_not_requeue_as_new(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = ServiceConfig(
                vault_path=root,
                data_path=root,
                mapping_file=root / "map.json",
                state_file=root / "state.json",
                log_file=root / "log.txt",
            )
            mappings = MappingStore(config.mapping_file)
            mappings.bind("PAPER001", "Excalidraw/Test.excalidraw.md")
            client = FakeClient([annotation("ANN00001")])
            engine = SyncEngine(config, client=client, state=StateStore(config.state_file), mappings=mappings)
            engine.sync()
            queue = engine.queue()
            self.assertEqual([item["annotationKey"] for item in queue], ["ANN00001"])
            engine.acknowledge({"annotationKey": "ANN00001", "canvasPath": "Excalidraw/Test.excalidraw.md", "elementIds": ["body", "link"]})
            imported = engine.state.data["annotations"]["ANN00001"]
            first_hash = snapshot_hash(imported["sourceSnapshot"])

            changed = annotation("ANN00001", version=11)
            changed["data"]["annotationText"] = "changed at Zotero"
            client.items = [changed]
            engine.sync()
            updated = engine.state.data["annotations"]["ANN00001"]
            self.assertEqual(updated["status"], "source_updated")
            self.assertEqual(snapshot_hash(updated["sourceSnapshot"]), first_hash)
            self.assertEqual(updated["elementIds"], ["body", "link"])

    def test_annotation_without_mapping_stays_pending_and_is_not_lost(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = ServiceConfig(
                vault_path=root,
                data_path=root,
                mapping_file=root / "map.json",
                state_file=root / "state.json",
                log_file=root / "log.txt",
            )
            engine = SyncEngine(
                config,
                client=FakeClient([annotation("UNMAPPED")]),
                state=StateStore(config.state_file),
                mappings=MappingStore(config.mapping_file),
            )
            engine.sync()
            self.assertEqual(engine.state.data["annotations"]["UNMAPPED"]["status"], "pending")
            self.assertEqual(engine.queue(), [])

    def test_canvas_request_is_coalesced_and_completion_binds_pending_annotations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = ServiceConfig(
                vault_path=root,
                data_path=root,
                mapping_file=root / "map.json",
                state_file=root / "state.json",
                log_file=root / "log.txt",
            )
            state = StateStore(config.state_file)
            state.data["annotations"]["ANN00001"] = {
                "annotationKey": "ANN00001",
                "attachmentKey": "ATTACH01",
                "parentItemKey": "PAPER001",
                "status": "pending",
                "source": source_snapshot(annotation("ANN00001")),
                "zoteroLink": "zotero://open-pdf/library/items/ATTACH01?page=1&annotation=ANN00001",
            }
            mappings = MappingStore(config.mapping_file)
            engine = SyncEngine(config, client=FakeClient([]), state=state, mappings=mappings)

            first = engine.request_canvas({"parentItemKey": "PAPER001", "title": "Paper"})
            second = engine.request_canvas({"parentItemKey": "PAPER001", "title": "Updated title"})
            self.assertEqual(first["requestId"], second["requestId"])
            self.assertEqual(engine.canvas_requests()[0]["title"], "Updated title")

            engine.acknowledge_canvas_request({
                "requestId": first["requestId"],
                "action": "completed",
                "canvasPath": "Excalidraw/Literature/Paper.excalidraw.md",
            })
            self.assertEqual(mappings.read()["PAPER001"], "Excalidraw/Literature/Paper.excalidraw.md")
            self.assertEqual(state.data["annotations"]["ANN00001"]["status"], "ready")
            self.assertEqual(engine.canvas_requests(), [])

    def test_canvas_request_rejects_invalid_item_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = ServiceConfig(
                mapping_file=root / "map.json",
                state_file=root / "state.json",
                log_file=root / "log.txt",
            )
            engine = SyncEngine(
                config,
                client=FakeClient([]),
                state=StateStore(config.state_file),
                mappings=MappingStore(config.mapping_file),
            )
            with self.assertRaises(ValueError):
                engine.request_canvas({"parentItemKey": "../bad"})

    def test_canvas_status_reports_existing_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = ServiceConfig(
                mapping_file=root / "map.json",
                state_file=root / "state.json",
                log_file=root / "log.txt",
            )
            mappings = MappingStore(config.mapping_file)
            mappings.bind("PAPER001", "Excalidraw/Literature/Paper.excalidraw.md")
            engine = SyncEngine(
                config,
                client=FakeClient([]),
                state=StateStore(config.state_file),
                mappings=mappings,
            )

            self.assertEqual(
                engine.canvas_status("paper001"),
                {
                    "parentItemKey": "PAPER001",
                    "mapped": True,
                    "canvasPath": "Excalidraw/Literature/Paper.excalidraw.md",
                },
            )
            self.assertEqual(engine.canvas_status("UNMAP001")["mapped"], False)
            with self.assertRaises(ValueError):
                engine.canvas_status("../bad")

    def test_legacy_import_is_queued_once_for_layout_repair(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = ServiceConfig(
                vault_path=root,
                data_path=root,
                mapping_file=root / "map.json",
                state_file=root / "state.json",
                log_file=root / "log.txt",
            )
            state = StateStore(config.state_file)
            state.data["annotations"]["ANN00001"] = {
                "annotationKey": "ANN00001",
                "attachmentKey": "ATTACH01",
                "parentItemKey": "PAPER001",
                "status": "imported",
                "source": annotation("ANN00001")["data"],
                "zoteroLink": "old",
                "elementIds": ["old-card", "old-body"],
            }
            engine = SyncEngine(config, client=FakeClient([]), state=state, mappings=MappingStore(config.mapping_file))

            queued = engine.queue()
            self.assertEqual([item["status"] for item in queued], ["layout_repair"])
            engine.acknowledge({
                "annotationKey": "ANN00001",
                "action": "imported",
                "elementIds": ["comment", "source"],
            })
            self.assertEqual(engine.queue(), [])
            self.assertEqual(state.data["annotations"]["ANN00001"]["layoutSchemaVersion"], 7)

    def test_missing_source_keeps_missing_status_after_layout_repair(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = ServiceConfig(
                vault_path=root,
                data_path=root,
                mapping_file=root / "map.json",
                state_file=root / "state.json",
                log_file=root / "log.txt",
            )
            state = StateStore(config.state_file)
            state.data["annotations"]["MISSING"] = {
                "annotationKey": "MISSING",
                "attachmentKey": "ATTACH01",
                "parentItemKey": "PAPER001",
                "status": "source_missing",
                "source": annotation("MISSING")["data"],
                "zoteroLink": "zotero://open-pdf/library/items/ATTACH01?page=1&annotation=MISSING",
                "elementIds": ["legacy-card"],
            }
            engine = SyncEngine(config, client=FakeClient([]), state=state, mappings=MappingStore(config.mapping_file))

            self.assertEqual(engine.queue()[0]["status"], "layout_repair")
            engine.acknowledge({"annotationKey": "MISSING", "action": "imported", "elementIds": ["new-text"]})
            record = state.data["annotations"]["MISSING"]
            self.assertEqual(record["status"], "source_missing")
            self.assertEqual(record["layoutSchemaVersion"], 7)

    def test_live_reference_crop_matches_existing_dimensions_when_available(self) -> None:
        pdf = Path(r"C:\Users\pec\Zotero\storage\5N7QUKFQ\3D imaging of the biphoton spatiotemporal wave.pdf")
        reference = Path(r"D:\Obsidian\Steins Gate\Excalidraw\Images\KZ9MWXAM.png")
        if not pdf.exists() or not reference.exists():
            self.skipTest("local Zotero fixture is unavailable")
        from PIL import Image, ImageChops, ImageStat

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "KZ9MWXAM.png"
            position = {"pageIndex": 16, "rects": [[108.125, 479.078, 459.375, 728.765]]}
            crop_image_annotation(pdf, position, output, 4)
            with Image.open(output) as actual, Image.open(reference) as expected:
                self.assertLessEqual(abs(actual.width - expected.width), 1)
                self.assertLessEqual(abs(actual.height - expected.height), 1)
                common = (min(actual.width, expected.width), min(actual.height, expected.height))
                difference = ImageChops.difference(
                    actual.convert("RGB").crop((0, 0, *common)),
                    expected.convert("RGB").crop((0, 0, *common)),
                )
                mean_rms = sum(ImageStat.Stat(difference).rms) / 3
                self.assertLess(mean_rms, 35)

    def test_missing_imported_source_is_marked_without_deleting_canvas_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = ServiceConfig(
                vault_path=root,
                data_path=root,
                mapping_file=root / "map.json",
                state_file=root / "state.json",
                log_file=root / "log.txt",
                deletion_check_interval_seconds=30,
            )
            mappings = MappingStore(config.mapping_file)
            mappings.bind("PAPER001", "Excalidraw/Test.excalidraw.md")
            state = StateStore(config.state_file)
            state.data["lastLibraryVersion"] = 41
            state.data["annotations"]["ANN00001"] = {
                "annotationKey": "ANN00001",
                "attachmentKey": "ATTACH01",
                "parentItemKey": "PAPER001",
                "status": "imported",
                "source": annotation("ANN00001")["data"],
                "sourceHash": "old",
                "sourceSnapshot": {"text": "manually edited canvas remains independent"},
                "elementIds": ["body", "link"],
            }
            engine = SyncEngine(config, client=MissingClient([]), state=state, mappings=mappings)
            engine.sync()
            record = state.data["annotations"]["ANN00001"]
            self.assertEqual(record["status"], "layout_repair")
            self.assertEqual(record["layoutRepairReturnStatus"], "source_missing")
            self.assertEqual(record["elementIds"], ["body", "link"])


if __name__ == "__main__":
    unittest.main()
