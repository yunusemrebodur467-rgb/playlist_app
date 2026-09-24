import json
import os
import time

_DIZIN = os.path.dirname(os.path.abspath(__file__))
HEARTBEAT = os.path.join(_DIZIN, "bridge_heartbeat.json")

_AKTIF = 300  # saniye: köprü son temasa bu kadar yakınsa "aktif"


def _yaz(d):
    try:
        with open(HEARTBEAT, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def note(sp=False, yt=False):
    d = _oku()
    now = time.time()
    if sp:
        d["sp_at"] = now
    if yt:
        d["yt_at"] = now
    d["last_seen"] = now
    _yaz(d)


def _oku():
    try:
        with open(HEARTBEAT, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"last_seen": 0, "sp_at": 0, "yt_at": 0}


def recent():
    d = _oku()
    now = time.time()
    ay = lambda t: (now - t) < _AKTIF
    return {
        "last_seen": d.get("last_seen", 0),
        "active": bool(d.get("last_seen") and ay(d["last_seen"])),
        "spotify": bool(d.get("sp_at") and ay(d["sp_at"])),
        "youtube": bool(d.get("yt_at") and ay(d["yt_at"])),
    }


def clear():
    if os.path.exists(HEARTBEAT):
        os.remove(HEARTBEAT)