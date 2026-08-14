import { requestUrl } from "obsidian";
import type { CanvasRequestResponse, QueueResponse, ServiceStatus } from "./types";

export class ServiceClient {
  constructor(private readonly baseUrl: string) {}

  async health(): Promise<ServiceStatus> {
    return this.get<ServiceStatus>("/health");
  }

  async sync(): Promise<ServiceStatus> {
    return this.post<ServiceStatus>("/sync", {});
  }

  async queue(): Promise<QueueResponse> {
    return this.get<QueueResponse>("/queue");
  }

  async canvasRequests(): Promise<CanvasRequestResponse> {
    return this.get<CanvasRequestResponse>("/canvas-requests");
  }

  async acknowledgeCanvasRequest(payload: {
    requestId: string;
    action: "completed" | "failed";
    canvasPath?: string;
  }): Promise<void> {
    await this.post("/canvas-request/ack", payload);
  }

  async acknowledge(payload: {
    annotationKey: string;
    action?: "imported" | "source-updated-notified" | "manual-delete";
    canvasPath?: string;
    elementIds?: string[];
  }): Promise<void> {
    await this.post("/ack", payload);
  }

  async bind(parentItemKey: string, canvasPath: string): Promise<void> {
    await this.post("/bind", { parentItemKey, canvasPath });
  }

  async reimport(annotationKey: string): Promise<void> {
    await this.post("/reimport", { annotationKey });
  }

  private async get<T>(path: string): Promise<T> {
    const response = await requestUrl({
      url: `${this.baseUrl.replace(/\/$/, "")}${path}`,
      method: "GET",
      throw: false,
    });
    if (response.status < 200 || response.status >= 300) {
      throw new Error(response.json?.error ?? `service returned HTTP ${response.status}`);
    }
    return response.json as T;
  }

  private async post<T = unknown>(path: string, body: unknown): Promise<T> {
    const response = await requestUrl({
      url: `${this.baseUrl.replace(/\/$/, "")}${path}`,
      method: "POST",
      contentType: "application/json",
      body: JSON.stringify(body),
      throw: false,
    });
    if (response.status < 200 || response.status >= 300) {
      throw new Error(response.json?.error ?? `service returned HTTP ${response.status}`);
    }
    return response.json as T;
  }
}
