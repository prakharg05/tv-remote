# Reference

Design: [ARCHITECTURE.md](ARCHITECTURE.md). Install: [INSTALL.md](../INSTALL.md).

This document lists all the interfaces of the tool:

| Interface | Section |
|---|---|
| Launchers `tv`, `tv.cmd` | [Launchers](#launchers) |
| Command line | [CLI](#cli) |
| Keyboard in `tv live` and the remote window | [Keyboard shortcuts](#keyboard-shortcuts) |
| Remote window | [Remote window](#remote-window-uihtml) |
| Python modules | [Python API](#python-api-tvremotepy), [mDNS](#mdns-mdnspy), [Profiles](#profiles-profilespy) |
| Local HTTP server | [HTTP API](#http-api-tvuipy) |
| Files on disk | [Files](#files) |
| Network | [Protocol](#protocol) |

## Launchers

| File | Platform | Function |
|---|---|---|
| `tv` | macOS, Linux | Runs `tv_cli.py` with `.venv/bin/python`. Works through a symlink on `PATH`. |
| `tv.cmd` | Windows | Runs `tv_cli.py` with `.venv\Scripts\python.exe`. |

## CLI

Syntax: `tv [--tv NAME|ID|IP] <command>`. The exit code is 1 if an error occurs.

`--tv` selects a saved TV. If you do not use `--tv`, the tool uses the default TV (the last paired TV or the TV from `tv use`).

| Command | Function |
|---|---|
| `discover` | Lists the TVs on the network. Shows if the tool supports each TV and if the TV is saved. |
| `pair [--host IP]` | Pairs with a TV. The TV shows a code. Without `--host`, the tool finds TVs and asks you to select one. |
| `check` | Gives a compatibility report: Python, library, identity, network, saved TV. |
| `KEY…`, `press KEY…` | Sends keys. `'down*3'` sends a key 3 times. Wakes the TV first if necessary. |
| `type TEXT…` | Types text into the selected field. |
| `open TARGET` | Opens an app alias, an input alias (`hdmiN`) or a URL. |
| `status` | Shows power, app and volume. |
| `wake` | Turns the TV on. Does nothing if the TV is on. |
| `power` | Turns the TV on or off. |
| `live` | Starts a keyboard remote in the terminal (macOS and Linux). |
| `ui [--browser]` | Opens the remote window. `--browser` uses the default browser, not an app window. |
| `list` | Lists saved TVs. |
| `use NAME` | Sets the default TV. |
| `forget NAME [--yes]` | Removes a saved TV. |
| `forget --all [--yes]` | Removes all saved TVs. |
| `reset [--yes]` | Removes saved TVs and the client identity. |
| `keys` | Lists the key aliases. |
| `apps` | Lists the app and input aliases for the TV. |
| `help` | Shows the help. |

Notes:
- A word that is not a command is a key. `tv up up ok` is the same as `tv press up up ok`.
- `--yes` skips the confirmation prompt.
- The tool waits 0.12 s between keys.
- `pair --code-file FILE` reads the code from a file. It is for scripts and is not in the help.

### Key aliases

`KEYS` in `tvremote.py`. A key can also be a shortcut letter (`b`) or a library name (`DPAD_UP`, `KEYCODE_DPAD_UP`).

| Alias | Key |
|---|---|
| `up`, `down`, `left`, `right` | `DPAD_UP`, `DPAD_DOWN`, `DPAD_LEFT`, `DPAD_RIGHT` |
| `ok`, `select`, `enter` | `DPAD_CENTER` |
| `back`, `home`, `menu`, `settings`, `input` | `BACK`, `HOME`, `MENU`, `SETTINGS`, `TV_INPUT` |
| `vol+`, `volup`, `vol-`, `voldown`, `mute` | `VOLUME_UP`, `VOLUME_DOWN`, `VOLUME_MUTE` |
| `power`, `sleep`, `wake` | `POWER`, `SLEEP`, `WAKEUP` |
| `play`, `pause`, `playpause`, `stop` | `MEDIA_PLAY`, `MEDIA_PAUSE`, `MEDIA_PLAY_PAUSE`, `MEDIA_STOP` |
| `next`, `prev`, `ff`, `rew` | `MEDIA_NEXT`, `MEDIA_PREVIOUS`, `MEDIA_FAST_FORWARD`, `MEDIA_REWIND` |
| `ch+`, `ch-`, `info`, `guide`, `del` | `CHANNEL_UP`, `CHANNEL_DOWN`, `INFO`, `GUIDE`, `DEL` |

### App aliases

`COMMON_APPS` in `profiles.py`. A profile can add input aliases (for example `hdmi1`).

| Alias | Link |
|---|---|
| `netflix` | `nflx://www.netflix.com/` |
| `youtube` | `vnd.youtube.launch://` |
| `ytmusic` | `https://music.youtube.com/` |
| `prime` | `https://app.primevideo.com/` |
| `appletv` | `https://tv.apple.com/` |
| `hotstar` | `hotstar://` |
| `sonyliv` | `sonyliv://home` |
| `zee5` | `https://www.zee5.com/` |

## Keyboard shortcuts

`tv live` and the remote window use the same keys. The table is `SHORTCUTS` in `tvremote.py`.

| Key | Action |
|---|---|
| Arrows, Enter | Move, OK |
| Backspace, Esc, `b` | Back |
| `h`, `s`, `p` | Home, Settings, Power |
| `+`/`=`, `-`, `m`, Space | Volume up, volume down, mute, play/pause |
| `t`, `/` | Type text |
| `1`–`9` | Inputs from the profile |
| `i` | Input key, only if the profile sets `input_key` |

| Surface | Additional keys |
|---|---|
| `tv live` | `q` or Ctrl+C stops. |
| Remote window | `l` opens the TVs panel. `w` changes between D-pad and Swipe. Esc closes the panel or leaves the text field. |

## Remote window (`ui.html`)

`tv ui` starts `tvui.py` in the background and opens `ui.html`.

| Item | Behavior |
|---|---|
| Window | App window in Chrome, Edge, Chromium or Brave. Else the default browser. |
| Setup panel | Opens if there is no saved TV. Finds TVs, pairs by list or by IP. |
| TVs panel | Selects, pairs again or forgets a saved TV. |
| D-pad mode | Buttons for move and OK. |
| Swipe mode | Drag 48 px or scroll with two fingers for one step. Click for OK. The browser keeps the mode. |
| Power button | If the TV is offline, sends Wake-on-LAN (`/api/wake`). |
| Server stop | 20 s after the last window closes. 90 s if no window connects. |
| Other TV | `tv ui --tv X` stops a server for a different TV and starts a new one. |

## Python API (`tvremote.py`)

### Discovery and pairing

| Function | Result |
|---|---|
| `discover(timeout=2.5)` | List of `FoundTV(name, host, port, supported, hint_id)`. v1-only TVs have `supported=False`. |
| `start_pairing(host)` | A `PairingSession`. The TV shows a code. |
| `finish_pairing(session, code)` | The TV id. A bad code raises `InvalidAuth`. You can try again. |
| `pair(host, read_code)` | Starts and finishes pairing. `read_code` gives the code. Returns `(id, name)`. |
| `identify(host)` | `(id, name)` of a paired TV. |
| `ensure_cert()` | Makes the client identity if it does not exist. |

### Connection and control

| Function | Result |
|---|---|
| `connected(selector=None)` | Async context manager. Gives `(remote, id, tv)`. Disconnects at the end. |
| `connect_tv(id, tv)` | Connected `remote`. Finds the TV again if its IP changed. |
| `open_remote(host)` | Connected `remote`. Times out after 4 s. |
| `make_remote(host)` | A `remote` that is not connected. |
| `relocate(id, tv)` | New IP of a TV that moved, or `None`. |
| `wake_tv(id, tv, ensure_on=True, …)` | Turns the TV on. Uses Wake-on-LAN for deep standby. |
| `send_wol(mac, host=None)` | Sends a Wake-on-LAN packet. |
| `lookup_mac(host)` | MAC from the ARP table, or `None`. |
| `launch(remote, link, wait=0.6)` | Opens a link. Raises `TVError` if the TV rejects it. |
| `features(remote)` | `{keys, text, power, volume, open}` from the handshake, or `None`. |

### Config and aliases

| Function | Result |
|---|---|
| `load_config()`, `save_config(config)` | Reads or writes `config.json`. |
| `get_tv(selector=None)` | `(id, tv)` by name, id or IP. Raises `TVError` if not found. |
| `remember_tv(id, info, make_default=False, replace=None)` | Saves a TV. |
| `forget_tv(id)`, `reset_all()` | Removes a TV. Removes all TVs and the identity. |
| `load_ui_state()`, `save_ui_state(s)`, `clear_ui_state()` | Operations on `ui.json`. |
| `profile_for(tv)` | The profile of the TV. |
| `launch_targets(profile)` | `{alias: (label, link)}` for apps and inputs. |
| `resolve_link(name, profile)` | Link for an alias or a URL. Raises `TVError` if unknown. |
| `resolve_key(name)` | Library key name for an alias, a shortcut letter or a key name. |
| `is_private_ip(host)` | `True` for a LAN address. |

### Constants

| Name | Content |
|---|---|
| `KEYS`, `SHORTCUTS` | Key aliases. Single-key shortcuts. |
| `APP_NAMES` | Package id to display name. |
| `SERVICE_V2`, `SERVICE_V1` | mDNS service types. |
| `CONFIG_DIR`, `CONFIG_FILE`, `CERT_FILE`, `KEY_FILE`, `UI_STATE_FILE` | Paths. |
| `TVError` | Exception with a message for the user. |

`remote` is the library `AndroidTVRemote` object. Use `send_key_command`, `send_text` and `send_launch_app_command`.

```python
async with core.connected() as (remote, _, tv):
    await core.launch(remote, core.resolve_link("netflix", core.profile_for(tv)))
```

## mDNS (`mdns.py`)

Standard library only.

| Function | Result |
|---|---|
| `browse(service_types, timeout=2.5)` | List of `Service(type, instance, target, port, addresses, txt)`. |
| `resolve(svc)` | Best IPv4 address: the mDNS answer, else the OS resolver. |

How `browse` finds TVs:

| Step | Action |
|---|---|
| 1 | Sends 3 queries to `224.0.0.251:5353` from a random port, through the LAN interface (not a VPN `utun`). |
| 2 | Receives unicast answers on the random port. |
| 3 | Also receives multicast answers on port 5353. The port is shared with the OS responder (`SO_REUSEPORT`). |
| 4 | Asks for SRV and A records that are missing. |

- Some TVs (for example TCL) send their answer only to the multicast group. Step 3 is necessary for these TVs.
- If the tool cannot bind port 5353, it uses steps 1, 2 and 4 only.

## HTTP API (`tvui.py`)

The server address is `127.0.0.1:47821`. If the port is in use, the server uses a random port. The URL and token are in `~/.config/tv-remote/ui.json`.

Authentication:
- For GET, add `?t=TOKEN`.
- For POST, add the header `X-TV-Token`.
- The `Host` header must be a loopback address.
- A bad token or host gives 403.

| Endpoint | Body | Response |
|---|---|---|
| `GET /` | None | The remote window (`ui.html`) |
| `GET /api/events` | None | SSE stream of the state. Sends on each change and every 15 s. |
| `POST /api/key` | `{key}` (library key name) | `{ok}` |
| `POST /api/text` | `{text}` (max. 500 characters) | `{ok}` |
| `POST /api/open` | `{app}` (alias or URL) | `{ok}` |
| `POST /api/wake` | None | `{ok}`. Wake-on-LAN, then connects. |
| `POST /api/discover` | None | `{tvs: [{name, host, supported, saved}]}` |
| `POST /api/pair/start` | `{host}` | `{name}` |
| `POST /api/pair/finish` | `{code}` | `{ok}` |
| `POST /api/pair/cancel` | None | `{ok}` |
| `POST /api/select`, `POST /api/forget` | `{id}` (saved TV) | `{ok}` |

Errors are `{error}`:

| Status | Cause |
|---|---|
| 400 | Bad input, unknown key or TV, or `TVError` |
| 403 | Bad token or host |
| 404 | Unknown endpoint |
| 503 | TV not connected |
| 504 | The TV did not answer in time |

State keys:

| Key | Content |
|---|---|
| `status` | `setup`, `connecting`, `connected`, `reconnecting`, `offline` or `unpaired` |
| `power`, `app`, `app_name`, `volume` | TV state |
| `features` | Output of `features()` |
| `message` | Text for the user, or `null` |
| `info` | `{name, apps, inputs, input_key, shortcuts, profile, verified}` |
| `tvs`, `current` | Saved TVs `[{id, name, host, model}]`. Selected TV id. |

## Files

The directory is `~/.config/tv-remote/`. The directory mode is 0700. The file mode is 0600.

| File | Content |
|---|---|
| `cert.pem`, `key.pem` | Client identity. Keep these files secret. |
| `config.json` | `{default, tvs: {MAC: {name, host, manufacturer, model, wol_mac}}}` |
| `ui.json` | `{url, pid, tv_id}` of the running UI server |
| `ui.log` | UI server log |

## Profiles (`profiles.py`)

A profile matches the `manufacturer` and `model` of the TV. Case is not important.

| Field | Content |
|---|---|
| `id`, `label` | Identifier. Name in the window. |
| `match` | `{manufacturer, model}` |
| `input_key` | `True` if the TV accepts the Input key |
| `inputs` | Alias to (label, URL) |

| Name | Content |
|---|---|
| `PROFILES` | Tested profiles |
| `DEFAULT` | Profile for a TV without a match |
| `COMMON_APPS` | App aliases for all TVs |
| `for_device(manufacturer, model)` | The matching profile, else `DEFAULT` |

Add only behavior that you tested.

## Protocol

| Item | Value |
|---|---|
| mDNS | `_androidtvremote2._tcp` is v2. A TV with v1 only is not supported. |
| Ports | 6467 for pairing. 6466 for control. Both use TLS. 5353/UDP for discovery. |
| TV requirement | Android TV Remote Service v5 or later |
| Library | `androidtvremote2` 0.3.2 |

Notes:
- `current_app` shows only apps that accept text input.
- An app without deep links cannot open over this protocol.
- If `discover` finds no TVs, use `tv pair --host IP`. On macOS, `dns-sd -B _androidtvremote2._tcp local.` lists TVs.
- On macOS, allow Local Network access for the terminal. Else the error is `No route to host`.

## Wake the TV

| TV state | Result of `wake` or the power button |
|---|---|
| Screen off. The remote service answers. | The tool sends POWER only if the TV reports that it is off. |
| Deep standby. The TV does not answer. | The tool sends a Wake-on-LAN packet, then connects. |

- The tool saves the TV network MAC (`wol_mac`) from the ARP table when it connects.
- This MAC is not the MAC in the pairing certificate.
- Many TVs ignore WAKEUP over this protocol. POWER is a toggle, so the tool checks the state first.
- Wake-on-LAN must be on in the TV. Some TVs accept it only on Ethernet.

## Changhong AI PONT (tested)

- The TV ignores input keys and WAKEUP over this protocol. POWER operates.
- `network_wol=true` in the TV settings.
- HDMI 1 to 3: `content://android.media.tv/passthrough/com.mediatek.tvinput%2F.hdmi.HDMIInputService%2FHW4|5|6`
- Jellyfin and FanCode have no deep links.
