# Zotero Excalidraw Canvas

Select one regular Zotero item (or its PDF attachment), then use the **Excalidraw Canvas** item-pane section. The existing canvas control creates or opens its bound Excalidraw canvas, while the literature-card control creates or opens the Markdown card matched by its frontmatter `zotero_key`. The canvas action remains available from the item context menu.

On Windows the plugin directly starts the configured Obsidian executable, bypassing Zotero's external-protocol confirmation dialog; other platforms use the registered protocol handler. Canvas requests deliberately leave the file parameter to the Excalidraw plugin so `.excalidraw.md` files never race with Obsidian's Markdown opener. Literature-card requests send the selected item's key, title, authors, year, and optional citation key through the same local service at `127.0.0.1:27119`, then open the returned Markdown path. Existing annotation synchronization and canvas mapping behavior are unchanged.
