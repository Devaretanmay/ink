"""Zero-dependency local HTTP server for the Ink Console UI."""

from __future__ import annotations

import json
import logging
import re
import secrets
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Any

from .assets import INDEX_HTML

if TYPE_CHECKING:
    from ..client import Ink

logger = logging.getLogger("ink.ui.server")

REDACT_PATTERN = re.compile(r"token|secret|password|api_key|auth|email|credit|ssn", re.IGNORECASE)


def redact_payload(obj: Any) -> Any:
    """Masks potentially sensitive keys in JSON structures for privacy."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if REDACT_PATTERN.search(str(k)):
                out[k] = "[REDACTED]"
            else:
                out[k] = redact_payload(v)
        return out
    elif isinstance(obj, list):
        return [redact_payload(item) for item in obj]
    return obj


class InkUIHandler(BaseHTTPRequestHandler):
    """Handles API queries and static console views on localhost."""

    server_version = "InkUI/0.7.1"

    @property
    def client(self) -> Ink:
        return self.server.client  # type: ignore

    def log_message(self, format: str, *args: Any) -> None:
        logger.debug(format, *args)

    def _check_request_security(self) -> bool:
        """Validates Host and Origin headers to protect against DNS rebinding and cross-site requests."""
        host_header = self.headers.get("Host", "")
        host_name = host_header.split(":")[0].lower() if host_header else ""
        server_host = self.server.server_address[0].lower()
        allowed_hosts = {"127.0.0.1", "localhost", server_host}
        if not host_name or host_name not in allowed_hosts:
            self._send_json(
                {"error": "Forbidden: Invalid Host header (DNS rebinding protection)"},
                status=403,
            )
            return False

        origin = self.headers.get("Origin")
        if origin:
            parsed_origin = urllib.parse.urlparse(origin)
            origin_host = (parsed_origin.hostname or "").lower()
            origin_port = parsed_origin.port or (
                443 if parsed_origin.scheme == "https" else (80 if parsed_origin.scheme == "http" else None)
            )
            server_port = self.server.server_address[1]
            if origin_host not in allowed_hosts or (origin_port is not None and origin_port != server_port):
                self._send_json(
                    {"error": "Forbidden: Cross-origin request denied (port/host mismatch)"},
                    status=403,
                )
                return False
        return True

    def _send_json(self, data: Any, status: int = 200) -> None:
        body = json.dumps(data, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        origin = self.headers.get("Origin")
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Ink-Console")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        if not self._check_request_security():
            return
        origin = self.headers.get("Origin")
        self.send_response(204)
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Ink-Console")
        self.end_headers()

    def do_GET(self) -> None:
        if not self._check_request_security():
            return
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if path in ("/", "/index.html"):
            body = INDEX_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/api/health":
            self._send_json({"status": "ok", "db": str(self.client.store.path)})
            return

        if path == "/api/overview":
            try:
                overview = self.client.overview()
                self._send_json(overview)
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=500)
            return

        if path == "/api/sites":
            try:
                sites = self.client.sites()
                # Enrich with serving policy
                for s in sites:
                    try:
                        s["serving_policy"] = self.client.get_serving_policy(s["name"])
                    except Exception:
                        s["serving_policy"] = {
                            "mode": "ACTIVE",
                            "canary_rate": 1.0,
                            "updated_at": 0,
                        }
                self._send_json(sites)
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=500)
            return

        if path.startswith("/api/sites/"):
            site_name = path[len("/api/sites/") :]
            try:
                data = self.client.inspect(site_name)
                data["serving_policy"] = self.client.get_serving_policy(site_name)
                self._send_json(data)
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=404)
            return

        if path == "/api/decisions":
            site = query.get("site", [None])[0]
            source = query.get("source", [None])[0]
            reason = query.get("reason", [None])[0]
            has_outcome_raw = query.get("has_outcome", [None])[0]
            has_outcome = (has_outcome_raw == "1") if has_outcome_raw in ("1", "0") else None
            limit = int(query.get("limit", [50])[0])
            offset = int(query.get("offset", [0])[0])
            is_unredacted = (
                query.get("raw", ["0"])[0] in ("1", "true")
                or query.get("unredacted", ["0"])[0] in ("1", "true")
                or query.get("redact", ["1"])[0] in ("0", "false")
            )

            try:
                rows, total = self.client.query_decisions(
                    site=site,
                    source=source,
                    reason=reason,
                    has_outcome=has_outcome,
                    limit=limit,
                    offset=offset,
                )
                if not is_unredacted:
                    for r in rows:
                        for k in ("state", "prediction", "outcome"):
                            if k in r and r[k] is not None:
                                r[k] = redact_payload(r[k])
                self._send_json(
                    {"decisions": rows, "total": total, "limit": limit, "offset": offset}
                )
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=500)
            return

        if path.startswith("/api/decisions/"):
            decision_id = path[len("/api/decisions/") :]
            is_unredacted = (
                query.get("raw", ["0"])[0] in ("1", "true")
                or query.get("unredacted", ["0"])[0] in ("1", "true")
                or query.get("redact", ["1"])[0] in ("0", "false")
            )
            try:
                row = self.client.store.get_decision(decision_id)
                if not row:
                    self._send_json({"error": "Decision not found"}, status=404)
                    return
                # Attach outcome and receipt
                outcome_row = self.client.store.get_outcome(decision_id)
                receipt = (
                    self.client.receipt(decision_id) if row.get("source") == "fast_path" else {}
                )

                state_val = json.loads(row["state"]) if row.get("state") else None
                pred_val = json.loads(row["prediction"]) if row.get("prediction") else None
                out_val = (
                    json.loads(outcome_row["payload"])
                    if outcome_row and outcome_row.get("payload")
                    else None
                )

                if not is_unredacted:
                    if state_val is not None:
                        state_val = redact_payload(state_val)
                    if pred_val is not None:
                        pred_val = redact_payload(pred_val)
                    if out_val is not None:
                        out_val = redact_payload(out_val)

                resp = dict(row)
                resp["state"] = state_val
                resp["prediction"] = pred_val
                resp["outcome"] = out_val
                resp["receipt"] = receipt
                self._send_json(resp)
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=500)
            return

        self._send_json({"error": "Not Found"}, status=404)

    def do_POST(self) -> None:
        """Handles POST operations with strictly read-only execution invariants.

        In v0.7.0 Local Developer Workbench:
        1. /api/decisions/<id>/replay: In-memory deterministic prediction inspection
           against historical state without invoking external verifiers or side effects.
        2. /api/sites/<site>/policy: Explicitly rejected with 405 Method Not Allowed
           to keep the HTTP surface strictly read-only on localhost. Serving policies
           are managed programmatically via the Python API or local CLI.
        """
        if not self._check_request_security():
            return

        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        content_length = int(self.headers.get("Content-Length", 0))
        if content_length > 1_048_576:
            self.rfile.read(min(content_length, 2_097_152))
            self._send_json({"error": "Payload Too Large (max 1MB)"}, status=413)
            return

        if content_length > 0:
            self.rfile.read(content_length)

        # 1. Prediction Inspection (strictly read-only simulation)
        if path.startswith("/api/decisions/") and path.endswith("/replay"):
            decision_id = path[len("/api/decisions/") : -len("/replay")]
            try:
                dec = self.client.store.get_decision(decision_id)
                if not dec:
                    self._send_json({"error": "Decision not found"}, status=404)
                    return
                state = json.loads(dec["state"]) if dec.get("state") else {}
                site_version = dec["site"]
                site = self.client._resolve(site_version)

                active = self.client.store.get_active_artifact(site.version)
                if not active:
                    self._send_json(
                        {
                            "error": "No active artifact qualified for this site to simulate replay against",
                            "status": "NO_ACTIVE_ARTIFACT",
                        },
                        status=400,
                    )
                    return

                eng_key = active["payload"]["engine_data"]["engine"]
                engine = self.client.engines[eng_key]
                pred_choice, raw_prob = engine.predict(active["payload"]["engine_data"], state)

                matches = pred_choice == dec["choice"]

                # Inspection-only counterfactual evaluation: evaluates deterministic in-process
                # engine prediction. External callbacks and verifiers are NOT invoked to prevent
                # external side effects (API calls, remote mutations).
                self._send_json(
                    {
                        "candidate_choice": pred_choice,
                        "raw_probability": raw_prob,
                        "historical_choice": dec["choice"],
                        "matches_historical": matches,
                        "status": "INSPECTION_REPLAY",
                        "provenance_label": "Side-Effect-Free In-Memory Prediction (Inspection Only)",
                        "side_effects_prevented": True,
                    }
                )
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=500)
            return

        # 2. Serving Policy Controls: strictly read-only over HTTP in v0.7.0
        if path.startswith("/api/sites/") and path.endswith("/policy"):
            self._send_json(
                {
                    "error": "HTTP policy mutations are disabled in v0.7.0. Configure serving policies programmatically via the Python API or CLI.",
                    "code": "HTTP_MUTATIONS_DISABLED",
                },
                status=405,
            )
            return

        self._send_json({"error": "Not Found"}, status=404)


class InkServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address,
        RequestHandlerClass,
        client: Ink,
        allow_control_writes: bool = False,
        auth_token: str | None = None,
    ):
        super().__init__(server_address, RequestHandlerClass)
        self.client = client
        self.allow_control_writes = allow_control_writes
        self.auth_token = auth_token or secrets.token_hex(16)


def start_server(
    client: Ink,
    host: str = "127.0.0.1",
    port: int = 4242,
    open_browser: bool = False,
    background: bool = False,
    allow_control_writes: bool = False,
    auth_token: str | None = None,
) -> InkServer:
    """Instantiates and starts the Ink Console HTTP server on localhost."""
    server = InkServer(
        (host, port),
        InkUIHandler,
        client=client,
        allow_control_writes=allow_control_writes,
        auth_token=auth_token,
    )
    logger.info("Ink Console running at http://%s:%d", host, port)
    if background:
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        server._thread = t
    if open_browser:
        try:
            import webbrowser

            webbrowser.open(f"http://{host}:{port}")
        except Exception:
            pass
    return server
