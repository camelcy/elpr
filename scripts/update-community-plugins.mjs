import { readFileSync, renameSync, writeFileSync } from "node:fs";
import process from "node:process";

const [action, targetPath, pluginId = "zotero-excalidraw-sync"] = process.argv.slice(2);
if (!targetPath || !["enable", "disable"].includes(action)) {
  throw new Error("usage: update-community-plugins.mjs <enable|disable> <json-path> [plugin-id]");
}

const parsed = JSON.parse(readFileSync(targetPath, "utf8").replace(/^\uFEFF/, ""));
const flattened = [];
const visit = (value) => {
  if (typeof value === "string") flattened.push(value);
  else if (Array.isArray(value)) value.forEach(visit);
  else if (value && typeof value === "object" && "value" in value) visit(value.value);
};
visit(parsed);

const unique = [...new Set(flattened.filter((value) => value !== pluginId))];
if (action === "enable") unique.push(pluginId);
const temporary = `${targetPath}.zotero-sync.tmp`;
writeFileSync(temporary, `${JSON.stringify(unique, null, 2)}\n`, "utf8");
const verified = JSON.parse(readFileSync(temporary, "utf8"));
if (!Array.isArray(verified) || !verified.every((value) => typeof value === "string")) {
  throw new Error("generated community plugin list is invalid");
}
renameSync(temporary, targetPath);

