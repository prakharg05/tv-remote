# Architecture

The computer controls the TV with the Android TV Remote protocol v2.
The protocol uses TLS on the local network. ADB is not necessary.

```mermaid
flowchart LR
  subgraph Computer
    CLI["tv_cli.py<br/>(tv …)"] --> CORE
    UI["ui.html"] <-->|"HTTP + SSE<br/>127.0.0.1, token"| SRV["tvui.py"]
    SRV --> CORE["tvremote.py<br/>core"]
    CORE --> MDNS["mdns.py"]
    CORE --> PROF["profiles.py"]
    CORE --> CFG[("~/.config/tv-remote<br/>cert · key · config.json")]
  end
  MDNS -.->|"mDNS _androidtvremote2._tcp"| TV
  CORE -->|"TLS 6467 pair<br/>TLS 6466 control"| TV["Android TV<br/>Remote Service"]
```

## Sequence

```mermaid
sequenceDiagram
  participant U as User
  participant C as core
  participant T as TV
  U->>C: discover
  C->>T: mDNS query
  T-->>C: name, IP
  U->>C: pair(host)
  C->>T: start pairing (6467)
  T-->>U: shows 6-character code
  U->>C: code
  C->>T: secret
  Note over C: save TV by certificate MAC
  U->>C: key, text or link
  C->>T: remote message (6466)
  T-->>C: volume, app, power, errors
```

## Files

| File | Function |
|---|---|
| `tvremote.py` | Config, discovery, pairing, connection, features, TV errors |
| `mdns.py` | mDNS browser. Uses the standard library only. |
| `profiles.py` | Tested behavior for each TV model. `DEFAULT` for other TVs. |
| `tv_cli.py` | CLI. `tv check` gives a compatibility report. |
| `tvui.py`, `ui.html` | Local server and page. The server keeps one TV connection open. |

## Rules

- Use only one dependency: `androidtvremote2`. Keep it hash-pinned in `requirements.lock`.
- Keep secrets in `~/.config/tv-remote/` (mode 0600). Do not put them in the repo.
- Keep the UI server on loopback, with a token and a Host check.
- Pair only with private IP addresses.
- In a profile, put only behavior that you tested on a real TV. The TV gives no error for an ignored key.

## Known problems

| Problem | Solution |
|---|---|
| The TV ignores keys before its first state message. | `open_remote()` waits for that message. |
| Many TVs cannot open `market://launch?id=` links. | Use the deep links that the apps declare (`profiles.COMMON_APPS`). |
| The library only logs TV error replies. | `tvremote.launch()` captures the log. |
| `features()` reads a private library field. | Check it after a library upgrade. |

## Test a change

```sh
./tv check
./tv ui
```
