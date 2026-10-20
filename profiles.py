"""What works on which TV.

The remote protocol itself is standard, but some behaviour is per-TV and can only be
learned by testing on the device (the protocol gives no error for ignored keys).
A profile records what has been verified for a manufacturer/model; unknown TVs get
DEFAULT, which only assumes the standard parts.

To add a TV: copy an entry, set `match` to the manufacturer/model shown by `tv check`,
and only list things you have confirmed on that TV.
"""
from __future__ import annotations

# Deep links each app declares for itself on Android TV, so they should work on any TV
# with the app installed. Verified on the Changhong AI PONT; elsewhere best-effort.
# (The library's default market://launch?id=<pkg> links have no handler on many TVs.)
COMMON_APPS = {
    "netflix": ("Netflix", "nflx://www.netflix.com/"),
    "youtube": ("YouTube", "vnd.youtube.launch://"),
    "ytmusic": ("YT Music", "https://music.youtube.com/"),
    "prime": ("Prime", "https://app.primevideo.com/"),
    "appletv": ("Apple TV", "https://tv.apple.com/"),
    "hotstar": ("Hotstar", "hotstar://"),
    "sonyliv": ("SonyLIV", "sonyliv://home"),
    "zee5": ("Zee5", "https://www.zee5.com/"),
}

DEFAULT = {
    "id": "generic",
    "label": "Generic Android TV / Google TV",
    # Standard key; ignored by some TVs (no error is returned, so it can't be detected).
    "input_key": True,
    "inputs": {},
}

_MTK_PASSTHROUGH = "content://android.media.tv/passthrough/com.mediatek.tvinput%2F.hdmi.HDMIInputService%2F"

PROFILES = [
    {
        "id": "changhong-ai-pont",
        "label": "Changhong AI PONT (MediaTek/MStar, Android TV 11)",
        "match": {"manufacturer": "Changhong", "model": "AI PONT"},
        # TV_INPUT and TV_INPUT_HDMI_n are silently ignored over the remote protocol here.
        "input_key": False,
        # HDMI port n = HDMIInputService/HW(n+3), from `dumpsys tv_input`; each switch verified.
        "inputs": {
            "hdmi1": ("HDMI 1", _MTK_PASSTHROUGH + "HW4"),
            "hdmi2": ("HDMI 2", _MTK_PASSTHROUGH + "HW5"),
            "hdmi3": ("HDMI 3", _MTK_PASSTHROUGH + "HW6"),
        },
    },
]


def for_device(manufacturer: str | None, model: str | None) -> dict:
    """Profile matching the TV's reported manufacturer/model, else DEFAULT."""
    for profile in PROFILES:
        m = profile["match"]
        if (manufacturer or "").strip().lower() == m["manufacturer"].lower() and \
           (model or "").strip().lower() == m["model"].lower():
            return profile
    return DEFAULT
