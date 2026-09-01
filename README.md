# Telegram bot ini kamu yang punya. Mintalah token ke @BotFather kalau belum punya.

# ======== Dependensi / runtime ========
# Python >= 3.10
# ffmpeg wajib (buat konversi MP3 + potong preview). Di Debian/Ubuntu:
#   sudo apt update && sudo apt install -y ffmpeg python3-venv

# ======== Setup (local / VPS baru) ========
python3 -m venv venv
venv/bin/pip install -r requirements.txt

# ======== Jalankan ========
# wajib isi BOT_TOKEN dulu (dapat dari @BotFather -> /mybots -> API Token)
export BOT_TOKEN="ISI_TOKEN_DARI_BOTFATHER"

# Opsional:
#   AUDD_API_TOKEN=...      -> buat fitur /shazam (bukan token dari gambar ini; dari audd.io)
#   NVIDIA_API_KEY=...      -> kalau ada fitur AI terjemah
#   OPENAI_API_KEY=...

venv/bin/python bot.py

# Cek bot hidup: cukup /ping di Telegram, atau lihat log "Application started".

# ======== Systemd (biar auto-restart kalau server restart) ========
# 1. Salin file renn-music-bot.service -> /etc/systemd/system/
# 2. Isi token di baris Environment=
# 3. Jalankan:
#    systemctl daemon-reload
#    systemctl enable --now renn-music-bot.service
#    journalctl -u renn-music-bot.service -f

# ======== cookies.txt (opsional) ========
# Hanya dipakai kalau ada video yang butuh login/umur. Ekspor cookies YouTube kamu
# (browser extension "Get cookies.txt LOCALLY"), simpan sebagai cookies.txt di
# folder yang sama dengan bot.py, lalu:
#   chmod 600 cookies.txt
# JANGAN pernah commit cookies.txt ke git (file ini di-ignore).

# ======== Catatan audit (2026-09-01) ========
# - 9 titik download pakai suffix uuid per-request -> aman dari race/overwrite antar user.
# - DB sqlite pakai WAL + synchronous=NORMAL.
# - Bot tidak pernah menyimpan token di source; semua dari env.