import { App, TFile, WorkspaceLeaf } from "obsidian";
import {
  ANNOTATION_LAYOUT,
  HANDWRITING_FONT_FAMILY,
  PAPER_CANVAS_TEMPLATE,
  PLUGIN_DATA_KEY,
  SPECIAL_ANNOTATION_SECTIONS,
  annotationElementId,
  annotationTextBlocks,
  calculateBounds,
  elementSyncData,
  elementsForAnnotation,
  nextAnnotationColumnPlacement,
  paperCanvasTitle,
  specialAnnotationSection,
  SYNCED_TEXT_FONT_SIZE,
  zoteroItemLink,
  wrapTextForCanvas,
} from "./core";
import type { SpecialAnnotationSection } from "./core";
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
  excalidrawAPI?: {
    updateScene?: (sceneData: { appState?: Record<string, unknown> }) => void;
  };
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
    const shouldAddTemplate = scene.length === 0;
    const existing = scene.find((element) => {
      const data = elementSyncData(element);
      return data?.role === "paper-title" && data?.parentItemKey === request.parentItemKey;
    });

    try {
      if (existing) return;
      const zoteroLink = zoteroItemLink(request.parentItemKey);
      const title = paperCanvasTitle(request);
      ea.style.strokeColor = PAPER_CANVAS_TEMPLATE.strokeColor;
      ea.style.backgroundColor = "transparent";
      ea.style.opacity = 100;
      ea.style.fontSize = PAPER_CANVAS_TEMPLATE.title.fontSize;
      ea.style.fontFamily = PAPER_CANVAS_TEMPLATE.title.fontFamily;
      const titleId = ea.addText(
        PAPER_CANVAS_TEMPLATE.title.x,
        PAPER_CANVAS_TEMPLATE.title.y,
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
      if (shouldAddTemplate) this.addPaperTemplate(ea, request.parentItemKey);
      const saved = await ea.addElementsToView(false, true, true);
      if (!saved) throw new Error("Excalidraw rejected the linked paper title");
      if (shouldAddTemplate) this.setInitialViewport(handle.view);
      await handle.view.forceSave?.();
    } finally {
      ea.destroy();
      handle.temporaryLeaf?.detach();
    }
  }

  private addPaperTemplate(ea: ExcalidrawAutomateLike, parentItemKey: string): void {
    for (const section of PAPER_CANVAS_TEMPLATE.sections) {
      ea.style.strokeColor = PAPER_CANVAS_TEMPLATE.strokeColor;
      ea.style.backgroundColor = "transparent";
      ea.style.opacity = 100;
      ea.style.fontSize = PAPER_CANVAS_TEMPLATE.sectionLabelFontSize;
      ea.style.fontFamily = PAPER_CANVAS_TEMPLATE.title.fontFamily;
      const labelId = ea.addText(
        PAPER_CANVAS_TEMPLATE.sectionLabelX,
        section.labelY,
        section.label,
        { autoResize: true, textAlign: "left" },
        annotationElementId(parentItemKey, `template-${section.key}-label`),
      );
      this.tagPaperTemplateElement(ea, labelId, parentItemKey, "template-section-label", section.key);

      ea.style.strokeColor = PAPER_CANVAS_TEMPLATE.strokeColor;
      ea.style.backgroundColor = section.backgroundColor;
      ea.style.fillStyle = "solid";
      ea.style.strokeWidth = PAPER_CANVAS_TEMPLATE.strokeWidth;
      ea.style.roughness = 1;
      ea.style.roundness = PAPER_CANVAS_TEMPLATE.roundness;
      ea.style.opacity = 100;
      const backgroundId = ea.addRect(
        PAPER_CANVAS_TEMPLATE.sectionX,
        section.boxY,
        PAPER_CANVAS_TEMPLATE.sectionWidth,
        PAPER_CANVAS_TEMPLATE.sectionHeight,
        annotationElementId(parentItemKey, `template-${section.key}-background`),
      );
      this.tagPaperTemplateElement(ea, backgroundId, parentItemKey, "template-section-background", section.key);
    }
    for (const section of SPECIAL_ANNOTATION_SECTIONS) {
      this.addSpecialAnnotationSection(ea, parentItemKey, section);
    }
  }

  private addSpecialAnnotationSection(
    ea: ExcalidrawAutomateLike,
    parentItemKey: string,
    section: SpecialAnnotationSection,
  ): string {
    ea.style.strokeColor = PAPER_CANVAS_TEMPLATE.strokeColor;
    ea.style.backgroundColor = section.backgroundColor;
    ea.style.fillStyle = "solid";
    ea.style.strokeWidth = PAPER_CANVAS_TEMPLATE.strokeWidth;
    ea.style.roughness = 1;
    ea.style.roundness = PAPER_CANVAS_TEMPLATE.roundness;
    ea.style.opacity = 100;
    const id = ea.addRect(
      section.x,
      section.y,
      section.width,
      section.height,
      annotationElementId(parentItemKey, `template-${section.key}-background`),
    );
    this.tagPaperTemplateElement(ea, id, parentItemKey, "template-special-background", section.key);
    return id;
  }

  private tagPaperTemplateElement(
    ea: ExcalidrawAutomateLike,
    id: string,
    parentItemKey: string,
    role: string,
    sectionKey: string,
  ): void {
    ea.addAppendUpdateCustomData(id, {
      [PLUGIN_DATA_KEY]: {
        schemaVersion: 2,
        parentItemKey,
        role,
        sectionKey,
      },
    });
  }

  private setInitialViewport(view: ExcalidrawViewLike): void {
    view.excalidrawAPI?.updateScene?.({
      appState: PAPER_CANVAS_TEMPLATE.initialViewport,
    });
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
    const specialSection = specialAnnotationSection(item.source.color);
    if (specialSection) return this.addSpecialAnnotationRow(ea, scene, item, specialSection);

    const placement = placementOverride ?? this.nextPlacement(ea, scene, item);
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
        ANNOTATION_LAYOUT.cardWidth,
        estimatedHeight,
        annotationElementId(item.annotationKey, role === "comment" ? "cb" : "sb"),
      );
      const background = ea.getElement(backgroundId);
      this.tag(ea, backgroundId, item.annotationKey, `${role}-background`, {
        annotationColor: item.source.color,
      });

      ea.style.opacity = 100;
      ea.style.strokeColor = "#1e1e1e";
      ea.style.backgroundColor = "transparent";
      ea.style.fontSize = SYNCED_TEXT_FONT_SIZE;
      ea.style.fontFamily = HANDWRITING_FONT_FAMILY;
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
      this.tag(ea, textId, item.annotationKey, `${role}-text`, {
        annotationColor: item.source.color,
        zoteroLink: item.zoteroLink,
      });
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
      this.tag(ea, imageId, item.annotationKey, "source-image", {
        annotationColor: item.source.color,
        zoteroLink: item.zoteroLink,
      });
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

  private addSpecialAnnotationRow(
    ea: ExcalidrawAutomateLike,
    scene: ExcalidrawElementLike[],
    item: QueueItem,
    section: SpecialAnnotationSection,
  ): string[] {
    let all = this.uniqueElements([...scene, ...ea.getElements()]);
    let container = all.find((element) => {
      const data = elementSyncData(element);
      return data?.role === "template-special-background" && data?.sectionKey === section.key && !element.isDeleted;
    });
    let containerIsNew = false;
    if (!container) {
      const id = this.addSpecialAnnotationSection(ea, item.parentItemKey, section);
      container = ea.getElement(id);
      containerIsNew = true;
      all = this.uniqueElements([...all, container]);
    }

    const existingRows = all.filter((element) => {
      const data = elementSyncData(element);
      return data?.specialSectionKey === section.key
        && typeof data?.annotationKey === "string"
        && !element.isDeleted;
    });
    const rowY = existingRows.length > 0
      ? calculateBounds(existingRows).maxY + 24
      : container.y + 30;

    ea.style.opacity = 100;
    ea.style.strokeColor = "#1e1e1e";
    ea.style.backgroundColor = "transparent";
    ea.style.fontSize = SYNCED_TEXT_FONT_SIZE;
    ea.style.fontFamily = HANDWRITING_FONT_FAMILY;
    const wordId = ea.addText(
      container.x + section.wordXOffset,
      rowY,
      wrapTextForCanvas(item.source.text.trim(), section.wordWrapWidth),
      { autoResize: true, textAlign: "left" },
      annotationElementId(item.annotationKey, "s"),
    );
    const word = ea.getElement(wordId);
    word.link = item.zoteroLink;
    this.tag(ea, wordId, item.annotationKey, "source-text", {
      annotationColor: item.source.color,
      specialSectionKey: section.key,
      zoteroLink: item.zoteroLink,
    });

    const ids = [wordId];
    let rowHeight = word.height;
    const comment = item.source.comment.trim();
    if (comment) {
      const commentId = ea.addText(
        container.x + section.commentXOffset,
        rowY,
        wrapTextForCanvas(comment, section.commentWrapWidth),
        { autoResize: true, textAlign: "left" },
        annotationElementId(item.annotationKey, "c"),
      );
      const commentElement = ea.getElement(commentId);
      commentElement.link = null;
      this.tag(ea, commentId, item.annotationKey, "comment-text", {
        annotationColor: item.source.color,
        specialSectionKey: section.key,
      });
      ids.push(commentId);
      rowHeight = Math.max(rowHeight, commentElement.height);
    }

    const requiredHeight = rowY + rowHeight + 30 - container.y;
    if (requiredHeight > container.height) {
      let editable = container;
      const containerIsPending = ea.getElements().some((element) => element.id === container.id);
      if (!containerIsNew && !containerIsPending) {
        ea.copyViewElementsToEAforEditing([container]);
        editable = ea.getElement(container.id);
      }
      editable.height = requiredHeight;
      this.touch(editable);
      container.height = requiredHeight;
      if (section.key === "professional-terms") {
        this.keepSpecialSectionsSeparated(ea, scene, container.y + requiredHeight);
      }
    }

    const added = ea.getElements().filter(
      (element) => !element.isDeleted && elementSyncData(element)?.annotationKey === item.annotationKey,
    );
    scene.push(...added);
    return ids;
  }

  private keepSpecialSectionsSeparated(
    ea: ExcalidrawAutomateLike,
    scene: ExcalidrawElementLike[],
    professionalTermsBottom: number,
  ): void {
    const vocabulary = SPECIAL_ANNOTATION_SECTIONS.find((section) => section.key === "vocabulary");
    if (!vocabulary) return;
    const all = this.uniqueElements([...scene, ...ea.getElements()]);
    const targets = all.filter((element) => {
      const data = elementSyncData(element);
      return !element.isDeleted && (
        (data?.role === "template-special-background" && data?.sectionKey === vocabulary.key)
        || data?.specialSectionKey === vocabulary.key
      );
    });
    const container = targets.find(
      (element) => elementSyncData(element)?.role === "template-special-background",
    );
    if (!container) return;
    const minimumY = professionalTermsBottom + 83;
    const delta = minimumY - container.y;
    if (delta <= 0) return;

    const pendingIds = new Set(ea.getElements().map((element) => element.id));
    for (const target of targets) {
      const originalY = target.y;
      if (!pendingIds.has(target.id)) ea.copyViewElementsToEAforEditing([target]);
      const editable = ea.getElement(target.id);
      editable.y = originalY + delta;
      this.touch(editable);
      target.y = originalY + delta;
    }
  }

  private nextPlacement(
    ea: ExcalidrawAutomateLike,
    scene: ExcalidrawElementLike[],
    item: QueueItem,
  ): { x: number; y: number } {
    const all = this.uniqueElements([...scene, ...ea.getElements()]);
    const firstTemplateBox = all.find((element) => {
      const data = elementSyncData(element);
      return data?.role === "template-section-background" && data?.sectionKey === "main-work" && !element.isDeleted;
    });
    if (firstTemplateBox) {
      return nextAnnotationColumnPlacement(all, item.source.color, {
        x: firstTemplateBox.x + firstTemplateBox.width + ANNOTATION_LAYOUT.firstColumnGap,
        y: firstTemplateBox.y,
      });
    }
    const bounds = calculateBounds(scene);
    return nextAnnotationColumnPlacement(all, item.source.color, {
      x: bounds.maxX + ANNOTATION_LAYOUT.firstColumnGap,
      y: bounds.minY,
    });
  }

  private uniqueElements(elements: ExcalidrawElementLike[]): ExcalidrawElementLike[] {
    return [...new Map(elements.map((element) => [element.id, element])).values()];
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
