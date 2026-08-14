import type { CanvasRequestItem, DisplayOrder, QueueItem } from "./types";

export const PLUGIN_DATA_KEY = "zoteroExcalidrawSync";

export interface Bounds {
  minX: number;
  minY: number;
  maxX: number;
  maxY: number;
}

export interface ElementLike {
  id: string;
  x: number;
  y: number;
  width: number;
  height: number;
  isDeleted?: boolean;
  customData?: Record<string, unknown>;
}

export interface AnnotationTextBlock {
  role: "comment" | "source";
  text: string;
}

export function annotationElementId(annotationKey: string, role: string): string {
  let hash = 2166136261;
  for (const character of `${annotationKey}:${role}`) {
    hash ^= character.charCodeAt(0);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(16).padStart(8, "0");
}

export function zoteroItemLink(parentItemKey: string): string {
  return `zotero://select/library/items/${parentItemKey}`;
}

export function elementSyncData(element: ElementLike): Record<string, unknown> | undefined {
  const value = element.customData?.[PLUGIN_DATA_KEY];
  return value && typeof value === "object" ? (value as Record<string, unknown>) : undefined;
}

export function elementsForAnnotation(elements: ElementLike[], annotationKey: string): ElementLike[] {
  return elements.filter((element) => elementSyncData(element)?.annotationKey === annotationKey);
}

export function calculateBounds(elements: ElementLike[]): Bounds {
  const live = elements.filter((element) => !element.isDeleted);
  if (live.length === 0) {
    return { minX: 0, minY: 0, maxX: 0, maxY: 0 };
  }
  return live.reduce<Bounds>(
    (bounds, element) => ({
      minX: Math.min(bounds.minX, element.x),
      minY: Math.min(bounds.minY, element.y),
      maxX: Math.max(bounds.maxX, element.x + element.width),
      maxY: Math.max(bounds.maxY, element.y + element.height),
    }),
    { minX: Infinity, minY: Infinity, maxX: -Infinity, maxY: -Infinity },
  );
}

export function annotationTextBlocks(item: QueueItem, order: DisplayOrder): AnnotationTextBlock[] {
  const blocks: AnnotationTextBlock[] = [
    { role: "comment", text: item.source.comment.trim() },
    { role: "source", text: item.source.text.trim() },
  ];
  if (order === "text-comment") blocks.reverse();
  return blocks.filter((block) => Boolean(block.text));
}

function visualWidth(character: string): number {
  return /^[\u0000-\u00ff]$/.test(character) ? 1 : 2;
}

function wrapParagraph(paragraph: string, maxVisualWidth: number): string[] {
  if (!paragraph) return [""];
  const lines: string[] = [];
  let remaining = paragraph.trim();
  while (remaining) {
    let width = 0;
    let end = 0;
    let lastWhitespace = -1;
    for (const character of remaining) {
      const nextWidth = width + visualWidth(character);
      if (nextWidth > maxVisualWidth && end > 0) break;
      width = nextWidth;
      end += character.length;
      if (/\s/.test(character)) lastWhitespace = end;
    }
    if (end >= remaining.length) {
      lines.push(remaining);
      break;
    }
    const breakAt = lastWhitespace > 0 ? lastWhitespace : end;
    lines.push(remaining.slice(0, breakAt).trimEnd());
    remaining = remaining.slice(breakAt).trimStart();
  }
  return lines;
}

export function wrapTextForCanvas(text: string, maxVisualWidth = 64): string {
  return text
    .replace(/\r\n?/g, "\n")
    .split("\n")
    .flatMap((paragraph) => wrapParagraph(paragraph, maxVisualWidth))
    .join("\n");
}

export function pageSortValue(label: string): [number, string] {
  const digits = label.match(/\d+/)?.[0];
  return [digits ? Number.parseInt(digits, 10) : Number.MAX_SAFE_INTEGER, label];
}

export function compareQueueItems(left: QueueItem, right: QueueItem): number {
  const [leftPage, leftLabel] = pageSortValue(left.source.pageLabel);
  const [rightPage, rightLabel] = pageSortValue(right.source.pageLabel);
  return (
    leftPage - rightPage ||
    leftLabel.localeCompare(rightLabel) ||
    left.source.sortIndex.localeCompare(right.source.sortIndex) ||
    left.source.dateAdded.localeCompare(right.source.dateAdded)
  );
}

function filenamePart(value: string): string {
  return value
    .replace(/[\\/:*?"<>|#^\[\]]/g, " ")
    .replace(/[\u0000-\u001f]/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/[. ]+$/g, "");
}

export function canvasBaseName(request: CanvasRequestItem): string {
  const year = filenamePart(request.year).match(/\b\d{4}\b/)?.[0] ?? "";
  const creator = filenamePart(request.firstCreator);
  const title = filenamePart(request.title);
  const parts = [year, creator, title].filter(Boolean);
  const fallback = `Zotero ${request.parentItemKey}`;
  return (parts.join(" - ") || fallback).slice(0, 140).trim().replace(/[. ]+$/g, "") || fallback;
}
