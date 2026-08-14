from __future__ import annotations

import json
import logging
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from .config import ServiceConfig
from .sync import SyncEngine


class SyncHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], engine: SyncEngine) -> None:
        super().__init__(address, SyncRequestHandler)
        self.engine = engine


class SyncRequestHandler(BaseHTTPRequestHandler):
    server: SyncHTTPServer

    def log_message(self, format: str, *args: Any) -> None:
        logging.getLogger("zotero-excalidraw-sync.http").debug(format, *args)

    def _json(self, status: HTTPStatus, value: Any) -> None:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _payload(self) -> dict[str, Any]:
        size = int(self.headers.get("Content-Length", "0"))
        if size <= 0:
            return {}
        return json.loads(self.rfile.read(size).decode("utf-8"))

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            if path == "/health":
                self._json(HTTPStatus.OK, {"ok": True, **self.server.engine.state.summary()})
            elif path == "/queue":
                self._json(HTTPStatus.OK, {"items": self.server.engine.queue()})
            elif path == "/canvas-requests":
                self._json(HTTPStatus.OK, {"items": self.server.engine.canvas_requests()})
            elif path == "/state":
                self._json(HTTPStatus.OK, self.server.engine.state.summary())
            else:
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
        except Exception as error:
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"{type(error).__name__}: {error}"})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            payload = self._payload()
            if path == "/sync":
                result = self.server.engine.sync()
            elif path == "/ack":
                result = self.server.engine.acknowledge(payload)
            elif path == "/bind":
                result = self.server.engine.bind(str(payload.get("parentItemKey", "")), str(payload.get("canvasPath", "")))
            elif path == "/reimport":
                result = self.server.engine.reimport(str(payload.get("annotationKey", "")))
            elif path == "/canvas-request":
                result = self.server.engine.request_canvas(payload)
            elif path == "/canvas-status":
                result = self.server.engine.canvas_status(str(payload.get("parentItemKey", "")))
            elif path == "/canvas-request/ack":
                result = self.server.engine.acknowledge_canvas_request(payload)
            else:
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                return
            self._json(HTTPStatus.OK, result)
        except (KeyError, ValueError) as error:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
        except Exception as error:
            logging.getLogger("zotero-excalidraw-sync").exception("request failed path=%s", path)
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"{type(error).__name__}: {error}"})


class Poller:
    def __init__(self, engine: SyncEngine, interval_seconds: int) -> None:
        self.engine = engine
        self.interval_seconds = interval_seconds
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, name="zotero-sync-poller", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=2)

    def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                self.engine.sync()
            except Exception as error:
                logging.getLogger("zotero-excalidraw-sync").warning(
                    "poll failed error=%s message=%s", type(error).__name__, error
                )
            self.stop_event.wait(self.interval_seconds)


def run_server(config: ServiceConfig) -> None:
    config.data_path.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[logging.FileHandler(config.log_file, encoding="utf-8"), logging.StreamHandler()],
    )
    engine = SyncEngine(config)
    server = SyncHTTPServer((config.listen_host, config.listen_port), engine)
    poller = Poller(engine, config.poll_interval_seconds)
    poller.start()
    logging.getLogger("zotero-excalidraw-sync").info(
        "service listening host=%s port=%s", config.listen_host, config.listen_port
    )
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        poller.stop()
        server.server_close()
