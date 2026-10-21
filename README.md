# tv-remote 📺

> So, the TV was getting slower, and searching for the remote is a pain. This helps.

tv-remote controls an Android TV or Google TV from a computer on the same Wi-Fi.
It gives a remote window and a `tv` command.
The TV does not need ADB or developer options.
The tool uses the same protocol as the Google TV phone remote.

```sh
tv ui                  # open the remote window
tv open netflix        # open an app
tv type "the bear"     # type into the TV search field
tv 'down*3' ok         # send keys from a script
```

- Finds TVs on the network. Pairs with a 6-character code. Keeps more than one TV.
- Encrypts all traffic. Traffic stays on the local network.
- Uses one dependency, with hash pins.
- Runs on macOS, Linux and Windows. Needs Python 3.10 or later.

## Install

Refer to [INSTALL.md](INSTALL.md). It has steps for persons and for AI agents.

## More

- `tv help` shows all commands. `tv check` shows what the TV supports.
- Design: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- Reference: [docs/DOCS.md](docs/DOCS.md)

---

Works for me, should work for y'all.

Claude helped a lot.
