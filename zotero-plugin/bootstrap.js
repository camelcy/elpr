var ZoteroExcalidrawCanvas;

function install() {}

function uninstall() {}

async function startup({ id, version, rootURI }) {
	await Zotero.initializationPromise;
	Services.scriptloader.loadSubScript(rootURI + "zotero-excalidraw-canvas.js");
	for (const window of Zotero.getMainWindows()) {
		ZoteroExcalidrawCanvas.onMainWindowLoad(window);
	}
	ZoteroExcalidrawCanvas.init({ id, version, rootURI });
}

function onMainWindowLoad({ window }) {
	ZoteroExcalidrawCanvas?.onMainWindowLoad(window);
}

function onMainWindowUnload({ window }) {
	ZoteroExcalidrawCanvas?.onMainWindowUnload(window);
}

function shutdown() {
	ZoteroExcalidrawCanvas?.shutdown();
	ZoteroExcalidrawCanvas = undefined;
}
