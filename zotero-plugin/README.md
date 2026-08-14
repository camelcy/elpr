# Zotero Excalidraw Canvas

Select one regular Zotero item (or its PDF attachment), then use the **Excalidraw Canvas** item-pane section or right-click and choose **Create/Open Excalidraw Canvas**.

The pane shows whether the item is linked and provides a create/open button. On Windows it directly starts the configured Obsidian executable with the vault URI, bypassing Zotero's external-protocol confirmation dialog; other platforms use the registered protocol handler. It deliberately leaves the file parameter to the Excalidraw plugin so `.excalidraw.md` files never race with Obsidian's Markdown opener. The plugin sends only the selected item's key, title, year, and first creator to the local service at `127.0.0.1:27119`. Obsidian creates or opens the canvas in the active main tab without splitting the workspace, adds a linked paper title, binds it to the Zotero item, and imports pending annotations.
