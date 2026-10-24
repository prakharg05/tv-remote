"""`tv` — control an Android TV / Google TV on your Wi-Fi from the terminal.

Setup
  tv discover            list TVs on this network
  tv pair                pair with a TV (a code appears on the TV)
  tv check               check this computer + TV are compatible

Control
  tv up down ok          press keys in order (also: down*3)
  tv home | back | vol+ | mute | power | playpause ...
  tv type "text"         type into the focused text field
  tv open netflix        open an app, input (hdmi1) or deep-link URL
  tv status              power, current app, volume
  tv live                keyboard remote in the terminal
  tv ui                  open the remote window

More
  tv list | use NAME | forget NAME     manage saved TVs (--tv NAME picks one per command)
  tv forget --all | tv reset           clear saved TVs | also delete this computer's pairing identity
  tv keys | tv apps                    list key / app names
"""
from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from androidtvremote2 import CannotConnect, ConnectionClosed, InvalidAuth

import profiles
import tvremote as core

KEY_DELAY = 0.12  # seconds between consecutive key presses
HERE = Path(__file__).resolve().parent
TESTED_LIBRARY = "0.3.2"


def fail(msg: str):
    sys.exit(f"tv: {msg}")


async def _with_remote(args, fn):
    try:
        async with core.connected(args.tv) as (remote, tv_id, tv):
            return await fn(remote, tv)
    except InvalidAuth:
        fail("The TV doesn't recognise this computer (pairing was reset?). Run: tv pair")
    except core.TVError as exc:
        fail(str(exc))


# --- control -------------------------------------------------------------------

def _expand(tokens: list[str]) -> list[str]:
    keys = []
    for tok in tokens:
        name, _, count = tok.partition("*")
        keys += [core.resolve_key(name)] * (int(count) if count.isdigit() else 1)
    return keys


WAKE_KEYS = ("WAKEUP", "POWER")


async def _press_with_wake(args, keys):
    """wake = turn on if off; power = toggle. Both reach a TV in deep standby via Wake-on-LAN."""
    first = keys[0]
    try:
        tv_id, tv = core.get_tv(args.tv)
        remote, used_wol = await core.wake_tv(tv_id, tv, ensure_on=first == "WAKEUP")
    except InvalidAuth:
        fail("The TV doesn't recognise this computer (pairing was reset?). Run: tv pair")
    except core.TVError as exc:
        fail(str(exc))
    if used_wol:
        print("Woke the TV with Wake-on-LAN.", file=sys.stderr)
    if first == "WAKEUP" or used_wol:  # the TV is on now; another POWER would turn it off
        while keys and keys[0] in WAKE_KEYS:
            keys = keys[1:]
    try:
        for i, key in enumerate(keys):
            if i:
                await asyncio.sleep(KEY_DELAY)
            try:
                remote.send_key_command(key)
            except ValueError:
                fail(f"unknown key '{key}'. See: tv keys")
    finally:
        await asyncio.sleep(0.3)
        remote.disconnect()


async def cmd_press(args):
    keys = _expand(args.keys)
    if keys and keys[0] in WAKE_KEYS:
        return await _press_with_wake(args, keys)

    async def run(remote, tv):
        if "TV_INPUT" in keys and not core.profile_for(tv)["input_key"]:
            print("note: this TV ignores the Input key over the remote; use `tv open hdmi1` etc.",
                  file=sys.stderr)
        for i, key in enumerate(keys):
            if i:
                await asyncio.sleep(KEY_DELAY)
            try:
                remote.send_key_command(key)
            except ValueError:
                fail(f"unknown key '{key}'. See: tv keys")

    await _with_remote(args, run)


async def cmd_type(args):
    async def run(remote, tv):
        remote.send_text(" ".join(args.text))

    await _with_remote(args, run)


async def cmd_open(args):
    async def run(remote, tv):
        try:
            await core.launch(remote, core.resolve_link(args.target, core.profile_for(tv)))
        except core.TVError as exc:
            fail(str(exc))

    await _with_remote(args, run)


async def cmd_status(args):
    async def run(remote, tv):
        info, vol = remote.device_info or {}, remote.volume_info
        app = remote.current_app
        print(f"tv:       {tv['name']} ({tv['host']})")
        print(f"device:   {info.get('manufacturer')} {info.get('model')}")
        print(f"power:    {'on' if remote.is_on else 'off/standby'}")
        # TVs only report apps that take text input; launchers usually don't.
        print(f"app:      {core.APP_NAMES.get(app, app) if app else 'unknown (likely home screen)'}")
        if vol:
            print(f"volume:   {vol['level']}/{vol['max']}{' (muted)' if vol['muted'] else ''}")

    await _with_remote(args, run)


# --- setup ---------------------------------------------------------------------

async def cmd_discover(args):
    print("Looking for TVs on this network…", file=sys.stderr)
    found = await asyncio.to_thread(core.discover)
    if not found:
        fail("No TVs found. Check the TV is on and on the same (non-guest) Wi-Fi, and no VPN is "
             "blocking the local network.")
    saved = {tv.get("host"): tv for tv in core.load_config()["tvs"].values()}
    for i, f in enumerate(found, 1):
        status = "supported" if f.supported else "NOT supported (old remote service; update " \
                 "'Android TV Remote Service' on the TV from the Play Store)"
        mark = "  [saved]" if f.host in saved else ""
        print(f"{i}. {f.name}  {f.host or '?'}  {status}{mark}")


async def _choose_tv(args):
    if args.host:
        return args.host
    print("Looking for TVs on this network…", flush=True)
    found = [f for f in await asyncio.to_thread(core.discover) if f.supported and f.host]
    if not found:
        fail("No compatible TV found. Run `tv discover` for details, or pass --host <TV IP>.")
    if len(found) == 1:
        return found[0].host
    for i, f in enumerate(found, 1):
        print(f"{i}. {f.name}  {f.host}")
    if not sys.stdin.isatty():
        fail("Several TVs found; choose one with --host <IP>.")
    choice = input("Which TV? ").strip()
    if not choice.isdigit() or not 1 <= int(choice) <= len(found):
        fail("No TV chosen.")
    return found[int(choice) - 1].host


async def cmd_pair(args):
    host = await _choose_tv(args)
    code_file = Path(args.code_file) if args.code_file else None

    async def read_code():
        if code_file is None:
            return await asyncio.to_thread(input, "Code shown on the TV: ")
        print(f"Waiting for code in {code_file} …", flush=True)
        while not (code_file.exists() and code_file.read_text().strip()):
            await asyncio.sleep(0.5)
        code = code_file.read_text()
        code_file.unlink()
        return code

    print(f"Pairing with {host} — a code will appear on the TV.", flush=True)
    try:
        session = await core.start_pairing(host)
        for attempt in range(3):
            try:
                await core.finish_pairing(session, await read_code())
                break
            except InvalidAuth:
                if attempt == 2 or code_file:
                    fail("Wrong code. Run `tv pair` again.")
                print("That code doesn't match — check it and try again.")
    except (CannotConnect, ConnectionClosed, OSError) as exc:
        fail(f"Pairing failed ({exc.__class__.__name__}). Was it cancelled on the TV? Try again.")
    finally:
        if "session" in locals():
            session.remote.disconnect()
    print(f"Paired with {session.name} ({session.mac}). Next: tv check")


def cmd_list(args):
    config = core.load_config()
    if not config["tvs"]:
        print("No saved TVs. Run: tv pair")
    for tv_id, tv in config["tvs"].items():
        star = "*" if tv_id == config["default"] else " "
        print(f"{star} {tv['name']}  {tv.get('host')}  {tv.get('manufacturer') or ''} {tv.get('model') or ''}")


def cmd_use(args):
    try:
        tv_id, tv = core.get_tv(args.name)
    except core.TVError as exc:
        fail(str(exc))
    core.remember_tv(tv_id, {}, make_default=True)
    print(f"Default TV: {tv['name']}")


ON_TV_NOTE = ("To also remove this computer on the TV, clear the data of 'Android TV Remote Service' "
              "in its Settings → Apps (unpairs every remote, phones too).")


def _confirm(args, question: str) -> None:
    if args.yes:
        return
    if not sys.stdin.isatty():
        fail("Refusing without confirmation; add --yes.")
    if input(f"{question} [y/N] ").strip().lower() not in ("y", "yes"):
        fail("Cancelled.")


def cmd_forget(args):
    if args.all:
        tvs = core.load_config()["tvs"]
        if not tvs:
            print("No saved TVs.")
            return
        _confirm(args, f"Forget all {len(tvs)} saved TV(s)? (Pairing identity is kept; re-add with tv pair)")
        for tv_id in list(tvs):
            core.forget_tv(tv_id)
        print("Forgot all saved TVs. " + ON_TV_NOTE)
        return
    if not args.name:
        fail("Name a TV (see tv list), or use --all.")
    try:
        tv_id, tv = core.get_tv(args.name)
    except core.TVError as exc:
        fail(str(exc))
    core.forget_tv(tv_id)
    print(f"Forgot {tv['name']}. " + ON_TV_NOTE)


def cmd_reset(args):
    _confirm(args, f"Delete all saved TVs AND this computer's pairing identity in {core.CONFIG_DIR}? "
                   "Every TV will need pairing again")
    _stop_ui_server()
    core.reset_all()
    print("Reset done. Run tv pair to start again. " + ON_TV_NOTE)


# --- check -----------------------------------------------------------------------

async def cmd_check(args):
    ok_all = True

    def line(ok, label, detail=""):
        nonlocal ok_all
        mark = {True: "✓", False: "✗", None: "–"}[ok]
        ok_all &= ok is not False
        print(f"  {mark} {label}{': ' + detail if detail else ''}")

    from importlib.metadata import version
    print("This computer")
    py = sys.version_info
    line(py >= (3, 10), "Python", f"{py.major}.{py.minor}.{py.micro} (needs 3.10+)")
    lib = version("androidtvremote2")
    line(True if lib == TESTED_LIBRARY else None, "androidtvremote2",
         lib + ("" if lib == TESTED_LIBRARY else f" (tested with {TESTED_LIBRARY})"))
    have_cert = core.CERT_FILE.exists() and core.KEY_FILE.exists()
    private = have_cert and os.name != "nt" and not (os.stat(core.CONFIG_DIR).st_mode & 0o077)
    line(True if have_cert else None, "Remote identity", f"{core.CONFIG_DIR}{' (owner-only)' if private else ''}"
         if have_cert else "none yet (created by tv pair)")

    print("Network")
    found = await asyncio.to_thread(core.discover)
    if not found:
        line(False, "TVs found", "none (TV off, guest Wi-Fi, VPN, or mDNS blocked)")
    for f in found:
        line(f.supported, f.name, f"{f.host}  " + ("protocol v2" if f.supported
             else "old protocol only — update 'Android TV Remote Service' on the TV"))

    print("Saved TV")
    try:
        tv_id, tv = core.get_tv(args.tv)
    except core.TVError as exc:
        line(False, str(exc))
        sys.exit(1)
    print(f"  {tv['name']}  ({tv.get('host')})")
    try:
        remote = await core.connect_tv(tv_id, tv)
    except InvalidAuth:
        line(False, "Paired", "no — run: tv pair")
        sys.exit(1)
    except core.TVError as exc:
        line(False, "Reachable", str(exc))
        sys.exit(1)
    tv_id, tv = core.get_tv(tv["name"])  # may have been re-keyed or moved
    info = remote.device_info or {}
    line(True, "Paired and connected", tv["host"])
    line(True, "Device", f"{info.get('manufacturer')} {info.get('model')}, remote service {info.get('sw_version')}")
    feats = core.features(remote)
    names = {"keys": "keys", "text": "typing", "volume": "volume", "power": "power", "open": "open apps"}
    if feats is None:
        line(None, "Features", "unknown (newer library?)")
    else:
        for k, label in names.items():
            line(feats[k], f"Supports {label}")
    profile = core.profile_for(tv)
    verified = profile is not profiles.DEFAULT
    line(True if verified else None, "Profile", profile["label"] + ("" if verified else
         " — app links best-effort, no HDMI shortcuts; see profiles.py to add this TV"))
    if profile["inputs"]:
        line(True, "Inputs", ", ".join(label for label, _ in profile["inputs"].values()))
    if not profile["input_key"]:
        line(None, "Input key", "ignored by this TV; use the HDMI shortcuts")
    remote.disconnect()
    print("\nAll good." if ok_all else "\nSome checks failed (✗).")
    sys.exit(0 if ok_all else 1)


# --- live (terminal) -----------------------------------------------------------

# Terminal escape sequences for the special keys; letters come from core.SHORTCUTS.
LIVE_KEYS = {
    "\x1b[A": "DPAD_UP", "\x1b[B": "DPAD_DOWN", "\x1b[C": "DPAD_RIGHT", "\x1b[D": "DPAD_LEFT",
    "\r": "DPAD_CENTER", "\n": "DPAD_CENTER", "\x7f": "BACK", "\x1b": "BACK", **core.SHORTCUTS,
}


async def cmd_live(args):
    try:
        import termios
        import tty
    except ImportError:
        fail("`tv live` needs a Unix terminal. On Windows use `tv ui`.")

    async def run(remote, tv):
        profile = core.profile_for(tv)
        inputs = list(profile["inputs"].values())
        digits = {str(i): link for i, (_, link) in enumerate(inputs, 1)}
        keys = {**LIVE_KEYS, **({"i": "TV_INPUT"} if profile["input_key"] else {})}
        help_text = ("arrows=move  enter=ok  backspace/esc/b=back  h=home  space=play/pause  +/-=volume  "
                     "m=mute  s=settings  p=power  t or /=type  q=quit")
        if digits:
            help_text += f"  1-{len(digits)}=inputs"
        elif profile["input_key"]:
            help_text += "  i=input"
        print(help_text + "\r")
        fd = sys.stdin.fileno()
        saved = termios.tcgetattr(fd)
        try:
            tty.setraw(fd, termios.TCSANOW)
            while True:
                ch = await asyncio.to_thread(_read_key, fd)
                if ch in ("q", "\x03"):
                    break
                if ch in ("t", "/"):
                    # Switch modes at once and keep type-ahead (TCSAFLUSH would drop it)
                    termios.tcsetattr(fd, termios.TCSANOW, saved)
                    text = await asyncio.to_thread(input, "type> ")
                    if text:
                        remote.send_text(text)
                    tty.setraw(fd, termios.TCSANOW)
                elif ch in digits:
                    remote.send_launch_app_command(digits[ch])
                elif ch in keys:
                    remote.send_key_command(keys[ch])
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, saved)
            print()

    await _with_remote(args, run)


def _read_key(fd) -> str:
    import select

    ch = os.read(fd, 1).decode(errors="ignore")
    if ch == "\x1b" and select.select([fd], [], [], 0.05)[0]:
        ch += os.read(fd, 2).decode(errors="ignore")
    return ch


# --- ui ---------------------------------------------------------------------------

def _server_alive(state) -> bool:
    if not state:
        return False
    try:
        with urllib.request.urlopen(state["url"], timeout=2) as r:
            return r.status == 200
    except (OSError, ValueError):
        return False


def _open_window(url: str, force_browser: bool) -> None:
    """Open url as a small app window (Chrome/Edge/Chromium) or fall back to the default browser."""
    app_args = [f"--app={url}", "--window-size=400,900"]
    if not force_browser:
        if sys.platform == "darwin":
            for app in ("Google Chrome", "Microsoft Edge", "Chromium", "Brave Browser"):
                if Path(f"/Applications/{app}.app").exists():
                    subprocess.run(["open", "-na", f"/Applications/{app}.app", "--args", *app_args], check=False)
                    return
        else:
            candidates = ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
                          "microsoft-edge", "brave-browser", "msedge", "chrome"]
            if os.name == "nt":
                pf = [os.environ.get(v, "") for v in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")]
                candidates += [str(Path(p) / sub) for p in pf if p for sub in (
                    "Google/Chrome/Application/chrome.exe", "Microsoft/Edge/Application/msedge.exe")]
            for c in candidates:
                exe = shutil.which(c) or (c if Path(c).is_file() else None)
                if exe:
                    subprocess.Popen([exe, *app_args], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    return
    import webbrowser
    webbrowser.open(url)


def _stop_ui_server() -> None:
    state = core.load_ui_state()
    if _server_alive(state):
        try:
            os.kill(state["pid"], signal.SIGTERM)
        except OSError:
            pass
        time.sleep(0.5)
    core.clear_ui_state()


def cmd_ui(args):
    """Start (or reuse) the background UI server and open its window.

    With no saved TV the window opens on its setup panel (find + pair a TV).
    """
    tv_id, label = None, "setup"
    if args.tv or core.load_config()["tvs"]:
        try:
            tv_id, tv = core.get_tv(args.tv)
            label = tv["name"]
        except core.TVError as exc:
            fail(str(exc))
    state = core.load_ui_state()
    if _server_alive(state) and tv_id and state.get("tv_id") != tv_id:
        _stop_ui_server()  # serving another TV; the window can also switch TVs itself
        state = None
    if not _server_alive(state):
        core.clear_ui_state()
        core._ensure_private_dir()
        log = open(core.CONFIG_DIR / "ui.log", "w")
        kwargs = {"creationflags": 0x00000008} if os.name == "nt" else {"start_new_session": True}
        subprocess.Popen([sys.executable, str(HERE / "tvui.py"), *([tv_id] if tv_id else [])], cwd=HERE,
                         stdout=log, stderr=log, stdin=subprocess.DEVNULL, **kwargs)
        for _ in range(50):
            time.sleep(0.2)
            state = core.load_ui_state()
            if state:
                break
        else:
            fail(f"UI server didn't start. See {core.CONFIG_DIR / 'ui.log'}")
    _open_window(state["url"], args.browser)
    print(f"Remote window opened ({label}). Its server stops ~20 s after the window closes.")


# --- main ---------------------------------------------------------------------------

def main():
    argv = sys.argv[1:]
    commands = {"type", "open", "status", "live", "ui", "pair", "discover", "check", "list", "use",
                "forget", "reset", "keys", "apps", "help", "press"}
    parser = argparse.ArgumentParser(prog="tv", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tv", help="saved TV to use (name, id or IP); default: last paired / `tv use`")
    sub = parser.add_subparsers(dest="cmd")
    p = sub.add_parser("type"); p.add_argument("text", nargs="+")
    p = sub.add_parser("open"); p.add_argument("target")
    p = sub.add_parser("press"); p.add_argument("keys", nargs="+")
    p = sub.add_parser("pair"); p.add_argument("--host", help="TV IP (skip discovery)")
    p.add_argument("--code-file", help=argparse.SUPPRESS)  # for scripted pairing
    p = sub.add_parser("use"); p.add_argument("name")
    p = sub.add_parser("forget"); p.add_argument("name", nargs="?")
    p.add_argument("--all", action="store_true", help="forget every saved TV")
    p.add_argument("--yes", action="store_true", help="don't ask for confirmation")
    p = sub.add_parser("reset", help="delete saved TVs and this computer's pairing identity")
    p.add_argument("--yes", action="store_true", help="don't ask for confirmation")
    p = sub.add_parser("ui"); p.add_argument("--browser", action="store_true", help="open in default browser")
    for name in ("status", "live", "discover", "check", "list", "keys", "apps", "help"):
        sub.add_parser(name)

    # Bare key names (`tv up up ok`) are shorthand for `tv press ...`
    first = next((i for i, a in enumerate(argv) if not a.startswith("-") and argv[i - 1:i] != ["--tv"]), None)
    if first is not None and argv[first] not in commands:
        argv = argv[:first] + ["press"] + argv[first:]
    args = parser.parse_args(argv)

    if args.cmd in (None, "help"):
        parser.print_help()
    elif args.cmd == "keys":
        for k, v in core.KEYS.items():
            print(f"{k:<10} {v}")
    elif args.cmd == "apps":
        try:
            profile = core.profile_for(core.get_tv(args.tv)[1])
        except core.TVError:
            profile = profiles.DEFAULT
        for k, (label, link) in core.launch_targets(profile).items():
            print(f"{k:<9} {label:<10} {link}")
    elif args.cmd in ("list", "use", "forget", "reset", "ui"):
        {"list": cmd_list, "use": cmd_use, "forget": cmd_forget, "reset": cmd_reset, "ui": cmd_ui}[args.cmd](args)
    else:
        handler = {"press": cmd_press, "type": cmd_type, "open": cmd_open, "status": cmd_status,
                   "live": cmd_live, "pair": cmd_pair, "discover": cmd_discover, "check": cmd_check}[args.cmd]
        try:
            asyncio.run(handler(args))
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
