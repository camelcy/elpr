import { App, PluginSettingTab, Setting } from "obsidian";
import type ZoteroExcalidrawSyncPlugin from "./main";

export class SyncSettingTab extends PluginSettingTab {
  constructor(app: App, private readonly plugin: ZoteroExcalidrawSyncPlugin) {
    super(app, plugin);
  }

  display(): void {
    const { containerEl } = this;
    containerEl.empty();
    new Setting(containerEl).setName("Zotero Excalidraw Sync").setHeading();

    const status = containerEl.createDiv({ cls: "zotero-excalidraw-sync-status", text: "尚未检查本地服务" });
    new Setting(containerEl)
      .setName("本地服务状态")
      .setDesc("只连接 127.0.0.1，不上传批注内容。")
      .addButton((button) =>
        button.setButtonText("检查").onClick(async () => {
          status.setText(await this.plugin.statusText());
        }),
      );

    new Setting(containerEl)
      .setName("自动同步")
      .setDesc("Obsidian 打开时按间隔读取队列并写入绑定画布。")
      .addToggle((toggle) =>
        toggle.setValue(this.plugin.settings.autoSync).onChange(async (value) => {
          this.plugin.settings.autoSync = value;
          await this.plugin.savePluginSettings();
          this.plugin.restartTimer();
        }),
      );

    new Setting(containerEl)
      .setName("插件轮询间隔（秒）")
      .addText((text) =>
        text.setValue(String(this.plugin.settings.pluginPollSeconds)).onChange(async (value) => {
          const parsed = Number.parseInt(value, 10);
          if (Number.isFinite(parsed)) {
            this.plugin.settings.pluginPollSeconds = Math.max(3, parsed);
            await this.plugin.savePluginSettings();
            this.plugin.restartTimer();
          }
        }),
      );

    new Setting(containerEl)
      .setName("正文显示顺序")
      .setDesc("只影响首次导入；之后不会覆盖画布中的人工编辑。")
      .addDropdown((dropdown) =>
        dropdown
          .addOption("comment-text", "评论/翻译在前，原文在后")
          .addOption("text-comment", "原文在前，评论/翻译在后")
          .setValue(this.plugin.settings.displayOrder)
          .onChange(async (value) => {
            this.plugin.settings.displayOrder = value === "text-comment" ? "text-comment" : "comment-text";
            await this.plugin.savePluginSettings();
          }),
      );

    new Setting(containerEl)
      .setName("自动启动本地服务")
      .addToggle((toggle) =>
        toggle.setValue(this.plugin.settings.autoStartService).onChange(async (value) => {
          this.plugin.settings.autoStartService = value;
          await this.plugin.savePluginSettings();
        }),
      );

    this.pathSetting(containerEl, "Python 路径", "python.exe", "pythonPath");
    this.pathSetting(containerEl, "服务脚本", "D:\\elpr\\service.py", "serviceScriptPath");
    this.pathSetting(containerEl, "服务配置", "D:\\elpr\\config.json", "serviceConfigPath");

    new Setting(containerEl)
      .setName("Zotero 文献画布目录")
      .setDesc("从 Zotero 一键创建的 Excalidraw 画布保存位置，相对于当前仓库。")
      .addText((text) =>
        text.setValue(this.plugin.settings.canvasFolder).onChange(async (value) => {
          this.plugin.settings.canvasFolder = value.trim() || "Excalidraw/Literature";
          await this.plugin.savePluginSettings();
        }),
      );

    new Setting(containerEl)
      .setName("服务地址")
      .setDesc("默认仅监听本机回环地址。")
      .addText((text) =>
        text.setValue(this.plugin.settings.serviceUrl).onChange(async (value) => {
          this.plugin.settings.serviceUrl = value.trim();
          await this.plugin.savePluginSettings();
        }),
      );
  }

  private pathSetting(
    container: HTMLElement,
    name: string,
    placeholder: string,
    key: "pythonPath" | "serviceScriptPath" | "serviceConfigPath",
  ): void {
    new Setting(container).setName(name).addText((text) =>
      text
        .setPlaceholder(placeholder)
        .setValue(this.plugin.settings[key])
        .onChange(async (value) => {
          this.plugin.settings[key] = value.trim();
          await this.plugin.savePluginSettings();
        }),
    );
  }
}
