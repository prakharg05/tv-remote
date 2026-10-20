# Architecture

Laptop → Android TV over the **Android TV Remote protocol v2** (TLS, LAN only). No ADB.

```mermaid
flowchart LR
  subgraph Laptop
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

## Flows

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
  T-->>U: shows 6-char code
  U->>C: code
  C->>T: secret → trusted
  Note over C: save TV by cert MAC
  U->>C: key / text / open link
  C->>T: remote msg (6466)
  T-->>C: volume, app, power, errors
```

## Files

| File | Role |
|---|---|
| `tvremote.py` | Config, discover, pair, connect (+relocate on IP change), features, error capture |
| `mdns.py` | Stdlib mDNS browser |
| `profiles.py` | Per-model verified behaviour; `DEFAULT` for unknown TVs |
| `tv_cli.py` | CLI; `tv check` = compatibility report |
| `tvui.py` / `ui.html` | Local server (one persistent TV connection) + single-file page |

## Rules

- **Deps:** only `androidtvremote2` (hash-pinned in `requirements.lock`). Stdlib for everything else.
- **Secrets** live in `~/.config/tv-remote/` (0600), never in the repo.
- **UI server:** loopback only, per-run token, Host check; pairing only to private IPs.
- **Profiles:** only list what was verified on a real TV. The TV gives **no error** for ignored keys.

## Gotchas

- Keys sent before the TV's first state push are dropped → `open_remote()` waits for it.
- `market://launch?id=` has no handler on many TVs → use app-declared deep links (`profiles.COMMON_APPS`).
- Library only *logs* TV error replies → `tvremote.launch()` captures them.
- Feature flags read from a private library attr (`features()`); re-check on library upgrade.

## Verify a change

```sh
./tv check          # end-to-end against your TV
./tv ui             # visual check
```
