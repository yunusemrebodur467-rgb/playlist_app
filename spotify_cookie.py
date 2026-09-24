import json
import os
import time

import requests

import spotify_provider as spo

CEREZ_DOSYA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spotify_cookie.json")
TOKEN_DOSYA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spotify_cookie_token.json")
BEYENI_DOSYA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spotify_liked_cache.json")

# Spotify Web Player'in herkese açık OAuth client'i — sadece oturum çerezi (sp_dc) gerekir.
CLIENT_ID = "65b708073fc0480ea92a077233ca87bd"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
TOKEN_URL = "https://open.spotify.com/get_access_token"

_cache = {}


def _kaydet_token(t):
    with open(TOKEN_DOSYA, "w", encoding="utf-8") as f:
        json.dump(t, f, ensure_ascii=False)
    _cache.pop("token", None)


def clear():
    _cache.pop("token", None)
    for p in (CEREZ_DOSYA, TOKEN_DOSYA, BEYENI_DOSYA):
        if os.path.exists(p):
            os.remove(p)


def _cerezli_session(cookie):
    """Cookie: 'sp_dc=...' ya da 'ad=val; ad2=val2' — hepsini oturuma kurar."""
    session = requests.Session()
    from http.cookies import SimpleCookie

    sc = SimpleCookie()
    sc.load(cookie)
    for ad, morsel in sc.items():
        if ad and morsel.value:
            session.cookies.set(ad, morsel.value, domain=".spotify.com", path="/")
    return session


def _sp_dc_deger(cookie):
    """Cookie metninden sp_dc değerini ayıklar; yoksa None."""
    from http.cookies import SimpleCookie

    sc = SimpleCookie()
    sc.load(cookie)
    m = sc.get("sp_dc")
    return m.value if m and m.value else None


def _fetch_access_token_v1(cookie, future=False):
    """Tek varyant denemesi. Başarı → (at, bitis); hata → RuntimeError."""
    session = _cerezli_session(cookie)
    prm = {"reason": "transport", "productType": "web_player"}
    if future:
        prm["future"] = "true"
    r = session.get(
        TOKEN_URL,
        params=prm,
        headers={"User-Agent": UA, "Referer": "https://open.spotify.com/"},
        timeout=20,
    )
    if r.status_code != 200:
        raise RuntimeError(f"get_access_token {r.status_code}: {r.text[:160]}")
    g = r.json()
    at = g.get("accessToken")
    if not at:
        raise RuntimeError("get_access_token accessToken döndürmedi.")
    bitis_ms = g.get("accessTokenExpirationTimestampMs", 0)
    bitis = (bitis_ms / 1000.0 - 120) if bitis_ms else time.time() + 3600
    return at, bitis


def _fetch_access_token_v2(cookie):
    """sp_dc'yi yalnızca oturuma kurar (saf yöntem), iki kez dener."""
    from http.cookies import SimpleCookie

    sc = SimpleCookie()
    sc.load(cookie)
    spdc = sc.get("sp_dc")
    if not spdc or not spdc.value:
        raise RuntimeError("sp_dc çerezinde sp_dc yok.")
    denemeler = [
        {"cookie": "sp_dc=" + spdc.value, "future": False},
        {"cookie": "sp_dc=" + spdc.value, "future": True},
    ]
    for d in denemeler:
        try:
            return _fetch_access_token_v1(d["cookie"], d["future"])
        except RuntimeError:
            continue
    raise RuntimeError("get_access_token sp_dc ile de başarısız oldu.")


def _fetch_access_token():
    """Önce saf sp_dc, olmazsa tam çerez listesi ile dener."""
    if not os.path.exists(CEREZ_DOSYA):
        raise RuntimeError("Spotify çerez bağlantısı yok.")
    try:
        with open(CEREZ_DOSYA, encoding="utf-8") as f:
            kayit = json.load(f)
    except Exception:
        raise RuntimeError("Spotify çerez kaydı okunamadı.")
    cookie = kayit.get("cookie") or ("sp_dc=" + (kayit.get("sp_dc") or ""))
    if "=" not in cookie:
        cookie = "sp_dc=" + cookie

    try:
        return _fetch_access_token_v2(cookie)
    except RuntimeError as e:
        son_hata = str(e)
    try:
        return _fetch_access_token_v1(cookie)
    except RuntimeError as e2:
        raise RuntimeError(f"{son_hata} | tam liste: {e2}")


def _token():
    """Eklentinin ilettiği token'ı döndürür; süresi dolduysa hata verir (eklenti yeniler)."""
    kis = _cache.get("token")
    if kis and kis[1] > time.time() + 60:
        return kis[0]
    if not os.path.exists(TOKEN_DOSYA):
        raise RuntimeError("Spotify çerez tokenı yok; eklentiyle bağlan.")
    try:
        with open(TOKEN_DOSYA, encoding="utf-8") as f:
            tok = json.load(f)
    except Exception:
        raise RuntimeError("Spotify çerez tokeni okunamadı.")
    if tok.get("expires_at", 0) <= time.time() + 60:
        _cache.pop("token", None)
        raise RuntimeError("Spotify token süresi doldu; eklentiyle yenile.")
    at = tok.get("access_token", "")
    if not at:
        raise RuntimeError("access_token yok.")
    _cache["token"] = (at, tok.get("expires_at", 0))
    return at


SESSIZ_KABUL = 0  # epoch; bu ana kadar /v1/me denemesi yapma

def _dogrula(at, deneme=0):
    """Token'ı /v1/me ile doğrular. 401/403 → geçersiz (reddet). 429 → kısıt,
    token bozuk olmayabilir: kabul et ama doğrulama askıda işaretle (retry=True).
    Ayrıca 429'da daha sabırlı yeniden dener ve kısıt penceresi boyunca sessiz kabul yapar."""
    global SESSIZ_KABUL
    if time.time() < SESSIZ_KABUL:
        return {"ok": True, "retry": True, "profil": None,
                "hata": "429 kısıt penceresi bekleniyor; doğrulama birazdan tamamlanır."}
    try:
        me = spo._http_get("/me", oya=at)
        return {"ok": True, "profil": {"id": me.get("id"), "name": me.get("display_name") or me.get("id")}}
    except requests.exceptions.HTTPError as e:
        durum = getattr(e.response, "status_code", None)
        if durum == 429:
            ra = e.response.headers.get("Retry-After")
            bir = ""
            try:
                bir = int(ra or "0")
            except ValueError:
                bir = 30
            if not bir:
                bir = 30
            # Kısıt kalıcı/global; art arda denemek kısıtı daha da besler.
            # Token muhtemelen geçerli (web player kullanıyor) — kabul et, API'yi yorma.
            SESSIZ_KABUL = time.time() + bir + 10
            return {"ok": True, "retry": True, "profil": None,
                    "hata": "429 geçici kısıt; bağlantı kuruldu, doğrulama birazdan tamamlanır."}
        if durum in (401, 403, 400):
            return {"ok": False, "hata": f"Token geçersiz (HTTP {durum}) — Spotify'da oturum açıksa eklentiden yeniden bağlan."}
        return {"ok": False, "hata": f"Token doğrulama hatası (HTTP {durum or '?'})"}
    except Exception as ex:
        return {"ok": False, "hata": f"Token doğrulama hatası: {ex}"}


def is_connected():
    try:
        return bool(_token())
    except Exception:
        return False


def user_profile():
    try:
        return spo.user_profile_with_token(_token())
    except Exception:
        return None


def _normalize_graphql_track(t):
    """Web player'ın api-partner graphql yanıtındaki tek bir parçayı standart dict'e çevirir."""
    album = t.get("albumOfTrack") or {}
    kapak = (album.get("coverArt") or {}).get("sources") or []
    img = ""
    if kapak:
        kapak_sirali = sorted(kapak, key=lambda s: s.get("width", 0), reverse=True)
        img = kapak_sirali[0].get("url", "")
    sanatcilar = ((t.get("artists") or {}).get("items")) or []
    sanatci = ", ".join((a.get("profile") or {}).get("name", "") for a in sanatcilar if a.get("profile"))
    uri = t.get("uri", "")
    pid = (uri.split(":")[-1]) if uri else (t.get("id") or "")
    return {
        "provider": "spotify",
        "id": pid,
        "name": t.get("name", ""),
        "artist": sanatci,
        "album": album.get("name", ""),
        "image": img,
        "url": ("https://open.spotify.com/track/" + pid) if pid else "",
        "preview_url": "",
        "tempo": None,
        "energy": None,
        "danceability": None,
        "popularity": 0,
        "mood": None,
    }


def _kaydet_beyeni(parcalar):
    """Eklentiden gelen beğeni listesini cache dosyasına yazar."""
    _cache["liked"] = parcalar
    with open(BEYENI_DOSYA, "w", encoding="utf-8") as f:
        json.dump(parcalar, f, ensure_ascii=False)


def _beyeni_oku():
    """Cache'teki beğeni listesini döndürür; yoksa [-2]."""
    ks = _cache.get("liked")
    if ks is None and os.path.exists(BEYENI_DOSYA):
        try:
            with open(BEYENI_DOSYA, encoding="utf-8") as f:
                ks = json.load(f)
            _cache["liked"] = ks
        except Exception:
            ks = None
    return ks if ks is not None else None


def refine_beyeni_moodlari():
    """Cache'teki beğenilere müzik özelliği yoksa (GraphQL), radyo önerileri için
    audio-features'ı akıllıca çekmeyi deneyen genişletme kancası. Şimdilik no-op (429 riski)."""
    return None


def get_liked_tracks(limit=300):
    ks = _beyeni_oku()
    if ks is not None:
        return ks[:limit]
    tok = _token()
    parcalar = []
    offset = 0
    while len(parcalar) < limit:
        data = spo._http_get("/me/tracks", {"limit": 50, "offset": offset}, oya=tok)
        items = data.get("items", [])
        if not items:
            break
        for it in items:
            t = it.get("track") or {}
            if not t.get("id"):
                continue
            parcalar.append(spo._normalize_track(t, tok))
        offset += 50
        if offset > 1000:
            break
    if parcalar:
        _kaydet_beyeni(parcalar)
    return parcalar[:limit]


def get_recommendations(seed_ids, limit=20):
    tok = _token()
    ids = seed_ids[:5]
    if not ids:
        return []
    try:
        data = spo._http_get(
            "/recommendations",
            {"seed_tracks": ",".join(ids), "limit": limit, "market": "TR"},
            oya=tok,
        )
        return [spo._normalize_track(t, tok) for t in data.get("tracks", [])]
    except Exception:
        return []


def authorize(cookie):
    """Cookie: 'sp_dc=...' ya da tam çerez listesi alır, kaydeder, token dener."""
    cookie = (cookie or "").strip()
    if not cookie:
        return {"ok": False, "error": "Boş sp_dc çerezi gönderildi."}
    if "=" not in cookie:
        cookie = "sp_dc=" + cookie
    try:
        with open(CEREZ_DOSYA, "w", encoding="utf-8") as f:
            json.dump({"cookie": cookie}, f, ensure_ascii=False)
        _cache.pop("token", None)
        _fetch_access_token()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}