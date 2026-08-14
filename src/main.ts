import { ChildProcessWithoutNullStreams, spawn } from "node:child_process";
import { appendFileSync } from "node:fs";
import { join } from "node:path";
import { App, FileSystemAdapter, Modal, Notice, Plugin, Setting, TFile, WorkspaceLeaf } from "obsidian";
import { CanvasManager } from "./canvas-manager";
import { compareQueueItems } from "./core";
import { ExcalidrawWriter } from "./excalidraw-writer";
import { ServiceClient } from "./service-client";
import { SyncSettingTab } from "./settings";
import type { QueueItem, SyncPluginSettings } from "./types";

const DEFAULT_SETTINGS: SyncPluginSettings = {
  serviceUrl: "http://127.0.0.1:27119",
  autoStartService: true,
  autoSync: true,
  pluginPollSeconds: 10,
  pythonPath: "python.exe",
  serviceScriptPath: "D:\\elpr\\service.py",
  serviceConfigPath: "D:\\elpr\\config.json",
  displayOrder: "comment-text",
  canvasFolder: "Excalidraw/Literature",
};

class TextPromptModal extends Modal {
  private value = "";

  constructor(
    app: App,
    private readonly title: string,
    private readonly placeholder: string,
    private readonly onSubmit: (value: string) => Promise<void>,
  ) {
    super(app);
  }

  onOpen(): void {
    this.setTitle(this.title);
    new Setting(this.contentEl)
      .setName(this.placeholder)
      .addText((text) =>
        text.onChange((value) => {
          this.value = value.trim();
        }),
      )
      .addButton((button) =>
        button
          .setButtonText("确定")
          .setCta()
          .onClick(async () => {
            if (!this.value) return;
            await this.onSubmit(this.value);
            this.close();
          }),
      );
  }

  onClose(): void {
    this.contentEl.empty();
  }
}

export default class ZoteroExcalidrawSyncPlugin extends Plugin {
  settings: SyncPluginSettings = DEFAULT_SETTINGS;
  private timerId?: number;
  private canvasRequestTimerId?: number;
  private syncRunning = false;
  private canvasRequestsRunning = false;
  private serviceProcess?: ChildProcessWithoutNullStreams;
  private focusedCanvasRoot?: HTMLElement;

  async onload(): Promise<void> {
    this.logRuntime("plugin onload");
    await this.loadPluginSettings();
    this.addSettingTab(new SyncSettingTab(this.app, this));
    this.addRibbonIcon("refresh-cw", "立即同步 Zotero 批注", () => void this.syncAndImport(true));

    this.addCommand({
      id: "sync-now",
      name: "立即同步 Zotero 批注",
      callback: () => void this.syncAndImport(true),
    });
    this.addCommand({
      id: "bind-current-canvas",
      name: "将当前画布绑定到 Zotero 文献",
      checkCallback: (checking) => {
        const file = this.app.workspace.getActiveFile();
        const available = file instanceof TFile && file.path.endsWith(".excalidraw.md");
        if (!checking && available && file) this.openBindPrompt(file);
        return available;
      },
    });
    this.addCommand({
      id: "reimport-annotation",
      name: "明确重新导入一条批注",
      callback: () => this.openReimportPrompt(),
    });
    this.addCommand({
      id: "show-sync-status",
      name: "显示同步状态",
      callback: async () => new Notice(await this.statusText(), 8000),
    });
    this.addCommand({
      id: "toggle-canvas-focus",
      name: "切换 Zotero 画布专注模式",
      checkCallback: (checking) => {
        const activeFile = this.app.workspace.getActiveFile();
        const available = Boolean(this.focusedCanvasRoot)
          || (activeFile instanceof TFile && activeFile.path.endsWith(".excalidraw.md"));
        if (!checking && available) {
          if (this.focusedCanvasRoot) this.clearCanvasFocus();
          else if (activeFile) void this.focusCanvas(activeFile.path);
        }
        return available;
      },
    });

    this.app.workspace.onLayoutReady(() => {
      this.logRuntime("workspace layout ready");
      void this.ensureService()
        .then(() => this.syncAndImport(false))
        .catch((error) => this.logRuntime(`startup failed: ${this.errorMessage(error)}`));
      this.restartTimer();
      this.restartCanvasRequestTimer();
    });
  }

  onunload(): void {
    if (this.timerId !== undefined) window.clearInterval(this.timerId);
    if (this.canvasRequestTimerId !== undefined) window.clearInterval(this.canvasRequestTimerId);
    this.clearCanvasFocus();
    // Keep the local bridge alive so Zotero can launch Obsidian while the app is closed.
    this.serviceProcess = undefined;
  }

  async loadPluginSettings(): Promise<void> {
    this.settings = { ...DEFAULT_SETTINGS, ...(await this.loadData()) };
  }

  async savePluginSettings(): Promise<void> {
    await this.saveData(this.settings);
  }

  restartTimer(): void {
    if (this.timerId !== undefined) window.clearInterval(this.timerId);
    this.timerId = undefined;
    if (!this.settings.autoSync) return;
    this.timerId = window.setInterval(
      () => void this.syncAndImport(false),
      Math.max(3, this.settings.pluginPollSeconds) * 1000,
    );
    this.registerInterval(this.timerId);
  }

  restartCanvasRequestTimer(): void {
    if (this.canvasRequestTimerId !== undefined) window.clearInterval(this.canvasRequestTimerId);
    this.canvasRequestTimerId = window.setInterval(() => void this.pollCanvasRequests(), 1000);
    this.registerInterval(this.canvasRequestTimerId);
  }

  private async pollCanvasRequests(): Promise<void> {
    if (this.syncRunning || this.canvasRequestsRunning) return;
    this.canvasRequestsRunning = true;
    try {
      await this.ensureService();
      await this.handleCanvasRequests(this.client());
    } catch (error) {
      this.logRuntime(`canvas request poll failed: ${this.errorMessage(error)}`);
    } finally {
      this.canvasRequestsRunning = false;
    }
  }

  async statusText(): Promise<string> {
    try {
      const status = await this.client().health();
      const counts = Object.entries(status.counts)
        .map(([key, value]) => `${key}: ${value}`)
        .join("，");
      return `服务正常；追踪 ${status.trackedAnnotations} 条；库版本 ${status.lastLibraryVersion}${counts ? `；${counts}` : ""}`;
    } catch (error) {
      return `服务未连接：${this.errorMessage(error)}`;
    }
  }

  async syncAndImport(showNotice: boolean): Promise<void> {
    if (this.syncRunning) return;
    this.syncRunning = true;
    try {
      await this.ensureService();
      const client = this.client();
      await this.handleCanvasRequests(client);
      await client.sync();
      const response = await client.queue();
      const items = response.items.sort(compareQueueItems);
      this.logRuntime(`queue received count=${items.length}`);
      const grouped = this.groupByCanvas(items);
      let imported = 0;
      for (const [canvasPath, canvasItems] of grouped) {
        const writer = new ExcalidrawWriter(this.app, this.settings.displayOrder);
        const results = await writer.write(canvasPath, canvasItems);
        for (const result of results) {
          await client.acknowledge({
            annotationKey: result.annotationKey,
            action: result.action,
            canvasPath,
            elementIds: result.elementIds,
          });
          imported += result.action === "imported" ? 1 : 0;
        }
      }
      if (showNotice) new Notice(items.length === 0 ? "没有待导入的 Zotero 批注" : `同步完成：处理 ${items.length} 条，导入/确认 ${imported} 条`);
    } catch (error) {
      console.error("[Zotero Excalidraw Sync]", error);
      this.logRuntime(`sync failed: ${this.errorMessage(error)}`);
      if (showNotice) new Notice(`同步失败：${this.errorMessage(error)}`, 8000);
    } finally {
      this.syncRunning = false;
    }
  }

  private client(): ServiceClient {
    return new ServiceClient(this.settings.serviceUrl);
  }

  private async handleCanvasRequests(client: ServiceClient): Promise<void> {
    const response = await client.canvasRequests();
    for (const request of response.items) {
      try {
        const manager = new CanvasManager(this.app, this.settings.canvasFolder);
        const canvasPath = await manager.createOrOpen(request);
        await client.acknowledgeCanvasRequest({ requestId: request.requestId, action: "completed", canvasPath });
        try {
          const writer = new ExcalidrawWriter(this.app, this.settings.displayOrder);
          await writer.ensurePaperTitle(canvasPath, request);
        } catch (error) {
          const message = this.errorMessage(error);
          this.logRuntime(`paper title link failed parent=${request.parentItemKey}: ${message}`);
          new Notice(`画布已关联，但论文标题链接写入失败：${message}`, 8000);
        }
        await this.focusCanvas(canvasPath);
        new Notice(`Zotero 文献画布已就绪：${canvasPath}`, 5000);
      } catch (error) {
        await client.acknowledgeCanvasRequest({ requestId: request.requestId, action: "failed" });
        const message = this.errorMessage(error);
        this.logRuntime(`canvas request failed parent=${request.parentItemKey}: ${message}`);
        new Notice(`Zotero 文献画布创建失败：${message}`, 8000);
      }
    }
  }

  private async ensureService(): Promise<void> {
    try {
      await this.client().health();
      return;
    } catch {
      if (!this.settings.autoStartService || this.serviceProcess) throw new Error("本地同步服务不可用");
    }
    this.serviceProcess = spawn(
      this.settings.pythonPath,
      [this.settings.serviceScriptPath, "--config", this.settings.serviceConfigPath],
      { windowsHide: true, stdio: "ignore" },
    ) as ChildProcessWithoutNullStreams;
    this.serviceProcess.once("exit", () => {
      this.serviceProcess = undefined;
    });
    for (let attempt = 0; attempt < 30; attempt += 1) {
      await new Promise((resolve) => window.setTimeout(resolve, 200));
      try {
        await this.client().health();
        return;
      } catch {
        // Retry while Python imports and opens the local listener.
      }
    }
    throw new Error("本地同步服务启动超时；请查看 D:\\elpr\\data\\service.log");
  }

  private async focusCanvas(canvasPath: string): Promise<void> {
    let leaf: WorkspaceLeaf | undefined;
    for (let attempt = 0; attempt < 50; attempt += 1) {
      const activeLeaf = this.app.workspace.activeLeaf;
      const activePath = (activeLeaf?.view as { file?: TFile } | undefined)?.file?.path;
      leaf = activePath === canvasPath
        ? activeLeaf ?? undefined
        : this.app.workspace
          .getLeavesOfType("excalidraw")
          .find((candidate) => (candidate.view as { file?: TFile }).file?.path === canvasPath);
      if (leaf) break;
      await new Promise((resolve) => window.setTimeout(resolve, 100));
    }
    if (!leaf) throw new Error(`无法定位已打开的画布标签：${canvasPath}`);

    await this.app.workspace.revealLeaf(leaf);
    const viewContainer = (leaf.view as { containerEl?: HTMLElement }).containerEl;
    if (!viewContainer) throw new Error("无法定位 Excalidraw 视图容器");
    const root = viewContainer.closest<HTMLElement>(".workspace-split.mod-root");
    if (!root) throw new Error("无法定位 Obsidian 主编辑区");

    this.clearCanvasFocus();
    root.classList.add("zotero-canvas-focus-mode");
    viewContainer.classList.add("zotero-canvas-focus-target");
    for (let element: HTMLElement | null = viewContainer; element; element = element.parentElement) {
      if (element.matches(".workspace-split, .workspace-tabs")) {
        element.classList.add("zotero-canvas-focus-ancestor");
      }
      if (element === root) break;
    }
    this.focusedCanvasRoot = root;
  }

  private clearCanvasFocus(): void {
    const root = this.focusedCanvasRoot;
    if (!root) return;
    root.classList.remove("zotero-canvas-focus-mode");
    root.querySelectorAll(".zotero-canvas-focus-ancestor, .zotero-canvas-focus-target")
      .forEach((element) => {
        element.classList.remove("zotero-canvas-focus-ancestor", "zotero-canvas-focus-target");
      });
    this.focusedCanvasRoot = undefined;
  }

  private groupByCanvas(items: QueueItem[]): Map<string, QueueItem[]> {
    const grouped = new Map<string, QueueItem[]>();
    for (const item of items) {
      if (!item.canvasPath) continue;
      const group = grouped.get(item.canvasPath) ?? [];
      group.push(item);
      grouped.set(item.canvasPath, group);
    }
    return grouped;
  }

  private openBindPrompt(file: TFile): void {
    new TextPromptModal(this.app, "绑定 Zotero 文献", "输入文献 parent item key", async (parentItemKey) => {
      await this.ensureService();
      await this.client().bind(parentItemKey, file.path);
      new Notice(`已绑定 ${parentItemKey} → ${file.path}`);
      await this.syncAndImport(false);
    }).open();
  }

  private openReimportPrompt(): void {
    new TextPromptModal(this.app, "重新导入批注", "输入 annotation key", async (annotationKey) => {
      await this.ensureService();
      await this.client().reimport(annotationKey);
      new Notice(`已允许重新导入 ${annotationKey}`);
      await this.syncAndImport(false);
    }).open();
  }

  private errorMessage(error: unknown): string {
    return error instanceof Error ? error.message : String(error);
  }

  private logRuntime(message: string): void {
    try {
      const adapter = this.app.vault.adapter;
      if (!(adapter instanceof FileSystemAdapter)) return;
      const logPath = join(adapter.getBasePath(), ".obsidian", "plugins", this.manifest.id, "runtime.log");
      appendFileSync(logPath, `${new Date().toISOString()} ${message.replace(/[\r\n]+/g, " ")}\n`, "utf8");
    } catch {
      // Diagnostics must never interrupt synchronization.
    }
  }
}
