from flask import Flask, jsonify, request, send_from_directory

import json
import os

import clusters
import bridge
import spotify_cookie
import spotify_provider
import youtube_provider

app = Flask(__name__, static_folder="static")

_Port = 5001


@app.after_request
def _cors(r):
    r.headers["Access-Control-Allow-Origin"] = "*"
    r.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    r.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    r.headers["Access-Control-Allow-Private-Network"] = "true"
    return r


@app.before_request
def _options():
    if request.method == "OPTIONS":
        return ("", 204)


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/moods")
def moods():
    return jsonify({"moods": list(spotify_provider.MOODS.keys()), "live": spotify_provider.has_credentials()})


@app.route("/api/playlist")
def playlist():
    mood = request.args.get("mood", "calisma")
    return jsonify(spotify_provider.build_playlist(mood))


@app.route("/api/status")
def status():
    oy = spotify_provider.is_connected()
    ck = spotify_cookie.is_connected()
    sp = oy or ck
    prof = (spotify_provider.user_profile() if oy else spotify_cookie.user_profile()) if sp else None
    if oy:
        method = "oauth"
    elif ck:
        method = "cookie"
    else:
        method = "none"
    yty = youtube_provider.connect_state()
    return jsonify({
        "spotify": {
            "configured": spotify_provider.has_credentials(),
            "connected": bool(sp),
            "method": method,
            "user": prof,
            "error": "" if sp else ("credential yok" if not spotify_provider.has_credentials() else "oturum yok"),
        },
        "youtube": yty,
        "bridge": bridge.recent(),
        "moods": clusters.mood_meta(),
    })


@app.route("/api/connect/spotify")
def connect_spotify():
    if not spotify_provider.has_credentials():
        return jsonify({"ok": False, "hata": "config.json'a client_id ve client_secret girin.", "cookie": True})
    return jsonify({"ok": True, "redirect": spotify_provider.authorize_url()})


@app.route("/api/youtube/start", methods=["POST"])
def youtube_start():
    return jsonify(youtube_provider.device_start())


@app.route("/api/youtube/poll")
def youtube_poll():
    return jsonify(youtube_provider.device_poll())


@app.route("/api/youtube/browser", methods=["GET", "POST"])
def youtube_browser():
    if request.method == "GET":
        return jsonify(youtube_provider.connect_state())
    cookie = (request.get_json(silent=True) or {}).get("cookie") or request.form.get("cookie") or ""
    return jsonify(youtube_provider.browser_setup(cookie))


@app.route("/api/spotify/cookie", methods=["GET", "POST"])
def spotify_cookie_endpoint():
    if request.method == "GET":
        return jsonify({"connected": spotify_cookie.is_connected()})
    cookie = (request.get_json(silent=True) or {}).get("cookie") or request.form.get("cookie") or ""
    return jsonify(spotify_cookie.authorize(cookie))


# ---- Chrome köprü eklentisi (kullanıcı kendi tarayıcı oturumunu iletir) ----
@app.route("/api/bridge/status")
def bridge_status():
    return jsonify(bridge.recent())


@app.route("/api/bridge/log", methods=["POST"])
def bridge_log():
    g = request.get_json(silent=True) or {}
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "bridge_log.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(g, ensure_ascii=False) + "\n")
    except Exception:
        pass
    return jsonify({"ok": True})


@app.route("/api/bridge/spotify/cookie", methods=["POST"])
def bridge_spotify_cookie():
    g = request.get_json(silent=True) or {}
    cookie = (g.get("cookie") or "").strip()
    if not cookie:
        return jsonify({"ok": False, "hata": "sp_dc boş."})
    sonuc = spotify_cookie.authorize(cookie)
    if sonuc.get("ok"):
        bridge.note(sp=True)
    return jsonify(sonuc)


@app.route("/api/bridge/spotify/token", methods=["POST"])
def bridge_spotify_token():
    g = request.get_json(silent=True) or {}
    at = (g.get("access_token") or "").strip()
    if not at:
        return jsonify({"ok": False, "hata": "access_token eksik."})
    import time as _t
    try:
        dogru = spotify_cookie._dogrula(at)
    except Exception as e:
        return jsonify({"ok": False, "hata": f"token doğrulanamadı: {e}"})
    if not dogru.get("ok"):
        return jsonify({"ok": False, "hata": dogru.get("hata", "Token geçersiz (anonim/kısıtlı oturum olabilir)." )})
    spotify_cookie._kaydet_token({
        "access_token": at,
        "refresh_token": (g.get("refresh_token") or "").strip(),
        "expires_at": _t.time() + int(g.get("expires_in", 1800)),
    })
    bridge.note(sp=True)
    return jsonify({"ok": True})


@app.route("/api/bridge/spotify/liked", methods=["POST"])
def bridge_spotify_liked():
    """Eklenti, web player'ın api-partner graphql yanıtından yakaladığı beğeni
    listesini ({ "tracks": [ ...graphql track... ] }) iletir; 429 baypas edilir."""
    g = request.get_json(silent=True) or {}
    raw = g.get("tracks") or g.get("lista") or []
    if not isinstance(raw, list) or not raw:
        return jsonify({"ok": False, "hata": "tracks listesi boş."})
    parcalar = []
    for t in raw:
        if not isinstance(t, dict):
            continue
        if not any(k in t for k in ("uri", "id")):
            continue
        parcalar.append(spotify_cookie._normalize_graphql_track(t))
    if not parcalar:
        return jsonify({"ok": False, "hata": "Track öğeleri çözülemedi."})
    spotify_cookie._kaydet_beyeni(parcalar)
    bridge.note(sp=True)
    return jsonify({"ok": True, "adet": len(parcalar)})


@app.route("/api/bridge/youtube/cookie", methods=["POST"])
def bridge_youtube_cookie():
    g = request.get_json(silent=True) or {}
    cookie = (g.get("cookie") or "").strip()
    if not cookie:
        return jsonify({"ok": False, "hata": "Cookie boş."})
    sonuc = youtube_provider.browser_setup(cookie)
    if sonuc.get("ok"):
        bridge.note(yt=True)
    return jsonify(sonuc)


@app.route("/api/callback")
def callback():
    hata = request.args.get("error")
    if hata:
        return _callback_html(f"Spotify girişinde hata: {hata}")
    if not spotify_provider.check_oauth_state(request.args.get("state", "")):
        return _callback_html("Güvenlik (state) doğrulaması başarısız — bağlantıyı yeniden başlatın.")
    kod = request.args.get("code")
    if not kod:
        return _callback_html("Authorization code alınamadı.")
    try:
        spotify_provider.exchange_code(kod)
    except Exception as e:
        return _callback_html(f"Token alınamadı: {e}")
    return _callback_html("Spotify bağlandı!")


def _callback_html(mesaj):
    return f"""<!DOCTYPE html><html lang="tr"><head><meta charset="utf-8">
<title>{mesaj}</title><style>body{{font-family:sans-serif;padding:40px;text-align:center}}
a{{color:#1db954}}</style></head><body>
<h2>{"✅" if "bağlandı" in mesaj or "hata" not in mesaj else "⚠️"} {mesaj}</h2>
<p id="kapan">Bu sekme kapanıyor...</p>
<script>
  var kapandi = false;
  if (window.opener) {{ try {{ window.opener.location.reload(); kapandi = true; }} catch(e){{}} }}
  setTimeout(function(){{
    if (window.opener) {{ window.close(); }}
    else {{ location.href = "/"; }}
  }}, 1800);
</script></body></html>"""


@app.route("/api/disconnect/<kaynak>")
def disconnect(kaynak):
    if kaynak == "spotify":
        spotify_provider.clear_token()
        spotify_cookie.clear()
    if kaynak == "youtube":
        import os
        for dosya in (youtube_provider.YT_AUTH_FILE, youtube_provider.YT_BROWSER_FILE):
            if os.path.exists(dosya):
                os.remove(dosya)
    bridge.clear()
    return jsonify({"ok": True})


@app.route("/api/liked")
def liked():
    kaynak = request.args.get("source", "all")
    parcalar = []
    uyarilar = []
    if kaynak in ("all", "spotify"):
        if spotify_provider.is_connected():
            try:
                parcalar += spotify_provider.get_liked_tracks(300)
            except Exception as e:
                uyarilar.append(f"Spotify: {e}")
        elif spotify_cookie.is_connected():
            try:
                parcalar += spotify_cookie.get_liked_tracks(300)
            except Exception as e:
                uyarilar.append(f"Spotify: {e}")
    if kaynak in ("all", "youtube"):
        try:
            parcalar += youtube_provider.get_liked_songs(400)
        except Exception as e:
            uyarilar.append(f"YouTube: {e}")

    moodlar = {}
    for p in parcalar:
        m = p.get("mood") or clusters.KARISIK
        s = moodlar.setdefault(m, {"count": 0})
        s["count"] += 1
    sirali = {}
    for m in list(clusters.MOOD_DEFS.keys()) + [clusters.KARISIK]:
        if m in moodlar:
            sirali[m] = moodlar[m]
    return jsonify({
        "tracks": parcalar,
        "moods": sirali,
        "warnings": uyarilar,
        "spotify_connected": spotify_provider.is_connected() or spotify_cookie.is_connected(),
        "youtube_connected": youtube_provider.is_connected(),
    })


@app.route("/api/radio")
def radio():
    mood = request.args.get("mood", "")
    seed = request.args.get("seed", "")  # tek videoId (youtube) ya da "spotify|id1,id2,.."
    parcalar = []
    uyarilar = []

    if seed:
        if seed.startswith("spotify|"):
            ids = [i for i in seed.split("|", 1)[1].split(",") if i][:5]
            if spotify_provider.is_connected():
                try:
                    parcalar += spotify_provider.get_recommendations(ids)
                except Exception as e:
                    uyarilar.append(f"Spotify: {e}")
            elif spotify_cookie.is_connected():
                try:
                    parcalar += spotify_cookie.get_recommendations(ids)
                except Exception as e:
                    uyarilar.append(f"Spotify: {e}")
        else:
            try:
                parcalar += youtube_provider.get_radio(seed)
            except Exception as e:
                uyarilar.append(f"YouTube: {e}")
    else:
        liked = []
        if spotify_provider.is_connected():
            try:
                liked += spotify_provider.get_liked_tracks(300)
            except Exception as e:
                uyarilar.append(f"Spotify: {e}")
        elif spotify_cookie.is_connected():
            try:
                liked += spotify_cookie.get_liked_tracks(300)
            except Exception as e:
                uyarilar.append(f"Spotify: {e}")
        if youtube_provider.is_connected():
            try:
                liked += youtube_provider.get_liked_songs(400)
            except Exception as e:
                uyarilar.append(f"YouTube: {e}")
        harken = [p for p in liked if p.get("mood") == mood]
        sp_ids = [p["id"] for p in harken if p.get("provider") == "spotify"][:5]
        yt_ids = [p["id"] for p in harken if p.get("provider") == "youtube"][:3]
        if sp_ids:
            try:
                if spotify_provider.is_connected():
                    parcalar += spotify_provider.get_recommendations(sp_ids, 10)
                elif spotify_cookie.is_connected():
                    parcalar += spotify_cookie.get_recommendations(sp_ids, 10)
            except Exception as e:
                uyarilar.append(f"Spotify radio: {e}")
        try:
            for vid in yt_ids:
                parcalar += youtube_provider.get_radio(vid, 8)
        except Exception as e:
            uyarilar.append(f"YouTube radio: {e}")

    benzersiz = []
    gorulen = set()
    for p in parcalar:
        anahtar = (p.get("provider"), p.get("id"))
        if anahtar[1] and anahtar not in gorulen:
            gorulen.add(anahtar)
            benzersiz.append(p)
    return jsonify({"mood": mood, "tracks": benzersiz, "warnings": uyarilar})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=_Port, debug=True)