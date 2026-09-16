# EXP-107 — VPS kurulumu, adım adım

**Bu komutları sen çalıştıracaksın.** Benim ortamımdan dışa SSH engelleniyor, o yüzden ben
bağlanamıyorum.

İki servis kurulacak:

| Servis | Ne yapar | Port |
|---|---|---|
| `borsabot-exp107-shadow` | Sinyal üretir, shadow trade kaydeder | **yok** (sadece dışa bağlanır) |
| `borsabot-exp107-dashboard` | Veritabanını **salt-okunur** gösterir | **8440** |

Mevcut hiçbir şeye dokunmaz: exp020/028/048a/048b, nginx, firewall, diğer siteler — hepsi
olduğu gibi kalır. Kendi klasörü, kendi veritabanı, kendi unit'leri.

---

## Adım 1 — Dosyaları kopyala

**Kendi bilgisayarında**, repo kökünde:

```
scp -r results\exp107_shadow root@31.57.77.4:/opt/projects/borsabot-exp107
```

`artifact/` klasörü mutlaka gitmeli (2.2 MB) — motor dondurulmuş modelleri ve eşiği oradan
yükler, hiçbir şeyi yeniden hesaplamaz.

## Adım 2 — Sunucuya bağlan

```
ssh root@31.57.77.4
cd /opt/projects/borsabot-exp107
```

## Adım 3 — Python ortamı

```
apt-get update && apt-get install -y python3-venv
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install numpy scikit-learn lightgbm joblib
```

Dashboard hiçbir ek paket istemiyor — saf stdlib.

## Adım 4 — Çalıştığını test et

```
.venv/bin/python scripts/R3_shadow_run.py status
```

`decision points 0` yazması normal (veritabanı yeni). Hata vermiyorsa devam.

## Adım 5 — Runner servisini kur

```
cp deploy/borsabot-exp107-shadow.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now borsabot-exp107-shadow
systemctl status borsabot-exp107-shadow --no-pager
```

`active (running)` görmelisin. Logu izle:

```
journalctl -u borsabot-exp107-shadow -f
```

`ready. bars=3479 ...` satırını görünce Ctrl+C ile logdan çık (servis çalışmaya devam eder).

## Adım 6 — Dashboard servisini kur

**Önce token'ı ayarla.** Senin için ürettiğim rastgele token:

```
TbKbhhVRPbttl54s44V_ujGPNekKqZoF
```

```
cp deploy/borsabot-exp107-dashboard.service /etc/systemd/system/
sed -i 's/CHANGE_ME_TO_A_LONG_RANDOM_STRING/TbKbhhVRPbttl54s44V_ujGPNekKqZoF/' /etc/systemd/system/borsabot-exp107-dashboard.service
systemctl daemon-reload
systemctl enable --now borsabot-exp107-dashboard
systemctl status borsabot-exp107-dashboard --no-pager
```

## Adım 7 — Tarayıcıdan aç

```
http://31.57.77.4:8440/?t=TbKbhhVRPbttl54s44V_ujGPNekKqZoF
```

Telefonuna yer imi yap. Sayfa 60 saniyede bir kendini yeniler.

**Token olmadan sayfa 403 döner.** Kutuda firewall yok, sayfanın gizliliğini sadece bu token
sağlıyor. Token'ı paylaşma; sızarsa unit dosyasındaki değeri değiştirip
`systemctl daemon-reload && systemctl restart borsabot-exp107-dashboard` yap.

---

## Günlük kullanım

| İş | Komut |
|---|---|
| Durum (web) | `http://31.57.77.4:8440/?t=...` |
| Durum (ssh) | `.venv/bin/python scripts/R3_shadow_run.py status` |
| Canlı log | `journalctl -u borsabot-exp107-shadow -f` |
| Rapor üret | `.venv/bin/pip install pandas` sonra `.venv/bin/python scripts/R4_report.py` |
| Durdur | `systemctl stop borsabot-exp107-shadow` |
| Tekrar başlat | `systemctl start borsabot-exp107-shadow` |

## Tamamen kaldırmak

```
systemctl disable --now borsabot-exp107-shadow borsabot-exp107-dashboard
rm /etc/systemd/system/borsabot-exp107-{shadow,dashboard}.service
systemctl daemon-reload
rm -rf /opt/projects/borsabot-exp107
```

Kutuda başka hiçbir şey etkilenmez.

---

## Ne beklemelisin

D, 10 sembolde günde **~2.4 sinyal** üretiyor ve her pozisyonu **~23.4 saat** tutuyor.

| Geçen süre | Sinyal (yaklaşık) | Kapanan |
|---|---|---|
| 24 saat | ~2 | ~0 |
| 48 saat | ~5 | ~2 |
| 5 gün | ~12 | ~9 |
| 7 gün | ~17 | ~14 |

**Saatlerce `signals fired 0` görmek normaldir** — %99 persentil kapısının beklenen
davranışı, arıza değil. Canlılık göstergesi dashboard'daki **decision points** sayacı: saat
başı ~60 artar (`:00`'da 40, `:10`'da 10, `:30`'da 10).

İlk kapanan işlem, ilk sinyalden yaklaşık **bir gün sonra** görünür.

---

## Güvenlik notları

- Runner **emir gönderemez**: hiçbir exchange client, imzalama kodu veya kimlik bilgisi
  import etmiyor. Sadece iki Binance **public** endpoint'ini çağırıyor
  (`/fapi/v1/klines`, `/fapi/v1/ticker/bookTicker`) — API key gerektirmiyorlar.
- Her iki unit `BINANCE_API_KEY` ve `BINANCE_API_SECRET`'ı **boşa çekiyor**, böylece başıboş
  bir environment bu süreçlere trading yetkili anahtar veremez.
- Dashboard veritabanını `mode=ro` ile açıyor ve unit'i `ReadOnlyPaths` altında çalışıyor —
  yazma yetkisi hiç yok.
- Her iki servis `NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome` altında.
- **SSH root parolanı değiştir** (`passwd`) — eski parola bir sohbet transcript'ine düz metin
  olarak geçti.
