import { App, TFile } from "obsidian";
import { canvasBaseName } from "./core";
import type { CanvasRequestItem } from "./types";

interface ExcalidrawCreator {
  reset(): void;
  create(options: {
    filename: string;
    foldername: string;
    onNewPane: boolean;
    silent?: boolean;
  }): Promise<string>;
}

interface ExcalidrawPluginLike {
  isReady?: boolean;
  ea?: ExcalidrawCreator;
  openDrawing?: (file: TFile, location: string, active?: boolean) => void;
}

export class CanvasManager {
  constructor(
    private readonly app: App,
    private readonly folder: string,
  ) {}

  async createOrOpen(request: CanvasRequestItem): Promise<string> {
    const plugin = this.excalidrawPlugin();
    if (!plugin?.isReady) throw new Error("Excalidraw is still initializing");

    if (request.canvasPath) {
      const existing = this.app.vault.getAbstractFileByPath(request.canvasPath);
      if (!(existing instanceof TFile)) {
        throw new Error(`mapped canvas does not exist: ${request.canvasPath}`);
      }
      if (!plugin.openDrawing) throw new Error("Excalidraw openDrawing API is unavailable");
      plugin.openDrawing(existing, "active-pane", true);
      return existing.path;
    }

    if (!plugin.ea) throw new Error("Excalidraw Automate create API is unavailable");
    const folder = this.normalizedFolder();
    await this.ensureFolder(folder);
    const filename = this.availableFilename(folder, canvasBaseName(request), request.parentItemKey);
    plugin.ea.reset();
    return plugin.ea.create({ filename, foldername: folder, onNewPane: false, silent: false });
  }

  private excalidrawPlugin(): ExcalidrawPluginLike | undefined {
    const host = this.app as App & { plugins?: { plugins?: Record<string, unknown> } };
    return host.plugins?.plugins?.["obsidian-excalidraw-plugin"] as ExcalidrawPluginLike | undefined;
  }

  private normalizedFolder(): string {
    const folder = this.folder.replace(/\\/g, "/").replace(/^\/+|\/+$/g, "").trim();
    const normalized = folder || "Excalidraw/Literature";
    if (normalized.split("/").some((part) => part === "." || part === "..")) {
      throw new Error("canvas folder cannot contain relative path segments");
    }
    return normalized;
  }

  private async ensureFolder(folder: string): Promise<void> {
    let current = "";
    for (const part of folder.split("/").filter(Boolean)) {
      current = current ? `${current}/${part}` : part;
      if (!this.app.vault.getAbstractFileByPath(current)) await this.app.vault.createFolder(current);
    }
  }

  private availableFilename(folder: string, preferred: string, parentItemKey: string): string {
    let candidate = preferred;
    for (let collision = 0; ; collision += 1) {
      if (!this.app.vault.getAbstractFileByPath(`${folder}/${candidate}.excalidraw.md`)) return candidate;
      candidate = collision === 0
        ? `${preferred} - ${parentItemKey}`
        : `${preferred} - ${parentItemKey} (${collision + 1})`;
    }
  }
}
