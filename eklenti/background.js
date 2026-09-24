const LOCAL = "http://127.0.0.1:5001/api/bridge";
const SURUM = "v14-oyun-2026-09-24";

function logla(d) {
  try {
    fetch(LOCAL + "/log", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ t: Date.now(), ...d, surum: SURUM }),
      keepalive: true,
    }).catch(() => {});
  } catch (e) { /* yok */ }
}

async function spdcOku() {
  const cs = await chrome.cookies.getAll({ url: "https://open.spotify.com" }).catch(() => []);
  const parcalar = [];
  let spdcVar = false;
  for (const c of cs) {
    if (!c.value) continue;
    if (c.name === "sp_dc") spdcVar = true;
    if (["sp_dc", "sp_key", "sp_landing", "sp_t", "sp_session", "sp_protocol", "sp_t_fs", "sp_gaid", "sp_ab", "sp_ads", "sp_test"].includes(c.name)) {
      parcalar.push(c.name + "=" + c.value);
    }
  }
  if (spdcVar) return parcalar.join("; ");
  const urls = [
    "https://open.spotify.com",
    "https://accounts.spotify.com",
    "https://www.spotify.com",
    "https://play.spotify.com",
  ];
  for (const u of urls) {
    try {
      const c = await chrome.cookies.get({ url: u, name: "sp_dc" });
      if (c && c.value) return c.value;
    } catch (e) { /* yok */ }
  }
  return "";
}

// open.spotify.com sekmesi canlı isteklerinde gönderdiği `Authorization: Bearer`
// başlığını yakalar. Bu, Spotify'ın kapatamadığı tek güvenilir kanaldır.
let sonSpToken = "";
let sonYakalananMs = 0;
let debugSayaci = 0;

chrome.webRequest.onBeforeSendHeaders.addListener(
  function (d) {
    try {
      const u = String(d.url || "");
      if (/127\.0\.0\.1|localhost/.test(u)) return;
      const basliklar = d.requestHeaders || [];
      if (!basliklar.length) return;
      let auth = "";
      for (const h of basliklar) {
        if (/^Bearer\s+/i.test((h.value || ""))) { auth = h.value; break; }
      }
      if (auth) {
        const tok = auth.slice(7).trim();
        if (tok && tok !== sonSpToken) {
          sonSpToken = tok;
          sonYakalananMs = Date.now();
          spTokenYakalandi(tok);
        }
        logla({ adim: "spotify:rota", url: u.slice(0, 140), auth: true });
      } else {
        logla({ adim: "spotify:rota", url: u.slice(0, 140), auth: false });
      }
    } catch (e) { /* yok */ }
  },
  { urls: ["<all_urls>"] },
  ["requestHeaders", "extraHeaders"]
);

let sonPostMs = 0;

function spTokenYakalandi(tok) {
  // API'yi besleyen POST sarmalını frenle: en fazla 120 sn'de bir token ilet.
  // (Aksi halde web player her token değişiminde POST → sunucu /v1/me → 429 hiç düşmez.)
  const simdi = Date.now();
  if (simdi - sonPostMs < 120000) {
    logla({ adim: "spotify:fren", ok: true, uzunluk: tok.length, kalan: sonPostMs + 120000 - simdi });
    return;
  }
  sonPostMs = simdi;
  logla({ adim: "spotify:yakin", ok: true, uzunluk: tok.length });
  chrome.storage.local.set({ sp_token: tok, sp_token_ms: Date.now() }).catch(() => {});
  fetch(LOCAL + "/spotify/token", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ access_token: tok, refresh_token: "", expires_in: 1800 }),
    keepalive: true,
  })
    .then((p) => p.json())
    .then((d) => logla({ adim: "spotify:yakin-bridge", ok: !!d.ok, hata: d.hata || "" }))
    .catch((e) => logla({ adim: "spotify:yakin-bridge", ok: false, hata: e.message }));
}

async function spTokenYakalananVar() {
  if (sonSpToken) return sonSpToken;
  const k = await new Promise((res) => chrome.storage.local.get({ sp_token: "", sp_token_ms: 0 }, res));
  if (k.sp_token && k.sp_token_ms && Date.now() - k.sp_token_ms < 1800000) return k.sp_token;
  return "";
}

// Web player güncel olarak `/api/token` yanıt GÖVDESİNDE accessToken döndürüyor
// (get_access_token ucu CDN'de kapatıldı). Yanıt gövdesini chrome.debugger ile okuyoruz.
function spTokenGovdeyeYapistir(m) {
  chrome.runtime.onMessage.addListener(function spGovdeDinle(msg, sender) {
    if (msg && msg.action === "spDebug" && msg.token) {
      sonSpToken = msg.token;
      sonYakalananMs = Date.now();
      chrome.storage.local.set({ sp_token: msg.token, sp_token_ms: Date.now() }).catch(() => {});
      chrome.runtime.onMessage.removeListener(spGovdeDinle);
      spTokenYakalandi(msg.token);
    }
    return false;
  });
}

// Beğenilerin gerçekte hangi uçtan geldiğini gör: debugger ile sayfanın tüm
// ağ isteklerini kısa bir pencere boyunca logla (SW/cache/websocket dahil).
async function spYolTarifi(sureMs) {
  const beklenen = sureMs || 40000;
  logla({ adim: "spotify:kesifbas", surum: SURUM, baslama: Date.now() });
  let tabs = [];
  try { tabs = await chrome.tabs.query({ url: "https://open.spotify.com/*" }); } catch (e) {}
  if (!tabs.length) { try { tabs = await chrome.tabs.query({ url: "https://open.spotify.com" }); } catch (e) {} }
  if (!tabs.length) return { ok: false, error: "open.spotify.com sekmesi yok." };
  const tabId = tabs[0].id;
  let takili = false;
  try { await chrome.debugger.attach({ tabId }, "1.3"); takili = true; } catch (e) {
    return { ok: false, error: "debugger takılamadı: " + e.message };
  }
  await chrome.debugger.sendCommand({ tabId }, "Network.enable", {}).catch(() => {});
  // Keşif başlarken sekmeyi otomatik yenile: veri istekleri bu pencereye düşer.
  chrome.tabs.reload(tabId).catch(() => {});
  const gorulen = {};
  const ridUrl = {};
  function dinle(src, method, params) {
    if (src.tabId !== tabId) return;
    if (method === "Network.requestWillBeSent") {
      const u = (params && params.request && params.request.url) || "";
      ridUrl[params.requestId] = u;
      if (!/pathfinder|me\/tracks|collection|liked|graphql/i.test(u)) return;
      let op = "";
      try {
        const g = (params.request.postDataEntries && params.request.postDataEntries[0] && params.request.postDataEntries[0].bytes) || (params.request.postData) || "";
        if (g && (u.includes("pathfinder") || u.includes("query"))) {
          const es = decodeURIComponent(g.replace(/\+/g, " ")).match(/"operationName"\s*:\s*"([^"]+)"/);
          if (es) op = es[1];
        }
      } catch (e) {}
      logla({ adim: "spotify:yolposta", url: u.slice(0, 150), op: op || "-", boyut: ((params.request.postData || "")).length });
      return;
    }
    if (method === "Network.loadingFinished") {
      const u = ridUrl[params.requestId] || "";
      if (!/pathfinder|me\/tracks|collection|liked|graphql/i.test(u) && !u.includes("api-partner")) return;
      chrome.debugger.sendCommand({ tabId }, "Network.getResponseBody", { requestId: params.requestId })
        .then((gd) => {
          const metin = (gd && gd.body) || "";
          if (!metin) return;
          logla({ adim: "spotify:govde", url: u.slice(0, 140), boyut: metin.length, bas: metin.slice(0, 500).replace(/\s+/g, " ") });
          // Web player artık beğenileri api.spotify.com/v1/me/tracks'ten değil
          // api-partner.graphql "data.tracks" listesinden getiriyor. Yakalanan
          // listeyi 429'a takılmadan sunucuya ilet:
          if (metin.includes('"data"') && metin.includes('"tracks"')) {
            try {
              const js = JSON.parse(metin);
              const trk = js && js.data && js.data.tracks;
              if (Array.isArray(trk) && trk.length) {
                fetch(LOCAL + "/spotify/liked", {
                  method: "POST",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({ tracks: trk }),
                  keepalive: true,
                }).then((r) => r.json().catch(() => ({ ok: false })))
                  .then((sr) => logla({ adim: "spotify:yakalanan", ok: !!(sr && sr.ok), adet: trk.length }))
                  .catch(() => {});
              }
            } catch (e) { /* JSON değil */ }
          }
        })
        .catch(() => {});
      return;
    }
    if (method !== "Network.responseReceived") return;
    const u = (params && params.response && params.response.url) || "";
    if (!u) return;
    if (/\.(js|css|png|jpg|gif|svg|woff2?|ico|mp3|m4a|webm|mp4)(\?|#|$)/i.test(u)) return;
    if (u.includes("sentry") || u.includes("analytics") || u.includes("events.spotify") || u.includes("simple-iframe")) return;
    const ana = u.split("?")[0].replace(/\/+$/, "");
    if (gorulen[ana]) return;
    gorulen[ana] = true;
    logla({ adim: "spotify:yol", url: u.slice(0, 160), tip: (params.response.mimeType || "") });
  }
  chrome.debugger.onEvent.addListener(dinle);
  await new Promise((res) => setTimeout(res, beklenen));
  chrome.debugger.onEvent.removeListener(dinle);
  if (takili) { try { await chrome.debugger.detach({ tabId }); } catch (e) {} }
  return { ok: true, adet: Object.keys(gorulen).length };
}

async function spTokenDebugIle() {
  let tabs = [];
  try { tabs = await chrome.tabs.query({ url: "https://open.spotify.com/*" }); } catch (e) { /* yok */ }
  if (!tabs.length) {
    try { tabs = await chrome.tabs.query({ url: "https://open.spotify.com" }); } catch (e) { /* yok */ }
  }
  if (!tabs.length) {
    try { tabs = [await chrome.tabs.create({ url: "https://open.spotify.com/", active: true })]; } catch (e) { /* yok */ }
  }
  if (!tabs.length) return { ok: false, error: "open.spotify.com sekmesi açılamadı." };
  const tabId = tabs[0].id;

  let debuggerOn = false;
  try {
    await chrome.debugger.attach({ tabId: tabId }, "1.3");
    debuggerOn = true;
  } catch (e) {
    return { ok: false, error: "Hata ayıklayıcı eklenemedi (başka alet bağlı olabilir): " + e.message };
  }

  await new Promise((res) => {
    chrome.debugger.sendCommand({ tabId: tabId }, "Network.enable", {}).then(res).catch(() => res());
  });

  const sonuc = await new Promise((resolve) => {
    let bekliyor = true;
    const zamanlayici = setTimeout(() => {
      if (bekliyor) { bekliyor = false; resolve({ ok: false, error: "zaman aşımı — oturumlu /api/token yanıtı görülmedi (yalnızca anonim döndü? Spotify'da oturum açıksa sekme yenilensin)." }); }
    }, 15000);

    function dinle(src, method, params) {
      if (src.tabId !== tabId) return;
      if (method !== "Network.responseReceived") return;
      const url = (params && params.response && params.response.url) || "";
      if (!url.includes("/api/token")) return;
      const rid = params.requestId;
      chrome.debugger.sendCommand({ tabId: tabId }, "Network.getResponseBody", { requestId: rid })
        .then((gd) => {
          if (!bekliyor) return;
          const metin = (gd && gd.body) || "";
          try {
            const j = JSON.parse(metin);
            const m = metin.match(/"accessToken"\s*:\s*"([^"]+)"/);
            if (!m) return;
            const anonim = j.isAnonymous === true || j.isAnonymous === "true";
            const kullanici = j.username || j.userId || "";
            logla({
              adim: "spotify:yanit",
              ok: true,
              anonim: anonim,
              kullanici: kullanici || "-",
              alanlar: Object.keys(j).join(","),
              pr: typeof j.accessTokenExpirationTimestampMs,
            });
            if (anonim) {
              logla({ adim: "spotify:anonim", ok: true, kullanici: kullanici || "-" });
              return; // anonim tokenı atla; oturumlu yanıtı bekle
            }
            bekliyor = false;
            clearTimeout(zamanlayici);
            resolve({ ok: true, accessToken: m[1], kullanici: kullanici });
          } catch (e) {
            /* JSON değilse atla */
          }
        })
        .catch(() => { /* gövde okunamadı, sonraki yanıtı bekle */ });
    }
    chrome.debugger.onEvent.addListener(dinle);

    // Dinleyiciyi kalıcı dinlemek için: sekme zaten gerçek /api/token yapıyor,
    // yeniden yükleyip yakalayalım.
    chrome.tabs.reload(tabId).catch(() => {});
    setTimeout(() => chrome.debugger.onEvent.removeListener(dinle), 14000);
  });

  if (debuggerOn) {
    try { await chrome.debugger.detach({ tabId: tabId }); } catch (e) { /* yok */ }
  }
  return sonuc;
}

// open.spotify.com sekmesini aç / yenile ki web player bir istek göndersin.
async function spSekmeTetikle() {
  let tabs = [];
  try { tabs = await chrome.tabs.query({ url: "https://open.spotify.com/*" }); } catch (e) { /* yok */ }
  if (!tabs.length) {
    try { tabs = await chrome.tabs.query({ url: "https://open.spotify.com" }); } catch (e) { /* yok */ }
  }
  if (tabs.length) {
    try { await chrome.tabs.reload(tabs[0].id); } catch (e) { /* yok */ }
  } else {
    try { await chrome.tabs.create({ url: "https://open.spotify.com/", active: false }); } catch (e) { /* yok */ }
  }
}

async function spotifyBagla() {
  const spdc = await spdcOku();
  if (!spdc) {
    logla({ adim: "spotify:cookie", ok: false, error: "sp_dc bulunamadı" });
    return { ok: false, error: "sp_dc çerezi bulunamadı. Bu Chrome profiline açık bir sekmede https://open.spotify.com'da oturum aç, sayfayı yenile, sonra tekrar dene." };
  }
  logla({ adim: "spotify:cookie", ok: true });

  // Her "bağla"da TAZE token yakala (depodaki eski/anonim token'ı kullanma).
  spTokenGovdeyeYapistir();
  const g = await spTokenDebugIle();
  let tok;
  if (g.ok && g.accessToken) {
    tok = g.accessToken;
    sonSpToken = tok;
    sonYakalananMs = Date.now();
    chrome.storage.local.set({ sp_token: tok, sp_token_ms: Date.now(), sp_token_kullanici: g.kullanici || "" }).catch(() => {});
    logla({ adim: "spotify:yakin", ok: true, uzunluk: tok.length, kullanici: g.kullanici || "-" });
  } else {
    logla({ adim: "spotify:yakin", ok: false, error: g.error || "debugger boş" });
    return { ok: false, error: g.error || "Token yakalanamadı." };
  }

  let p;
  try {
    p = await fetch(LOCAL + "/spotify/token", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ access_token: tok, refresh_token: "", expires_in: 1800 }),
    });
  } catch (e) {
    logla({ adim: "spotify:bridge", ok: false, error: e.message });
    return { ok: false, error: "Yerel PopGün uygulamasına ulaşılamadı — uygulama açık mı? (http://127.0.0.1:5001) — " + e.message };
  }
  const d = await p.json();
  logla({ adim: "spotify:bridge", ok: !!d.ok, hata: d.hata || "" });
  if (d.ok) {
    chrome.storage.local.set({ sp_token: tok, sp_token_ms: Date.now() }).catch(() => {});
    return { ok: true, mesaj: "Spotify yerel uygulamaya iletildi." };
  }
  return { ok: false, error: d.hata || "Yerel uygulama reddetti." };
}

async function youtubeBagla() {
  const adlar = ["__Secure-3PAPISID", "SAPISID"];
  const parcaci = [];
  for (const ad of adlar) {
    try {
      const c = await chrome.cookies.get({ url: "https://music.youtube.com", name: ad });
      if (c && c.value) parcaci.push(ad + "=" + c.value);
    } catch (e) { /* yok say */ }
  }
  if (parcaci.length === 0) {
    return { ok: false, error: "YouTube çerezleri bulunamadı. music.youtube.com'da oturum açıp sayfayı yenile, sonra tekrar dene." };
  }

  try {
    const p = await fetch(LOCAL + "/youtube/cookie", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cookie: parcaci.join("; ") }),
    });
    const d = await p.json();
    return d.ok ? { ok: true, mesaj: "YouTube Music yerel uygulamaya iletildi." } : { ok: false, error: d.error || "Yerel uygulama reddetti." };
  } catch (e) {
    return { ok: false, error: "Yerel PopGün uygulamasına ulaşılamadı. uygulama açık mı? (http://127.0.0.1:5001) — " + e.message };
  }
}

async function durum() {
  const yerel = {
    sp_dc: false,
    yt_papisid: false,
    yerel_aktif: false,
    bridge: { active: false, spotify: false, youtube: false },
    surum: SURUM,
  };
  try {
    yerel.sp_dc = !!(await spdcOku());
  } catch (e) { /* yok */ }
  try {
    const yt = await chrome.cookies.get({ url: "https://music.youtube.com", name: "__Secure-3PAPISID" });
    yerel.yt_papisid = !!(yt && yt.value);
  } catch (e) { /* yok */ }
  try {
    const r = await fetch(LOCAL + "/status", { cache: "no-store" });
    const d = await r.json();
    yerel.yerel_aktif = true;
    yerel.bridge = d;
  } catch (e) {
    yerel.yerel_aktif = false;
  }
  return yerel;
}

// ---------- Otomatik bağlantı ----------
// Çerez kopyalama gerekmez: yerel uygulama açıkken eklenti, tarayıcıdaki
// Spotify/YouTube oturum çerezlerini otomatik okur ve köprü aracılığıyla iletir.
let otomatikAktif = false;

async function otomatikDenetle() {
  if (otomatikAktif) return { ok: false, error: "zaten çalışıyor" };
  otomatikAktif = true;
  try {
    let s;
    try {
      const r = await fetch(LOCAL + "/status", { cache: "no-store" });
      s = await r.json();
    } catch (e) {
      return { ok: false, error: "uygulama kapalı" };
    }
    const sonuc = { spotify: !!s.spotify, youtube: !!s.youtube };
    const k = await new Promise((res) => chrome.storage.local.get({ sonDeneme: 0 }, res));
    const simdi = Date.now();
    const bekleme = (k.sonDeneme || 0) + 60000;

    if (!sonuc.spotify) {
      const spdc = await spdcOku();
      if (spdc && simdi >= bekleme) {
        chrome.storage.local.set({ sonDeneme: simdi });
        const r = await spotifyBagla();
        if (r.ok) {
          sonuc.spotify = true;
          chrome.storage.local.set({ sonDeneme: 0 });
        }
      }
    }

    if (!sonuc.youtube) {
      const yc = await chrome.cookies.get({ url: "https://music.youtube.com", name: "__Secure-3PAPISID" }).catch(() => null);
      if (yc && yc.value && simdi >= bekleme) {
        chrome.storage.local.set({ sonDeneme: simdi });
        const r = await youtubeBagla();
        if (r.ok) {
          sonuc.youtube = true;
          chrome.storage.local.set({ sonDeneme: 0 });
        }
      }
    }

    return { ok: true, ...sonuc };
  } finally {
    otomatikAktif = false;
  }
}

try {
  chrome.runtime.onInstalled.addListener(() => {
    chrome.alarms.create("otomatikBaglan", { periodInMinutes: 1 });
  });
  chrome.alarms.onAlarm.addListener((a) => {
    if (a.name === "otomatikBaglan") otomatikDenetle();
  });
  chrome.runtime.onStartup.addListener(() => otomatikDenetle());
} catch (e) {
  // alarms API yoksa (izinsiz) sessizce atla; mesajlar yine çalışsın.
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg && msg.action === "spotify") {
    spotifyBagla().then(sendResponse);
    return true;
  }
  if (msg && msg.action === "youtube") {
    youtubeBagla().then(sendResponse);
    return true;
  }
  if (msg && msg.action === "yolkeşif") {
    spYolTarifi().then(sendResponse);
    return true;
  }
  if (msg && msg.action === "otomatik") {
    otomatikDenetle().then(sendResponse);
    return true;
  }
  if (msg && msg.action === "durum") {
    durum().then(sendResponse);
    return true;
  }
  return false;
});