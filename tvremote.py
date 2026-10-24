"""Shared core: discover, pair with and control Android TVs over the Android TV Remote
protocol v2 (the protocol Google's phone remote app uses; no ADB / developer options).

Used by the `tv` CLI and the `tv ui` window, so keep it UI-agnostic.
Credentials and config live in ~/.config/tv-remote/ (owner-only), never in the project.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from androidtvremote2 import AndroidTVRemote, CannotConnect, ConnectionClosed

import mdns
import profiles

CONFIG_DIR = Path.home() / ".config" / "tv-remote"
CONFIG_FILE = CONFIG_DIR / "config.json"
CERT_FILE = CONFIG_DIR / "cert.pem"
KEY_FILE = CONFIG_DIR / "key.pem"
UI_STATE_FILE = CONFIG_DIR / "ui.json"

# Shown on the TV while pairing
CLIENT_NAME = f"tv-remote ({socket.gethostname().split('.')[0]})"

SERVICE_V2 = "_androidtvremote2._tcp"  # current protocol (remote service v5+)
SERVICE_V1 = "_androidtvremote._tcp"   # old protocol; not supported by the library

# Friendly names -> RemoteKeyCode names understood by androidtvremote2
KEYS = {
    "up": "DPAD_UP", "down": "DPAD_DOWN", "left": "DPAD_LEFT", "right": "DPAD_RIGHT",
    "ok": "DPAD_CENTER", "select": "DPAD_CENTER", "enter": "DPAD_CENTER",
    "back": "BACK", "home": "HOME", "menu": "MENU", "settings": "SETTINGS", "input": "TV_INPUT",
    "vol+": "VOLUME_UP", "vol-": "VOLUME_DOWN", "volup": "VOLUME_UP", "voldown": "VOLUME_DOWN",
    "mute": "VOLUME_MUTE", "power": "POWER", "sleep": "SLEEP", "wake": "WAKEUP",
    "play": "MEDIA_PLAY", "pause": "MEDIA_PAUSE", "playpause": "MEDIA_PLAY_PAUSE",
    "stop": "MEDIA_STOP", "next": "MEDIA_NEXT", "prev": "MEDIA_PREVIOUS",
    "ff": "MEDIA_FAST_FORWARD", "rew": "MEDIA_REWIND",
    "ch+": "CHANNEL_UP", "ch-": "CHANNEL_DOWN", "info": "INFO", "guide": "GUIDE",
    "del": "DEL",
}

# Single-key shortcuts, shared by `tv live` and the remote window so they stay identical.
# Both also have: arrows = move, Enter = OK, Backspace/Esc = Back, t or / = type text,
# 1-9 = the profile's inputs, i = Input key (only where the profile says it works).
SHORTCUTS = {
    "h": "HOME", "b": "BACK", "s": "SETTINGS", "p": "POWER", "m": "VOLUME_MUTE",
    "+": "VOLUME_UP", "=": "VOLUME_UP", "-": "VOLUME_DOWN", " ": "MEDIA_PLAY_PAUSE",
}

# Package id -> display name, for showing the current app
APP_NAMES = {
    "com.netflix.ninja": "Netflix",
    "com.google.android.youtube.tv": "YouTube",
    "com.google.android.youtube.tvmusic": "YouTube Music",
    "org.jellyfin.androidtv": "Jellyfin",
    "com.apple.atve.androidtv.appletv": "Apple TV",
    "in.startv.hotstar": "JioHotstar",
    "com.sonyliv": "SonyLIV",
    "com.graymatrix.did": "Zee5",
    "com.fancode.tv": "FanCode",
    "com.amazon.amazonvideo.livingroom": "Prime Video",
    "com.google.android.videos": "Play Movies",
    "com.android.tv.settings": "Settings",
    "com.android.vending": "Play Store",
    "com.google.android.tvlauncher": "Home",
    "com.google.android.apps.tv.launcherx": "Home",
    "com.leanbitlab.ltvL": "Home",
    "com.mediatek.wwtv.tvcenter": "Live TV / Inputs",
}


class TVError(Exception):
    """A problem worth showing to the user as-is."""


# --- config ---------------------------------------------------------------

def _ensure_private_dir() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(CONFIG_DIR, 0o700)


def _write_private(path: Path, text: str) -> None:
    _ensure_private_dir()
    path.write_text(text)
    os.chmod(path, 0o600)


def load_config() -> dict:
    """{"default": tv_id, "tvs": {tv_id: {name, host, manufacturer, model, paired}}}"""
    try:
        raw = json.loads(CONFIG_FILE.read_text())
    except (FileNotFoundError, ValueError):
        raw = {}
    if "tvs" not in raw and "host" in raw:  # single-TV config from earlier versions
        tv_id = raw["host"]
        raw = {"default": tv_id, "tvs": {tv_id: {"name": raw.get("service_name", raw["host"]),
                                                  "host": raw["host"], "paired": True}}}
    raw.setdefault("tvs", {})
    raw.setdefault("default", next(iter(raw["tvs"]), None))
    return raw


def save_config(config: dict) -> None:
    _write_private(CONFIG_FILE, json.dumps(config, indent=2) + "\n")


def is_private_ip(host: str) -> bool:
    """Only LAN addresses may be paired with / connected to (no public hosts)."""
    import ipaddress
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private and not ip.is_loopback


def get_tv(selector: str | None = None) -> tuple[str, dict]:
    """Pick a saved TV by id, name or host (default TV if no selector)."""
    config = load_config()
    tvs = config["tvs"]
    if not tvs:
        raise TVError("No TV set up yet. Run: tv pair")
    if selector is None:
        tv_id = config["default"] if config["default"] in tvs else next(iter(tvs))
        return tv_id, tvs[tv_id]
    s = selector.lower()
    for tv_id, tv in tvs.items():
        if s in (tv_id.lower(), tv.get("name", "").lower(), tv.get("host", "").lower()):
            return tv_id, tv
    raise TVError(f"No saved TV matches '{selector}'. Saved: {', '.join(t['name'] for t in tvs.values())}")


def remember_tv(tv_id: str, info: dict, make_default: bool = False, replace: str | None = None) -> None:
    config = load_config()
    if replace and replace != tv_id:
        config["tvs"].pop(replace, None)
        if config["default"] == replace:
            config["default"] = tv_id
    config["tvs"][tv_id] = {**config["tvs"].get(tv_id, {}), **info}
    if make_default or not config["default"] or config["default"] not in config["tvs"]:
        config["default"] = tv_id
    save_config(config)


def save_ui_state(state: dict) -> None:
    """Record the running UI server's URL (contains its token) so `tv ui` can reuse it."""
    _write_private(UI_STATE_FILE, json.dumps(state) + "\n")


def load_ui_state() -> dict | None:
    try:
        return json.loads(UI_STATE_FILE.read_text())
    except (FileNotFoundError, ValueError):
        return None


def clear_ui_state() -> None:
    UI_STATE_FILE.unlink(missing_ok=True)


# --- discovery --------------------------------------------------------------

@dataclass
class FoundTV:
    name: str
    host: str | None
    port: int
    supported: bool   # advertises the v2 protocol
    hint_id: str      # Bluetooth MAC from the advert, if any (not always the TV's id)


def discover(timeout: float = 2.5) -> list[FoundTV]:
    """TVs advertising a remote service on the LAN. v1-only TVs are listed as unsupported."""
    services = mdns.browse([SERVICE_V2, SERVICE_V1], timeout=timeout)
    v2 = [s for s in services if s.type.startswith(SERVICE_V2)]
    v2_names = {s.instance for s in v2}
    found = [FoundTV(s.instance, mdns.resolve(s), s.port, True, s.txt.get("bt", "")) for s in v2]
    found += [FoundTV(s.instance, mdns.resolve(s), s.port, False, s.txt.get("bt", ""))
              for s in services if s.type.startswith(SERVICE_V1) and s.instance not in v2_names]
    return found


def make_remote(host: str) -> AndroidTVRemote:
    return AndroidTVRemote(CLIENT_NAME, str(CERT_FILE), str(KEY_FILE), host)


async def ensure_cert() -> None:
    _ensure_private_dir()
    await make_remote("0.0.0.0").async_generate_cert_if_missing()
    for path in (CERT_FILE, KEY_FILE):
        os.chmod(path, 0o600)


async def identify(host: str) -> tuple[str, str]:
    """(name, MAC) from the TV's pairing certificate. Needs no pairing."""
    await ensure_cert()
    try:
        return await asyncio.wait_for(make_remote(host).async_get_name_and_mac(), CONNECT_TIMEOUT)
    except asyncio.TimeoutError as exc:
        raise CannotConnect(f"Timed out connecting to {host}") from exc


async def relocate(tv_id: str, tv: dict) -> str | None:
    """Find a saved TV's new IP after a DHCP change; verified against its certificate MAC."""
    for found in await asyncio.to_thread(discover):
        if not (found.supported and found.host) or found.host == tv.get("host"):
            continue
        if found.name != tv.get("name") and found.hint_id.upper() != tv_id.upper():
            continue
        try:
            _, mac = await identify(found.host)
        except (CannotConnect, OSError):
            continue
        if mac.upper() == tv_id.upper() or tv_id == tv.get("host"):  # legacy ids are hosts
            return found.host
    return None


# --- pairing & connecting ----------------------------------------------------

@dataclass
class PairingSession:
    remote: AndroidTVRemote
    host: str
    name: str
    mac: str


async def start_pairing(host: str) -> PairingSession:
    """Ask the TV at host to show a pairing code."""
    await ensure_cert()
    remote = make_remote(host)
    try:
        # The certificate holds the model name; the mDNS advert holds the user's name for the TV.
        (cert_name, mac), found = await asyncio.wait_for(asyncio.gather(
            remote.async_get_name_and_mac(), asyncio.to_thread(discover, 1.5)), CONNECT_TIMEOUT)
        await asyncio.wait_for(remote.async_start_pairing(), CONNECT_TIMEOUT)
    except asyncio.TimeoutError as exc:
        remote.disconnect()
        raise CannotConnect(f"Timed out connecting to {host}") from exc
    name = next((f.name for f in found if f.supported and f.host == host), cert_name)
    return PairingSession(remote, host, name, mac)


async def finish_pairing(session: PairingSession, code: str) -> str:
    """Complete pairing with the on-screen code and save the TV as default. Returns its id.

    A mistyped code raises InvalidAuth before anything is sent, so the session can be retried.
    """
    await session.remote.async_finish_pairing(code.strip().upper())
    old = next((tv_id for tv_id, tv in load_config()["tvs"].items() if tv.get("host") == session.host), None)
    remember_tv(session.mac, {"name": session.name, "host": session.host, "paired": True},
                make_default=True, replace=old)
    return session.mac


async def pair(host: str, read_code) -> tuple[str, str]:
    """Pair with the TV at host. `read_code` is an async callable returning the on-screen code."""
    session = await start_pairing(host)
    await finish_pairing(session, await read_code())
    return session.name, session.mac


def forget_tv(tv_id: str) -> None:
    config = load_config()
    config["tvs"].pop(tv_id, None)
    if config["default"] == tv_id:
        config["default"] = next(iter(config["tvs"]), None)
    save_config(config)


def reset_all() -> None:
    """Delete saved TVs and this computer's pairing identity (every TV must be paired again)."""
    for path in (CONFIG_FILE, CERT_FILE, KEY_FILE, UI_STATE_FILE, CONFIG_DIR / "ui.log"):
        path.unlink(missing_ok=True)


CONNECT_TIMEOUT = 4  # seconds; a dead IP otherwise waits for the OS TCP timeout (~75 s)


async def open_remote(host: str) -> AndroidTVRemote:
    """Connect and wait until the TV is ready to accept keys."""
    remote = make_remote(host)
    try:
        await asyncio.wait_for(remote.async_connect(), CONNECT_TIMEOUT)
    except asyncio.TimeoutError as exc:
        remote.disconnect()
        raise CannotConnect(f"Timed out connecting to {host}") from exc
    # TVs drop keys sent before they have pushed their initial state; wait for that.
    for _ in range(20):
        if remote.volume_info is not None:
            break
        await asyncio.sleep(0.05)
    await asyncio.sleep(0.1)
    return remote


async def connect_tv(tv_id: str, tv: dict) -> AndroidTVRemote:
    """Connect to a saved TV, re-finding it on the network if its IP changed."""
    try:
        remote = await open_remote(tv["host"])
    except (CannotConnect, ConnectionClosed, OSError):
        new_host = await relocate(tv_id, tv)
        if not new_host:
            raise TVError(f"Can't reach {tv.get('name', 'the TV')} at {tv['host']}. "
                          "Is it on, and is this computer on the same (non-guest) Wi-Fi?")
        remote = await open_remote(new_host)
        tv = {**tv, "host": new_host}
    info = remote.device_info or {}
    new_id = tv_id
    if tv_id == tv.get("host"):  # entry from an old config, keyed by IP: re-key by the TV's MAC
        try:
            new_id = (await identify(tv["host"]))[1]
        except (CannotConnect, OSError):
            pass
    mac = await asyncio.to_thread(lookup_mac, tv["host"])  # for Wake-on-LAN later
    remember_tv(new_id, {**tv, "manufacturer": info.get("manufacturer"), "model": info.get("model"),
                         "paired": True, **({"wol_mac": mac} if mac else {})}, replace=tv_id)
    return remote


# --- waking from deep standby ---------------------------------------------------
# In screen-off standby the remote service still answers, so WAKEUP/POWER keys work.
# In deep standby it does not; Wake-on-LAN (if the TV has it on) is the only way in.
# The packet needs the TV's network MAC, which differs from the MAC in its pairing
# certificate, so it is read from this computer's ARP table while the TV is reachable.

def lookup_mac(host: str) -> str | None:
    """The MAC this computer's ARP table has for host, as aa:bb:cc:dd:ee:ff."""
    import re
    import subprocess
    try:
        if Path("/proc/net/arp").exists():  # Linux
            text = Path("/proc/net/arp").read_text()
            text = "\n".join(line for line in text.splitlines() if line.split()[:1] == [host])
        else:  # macOS: arp -n IP; Windows: arp -a IP
            cmd = ["arp", "-a", host] if os.name == "nt" else ["arp", "-n", host]
            text = subprocess.run(cmd, capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"([0-9a-fA-F]{1,2})[:-]([0-9a-fA-F]{1,2})[:-]([0-9a-fA-F]{1,2})[:-]"
                  r"([0-9a-fA-F]{1,2})[:-]([0-9a-fA-F]{1,2})[:-]([0-9a-fA-F]{1,2})", text)
    if not m:
        return None
    mac = ":".join(part.zfill(2).lower() for part in m.groups())  # macOS drops leading zeros
    return None if mac in ("ff:ff:ff:ff:ff:ff", "00:00:00:00:00:00") else mac


def send_wol(mac: str, host: str | None = None) -> None:
    """Send Wake-on-LAN magic packets (broadcast and, if known, the host's /24 broadcast)."""
    payload = b"\xff" * 6 + bytes.fromhex(mac.replace(":", "")) * 16
    targets = ["255.255.255.255"]
    if host and is_private_ip(host):
        targets.append(".".join(host.split(".")[:3] + ["255"]))
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for _ in range(3):
            for target in targets:
                for port in (9, 7):
                    try:
                        sock.sendto(payload, (target, port))
                    except OSError:
                        pass


async def wake_tv(tv_id: str, tv: dict, ensure_on: bool = True,
                  timeout: float = 30) -> tuple[AndroidTVRemote, bool]:
    """Connect to a TV, using Wake-on-LAN first if it does not answer. Returns (remote, used_wol).

    With ensure_on (or after Wake-on-LAN) it sends POWER only if the TV says it is off.
    Many TVs ignore WAKEUP over this protocol, and POWER is a toggle, so check first.
    """
    used_wol = False
    try:
        remote = await connect_tv(tv_id, tv)
    except TVError:
        mac = tv.get("wol_mac")
        if not mac:
            raise TVError(f"{tv.get('name', 'The TV')} is in deep standby, and no network address is "
                          "saved for Wake-on-LAN yet. Turn it on once with its remote, then try again.") from None
        send_wol(mac, tv.get("host"))
        used_wol = True
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            await asyncio.sleep(2)
            try:
                remote = await open_remote(tv["host"])
                break
            except (CannotConnect, ConnectionClosed, OSError):
                if asyncio.get_running_loop().time() > deadline:
                    raise TVError(f"{tv.get('name', 'The TV')} did not wake up. Its Wake-on-LAN may not "
                                  "work over Wi-Fi; use its own remote.") from None
                send_wol(mac, tv.get("host"))
    if (ensure_on or used_wol) and not remote.is_on:
        remote.send_key_command("POWER")
    return remote, used_wol


@asynccontextmanager
async def connected(selector: str | None = None):
    """Yield (remote, tv_id, tv) for a saved TV; disconnects (after flushing) on exit."""
    tv_id, tv = get_tv(selector)
    remote = await connect_tv(tv_id, tv)
    tv_id, tv = get_tv(tv["name"])  # may have been re-keyed or moved
    try:
        yield remote, tv_id, tv
    finally:
        await asyncio.sleep(0.3)  # sends are buffered; let them flush
        remote.disconnect()


# --- capabilities ------------------------------------------------------------

def features(remote: AndroidTVRemote) -> dict[str, bool] | None:
    """What the TV says it supports, from its handshake. None if unknown."""
    from androidtvremote2.remote import Feature
    proto = getattr(remote, "_remote_message_protocol", None)
    active = getattr(proto, "_active_features", None)  # requested & supported (private in 0.3.x)
    if active is None:
        return None
    return {"keys": Feature.KEY in active, "text": Feature.IME in active, "power": Feature.POWER in active,
            "volume": Feature.VOLUME in active, "open": Feature.APP_LINK in active}


def profile_for(tv: dict) -> dict:
    return profiles.for_device(tv.get("manufacturer"), tv.get("model"))


def launch_targets(profile: dict) -> dict[str, tuple[str, str]]:
    """alias -> (label, link) for apps and this TV's inputs."""
    return {**profiles.COMMON_APPS, **profile["inputs"]}


def resolve_link(name: str, profile: dict) -> str:
    target = launch_targets(profile).get(name.lower())
    if target:
        return target[1]
    if "://" in name:
        return name
    raise TVError(f"No link known for '{name}'. Use an alias from `tv apps` or a full URL")


def resolve_key(name: str) -> str:
    """Alias (`back`), shortcut letter (`b`, as in `tv live`) or library key name (`DPAD_UP`)."""
    lower = name.lower()
    return KEYS.get(lower) or SHORTCUTS.get(lower) or name.upper().removeprefix("KEYCODE_")


# --- TV error replies ----------------------------------------------------------
# The library only logs the TV's error replies, so collect them to report failures.

class _DeviceErrors(logging.Handler):
    def __init__(self):
        super().__init__(logging.ERROR)
        self.messages: list[str] = []

    def emit(self, record):
        self.messages.append(record.getMessage().replace("Received an error from the device: ", ""))


_errors = _DeviceErrors()
logging.getLogger("androidtvremote2").addHandler(_errors)


async def launch(remote: AndroidTVRemote, link: str, wait: float = 0.6) -> None:
    """Open a link and raise TVError if the TV replies with an error."""
    seen = len(_errors.messages)
    remote.send_launch_app_command(link)
    await asyncio.sleep(wait)
    if len(_errors.messages) > seen:
        raise TVError("The TV couldn't open that (app not installed, or it doesn't accept this link).")
