var ZoteroExcalidrawCanvas = {
	pluginID: "",
	menuRegistrationID: null,
	paneRegistrationID: null,
	localizationHref: "zotero-excalidraw-canvas.ftl",

	init({ id, version, rootURI }) {
		this.pluginID = id;
		Zotero.debug(`Zotero Excalidraw Canvas ${version}: starting`);
		this.menuRegistrationID = Zotero.MenuManager.registerMenu({
			menuID: "zotero-excalidraw-canvas-create-open",
			pluginID: id,
			target: "main/library/item",
			menus: [
				{
					menuType: "menuitem",
					l10nID: "zotero-excalidraw-canvas-create-open",
					icon: rootURI + "icon.svg",
					onShowing: (_event, context) => {
						context.setVisible(Boolean(this.regularItem(context.items)));
					},
					onCommand: (_event, context) => {
						void this.requestCanvas(context.items);
					},
				},
			],
		});
		this.paneRegistrationID = Zotero.ItemPaneManager.registerSection({
			paneID: "zotero-excalidraw-canvas-section",
			pluginID: id,
			header: {
				l10nID: "zotero-excalidraw-canvas-pane-header",
				icon: rootURI + "icon.svg",
			},
			sidenav: {
				l10nID: "zotero-excalidraw-canvas-pane-header",
				icon: rootURI + "icon.svg",
			},
			onRender: ({ body, item }) => {
				void this.renderItemPane(body, item);
			},
		});
	},

	onMainWindowLoad(window) {
		window.MozXULElement.insertFTLIfNeeded(this.localizationHref);
	},

	onMainWindowUnload(window) {
		window.document
			.querySelector(`[href="${this.localizationHref}"]`)
			?.remove();
	},

	shutdown() {
		for (const window of Zotero.getMainWindows()) {
			this.onMainWindowUnload(window);
		}
		if (this.menuRegistrationID) {
			Zotero.MenuManager.unregisterMenu(this.menuRegistrationID);
			this.menuRegistrationID = null;
		}
		if (this.paneRegistrationID) {
			Zotero.ItemPaneManager.unregisterSection(this.paneRegistrationID);
			this.paneRegistrationID = null;
		}
	},

	regularItem(items) {
		if (!Array.isArray(items) || items.length !== 1) return null;
		const item = items[0];
		if (item?.isRegularItem?.()) return item;
		if (!item?.isAttachment?.()) return null;
		const parentID = item.parentID || item.parentItemID;
		const parent = parentID ? Zotero.Items.get(parentID) : null;
		return parent?.isRegularItem?.() ? parent : null;
	},

	serviceURL() {
		return String(
			Zotero.Prefs.get("extensions.zotero-excalidraw-canvas.serviceURL", true)
				|| "http://127.0.0.1:27119",
		).replace(/\/$/, "");
	},

	itemPayload(item) {
		const title = String(item.getField("title") || "Untitled");
		const date = String(item.getField("date") || "");
		const creator = item.getCreators?.()[0] || {};
		return {
			parentItemKey: item.key,
			title,
			year: date.match(/\b\d{4}\b/)?.[0] || "",
			firstCreator: creator.lastName || creator.name || creator.firstName || "",
		};
	},

	async post(path, payload) {
		const response = await Zotero.HTTP.request("POST", this.serviceURL() + path, {
			body: JSON.stringify(payload),
			headers: { "Content-Type": "application/json" },
			responseType: "json",
			timeout: 10000,
		});
		return response.response;
	},

	async canvasStatus(item) {
		return this.post("/canvas-status", { parentItemKey: item.key });
	},

	obsidianURI() {
		const vaultName = String(
			Zotero.Prefs.get("extensions.zotero-excalidraw-canvas.vaultName", true)
				|| "Steins Gate",
		);
		return `obsidian://open?vault=${encodeURIComponent(vaultName)}`;
	},

	launchObsidian() {
		const uri = this.obsidianURI();
		if (Zotero.isWin) {
			const executable = String(
				Zotero.Prefs.get("extensions.zotero-excalidraw-canvas.obsidianExecutable", true)
					|| "D:\\Program Files\\Obsidian\\Obsidian.exe",
			);
			try {
				void Zotero.Utilities.Internal.exec(executable, [uri]).catch((error) => Zotero.logError(error));
				return;
			} catch (error) {
				Zotero.logError(error);
				throw new Error(`Could not launch Obsidian executable: ${executable}`);
			}
		}
		if (typeof Zotero.launchURL === "function") {
			Zotero.launchURL(uri);
			return;
		}
		Services.externalProtocolService.loadURI(Services.io.newURI(uri));
	},

	async waitForService() {
		for (let attempt = 0; attempt < 60; attempt += 1) {
			try {
				await Zotero.HTTP.request("GET", this.serviceURL() + "/health", {
					responseType: "json",
					timeout: 1500,
				});
				return true;
			} catch (_error) {
				await Zotero.Promise.delay(500);
			}
		}
		return false;
	},

	async renderItemPane(body, selectedItem) {
		const XHTML = "http://www.w3.org/1999/xhtml";
		const document = body.ownerDocument;
		const container = document.createElementNS(XHTML, "div");
		const statusText = document.createElementNS(XHTML, "div");
		const button = document.createElementNS(XHTML, "button");
		container.style.cssText = "display:flex;flex-direction:column;gap:8px;padding:8px 12px 12px;";
		statusText.style.cssText = "font-size:0.95em;overflow-wrap:anywhere;";
		button.type = "button";
		button.style.cssText = "align-self:flex-start;padding:5px 12px;";
		container.append(statusText, button);
		body.replaceChildren(container);

		const item = this.regularItem([selectedItem]);
		if (!item) {
			statusText.textContent = this.isChinese() ? "请选择文献条目或其 PDF 附件" : "Select a library item or its PDF attachment";
			button.textContent = this.isChinese() ? "不可用" : "Unavailable";
			button.disabled = true;
			return;
		}

		statusText.textContent = this.isChinese() ? "正在查询画布关联…" : "Checking canvas link…";
		button.textContent = this.isChinese() ? "请稍候…" : "Please wait…";
		button.disabled = true;
		try {
			const status = await this.canvasStatus(item);
			if (!body.contains(container)) return;
			statusText.textContent = status.mapped
				? (this.isChinese() ? `已关联：${status.canvasPath}` : `Linked: ${status.canvasPath}`)
				: (this.isChinese() ? "尚未关联画布" : "No canvas linked yet");
			button.textContent = status.mapped
				? (this.isChinese() ? "打开对应画布" : "Open linked canvas")
				: (this.isChinese() ? "创建并关联画布" : "Create and link canvas");
			button.disabled = false;
			button.addEventListener("click", async () => {
				button.disabled = true;
				statusText.textContent = this.isChinese() ? "正在发送到 Obsidian…" : "Sending to Obsidian…";
				const sent = await this.requestCanvas([item]);
				if (!sent) {
					button.disabled = false;
					return;
				}
				statusText.textContent = this.isChinese()
					? "请求已发送；Obsidian 将创建或打开对应画布"
					: "Request sent; Obsidian will create or open the linked canvas";
			});
		} catch (error) {
			Zotero.logError(error);
			statusText.textContent = this.isChinese()
				? "Obsidian 当前未运行"
				: "Obsidian is not currently running";
			button.textContent = this.isChinese() ? "启动 Obsidian 并打开画布" : "Start Obsidian and open canvas";
			button.disabled = false;
			button.addEventListener("click", async () => {
				button.disabled = true;
				statusText.textContent = this.isChinese() ? "正在启动 Obsidian…" : "Starting Obsidian…";
				const sent = await this.requestCanvas([item]);
				if (!sent) button.disabled = false;
			}, { once: true });
		}
	},

	async requestCanvas(items) {
		const item = this.regularItem(items);
		if (!item) return false;
		const payload = this.itemPayload(item);
		try {
			try {
				await this.post("/canvas-request", payload);
			} catch (_serviceError) {
				this.launchObsidian();
				this.notify(
					"Obsidian",
					this.isChinese() ? "正在启动并连接本地画布服务…" : "Starting and connecting to the local canvas service…",
				);
				if (!(await this.waitForService())) {
					throw new Error(this.isChinese() ? "Obsidian 启动或插件服务连接超时" : "Obsidian startup or plugin service timed out");
				}
				await this.post("/canvas-request", payload);
			}
			// Only focus the vault here. Opening an .excalidraw.md URI directly races
			// with Excalidraw's view switch and can leave the file in Markdown mode.
			this.launchObsidian();
			this.notify(
				"Excalidraw",
				this.isChinese()
					? "正在 Obsidian 当前主标签中打开画布"
					: "Opening the canvas in Obsidian's active main tab",
			);
			return true;
		} catch (error) {
			Zotero.logError(error);
			const message = this.isChinese()
				? `无法启动或连接 Obsidian：${error.message || error}`
				: `Could not start or connect to Obsidian: ${error.message || error}`;
			Services.prompt.alert(null, "Zotero Excalidraw", message);
			return false;
		}
	},

	notify(headline, description) {
		try {
			const progress = new Zotero.ProgressWindow();
			progress.changeHeadline(headline);
			progress.addDescription(description);
			progress.show();
			progress.startCloseTimer(4000);
		} catch (_error) {
			Services.prompt.alert(null, headline, description);
		}
	},

	isChinese() {
		return String(Zotero.locale || "").toLowerCase().startsWith("zh");
	},
};
