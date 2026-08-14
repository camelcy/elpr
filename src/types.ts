export type AnnotationStatus =
  | "pending"
  | "ready"
  | "imported"
  | "source_updated"
  | "source_updated_notified"
  | "source_missing"
  | "link_repair"
  | "layout_repair"
  | "manually_deleted";

export interface SourceSnapshot {
  key: string;
  version: number;
  type: "highlight" | "image";
  text: string;
  comment: string;
  color: string;
  pageLabel: string;
  sortIndex: string;
  position: string;
  dateAdded: string;
  dateModified: string;
}

export interface QueueItem {
  annotationKey: string;
  attachmentKey: string;
  parentItemKey: string;
  status: AnnotationStatus;
  source: SourceSnapshot;
  sourceHash: string;
  canvasPath: string;
  imagePath?: string;
  zoteroLink: string;
  elementIds?: string[];
  layoutSchemaVersion?: number;
}

export interface QueueResponse {
  items: QueueItem[];
}

export interface ServiceStatus {
  ok?: boolean;
  lastLibraryVersion: number;
  trackedAnnotations: number;
  counts: Record<string, number>;
}

export interface CanvasRequestItem {
  requestId: string;
  parentItemKey: string;
  title: string;
  year: string;
  firstCreator: string;
  requestedAt: string;
  canvasPath?: string;
}

export interface CanvasRequestResponse {
  items: CanvasRequestItem[];
}

export type DisplayOrder = "comment-text" | "text-comment";

export interface SyncPluginSettings {
  serviceUrl: string;
  autoStartService: boolean;
  autoSync: boolean;
  pluginPollSeconds: number;
  pythonPath: string;
  serviceScriptPath: string;
  serviceConfigPath: string;
  displayOrder: DisplayOrder;
  canvasFolder: string;
}
