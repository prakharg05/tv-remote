"""Local web UI for the TV remote (stdlib only).

One process holds a single persistent connection to the chosen TV (so presses are
instant) and serves ui.html on 127.0.0.1. The window can also find TVs on the network,
pair with them and switch between saved TVs. Started by `tv ui`; exits by itself shortly
after the last window closes.

Security: listens on loopback only; every request must carry a per-run random token
(header for commands, query param for the page/event stream), and the Host header must
be loopback, which blocks other websites and DNS-rebinding from driving the TV.
Pairing/connecting is limited to private (LAN) addresses.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import secrets
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from androidtvremote2 import CannotConnect, ConnectionClosed, InvalidAuth

import profiles
import tvremote as core

PREFERRED_PORT = 47821
UI_FILE = Path(__file__).with_name("ui.html")
IDLE_EXIT_SECONDS = 20      # exit this long after the last window closes
STARTUP_GRACE_SECONDS = 90  # ...or if no window ever connects
ALLOWED_KEYS = set(core.KEYS.values())


class State:
    """State pushed to the page, shared between the asyncio thread and HTTP threads."""

    def __init__(self):
        self._cond = threading.Condition()
        self.version = 0
        self.data = {"status": "setup", "power": None, "app": None, "app_name": None, "volume": None,
                     "message": None, "features": None, "info": None, "tvs": [], "current": None}

    def update(self, **changes):
        with self._cond:
            self.data.update(changes)
            self.version += 1
            self._cond.notify_all()

    def snapshot(self):
        with self._cond:
            return self.version, dict(self.data)

    def wait_change(self, version, timeout):
        with self._cond:
            self._cond.wait_for(lambda: self.version != version, timeout)
            return self.version, dict(self.data)


class Manager:
    """Owns the TV connection and pairing sessions; all TV I/O runs on the asyncio loop."""

    def __init__(self, state: State):
        self.state = state
        self.loop: asyncio.AbstractEventLoop | None = None
        self.tv_id: str | None = None
        self.tv: dict = {}
        self.profile = profiles.DEFAULT
        self.remote = None
        self.pairing: core.PairingSession | None = None

    # --- called from HTTP threads -------------------------------------------------
    def run_coro(self, coro, timeout: float = 15):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout=timeout)

    def call(self, fn_name: str, *args):
        if self.remote is None:
            raise ConnectionClosed("not connected")
        return self.run_coro(self._call(fn_name, *args), timeout=3)

    # --- loop side ----------------------------------------------------------------
    async def run(self, initial: str | None, stop: asyncio.Event):
        self.loop = asyncio.get_running_loop()
        await self.select(initial)
        await stop.wait()
        self._disconnect()
        await self.pair_cancel()

    async def _call(self, fn_name: str, *args):
        getattr(self.remote, fn_name)(*args)

    async def open(self, link: str):
        if self.remote is None:
            raise ConnectionClosed("not connected")
        await core.launch(self.remote, link)

    def _publish_tvs(self):
        config = core.load_config()
        tvs = [{"id": tv_id, "name": tv["name"], "host": tv.get("host"),
                "model": " ".join(filter(None, (tv.get("manufacturer"), tv.get("model"))))}
               for tv_id, tv in config["tvs"].items()]
        self.state.update(tvs=tvs, current=self.tv_id)

    def _info(self) -> dict:
        targets = lambda d: [{"id": k, "label": label} for k, (label, _) in d.items()]  # noqa: E731
        return {"name": self.tv.get("name", "TV"), "apps": targets(profiles.COMMON_APPS),
                "inputs": targets(self.profile["inputs"]), "input_key": self.profile["input_key"],
                "shortcuts": core.SHORTCUTS,
                "profile": self.profile["label"], "verified": self.profile is not profiles.DEFAULT}

    def _disconnect(self):
        if self.remote is not None:
            self.remote.disconnect()
            self.remote = None

    async def select(self, selector: str | None):
        """Switch to a saved TV (None = default, or setup mode if there are none)."""
        self._disconnect()
        self.state.update(power=None, app=None, app_name=None, volume=None, features=None, message=None)
        try:
            self.tv_id, self.tv = core.get_tv(selector)
        except core.TVError:
            self.tv_id, self.tv = None, {}
            self.state.update(status="setup", info=None)
            self._publish_tvs()
            return
        self.profile = core.profile_for(self.tv)
        self.state.update(status="connecting", info=self._info())
        self._publish_tvs()
        try:
            remote = await core.connect_tv(self.tv_id, self.tv)
        except InvalidAuth:
            self.state.update(status="unpaired", message="This TV forgot the pairing. Click its name to pair again.")
            return
        except core.TVError as exc:
            self.state.update(status="offline", message=str(exc))
            return
        self.remote = remote
        self.tv_id, self.tv = core.get_tv(self.tv["name"])  # may have been re-keyed or moved
        core.remember_tv(self.tv_id, {}, make_default=True)
        self.profile = core.profile_for(self.tv)  # model is known only after connecting
        ui = core.load_ui_state() or {}
        core.save_ui_state({**ui, "tv_id": self.tv_id})

        mine = lambda fn: (lambda *a: fn(*a) if self.remote is remote else None)  # noqa: E731
        remote.add_is_on_updated_callback(mine(lambda on: self.state.update(power=on)))
        remote.add_current_app_updated_callback(mine(self._on_app))
        remote.add_volume_info_updated_callback(mine(lambda v: self.state.update(volume=dict(v))))
        remote.add_is_available_updated_callback(
            mine(lambda ok: self.state.update(status="connected" if ok else "reconnecting")))
        remote.keep_reconnecting(mine(lambda: self.state.update(
            status="unpaired", message="This TV forgot the pairing. Click its name to pair again.")))
        self.state.update(status="connected", info=self._info(), features=core.features(remote),
                          power=remote.is_on, volume=dict(remote.volume_info) if remote.volume_info else None)
        if remote.current_app:
            self._on_app(remote.current_app)
        self._publish_tvs()

    async def wake(self):
        """Power button while the TV does not answer: Wake-on-LAN, then reconnect."""
        if self.tv_id is None:
            raise core.TVError("No TV selected.")
        if self.remote is not None:
            if not self.remote.is_on:  # many TVs ignore WAKEUP; POWER is a toggle
                self.remote.send_key_command("POWER")
            return
        self.state.update(status="connecting", message="Waking the TV…")
        try:
            remote, _ = await core.wake_tv(self.tv_id, self.tv, ensure_on=True)
        except core.TVError as exc:
            self.state.update(status="offline", message=str(exc))
            raise
        remote.disconnect()  # select() opens the long-lived connection with callbacks
        await self.select(self.tv_id)

    def _on_app(self, package: str):
        self.state.update(app=package, app_name=core.APP_NAMES.get(package, package))

    async def discover(self) -> list[dict]:
        saved_hosts = {tv.get("host") for tv in core.load_config()["tvs"].values()}
        found = await asyncio.to_thread(core.discover)
        return [{"name": f.name, "host": f.host, "supported": f.supported, "saved": f.host in saved_hosts}
                for f in found]

    async def pair_start(self, host: str) -> str:
        if not core.is_private_ip(host):
            raise core.TVError("Enter the TV's local IP address (e.g. 192.168.1.20).")
        await self.pair_cancel()
        try:
            self.pairing = await core.start_pairing(host)
        except (CannotConnect, ConnectionClosed, OSError):
            raise core.TVError(f"Couldn't start pairing with {host}. Is the TV on?") from None
        return self.pairing.name

    async def pair_finish(self, code: str):
        if self.pairing is None:
            raise core.TVError("Pairing expired. Start again.")
        try:
            tv_id = await core.finish_pairing(self.pairing, code)
        except InvalidAuth:
            raise core.TVError("That code doesn't match. Check the TV and try again.") from None
        except (ConnectionClosed, OSError):
            self.pairing = None
            raise core.TVError("The TV closed pairing (cancelled or timed out). Start again.") from None
        await self.pair_cancel()
        await self.select(tv_id)

    async def pair_cancel(self):
        if self.pairing is not None:
            self.pairing.remote.disconnect()
            self.pairing = None

    async def forget(self, tv_id: str):
        core.forget_tv(tv_id)
        if tv_id == self.tv_id:
            await self.select(None)
        else:
            self._publish_tvs()


class Handler(BaseHTTPRequestHandler):
    server_version = "tv-remote"
    mgr: Manager
    state: State
    token: str
    port: int
    clients: list[int]  # [open event streams]
    last_client_seen: list[float]

    def log_message(self, fmt, *args):  # keep quiet; errors still go to stderr
        pass

    # --- guards -----------------------------------------------------------
    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").lower()
        return host in (f"127.0.0.1:{self.port}", f"localhost:{self.port}")

    def _query_token(self) -> str:
        return (parse_qs(urlparse(self.path).query).get("t") or [""])[0]

    def _reject(self, code=HTTPStatus.FORBIDDEN):
        self.send_response(code)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _json(self, obj, code=HTTPStatus.OK):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    # --- routes -----------------------------------------------------------
    def do_GET(self):
        if not self._host_ok() or not secrets.compare_digest(self._query_token(), self.token):
            return self._reject()
        path = urlparse(self.path).path
        if path == "/":
            body = UI_FILE.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy",
                             "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                             "connect-src 'self'; img-src data:")
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/events":
            self._events()
        else:
            self._reject(HTTPStatus.NOT_FOUND)

    def do_POST(self):
        if not self._host_ok() or not secrets.compare_digest(self.headers.get("X-TV-Token", ""), self.token):
            return self._reject()
        mgr = self.mgr
        try:
            length = min(int(self.headers.get("Content-Length") or 0), 4096)
            body = json.loads(self.rfile.read(length) or b"{}")
            path = urlparse(self.path).path
            result = {"ok": True}
            if path == "/api/key":
                key = str(body.get("key", ""))
                if key not in ALLOWED_KEYS:
                    return self._json({"error": f"unknown key {key}"}, HTTPStatus.BAD_REQUEST)
                mgr.call("send_key_command", key)
            elif path == "/api/text":
                text = str(body.get("text", ""))[:500]
                if text:
                    mgr.call("send_text", text)
            elif path == "/api/open":
                mgr.run_coro(mgr.open(core.resolve_link(str(body.get("app", "")), mgr.profile)), timeout=5)
            elif path == "/api/wake":
                mgr.run_coro(mgr.wake(), timeout=60)
            elif path == "/api/discover":
                result = {"tvs": mgr.run_coro(mgr.discover())}
            elif path == "/api/pair/start":
                result = {"name": mgr.run_coro(mgr.pair_start(str(body.get("host", "")).strip()))}
            elif path == "/api/pair/finish":
                mgr.run_coro(mgr.pair_finish(str(body.get("code", ""))), timeout=20)
            elif path == "/api/pair/cancel":
                mgr.run_coro(mgr.pair_cancel())
            elif path in ("/api/select", "/api/forget"):
                tv_id = str(body.get("id", ""))
                if tv_id not in core.load_config()["tvs"]:
                    raise core.TVError("Unknown TV")
                action = mgr.select if path == "/api/select" else mgr.forget
                mgr.run_coro(action(tv_id), timeout=20)
            else:
                return self._reject(HTTPStatus.NOT_FOUND)
            self._json(result)
        except (ConnectionClosed, CannotConnect):
            self._json({"error": "TV not connected"}, HTTPStatus.SERVICE_UNAVAILABLE)
        except (core.TVError, ValueError) as exc:
            self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except (TimeoutError, concurrent.futures.TimeoutError):  # distinct types before 3.11
            self._json({"error": "The TV didn't respond in time"}, HTTPStatus.GATEWAY_TIMEOUT)

    def _events(self):
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.clients[0] += 1
        try:
            version, data = self.state.snapshot()
            while True:
                self.wfile.write(f"data: {json.dumps(data)}\n\n".encode())
                self.wfile.flush()
                version, data = self.state.wait_change(version, timeout=15)
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            self.clients[0] -= 1
            self.last_client_seen[0] = time.monotonic()


def _bind() -> ThreadingHTTPServer:
    try:
        return ThreadingHTTPServer(("127.0.0.1", PREFERRED_PORT), Handler)
    except OSError:
        return ThreadingHTTPServer(("127.0.0.1", 0), Handler)


async def main(selector: str | None):
    state = State()
    mgr = Manager(state)
    httpd = _bind()
    httpd.daemon_threads = True
    port = httpd.server_address[1]
    token = secrets.token_urlsafe(18)
    Handler.mgr, Handler.state, Handler.token, Handler.port = mgr, state, token, port
    Handler.clients, Handler.last_client_seen = [0], [time.monotonic()]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    url = f"http://127.0.0.1:{port}/?t={token}"
    core.save_ui_state({"url": url, "pid": os.getpid(), "tv_id": selector})

    stop = asyncio.Event()
    task = asyncio.create_task(mgr.run(selector, stop))
    started = time.monotonic()
    seen_client = False
    try:
        while True:
            await asyncio.sleep(2)
            if Handler.clients[0] > 0:
                seen_client = True
            elif seen_client and time.monotonic() - Handler.last_client_seen[0] > IDLE_EXIT_SECONDS:
                break
            elif not seen_client and time.monotonic() - started > STARTUP_GRACE_SECONDS:
                break
    finally:
        stop.set()
        await task
        httpd.shutdown()
        core.clear_ui_state()


if __name__ == "__main__":
    try:
        asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else None))
    except KeyboardInterrupt:
        sys.exit(0)
