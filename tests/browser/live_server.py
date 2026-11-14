"""Servidor HTTP descartavel para Chromium: loopback, porta efemera, sem proxy headers."""

import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

import uvicorn
from fastapi import FastAPI
from panel_rig import PanelRig


@dataclass(frozen=True)
class LiveServer:
    """Origem HTTP e aparelho com os servicos reais sobre dados sinteticos."""

    base_url: str
    rig: PanelRig


@contextmanager
def run_live_server(app: FastAPI) -> Iterator[str]:
    """Abre so loopback e encerra sempre a thread, incluindo falhas do teste."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        url = f"http://127.0.0.1:{sock.getsockname()[1]}"
        server = uvicorn.Server(
            uvicorn.Config(app, proxy_headers=False, server_header=False, log_level="warning")
        )
        thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started:
                if not thread.is_alive() or time.monotonic() > deadline:
                    raise RuntimeError("painel de teste nao arrancou em 10 s")
                time.sleep(0.01)
            yield url
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            if thread.is_alive():
                raise RuntimeError("painel de teste nao encerrou em 10 s")
