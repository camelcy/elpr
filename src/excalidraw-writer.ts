import { App, TFile, WorkspaceLeaf } from "obsidian";
import {
  PLUGIN_DATA_KEY,
  annotationElementId,
  annotationTextBlocks,
  calculateBounds,
  elementSyncData,
  elementsForAnnotation,
  zoteroItemLink,
  wrapTextForCanvas,
} from "./core";
import type { CanvasRequestItem, DisplayOrder, QueueItem } from "./types";

interface ExcalidrawElementLike {
  id: string;
  type: string;
  x: number;
  y: number;
  width: number;
  height: number;
  isDeleted?: boolean;
  link?: string | null;
  locked?: boolean;
  containerId?: string | null;
  boundElements?: Array<{ id: string; type: string }> | null;
  version?: number;
  versionNonce?: number;
  updated?: number;
  customData?: Record<string, unknown>;
}

interface ExcalidrawAutomateLike {
  style: Record<string, unknown>;
  reset(): void;
  destroy(): void;
  setView(view: unknown): unknown;
  getViewElements(): ExcalidrawElementLike[];
  getElements(): ExcalidrawElementLike[];
  getElement(id: string): ExcalidrawElementLike;
  copyViewElementsToEAforEditing(elements: ExcalidrawElementLike[]): void;
  addRect(x: number, y: number, width: number, height: number, id?: string): string;
  addText(
    x: number,
    y: number,
    text: string,
    formatting?: Record<string, unknown>,
    id?: string,
  ): string;
  addImage(x: number, y: number, file: TFile | string, scale?: boolean, anchor?: boolean): Promise<string>;
  addAppendUpdateCustomData(id: string, data: Record<string, unknown>): unknown;
  addElementsToView(repositionToCursor?: boolean, save?: boolean, newElementsOnTop?: boolean): Promise<boolean>;
}

interface ExcalidrawViewLike {
  file?: TFile;
  _loaded?: boolean;
  excalidrawAPI?: unknown;
  forceSave?: () => Promise<void>;
}

declare global {
  interface Window {
    ExcalidrawAutomate?: {
      getAPI(view?: unknown): ExcalidrawAutomateLike;
    };
  }
}

interface ViewHandle {
  view: ExcalidrawViewLike;
  temporaryLeaf?: WorkspaceLeaf;
}

interface ImportResult {
  annotationKey: string;
  action: "imported" | "source-updated-notified";
  elementIds: string[];
}

export class ExcalidrawWriter {
  constructor(
    private readonly app: App,
    private readonly displayOrder: DisplayOrder,
  ) {}

  async ensurePaperTitle(canvasPath: string, request: CanvasRequestItem): Promise<void> {
    const handle = await this.getView(canvasPath);
    const ea = window.ExcalidrawAutomate?.getAPI(handle.view);
    if (!ea) {
      handle.temporaryLeaf?.detach();
      throw new Error("Excalidraw Automate API is unavailable");
    }

    ea.reset();
    ea.setView(handle.view);
    const scene = ea.getViewElements().filter((element) => !element.isDeleted);
    const existing = scene.find((element) => {
      const data = elementSyncData(element);
      return data?.role === "paper-title" && data?.parentItemKey === request.parentItemKey;
    });

    try {
      if (existing) return;
      const zoteroLink = zoteroItemLink(request.parentItemKey);
      const title = wrapTextForCanvas(request.title.trim() || `Zotero ${request.parentItemKey}`, 56);
      ea.style.strokeColor = "#1e1e1e";
      ea.style.backgroundColor = "transparent";
      ea.style.opacity = 100;
      ea.style.fontSize = 32;
      const titleId = ea.addText(
        0,
        0,
        title,
        { autoResize: true, textAlign: "left" },
        annotationElementId(request.parentItemKey, "paper-title"),
      );
      const titleElement = ea.getElement(titleId);
      titleElement.link = zoteroLink;
      ea.addAppendUpdateCustomData(titleId, {
        [PLUGIN_DATA_KEY]: {
          schemaVersion: 2,
          parentItemKey: request.parentItemKey,
          role: "paper-title",
          zoteroLink,
        },
      });
      const saved = await ea.addElementsToView(false, true, true);
      if (!saved) throw new Error("Excalidraw rejected the linked paper title");
      await handle.view.forceSave?.();
    } finally {
      ea.destroy();
      handle.temporaryLeaf?.detach();
    }
  }

  async write(canvasPath: string, items: QueueItem[]): Promise<ImportResult[]> {
    const handle = await this.getView(canvasPath);
    const ea = window.ExcalidrawAutomate?.getAPI(handle.view);
    if (!ea) {
      handle.temporaryLeaf?.detach();
      throw new Error("Excalidraw Automate API is unavailable");
    }

    ea.reset();
    ea.setView(handle.view);
    const scene = ea.getViewElements().filter((element) => !element.isDeleted);
    const results: ImportResult[] = [];
    let changed = false;

    try {
      if (items.some((item) => item.status === "layout_repair")) {
        changed = this.removeLegacyHeaders(ea, scene) || changed;
      }

      for (const item of items) {
        const existing = elementsForAnnotation(scene, item.annotationKey) as ExcalidrawElementLike[];
        if (item.status === "source_updated") {
          if (existing.length > 0) {
            const ids = this.addUpdateBadge(ea, scene, item.annotationKey);
            if (ids.length > 0) {
              results.push({ annotationKey: item.annotationKey, action: "source-updated-notified", elementIds: ids });
              changed = true;
            }
          }
          continue;
        }

        if (item.status === "layout_repair") {
          const placement = existing.length > 0
            ? { x: calculateBounds(existing).minX, y: calculateBounds(existing).minY }
            : undefined;
          const elementIds = new Set([...existing.map((element) => element.id), ...(item.elementIds ?? [])]);
          this.removeElements(ea, scene, elementIds);
          const ids = await this.addCard(ea, scene, item, placement);
          results.push({ annotationKey: item.annotationKey, action: "imported", elementIds: ids });
          changed = true;
          continue;
        }

        if (existing.length > 0) {
          const linkElements = existing.filter((element) => {
            const data = elementSyncData(element);
            return data?.role === "link" || typeof data?.zoteroLink === "string";
          });
          for (const linkElement of linkElements) {
            if (linkElement.link === item.zoteroLink) continue;
            ea.copyViewElementsToEAforEditing([linkElement]);
            const editableLink = ea.getElement(linkElement.id);
            editableLink.link = item.zoteroLink;
            this.touch(editableLink);
            changed = true;
          }
          results.push({ annotationKey: item.annotationKey, action: "imported", elementIds: existing.map((element) => element.id) });
          continue;
        }

        const ids = await this.addCard(ea, scene, item);
        results.push({ annotationKey: item.annotationKey, action: "imported", elementIds: ids });
        changed = true;
      }

      if (changed) {
        const saved = await ea.addElementsToView(false, true, true);
        if (!saved) throw new Error("Excalidraw rejected the scene update");
        await handle.view.forceSave?.();
      }
      return results;
    } finally {
      ea.destroy();
      handle.temporaryLeaf?.detach();
    }
  }

  private async getView(canvasPath: string): Promise<ViewHandle> {
    const file = this.app.vault.getAbstractFileByPath(canvasPath);
    if (!(file instanceof TFile)) throw new Error(`mapped canvas does not exist: ${canvasPath}`);

    const existingLeaf = this.app.workspace
      .getLeavesOfType("excalidraw")
      .find((leaf) => (leaf.view as ExcalidrawViewLike).file?.path === file.path);
    if (existingLeaf) {
      const view = existingLeaf.view as ExcalidrawViewLike;
      if (await this.waitForView(view)) return { view };
      throw new Error(`Excalidraw did not load canvas: ${canvasPath}`);
    }

    const leavesBefore = new Set(this.app.workspace.getLeavesOfType("excalidraw"));
    const pluginHost = this.app as App & { plugins?: { plugins?: Record<string, unknown> } };
    const excalidrawPlugin = pluginHost.plugins?.plugins?.["obsidian-excalidraw-plugin"] as
      | { isReady?: boolean; openDrawing?: (target: TFile, location: string, active?: boolean) => void }
      | undefined;
    if (!excalidrawPlugin?.openDrawing) throw new Error("Excalidraw plugin openDrawing API is unavailable");
    if (!excalidrawPlugin.isReady) throw new Error("Excalidraw is still initializing; the queue will be retried");
    excalidrawPlugin.openDrawing(file, "new-tab", false);

    let leaf: WorkspaceLeaf | undefined;
    let view: ExcalidrawViewLike | undefined;
    for (let attempt = 0; attempt < 100; attempt += 1) {
      leaf = this.app.workspace
        .getLeavesOfType("excalidraw")
        .find((candidate) => (candidate.view as ExcalidrawViewLike).file?.path === file.path);
      view = leaf?.view as ExcalidrawViewLike | undefined;
      if (view?._loaded && view.excalidrawAPI) break;
      await new Promise((resolve) => window.setTimeout(resolve, 100));
    }
    if (!leaf || !view?._loaded || !view.excalidrawAPI) {
      if (leaf && !leavesBefore.has(leaf)) leaf.detach();
      throw new Error(`Excalidraw did not load canvas: ${canvasPath}`);
    }
    return { view, temporaryLeaf: leavesBefore.has(leaf) ? undefined : leaf };
  }

  private async waitForView(view: ExcalidrawViewLike): Promise<boolean> {
    for (let attempt = 0; attempt < 100; attempt += 1) {
      if (view._loaded && view.excalidrawAPI) return true;
      await new Promise((resolve) => window.setTimeout(resolve, 100));
    }
    return false;
  }

  private async addCard(
    ea: ExcalidrawAutomateLike,
    scene: ExcalidrawElementLike[],
    item: QueueItem,
    placementOverride?: { x: number; y: number },
  ): Promise<string[]> {
    const placement = placementOverride ?? this.nextPlacement(ea, scene);
    const x = placement.x;
    let y = placement.y;

    const addTextBlock = (role: "comment" | "source", text: string): void => {
      const wrapped = wrapTextForCanvas(text);
      const estimatedHeight = Math.max(96, wrapped.split("\n").length * 25 + 48);
      ea.style.strokeColor = item.source.color || "#ffd400";
      ea.style.backgroundColor = item.source.color || "#ffd400";
      ea.style.fillStyle = "solid";
      ea.style.opacity = 18;
      const backgroundId = ea.addRect(
        x,
        y,
        680,
        estimatedHeight,
        annotationElementId(item.annotationKey, role === "comment" ? "cb" : "sb"),
      );
      const background = ea.getElement(backgroundId);
      this.tag(ea, backgroundId, item.annotationKey, `${role}-background`);

      ea.style.opacity = 100;
      ea.style.strokeColor = "#1e1e1e";
      ea.style.backgroundColor = "transparent";
      ea.style.fontSize = 18;
      const textId = ea.addText(
        x + 30,
        y + 24,
        wrapped,
        { autoResize: true, textAlign: "left" },
        annotationElementId(item.annotationKey, role === "comment" ? "c" : "s"),
      );
      const textElement = ea.getElement(textId);
      textElement.link = item.zoteroLink;
      background.height = Math.max(estimatedHeight, textElement.height + 48);
      this.tag(ea, textId, item.annotationKey, `${role}-text`, { zoteroLink: item.zoteroLink });
      y += background.height + 24;
    };

    const addImageBlock = async (): Promise<void> => {
      if (!item.imagePath) throw new Error(`image path missing for annotation ${item.annotationKey}`);
      const imageFile = this.app.vault.getAbstractFileByPath(item.imagePath);
      if (!(imageFile instanceof TFile)) throw new Error(`cropped image does not exist: ${item.imagePath}`);

      ea.style.opacity = 100;
      const imageId = await ea.addImage(x, y, imageFile, true, true);
      const image = ea.getElement(imageId);
      if (image.width > 600) {
        const factor = 600 / image.width;
        image.width *= factor;
        image.height *= factor;
      }
      if (image.height > 400) {
        const factor = 400 / image.height;
        image.width *= factor;
        image.height *= factor;
      }
      image.link = item.zoteroLink;
      this.tag(ea, imageId, item.annotationKey, "source-image", { zoteroLink: item.zoteroLink });
      y += image.height + 24;
    };

    if (item.source.type === "image" && this.displayOrder === "text-comment") await addImageBlock();
    for (const block of annotationTextBlocks(item, this.displayOrder)) addTextBlock(block.role, block.text);
    if (item.source.type === "image" && this.displayOrder === "comment-text") await addImageBlock();

    const added = ea.getElements().filter(
      (element) => !element.isDeleted && elementSyncData(element)?.annotationKey === item.annotationKey,
    );
    scene.push(...added);
    return added.map((element) => element.id);
  }

  private nextPlacement(ea: ExcalidrawAutomateLike, scene: ExcalidrawElementLike[]): { x: number; y: number } {
    const all = [...scene, ...ea.getElements()];
    const inboxElements = all.filter((element) => Boolean(elementSyncData(element)) && !element.isDeleted);
    if (inboxElements.length > 0) {
      const bounds = calculateBounds(inboxElements);
      return { x: bounds.minX, y: bounds.maxY + 40 };
    }
    const bounds = calculateBounds(scene);
    return { x: bounds.maxX + 160, y: bounds.minY };
  }

  private removeLegacyHeaders(ea: ExcalidrawAutomateLike, scene: ExcalidrawElementLike[]): boolean {
    const ids = new Set(
      scene
        .filter((element) => elementSyncData(element)?.role === "inbox-header")
        .map((element) => element.id),
    );
    return this.removeElements(ea, scene, ids);
  }

  private removeElements(
    ea: ExcalidrawAutomateLike,
    scene: ExcalidrawElementLike[],
    initialIds: Set<string>,
  ): boolean {
    const ids = new Set(initialIds);
    let expanded = true;
    while (expanded) {
      expanded = false;
      for (const element of scene) {
        const related = (element.containerId && ids.has(element.containerId))
          || element.boundElements?.some((bound) => ids.has(bound.id));
        if (related && !ids.has(element.id)) {
          ids.add(element.id);
          expanded = true;
        }
      }
    }

    const targets = scene.filter((element) => ids.has(element.id) && !element.isDeleted);
    if (targets.length === 0) return false;
    ea.copyViewElementsToEAforEditing(targets);
    for (const target of targets) {
      const editable = ea.getElement(target.id);
      editable.isDeleted = true;
      this.touch(editable);
      target.isDeleted = true;
    }
    return true;
  }

  private addUpdateBadge(ea: ExcalidrawAutomateLike, scene: ExcalidrawElementLike[], annotationKey: string): string[] {
    const existingBadge = scene.find(
      (element) => elementSyncData(element)?.annotationKey === annotationKey
        && elementSyncData(element)?.role === "update-badge",
    );
    if (existingBadge) return [];
    const annotationElements = elementsForAnnotation(scene, annotationKey);
    if (annotationElements.length === 0) return [];
    const bounds = calculateBounds(annotationElements);
    ea.style.strokeColor = "#e8590c";
    ea.style.backgroundColor = "#fff4e6";
    ea.style.opacity = 100;
    ea.style.fontSize = 14;
    const id = ea.addText(
      bounds.maxX + 18,
      bounds.minY,
      "⚠ Zotero source updated",
      { box: true, boxPadding: 8, autoResize: true },
      annotationElementId(annotationKey, "updated"),
    );
    this.tag(ea, id, annotationKey, "update-badge");
    scene.push(ea.getElement(id));
    return [id];
  }

  private tag(
    ea: ExcalidrawAutomateLike,
    id: string,
    annotationKey: string,
    role: string,
    extra: Record<string, unknown> = {},
  ): void {
    ea.addAppendUpdateCustomData(id, {
      [PLUGIN_DATA_KEY]: {
        schemaVersion: 2,
        annotationKey,
        role,
        ...extra,
      },
    });
  }

  private touch(element: ExcalidrawElementLike): void {
    element.version = (element.version ?? 0) + 1;
    element.versionNonce = Math.floor(Math.random() * 1_000_000_000);
    element.updated = Date.now();
  }
}
