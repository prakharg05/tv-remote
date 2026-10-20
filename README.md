# tv-remote

Control an **Android TV / Google TV** from your computer over your home Wi-Fi — terminal
commands plus a small remote window. It uses the **Android TV Remote protocol v2**, the
same one Google's phone remote app uses, so the TV needs **no USB debugging, ADB or
developer options**. Traffic is encrypted (TLS, ports 6466/6467) and never leaves your network.

Runs on macOS, Linux and Windows. Needs Python 3.10+. No dependencies beyond the
pinned protocol library (`androidtvremote2`, the one Home Assistant uses).

## Setup

```sh
git clone <this repo> tv-remote && cd tv-remote
python3 -m venv .venv
.venv/bin/pip install --require-hashes -r requirements.lock     # Windows: .venv\Scripts\pip ...
./tv ui              # opens the remote window; with no TV yet it starts on setup:
                     # Find TVs → Pair → type the 6-character code shown on the TV
```

Prefer the terminal? `./tv discover`, `./tv pair`, then `./tv check` to confirm everything works.

Optional, to type `tv` from anywhere: `ln -s "$PWD/tv" ~/.local/bin/tv` (macOS/Linux).

## Use

```sh
tv ui                       # remote window (keyboard shortcuts shown at the bottom)
tv status                   # power, current app, volume
tv up down ok               # keys in order; repeats: down*3
tv home | back | vol+ | vol- | mute | playpause | power | settings | input
tv type "stranger things"   # type into the TV's focused search box
tv open netflix             # app alias (tv apps), input (hdmi1) or a deep-link URL
tv live                     # keyboard remote in the terminal (macOS/Linux)
```

Several TVs: pair each one (click the TV name in the window, or `tv pair`), then switch from the
window, or use `tv list`, `tv use "Bedroom TV"`, or `--tv NAME` on any command.

Clearing saved data: `tv forget NAME` / `tv forget --all` removes saved TVs (re-add by pairing);
`tv reset` also deletes this computer's pairing identity, so every TV must be paired again.

## What works where

`tv check` reports what your TV supports. The protocol is standard, but some things
differ per TV and the TV gives **no error** for ignored keys, so they can only be known by testing:

| | Any compatible TV | TVs with a verified profile |
|---|---|---|
| Keys, volume, power, typing | ✅ if `tv check` lists them | ✅ |
| Opening apps (`tv open netflix`) | Best-effort: uses links the apps declare themselves; you'll get an error if the TV rejects one | ✅ verified |
| Switching inputs | The standard Input key (some TVs ignore it) | Direct HDMI 1/2/3 buttons |

Verified profiles live in `profiles.py` (currently: Changhong AI PONT). To add your TV, copy
an entry, match the manufacturer/model shown by `tv check`, and only list what you've confirmed.

**Not supported:** Samsung (Tizen), LG (webOS), Roku and Fire TV, and older Android TVs that
only advertise the v1 remote service — `tv discover` flags those; updating "Android TV Remote
Service" on the TV from the Play Store sometimes fixes it.

## Troubleshooting

| Problem | Fix |
|---|---|
| `tv discover` finds nothing | TV on and on the **same** network (not a guest network), no VPN on the computer, router not isolating devices. On macOS, allow your terminal under System Settings → Privacy & Security → Local Network |
| "Can't reach …" | Same as above. If the TV's IP changed, `tv` finds it again automatically |
| "doesn't recognise this computer" | The TV forgot the pairing (reset, or remote service data cleared). Run `tv pair` |
| A button does nothing | That TV ignores that key — see *What works where* |

## Security & privacy

- Pairing creates a client certificate in `~/.config/tv-remote/` (owner-only). **Anyone with
  those files can control your TV from your network** — don't share or commit them.
- `tv ui` serves the remote window from `127.0.0.1` only, with a random per-session token, so
  other devices and websites can't send commands. It shuts down ~20 s after the window closes.
- Dependencies are hash-pinned (`requirements.lock`); install with `--require-hashes`.
- To revoke: delete `~/.config/tv-remote/`, and on the TV clear the data of "Android TV Remote
  Service" (Settings → Apps), which unpairs every remote, phones included.

## Files

| File | |
|---|---|
| `tvremote.py` | Core: config, discovery, pairing, connecting, capabilities, TV error replies |
| `mdns.py` | Small stdlib mDNS browser used for discovery (no extra dependencies) |
| `profiles.py` | Per-TV verified behaviour + common app links |
| `tv_cli.py`, `tv`, `tv.cmd` | CLI and its launchers |
| `tvui.py`, `ui.html` | Remote window: local server + single-file page |

Design overview for contributors and agents: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
