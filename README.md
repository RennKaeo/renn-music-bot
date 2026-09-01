# 🎵 Renn Music Bot

> Bot Telegram pemutar lagu dari YouTube / Spotify / SoundCloud / TikTok / Apple Music, lengkap dengan lirik, favorit, antrian, dan deteksi lagu (shazam).

Bot asli (production) berjalan di bawah `@mypersonalbotmusic_bot`. Repo ini berisi kode sumber penuh agar bot bisa dijalankan ulang di server lain atau di local kapan pun.

---

## ✨ Fitur

**Pemutar & unduhan**
- `/play <judul / link>` — cari lagu, pilih kualitas (MP3 192/320 atau M4A AAC asli 🎧)
- Dukungan link: YouTube, Spotify, SoundCloud, TikTok, Apple Music, playlist
- `/artist <nama>` — lagu-lagu terbaik suatu artis (pagination)
- `/playlist <url>` — unduh hingga 50 lagu dari sebuah playlist
- `/preview` — potongan 30 detik sebelum download penuh

**Info & personal**
- `/lyrics` — lirik (dari LRCLIB / LyricsOVH) + tombol terjemahan 🇮🇩/🇬🇧
- `/history` & `/favorites` — riwayat dan lagu favorit (tombol ⭐)
- `/queue` — antrian lagu, play-next per lagu
- `/shazam` — deteksi judul lagu dari voice/audio yang dikirim ke bot
- `/stats` & `/settings` — statistik pemakaian & kualitas default

**Teknis**
- Cache file (video_id × kualitas) → lagu berulang instan tanpa download ulang
- Inline mode — ketik `@username_bot <judul>` di chat mana pun
- Rate-limit anti-spam, auto-clean file sementara (1 jam)
- Aman dari race-condition: tiap download pakai sub-file bernama unik

---

## 🧰 Prasyarat

- **Python ≥ 3.10**
- **ffmpeg** (untuk konversi MP3 & potong preview)

Debian/Ubuntu:
```bash
sudo apt update && sudo apt install -y ffmpeg python3-venv
```

---

## 🚀 Setup & Jalankan

```bash
git clone https://github.com/RennKaeo/renn-music-bot.git
cd renn-music-bot

# 1. Environment
python3 -m venv venv
venv/bin/pip install -r requirements.txt

# 2. Token bot (buat via @BotFather → /newbot, lalu salin API Token)
export BOT_TOKEN="123456:ABC-DEF..."   # ISI dengan token kamu

# 3. Jalankan
venv/bin/python bot.py
```

Saat log muncul `Application started`, bot sudah online — cek dengan mengirim `/ping` di Telegram.

---

## 🔧 Konfigurasi (Environment Variable)

| Variable | Wajib | Keterangan |
|---|---|---|
| `BOT_TOKEN` | ✅ | Token bot dari @BotFather |
| `AUDD_API_TOKEN` | — | Aktivasi `/shazam` (dari [audd.io](https://audd.io)) |
| `NVIDIA_API_KEY` | — | Fitur terjemahan berbasis AI |
| `OPENAI_API_KEY` | — | Opsional, alternatif terjemahan |

---

## 💾 Menjalankan sebagai Layanan (auto-restart)

Jadikan bot tetap hidup saat server restart:

```bash
sudo cp renn-music-bot.service /etc/systemd/system/
# lalu isi token di baris Environment= pada file tersebut
sudo systemctl daemon-reload
sudo systemctl enable --now renn-music-bot.service
journalctl -u renn-music-bot.service -f
```

---

## 🍪 cookies.txt (opsional)

Hanya dibutuhkan untuk video yang perlu login / batas umur.

1. Ekspor cookies YouTube kamu (ekstensi browser seperti *Get cookies.txt LOCALLY*)
2. Simpan sebagai `cookies.txt` di folder yang sama dengan `bot.py`
3. Jagalah kerahasiaannya:
   ```bash
   chmod 600 cookies.txt
   ```

> ⚠️ **Jangan pernah commit `cookies.txt`** — file ini sudah masuk `.gitignore`.

---

## 🔒 Keamanan

- Tidak ada token/secret di dalam kode — semuanya dibaca dari environment variable.
- `run.sh`, `cookies.txt`, database, dan file sementara diabaikan oleh `.gitignore`.
- Filter anti-spam / rate-limit aktif di tiap endpoint download.

---

## 🔀 Branch

| Branch | Keterangan |
|---|---|
| `dev` | Pengembangan aktif (default) |
| `main` | Release stabil |

---

## 📝 Catatan Audit (2026-09-01)

- 9 titik unduhan memakai suffix unik per-request → bebas tabrakan/overwrite antar pengguna.
- Database SQLite memakai mode **WAL** + `synchronous=NORMAL` untuk performa baca-tulis.
- Bot tidak pernah menyimpan token di source; seluruhnya dari env.

---

## 👤 Owner & Kontak

Dibuat oleh [@aeounn](https://t.me/aeounn) · Repo: [RennKaeo/renn-music-bot](https://github.com/RennKaeo/renn-music-bot)