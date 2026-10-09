"""Local-first HTTP web server for Ink Console Alpha."""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

from .ui import HTML_PAGE

logger = logging.getLogger("ink.console")


class ConsoleHandler(BaseHTTPRequestHandler):
    db_path: str = ".ink/decisions.db"
    demo_mode: bool = False

    def log_message(self, format, *args):
        # Silence default HTTP server access logs to keep terminal clean
        pass

    def _send_json(self, data: Any, status: int = 200):
        body = json.dumps(data, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str, status: int = 200):
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _get_db(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            self._send_html(HTML_PAGE)
            return

        if path == "/api/overview":
            self._handle_overview()
        elif path == "/api/sites":
            self._handle_sites()
        elif path == "/api/artifacts":
            self._handle_artifacts()
        elif path == "/api/activity":
            self._handle_activity()
        elif path == "/api/discovery":
            self._handle_discovery()
        elif path == "/api/system":
            self._handle_system()
        else:
            self.send_error(404, "Not Found")

    def _handle_overview(self):
        if self.demo_mode or not Path(self.db_path).is_file():
            self._send_json(self._demo_overview())
            return

        try:
            conn = self._get_db()
            total_decisions = conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
            fast_served = conn.execute(
                "SELECT COUNT(*) FROM decisions WHERE source='fast_path'"
            ).fetchone()[0]
            fast_rate = fast_served / total_decisions if total_decisions > 0 else 0.0

            site_rows = conn.execute("SELECT version, name, contract FROM sites").fetchall()
            sites = []
            active_count = 0
            for s in site_rows:
                obs = conn.execute(
                    "SELECT COUNT(*) FROM decisions WHERE site=?", (s["version"],)
                ).fetchone()[0]
                outcomes = conn.execute(
                    "SELECT COUNT(*) FROM outcomes o JOIN decisions d ON o.decision = d.id WHERE d.site=?",
                    (s["version"],),
                ).fetchone()[0]
                fast_cnt = conn.execute(
                    "SELECT COUNT(*) FROM decisions WHERE site=? AND source='fast_path'",
                    (s["version"],),
                ).fetchone()[0]
                art = conn.execute(
                    "SELECT payload, status FROM artifacts WHERE site=? ORDER BY epoch DESC LIMIT 1",
                    (s["version"],),
                ).fetchone()
                status = art["status"] if art else "OBSERVE"
                eng = None
                if art and art["payload"]:
                    try:
                        eng = json.loads(art["payload"]).get("engine_data", {}).get("engine")
                    except Exception:
                        pass
                if status == "ACTIVE":
                    active_count += 1
                cov = outcomes / obs if obs > 0 else 0.0
                sites.append({
                    "name": s["name"],
                    "state": status,
                    "observations": obs,
                    "outcomes": outcomes,
                    "outcome_coverage": cov,
                    "fast_served": fast_cnt,
                    "engine": eng,
                })

            recent_rows = conn.execute(
                "SELECT d.created, s.name as site, d.source, d.choice, d.confidence, d.reason as fallback_reason "
                "FROM decisions d JOIN sites s ON d.site = s.version "
                "ORDER BY d.created DESC LIMIT 10"
            ).fetchall()
            recent = [dict(r) for r in recent_rows]

            self._send_json({
                "is_demo": False,
                "total_decisions": total_decisions,
                "fast_served": fast_served,
                "fast_served_rate": fast_rate,
                "estimated_latency_saved_ms": fast_served * 750.0,
                "total_sites": len(sites),
                "active_sites": active_count,
                "sites": sites,
                "recent_activity": recent,
            })
            conn.close()
        except Exception as exc:
            logger.exception("Error loading overview: %s", exc)
            self._send_json(self._demo_overview())

    def _demo_overview(self):
        return {
            "is_demo": True,
            "total_decisions": 1420,
            "fast_served": 1085,
            "fast_served_rate": 0.764,
            "estimated_latency_saved_ms": 1085 * 650.0,
            "total_sites": 3,
            "active_sites": 2,
            "sites": [
                {
                    "name": "workflow.route",
                    "state": "ACTIVE",
                    "observations": 840,
                    "outcomes": 840,
                    "outcome_coverage": 1.0,
                    "fast_served": 795,
                    "engine": "exact",
                },
                {
                    "name": "ticket.triage",
                    "state": "ACTIVE",
                    "observations": 420,
                    "outcomes": 395,
                    "outcome_coverage": 0.94,
                    "fast_served": 290,
                    "engine": "linear",
                },
                {
                    "name": "agent.supervisor",
                    "state": "SHADOW",
                    "observations": 160,
                    "outcomes": 120,
                    "outcome_coverage": 0.75,
                    "fast_served": 0,
                    "engine": "linear",
                },
            ],
            "recent_activity": [
                {
                    "created": time.time() - 2,
                    "site": "ticket.triage",
                    "source": "fast_path",
                    "choice": "billing",
                    "confidence": 0.98,
                    "fallback_reason": None,
                },
                {
                    "created": time.time() - 5,
                    "site": "workflow.route",
                    "source": "fast_path",
                    "choice": "auto_approve",
                    "confidence": 1.0,
                    "fallback_reason": None,
                },
                {
                    "created": time.time() - 9,
                    "site": "agent.supervisor",
                    "source": "fallback",
                    "choice": "delegate_research",
                    "confidence": 0.82,
                    "fallback_reason": "shadow",
                },
                {
                    "created": time.time() - 15,
                    "site": "ticket.triage",
                    "source": "fast_path",
                    "choice": "technical",
                    "confidence": 0.94,
                    "fallback_reason": None,
                },
                {
                    "created": time.time() - 21,
                    "site": "workflow.route",
                    "source": "fallback",
                    "choice": "manual_review",
                    "confidence": None,
                    "fallback_reason": "comparison",
                },
            ],
        }

    def _handle_sites(self):
        if self.demo_mode or not Path(self.db_path).is_file():
            self._send_json([
                {
                    "name": "workflow.route",
                    "description": "High-volume deterministic approval & routing gate",
                    "state": "ACTIVE",
                    "choices": ["auto_approve", "manual_review", "reject"],
                    "schema": {"account_tier": "string", "amount": "number", "risk_flag": "boolean"},
                    "observations": 840,
                    "outcomes": 840,
                    "blocker": "none",
                },
                {
                    "name": "ticket.triage",
                    "description": "Natural language customer support ticket routing",
                    "state": "ACTIVE",
                    "choices": ["billing", "technical", "account", "general"],
                    "schema": {"subject": "string", "message": "string", "priority": "string"},
                    "observations": 420,
                    "outcomes": 395,
                    "blocker": "none",
                },
                {
                    "name": "agent.supervisor",
                    "description": "Multi-agent graph supervisor delegation boundary",
                    "state": "SHADOW",
                    "choices": ["delegate_coder", "delegate_research", "finish"],
                    "schema": {"task": "string", "iterations": "integer"},
                    "observations": 160,
                    "outcomes": 120,
                    "blocker": "accumulating_shadow_evidence",
                },
            ])
            return

        try:
            conn = self._get_db()
            rows = conn.execute("SELECT version, name, contract FROM sites").fetchall()
            results = []
            for r in rows:
                c = json.loads(r["contract"])
                obs = conn.execute(
                    "SELECT COUNT(*) FROM decisions WHERE site=?", (r["version"],)
                ).fetchone()[0]
                outcomes = conn.execute(
                    "SELECT COUNT(*) FROM outcomes o JOIN decisions d ON o.decision = d.id WHERE d.site=?",
                    (r["version"],),
                ).fetchone()[0]
                art = conn.execute(
                    "SELECT status FROM artifacts WHERE site=? ORDER BY created DESC LIMIT 1",
                    (r["version"],),
                ).fetchone()
                status = art["status"] if art else "OBSERVE"
                results.append({
                    "name": r["name"],
                    "description": c.get("description", ""),
                    "state": status,
                    "choices": c.get("choices", []),
                    "schema": c.get("state_schema", {}),
                    "observations": obs,
                    "outcomes": outcomes,
                    "blocker": "none" if status == "ACTIVE" else "shadow_qualification",
                })
            conn.close()
            self._send_json(results)
        except Exception as exc:
            logger.exception("Error loading sites: %s", exc)
            self._send_json([])

    def _handle_artifacts(self):
        if self.demo_mode or not Path(self.db_path).is_file():
            self._send_json([
                {
                    "id": "art_exact_98ef12ba09cd81",
                    "site_name": "workflow.route",
                    "engine": "exact",
                    "status": "ACTIVE",
                    "active_regions_count": 18,
                    "epoch": 2,
                },
                {
                    "id": "art_linear_77ac32be04ff62",
                    "site_name": "ticket.triage",
                    "engine": "linear",
                    "status": "ACTIVE",
                    "active_regions_count": 6,
                    "epoch": 1,
                },
            ])
            return

        try:
            conn = self._get_db()
            rows = conn.execute(
                "SELECT a.id, s.name as site_name, a.payload, a.status, a.epoch, a.evidence "
                "FROM artifacts a JOIN sites s ON a.site = s.version "
                "WHERE a.status != 'RETIRED' ORDER BY a.epoch DESC"
            ).fetchall()
            res = []
            for r in rows:
                ev = {}
                if r["evidence"]:
                    try:
                        ev = json.loads(r["evidence"])
                    except Exception:
                        pass
                eng = "exact"
                if r["payload"]:
                    try:
                        eng = json.loads(r["payload"]).get("engine_data", {}).get("engine", "exact")
                    except Exception:
                        pass
                active_regs = len(ev.get("qualified_semantic_regions", []))
                res.append({
                    "id": r["id"],
                    "site_name": r["site_name"],
                    "engine": eng,
                    "status": r["status"],
                    "active_regions_count": active_regs,
                    "epoch": r["epoch"],
                })
            conn.close()
            self._send_json(res)
        except Exception as exc:
            logger.exception("Error loading artifacts: %s", exc)
            self._send_json([])

    def _handle_activity(self):
        if self.demo_mode or not Path(self.db_path).is_file():
            self._send_json(self._demo_overview()["recent_activity"])
            return

        try:
            conn = self._get_db()
            rows = conn.execute(
                "SELECT d.created, s.name as site, d.source, d.choice, d.confidence, d.reason as fallback_reason "
                "FROM decisions d JOIN sites s ON d.site = s.version "
                "ORDER BY d.created DESC LIMIT 50"
            ).fetchall()
            conn.close()
            self._send_json([dict(r) for r in rows])
        except Exception as exc:
            logger.exception("Error loading activity: %s", exc)
            self._send_json([])

    def _handle_discovery(self):
        self._send_json([
            {
                "name": "workflow.route",
                "unique_patterns": 24,
                "volume": 840,
                "potential_coverage": 0.94,
                "suggested_engine": "exact",
            },
            {
                "name": "ticket.triage",
                "unique_patterns": 180,
                "volume": 420,
                "potential_coverage": 0.69,
                "suggested_engine": "linear",
            },
        ])

    def _handle_system(self):
        p = Path(self.db_path)
        exists = p.is_file()
        integrity = "ok"
        ver = 0
        sites_cnt = 0
        art_cnt = 0
        if exists:
            try:
                conn = self._get_db()
                row = conn.execute("PRAGMA integrity_check").fetchone()
                integrity = row[0] if row else "unknown"
                ver = conn.execute("PRAGMA user_version").fetchone()[0]
                sites_cnt = conn.execute("SELECT COUNT(*) FROM sites").fetchone()[0]
                art_cnt = conn.execute(
                    "SELECT COUNT(*) FROM artifacts WHERE status='ACTIVE'"
                ).fetchone()[0]
                conn.close()
            except Exception:
                integrity = "check_failed"

        neural_ok = False
        try:
            import mlx.core  # noqa: F401
            neural_ok = True
        except Exception:
            pass

        self._send_json({
            "database_path": str(p.resolve()) if exists else str(p),
            "status": "healthy" if exists or self.demo_mode else "clean_install",
            "integrity": integrity,
            "schema_version": ver,
            "sites_count": sites_cnt,
            "active_artifacts_count": art_cnt,
            "neural_available": neural_ok,
        })


def run_console(
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    db_path: str = ".ink/decisions.db",
    demo: bool = False,
):
    ConsoleHandler.db_path = db_path
    ConsoleHandler.demo_mode = demo
    server_address = (host, port)
    httpd = HTTPServer(server_address, ConsoleHandler)
    mode_str = " (DEMO MODE)" if demo else ""
    print("\n=======================================================")
    print(f"  Ink Console Alpha live at http://{host}:{port}{mode_str}")
    print(f"  Evidence Database: {db_path}")
    print("  Press Ctrl+C to stop.")
    print("=======================================================\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Ink Console...")
    finally:
        httpd.server_close()
