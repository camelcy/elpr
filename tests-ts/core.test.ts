import assert from "node:assert/strict";
import test from "node:test";
import {
  PLUGIN_DATA_KEY,
  annotationElementId,
  annotationTextBlocks,
  canvasBaseName,
  compareQueueItems,
  elementsForAnnotation,
  wrapTextForCanvas,
  zoteroItemLink,
} from "../src/core";
import type { QueueItem } from "../src/types";

function queueItem(key: string, page: string, sortIndex: string): QueueItem {
  return {
    annotationKey: key,
    attachmentKey: "ATTACH01",
    parentItemKey: "PAPER001",
    status: "ready",
    sourceHash: "hash",
    canvasPath: "Excalidraw/Test.excalidraw.md",
    zoteroLink: `zotero://open-pdf/library/items/ATTACH01?page=${page}&annotation=${key}`,
    source: {
      key,
      version: 1,
      type: "highlight",
      text: "English source",
      comment: "中文评论",
      color: "#ffd400",
      pageLabel: page,
      sortIndex,
      position: "{}",
      dateAdded: "2026-01-01T00:00:00Z",
      dateModified: "2026-01-01T00:00:00Z",
    },
  };
}

test("comment and source are independent blocks in the configured order", () => {
  const item = queueItem("ANN00001", "8", "1");
  assert.deepEqual(annotationTextBlocks(item, "comment-text"), [
    { role: "comment", text: "中文评论" },
    { role: "source", text: "English source" },
  ]);
  assert.deepEqual(annotationTextBlocks(item, "text-comment").map((block) => block.role), ["source", "comment"]);
});

test("canvas text wrapping keeps long prose at a bounded visual width", () => {
  const wrapped = wrapTextForCanvas("one two three four five six seven eight", 12);
  assert.deepEqual(wrapped.split("\n"), ["one two", "three four", "five six", "seven eight"]);

  const chinese = wrapTextForCanvas("中文批注应该分成多行而不是纵向拉长", 10);
  assert.ok(chinese.split("\n").every((line) => [...line].length <= 5));
});

test("queue ordering uses numeric PDF page before sort index", () => {
  const items = [queueItem("A", "10", "1"), queueItem("B", "2", "9"), queueItem("C", "2", "1")];
  assert.deepEqual(items.sort(compareQueueItems).map((item) => item.annotationKey), ["C", "B", "A"]);
});

test("deduplication is based on annotation key metadata, not editable text", () => {
  const elements = [
    {
      id: "body",
      x: 0,
      y: 0,
      width: 10,
      height: 10,
      customData: { [PLUGIN_DATA_KEY]: { annotationKey: "ANN00001", role: "source-text" } },
    },
  ];
  assert.equal(elementsForAnnotation(elements, "ANN00001").length, 1);
  assert.equal(elementsForAnnotation(elements, "different text").length, 0);
  const sourceId = annotationElementId("ANN00001", "source-text");
  assert.match(sourceId, /^[0-9a-f]{8}$/);
  assert.equal(sourceId, annotationElementId("ANN00001", "source-text"));
  assert.notEqual(sourceId, annotationElementId("ANN00001", "comment-text"));
});

test("canvas filenames use metadata and remove unsafe path characters", () => {
  assert.equal(
    canvasBaseName({
      requestId: "request",
      parentItemKey: "PAPER001",
      title: "A/B: C?",
      year: "2024-03-01",
      firstCreator: "Doe|Smith",
      requestedAt: "2026-01-01T00:00:00Z",
    }),
    "2024 - Doe Smith - A B C",
  );
});

test("paper titles link back to the parent Zotero item", () => {
  assert.equal(zoteroItemLink("PAPER001"), "zotero://select/library/items/PAPER001");
});
