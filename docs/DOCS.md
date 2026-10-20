# Reference

Design: [ARCHITECTURE.md](ARCHITECTURE.md). Install: [INSTALL.md](../INSTALL.md).

## CLI

Syntax: `tv [--tv NAME|ID|IP] <command>`. The exit code is 1 if an error occurs.

| Command | Function |
|---|---|
| `discover` | Lists the TVs on the network. Shows if the tool supports each TV. |
| `pair [--host IP]` | Pairs with a TV. The TV shows a code. |
| `check` | Gives a compatibility report. |
| `KEY…`, `press KEY…` | Sends keys. `'down*3'` sends a key 3 times. |
| `type TEXT` | Types text into the selected field. |
| `open TARGET` | Opens an app alias, `hdmiN` or a URL. |
| `status` | Shows power, app and volume. |
| `live` | Starts a keyboard remote (macOS and Linux). |
| `ui [--browser]` | Opens the remote window. |
| `list`, `use NAME` | Lists saved TVs. Sets the default TV. |
| `forget NAME`, `forget --all` | Removes saved TVs. |
| `reset` | Removes saved TVs and the client identity. `--yes` skips the prompt. |
| `keys`, `apps` | Lists the aliases. |

## Python API (`tvremote.py`)

| Function | Result |
|---|---|
| `discover()` | List of `FoundTV(name, host, port, supported, hint_id)` |
| `start_pairing(host)` | A pairing session. The TV shows a code. |
| `finish_pairing(session, code)` | The TV id. A bad code raises `InvalidAuth`. You can try again. |
| `connected(selector)` | Context manager. Gives `(remote, id, tv)`. |
| `launch(remote, link)` | Opens a link. Raises `TVError` if the TV rejects it. |
| `features(remote)` | `{keys, text, power, volume, open}` |
| `get_tv`, `remember_tv`, `forget_tv`, `reset_all` | Config operations |
| `profile_for`, `resolve_link`, `resolve_key` | Alias operations |

`remote` is the library `AndroidTVRemote` object. Use `send_key_command`, `send_text` and `send_launch_app_command`.

```python
async with core.connected() as (remote, _, tv):
    await core.launch(remote, core.resolve_link("netflix", core.profile_for(tv)))
```

## HTTP API (`tvui.py`)

The server address is `127.0.0.1:47821`. The URL and token are in `~/.config/tv-remote/ui.json`.

Authentication:
- For GET, add `?t=TOKEN`.
- For POST, add the header `X-TV-Token`.
- The `Host` header must be a loopback address.

| Endpoint | Body |
|---|---|
| `GET /api/events` | None. Gives an SSE state stream. |
| `POST /api/key` | `{key}` |
| `POST /api/text` | `{text}` |
| `POST /api/open` | `{app}` |
| `POST /api/discover` | None |
| `POST /api/pair/start` | `{host}` |
| `POST /api/pair/finish` | `{code}` |
| `POST /api/pair/cancel` | None |
| `POST /api/select`, `POST /api/forget` | `{id}` |

Errors are `{error}`, with status 400, 503 or 504.

State keys: `status`, `power`, `app`, `app_name`, `volume`, `features`, `info`, `tvs`, `current`.

## Files

The directory is `~/.config/tv-remote/`. The directory mode is 0700. The file mode is 0600.

| File | Content |
|---|---|
| `cert.pem`, `key.pem` | Client identity. Keep these files secret. |
| `config.json` | `{default, tvs: {MAC: {name, host, manufacturer, model}}}` |
| `ui.json`, `ui.log` | UI server state and log |

## Profiles (`profiles.py`)

A profile matches the `manufacturer` and `model` of the TV.

| Field | Content |
|---|---|
| `input_key` | `True` if the TV accepts the Input key |
| `inputs` | Alias to (label, URL) |

A TV without a profile uses `DEFAULT`. Add only behavior that you tested.

## Protocol

| Item | Value |
|---|---|
| mDNS | `_androidtvremote2._tcp` is v2. A TV with v1 only is not supported. |
| Ports | 6467 for pairing. 6466 for control. Both use TLS. |
| TV requirement | Android TV Remote Service v5 or later |
| Library | `androidtvremote2` 0.3.2 |

Notes:
- `current_app` shows only apps that accept text input.
- An app without deep links cannot open over this protocol.

## Changhong AI PONT (tested)

- The TV ignores input keys over this protocol.
- HDMI 1 to 3: `content://android.media.tv/passthrough/com.mediatek.tvinput%2F.hdmi.HDMIInputService%2FHW4|5|6`
- Jellyfin and FanCode have no deep links.
