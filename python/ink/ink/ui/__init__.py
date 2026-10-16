"""Ink Local UI Console.

Zero-dependency local dashboard and trace inspector running strictly on 127.0.0.1.
Never a runtime serving dependency.
"""

from .server import InkServer, InkUIHandler, redact_payload, start_server

__all__ = ["start_server", "InkServer", "InkUIHandler", "redact_payload"]
