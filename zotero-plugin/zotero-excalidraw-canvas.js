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
		const title = String(item.getField("title") || "");
		const date = String(item.getField("date") || "");
		const creators = item.getCreators?.() || [];
		const authors = creators
			.filter((creator) => this.isAuthorCreator(creator))
			.map((creator) => String(
				creator.name || [creator.firstName, creator.lastName].filter(Boolean).join(" "),
			).replace(/\s+/g, " ").trim())
			.filter(Boolean);
		const creator = creators[0] || {};
		return {
			parentItemKey: item.key,
			title,
			year: date.match(/\b\d{4}\b/)?.[0] || "",
			firstCreator: creator.lastName || creator.name || creator.firstName || "",
			authors,
			citekey: this.citationKey(item),
			doi: String(item.getField("DOI") || "").trim(),
		};
	},

	isAuthorCreator(creator) {
		if (!creator?.creatorTypeID || !Zotero.CreatorTypes?.getName) return true;
		try {
			return Zotero.CreatorTypes.getName(creator.creatorTypeID) === "author";
		} catch (_error) {
			return true;
		}
	},

	citationKey(item) {
		try {
			const direct = item.getField("citationKey");
			if (direct) return String(direct).trim();
		} catch (_error) {
			// citationKey is a virtual field supplied by some Zotero extensions.
		}
		try {
			const betterBibTeX = Zotero.BetterBibTeX?.KeyManager?.get?.(item.id)?.citationKey;
			if (typeof betterBibTeX === "string" && betterBibTeX.trim()) return betterBibTeX.trim();
		} catch (_error) {
			// Better BibTeX is optional.
		}
		const extra = String(item.getField("extra") || "");
		return extra.match(/^Citation Key:\s*(.+)$/im)?.[1]?.trim() || "";
	},

	async post(path, payload) {
		const response = await Zotero.HTTP.request("POST", this.serviceURL() + path, {
			body: JSON.stringify(payload),
			headers: { "Content-Type": "application/json" },
			responseType: "json",
			successCodes: false,
			timeout: 10000,
		});
		if (response.status < 200 || response.status >= 300) {
			const error = new Error(response.response?.error || `Local service returned HTTP ${response.status}`);
			error.status = response.status;
			throw error;
		}
		return response.response;
	},

	async canvasStatus(item) {
		return this.post("/canvas-status", { parentItemKey: item.key });
	},

	async literatureCardStatus(item) {
		return this.post("/literature-card-status", { parentItemKey: item.key });
	},

	obsidianURI(filePath = "") {
		const vaultName = String(
			Zotero.Prefs.get("extensions.zotero-excalidraw-canvas.vaultName", true)
				|| "Steins Gate",
		);
		const file = filePath ? `&file=${encodeURIComponent(filePath)}` : "";
		return `obsidian://open?vault=${encodeURIComponent(vaultName)}${file}`;
	},

	launchObsidian(filePath = "") {
		const uri = this.obsidianURI(filePath);
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
		container.style.cssText = "display:flex;flex-direction:column;gap:12px;padding:8px 12px 12px;";
		body.replaceChildren(container);
		const canvasControl = this.createPaneControl(document, XHTML, this.isChinese() ? "Excalidraw 画布" : "Excalidraw canvas");
		const cardControl = this.createPaneControl(document, XHTML, this.isChinese() ? "文献卡片" : "Literature card");
		container.append(canvasControl.container, cardControl.container);

		const item = this.regularItem([selectedItem]);
		if (!item) {
			for (const control of [canvasControl, cardControl]) {
				control.status.textContent = this.isChinese() ? "请选择文献条目或其 PDF 附件" : "Select a library item or its PDF attachment";
				control.button.textContent = this.isChinese() ? "不可用" : "Unavailable";
				control.button.disabled = true;
			}
			return;
		}

		await Promise.all([
			this.renderCanvasControl(body, container, canvasControl, item),
			this.renderLiteratureCardControl(body, container, cardControl, item),
		]);
	},

	createPaneControl(document, XHTML, label) {
		const container = document.createElementNS(XHTML, "div");
		const heading = document.createElementNS(XHTML, "div");
		const status = document.createElementNS(XHTML, "div");
		const button = document.createElementNS(XHTML, "button");
		container.style.cssText = "display:flex;flex-direction:column;gap:6px;";
		heading.style.cssText = "font-weight:600;";
		status.style.cssText = "font-size:0.95em;overflow-wrap:anywhere;";
		button.type = "button";
		button.style.cssText = "align-self:flex-start;padding:5px 12px;";
		heading.textContent = label;
		container.append(heading, status, button);
		return { container, status, button };
	},

	async renderCanvasControl(body, paneContainer, control, item) {
		control.status.textContent = this.isChinese() ? "正在查询画布关联…" : "Checking canvas link…";
		control.button.textContent = this.isChinese() ? "请稍候…" : "Please wait…";
		control.button.disabled = true;
		try {
			const status = await this.canvasStatus(item);
			if (!body.contains(paneContainer)) return;
			control.status.textContent = status.mapped
				? (this.isChinese() ? `已关联：${status.canvasPath}` : `Linked: ${status.canvasPath}`)
				: (this.isChinese() ? "尚未关联画布" : "No canvas linked yet");
			control.button.textContent = status.mapped
				? (this.isChinese() ? "打开对应画布" : "Open linked canvas")
				: (this.isChinese() ? "创建并关联画布" : "Create and link canvas");
			control.button.disabled = false;
			control.button.addEventListener("click", async () => {
				control.button.disabled = true;
				control.status.textContent = this.isChinese() ? "正在发送到 Obsidian…" : "Sending to Obsidian…";
				const sent = await this.requestCanvas([item]);
				if (!sent) {
					control.button.disabled = false;
					return;
				}
				control.status.textContent = this.isChinese()
					? "请求已发送；Obsidian 将创建或打开对应画布"
					: "Request sent; Obsidian will create or open the linked canvas";
			});
		} catch (error) {
			Zotero.logError(error);
			control.status.textContent = this.isChinese()
				? "Obsidian 当前未运行"
				: "Obsidian is not currently running";
			control.button.textContent = this.isChinese() ? "启动 Obsidian 并打开画布" : "Start Obsidian and open canvas";
			control.button.disabled = false;
			control.button.addEventListener("click", async () => {
				control.button.disabled = true;
				control.status.textContent = this.isChinese() ? "正在启动 Obsidian…" : "Starting Obsidian…";
				const sent = await this.requestCanvas([item]);
				if (!sent) control.button.disabled = false;
			}, { once: true });
		}
	},

	async renderLiteratureCardControl(body, paneContainer, control, item) {
		control.status.textContent = this.isChinese() ? "正在查找文献卡片…" : "Checking for a literature card…";
		control.button.textContent = this.isChinese() ? "请稍候…" : "Please wait…";
		control.button.disabled = true;
		try {
			const status = await this.literatureCardStatus(item);
			if (!body.contains(paneContainer)) return;
			control.status.textContent = status.exists
				? (this.isChinese() ? `已找到：${status.cardPath}` : `Found: ${status.cardPath}`)
				: (this.isChinese() ? "尚未创建文献卡片" : "No literature card yet");
			control.button.textContent = status.exists
				? (this.isChinese() ? "打开文献卡片" : "Open literature card")
				: (this.isChinese() ? "创建文献卡片" : "Create literature card");
			control.button.disabled = false;
		} catch (error) {
			Zotero.logError(error);
			const message = error.message || String(error);
			if (message.includes("多张文献卡片")) {
				control.status.textContent = message;
				control.button.textContent = this.isChinese() ? "请手动处理冲突" : "Resolve the conflict manually";
				control.button.disabled = true;
				return;
			}
			control.status.textContent = error.status
				? message
				: (this.isChinese() ? "本地服务当前不可用" : "The local service is currently unavailable");
			control.button.textContent = this.isChinese()
				? "启动 Obsidian 并创建/打开文献卡片"
				: "Start Obsidian and create/open card";
			control.button.disabled = false;
		}
		control.button.addEventListener("click", async () => {
			control.button.disabled = true;
			control.status.textContent = this.isChinese() ? "正在处理文献卡片…" : "Processing literature card…";
			const result = await this.requestLiteratureCard([item]);
			if (!result) {
				control.status.textContent = this.isChinese() ? "处理失败，请查看错误提示" : "Failed; see the error message";
				control.button.disabled = false;
				return;
			}
			control.status.textContent = this.isChinese()
				? `已在 Obsidian 中打开：${result.cardPath}`
				: `Opened in Obsidian: ${result.cardPath}`;
			control.button.textContent = this.isChinese() ? "打开文献卡片" : "Open literature card";
			control.button.disabled = false;
		});
	},

	async requestCanvas(items) {
		const item = this.regularItem(items);
		if (!item) return false;
		const payload = this.itemPayload(item);
		try {
			try {
				await this.post("/canvas-request", payload);
			} catch (serviceError) {
				if (serviceError.status) throw serviceError;
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

	async requestLiteratureCard(items) {
		const item = this.regularItem(items);
		if (!item) return null;
		const payload = this.itemPayload(item);
		try {
			let result;
			try {
				result = await this.post("/literature-card", payload);
			} catch (serviceError) {
				if (serviceError.status) throw serviceError;
				this.launchObsidian();
				this.notify(
					"Obsidian",
					this.isChinese() ? "正在启动并连接本地文献服务…" : "Starting and connecting to the local literature service…",
				);
				if (!(await this.waitForService())) {
					throw new Error(this.isChinese() ? "Obsidian 启动或插件服务连接超时" : "Obsidian startup or plugin service timed out");
				}
				result = await this.post("/literature-card", payload);
			}
			this.launchObsidian(result.cardPath);
			this.notify(
				this.isChinese() ? "文献卡片" : "Literature card",
				result.created
					? (this.isChinese() ? "已创建并在 Obsidian 中打开" : "Created and opened in Obsidian")
					: (this.isChinese() ? "已在 Obsidian 中打开" : "Opened in Obsidian"),
			);
			return result;
		} catch (error) {
			Zotero.logError(error);
			const message = this.isChinese()
				? `无法创建或打开文献卡片：${error.message || error}`
				: `Could not create or open literature card: ${error.message || error}`;
			Services.prompt.alert(null, this.isChinese() ? "Zotero 文献卡片" : "Zotero Literature Card", message);
			return null;
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
