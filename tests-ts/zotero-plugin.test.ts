import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

function loadPlugin(): Record<string, any> {
  const source = readFileSync("zotero-plugin/zotero-excalidraw-canvas.js", "utf8");
  const context: Record<string, any> = {
    Zotero: {
      BetterBibTeX: undefined,
      CreatorTypes: { getName: (id: number) => (id === 1 ? "author" : "editor") },
      Prefs: { get: (name: string) => (name.endsWith("vaultName") ? "Steins Gate" : "") },
      logError: () => undefined,
    },
    Services: { prompt: { alert: () => undefined } },
  };
  vm.runInNewContext(source, context);
  return context.ZoteroExcalidrawCanvas;
}

test("literature card metadata includes authors, citation key, and DOI", () => {
  const plugin = loadPlugin();
  const item = {
    id: 1,
    key: "TEST0001",
    getCreators: () => [
      { creatorTypeID: 1, firstName: "Ada", lastName: "Lovelace" },
      { creatorTypeID: 2, firstName: "Test", lastName: "Editor" },
    ],
    getField: (field: string) => ({
      title: "Fixture title",
      date: "2026-08-15",
      citationKey: "lovelaceFixture2026",
      DOI: "10.1234/fixture.2026.001",
      extra: "",
    })[field] ?? "",
  };

  assert.deepEqual(
    JSON.parse(JSON.stringify(plugin.itemPayload(item))),
    {
      parentItemKey: "TEST0001",
      title: "Fixture title",
      year: "2026",
      firstCreator: "Lovelace",
      authors: ["Ada Lovelace"],
      citekey: "lovelaceFixture2026",
      doi: "10.1234/fixture.2026.001",
    },
  );
});

test("successful card request opens the returned Markdown path in Obsidian", async () => {
  const plugin = loadPlugin();
  const item = {
    id: 1,
    key: "TEST0001",
    isRegularItem: () => true,
    getCreators: () => [],
    getField: (field: string) => (field === "title" ? "Fixture" : ""),
  };
  let openedPath = "";
  plugin.post = async () => ({
    created: true,
    cardPath: "20 - 工作学习/文献/Literature/Fixture.md",
  });
  plugin.launchObsidian = (path: string) => {
    openedPath = path;
  };
  plugin.notify = () => undefined;
  plugin.isChinese = () => true;

  const result = await plugin.requestLiteratureCard([item]);

  assert.equal(result.created, true);
  assert.equal(openedPath, "20 - 工作学习/文献/Literature/Fixture.md");
  assert.equal(
    plugin.obsidianURI(openedPath),
    "obsidian://open?vault=Steins%20Gate&file=20%20-%20%E5%B7%A5%E4%BD%9C%E5%AD%A6%E4%B9%A0%2F%E6%96%87%E7%8C%AE%2FLiterature%2FFixture.md",
  );
});
