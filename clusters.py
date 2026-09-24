import math

MOOD_DEFS = {
    "spor":    {"label": "Spor",    "icon": "🏃", "center": {"tempo": 130, "energy": 0.78, "dance": 0.60, "valence": 0.55, "acoustic": 0.10}},
    "eglen":   {"label": "E\u011flen", "icon": "\U0001f57a", "center": {"tempo": 115, "energy": 0.72, "dance": 0.80, "valence": 0.68, "acoustic": 0.08}},
    "calisma": {"label": "\u00c7al\u0131\u015fma", "icon": "\U0001f4bb", "center": {"tempo": 95,  "energy": 0.45, "dance": 0.38, "valence": 0.42, "acoustic": 0.16}},
    "chill":   {"label": "Chill",    "icon": "\u2615", "center": {"tempo": 100, "energy": 0.42, "dance": 0.52, "valence": 0.50, "acoustic": 0.30}},
    "uyku":    {"label": "Uyku",     "icon": "\U0001f634", "center": {"tempo": 70,  "energy": 0.18, "dance": 0.25, "valence": 0.32, "acoustic": 0.42}},
}
KARISIK = "karisik"

_BOYUT = ["tempo", "energy", "dance", "valence", "acoustic"]
_OLCEK = {"tempo": (40.0, 200.0), "energy": (0.0, 1.0), "dance": (0.0, 1.0),
          "valence": (0.0, 1.0), "acoustic": (0.0, 1.0)}


def _norm(key, deger):
    lo, hi = _OLCEK[key]
    return max(0.0, min(1.0, (deger - lo) / (hi - lo)))


def assign_by_features(features):
    """Spotify audio-features'tan en uygun moodu donder. Uygun kosul yoksa KARISIK."""
    if not features:
        return KARISIK
    deger = {
        "tempo": _norm("tempo", features.get("tempo") or 0),
        "energy": features.get("energy") or 0,
        "dance": features.get("danceability") or 0,
        "valence": features.get("valence") or 0,
        "acoustic": features.get("acousticness") or 0,
    }
    eniyi, enkisa = KARISIK, 1e9
    for mood, meta in MOOD_DEFS.items():
        c = {k: _norm(k, v) for k, v in meta["center"].items()}
        uzaklik = math.sqrt(sum((deger[b] - c[b]) ** 2 for b in _BOYUT))
        if uzaklik < enkisa:
            eniyi, enkisa = mood, uzaklik
    return eniyi if enkisa < 0.42 else KARISIK


# YouTube'da audio-feature yok; baslik/sanatci anahtar kelimeleriyle tahmin.
_ANAHTARLAR = {
    "spor":    ["workout", "gym", "run", "cardio", "exercise", "energetic", "power", "hype", "edm",
                "remix", "spor", "sport", "exercise", "fit"],
    "eglen":   ["dance", "party", "summer", "hit", "reggaeton", "pop", "upbeat", "club",
                "e\u011flen", "parti", "yaz", "dans"],
    "calisma": ["focus", "study", "concentration", "brain", "work", "lo-fi", "lofi", "office",
                "odak", "\u00e7al\u0131\u015fma", "ders", "binaural"],
    "chill":   ["chill", "relax", "coffee", "lounge", "ambient", "acoustic", "smooth",
                "dinlenme", "kahve", "guitar"],
    "uyku":    ["sleep", "calm", "meditation", "rain", "ballad", "piano", "night", "slow",
                "yoga", "white noise", "uyku", "sakin", "gece", "lullaby", "insomniac"],
}

_SIRALAMA = ["spor", "eglen", "calisma", "chill", "uyku"]


def assign_by_keywords(metin):
    """Baslik + sanatci birlesimi metnini tara, ortak kelimelere gore mood don."""
    if not metin:
        return KARISIK
    t = metin.lower()
    puan = {m: 0 for m in MOOD_DEFS}
    for mood, kelimeler in _ANAHTARLAR.items():
        for k in kelimeler:
            if k in t:
                puan[mood] += 1
    enyuksek = max(puan.values())
    if enyuksek == 0:
        return KARISIK
    kazananlar = [m for m, p in puan.items() if p == enyuksek]
    kazananlar.sort(key=lambda m: _SIRALAMA.index(m))
    return kazananlar[0]


def mood_meta():
    return {m: {"label": d["label"], "icon": d["icon"]} for m, d in MOOD_DEFS.items()}