import os
import threading
import time
from pathlib import Path

from clusters import KARISIK, assign_by_keywords

_DIZIN = os.path.dirname(os.path.abspath(__file__))
YT_AUTH_FILE = os.path.join(_DIZIN, "youtube_oauth.json")
YT_BROWSER_FILE = os.path.join(_DIZIN, "youtube_browser.json")

_kilit = threading.RLock()
_durum = {"connecting": False, "error": "", "step": ""}
_ornek = None
_cihaz = {}  # {"cred": OAuthCredentials, "device": {"device_code",...}, "basladi": time}


def _creds():
    try:
        import json
        with open(os.path.join(_DIZIN, "config.json"), encoding="utf-8") as f:
            cfg = json.load(f)
        cid = (cfg.get("youtube_client_id") or "").strip()
        cts = (cfg.get("youtube_client_secret") or "").strip()
        return cid, cts
    except Exception:
        return "", ""


def _ortam():
    try:
        from ytmusicapi import OAuthCredentials, YTMusic
        return YTMusic, OAuthCredentials
    except ImportError:
        return None, None


def is_available():
    YTMusic, _ = _ortam()
    return YTMusic is not None


def is_connected():
    if not is_available():
        return False
    try:
        return _get_yt() is not None
    except Exception:
        return False


def connect_state():
    cid, cts = _creds()
    return {
        "available": is_available(),
        "connected": is_connected(),
        "connecting": bool(_cihaz),
        "creds_ok": bool(cid and cts),
        "browser_mode": os.path.exists(YT_BROWSER_FILE),
        "oauth_mode": os.path.exists(YT_AUTH_FILE),
        "error": _durum["error"],
        "step": _durum["step"],
    }


# ---- Secretsız çerez tabanlı bağlantı (Google OAuth geliştirici hesabı gerektirmez) ----
def _cookie_deger(cookie, ad):
    """basit cookie parseri: 'ad=deger; ...'"""
    for parca in cookie.split(";"):
        ana, _, deger = parca.strip().partition("=")
        if ana == ad:
            return deger.strip()
    return ""


def browser_setup(cookie):
    """Kullanıcının giriş yapmış tarayıcı çereziyle browser-auth başlığını kurar."""
    global _durum, _ornek
    cookie = (cookie or "").strip()
    if not cookie:
        return {"ok": False, "error": "Boş cookie gönderildi."}
    if not _cookie_deger(cookie, "__Secure-3PAPISID"):
        return {"ok": False, "error": "Cookie'da __Secure-3PAPISID bulunamadı. music.youtube.com'da oturum açıp tekrar kopyalayın."}

    import json
    basliklar = {
        "cookie": cookie,
        "origin": "https://music.youtube.com",
        "x-goog-authuser": "0",
        "authorization": "SAPISIDHASH 0_secretsiz-cerez",  # çalışma zamanında yeniden imzalanır
    }
    try:
        from ytmusicapi import YTMusic
        ornek = YTMusic(auth=basliklar.copy())
    except Exception as e:
        _durum["error"] = str(e)
        return {"ok": False, "error": f"Çerez kabul edilmedi: {e}"}

    try:
        sahsiyet = ornek.get_library_playlists(limit=1)
        ornek.get_library_songs(limit=1)
    except Exception as e:
        _durum["error"] = str(e)
        return {"ok": False, "error": f"Bağlantı doğrulanamadı (oturum sona ermiş olabilir): {e}"}

    try:
        with open(YT_BROWSER_FILE, "w", encoding="utf-8") as f:
            json.dump(basliklar, f, ensure_ascii=False, indent=4)
    except Exception as e:
        return {"ok": False, "error": f"Dosya yazılamadı: {e}"}

    with _kilit:
        _ornek = ornek
    _durum["step"] = "cerez"
    _durum["error"] = ""
    _cihaz = {}
    return {"ok": True}


def device_start():
    """Google onay URL'si + user_code uret (device flow). Donmeyen bekletme yok."""
    global _durum, _cihaz
    YTMusic, OAuthCredentials = _ortam()
    if YTMusic is None:
        return {"ok": False, "error": "ytmusicapi yüklenmemiş. `pip install ytmusicapi`"}
    cid, cts = _creds()
    if not (cid and cts):
        return {"ok": False, "error": "config.json'a youtube_client_id ve youtube_client_secret girin (Google Cloud OAuth)."}
    if is_connected():
        return {"ok": True, "done": True}
    with _kilit:
        if _cihaz and time.time() - _cihaz.get("basladi", 0) < 600:
            dev = _cihaz["device"]
            return {"ok": True, "done": False, "verification_url": dev.get("verification_url"), "user_code": dev.get("user_code")}
        try:
            creds = OAuthCredentials(cid, cts)
            code = creds.get_code()
            _cihaz = {
                "cred": creds,
                "device": code,
                "basladi": time.time(),
            }
            _durum["connecting"] = True
            _durum["error"] = ""
            _durum["step"] = "onay"
            return {
                "ok": True,
                "done": False,
                "verification_url": code.get("verification_url"),
                "user_code": code.get("user_code"),
            }
        except Exception as e:
            _cihaz = {}
            _durum["connecting"] = False
            _durum["error"] = str(e)
            return {"ok": False, "error": str(e)}


def device_poll():
    """Onay sekmesini yoklar. Kullanici onaylayinca tokeni kaydeder."""
    global _durum, _ornek, _cihaz
    with _kilit:
        if not _cihaz or not _cihaz.get("device"):
            return {"done": False, "error": "cihaz akışı başlatılmamış. Bağlan'a tekrar basın."}
        creds = _cihaz["cred"]
        device_code = _cihaz["device"].get("device_code")
    try:
        raw = creds.token_from_code(device_code)
    except Exception as e:
        return {"done": False, "error": str(e)}
    if "error" in raw and "access_token" not in raw:
        if raw.get("error") in ("authorization_pending", "slow_down", "expired_token", "access_denied"):
            return {"done": False}
        return {"done": False, "error": str(raw)}
    if "access_token" not in raw:
        return {"done": False}

    try:
        from ytmusicapi.auth.oauth.token import RefreshingToken
        tok = RefreshingToken(
            credentials=creds,
            access_token=raw["access_token"],
            refresh_token=raw.get("refresh_token", ""),
            scope=raw.get("scope", ""),
            token_type=raw.get("token_type", "Bearer"),
            expires_in=int(raw.get("expires_in", 3600)),
        )
        tok.update(raw)
        tok.local_cache = Path(YT_AUTH_FILE)  # otomatik store_token
        with _kilit:
            _ornek = _yeni_ornek(tok)
        _cihaz = {}
        _durum["connecting"] = False
        _durum["step"] = "tamam"
        _durum["error"] = ""
        return {"done": True}
    except Exception as e:
        _durum["error"] = str(e)
        return {"done": False, "error": str(e)}


def _yeni_ornek(token):
    from ytmusicapi import OAuthCredentials, YTMusic
    cid, cts = _creds()
    creds = OAuthCredentials(cid, cts)
    return YTMusic(auth=token, oauth_credentials=creds)


def _get_yt():
    global _ornek
    YTMusic, _ = _ortam()
    if YTMusic is None:
        return None
    if _ornek is not None:
        return _ornek
    cid, cts = _creds()
    with _kilit:
        # 1) OAuth tokeni varsa onu kullan
        if os.path.exists(YT_AUTH_FILE) and cid and cts:
            from ytmusicapi import OAuthCredentials
            creds = OAuthCredentials(cid, cts)
            try:
                _ornek = YTMusic(auth=YT_AUTH_FILE, oauth_credentials=creds)
                return _ornek
            except Exception:
                _ornek = None
        # 2) Secret'sız çerez dosyası varsa onu kullan
        if os.path.exists(YT_BROWSER_FILE):
            try:
                _ornek = YTMusic(auth=YT_BROWSER_FILE)
                return _ornek
            except Exception:
                _ornek = None
    return None


def _thumbnails(track):
    th = track.get("thumbnails") or []
    return th[-1]["url"] if th else ""


def _normalize(track):
    artistler = [a.get("name", "") for a in (track.get("artists") or []) if a]
    album = (track.get("album") or {}).get("name", "")
    metin = " ".join([track.get("title", ""), " ".join(artistler), album])
    return {
        "provider": "youtube",
        "id": track.get("videoId", ""),
        "name": track.get("title", ""),
        "artist": ", ".join(artistler),
        "album": album,
        "image": _thumbnails(track),
        "url": f"https://music.youtube.com/watch?v={track.get('videoId', '')}" if track.get("videoId") else "",
        "preview_url": "",
        "tempo": None,
        "energy": None,
        "danceability": None,
        "popularity": 0,
        "mood": assign_by_keywords(metin) or KARISIK,
    }


def get_liked_songs(limit=400):
    with _kilit:
        yt = _get_yt()
    if yt is None:
        return []
    try:
        sonuc = yt.get_liked_songs(limit=limit)
        satirlar = sonuc.get("tracks", [])
        parcalar = []
        for satir in satirlar:
            track = satir.get("track") or satir
            if not track.get("videoId"):
                continue
            parcalar.append(_normalize(track))
        return parcalar
    except Exception:
        return []


def get_radio(video_id, limit=25):
    with _kilit:
        yt = _get_yt()
    if yt is None:
        return []
    try:
        sonuc = yt.get_watch_playlist(videoId=video_id, limit=limit, radio=True)
        satirlar = sonuc.get("tracks", []) or []
        parcalar = []
        for satir in satirlar:
            track = satir.get("track") or satir
            if not track.get("videoId"):
                continue
            if track["videoId"] == video_id:
                continue
            parcalar.append(_normalize(track))
        return parcalar
    except Exception:
        return []