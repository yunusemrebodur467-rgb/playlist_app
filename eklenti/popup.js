const durEl = document.getElementById("dur");
const bilgiEl = document.getElementById("bilgi");
const spBtn = document.getElementById("spBtn");
const ytBtn = document.getElementById("ytBtn");
const yolBtn = document.getElementById("yolBtn");

function satir(ok, metin) {
  return '<div class="kademe ' + (typeof ok === "undefined" ? "isleme" : ok ? "" : "hata") + '">' +
    (ok ? "✓ " : ok === false ? "✕ " : "… ") + metin + "</div>";
}

function mesaj(parcalar) {
  durEl.innerHTML = parcalar.join("");
}

async function kilitli(btn, fn) {
  btn.disabled = true;
  birlikteYenile();
  const r = await fn();
  btn.disabled = false;
  birlikteYenile();
  return r;
}

async function birlikteYenile() {
  const r = await new Promise((res) => chrome.runtime.sendMessage({ action: "durum" }, res));
  var surumSatir = document.getElementById("surumSatir");
  if (surumSatir && r && r.surum) surumSatir.textContent = "Kod: " + r.surum;
  const satirlar = [];
  satirlar.push(satir(r.yerel_aktif, "Yerel PopGün uygulaması " + (r.yerel_aktif ? "çalışıyor" : "bulunamadı")));
  if (r.yerel_aktif) {
    satirlar.push(satir(r.bridge.spotify, "Spotify köprü token'ı iletildi"));
    satirlar.push(satir(r.bridge.youtube, "YouTube köprü çerezi iletildi"));
  }
  satirlar.push(satir(r.sp_dc, "Tarayıcıda Spotify oturumu (" + (r.sp_dc ? "var" : "yok") + ")"));
  satirlar.push(satir(r.yt_papisid, "Tarayıcıda YouTube oturumu (" + (r.yt_papisid ? "var" : "yok") + ")"));
  mesaj(satirlar);
  if (!r.sp_dc || !r.yt_papisid) {
    bilgiEl.innerHTML =
      '<div class="not">Oturum yoksa önce giriş yap:<br>' +
      '<button class="ac" id="acSp"' + (r.sp_dc ? ' style="display:none"' : '') + '>🟢 Spotify\'da oturum aç</button>' +
      '<button class="ac" id="acYt"' + (r.yt_papisid ? ' style="display:none"' : '') + '>▶️ YouTube Music\'te oturum aç</button>' +
      '<button class="ac yeniden" id="acYenile">↻ Yeniden kontrol</button>' +
      '<div class="not" style="border:none;padding:0;color:#8b8fa8">Giriş + sayfa yenileme sonrası "Yeniden kontrol" deyin, sonra bağlanın.</div></div>';
    var acSp = document.getElementById("acSp");
    var acYt = document.getElementById("acYt");
    var acYenile = document.getElementById("acYenile");
    if (acSp) acSp.addEventListener("click", function(){ chrome.tabs.create({ url: "https://open.spotify.com" }); });
    if (acYt) acYt.addEventListener("click", function(){ chrome.tabs.create({ url: "https://music.youtube.com" }); });
    if (acYenile) acYenile.addEventListener("click", function(){ birlikteYenile(); });
  }
}

spBtn.addEventListener("click", async () => {
  const r = await kilitli(spBtn, () => new Promise((res) => chrome.runtime.sendMessage({ action: "spotify" }, res)));
  durEl.innerHTML = r.ok ? satir(true, r.mesaj || "Spotify bağlandı") : satir(false, r.error || "Bağlanamadı");
});

ytBtn.addEventListener("click", async () => {
  const r = await kilitli(ytBtn, () => new Promise((res) => chrome.runtime.sendMessage({ action: "youtube" }, res)));
  durEl.innerHTML = r.ok ? satir(true, r.mesaj || "YouTube Music bağlandı") : satir(false, r.error || "Bağlanamadı");
});

yolBtn.addEventListener("click", async () => {
  durEl.innerHTML = satir(undefined, "İstekler dinleniyor (25 sn)...");
  const r = await kilitli(yolBtn, () => new Promise((res) => chrome.runtime.sendMessage({ action: "yolkeşif" }, res)));
  durEl.innerHTML = r && r.ok ? satir(true, "Keşif tamam — " + (r.adet || 0) + " benzersiz uç loglandı.") : satir(false, (r && r.error) || "Keşif başarısız");
});

chrome.runtime.sendMessage({ action: "otomatik" }, (r) => {
  if (r && r.ok && (r.spotify || r.youtube)) birlikteYenile();
});
birlikteYenile();