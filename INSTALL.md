# Install

## Quick procedure

```sh
git clone https://github.com/prakharg05/tv-remote.git && cd tv-remote
python3 -m venv .venv
.venv/bin/pip install --require-hashes -r requirements.lock   # Windows: .venv\Scripts\pip
./tv ui
```

In the window, do these steps:
1. Click **Find TVs on this Wi-Fi**.
2. Click **Pair** for your TV.
3. Type the code that the TV shows.

Optional: to use `tv` from all folders, run `ln -s "$PWD/tv" ~/.local/bin/tv`.

## Procedure for AI agents

1. Make sure that Python is version 3.10 or later.
2. Make sure that the computer and the TV are on the same Wi-Fi network.
   - Do not use a guest network.
   - Do not use a VPN.
3. Make sure that the TV is an Android TV or Google TV, and that it is on.
4. Do the install commands in the quick procedure.
   - Keep `--require-hashes`.
   - If the install fails, tell the user. Do not change the lockfile.
5. Run `./tv discover`.
   - If the result is `NOT supported`, stop and tell the user.
   - If the result is empty, refer to Troubleshooting.
6. Pair the TV. The TV shows the code, so get the code from the user.
   - With a terminal: run `./tv pair`.
   - Without a terminal: run `./tv pair --code-file /tmp/code` in the background. Then write the code to that file.
   - If there is more than one TV, add `--host IP`.
7. Run `./tv check`. Make sure that the result is `All good.`
8. Before you send keys to the TV, get approval from the user.

**WARNING:** Do not share or commit `~/.config/tv-remote/`. These files give control of the TV.

**WARNING:** Do not enable ADB on the TV. Do not scan networks that the user does not own.

## Troubleshooting

| Problem | Action |
|---|---|
| No TVs found | Use the same Wi-Fi, not a guest network. Disable the VPN. On macOS, allow the terminal in Privacy & Security > Local Network. |
| Cannot reach the TV | Do the same checks. The tool finds a new TV IP address automatically. |
| TV does not recognize the computer | Run `tv pair` again. |
| A button does not operate | The TV ignores that key. Run `tv check`. |

## Removal

1. Run `tv reset`.
2. On the TV, go to Settings > Apps > Android TV Remote Service.
3. Select **Clear data**.
