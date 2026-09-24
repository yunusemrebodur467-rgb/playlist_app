import json
import os
import secrets
import time

import requests

from clusters import KARISIK, assign_by_features, assign_by_keywords

BASE = "https://api.spotify.com/v1"
AUTH = "https://accounts.spotify.com/api/token"
PORT = 5001
REDIRECT = f"http://127.0.0.1:{PORT}/api/callback"
SCOPE = "user-library-read"

_DIZIN = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(_DIZIN, "config.json")
TOKEN_PATH = os.path.join(_DIZIN, "spotify_token.json")

# Etkinlik -> dinlence kriterleri (nosearch / keşfet sekmesi icin).
MOODS = {
    "spor":    {"min_bpm": 118, "max_bpm": 150, "min_energy": 0.65, "min_dance": 0.55, "max_energy": 1.0, "limit": 12},
    "eglen":   {"min_bpm": 105, "max_bpm": 130, "min_energy": 0.60, "min_dance": 0.65, "max_energy": 1.0, "limit": 12},
    "calisma": {"min_bpm": 82,  "max_bpm": 110, "min_energy": 0.25, "min_dance": 0.30, "max_energy": 0.60, "limit": 10},
    "chill":   {"min_bpm": 88,  "max_bpm": 112, "min_energy": 0.35, "min_dance": 0.35, "max_energy": 0.70, "limit": 10},
    "uyku":    {"min_bpm": 60,  "max_bpm": 84,  "min_energy": 0.10, "min_dance": 0.10, "max_energy": 0.30, "limit": 8},
}

_cache = {}
_oauth_state = {"state": "", "basladi": 0}


def new_oauth_state():
    """CSRF korumasi icin kayitli state uretir ve dogrulamak uzere hatirlar."""
    _oauth_state["state"] = secrets.token_hex(16)
    _oauth_state["basladi"] = time.time()
    return _oauth_state["state"]


def check_oauth_state(gelen):
    """State'i dogrular (en fazla 15 dk gecerli). Tek kullanimlik."""
    if not gelen or not _oauth_state["state"]:
        return False
    if time.time() - _oauth_state["basladi"] > 900:
        _oauth_state["state"] = ""
        return False
    ok = secrets.compare_digest(gelen, _oauth_state["state"])
    _oauth_state["state"] = ""
    return ok


def _load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"client_id": "", "client_secret": ""}


def has_credentials():
    cfg = _load_config()
    return bool(cfg.get("client_id")) and bool(cfg.get("client_secret"))


def authorize_url():
    cfg = _load_config()
    import urllib.parse
    prm = {
        "client_id": cfg["client_id"],
        "response_type": "code",
        "redirect_uri": REDIRECT,
        "scope": SCOPE,
        "state": new_oauth_state(),
    }
    return "https://accounts.spotify.com/authorize?" + urllib.parse.urlencode(prm)


def _save_token(token):
    with open(TOKEN_PATH, "w", encoding="utf-8") as f:
        json.dump(token, f, ensure_ascii=False)
    _cache.pop("user_token", None)


def exchange_code(code):
    cfg = _load_config()
    r = requests.post(
        AUTH,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT,
        },
        auth=(cfg["client_id"], cfg["client_secret"]),
        timeout=15,
    )
    r.raise_for_status()
    tok = r.json()
    _save_token({
        "access_token": tok["access_token"],
        "refresh_token": tok.get("refresh_token", ""),
        "expires_at": time.time() + tok.get("expires_in", 3600),
    })


def _refresh():
    cfg = _load_config()
    with open(TOKEN_PATH, encoding="utf-8") as f:
        tok = json.load(f)
    rt = tok.get("refresh_token", "")
    if not rt:
        raise RuntimeError("refresh_token yok; yeniden giriş gerekli")
    r = requests.post(
        AUTH,
        data={"grant_type": "refresh_token", "refresh_token": rt},
        auth=(cfg["client_id"], cfg["client_secret"]),
        timeout=15,
    )
    if r.status_code == 400:
        # invalid_grant: token iptal edilmis / uygulama izni kaldirilmis
        clear_token()
        raise RuntimeError("Oturum geçersiz; yeniden Spotify bağlantısı gerekli")
    r.raise_for_status()
    t = r.json()
    _save_token({
        "access_token": t["access_token"],
        "refresh_token": t.get("refresh_token") or tok.get("refresh_token", ""),
        "expires_at": time.time() + t.get("expires_in", 3600),
    })


def _user_token():
    cached = _cache.get("user_token")
    if cached and cached[1] > time.time() + 60:
        return cached[0]
    if not os.path.exists(TOKEN_PATH):
        return None
    try:
        with open(TOKEN_PATH, encoding="utf-8") as f:
            tok = json.load(f)
    except Exception:
        return None
    if tok.get("expires_at", 0) <= time.time() + 60 and tok.get("refresh_token"):
        try:
            _refresh()
        except Exception:
            return None
        with open(TOKEN_PATH, encoding="utf-8") as f:
            tok = json.load(f)
    if not tok.get("access_token"):
        return None
    _cache["user_token"] = (tok["access_token"], tok.get("expires_at", 0))
    return tok["access_token"]


def clear_token():
    _cache.pop("user_token", None)
    if os.path.exists(TOKEN_PATH):
        os.remove(TOKEN_PATH)


def _headers(oya=None):
    return {"Authorization": f"Bearer {oya or _get_token()}"}


def _get_token():
    """Client-credentials tokenı; secret yoksa çerez (sp_dc) tokenına düşer."""
    cached = _cache.get("token")
    if cached and cached["expires"] > time.time():
        return cached["value"]
    if has_credentials():
        cfg = _load_config()
        r = requests.post(
            AUTH,
            data={"grant_type": "client_credentials"},
            auth=(cfg["client_id"], cfg["client_secret"]),
            timeout=10,
        )
        r.raise_for_status()
        token = r.json()["access_token"]
        _cache["token"] = {"value": token, "expires": time.time() + 3300}
        return token
    try:
        import spotify_cookie
        return spotify_cookie._token()
    except Exception:
        raise RuntimeError("Spotify için ne client_credentials ne de çerez tokeni var.")


def _http_get(path, params=None, oya=None):
    r = requests.get(BASE + path, headers=_headers(oya), params=params, timeout=20)
    r.raise_for_status()
    return r.json()


def _http_post(path, data=None, oya=None):
    r = requests.post(BASE + path, headers=_headers(oya), json=data, timeout=20)
    r.raise_for_status()
    return r.json()


def is_connected():
    try:
        return bool(_user_token())
    except Exception:
        return False


def user_profile():
    tok = _user_token()
    if not tok:
        return None
    return user_profile_with_token(tok)


def user_profile_with_token(tok):
    try:
        me = _http_get("/me", oya=tok)
        return {"id": me.get("id"), "name": me.get("display_name") or me.get("id")}
    except Exception:
        return None


def get_liked_tracks(limit=300):
    tok = _user_token()
    if not tok:
        return []
    parcalar = []
    offset = 0
    while len(parcalar) < limit:
        data = _http_get("/me/tracks", {"limit": 50, "offset": offset}, oya=tok)
        items = data.get("items", [])
        if not items:
            break
        for it in items:
            t = it.get("track") or {}
            parcalar.append(_normalize_track(t, tok))
        offset += 50
        if offset > 1000:
            break
    return parcalar[:limit]


def _normalize_track(t, tok=None):
    album = t.get("album") or {}
    feats = _audio_features([t.get("id")], tok).get(t.get("id"))
    mood = assign_by_features(feats) if feats else KARISIK
    return {
        "provider": "spotify",
        "id": t.get("id", ""),
        "name": t.get("name", ""),
        "artist": ", ".join(a.get("name", "") for a in t.get("artists", [])),
        "album": album.get("name", ""),
        "image": (album.get("images") or [{}])[0].get("url", ""),
        "url": t.get("external_urls", {}).get("spotify", ""),
        "preview_url": t.get("preview_url", ""),
        "tempo": round(feats.get("tempo")) if feats and feats.get("tempo") else None,
        "energy": round(feats.get("energy"), 2) if feats else None,
        "danceability": round(feats.get("danceability"), 2) if feats else None,
        "popularity": t.get("popularity", 0),
        "mood": mood,
    }


def _audio_features(ids, tok=None):
    gecerli = [i for i in ids if i]
    if not gecerli:
        return {}
    sonuc = {}
    for i in range(0, len(gecerli), 100):
        donem = gecerli[i:i + 100]
        data = _http_get("/audio-features", {"ids": ",".join(donem)}, oya=tok or _get_token())
        for f in data.get("audio_features", []):
            if f:
                sonuc[f["id"]] = f
    return sonuc


def get_recommendations(seed_ids, limit=20):
    tok = _user_token()
    oya = tok or _get_token()
    ids = seed_ids[:5]
    if not ids:
        return []
    try:
        data = _http_get(
            "/recommendations",
            {"seed_tracks": ",".join(ids), "limit": limit, "market": "TR"},
            oya=oya,
        )
        return [_normalize_track(t, oya) for t in data.get("tracks", [])]
    except Exception:
        return []


# ---- Keşfet: pop arama (demo fallback'li) ----
def _fetch_tracks():
    key = "tracks_pop_tr"
    if key in _cache:
        return _cache[key]
    data = _http_get(
        "/search",
        {"q": "genre:pop", "type": "track", "market": "TR", "limit": 50},
    )
    _cache[key] = data["tracks"]["items"]
    return _cache[key]


def build_playlist(mood):
    if mood not in MOODS:
        mood = "calisma"
    kriter = MOODS[mood]

    if not has_credentials():
        try:
            import spotify_cookie
            if not (spotify_cookie.is_connected()):
                return _demo_playlist(mood, kriter, reason="config")
        except Exception:
            return _demo_playlist(mood, kriter, reason="config")

    try:
        items = _fetch_tracks()
        ids = [t["id"] for t in items]
        feats = _audio_features(ids)

        secilen = []
        for t in items:
            f = feats.get(t["id"])
            if not f:
                continue
            bpm = f.get("tempo") or 0
            if not (kriter["min_bpm"] <= bpm <= kriter["max_bpm"]):
                continue
            enerji = f.get("energy") or 0
            if not (kriter["min_energy"] <= enerji <= kriter["max_energy"]):
                continue
            if f.get("danceability", 0) < kriter["min_dance"]:
                continue
            secilen.append({
                "name": t["name"],
                "artist": ", ".join(a["name"] for a in t.get("artists", [])),
                "album": (t.get("album") or {}).get("name", ""),
                "image": (t.get("album", {}).get("images") or [{}])[0].get("url", ""),
                "spotify_url": t.get("external_urls", {}).get("spotify", ""),
                "preview_url": t.get("preview_url", ""),
                "tempo": round(bpm),
                "energy": round(f.get("energy", 0), 2),
                "danceability": round(f.get("danceability", 0), 2),
                "popularity": t.get("popularity", 0),
            })

        secilen.sort(key=lambda s: s["popularity"], reverse=True)
        return {"mood": mood, "live": True, "total": len(secilen), "tracks": secilen[: kriter["limit"]]}
    except Exception as e:
        return _demo_playlist(mood, kriter, reason=str(e))


# ---- Demo modu: API bilgisi / baglanti yoksa ornek pop listesi ----
def _demo_playlist(mood, kriter, reason=""):
    demo_pool = [
        ("Blinding Lights", "The Weeknd", "After Hours", 171, 0.79, 0.72, 93),
        ("Dance Monkey", "Tones and I", "The Kids Are Coming", 98, 0.61, 0.82, 91),
        ("Levitating", "Dua Lipa", "Future Nostalgia", 103, 0.79, 0.75, 94),
        ("Shape of You", "Ed Sheeran", "\u00f7", 96, 0.65, 0.83, 92),
        ("Mood", "24kGoldn feat. Iann Dior", "El Dorado", 91, 0.67, 0.75, 90),
        ("Stay", "The Kid LAROI & Justin Bieber", "F*CK LOVE", 83, 0.74, 0.72, 93),
        ("good 4 u", "Olivia Rodrigo", "SOUR", 166, 0.88, 0.58, 92),
        ("Peaches", "Justin Bieber feat. Daniel Caesar", "Justice", 90, 0.56, 0.70, 90),
        ("Save Your Tears", "The Weeknd", "After Hours", 118, 0.70, 0.68, 92),
        ("Watermelon Sugar", "Harry Styles", "Fine Line", 95, 0.53, 0.55, 90),
        ("Kiss Me More", "Doja Cat feat. SZA", "Planet Her", 111, 0.69, 0.82, 94),
        ("positions", "Ariana Grande", "positions", 144, 0.48, 0.73, 90),
        ("Heat Waves", "Glass Animals", "Dreamland", 81, 0.61, 0.75, 93),
        ("Astronaut In The Ocean", "Masked Wolf", "Astronaut", 97, 0.79, 0.78, 90),
        ("golden hour", "JVKE", "golden hour", 94, 0.48, 0.56, 92),
        ("About Damn Time", "Lizzo", "Special", 109, 0.75, 0.83, 90),
        ("As It Was", "Harry Styles", "Harry's House", 87, 0.65, 0.50, 95),
        ("Woman", "Doja Cat", "Planet Her", 109, 0.77, 0.86, 91),
        ("Don't Start Now", "Dua Lipa", "Future Nostalgia", 124, 0.79, 0.79, 93),
        ("Cold Heart", "Elton John & Dua Lipa", "The Lockdown Sessions", 116, 0.61, 0.75, 94),
        ("Bad Habit", "Steve Lacy", "Gemini Rights", 102, 0.50, 0.70, 90),
        ("Unholy", "Sam Smith & Kim Petras", "Gloria", 131, 0.83, 0.71, 95),
        ("Anti-Hero", "Taylor Swift", "Midnights", 97, 0.63, 0.66, 94),
        ("Flowers", "Miley Cyrus", "Endless Summer Vacation", 118, 0.68, 0.69, 94),
        ("Calm Down", "Rema & Selena Gomez", "Rave & Roses", 107, 0.74, 0.83, 92),
        ("Die For You", "The Weeknd", "Starboy (Deluxe)", 134, 0.64, 0.72, 93),
        ("STAYING ALIVE", "DJ Khaled ft. Drake", "STAYING ALIVE", 138, 0.76, 0.74, 91),
        ("CUFF IT", "Beyonc\u00e9", "RENAISSANCE", 115, 0.78, 0.88, 91),
        ("Pink Venom", "BLACKPINK", "BORN PINK", 90, 0.72, 0.67, 93),
        ("Gimme More", "Britney Spears", "Blackout", 113, 0.73, 0.76, 93),
        ("Lose You To Love Me", "Selena Gomez", "Rare", 85, 0.32, 0.36, 89),
        ("lovely", "Billie Eilish & Khalid", "dont smile at me", 62, 0.11, 0.23, 91),
        ("when the party's over", "Billie Eilish", "WHEN WE ALL FALL ASLEEP", 66, 0.10, 0.18, 88),
        ("Ocean Eyes", "Billie Eilish", "dont smile at me", 72, 0.26, 0.38, 87),
        ("Talking to the Moon", "Bruno Mars", "Doo-Wops & Hooligans", 100, 0.30, 0.34, 91),
        ("Perfect", "Ed Sheeran", "\u00f7", 95, 0.31, 0.40, 97),
        ("Photograph", "Ed Sheeran", "x", 108, 0.35, 0.46, 94),
        ("Someone Like You", "Adele", "21", 67, 0.25, 0.40, 92),
        ("Sunday Morning", "Maroon 5", "Songs About Jane", 93, 0.44, 0.68, 90),
        ("Silence", "Marshmello & Khalid", "Silence", 142, 0.51, 0.63, 92),
        ("Faded", "Alan Walker", "Faded", 90, 0.43, 0.26, 88),
    ]

    secilen = []
    for name, artist, album, tempo, energy, dance, pop in demo_pool:
        if not (kriter["min_bpm"] <= tempo <= kriter["max_bpm"]):
            continue
        if not (kriter["min_energy"] <= energy <= kriter["max_energy"]):
            continue
        if dance < kriter["min_dance"]:
            continue
        secilen.append({
            "name": name, "artist": artist, "album": album,
            "tempo": tempo, "energy": energy, "danceability": dance,
            "popularity": pop, "image": "", "spotify_url": "",
            "preview_url": "", "provider": "spotify", "mood": KARISIK,
        })
    secilen.sort(key=lambda s: s["popularity"], reverse=True)
    return {
        "mood": mood, "live": False, "demo": True, "reason": reason,
        "total": len(secilen), "tracks": secilen[: kriter["limit"]],
    }