#!/usr/bin/env python3
"""
Renn Music Bot v6 - Full Upgrade
Fitur: /play, /artist, /lyrics, /history, /favorites, /stats, /settings, /queue
+ Cache file_id, Direct link (YT/Spotify/SC/Apple/TikTok), Inline mode, Preview 30s, Re-download, Rate limit, Auto Clean
+ Shazam ID, Embed Lyric/Cover 320x320, Pagination, Related, Queue, Translate
Bot: @mypersonalbotmusic_bot (8863746399)
"""
import os
import re
import time
import json
import logging
import asyncio
import sqlite3
import zipfile
import requests
import tempfile
import subprocess
import random
import uuid
from pathlib import Path
from datetime import datetime, timedelta
from urllib.parse import quote_plus, urlparse

import yt_dlp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand, InlineQueryResultArticle, InputTextMessageContent
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, InlineQueryHandler, MessageHandler, filters
from telegram.constants import ParseMode

# ===== YT-DLP FIX: bypass YouTube bot detection (cookies + android client) =====
COOKIE_FILE = Path("/root/yt-music-bot/cookies.txt")
# Auto-patch all YoutubeDL instances to include extractor_args + cookies
_orig_YoutubeDL = yt_dlp.YoutubeDL
class PatchedYoutubeDL(_orig_YoutubeDL):
    def __init__(self, params=None, *args, **kwargs):
        params = dict(params or {})
        # Player clients: REMOVED forced ['android','web','mweb'] - caused SABR "Requested format is not available" (2026-08-30)
        # Let yt-dlp default handle format selection (42 formats). Only set player_skip if caller wants it.
        ea = params.get('extractor_args', {})
        # Do NOT force player_client - broken with SABR experiment (need PO token)
        # Keep ea as-is so default yt-dlp can pick best client automatically
        params['extractor_args'] = ea
        # Cookies if exists (export from browser: https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp)
        if COOKIE_FILE.exists() and COOKIE_FILE.stat().st_size > 100:
            params['cookiefile'] = str(COOKIE_FILE)
        # Common headers
        headers = params.get('http_headers', {})
        headers.setdefault('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
        params['http_headers'] = headers
        # Retry & geo bypass
        params.setdefault('retries', 3)
        params.setdefault('fragment_retries', 3)
        params.setdefault('geo_bypass', True)
        # Enable node JS runtime if available (for SABR challenge)
        try:
            import shutil
            if shutil.which('node'):
                params.setdefault('js_runtimes', {'node': {'path': shutil.which('node')}})
        except: pass
        super().__init__(params, *args, **kwargs)
yt_dlp.YoutubeDL = PatchedYoutubeDL

BOT_TOKEN = os.getenv("BOT_TOKEN", "ISI_TOKEN")
DOWNLOAD_DIR = Path("/root/yt-music-bot/downloads")
DOWNLOAD_DIR.mkdir(exist_ok=True, parents=True)
DB_PATH = Path("/root/yt-music-bot/bot_data.db")
BOT_USERNAME = "mypersonalbotmusic_bot"
# CHANNEL_ID / OWNER_ID removed (trending deleted)
AUDD_API_TOKEN = os.getenv("AUDD_API_TOKEN", "")
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
# Shazam graceful flag
try:
    from shazamio import Shazam as ShazamIO
    HAS_SHAZAMIO = True
except Exception as _e:
    ShazamIO = None
    HAS_SHAZAMIO = False
    logging.getLogger(__name__).warning(f"shazamio not available: {_e} - will fallback")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
log = logging.getLogger(__name__)

# ===== TOPIC/FORUM PATCH (2026-08-30) - keep audio in same topic, not General =====
try:
    import telegram as _tg_topic
    _orig_reply_text = _tg_topic.Message.reply_text
    async def _reply_text_with_thread(self, text, *args, **kwargs):
        if kwargs.get("message_thread_id") is None and getattr(self, "message_thread_id", None) is not None:
            kwargs["message_thread_id"] = self.message_thread_id
        return await _orig_reply_text(self, text, *args, **kwargs)
    _tg_topic.Message.reply_text = _reply_text_with_thread
    log.info("✅ Topic patch: Message.reply_text now respects message_thread_id")
except Exception as _e:
    log.warning(f"topic patch reply_text fail: {_e}")

def _get_thread_id(update=None, query=None):
    """helper to extract topic thread id"""
    try:
        if query is not None and getattr(query, "message", None) is not None and getattr(query.message, "message_thread_id", None) is not None:
            return query.message.message_thread_id
        if update is not None and getattr(update, "effective_message", None) is not None and getattr(update.effective_message, "message_thread_id", None) is not None:
            return update.effective_message.message_thread_id
        if update is not None and getattr(update, "message", None) is not None and getattr(update.message, "message_thread_id", None) is not None:
            return update.message.message_thread_id
    except:
        pass
    return None


# ===== DB =====
def init_db():
    con = sqlite3.connect(str(DB_PATH))
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    cur = con.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS file_cache (
        video_id TEXT, quality TEXT, file_id TEXT, file_unique_id TEXT,
        title TEXT, performer TEXT, file_size INTEGER, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(video_id, quality)
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER, video_id TEXT, title TEXT, artist TEXT, quality TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS favorites (
        user_id INTEGER, video_id TEXT, title TEXT, artist TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(user_id, video_id)
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS stats (
        key TEXT PRIMARY KEY, value INTEGER DEFAULT 0
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS user_settings (
        user_id INTEGER PRIMARY KEY,
        default_quality TEXT DEFAULT 'm4a'
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS queue (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        video_id TEXT,
        title TEXT,
        artist TEXT,
        quality TEXT DEFAULT 'm4a',
        position INTEGER,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")
    # init stats keys
    for k in ["total_downloads","cache_hits","total_users"]:
        cur.execute("INSERT OR IGNORE INTO stats(key,value) VALUES(?,0)", (k,))
    con.commit()
    con.close()

init_db()

def db_exec(query, params=(), fetch=False, fetchone=False):
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute(query, params)
    result = None
    if fetch:
        result = cur.fetchall()
    elif fetchone:
        result = cur.fetchone()
    else:
        con.commit()
    con.close()
    return result

def get_cache(video_id, quality):
    row = db_exec("SELECT * FROM file_cache WHERE video_id=? AND quality=?", (video_id, quality), fetchone=True)
    return dict(row) if row else None

def set_cache(video_id, quality, file_id, file_unique_id, title, performer, file_size):
    db_exec("INSERT OR REPLACE INTO file_cache(video_id,quality,file_id,file_unique_id,title,performer,file_size) VALUES(?,?,?,?,?,?,?)",
            (video_id, quality, file_id, file_unique_id, title, performer, file_size))

def add_history(user_id, video_id, title, artist, quality):
    db_exec("INSERT INTO history(user_id,video_id,title,artist,quality) VALUES(?,?,?,?,?)", (user_id, video_id, title, artist, quality))
    db_exec("UPDATE stats SET value=value+1 WHERE key='total_downloads'")
    # track users
    # we don't have unique users table, just increment if not seen? simple: count distinct from history
    pass

def get_history(user_id, limit=10):
    rows = db_exec("SELECT * FROM history WHERE user_id=? ORDER BY id DESC LIMIT ?", (user_id, limit), fetch=True)
    return [dict(r) for r in rows] if rows else []

def toggle_fav(user_id, video_id, title, artist):
    exists = db_exec("SELECT 1 FROM favorites WHERE user_id=? AND video_id=?", (user_id, video_id), fetchone=True)
    if exists:
        db_exec("DELETE FROM favorites WHERE user_id=? AND video_id=?", (user_id, video_id))
        return False
    else:
        db_exec("INSERT INTO favorites(user_id,video_id,title,artist) VALUES(?,?,?,?)", (user_id, video_id, title, artist))
        return True

def get_favorites(user_id):
    rows = db_exec("SELECT * FROM favorites WHERE user_id=? ORDER BY created_at DESC", (user_id,), fetch=True)
    return [dict(r) for r in rows] if rows else []

def inc_cache_hit():
    db_exec("UPDATE stats SET value=value+1 WHERE key='cache_hits'")

def get_stats():
    rows = db_exec("SELECT key,value FROM stats", fetch=True)
    d = {r["key"]: r["value"] for r in rows} if rows else {}
    # total users approx distinct
    r = db_exec("SELECT COUNT(DISTINCT user_id) as c FROM history", fetchone=True)
    d["total_users"] = r["c"] if r and r["c"] else 0
    # favorites count
    r2 = db_exec("SELECT COUNT(*) as c FROM favorites", fetchone=True)
    d["total_favs"] = r2["c"] if r2 else 0
    r3 = db_exec("SELECT COUNT(*) as c FROM file_cache", fetchone=True)
    d["cache_size"] = r3["c"] if r3 else 0
    return d

# ===== User Settings Helpers (v6) =====
def get_user_quality(user_id: int) -> str:
    row = db_exec("SELECT default_quality FROM user_settings WHERE user_id=?", (user_id,), fetchone=True)
    return row["default_quality"] if row and row["default_quality"] in ("192","320","m4a") else "m4a"

def set_user_quality(user_id: int, quality: str):
    quality = quality if quality in ("192","320","m4a") else "m4a"
    db_exec("INSERT OR REPLACE INTO user_settings(user_id,default_quality) VALUES(?,?)", (user_id, quality))

# ===== Queue Helpers (v6) =====
def add_to_queue(user_id: int, video_id: str, title: str, artist: str, quality: str = "m4a"):
    row = db_exec("SELECT MAX(position) as mx FROM queue WHERE user_id=?", (user_id,), fetchone=True)
    mx = row["mx"] if row and row["mx"] is not None else 0
    pos = mx + 1
    cnt = db_exec("SELECT COUNT(*) as c FROM queue WHERE user_id=?", (user_id,), fetchone=True)
    if cnt and cnt["c"] >= 50:
        return False, "antrian penuh max 50"
    db_exec("INSERT INTO queue(user_id,video_id,title,artist,quality,position) VALUES(?,?,?,?,?,?)",
            (user_id, video_id, title, artist, quality, pos))
    return True, pos

def get_queue(user_id: int, limit=50):
    rows = db_exec("SELECT * FROM queue WHERE user_id=? ORDER BY position ASC LIMIT ?", (user_id, limit), fetch=True)
    return [dict(r) for r in rows] if rows else []

def clear_queue(user_id: int):
    db_exec("DELETE FROM queue WHERE user_id=?", (user_id,))

def remove_queue_at(user_id: int, position: int):
    db_exec("DELETE FROM queue WHERE user_id=? AND position=?", (user_id, position))
    rows = get_queue(user_id, 100)
    for idx, r in enumerate(rows, 1):
        db_exec("UPDATE queue SET position=? WHERE id=?", (idx, r["id"]))

def pop_queue_next(user_id: int):
    rows = get_queue(user_id, 1)
    if not rows:
        return None
    item = rows[0]
    db_exec("DELETE FROM queue WHERE id=?", (item["id"],))
    rows2 = get_queue(user_id, 100)
    for idx, r in enumerate(rows2, 1):
        db_exec("UPDATE queue SET position=? WHERE id=?", (idx, r["id"]))
    return item

# ===== URL helpers v6 (SoundCloud/Apple/TikTok) =====
def is_soundcloud_url(url: str) -> bool:
    return "soundcloud.com" in url

def is_tiktok_url(url: str) -> bool:
    return "tiktok.com" in url

def is_apple_url(url: str) -> bool:
    return "music.apple.com" in url

# ===== Lyrics Translate helper (v6) =====
def translate_text_chunks(text: str, target: str = "id") -> str:
    """Translate via MyMemory free API chunk 500. target 'id' => en|id, 'en' => id|en"""
    if not text:
        return ""
    chunks = [text[i:i+500] for i in range(0, len(text), 500)]
    chunks = chunks[:6]
    langpair = "en|id" if target == "id" else "id|en"
    out_parts = []
    for ch in chunks:
        try:
            r = requests.get("https://api.mymemory.translated.net/get", params={"q": ch, "langpair": langpair}, timeout=12)
            if r.status_code == 200:
                data = r.json()
                trans = data.get("responseData", {}).get("translatedText")
                if trans:
                    out_parts.append(trans)
                else:
                    out_parts.append(ch)
            else:
                out_parts.append(ch)
        except Exception as e:
            log.warning(f"translate fail {e}")
            out_parts.append(ch)
        time.sleep(0.3)
    result = "\n".join(out_parts)
    return result[:3500]

# ===== Related / Autoplay helper (v6) =====
def yt_get_related(query_title: str, limit=5):
    try:
        q = f"{query_title} related"
        return yt_search(q, limit)
    except Exception as e:
        log.warning(f"yt_get_related fail {e}")
        return []

# ===== Helpers =====
def safe_filename(s: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "", s)[:80]

def format_duration(sec):
    if not sec:
        return "-"
    return f"{int(sec//60)}:{int(sec%60):02d}"

def is_url(s: str) -> bool:
    return s.startswith("http://") or s.startswith("https://") or s.startswith("www.") or "youtu.be" in s or "youtube.com" in s or "spotify.com" in s or "soundcloud.com" in s or "tiktok.com" in s or "music.apple.com" in s

def extract_youtube_id(url: str):
    # try various patterns
    m = re.search(r"(?:v=|youtu\.be/|youtube\.com/embed/|youtube\.com/shorts/)([A-Za-z0-9_-]{11})", url)
    if m:
        return m.group(1)
    # playlist
    m2 = re.search(r"list=([A-Za-z0-9_-]+)", url)
    if m2:
        return None  # playlist
    return None

def is_playlist_url(url: str) -> bool:
    return "list=" in url and ("youtube.com" in url or "youtu.be" in url)

def resolve_spotify_title(url: str):
    """Try to get title from Spotify oembed, fallback to None"""
    try:
        # Use oembed
        oembed_url = f"https://open.spotify.com/oembed?url={quote_plus(url)}"
        r = requests.get(oembed_url, timeout=10, headers={"User-Agent":"Mozilla/5.0"})
        if r.status_code == 200:
            data = r.json()
            title = data.get("title","")
            # title often like "Song - Artist"
            return title
    except Exception as e:
        log.warning(f"spotify resolve fail {e}")
    return None

def is_spotify_url(url: str) -> bool:
    return "open.spotify.com" in url or "spotify.com" in url

def is_spotify_playlist_url(url: str) -> bool:
    return "spotify.com" in url and ("/playlist/" in url or "/album/" in url)

def get_spotify_playlist_track_ids(url: str, limit=50):
    """Extract track IDs from Spotify playlist/album HTML via meta tags"""
    try:
        # normalize url (remove ?si=)
        url_clean = url.split("?")[0]
        r = requests.get(url_clean, timeout=15, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        if r.status_code != 200:
            log.warning(f"spotify playlist fetch status {r.status_code}")
            return []
        html = r.text
        # find all track links
        ids = re.findall(r"https://open\.spotify\.com/track/([A-Za-z0-9]+)", html)
        # dedup preserve order
        seen = set()
        uniq = []
        for i in ids:
            if i not in seen:
                seen.add(i)
                uniq.append(i)
            if len(uniq) >= limit:
                break
        return uniq
    except Exception as e:
        log.warning(f"get_spotify_playlist fail {e}")
        return []

def resolve_spotify_track_titles(track_ids, limit=50):
    """Bulk resolve via oembed, returns list of 'Title' strings"""
    titles = []
    for tid in track_ids[:limit]:
        url = f"https://open.spotify.com/track/{tid}"
        t = resolve_spotify_title(url)
        if t:
            titles.append(t)
        else:
            titles.append(tid)  # fallback to id
        # slight delay to avoid rate limit
        time.sleep(0.2)
    return titles

# Rate limit: user_id -> [timestamps]
rate_store = {}
def check_rate_limit(user_id: int, limit=5, window=30) -> bool:
    """return True if allowed, False if hit limit"""
    now = time.time()
    lst = rate_store.get(user_id, [])
    # keep only within window
    lst = [t for t in lst if now - t < window]
    if len(lst) >= limit:
        rate_store[user_id] = lst
        return False
    lst.append(now)
    rate_store[user_id] = lst
    return True

def get_lyrics(title: str, artist: str = ""):
    """Try lrclib then lyrics.ovh"""
    # clean title: remove (Official Video) etc
    clean_title = re.sub(r"\(.*?\)|\[.*?\]", "", title).strip()
    # Try lrclib search
    try:
        q = f"{clean_title} {artist}".strip()
        r = requests.get(f"https://lrclib.net/api/search?q={quote_plus(q)}", timeout=10)
        if r.status_code == 200:
            arr = r.json()
            if arr and len(arr) > 0:
                # pick first with plainLyrics
                for item in arr[:3]:
                    lyrics = item.get("plainLyrics") or item.get("syncedLyrics")
                    if lyrics:
                        # strip synced timestamps
                        lyrics = re.sub(r"\[\d+:\d+\.\d+\]", "", lyrics).strip()
                        return lyrics[:4000], item.get("trackName",""), item.get("artistName","")
    except Exception as e:
        log.warning(f"lrclib fail {e}")
    # fallback lyrics.ovh
    try:
        # need artist and title separate
        a = artist or "Unknown"
        # if title contains " - " split
        if " - " in clean_title and not artist:
            a = clean_title.split(" - ")[0].strip()
            clean_title = clean_title.split(" - ",1)[1].strip()
        r = requests.get(f"https://api.lyrics.ovh/v1/{quote_plus(a)}/{quote_plus(clean_title)}", timeout=10)
        if r.status_code == 200:
            data = r.json()
            lyr = data.get("lyrics")
            if lyr:
                return lyr[:4000], clean_title, a
    except Exception as e:
        log.warning(f"lyrics.ovh fail {e}")
    return None, None, None

COMMANDS = [
    BotCommand("start", "🏠 Menu utama"),
    BotCommand("play", "🎵 Cari / download lagu"),
    BotCommand("artist", "👤 Cari lagu artis"),
    BotCommand("lyrics", "📜 Cari lirik lagu"),
    BotCommand("history", "🕘 Riwayat kamu"),
    BotCommand("favorites", "⭐ Favorit kamu"),
    BotCommand("stats", "📊 Statistik bot"),
    BotCommand("settings", "⚙️ Kualitas default"),
    BotCommand("queue", "📋 Antrian kamu"),
    BotCommand("clearqueue", "🗑️ Kosongkan antrian"),
    BotCommand("help", "❓ Bantuan"),
    BotCommand("ping", "🏓 Cek bot"),
]

async def post_init(app: Application):
    await app.bot.set_my_commands(COMMANDS)
    log.info("✅ Menu commands v6 terpasang")
    # start auto clean background
    asyncio.create_task(auto_clean_task())

async def auto_clean_task():
    while True:
        await asyncio.sleep(3600)
        try:
            now = time.time()
            deleted = 0
            for p in DOWNLOAD_DIR.glob("*"):
                try:
                    if p.is_file() and now - p.stat().st_mtime > 3600:
                        p.unlink()
                        deleted += 1
                except: pass
            if deleted:
                log.info(f"🧹 Auto clean {deleted} files")
        except Exception as e:
            log.warning(f"auto clean fail {e}")

def yt_search(query: str, limit: int = 5):
    ydl_opts = {'quiet': True, 'extract_flat': True, 'skip_download': True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        result = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
        return result.get('entries', [])

def yt_get_info(url: str):
    ydl_opts = {'quiet': True, 'skip_download': True, 'noplaylist': True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(url, download=False)

def yt_get_playlist(url: str, limit=50):
    ydl_opts = {'quiet': True, 'extract_flat': True, 'skip_download': True, 'playlistend': limit}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        entries = info.get('entries', [])
        # filter None
        entries = [e for e in entries if e]
        return entries[:limit], info.get('title','Playlist')

# ===== Handlers =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # handle deep link like /start vid_abc123_192
    args = context.args
    if args and args[0].startswith("vid_"):
        try:
            parts = args[0].split("_")
            vid = parts[1]
            quality = parts[2] if len(parts)>2 else "m4a"
            url = f"https://www.youtube.com/watch?v={vid}"
            video = {"id": vid, "title": f"YouTube {vid}", "uploader": "YouTube", "duration": 0, "url": url}
            context.user_data['search_results'] = [video]
            context.user_data['picked_idx'] = 0
            await update.message.reply_text(f"🎵 Deep link: {url}\nMemproses {quality}...")
            def_q = get_user_quality(update.effective_user.id)
            quality = def_q  # opsi1 auto
            mark = " ✓ default" if def_q==quality else ""
            kb = [
                [InlineKeyboardButton("▶️ Preview 30s", callback_data="preview_0"),
                 InlineKeyboardButton("📜 Lirik", callback_data="lyrics_0")],
                [InlineKeyboardButton("🔀 Lagu Serupa", callback_data="related_0"),
                 InlineKeyboardButton("➕ Queue", callback_data="queue_0")],
                [InlineKeyboardButton("❌ Batal", callback_data="cancel")],
            ]
            await update.message.reply_text(f"🎵 Dipilih: {video['title']}\n⚡ Auto-download {quality} ✨ — tunggu 10-30 detik...", reply_markup=InlineKeyboardMarkup(kb))
            # opsi1: langsung auto-download tanpa pilih kualitas
            # trigger same as pick handler - reuse auto logic
            try:
                vid2 = video['id']
                url2 = f"https://www.youtube.com/watch?v={vid2}"
                title2 = video['title']
                uploader2 = "YouTube"
                dur2 = format_duration(0)
                # cache check
                cached2 = get_cache(vid2, quality)
                if cached2 and cached2.get('file_id'):
                    try:
                        cap2 = f"🎵 {title2}\n👤 {uploader2} • ⏱ {dur2}\n📦 cached • M4A 128 AAC ⚡\n✅ via @{BOT_USERNAME}"
                        sent2 = await context.bot.send_audio(chat_id=update.effective_chat.id, message_thread_id=getattr(update.effective_message, "message_thread_id", None), audio=cached2['file_id'], caption=cap2, title=title2[:64], performer=uploader2[:64], read_timeout=300, write_timeout=300)
                        inc_cache_hit()
                        add_history(update.effective_user.id, vid2, title2, uploader2, quality)
                        await update.message.reply_text(f"✅ Done (cached {quality}): {title2}\ncek di bawah ⬇️")
                        return
                    except Exception as e:
                        log.warning(f"cache send fail deep link {e}")
                        db_exec("DELETE FROM file_cache WHERE video_id=? AND quality=?", (vid2, quality))
                await update.message.reply_text(f"⏳ Downloading {quality}...\n🎵 {title2}\nMohon tunggu 10-30 detik... ✨")
                import asyncio as _asyncio2, os as _os2, yt_dlp as _yt2
                safe2 = safe_filename(f"{uploader2} - {title2} [{quality}]") + f"_{uuid.uuid4().hex[:10]}"
                loop2 = _asyncio2.get_event_loop()
                def do_dl2():
                    out2 = str(DOWNLOAD_DIR / f"{safe2}.%(ext)s")
                    opts2 = {'format': 'bestaudio[ext=m4a]/bestaudio/best','outtmpl': out2,'quiet': True,'noplaylist': True}
                    with _yt2.YoutubeDL(opts2) as ydl2:
                        ydl2.download([url2])
                    for ext in [".m4a",".mp4"]:
                        pp = DOWNLOAD_DIR / f"{safe2}{ext}"
                        if pp.exists(): return str(pp), None
                    files2 = sorted(DOWNLOAD_DIR.glob(f"{safe2}*"), key=lambda x: x.stat().st_mtime, reverse=True)
                    return (str(files2[0]) if files2 else None), None
                filepath2, thumb2 = await loop2.run_in_executor(None, do_dl2)
                if filepath2 and os.path.exists(filepath2):
                    try:
                        from mutagen.mp4 import MP4
                        audio2 = MP4(filepath2)
                        audio2["\xa9nam"] = title2
                        audio2["\xa9ART"] = uploader2
                        audio2.save()
                    except: pass
                    cap2 = f"🎵 {title2}\n👤 {uploader2} • ⏱ {dur2}\n📦 M4A 128 AAC\n✅ via @{BOT_USERNAME}"
                    sent2 = await context.bot.send_audio(chat_id=update.effective_chat.id, message_thread_id=getattr(update.effective_message, "message_thread_id", None), audio=open(filepath2,'rb'), caption=cap2, title=title2[:64], performer=uploader2[:64], read_timeout=300, write_timeout=300, filename=os.path.basename(filepath2))
                    try:
                        set_cache(vid2, quality, sent2.audio.file_id, sent2.audio.file_unique_id, title2, uploader2, sent2.audio.file_size or os.path.getsize(filepath2))
                    except: pass
                    add_history(update.effective_user.id, vid2, title2, uploader2, quality)
                    await update.message.reply_text(f"✅ Done (M4A): {title2}\ncek di bawah ⬇️")
                    try: os.remove(filepath2)
                    except: pass
            except Exception as e:
                log.warning(f"deep link auto fail {e}")
                await update.message.reply_text(f"❌ Gagal auto-download: {e}")
            return
        except Exception as e:
            log.warning(f"deep link fail {e}")
    kb = [
        [InlineKeyboardButton("🎵 Cari Lagu", callback_data="help_play"),
         InlineKeyboardButton("👤 Cari Artis", callback_data="help_artist")],
        [InlineKeyboardButton("📜 Lirik", callback_data="help_lyrics"),
         InlineKeyboardButton("📊 Stats", callback_data="show_stats")],
        [InlineKeyboardButton("🕘 History", callback_data="show_history"),
         InlineKeyboardButton("⭐ Favorites", callback_data="show_favs")],
        [InlineKeyboardButton("⚙️ Settings", callback_data="show_settings"),
         InlineKeyboardButton("📋 Queue", callback_data="show_queue")],
        [InlineKeyboardButton("❓ Help", callback_data="help_general"),
         InlineKeyboardButton("🔍 Shazam", callback_data="help_shazam")],
        [InlineKeyboardButton("👨‍💻 Owner", url="https://t.me/aeounn"),
         InlineKeyboardButton("📢 Channel", url="https://t.me/telegram")],
    ]
    text = (
        "🎶 **Renn Music Bot v6 — Full Upgrade**\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "Halo! Bot musik trial semua fitur v6 nyala.\n\n"
        "**📋 Daftar Perintah:**\n"
        "▸ `/play <judul / link YT / Spotify / SC / TikTok / Apple>` — cari & download\n"
        "▸ `/artist <nama>` — top lagu artis\n"
        "▸ `/lyrics <judul>` — cari lirik + translate ID/EN\n"
        "▸ `/history` — riwayat download kamu\n"
        "▸ `/favorites` — lagu favorit kamu\n"
        "▸ `/queue` — lihat antrian personal • `/clearqueue` kosongkan\n"
        "▸ `/settings` — set kualitas default (192/320/m4a)\n"
        "▸ `/stats` — statistik bot\n"
        "▸ `/ping` — cek bot\n\n"
        "✨ **Baru v6:**\n"
        "• Settings kualitas default per user\n"
        "• Shazam — kirim voice/audio/video untuk identify lagu\n"
        "• Lirik ter-embed + cover 320x320 di file\n"
        "• Support SoundCloud / Apple Music / TikTok link\n"
        "• Pagination search 20 hasil + Next/Prev\n"
        "• Related songs & Queue personal\n"
        "• Translate lirik ID/EN\n"

        "💡 *Ketik `/` untuk lihat menu biru, atau kirim voice note untuk Shazam!*"
    )
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "❓ **BANTUAN - Renn Music Bot v6**\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "**🎵 /play <judul / link>**\n"
        "`/play tak ingin usai`\n"
        "`/play https://youtu.be/xxxx`\n"
        "`/play https://open.spotify.com/track/xxxx`\n"
        "`/play https://soundcloud.com/artist/track`\n"
        "`/play https://www.tiktok.com/...` atau `https://music.apple.com/...`\n\n"
        "**👤 /artist <nama>**\n"
        "`/artist bernadya`\n\n"
        "**📜 /lyrics <judul>** → lalu pakai tombol translate 🌐\n"
        "`/lyrics tak ingin usai`\n\n"
        "**⚙️ /settings** — atur kualitas default 192/320/m4a\n"
        "**📋 /queue** — lihat antrian • `/clearqueue` kosongkan\n"
        "**🔍 Shazam** — kirim voice/audio/video, bot akan identify\n\n"
        "**🕘 /history** — lihat 10 lagu terakhir kamu\n"
        "**⭐ /favorites** — lihat favorit + tombol hapus\n"
        "**📊 /stats** — statistik global\n\n"
        "**Kualitas:**\n"
        "• `MP3 192` — hemat 5-6MB\n"
        "• `MP3 320` — gede 9-10MB\n"
        "• `M4A 128` — murni AAC kecil (default)\n\n"
        "🐛 **Bug?** Hubungi @aeounn"
    )
    kb = [
        [InlineKeyboardButton("👨‍💻 Hubungi Owner", url="https://t.me/aeounn")],
        [InlineKeyboardButton("🏠 Menu Utama", callback_data="back_start")],
    ]
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    s = get_stats()
    await update.message.reply_text(f"🏓 Pong! Bot v5 aktif — DL:{s.get('total_downloads',0)} Cache:{s.get('cache_hits',0)} Users:{s.get('total_users',0)} ✨")

async def lyrics_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("📜 **Cara pakai:**\n`/lyrics <judul lagu>`\nContoh: `/lyrics tak ingin usai`\n`/lyrics bernadya apa mungkin`", parse_mode="Markdown")
        return
    query = " ".join(context.args)
    msg = await update.message.reply_text(f"📜 Nyari lirik `{query}`...", parse_mode="Markdown")
    title = query
    artist = ""
    if " - " in query:
        artist, title = query.split(" - ",1)
    def do():
        lyr, t, a = get_lyrics(title, artist)
        return lyr, t, a
    loop = asyncio.get_event_loop()
    lyrics, t, a = await loop.run_in_executor(None, do)
    if not lyrics:
        await msg.edit_text(f"❌ Lirik gak ketemu untuk `{query}`\nCoba format: `/lyrics artist - judul`", parse_mode="Markdown")
        return
    header = f"📜 **{t or title}** — {a or artist}\n━━━━━━━━━━━━━━━━━━\n" if t else f"📜 **{query}**\n━━━━━━━━━━━━━━━━━━\n"
    full = header + lyrics
    if len(full) > 4000:
        full = full[:4000] + "\n\n... (kepotong, kepanjangan)"
    # v6: add translate buttons, store in user_data for later? use context but need idx - we simulate idx 0 with temp store
    # store lyrics in context for translate callback via search_results dummy
    # We'll create dummy entry for translate
    dummy_entry = {"title": t or title, "uploader": a or artist}
    # ensure search_results exists for translate logic, but translate callback for lyrics_cmd will use msg context? For simplicity, just add buttons that trigger tr_lyrics but need entries.
    # So we store in user_data with key lyrics_cmd_last
    context.user_data['search_results'] = [dummy_entry]
    context.user_data['search_results_full'] = [dummy_entry]
    context.user_data['lyrics_0'] = lyrics
    kb = [
        [InlineKeyboardButton("🌐 Translate ID", callback_data="tr_lyrics_0_id"),
         InlineKeyboardButton("🌐 Translate EN", callback_data="tr_lyrics_0_en")],
        [InlineKeyboardButton("❌ Tutup", callback_data="cancel")]
    ]
    await msg.edit_text(full, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

async def history_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = get_history(update.effective_user.id, 10)
    if not rows:
        await update.message.reply_text("🕘 Belum ada history. Coba `/play` dulu ya!")
        return
    text = "🕘 **History kamu (10 terakhir):**\n━━━━━━━━━━━━━━━━━━\n"
    for i, r in enumerate(rows,1):
        text += f"{i}. {r['title'][:35]} — {r['artist'][:20]} [{r['quality']}] • {r['created_at']}\n"
    kb = [[InlineKeyboardButton("⭐ Lihat Favorites", callback_data="show_favs")]]
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb))

async def favorites_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = get_favorites(update.effective_user.id)
    if not rows:
        await update.message.reply_text("⭐ Belum ada favorit. Pas abis download, klik ⭐ Favorite ya!")
        return
    text = "⭐ **Favorit kamu:**\n━━━━━━━━━━━━━━━━━━\n"
    kb = []
    for i, r in enumerate(rows[:10]):
        text += f"{i+1}. {r['title'][:35]} — {r['artist'][:15]}\n"
        kb.append([InlineKeyboardButton(f"▶️ {r['title'][:30]}", callback_data=f"favplay_{r['video_id']}"),
                   InlineKeyboardButton("❌", callback_data=f"favdel_{r['video_id']}")])
    kb.append([InlineKeyboardButton("🕘 History", callback_data="show_history")])
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb))

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    s = get_stats()
    text = (
        "📊 **Statistik Bot v5**\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"📥 Total Downloads: **{s.get('total_downloads',0)}**\n"
        f"⚡ Cache Hits: **{s.get('cache_hits',0)}**\n"
        f"👥 Total Users: **{s.get('total_users',0)}**\n"
        f"⭐ Total Favorites: **{s.get('total_favs',0)}**\n"
        f"💾 Cached Files: **{s.get('cache_size',0)}**\n"
        f"📂 Cache Dir: `{DOWNLOAD_DIR}`\n\n"
        f"Rate limit: 5 req / 30s per user"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

# ===== New v6 Commands =====
async def settings_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = get_user_quality(update.effective_user.id)
    kb = [
        [InlineKeyboardButton(f"{'✅ ' if q=='192' else ''}MP3 192kbps (hemat)", callback_data="set_quality_192"),
         InlineKeyboardButton(f"{'✅ ' if q=='320' else ''}MP3 320kbps (gede)", callback_data="set_quality_320")],
        [InlineKeyboardButton(f"{'✅ ' if q=='m4a' else ''}M4A 128kbps AAC asli ✨ (default)", callback_data="set_quality_m4a")],
        [InlineKeyboardButton("❌ Tutup", callback_data="cancel")],
    ]
    text = f"⚙️ **Settings Kualitas Default**\n━━━━━━━━━━━━━━━━━━\nDefault kamu sekarang: **{q}**\n\nPilih kualitas default untuk download. Saat pick lagu, opsi default akan bertanda ✅ tapi kamu tetap bisa pilih kualitas lain."
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

async def queue_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = get_queue(update.effective_user.id)
    if not rows:
        kb = [[InlineKeyboardButton("🎵 Cari Lagu", callback_data="help_play")]]
        await update.message.reply_text("📋 Antrian kosong. Pas di picker lagu, klik ➕ Queue untuk tambah.", reply_markup=InlineKeyboardMarkup(kb))
        return
    text = f"📋 **Antrian kamu ({len(rows)} lagu):**\n━━━━━━━━━━━━━━━━━━\n"
    kb = []
    for r in rows:
        text += f"{r['position']}. {r['title'][:35]} — {r['artist'][:15]} [{r['quality']}]\n"
        # per item row: play next / remove? simplify
    # actions
    kb.append([InlineKeyboardButton("▶️ Play Next (download)", callback_data="queue_play_next"),
               InlineKeyboardButton("⬇️ Download Semua", callback_data="queue_download_all")])
    kb.append([InlineKeyboardButton("🗑️ Clear Queue", callback_data="queue_clear"),
               InlineKeyboardButton("❌ Tutup", callback_data="cancel")])
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb))

async def clearqueue_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_queue(update.effective_user.id)
    await update.message.reply_text("🗑️ Antrian dikosongkan.")

# Shazam handler v6
async def shazam_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # detect file
    file_obj = None
    try:
        if update.message.voice:
            file_obj = await context.bot.get_file(update.message.voice.file_id)
        elif update.message.audio:
            file_obj = await context.bot.get_file(update.message.audio.file_id)
        elif update.message.video_note:
            file_obj = await context.bot.get_file(update.message.video_note.file_id)
        elif update.message.video:
            file_obj = await context.bot.get_file(update.message.video.file_id)
        elif update.message.document:
            # check mime audio/video
            mime = update.message.document.mime_type or ""
            if "audio" in mime or "video" in mime or update.message.document.file_name.lower().endswith((".mp3",".m4a",".ogg",".wav",".mp4",".mov")):
                file_obj = await context.bot.get_file(update.message.document.file_id)
            else:
                await update.message.reply_text("❌ File bukan audio/video. Kirim voice, audio, video, atau file musik ya.")
                return
        else:
            return
    except Exception as e:
        await update.message.reply_text(f"❌ Gagal ambil file: {e}")
        return
    if not file_obj:
        await update.message.reply_text("❌ Gagal ambil file untuk Shazam.")
        return
    # determine extension from message
    ext = ".ogg"
    try:
        if update.message.voice:
            ext = ".ogg"
        elif update.message.audio and update.message.audio.file_name:
            ext = "." + update.message.audio.file_name.split(".")[-1] if "." in update.message.audio.file_name else ".mp3"
        elif update.message.video:
            ext = ".mp4"
        elif update.message.video_note:
            ext = ".mp4"
        elif update.message.document and update.message.document.file_name:
            ext = "." + update.message.document.file_name.split(".")[-1] if "." in update.message.document.file_name else ".mp3"
        ext = ext[:5]  # safety
    except:
        ext = ".ogg"
    tmp_path = f"/tmp/shazam_{update.effective_user.id}_{int(time.time())}{ext}"
    msg = await update.message.reply_text("🔍 Dengerin lagunya... ⏳ (Shazam)")
    try:
        await file_obj.download_to_drive(custom_path=tmp_path)
    except Exception as e:
        await msg.edit_text(f"❌ Gagal download file: {e}")
        return
    # try shazamio first
    shazam_result = None
    title_found = None
    artist_found = None
    try:
        if HAS_SHAZAMIO and ShazamIO:
            try:
                shazam = ShazamIO()
                out = await shazam.recognize(tmp_path)
                # out structure: {'track': {'title':..., 'subtitle':..., ...}}
                track = out.get('track') if isinstance(out, dict) else None
                if track:
                    title_found = track.get('title')
                    artist_found = track.get('subtitle')
                    if title_found:
                        shazam_result = f"{artist_found} - {title_found}" if artist_found else title_found
            except Exception as e:
                log.warning(f"shazamio recognize fail {e}")
                shazam_result = None
        # fallback AudD if token exists
        if not shazam_result and AUDD_API_TOKEN:
            try:
                with open(tmp_path,'rb') as f:
                    r = requests.post("https://api.audd.io/", data={"api_token": AUDD_API_TOKEN, "return": "apple_music,spotify"}, files={"file": f}, timeout=20)
                    if r.status_code==200:
                        data = r.json()
                        if data.get('status')=='success' and data.get('result'):
                            res = data['result']
                            title_found = res.get('title')
                            artist_found = res.get('artist')
                            if title_found:
                                shazam_result = f"{artist_found} - {title_found}" if artist_found else title_found
            except Exception as e:
                log.warning(f"AudD fallback fail {e}")
    finally:
        try: os.remove(tmp_path)
        except: pass

    if not shazam_result or not title_found:
        await msg.edit_text("❌ Shazam gak nemuin lagunya 😢\nCoba kirim potongan yang lebih jelas (10-15 detik, minim noise) atau ketik `/play <judul>` manual.\n\nKalau Python 3.13 error audioop, install `audioop-lts` sudah terpasang kok — coba lagi.")
        return
    # success: search YouTube for that title
    query = f"{artist_found} {title_found}" if artist_found else title_found
    await msg.edit_text(f"✅ Ketemu: **{shazam_result}**\n🔍 Nyari di YouTube...", parse_mode="Markdown")
    try:
        loop = asyncio.get_event_loop()
        entries = await loop.run_in_executor(None, lambda: yt_search(query, 5))
    except Exception as e:
        await msg.edit_text(f"✅ Shazam: {shazam_result}\n❌ Gagal search YouTube: {e}")
        return
    if not entries:
        await msg.edit_text(f"✅ Shazam: {shazam_result}\n❌ Gak ketemu di YouTube, coba `/play {query}`")
        return
    # store and show picker
    context.user_data['search_results'] = entries
    # find best match index 0
    kb = []
    for i, e in enumerate(entries):
        t = e.get('title','Unknown')[:45]
        dur = format_duration(e.get('duration'))
        kb.append([InlineKeyboardButton(f"{i+1}. {t} ⏱ {dur}", callback_data=f"pick_{i}")])
    kb.append([InlineKeyboardButton("❌ Batal", callback_data="cancel")])
    await msg.edit_text(f"✅ **Shazam Result:** {shazam_result}\n━━━━━━━━━━━━━━━━━━\n🔍 Hasil YouTube untuk `{query}` — pilih lagu untuk download:", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
    # also auto show preview option? user can pick then preview

# ===== Pagination helpers (v6) =====
def build_search_keyboard(entries, page):
    PER_PAGE = 5
    total = len(entries)
    total_pages = (total + PER_PAGE - 1)//PER_PAGE if total else 1
    start = page*PER_PAGE
    end = min(start+PER_PAGE, total)
    kb = []
    for i in range(start, end):
        e = entries[i]
        title = e.get('title','Unknown')[:40]
        dur = format_duration(e.get('duration'))
        kb.append([InlineKeyboardButton(f"{i+1}. {title} ⏱ {dur}", callback_data=f"pick_{i}")])
    nav = []
    if page>0:
        nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"search_page_{page-1}"))
    nav.append(InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data="noop"))
    if page < total_pages-1:
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"search_page_{page+1}"))
    if nav:
        kb.append(nav)
    kb.append([InlineKeyboardButton("❌ Batal", callback_data="cancel")])
    return kb

async def show_search_page(query, msg_or_q, context, page, edit=False):
    entries_full = context.user_data.get('search_results_full', [])
    total_pages = (len(entries_full)+4)//5
    kb = build_search_keyboard(entries_full, page)
    text = f"🎶 **Hasil untuk:** `{query}`\n━━━━━━━━━━━━━━━━━━\nHalaman {page+1}/{total_pages} — pilih lagu:"
    if edit:
        # msg_or_q is callback query message
        await msg_or_q.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
    else:
        await msg_or_q.edit_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

async def play(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        context.user_data['awaiting_play'] = True
        context.user_data['awaiting_play_msg'] = update.message.message_id
        await update.message.reply_text("What song are you looking for?")
        return
    if not check_rate_limit(update.effective_user.id, limit=5, window=30):
        await update.message.reply_text("⏳ Kebanyakan request, slow down 30 detik ya bree. (5 req / 30s)")
        return
    query_raw = " ".join(context.args).strip()
    # Detect direct URL
    if is_url(query_raw):
        # Spotify -> resolve title then search
        if "spotify.com" in query_raw:
            msg = await update.message.reply_text(f"🎵 Link Spotify terdeteksi...\n🔗 {query_raw}\n🔍 Nyari di YouTube...")
            title_spotify = await asyncio.get_event_loop().run_in_executor(None, lambda: resolve_spotify_title(query_raw))
            if title_spotify:
                query = title_spotify
                await msg.edit_text(f"🎵 Spotify: **{title_spotify}**\n🔍 Nyari versi YouTube...")
            else:
                await msg.edit_text("❌ Gagal resolve Spotify title, coba kirim judul manual `/play <judul>`")
                return
            query_raw = query
            # fall through to search logic below, reuse msg? we will create new msg for search
        elif is_soundcloud_url(query_raw) or is_tiktok_url(query_raw) or is_apple_url(query_raw):
            # Direct via yt-dlp
            label = "SoundCloud" if is_soundcloud_url(query_raw) else "TikTok" if is_tiktok_url(query_raw) else "Apple Music"
            msg = await update.message.reply_text(f"🔗 Link {label} terdeteksi...\n🎵 {query_raw[:60]}\n⏳ Ambil info via yt-dlp...")
            try:
                info = await asyncio.get_event_loop().run_in_executor(None, lambda: yt_get_info(query_raw))
                title = info.get('title','Unknown')
                uploader = info.get('uploader') or info.get('creator') or info.get('artist') or "Unknown"
                dur = info.get('duration')
                vid = info.get('id') or f"ext_{int(time.time())}"
                entry = {"id": vid, "title": title, "uploader": uploader, "duration": dur, "url": query_raw, "webpage_url": query_raw}
                # store single result but also keep full for consistency
                context.user_data['search_results'] = [entry]
                context.user_data['search_results_full'] = [entry]
                context.user_data['search_query'] = title
                context.user_data['picked_idx'] = 0
                def_q = get_user_quality(update.effective_user.id)
                quality = def_q  # opsi1 auto
                kb = [
                    [InlineKeyboardButton("▶️ Preview 30s", callback_data="preview_0"),
                     InlineKeyboardButton("📜 Lirik", callback_data="lyrics_0")],
                    [InlineKeyboardButton("🔀 Lagu Serupa", callback_data="related_0"),
                     InlineKeyboardButton("➕ Queue", callback_data="queue_0")],
                    [InlineKeyboardButton("❌ Batal", callback_data="cancel")],
                ]
                await msg.edit_text(
                    f"🎵 **Ditemukan via {label}:**\n{title}\n👤 {uploader} • ⏱ {format_duration(dur)}\n\n⚡ **Auto-download {quality} ✨** — tunggu 10-30 detik...",
                    parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb)
                )
                # opsi1 auto-download
                try:
                    url_a = entry.get('url') or entry.get('webpage_url') or query_raw
                    vid_a = entry.get('id')
                    artist_a = uploader
                    if " - " in title: artist_a = title.split(" - ")[0].strip()
                    if not check_rate_limit(update.effective_user.id, limit=5, window=60):
                        await msg.edit_text("⏳ Terlalu banyak download, tunggu 1 menit ya.", reply_markup=InlineKeyboardMarkup(kb))
                        return
                    cached_a = get_cache(vid_a, quality)
                    if cached_a and cached_a.get('file_id'):
                        try:
                            cap_a = f"🎵 {title}\n👤 {artist_a} • ⏱ {format_duration(dur)}\n📦 cached • M4A 128 AAC ⚡\n✅ via @{BOT_USERNAME}"
                            sent_a = await context.bot.send_audio(chat_id=update.effective_chat.id, message_thread_id=getattr(update.effective_message, "message_thread_id", None), audio=cached_a['file_id'], caption=cap_a, title=title[:64], performer=artist_a[:64], read_timeout=300, write_timeout=300)
                            inc_cache_hit(); add_history(update.effective_user.id, vid_a, title, artist_a, quality)
                            await msg.edit_text(f"✅ Done (cached {quality}): {title}\ncek di bawah ⬇️")
                            return
                        except Exception as e:
                            log.warning(f"cache send fail auto SC {e}")
                            db_exec("DELETE FROM file_cache WHERE video_id=? AND quality=?", (vid_a, quality))
                    await msg.edit_text(f"⏳ Downloading {quality}...\n🎵 {title}\n👤 {artist_a} • ⏱ {format_duration(dur)}\nMohon tunggu 10-30 detik... ✨", reply_markup=InlineKeyboardMarkup(kb))
                    safe_a = safe_filename(f"{artist_a} - {title} [{quality}]") + f"_{uuid.uuid4().hex[:10]}"
                    loop_a = asyncio.get_event_loop()
                    def do_dl_a():
                        if quality == "m4a":
                            out_a = str(DOWNLOAD_DIR / f"{safe_a}.%(ext)s")
                            opts_a = {'format': 'bestaudio[ext=m4a]/bestaudio/best','outtmpl': out_a,'quiet': True,'noplaylist': True}
                            with yt_dlp.YoutubeDL(opts_a) as ydl_a: ydl_a.download([url_a])
                            for ext in [".m4a",".mp4"]:
                                pp = DOWNLOAD_DIR / f"{safe_a}{ext}"
                                if pp.exists(): return str(pp), None
                            files_a = sorted(DOWNLOAD_DIR.glob(f"{safe_a}*"), key=lambda x: x.stat().st_mtime, reverse=True)
                            return (str(files_a[0]) if files_a else None), None
                        else:
                            out_a = str(DOWNLOAD_DIR / f"{safe_a}.%(ext)s")
                            opts_a = {'format': 'bestaudio/best','outtmpl': out_a,'postprocessors': [{'key': 'FFmpegExtractAudio','preferredcodec':'mp3','preferredquality': quality}],'writethumbnail': True,'quiet': True,'noplaylist': True}
                            with yt_dlp.YoutubeDL(opts_a) as ydl_a: ydl_a.download([url_a])
                            base_a = DOWNLOAD_DIR / safe_a
                            mp3_a = base_a.with_suffix(".mp3")
                            if not mp3_a.exists():
                                mp3s_a = sorted(DOWNLOAD_DIR.glob("*.mp3"), key=lambda x: x.stat().st_mtime, reverse=True)
                                mp3_a = mp3s_a[0] if mp3s_a else None
                            thumb_a = None
                            for ext in [".webp",".jpg",".png"]:
                                pp = base_a.with_suffix(ext)
                                if pp.exists(): thumb_a = pp; break
                            return str(mp3_a) if mp3_a else None, str(thumb_a) if thumb_a else None
                    filepath_a, thumb_a = await loop_a.run_in_executor(None, do_dl_a)
                    if not filepath_a or not os.path.exists(filepath_a):
                        await msg.edit_text("❌ File gak ketemu setelah download.\nHubungi @aeounn", reply_markup=InlineKeyboardMarkup(kb))
                        return
                    # tagging simple
                    try:
                        lyr_a = None
                        try:
                            lyr_tmp,_,_=get_lyrics(title, artist_a)
                            if lyr_tmp: lyr_a=lyr_tmp[:4000]
                        except: pass
                        if quality=="m4a":
                            from mutagen.mp4 import MP4
                            au_a=MP4(filepath_a); au_a["\xa9nam"]=title; au_a["\xa9ART"]=artist_a; au_a["\xa9alb"]=uploader
                            if lyr_a:
                                try: au_a["\xa9lyr"]=lyr_a
                                except: pass
                            au_a.save()
                        else:
                            from mutagen.mp3 import MP3
                            from mutagen.id3 import ID3, TIT2, TPE1, TALB, USLT
                            au_a=MP3(filepath_a, ID3=ID3)
                            try: au_a.add_tags()
                            except: pass
                            if au_a.tags is None: au_a.add_tags()
                            au_a.tags.add(TIT2(encoding=3, text=title)); au_a.tags.add(TPE1(encoding=3, text=artist_a)); au_a.tags.add(TALB(encoding=3, text=uploader))
                            if lyr_a: au_a.tags.add(USLT(encoding=3, lang='eng', desc='lyrics', text=lyr_a))
                            au_a.save(v2_version=3)
                    except Exception as e: log.warning(f"tag fail auto SC {e}")
                    try:
                        cap_a = f"🎵 {title}\n👤 {artist_a} • ⏱ {format_duration(dur)}\n📦 M4A 128 AAC\n✅ via @{BOT_USERNAME}"
                        sent_a = await context.bot.send_audio(chat_id=update.effective_chat.id, message_thread_id=getattr(update.effective_message, "message_thread_id", None), audio=open(filepath_a,'rb'), caption=cap_a, title=title[:64], performer=artist_a[:64], read_timeout=300, write_timeout=300, filename=os.path.basename(filepath_a))
                        try: set_cache(vid_a, quality, sent_a.audio.file_id, sent_a.audio.file_unique_id, title, artist_a, sent_a.audio.file_size or os.path.getsize(filepath_a))
                        except: pass
                        add_history(update.effective_user.id, vid_a, title, artist_a, quality)
                        await msg.edit_text(f"✅ Done ({quality}): {title}\ncek di bawah ⬇️")
                    except Exception as e: await msg.edit_text(f"❌ Gagal kirim: {e}")
                    finally:
                        try:
                            os.remove(filepath_a)
                            if thumb_a and os.path.exists(thumb_a): os.remove(thumb_a)
                        except: pass
                except Exception as e:
                    log.warning(f"auto SC fail {e}")
                    await msg.edit_text(f"❌ Gagal auto-download: {e}")
                return
            except Exception as e:
                await msg.edit_text(f"❌ Gagal ambil info {label}: {e}\nCoba `/play <judul>` manual")
                return
        else:
            # YouTube direct video
            vid = extract_youtube_id(query_raw)
            if vid:
                msg = await update.message.reply_text(f"🔗 Link YouTube terdeteksi...\n🎵 {vid}\n⏳ Ambil info...")
                try:
                    info = await asyncio.get_event_loop().run_in_executor(None, lambda: yt_get_info(query_raw))
                    title = info.get('title','Unknown')
                    uploader = info.get('uploader','YouTube')
                    dur = info.get('duration')
                    entry = {"id": vid, "title": title, "uploader": uploader, "duration": dur, "url": query_raw, "webpage_url": query_raw}
                    context.user_data['search_results'] = [entry]
                    context.user_data['search_results_full'] = [entry]
                    context.user_data['search_query'] = title
                    context.user_data['picked_idx'] = 0
                    def_q = get_user_quality(update.effective_user.id)
                    quality = def_q
                    kb = [
                        [InlineKeyboardButton("▶️ Preview 30s", callback_data="preview_0"),
                         InlineKeyboardButton("📜 Lirik", callback_data="lyrics_0")],
                        [InlineKeyboardButton("🔀 Lagu Serupa", callback_data="related_0"),
                         InlineKeyboardButton("➕ Queue", callback_data="queue_0")],
                        [InlineKeyboardButton("❌ Batal", callback_data="cancel")],
                    ]
                    await msg.edit_text(
                        f"🎵 **Ditemukan via link:**\n{title}\n👤 {uploader} • ⏱ {format_duration(dur)}\n\n⚡ **Auto-download {quality} ✨** — tunggu 10-30 detik...",
                        parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb)
                    )
                    # opsi1 auto-download YT direct
                    try:
                        url_b = query_raw
                        vid_b = vid
                        artist_b = uploader
                        if " - " in title: artist_b = title.split(" - ")[0].strip()
                        if not check_rate_limit(update.effective_user.id, limit=5, window=60):
                            await msg.edit_text("⏳ Terlalu banyak download, tunggu 1 menit ya.", reply_markup=InlineKeyboardMarkup(kb))
                            return
                        cached_b = get_cache(vid_b, quality)
                        if cached_b and cached_b.get('file_id'):
                            try:
                                cap_b = f"🎵 {title}\n👤 {artist_b} • ⏱ {format_duration(dur)}\n📦 cached • M4A 128 AAC ⚡\n✅ via @{BOT_USERNAME}"
                                sent_b = await context.bot.send_audio(chat_id=update.effective_chat.id, message_thread_id=getattr(update.effective_message, "message_thread_id", None), audio=cached_b['file_id'], caption=cap_b, title=title[:64], performer=artist_b[:64], read_timeout=300, write_timeout=300)
                                inc_cache_hit(); add_history(update.effective_user.id, vid_b, title, artist_b, quality)
                                await msg.edit_text(f"✅ Done (cached {quality}): {title}\ncek di bawah ⬇️")
                                return
                            except Exception as e:
                                log.warning(f"cache fail YT direct {e}")
                                db_exec("DELETE FROM file_cache WHERE video_id=? AND quality=?", (vid_b, quality))
                        await msg.edit_text(f"⏳ Downloading {quality}...\n🎵 {title}\n👤 {artist_b} • ⏱ {format_duration(dur)}\nMohon tunggu 10-30 detik... ✨", reply_markup=InlineKeyboardMarkup(kb))
                        safe_b = safe_filename(f"{artist_b} - {title} [{quality}]") + f"_{uuid.uuid4().hex[:10]}"
                        loop_b = asyncio.get_event_loop()
                        def do_dl_b():
                            if quality == "m4a":
                                out_b = str(DOWNLOAD_DIR / f"{safe_b}.%(ext)s")
                                opts_b = {'format': 'bestaudio[ext=m4a]/bestaudio/best','outtmpl': out_b,'quiet': True,'noplaylist': True}
                                with yt_dlp.YoutubeDL(opts_b) as ydl_b: ydl_b.download([url_b])
                                for ext in [".m4a",".mp4"]:
                                    pp = DOWNLOAD_DIR / f"{safe_b}{ext}"
                                    if pp.exists(): return str(pp), None
                                files_b = sorted(DOWNLOAD_DIR.glob(f"{safe_b}*"), key=lambda x: x.stat().st_mtime, reverse=True)
                                return (str(files_b[0]) if files_b else None), None
                            else:
                                out_b = str(DOWNLOAD_DIR / f"{safe_b}.%(ext)s")
                                opts_b = {'format': 'bestaudio/best','outtmpl': out_b,'postprocessors': [{'key': 'FFmpegExtractAudio','preferredcodec':'mp3','preferredquality': quality}],'writethumbnail': True,'quiet': True,'noplaylist': True}
                                with yt_dlp.YoutubeDL(opts_b) as ydl_b: ydl_b.download([url_b])
                                base_b = DOWNLOAD_DIR / safe_b
                                mp3_b = base_b.with_suffix(".mp3")
                                if not mp3_b.exists():
                                    mp3s_b = sorted(DOWNLOAD_DIR.glob("*.mp3"), key=lambda x: x.stat().st_mtime, reverse=True)
                                    mp3_b = mp3s_b[0] if mp3s_b else None
                                thumb_b = None
                                for ext in [".webp",".jpg",".png"]:
                                    pp = base_b.with_suffix(ext)
                                    if pp.exists(): thumb_b = pp; break
                                return str(mp3_b) if mp3_b else None, str(thumb_b) if thumb_b else None
                        filepath_b, thumb_b = await loop_b.run_in_executor(None, do_dl_b)
                        if not filepath_b or not os.path.exists(filepath_b):
                            await msg.edit_text("❌ File gak ketemu setelah download.\nHubungi @aeounn", reply_markup=InlineKeyboardMarkup(kb))
                            return
                        try:
                            lyr_b=None
                            try:
                                lyr_tmp,_,_=get_lyrics(title, artist_b)
                                if lyr_tmp: lyr_b=lyr_tmp[:4000]
                            except: pass
                            if quality=="m4a":
                                from mutagen.mp4 import MP4
                                au_b=MP4(filepath_b); au_b["\xa9nam"]=title; au_b["\xa9ART"]=artist_b; au_b["\xa9alb"]=uploader
                                if lyr_b:
                                    try: au_b["\xa9lyr"]=lyr_b
                                    except: pass
                                au_b.save()
                            else:
                                from mutagen.mp3 import MP3
                                from mutagen.id3 import ID3, TIT2, TPE1, TALB, USLT
                                au_b=MP3(filepath_b, ID3=ID3)
                                try: au_b.add_tags()
                                except: pass
                                if au_b.tags is None: au_b.add_tags()
                                au_b.tags.add(TIT2(encoding=3, text=title)); au_b.tags.add(TPE1(encoding=3, text=artist_b)); au_b.tags.add(TALB(encoding=3, text=uploader))
                                if lyr_b: au_b.tags.add(USLT(encoding=3, lang='eng', desc='lyrics', text=lyr_b))
                                au_b.save(v2_version=3)
                        except Exception as e: log.warning(f"tag fail YT direct {e}")
                        try:
                            cap_b = f"🎵 {title}\n👤 {artist_b} • ⏱ {format_duration(dur)}\n📦 M4A 128 AAC\n✅ via @{BOT_USERNAME}"
                            sent_b = await context.bot.send_audio(chat_id=update.effective_chat.id, message_thread_id=getattr(update.effective_message, "message_thread_id", None), audio=open(filepath_b,'rb'), caption=cap_b, title=title[:64], performer=artist_b[:64], read_timeout=300, write_timeout=300, filename=os.path.basename(filepath_b))
                            try: set_cache(vid_b, quality, sent_b.audio.file_id, sent_b.audio.file_unique_id, title, artist_b, sent_b.audio.file_size or os.path.getsize(filepath_b))
                            except: pass
                            add_history(update.effective_user.id, vid_b, title, artist_b, quality)
                            await msg.edit_text(f"✅ Done ({quality}): {title}\ncek di bawah ⬇️")
                        except Exception as e: await msg.edit_text(f"❌ Gagal kirim: {e}")
                        finally:
                            try:
                                os.remove(filepath_b)
                                if thumb_b and os.path.exists(thumb_b): os.remove(thumb_b)
                            except: pass
                    except Exception as e:
                        log.warning(f"auto YT direct fail {e}")
                        await msg.edit_text(f"❌ Gagal auto-download: {e}")
                    return
                except Exception as e:
                    await msg.edit_text(f"❌ Gagal ambil info link: {e}\nCoba `/play <judul>` manual")
                    return
            else:
                msg = await update.message.reply_text(f"🔗 Link terdeteksi, coba ambil info...")
                try:
                    info = await asyncio.get_event_loop().run_in_executor(None, lambda: yt_get_info(query_raw))
                    vid = info.get('id') or "unknown"
                    title = info.get('title','Unknown')
                    uploader = info.get('uploader','YouTube')
                    dur = info.get('duration')
                    entry = {"id": vid, "title": title, "uploader": uploader, "duration": dur, "url": query_raw}
                    context.user_data['search_results'] = [entry]
                    context.user_data['search_results_full'] = [entry]
                    context.user_data['search_query'] = title
                    context.user_data['picked_idx'] = 0
                    def_q = get_user_quality(update.effective_user.id)
                    quality = def_q
                    kb = [
                        [InlineKeyboardButton("▶️ Preview", callback_data="preview_0"), InlineKeyboardButton("📜 Lirik", callback_data="lyrics_0")],
                        [InlineKeyboardButton("🔀 Lagu Serupa", callback_data="related_0"), InlineKeyboardButton("➕ Queue", callback_data="queue_0")],
                        [InlineKeyboardButton("❌ Batal", callback_data="cancel")],
                    ]
                    await msg.edit_text(f"🎵 {title}\n👤 {uploader} • ⏱ {format_duration(dur)}\n⚡ Auto-download {quality} ✨ — tunggu 10-30 detik...", reply_markup=InlineKeyboardMarkup(kb))
                    # opsi1 auto generic
                    try:
                        url_c = query_raw
                        vid_c = vid
                        artist_c = uploader
                        if " - " in title: artist_c = title.split(" - ")[0].strip()
                        if not check_rate_limit(update.effective_user.id, limit=5, window=60):
                            await msg.edit_text("⏳ Terlalu banyak download, tunggu 1 menit ya.", reply_markup=InlineKeyboardMarkup(kb))
                            return
                        cached_c = get_cache(vid_c, quality)
                        if cached_c and cached_c.get('file_id'):
                            try:
                                cap_c = f"🎵 {title}\n👤 {artist_c} • ⏱ {format_duration(dur)}\n📦 cached • M4A 128 AAC ⚡\n✅ via @{BOT_USERNAME}"
                                sent_c = await context.bot.send_audio(chat_id=update.effective_chat.id, message_thread_id=getattr(update.effective_message, "message_thread_id", None), audio=cached_c['file_id'], caption=cap_c, title=title[:64], performer=artist_c[:64], read_timeout=300, write_timeout=300)
                                inc_cache_hit(); add_history(update.effective_user.id, vid_c, title, artist_c, quality)
                                await msg.edit_text(f"✅ Done (cached {quality}): {title}\ncek di bawah ⬇️")
                                return
                            except Exception as e:
                                log.warning(f"cache fail generic {e}")
                                db_exec("DELETE FROM file_cache WHERE video_id=? AND quality=?", (vid_c, quality))
                        await msg.edit_text(f"⏳ Downloading {quality}...\n🎵 {title}\n👤 {artist_c} • ⏱ {format_duration(dur)}\nMohon tunggu 10-30 detik... ✨", reply_markup=InlineKeyboardMarkup(kb))
                        safe_c = safe_filename(f"{artist_c} - {title} [{quality}]") + f"_{uuid.uuid4().hex[:10]}"
                        loop_c = asyncio.get_event_loop()
                        def do_dl_c():
                            if quality == "m4a":
                                out_c = str(DOWNLOAD_DIR / f"{safe_c}.%(ext)s")
                                opts_c = {'format': 'bestaudio[ext=m4a]/bestaudio/best','outtmpl': out_c,'quiet': True,'noplaylist': True}
                                with yt_dlp.YoutubeDL(opts_c) as ydl_c: ydl_c.download([url_c])
                                for ext in [".m4a",".mp4"]:
                                    pp = DOWNLOAD_DIR / f"{safe_c}{ext}"
                                    if pp.exists(): return str(pp), None
                                files_c = sorted(DOWNLOAD_DIR.glob(f"{safe_c}*"), key=lambda x: x.stat().st_mtime, reverse=True)
                                return (str(files_c[0]) if files_c else None), None
                            else:
                                out_c = str(DOWNLOAD_DIR / f"{safe_c}.%(ext)s")
                                opts_c = {'format': 'bestaudio/best','outtmpl': out_c,'postprocessors': [{'key': 'FFmpegExtractAudio','preferredcodec':'mp3','preferredquality': quality}],'writethumbnail': True,'quiet': True,'noplaylist': True}
                                with yt_dlp.YoutubeDL(opts_c) as ydl_c: ydl_c.download([url_c])
                                base_c = DOWNLOAD_DIR / safe_c
                                mp3_c = base_c.with_suffix(".mp3")
                                if not mp3_c.exists():
                                    mp3s_c = sorted(DOWNLOAD_DIR.glob("*.mp3"), key=lambda x: x.stat().st_mtime, reverse=True)
                                    mp3_c = mp3s_c[0] if mp3s_c else None
                                thumb_c = None
                                for ext in [".webp",".jpg",".png"]:
                                    pp = base_c.with_suffix(ext)
                                    if pp.exists(): thumb_c = pp; break
                                return str(mp3_c) if mp3_c else None, str(thumb_c) if thumb_c else None
                        filepath_c, thumb_c = await loop_c.run_in_executor(None, do_dl_c)
                        if not filepath_c or not os.path.exists(filepath_c):
                            await msg.edit_text("❌ File gak ketemu setelah download.\nHubungi @aeounn", reply_markup=InlineKeyboardMarkup(kb))
                            return
                        try:
                            lyr_c=None
                            try:
                                lyr_tmp,_,_=get_lyrics(title, artist_c)
                                if lyr_tmp: lyr_c=lyr_tmp[:4000]
                            except: pass
                            if quality=="m4a":
                                from mutagen.mp4 import MP4
                                au_c=MP4(filepath_c); au_c["\xa9nam"]=title; au_c["\xa9ART"]=artist_c; au_c["\xa9alb"]=uploader
                                if lyr_c:
                                    try: au_c["\xa9lyr"]=lyr_c
                                    except: pass
                                au_c.save()
                            else:
                                from mutagen.mp3 import MP3
                                from mutagen.id3 import ID3, TIT2, TPE1, TALB, USLT
                                au_c=MP3(filepath_c, ID3=ID3)
                                try: au_c.add_tags()
                                except: pass
                                if au_c.tags is None: au_c.add_tags()
                                au_c.tags.add(TIT2(encoding=3, text=title)); au_c.tags.add(TPE1(encoding=3, text=artist_c)); au_c.tags.add(TALB(encoding=3, text=uploader))
                                if lyr_c: au_c.tags.add(USLT(encoding=3, lang='eng', desc='lyrics', text=lyr_c))
                                au_c.save(v2_version=3)
                        except Exception as e: log.warning(f"tag fail generic {e}")
                        try:
                            cap_c = f"🎵 {title}\n👤 {artist_c} • ⏱ {format_duration(dur)}\n📦 M4A 128 AAC\n✅ via @{BOT_USERNAME}"
                            sent_c = await context.bot.send_audio(chat_id=update.effective_chat.id, message_thread_id=getattr(update.effective_message, "message_thread_id", None), audio=open(filepath_c,'rb'), caption=cap_c, title=title[:64], performer=artist_c[:64], read_timeout=300, write_timeout=300, filename=os.path.basename(filepath_c))
                            try: set_cache(vid_c, quality, sent_c.audio.file_id, sent_c.audio.file_unique_id, title, artist_c, sent_c.audio.file_size or os.path.getsize(filepath_c))
                            except: pass
                            add_history(update.effective_user.id, vid_c, title, artist_c, quality)
                            await msg.edit_text(f"✅ Done ({quality}): {title}\ncek di bawah ⬇️")
                        except Exception as e: await msg.edit_text(f"❌ Gagal kirim: {e}")
                        finally:
                            try:
                                os.remove(filepath_c)
                                if thumb_c and os.path.exists(thumb_c): os.remove(thumb_c)
                            except: pass
                    except Exception as e:
                        log.warning(f"auto generic fail {e}")
                        await msg.edit_text(f"❌ Gagal auto-download: {e}")
                    return
                except Exception as e:
                    await msg.edit_text(f"❌ Gagal proses link: {e}")
                    return
        # if spotify resolved, query_raw now is title, continue to normal search

    # NORMAL SEARCH (v6 pagination: 20 results, 5 per page)
    query = query_raw  # after spotify resolve
    msg = await update.message.reply_text(f"🔍 Nyari lagu `{query}`...", parse_mode="Markdown")
    def search_filtered():
        ydl_opts = {'quiet': True, 'extract_flat': True, 'skip_download': True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            r = ydl.extract_info(f"ytsearch20:{query}", download=False)
            entries = r.get('entries', [])
            # filtering similar to v5 but keep up to 20
            low_q = query.lower()
            block_keywords = ["remix", "mashup", "playlist", "compilation", "nonstop", "full album", "1 hour", "1 jam", " mix", "mash up"]
            filtered = []
            for e in entries:
                title = (e.get('title') or "").lower()
                dur = e.get('duration') or 0
                if dur and dur > 600:
                    if "mix" not in low_q and "hour" not in low_q and "nonstop" not in low_q:
                        continue
                blocked = False
                for kw in block_keywords:
                    if kw.strip() in title and kw.strip() not in low_q:
                        blocked = True
                        break
                if not blocked and "dj" in low_q:
                    pass
                elif not blocked and (" dj " in f" {title} " or title.startswith("dj ") or "dj remix" in title or "dj tiktok" in title):
                    if "dj" not in low_q:
                        blocked = True
                if blocked:
                    continue
                filtered.append(e)
                if len(filtered) >= 20:
                    break
            if len(filtered) < 5:
                return entries[:20]
            return filtered[:20]
    try:
        entries = await asyncio.get_event_loop().run_in_executor(None, search_filtered)
    except Exception as e:
        await msg.edit_text(f"❌ Gagal search: {e}\nCoba lagi atau hubungi @aeounn")
        return
    if not entries:
        await msg.edit_text("❌ Gak ketemu, coba kata kunci lain.")
        return
    # v6: store full 20, show page 0 with 5
    context.user_data['search_results'] = entries  # full for pick index compatibility (global index)
    context.user_data['search_results_full'] = entries
    context.user_data['search_query'] = query
    context.user_data['search_page'] = 0
    kb = build_search_keyboard(entries, 0)
    await msg.edit_text(
        f"🎶 **Hasil untuk:** `{query}`\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Ditemukan {len(entries)} lagu — halaman 1/{(len(entries)+4)//5} — pilih untuk lanjut pilih kualitas:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def artist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        context.user_data['awaiting_artist'] = True
        await update.message.reply_text("What artist are you looking for?")
        return
    if not check_rate_limit(update.effective_user.id, limit=5, window=30):
        await update.message.reply_text("⏳ Slow down 30 detik ya (5 req / 30s)")
        return
    artist_name = " ".join(context.args)
    msg = await update.message.reply_text(f"👤 Nyari 25 lagu dari **{artist_name}**...", parse_mode="Markdown")
    def search():
        opts = {'quiet': True, 'extract_flat': True, 'skip_download': True}
        with yt_dlp.YoutubeDL(opts) as ydl:
            r = ydl.extract_info(f"ytsearch50:{artist_name} official audio", download=False)
            entries = r.get('entries', [])
            low = artist_name.lower()
            filtered = []
            for e in entries:
                title = (e.get('title') or "").lower()
                uploader = (e.get('uploader') or "").lower()
                channel = (e.get('channel') or "").lower()
                if low in title or low in uploader or low in channel:
                    filtered.append(e)
            if len(filtered) >= 8:
                return filtered[:25]
            first_word = low.split()[0]
            if len(first_word) >= 3:
                loose = [e for e in entries if first_word in (e.get('title') or "").lower() or first_word in (e.get('uploader') or "").lower()]
                if len(loose) >= 8:
                    return loose[:25]
            return entries[:25]
    try:
        entries = await asyncio.get_event_loop().run_in_executor(None, search)
    except Exception as e:
        await msg.edit_text(f"❌ Gagal cari artis: {e}")
        return
    if not entries:
        await msg.edit_text(f"❌ Gak ketemu lagu dari `{artist_name}`")
        return
    context.user_data['search_results'] = entries
    context.user_data['artist_name'] = artist_name
    context.user_data['artist_page'] = 0
    await show_artist_page(msg, context, 0, edit=True)

def build_artist_keyboard(entries, page):
    PER_PAGE = 4
    total = len(entries)
    total_pages = (total + PER_PAGE - 1) // PER_PAGE
    start = page * PER_PAGE
    end = min(start + PER_PAGE, total)
    kb = []
    for i in range(start, end):
        e = entries[i]
        title = e.get('title','Unknown')[:40]
        dur = format_duration(e.get('duration'))
        kb.append([InlineKeyboardButton(f"{i+1}. {title} ⏱ {dur}", callback_data=f"pick_{i}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Back", callback_data=f"artist_page_{page-1}"))
    nav.append(InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"artist_page_{page+1}"))
    if nav:
        kb.append(nav)
    kb.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel")])
    return kb

async def show_artist_page(msg_or_query, context, page, edit=False):
    entries = context.user_data.get('search_results', [])
    artist_name = context.user_data.get('artist_name', 'artis')
    total = len(entries)
    total_pages = (total + 3) // 4
    kb = build_artist_keyboard(entries, page)
    text = f"🎵 **MUSIC** | `{artist_name}`\n━━━━━━━━━━━━━━━━━━\n📄 Halaman {page+1}/{total_pages} — pilih lagu:"
    if edit:
        await msg_or_query.edit_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
    else:
        await msg_or_query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    if data == "help_play":
        await q.edit_message_text("Ketik `/play <judul>`\nContoh: `/play tak ingin usai`", parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu", callback_data="back_start")]]))
    elif data == "help_artist":
        await q.edit_message_text("Ketik `/artist <nama>`\nContoh: `/artist bernadya`", parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu", callback_data="back_start")]]))
    elif data == "help_lyrics":
        await q.edit_message_text("Ketik `/lyrics <judul>`\nContoh: `/lyrics tak ingin usai - keisya`", parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu", callback_data="back_start")]]))
    elif data == "help_shazam":
        await q.edit_message_text("🔍 **Shazam** — kirim voice note, audio, video, atau file musik\nBot akan coba kenali lagunya lalu kasih tombol download.\n\nKalau gagal, coba potongan 10-15 detik yang jelas.", parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu", callback_data="back_start")]]))
    elif data == "help_general":
        text = (
            "❓ **BANTUAN v6**\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "`/play <judul/link>` `/artist <nama>`\n"
            "`/lyrics <judul>` `/history` `/favorites` `/stats`\n"
            "`/settings` `/queue`\n"
            "Shazam: kirim voice/audio/video\n"
            "Bug? @aeounn"
        )
        await q.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu", callback_data="back_start")]]))

    elif data == "show_history":
        rows = get_history(q.from_user.id, 10)
        if not rows:
            await q.edit_message_text("🕘 Belum ada history. Coba `/play` dulu ya!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu", callback_data="back_start")]]))
        else:
            text = "🕘 **History kamu (10 terakhir):**\n━━━━━━━━━━━━━━━━━━\n"
            for i, r in enumerate(rows,1):
                text += f"{i}. {r['title'][:30]} [{r['quality']}] • {r['created_at'][:10]}\n"
            await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu", callback_data="back_start")]]))
    elif data == "show_favs":
        rows = get_favorites(q.from_user.id)
        if not rows:
            await q.edit_message_text("⭐ Belum ada favorit.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu", callback_data="back_start")]]))
        else:
            text = "⭐ **Favorit kamu:**\n━━━━━━━━━━━━━━━━━━\n"
            kb = []
            for i, r in enumerate(rows[:8]):
                text += f"{i+1}. {r['title'][:30]}\n"
                kb.append([InlineKeyboardButton(f"▶️ {r['title'][:25]}", callback_data=f"favplay_{r['video_id']}"),
                           InlineKeyboardButton("❌", callback_data=f"favdel_{r['video_id']}")])
            kb.append([InlineKeyboardButton("🏠 Menu", callback_data="back_start")])
            await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))
    elif data == "show_stats":
        s = get_stats()
        text = (
            f"📊 **Statistik v6**\n"
            f"📥 DL: {s.get('total_downloads',0)} | ⚡ Cache: {s.get('cache_hits',0)}\n"
            f"👥 Users: {s.get('total_users',0)} | ⭐ Favs: {s.get('total_favs',0)}\n"
            f"💾 Cached: {s.get('cache_size',0)}"
        )
        await q.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu", callback_data="back_start")]]))
    elif data == "show_settings":
        def_q = get_user_quality(q.from_user.id)
        kb = [
            [InlineKeyboardButton(f"{'✅ ' if def_q=='192' else ''}MP3 192", callback_data="set_quality_192"),
             InlineKeyboardButton(f"{'✅ ' if def_q=='320' else ''}MP3 320", callback_data="set_quality_320")],
            [InlineKeyboardButton(f"{'✅ ' if def_q=='m4a' else ''}M4A 128 ✨", callback_data="set_quality_m4a")],
            [InlineKeyboardButton("🏠 Menu", callback_data="back_start")]
        ]
        await q.edit_message_text(f"⚙️ Settings — default kamu: **{def_q}**\nPilih kualitas:", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
    elif data == "show_queue":
        rows = get_queue(q.from_user.id)
        if not rows:
            await q.edit_message_text("📋 Antrian kosong. Tambah via ➕ Queue di picker lagu.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu", callback_data="back_start")]]))
        else:
            text = f"📋 **Antrian ({len(rows)}):**\n"
            for r in rows:
                text += f"{r['position']}. {r['title'][:30]} [{r['quality']}]\n"
            kb = [
                [InlineKeyboardButton("▶️ Play Next", callback_data="queue_play_next"), InlineKeyboardButton("🗑️ Clear", callback_data="queue_clear")],
                [InlineKeyboardButton("🏠 Menu", callback_data="back_start")]
            ]
            await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))
    elif data == "back_start":
        kb = [
            [InlineKeyboardButton("🎵 Cari Lagu", callback_data="help_play"),
             InlineKeyboardButton("👤 Cari Artis", callback_data="help_artist")],
            [InlineKeyboardButton("📜 Lirik", callback_data="help_lyrics"),
             InlineKeyboardButton("📊 Stats", callback_data="show_stats")],
            [InlineKeyboardButton("🕘 History", callback_data="show_history"),
             InlineKeyboardButton("⭐ Favorites", callback_data="show_favs")],
            [InlineKeyboardButton("⚙️ Settings", callback_data="show_settings"),
             InlineKeyboardButton("📋 Queue", callback_data="show_queue")],
            [InlineKeyboardButton("❓ Help", callback_data="help_general"),
             InlineKeyboardButton("🔍 Shazam", callback_data="help_shazam")],
            [InlineKeyboardButton("👨‍💻 Owner", url="https://t.me/aeounn")],
        ]
        text = "🎶 **Renn Music Bot v6**\nKetik `/play <judul>` `/artist <nama>` atau kirim voice untuk Shazam!\nInline: `@mypersonalbotmusic_bot judul` di chat mana pun!"
        try:
            await q.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
        except:
            pass

# ===== Preview & Lyrics & Fav handlers =====
async def preview_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    # handled in pick_and_dl_handler now? but we have separate pattern preview_
    # This is legacy, redirect
    await pick_and_dl_handler(update, context)

# ===== Inline Query =====
async def inline_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.inline_query.query.strip()
    if not query:
        await update.inline_query.answer([], cache_time=30)
        return
    # search
    try:
        # limit 5 inline
        entries = await asyncio.get_event_loop().run_in_executor(None, lambda: yt_search(query, 5))
    except Exception as e:
        log.warning(f"inline search fail {e}")
        entries = []
    results = []
    for i, e in enumerate(entries):
        title = e.get('title','Unknown')
        dur = format_duration(e.get('duration'))
        vid = e.get('id')
        url = e.get('url') or f"https://www.youtube.com/watch?v={vid}"
        desc = f"⏱ {dur} • 👤 {e.get('uploader','YouTube')[:20]}"
        results.append(InlineQueryResultArticle(
            id=f"{vid}_{i}",
            title=title[:60],
            description=desc,
            input_message_content=InputTextMessageContent(
                f"🎵 **{title}**\n👤 {e.get('uploader','YouTube')} • ⏱ {dur}\n🔗 {url}\n\n📥 Download: buka @{BOT_USERNAME} → /play {title[:30]}"
            ),
            thumbnail_url=e.get('thumbnail') or (e.get('thumbnails', [{}])[0].get('url') if e.get('thumbnails') else None),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📥 Download MP3 di Bot", url=f"https://t.me/{BOT_USERNAME}?start=vid_{vid}")],
                [InlineKeyboardButton("▶️ Buka YouTube", url=url)]
            ])
        ))
    await update.inline_query.answer(results, cache_time=10, is_personal=True)

async def handle_awaiting_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # VKM style: after /play or /artist, next text is treated as query
    text = (update.message.text or "").strip()
    if not text:
        return
    if text.startswith('/'):
        return
    if context.user_data.get('awaiting_play'):
        context.user_data.pop('awaiting_play', None)
        context.user_data.pop('awaiting_play_msg', None)
        # also clear artist flag if any
        context.user_data.pop('awaiting_artist', None)
        context.args = text.split()
        await play(update, context)
        return
    if context.user_data.get('awaiting_artist'):
        context.user_data.pop('awaiting_artist', None)
        context.user_data.pop('awaiting_play', None)
        context.user_data.pop('awaiting_play_msg', None)
        context.args = text.split()
        await artist(update, context)
        return

# ===== Main Pick & Download =====
async def pick_and_dl_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    if data == "cancel":
        context.user_data.pop('awaiting_play', None)
        context.user_data.pop('awaiting_artist', None)
        context.user_data.pop('awaiting_play_msg', None)
        await q.edit_message_text("❌ Dibatalkan. Ketik /play atau /artist lagi untuk cari.")
        return
    if data == "noop":
        return
    # v6: settings quality
    if data.startswith("set_quality_"):
        quality = data.split("_")[-1]
        set_user_quality(q.from_user.id, quality)
        kb = [
            [InlineKeyboardButton(f"{'✅ ' if quality=='192' else ''}MP3 192kbps (hemat)", callback_data="set_quality_192"),
             InlineKeyboardButton(f"{'✅ ' if quality=='320' else ''}MP3 320kbps (gede)", callback_data="set_quality_320")],
            [InlineKeyboardButton(f"{'✅ ' if quality=='m4a' else ''}M4A 128kbps AAC asli ✨", callback_data="set_quality_m4a")],
            [InlineKeyboardButton("❌ Tutup", callback_data="cancel")],
        ]
        await q.edit_message_text(f"✅ Default kualitas di-set ke **{quality}**\nSelanjutnya picker akan tandai ✅ di opsi ini, tapi kamu tetap bisa pilih kualitas lain.", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
        return
    # v6: search pagination
    if data.startswith("search_page_"):
        try:
            page = int(data.split("_")[-1])
            query = context.user_data.get('search_query', 'hasil')
            context.user_data['search_page'] = page
            await show_search_page(query, q, context, page, edit=True)
        except Exception as e:
            await q.answer(f"err {e}")
        return
    # v6: related
    if data.startswith("related_"):
        idx = int(data.split("_")[1])
        entries = context.user_data.get('search_results', []) or context.user_data.get('search_results_full', [])
        if idx >= len(entries):
            await q.edit_message_text("❌ Data expired.")
            return
        video = entries[idx]
        title = video.get('title','Unknown')
        await q.edit_message_text(f"🔀 Nyari lagu serupa dengan:\n{title}\n⏳...")
        try:
            loop = asyncio.get_event_loop()
            related = await loop.run_in_executor(None, lambda: yt_get_related(title, 5))
        except Exception as e:
            await q.edit_message_text(f"❌ Gagal related: {e}")
            return
        if not related:
            await q.edit_message_text("❌ Gak ada lagu serupa ketemu.")
            return
        # store related as new search_results
        context.user_data['search_results'] = related
        context.user_data['search_results_full'] = related
        context.user_data['search_query'] = f"Related: {title[:30]}"
        kb = []
        for i, e in enumerate(related):
            t = e.get('title','Unknown')[:40]
            dur = format_duration(e.get('duration'))
            kb.append([InlineKeyboardButton(f"{i+1}. {t} ⏱ {dur}", callback_data=f"pick_{i}")])
        kb.append([InlineKeyboardButton("⬅️ Balik", callback_data=f"pick_{idx}"), InlineKeyboardButton("❌ Batal", callback_data="cancel")])
        await q.edit_message_text(f"🔀 **Lagu Serupa — {title[:35]}**\n━━━━━━━━━━━━━━━━━━\nPilih lagu:", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
        return
    # v6: queue add
    if data.startswith("queue_"):
        # queue_{idx} or queue_play_next / queue_clear / queue_download_all
        if data == "queue_play_next":
            item = pop_queue_next(q.from_user.id)
            if not item:
                await q.edit_message_text("📋 Antrian kosong.")
                return
            # need to trigger download? we can show pick for that item
            vid = item['video_id']
            entry = {"id": vid, "title": item['title'], "uploader": item['artist'], "duration": 0, "url": f"https://www.youtube.com/watch?v={vid}"}
            context.user_data['search_results'] = [entry]
            context.user_data['picked_idx'] = 0
            # simulate dl with stored quality
            quality = item.get('quality','m4a')
            # directly call dl logic? instead set picked and prompt quality, but we have quality already - auto download next?
            # For now, show quality picker with that quality highlighted and auto hint
            def_q = quality
            # opsi1: langsung auto-download tanpa pilih kualitas
            kb = [
                [InlineKeyboardButton("❌ Batal", callback_data="cancel")],
            ]
            await q.edit_message_text(f"▶️ Next Queue: {item['title']} [{quality}]\n⚡ Auto-download {quality} — tunggu 10-30 detik...", reply_markup=InlineKeyboardMarkup(kb))
            # trigger auto download for queue item
            try:
                url_q = f"https://www.youtube.com/watch?v={vid}"
                title_q = item['title']
                artist_q = item['artist']
                dur_q = "0:00"
                vid_q = vid
                if not check_rate_limit(q.from_user.id, limit=5, window=60):
                    await q.edit_message_text("⏳ Terlalu banyak download, tunggu 1 menit ya.", reply_markup=InlineKeyboardMarkup(kb))
                    return
                cached_q = get_cache(vid_q, quality)
                if cached_q and cached_q.get('file_id'):
                    try:
                        cap_q = f"🎵 {title_q}\n👤 {artist_q} • ⏱ {dur_q}\n📦 cached • M4A 128 AAC ⚡\n✅ via @{BOT_USERNAME}"
                        sent_q = await context.bot.send_audio(chat_id=q.message.chat_id, message_thread_id=getattr(q.message, "message_thread_id", None), audio=cached_q['file_id'], caption=cap_q, title=title_q[:64], performer=artist_q[:64], read_timeout=300, write_timeout=300)
                        inc_cache_hit(); add_history(q.from_user.id, vid_q, title_q, artist_q, quality)
                        await q.edit_message_text(f"✅ Done Queue (cached {quality}): {title_q}\ncek di bawah ⬇️")
                        return
                    except Exception as e:
                        log.warning(f"queue cache fail {e}")
                        db_exec("DELETE FROM file_cache WHERE video_id=? AND quality=?", (vid_q, quality))
                await q.edit_message_text(f"⏳ Downloading Queue {quality}...\n🎵 {title_q}\nMohon tunggu 10-30 detik... ✨", reply_markup=InlineKeyboardMarkup(kb))
                safe_q = safe_filename(f"{artist_q} - {title_q} [{quality}]") + f"_{uuid.uuid4().hex[:10]}"
                loop_q = asyncio.get_event_loop()
                def do_dl_q():
                    out_q = str(DOWNLOAD_DIR / f"{safe_q}.%(ext)s")
                    opts_q = {'format': 'bestaudio[ext=m4a]/bestaudio/best','outtmpl': out_q,'quiet': True,'noplaylist': True} if quality=="m4a" else {'format': 'bestaudio/best','outtmpl': out_q,'postprocessors': [{'key': 'FFmpegExtractAudio','preferredcodec':'mp3','preferredquality': quality}],'writethumbnail': True,'quiet': True,'noplaylist': True}
                    with yt_dlp.YoutubeDL(opts_q) as ydl_q: ydl_q.download([url_q])
                    if quality=="m4a":
                        for ext in [".m4a",".mp4"]:
                            pp = DOWNLOAD_DIR / f"{safe_q}{ext}"
                            if pp.exists(): return str(pp), None
                        files_q = sorted(DOWNLOAD_DIR.glob(f"{safe_q}*"), key=lambda x: x.stat().st_mtime, reverse=True)
                        return (str(files_q[0]) if files_q else None), None
                    else:
                        base_q = DOWNLOAD_DIR / safe_q
                        mp3_q = base_q.with_suffix(".mp3")
                        if not mp3_q.exists():
                            mp3s_q = sorted(DOWNLOAD_DIR.glob("*.mp3"), key=lambda x: x.stat().st_mtime, reverse=True)
                            mp3_q = mp3s_q[0] if mp3s_q else None
                        thumb_q=None
                        for ext in [".webp",".jpg",".png"]:
                            pp = base_q.with_suffix(ext)
                            if pp.exists(): thumb_q=pp; break
                        return str(mp3_q) if mp3_q else None, str(thumb_q) if thumb_q else None
                filepath_q, thumb_q = await loop_q.run_in_executor(None, do_dl_q)
                if not filepath_q or not os.path.exists(filepath_q):
                    await q.edit_message_text("❌ File gak ketemu setelah download queue.", reply_markup=InlineKeyboardMarkup(kb))
                    return
                cap_q = f"🎵 {title_q}\n👤 {artist_q} • ⏱ {dur_q}\n📦 M4A 128 AAC\n✅ via @{BOT_USERNAME}"
                sent_q = await context.bot.send_audio(chat_id=q.message.chat_id, message_thread_id=getattr(q.message, "message_thread_id", None), audio=open(filepath_q,'rb'), caption=cap_q, title=title_q[:64], performer=artist_q[:64], read_timeout=300, write_timeout=300, filename=os.path.basename(filepath_q))
                try: set_cache(vid_q, quality, sent_q.audio.file_id, sent_q.audio.file_unique_id, title_q, artist_q, sent_q.audio.file_size or os.path.getsize(filepath_q))
                except: pass
                add_history(q.from_user.id, vid_q, title_q, artist_q, quality)
                await q.edit_message_text(f"✅ Done Queue ({quality}): {title_q}\ncek di bawah ⬇️")
                try: os.remove(filepath_q)
                except: pass
                if thumb_q and os.path.exists(thumb_q):
                    try: os.remove(thumb_q)
                    except: pass
            except Exception as e:
                log.warning(f"queue auto fail {e}")
                await q.edit_message_text(f"❌ Queue gagal: {e}")
            return
        elif data == "queue_clear":
            clear_queue(q.from_user.id)
            await q.edit_message_text("🗑️ Antrian dikosongkan.")
            return
        elif data == "queue_download_all":
            rows = get_queue(q.from_user.id)
            if not rows:
                await q.edit_message_text("📋 Antrian kosong.")
                return
            await q.edit_message_text(f"📋 Download semua {len(rows)} lagu antrian?\nMasing-masing akan dikirim satu per satu (bisa agak lama).\n\nKlik ▶️ Play Next untuk download satu-per-satu yang disarankan, atau tunggu fitur batch.")
            return
        else:
            # queue_{idx}
            try:
                idx = int(data.split("_")[1])
            except:
                return
            entries = context.user_data.get('search_results', []) or context.user_data.get('search_results_full', [])
            if idx >= len(entries):
                await q.edit_message_text("❌ Data expired.")
                return
            video = entries[idx]
            vid = video.get('id')
            title = video.get('title','Unknown')
            uploader = video.get('uploader','YouTube')
            artist = uploader
            if " - " in title:
                artist = title.split(" - ")[0].strip()
            # default quality from settings
            def_q = get_user_quality(q.from_user.id)
            ok, info = add_to_queue(q.from_user.id, vid, title, artist, def_q)
            if ok:
                await q.answer(f"✅ Ditambah ke antrian #{info} ({def_q})", show_alert=False)
                # update keyboard to show queued
                try:
                    await q.edit_message_reply_markup(reply_markup=q.message.reply_markup)
                except: pass
            else:
                await q.answer(f"❌ {info}", show_alert=True)
            return
    # v6: translate lyrics
    if data.startswith("tr_lyrics_"):
        # tr_lyrics_{idx}_id or _en
        try:
            parts = data.split("_")
            # tr lyrics idx lang
            idx = int(parts[2])
            target = parts[3]  # id or en
            entries = context.user_data.get('search_results', []) or context.user_data.get('search_results_full', [])
            if idx >= len(entries):
                await q.edit_message_text("❌ Data expired.")
                return
            video = entries[idx]
            title = video.get('title','Unknown')
            uploader = video.get('uploader','')
            artist = uploader
            if " - " in title:
                artist = title.split(" - ")[0].strip()
            await q.edit_message_text(f"📜 Nyari lirik untuk translate...\n{title} → {target.upper()} ⏳")
            def do_lyr():
                lyr, t, a = get_lyrics(title, artist)
                return lyr, t, a
            loop = asyncio.get_event_loop()
            lyr, t, a = await loop.run_in_executor(None, do_lyr)
            if not lyr:
                await q.edit_message_text(f"❌ Lirik gak ketemu untuk translate: {title}")
                return
            # translate
            await q.edit_message_text(f"🌐 Translating {len(lyr)} char ke {target.upper()}... ⏳ (MyMemory, 2-5 detik)")
            translated = await loop.run_in_executor(None, lambda: translate_text_chunks(lyr, target))
            header = f"🌐 **Terjemahan {target.upper()} — {t or title}**\n━━━━━━━━━━━━━━━━━━\n"
            # store original for back button
            context.user_data[f'lyrics_{idx}'] = lyr
            context.user_data[f'lyrics_tr_{idx}_{target}'] = translated
            full = header + translated
            if len(full) > 4000:
                full = full[:4000] + "\n... (kepotong)"
            kb = [
                [InlineKeyboardButton("📜 Lirik Asli", callback_data=f"lyrics_{idx}")],
                [InlineKeyboardButton("⬅️ Balik ke Kualitas", callback_data=f"pick_{idx}")],
            ]
            if target == "id":
                kb.insert(0, [InlineKeyboardButton("🌐 Translate EN", callback_data=f"tr_lyrics_{idx}_en")])
            else:
                kb.insert(0, [InlineKeyboardButton("🌐 Translate ID", callback_data=f"tr_lyrics_{idx}_id")])
            await q.edit_message_text(full, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
            return
        except Exception as e:
            await q.edit_message_text(f"❌ Translate gagal: {e}")
            return

    if data.startswith("artist_page_"):
        page = int(data.split("_")[-1])
        context.user_data['artist_page'] = page
        await show_artist_page(q, context, page, edit=False)
        return
    if data.startswith("favdel_"):
        vid = data.split("_",1)[1]
        db_exec("DELETE FROM favorites WHERE user_id=? AND video_id=?", (q.from_user.id, vid))
        await q.edit_message_text("✅ Dihapus dari favorit.")
        return
    if data.startswith("favplay_"):
        vid = data.split("_",1)[1]
        fav = db_exec("SELECT * FROM favorites WHERE user_id=? AND video_id=?", (q.from_user.id, vid), fetchone=True)
        if not fav:
            await q.edit_message_text("❌ Favorit gak ketemu.")
            return
        entry = {"id": vid, "title": fav["title"], "uploader": fav["artist"], "duration": 0, "url": f"https://www.youtube.com/watch?v={vid}"}
        context.user_data['search_results'] = [entry]
        context.user_data['search_results_full'] = [entry]
        context.user_data['picked_idx'] = 0
        def_q = get_user_quality(q.from_user.id)
        quality = def_q
        kb = [
            [InlineKeyboardButton("▶️ Preview 30s", callback_data="preview_0"),
             InlineKeyboardButton("📜 Lirik", callback_data="lyrics_0")],
            [InlineKeyboardButton("🔀 Lagu Serupa", callback_data="related_0"),
             InlineKeyboardButton("➕ Queue", callback_data="queue_0")],
            [InlineKeyboardButton("❌ Batal", callback_data="cancel")],
        ]
        await q.edit_message_text(f"⭐ Favorit: {fav['title']}\n👤 {fav['artist']}\n\n⚡ Auto-download {quality} ✨ — tunggu 10-30 detik...", reply_markup=InlineKeyboardMarkup(kb))
        # opsi1 auto fav
        try:
            vid_f = fav['video_id']
            title_f = fav['title']
            artist_f = fav['artist']
            url_f = f"https://www.youtube.com/watch?v={vid_f}"
            dur_f = "0:00"
            uploader_f = artist_f
            if not check_rate_limit(q.from_user.id, limit=5, window=60):
                await q.edit_message_text("⏳ Terlalu banyak download, tunggu 1 menit ya.", reply_markup=InlineKeyboardMarkup(kb))
                return
            cached_f = get_cache(vid_f, quality)
            if cached_f and cached_f.get('file_id'):
                try:
                    cap_f = f"🎵 {title_f}\n👤 {artist_f} • ⏱ {dur_f}\n📦 cached • M4A 128 AAC ⚡\n✅ via @{BOT_USERNAME}"
                    sent_f = await context.bot.send_audio(chat_id=q.message.chat_id, message_thread_id=getattr(q.message, "message_thread_id", None), audio=cached_f['file_id'], caption=cap_f, title=title_f[:64], performer=artist_f[:64], read_timeout=300, write_timeout=300)
                    inc_cache_hit(); add_history(q.from_user.id, vid_f, title_f, artist_f, quality)
                    await q.edit_message_text(f"✅ Done Favorit (cached {quality}): {title_f}\ncek di bawah ⬇️")
                    return
                except Exception as e:
                    log.warning(f"fav cache fail {e}")
                    db_exec("DELETE FROM file_cache WHERE video_id=? AND quality=?", (vid_f, quality))
            await q.edit_message_text(f"⏳ Downloading Favorit {quality}...\n🎵 {title_f}\nMohon tunggu 10-30 detik... ✨", reply_markup=InlineKeyboardMarkup(kb))
            safe_f = safe_filename(f"{artist_f} - {title_f} [{quality}]") + f"_{uuid.uuid4().hex[:10]}"
            loop_f = asyncio.get_event_loop()
            def do_dl_f():
                out_f = str(DOWNLOAD_DIR / f"{safe_f}.%(ext)s")
                opts_f = {'format': 'bestaudio[ext=m4a]/bestaudio/best','outtmpl': out_f,'quiet': True,'noplaylist': True} if quality=="m4a" else {'format': 'bestaudio/best','outtmpl': out_f,'postprocessors': [{'key': 'FFmpegExtractAudio','preferredcodec':'mp3','preferredquality': quality}],'writethumbnail': True,'quiet': True,'noplaylist': True}
                with yt_dlp.YoutubeDL(opts_f) as ydl_f: ydl_f.download([url_f])
                if quality=="m4a":
                    for ext in [".m4a",".mp4"]:
                        pp = DOWNLOAD_DIR / f"{safe_f}{ext}"
                        if pp.exists(): return str(pp), None
                    files_f = sorted(DOWNLOAD_DIR.glob(f"{safe_f}*"), key=lambda x: x.stat().st_mtime, reverse=True)
                    return (str(files_f[0]) if files_f else None), None
                else:
                    base_f = DOWNLOAD_DIR / safe_f
                    mp3_f = base_f.with_suffix(".mp3")
                    if not mp3_f.exists():
                        mp3s_f = sorted(DOWNLOAD_DIR.glob("*.mp3"), key=lambda x: x.stat().st_mtime, reverse=True)
                        mp3_f = mp3s_f[0] if mp3s_f else None
                    thumb_f=None
                    for ext in [".webp",".jpg",".png"]:
                        pp = base_f.with_suffix(ext)
                        if pp.exists(): thumb_f=pp; break
                    return str(mp3_f) if mp3_f else None, str(thumb_f) if thumb_f else None
            filepath_f, thumb_f = await loop_f.run_in_executor(None, do_dl_f)
            if not filepath_f or not os.path.exists(filepath_f):
                await q.edit_message_text("❌ File gak ketemu setelah download favorit.", reply_markup=InlineKeyboardMarkup(kb))
                return
            # tag
            try:
                lyr_f=None
                try:
                    lyr_tmp,_,_=get_lyrics(title_f, artist_f)
                    if lyr_tmp: lyr_f=lyr_tmp[:4000]
                except: pass
                if quality=="m4a":
                    from mutagen.mp4 import MP4
                    au_f=MP4(filepath_f); au_f["\xa9nam"]=title_f; au_f["\xa9ART"]=artist_f; au_f["\xa9alb"]=uploader_f
                    if lyr_f:
                        try: au_f["\xa9lyr"]=lyr_f
                        except: pass
                    au_f.save()
                else:
                    from mutagen.mp3 import MP3
                    from mutagen.id3 import ID3, TIT2, TPE1, TALB, USLT
                    au_f=MP3(filepath_f, ID3=ID3)
                    try: au_f.add_tags()
                    except: pass
                    if au_f.tags is None: au_f.add_tags()
                    au_f.tags.add(TIT2(encoding=3, text=title_f)); au_f.tags.add(TPE1(encoding=3, text=artist_f)); au_f.tags.add(TALB(encoding=3, text=uploader_f))
                    if lyr_f: au_f.tags.add(USLT(encoding=3, lang='eng', desc='lyrics', text=lyr_f))
                    au_f.save(v2_version=3)
            except Exception as e: log.warning(f"tag fav fail {e}")
            cap_f = f"🎵 {title_f}\n👤 {artist_f} • ⏱ {dur_f}\n📦 M4A 128 AAC\n✅ via @{BOT_USERNAME}"
            sent_f = await context.bot.send_audio(chat_id=q.message.chat_id, message_thread_id=getattr(q.message, "message_thread_id", None), audio=open(filepath_f,'rb'), caption=cap_f, title=title_f[:64], performer=artist_f[:64], read_timeout=300, write_timeout=300, filename=os.path.basename(filepath_f))
            try: set_cache(vid_f, quality, sent_f.audio.file_id, sent_f.audio.file_unique_id, title_f, artist_f, sent_f.audio.file_size or os.path.getsize(filepath_f))
            except: pass
            add_history(q.from_user.id, vid_f, title_f, artist_f, quality)
            await q.edit_message_text(f"✅ Done Favorit ({quality}): {title_f}\ncek di bawah ⬇️")
            try: os.remove(filepath_f)
            except: pass
            if thumb_f and os.path.exists(thumb_f):
                try: os.remove(thumb_f)
                except: pass
        except Exception as e:
            log.warning(f"auto fav fail {e}")
            await q.edit_message_text(f"❌ Favorit gagal: {e}")
        return
        return
    # lyrics button (original) but enhance with translate
    if data.startswith("lyrics_"):
        idx = int(data.split("_")[1])
        entries = context.user_data.get('search_results', []) or context.user_data.get('search_results_full', [])
        if idx >= len(entries):
            await q.edit_message_text("❌ Data expired.")
            return
        video = entries[idx]
        title = video.get('title','Unknown')
        uploader = video.get('uploader','')
        artist = uploader
        if " - " in title:
            artist = title.split(" - ")[0].strip()
        await q.edit_message_text(f"📜 Nyari lirik...\n🎵 {title}\n👤 {artist}\n⏳...")
        def do_lyr():
            lyr, t, a = get_lyrics(title, artist)
            return lyr, t, a
        lyr, t, a = await asyncio.get_event_loop().run_in_executor(None, do_lyr)
        if not lyr:
            kb = [[InlineKeyboardButton("⬅️ Balik ke Kualitas", callback_data=f"pick_{idx}")]]
            await q.edit_message_text(f"❌ Lirik gak ketemu untuk:\n{title}\nCoba `/lyrics {title[:30]}`", reply_markup=InlineKeyboardMarkup(kb))
            return
        # store lyrics for translate
        context.user_data[f'lyrics_{idx}'] = lyr
        header = f"📜 **{t or title}** — {a or artist}\n━━━━━━━━━━━━━━━━━━\n"
        full = header + lyr
        if len(full) > 4000:
            full = full[:4000] + "\n... (kepotong)"
        kb = [
            [InlineKeyboardButton("🌐 Translate ID", callback_data=f"tr_lyrics_{idx}_id"),
             InlineKeyboardButton("🌐 Translate EN", callback_data=f"tr_lyrics_{idx}_en")],
            [InlineKeyboardButton("⬅️ Balik ke Kualitas", callback_data=f"pick_{idx}")],
            [InlineKeyboardButton("📥 Download Lagu Ini", callback_data=f"pick_{idx}")]
        ]
        await q.edit_message_text(full, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
        return
    # preview 30s
    if data.startswith("preview_"):
        idx = int(data.split("_")[1])
        entries = context.user_data.get('search_results', []) or context.user_data.get('search_results_full', [])
        if idx >= len(entries):
            await q.edit_message_text("❌ Data expired.")
            return
        video = entries[idx]
        url = video.get('url') or f"https://www.youtube.com/watch?v={video.get('id')}"
        title = video.get('title','Unknown')
        await q.edit_message_text(f"▶️ Preview 30s...\n🎵 {title}\n⏳ Download cuplikan (10-15 detik)...")
        safe_name = safe_filename(f"preview_{video.get('id')}") + f"_{uuid.uuid4().hex[:10]}"
        loop = asyncio.get_event_loop()
        def do_preview():
            out = str(DOWNLOAD_DIR / f"{safe_name}.%(ext)s")
            opts = {'format':'bestaudio/best','outtmpl':out,'quiet':True,'noplaylist':True}
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
            files = sorted(DOWNLOAD_DIR.glob(f"{safe_name}*"), key=lambda x: x.stat().st_mtime, reverse=True)
            src = str(files[0]) if files else None
            if not src:
                return None
            preview_path = str(DOWNLOAD_DIR / f"{safe_name}_30s.mp3")
            import subprocess
            cmd = ["ffmpeg","-y","-i",src,"-t","30","-vn","-acodec","libmp3lame","-q:a","5",preview_path]
            try:
                subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
                if os.path.exists(preview_path):
                    return preview_path
                return src
            except Exception as e:
                log.warning(f"ffmpeg preview fail {e}")
                return src
        try:
            preview_path = await loop.run_in_executor(None, do_preview)
        except Exception as e:
            await q.edit_message_text(f"❌ Preview gagal: {e}")
            return
        if not preview_path or not os.path.exists(preview_path):
            await q.edit_message_text("❌ Preview gagal (file gak ketemu)")
            return
        try:
            await context.bot.send_audio(chat_id=q.message.chat_id, message_thread_id=getattr(q.message, "message_thread_id", None), audio=open(preview_path,'rb'),
                                         title=f"[PREVIEW 30s] {title[:30]}", performer=video.get('uploader','Preview'),
                                         caption=f"▶️ Preview 30s — {title}\nKalau cocok, klik balik untuk download full ⬇️")
            kb = [[InlineKeyboardButton("⬅️ Balik", callback_data=f"pick_{idx}")],
                  [InlineKeyboardButton("⭐ Favorite", callback_data=f"fav_{idx}"),
                   InlineKeyboardButton("❌ Batal", callback_data="cancel")]]
            await q.edit_message_text(f"✅ Preview 30s terkirim ⬆️\n🎵 {title}\n\nPilih kualitas full:", reply_markup=InlineKeyboardMarkup(kb))
        except Exception as e:
            await q.edit_message_text(f"❌ Gagal kirim preview: {e}")
        finally:
            try:
                for p in DOWNLOAD_DIR.glob(f"{safe_name}*"):
                    try: os.remove(p)
                    except: pass
            except: pass
        return

    # STEP 1: pilih lagu -> auto-download M4A default, tombol tetap ada
    if data.startswith("pick_"):
        idx = int(data.split("_")[1])
        entries = context.user_data.get('search_results', []) or context.user_data.get('search_results_full', [])
        if entries is None or idx >= len(entries):
            await q.edit_message_text("❌ Data expired, coba /play /artist lagi ya.")
            return
        context.user_data['picked_idx'] = idx
        video = entries[idx]
        title = video.get('title','Unknown')
        dur = format_duration(video.get('duration'))
        vid = video.get('id')
        cache_info = ""
        for qual in ["192","320","m4a"]:
            c = get_cache(vid, qual)
            if c:
                cache_info += f" ⚡{qual} cached"
        if cache_info:
            cache_info = "\n⚡ Cached tersedia — download instan!" + cache_info
        def_q = get_user_quality(q.from_user.id)
        quality = def_q  # opsi1 auto pakai default user
        kb = [
            [InlineKeyboardButton("▶️ Preview 30s", callback_data=f"preview_{idx}"),
             InlineKeyboardButton("📜 Lirik", callback_data=f"lyrics_{idx}")],
            [InlineKeyboardButton("🔀 Lagu Serupa", callback_data=f"related_{idx}"),
             InlineKeyboardButton("➕ Queue", callback_data=f"queue_{idx}")],
            [InlineKeyboardButton("⭐ Favorite", callback_data=f"fav_{idx}"),
             InlineKeyboardButton("❌ Batal", callback_data="cancel")],
        ]
        # Auto-download notice — 3 tombol kualitas dihilangkan
        await q.edit_message_text(
            f"🎵 Dipilih: {title}\n⏱ {dur}{cache_info}\n\n⚡ **Auto-download {quality} ✨** — tunggu 10-30 detik ya...\nLangsung jadi, tanpa pilih kualitas lagi ✨",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(kb)
        )
        # === langsung download M4A default (tanpa nunggu klik) ===
        url = video.get('url') or f"https://www.youtube.com/watch?v={vid}"
        uploader = video.get('uploader', 'YouTube')
        artist = uploader
        if " - " in title:
            artist = title.split(" - ")[0].strip()
        if not check_rate_limit(q.from_user.id, limit=5, window=60):
            await q.edit_message_text("⏳ Terlalu banyak download, tunggu 1 menit ya.", reply_markup=InlineKeyboardMarkup(kb))
            return
        # cache hit?
        cached = get_cache(vid, quality)
        if cached and cached.get('file_id'):
            try:
                info_q = "M4A 128 AAC"
                cap = f"🎵 {title}\n👤 {artist} • ⏱ {dur}\n📦 cached • {info_q} ⚡\n✅ via @{BOT_USERNAME}"
                sent = await context.bot.send_audio(chat_id=q.message.chat_id, message_thread_id=getattr(q.message, "message_thread_id", None), audio=cached['file_id'], caption=cap, title=title[:64], performer=artist[:64], read_timeout=300, write_timeout=300)
                inc_cache_hit()
                add_history(q.from_user.id, vid, title, artist, quality)
                is_fav = db_exec("SELECT 1 FROM favorites WHERE user_id=? AND video_id=?", (q.from_user.id, vid), fetchone=True)
                fav_text = "✅ Favorited!" if is_fav else "⭐ Favorite"
                kb_done = [
                    [InlineKeyboardButton(fav_text, callback_data=f"fav_{idx}"), InlineKeyboardButton("❌ Batal", callback_data="cancel")],
                ]
                await q.edit_message_text(f"✅ Done (cached M4A): {title}\n⚡ Instant dari cache — cek audio di bawah ⬇️", reply_markup=InlineKeyboardMarkup(kb_done))
                return
            except Exception as e:
                log.warning(f"cache send fail {e} - will re-download")
                db_exec("DELETE FROM file_cache WHERE video_id=? AND quality=?", (vid, quality))
        # download fresh
        await q.edit_message_text(
            f"⏳ Downloading M4A...\n🎵 {title}\n👤 {artist} • ⏱ {dur}\n🔗 {url}\n\nMohon tunggu 10-30 detik... ✨",
            reply_markup=InlineKeyboardMarkup(kb)
        )
        safe_name = safe_filename(f"{artist} - {title} [{quality}]") + f"_{uuid.uuid4().hex[:10]}"
        loop = asyncio.get_event_loop()
        def do_dl_auto():
            if quality == "m4a":
                out = str(DOWNLOAD_DIR / f"{safe_name}.%(ext)s")
                opts = {'format': 'bestaudio[ext=m4a]/bestaudio/best','outtmpl': out,'quiet': True,'noplaylist': True}
                with yt_dlp.YoutubeDL(opts) as ydl:
                    ydl.download([url])
                for ext in [".m4a",".mp4"]:
                    p = DOWNLOAD_DIR / f"{safe_name}{ext}"
                    if p.exists():
                        return str(p), None
                files = sorted(DOWNLOAD_DIR.glob(f"{safe_name}*"), key=lambda x: x.stat().st_mtime, reverse=True)
                return (str(files[0]) if files else None), None
            else:
                out = str(DOWNLOAD_DIR / f"{safe_name}.%(ext)s")
                opts = {'format': 'bestaudio/best','outtmpl': out,'postprocessors': [{'key': 'FFmpegExtractAudio','preferredcodec':'mp3','preferredquality': quality}],'writethumbnail': True,'quiet': True,'noplaylist': True}
                with yt_dlp.YoutubeDL(opts) as ydl:
                    ydl.download([url])
                base = DOWNLOAD_DIR / safe_name
                mp3_path = base.with_suffix(".mp3")
                if not mp3_path.exists():
                    mp3s = sorted(DOWNLOAD_DIR.glob("*.mp3"), key=lambda x: x.stat().st_mtime, reverse=True)
                    mp3_path = mp3s[0] if mp3s else None
                thumb = None
                for ext in [".webp",".jpg",".png"]:
                    p = base.with_suffix(ext)
                    if p.exists():
                        thumb = p
                        break
                return str(mp3_path) if mp3_path else None, str(thumb) if thumb else None
        try:
            filepath, thumb_path = await loop.run_in_executor(None, do_dl_auto)
        except Exception as e:
            await q.edit_message_text(f"❌ Gagal download: {e}\n\nCoba lagi atau hubungi @aeounn", reply_markup=InlineKeyboardMarkup(kb))
            return
        if not filepath or not os.path.exists(filepath):
            await q.edit_message_text("❌ File gak ketemu setelah download.\nHubungi @aeounn", reply_markup=InlineKeyboardMarkup(kb))
            return
        # tagging
        try:
            lyr_text = None
            try:
                lyr, _, _ = get_lyrics(title, artist)
                if lyr:
                    lyr_text = lyr[:4000]
            except: pass
            if quality == "m4a":
                from mutagen.mp4 import MP4
                audio = MP4(filepath)
                audio["\xa9nam"] = title
                audio["\xa9ART"] = artist
                audio["\xa9alb"] = uploader
                if lyr_text:
                    try:
                        audio["\xa9lyr"] = lyr_text
                    except:
                        try:
                            audio["----:com.apple.iTunes:LYRICS"] = bytes(lyr_text, 'utf-8')
                        except: pass
                audio.save()
                if thumb_path and os.path.exists(thumb_path):
                    try:
                        from PIL import Image
                        im = Image.open(thumb_path).convert("RGB")
                        im = im.resize((320,320), Image.LANCZOS)
                        jpg = thumb_path.rsplit(".",1)[0]+".jpg"
                        im.save(jpg,"JPEG", quality=90)
                        thumb_path = jpg
                        from mutagen.mp4 import MP4Cover
                        audio2 = MP4(filepath)
                        with open(thumb_path,'rb') as f:
                            audio2["covr"] = [MP4Cover(f.read(), imageformat=MP4Cover.FORMAT_JPEG)]
                        audio2.save()
                    except Exception as e:
                        log.warning(f"m4a cover fail {e}")
            else:
                from mutagen.mp3 import MP3
                from mutagen.id3 import ID3, TIT2, TPE1, TALB, USLT, APIC
                try:
                    audio = MP3(filepath, ID3=ID3)
                except:
                    audio = MP3(filepath)
                if audio.tags is None:
                    audio.add_tags()
                audio.tags.add(TIT2(encoding=3, text=title))
                audio.tags.add(TPE1(encoding=3, text=artist))
                audio.tags.add(TALB(encoding=3, text=uploader))
                if lyr_text:
                    audio.tags.add(USLT(encoding=3, lang='eng', desc='lyrics', text=lyr_text))
                if thumb_path and os.path.exists(thumb_path):
                    try:
                        from PIL import Image
                        im = Image.open(thumb_path).convert("RGB")
                        im = im.resize((320,320), Image.LANCZOS)
                        jpg = thumb_path.rsplit(".",1)[0]+".jpg"
                        im.save(jpg,"JPEG", quality=90)
                        with open(jpg,'rb') as f:
                            audio.tags.add(APIC(encoding=3, mime='image/jpeg', type=3, desc='Cover', data=f.read()))
                    except Exception as e:
                        log.warning(f"mp3 cover fail {e}")
                audio.save(v2_version=3)
        except Exception as e:
            log.warning(f"tag fail {e}")
        # send
        try:
            info_q = "M4A 128 AAC"
            cap = f"🎵 {title}\n👤 {artist} • ⏱ {dur}\n📦 {info_q}\n✅ via @{BOT_USERNAME}"
            sent = await context.bot.send_audio(chat_id=q.message.chat_id, message_thread_id=getattr(q.message, "message_thread_id", None), audio=open(filepath,'rb'), caption=cap, title=title[:64], performer=artist[:64], read_timeout=300, write_timeout=300, filename=os.path.basename(filepath))
            # cache
            try:
                fid = sent.audio.file_id
                fuid = sent.audio.file_unique_id
                fsize = sent.audio.file_size or os.path.getsize(filepath)
                set_cache(vid, quality, fid, fuid, title, artist, fsize)
            except: pass
            add_history(q.from_user.id, vid, title, artist, quality)
            is_fav = db_exec("SELECT 1 FROM favorites WHERE user_id=? AND video_id=?", (q.from_user.id, vid), fetchone=True)
            fav_text = "✅ Favorited!" if is_fav else "⭐ Favorite"
            kb_done = [
                [InlineKeyboardButton(fav_text, callback_data=f"fav_{idx}"), InlineKeyboardButton("❌ Batal", callback_data="cancel")],
            ]
            await q.edit_message_text(f"✅ Done (M4A): {title}\ncek di bawah ⬇️", reply_markup=InlineKeyboardMarkup(kb_done))
        except Exception as e:
            await q.edit_message_text(f"❌ Gagal kirim audio: {e}", reply_markup=InlineKeyboardMarkup(kb))
        finally:
            try:
                import os as _os2
                _os2.remove(filepath)
                if thumb_path and _os2.path.exists(thumb_path): _os2.remove(thumb_path)
                for p in DOWNLOAD_DIR.glob(safe_name+"*"):
                    try: _os2.remove(p)
                    except: pass
            except: pass
        return
    if data.startswith("fav_"):
        idx = int(data.split("_")[1])
        entries = context.user_data.get('search_results', []) or context.user_data.get('search_results_full', [])
        if idx >= len(entries):
            await q.edit_message_text("❌ Data expired.")
            return
        video = entries[idx]
        vid = video.get('id')
        title = video.get('title','Unknown')
        uploader = video.get('uploader','YouTube')
        artist = uploader
        if " - " in title:
            artist = title.split(" - ")[0].strip()
        added = toggle_fav(q.from_user.id, vid, title, artist)
        if added:
            await q.answer("⭐ Ditambah ke favorit!", show_alert=False)
        else:
            await q.answer("💔 Dihapus dari favorit", show_alert=False)
        try:
            msg_text = q.message.text or q.message.caption or ""
            if msg_text.startswith("✅ Done"):
                fav_text = "✅ Favorited!" if added else "⭐ Favorite"
                kb_min = [[InlineKeyboardButton(fav_text, callback_data=f"fav_{idx}"), InlineKeyboardButton("❌ Batal", callback_data="cancel")]]
                await q.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(kb_min))
                return
        except: pass
        dur = format_duration(video.get('duration'))
        kb = [
            [InlineKeyboardButton("▶️ Preview 30s", callback_data=f"preview_{idx}"),
             InlineKeyboardButton("📜 Lirik", callback_data=f"lyrics_{idx}")],
            [InlineKeyboardButton("🔀 Lagu Serupa", callback_data=f"related_{idx}"),
             InlineKeyboardButton("➕ Queue", callback_data=f"queue_{idx}")],
            [InlineKeyboardButton("✅ Favorited!" if added else "⭐ Favorite", callback_data=f"fav_{idx}"),
             InlineKeyboardButton("❌ Batal", callback_data="cancel")],
        ]
        try:
            await q.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(kb))
        except: pass
        return
    # STEP 2: download dengan kualitas terpilih
    if data.startswith("dl_"):
        quality = data.split("_")[1]  # 192, 320, m4a
        idx = context.user_data.get('picked_idx', 0)
        entries = context.user_data.get('search_results', []) or context.user_data.get('search_results_full', [])
        if idx >= len(entries):
            await q.edit_message_text("❌ Data expired, coba lagi.")
            return
        video = entries[idx]
        url = video.get('url') or f"https://www.youtube.com/watch?v={video.get('id')}"
        vid = video.get('id')
        title = video.get('title', 'Unknown')
        uploader = video.get('uploader', 'YouTube')
        artist = uploader
        if " - " in title:
            artist = title.split(" - ")[0].strip()
        dur = format_duration(video.get('duration'))

        if not check_rate_limit(q.from_user.id, limit=5, window=60):
            await q.edit_message_text("⏳ Terlalu banyak download, tunggu 1 menit ya.")
            return

        cached = get_cache(vid, quality)
        if cached and cached.get('file_id'):
            await q.edit_message_text(
                f"⚡ Cache hit! Ngirim instan ({quality})...\n"
                f"🎵 {title}\n"
                f"👤 {artist} • ⏱ {dur}"
            )
            try:
                info_q = "MP3 192" if quality=="192" else "MP3 320" if quality=="320" else "M4A 128 AAC"
                cap = f"🎵 {title}\n👤 {artist} • ⏱ {dur}\n📦 cached • {info_q} ⚡\n✅ via @{BOT_USERNAME}"
                if quality == "m4a":
                    sent = await context.bot.send_audio(chat_id=q.message.chat_id, message_thread_id=getattr(q.message, "message_thread_id", None), audio=cached['file_id'],
                                                         caption=cap, title=title[:64], performer=artist[:64],
                                                         read_timeout=300, write_timeout=300)
                else:
                    sent = await context.bot.send_audio(chat_id=q.message.chat_id, message_thread_id=getattr(q.message, "message_thread_id", None), audio=cached['file_id'],
                                                         caption=cap, title=title[:64], performer=artist[:64],
                                                         read_timeout=300, write_timeout=300)
                inc_cache_hit()
                add_history(q.from_user.id, vid, title, artist, quality)
                is_fav = db_exec("SELECT 1 FROM favorites WHERE user_id=? AND video_id=?", (q.from_user.id, vid), fetchone=True)
                fav_text = "✅ Favorited!" if is_fav else "⭐ Favorite"
                kb = [
                    [InlineKeyboardButton(fav_text, callback_data=f"fav_{idx}"),
                     InlineKeyboardButton("❌ Batal", callback_data="cancel")],
                ]
                await q.edit_message_text(f"✅ Done (cached {quality}): {title}\n⚡ Instant dari cache — cek audio di bawah ⬇️", reply_markup=InlineKeyboardMarkup(kb))
                return
            except Exception as e:
                log.warning(f"cache send fail {e} - will re-download")
                db_exec("DELETE FROM file_cache WHERE video_id=? AND quality=?", (vid, quality))

        kb_keep = [
            [InlineKeyboardButton("❌ Batal", callback_data="cancel")],
        ]
        await q.edit_message_text(
            f"⏳ Downloading {quality}...\n"
            f"🎵 {title}\n"
            f"👤 {artist} • ⏱ {dur}\n"
            f"🔗 {url}\n\n"
            f"Mohon tunggu 10-30 detik (timeout 5 menit)...",
            reply_markup=InlineKeyboardMarkup(kb_keep)
        )
        safe_name = safe_filename(f"{artist} - {title} [{quality}]") + f"_{uuid.uuid4().hex[:10]}"
        loop = asyncio.get_event_loop()
        def do_dl():
            if quality == "m4a":
                out = str(DOWNLOAD_DIR / f"{safe_name}.%(ext)s")
                opts = {
                    'format': 'bestaudio[ext=m4a]/bestaudio/best',
                    'outtmpl': out,
                    'quiet': True,
                    'noplaylist': True,
                }
                with yt_dlp.YoutubeDL(opts) as ydl:
                    ydl.download([url])
                for ext in [".m4a",".mp4"]:
                    p = DOWNLOAD_DIR / f"{safe_name}{ext}"
                    if p.exists():
                        return str(p), None
                files = sorted(DOWNLOAD_DIR.glob(f"{safe_name}*"), key=lambda x: x.stat().st_mtime, reverse=True)
                return (str(files[0]) if files else None), None
            else:
                out = str(DOWNLOAD_DIR / f"{safe_name}.%(ext)s")
                opts = {
                    'format': 'bestaudio/best',
                    'outtmpl': out,
                    'postprocessors': [{'key': 'FFmpegExtractAudio','preferredcodec':'mp3','preferredquality': quality}],
                    'writethumbnail': True,
                    'quiet': True,
                    'noplaylist': True,
                }
                with yt_dlp.YoutubeDL(opts) as ydl:
                    ydl.download([url])
                base = DOWNLOAD_DIR / safe_name
                mp3_path = base.with_suffix(".mp3")
                if not mp3_path.exists():
                    mp3s = sorted(DOWNLOAD_DIR.glob("*.mp3"), key=lambda x: x.stat().st_mtime, reverse=True)
                    mp3_path = mp3s[0] if mp3s else None
                thumb = None
                for ext in [".webp",".jpg",".png"]:
                    p = base.with_suffix(ext)
                    if p.exists():
                        thumb = p
                        break
                return str(mp3_path) if mp3_path else None, str(thumb) if thumb else None

        try:
            filepath, thumb_path = await loop.run_in_executor(None, do_dl)
        except Exception as e:
            await q.edit_message_text(f"❌ Gagal download: {e}\n\nCoba lagi atau hubungi @aeounn")
            return
        if not filepath or not os.path.exists(filepath):
            await q.edit_message_text("❌ File gak ketemu setelah download.\nHubungi @aeounn")
            return
        # tagging v6 improved: embed lyric + cover 320x320 + ID3v2.3
        try:
            # fetch lyrics for embedding
            lyr_text = None
            try:
                lyr, _, _ = get_lyrics(title, artist)
                if lyr:
                    lyr_text = lyr[:4000]
            except: pass
            if quality == "m4a":
                from mutagen.mp4 import MP4
                audio = MP4(filepath)
                audio["\xa9nam"] = title
                audio["\xa9ART"] = artist
                audio["\xa9alb"] = uploader
                # embed lyrics as ©lyr (custom) or use implicit? mutagen MP4 uses ----:com.apple.iTunes:LYRICS or ©lyr
                if lyr_text:
                    try:
                        audio["\xa9lyr"] = lyr_text
                    except:
                        try:
                            audio["----:com.apple.iTunes:LYRICS"] = bytes(lyr_text, 'utf-8')
                        except: pass
                audio.save()
                # embed cover for m4a if thumb exists? download thumbnail via yt thumbnail if needed
                if thumb_path and os.path.exists(thumb_path):
                    try:
                        # convert to jpg 320x320 if needed
                        from PIL import Image
                        im = Image.open(thumb_path).convert("RGB")
                        im = im.resize((320,320), Image.LANCZOS)
                        jpg = thumb_path.rsplit(".",1)[0]+".jpg"
                        im.save(jpg,"JPEG", quality=90)
                        thumb_path = jpg
                        from mutagen.mp4 import MP4Cover
                        audio2 = MP4(filepath)
                        with open(thumb_path,'rb') as f:
                            audio2["covr"] = [MP4Cover(f.read(), imageformat=MP4Cover.FORMAT_JPEG)]
                        audio2.save()
                    except Exception as e:
                        log.warning(f"m4a cover embed fail {e}")
            else:
                from mutagen.id3 import ID3, TIT2, TPE1, TALB, APIC, USLT
                from mutagen.mp3 import MP3
                audio = MP3(filepath, ID3=ID3)
                try: audio.add_tags()
                except: pass
                try:
                    audio.tags.delall("TIT2"); audio.tags.delall("TPE1"); audio.tags.delall("TALB"); audio.tags.delall("USLT"); audio.tags.delall("APIC")
                except: pass
                audio.tags.add(TIT2(encoding=3, text=title))
                audio.tags.add(TPE1(encoding=3, text=artist))
                audio.tags.add(TALB(encoding=3, text=uploader))
                if lyr_text:
                    try:
                        audio.tags.add(USLT(encoding=3, lang='eng', desc='', text=lyr_text))
                    except Exception as e:
                        log.warning(f"USLT fail {e}")
                if thumb_path and os.path.exists(thumb_path):
                    try:
                        if thumb_path.endswith(".webp"):
                            from PIL import Image
                            im = Image.open(thumb_path).convert("RGB")
                            im = im.resize((320,320), Image.LANCZOS)
                            jpg = thumb_path.rsplit(".",1)[0]+".jpg"
                            im.save(jpg,"JPEG", quality=90)
                            thumb_path = jpg
                        else:
                            # ensure 320x320
                            from PIL import Image
                            im = Image.open(thumb_path).convert("RGB")
                            im = im.resize((320,320), Image.LANCZOS)
                            jpg = thumb_path.rsplit(".",1)[0]+".jpg"
                            im.save(jpg,"JPEG", quality=90)
                            thumb_path = jpg
                        if os.path.exists(thumb_path):
                            with open(thumb_path,'rb') as f:
                                audio.tags.add(APIC(encoding=3, mime='image/jpeg', type=3, desc='Cover', data=f.read()))
                    except Exception as e:
                        log.warning(f"cover convert fail {e}")
                # if still no thumb, try to fetch via yt thumbnail url? use video thumbnail if available in context
                if not thumb_path or not os.path.exists(thumb_path):
                    try:
                        # try to get thumbnail from yt info: search_results already has thumbnail maybe, but we can fetch via requests
                        # attempt to find thumbnail url in entries
                        thumb_url = None
                        # fallback: try ydl thumbnail extraction quickly via yt_get_info
                        info_thumb = None
                        try:
                            # reuse video dict if has thumbnail
                            thumb_url = video.get('thumbnail') or (video.get('thumbnails') or [{}])[-1].get('url')
                        except: pass
                        if thumb_url:
                            r = requests.get(thumb_url, timeout=10)
                            if r.status_code==200:
                                tmp_jpg = str(DOWNLOAD_DIR / f"{safe_name}_cover.jpg")
                                with open(tmp_jpg,'wb') as f:
                                    f.write(r.content)
                                from PIL import Image
                                im = Image.open(tmp_jpg).convert("RGB")
                                im = im.resize((320,320), Image.LANCZOS)
                                im.save(tmp_jpg,"JPEG", quality=90)
                                with open(tmp_jpg,'rb') as f:
                                    audio.tags.add(APIC(encoding=3, mime='image/jpeg', type=3, desc='Cover', data=f.read()))
                                thumb_path = tmp_jpg
                    except Exception as e:
                        log.warning(f"thumb fetch fail {e}")
                audio.save(v2_version=3)
        except Exception as e:
            log.warning(f"tag fail {e}")

        try:
            import os as _os
            size_mb = _os.path.getsize(filepath)/1024/1024
            info_q = "MP3 192" if quality=="192" else "MP3 320" if quality=="320" else "M4A 128 AAC"
            cap = f"🎵 {title}\n👤 {artist} • ⏱ {dur}\n📦 {size_mb:.1f} MB • {info_q}\n✅ via @{BOT_USERNAME}"
            if quality == "m4a":
                sent = await context.bot.send_audio(chat_id=q.message.chat_id, message_thread_id=getattr(q.message, "message_thread_id", None), audio=open(filepath,'rb'), title=title[:64], performer=artist[:64], caption=cap, read_timeout=300, write_timeout=300, connect_timeout=30, pool_timeout=30)
            else:
                sent = await context.bot.send_audio(chat_id=q.message.chat_id, message_thread_id=getattr(q.message, "message_thread_id", None), audio=open(filepath,'rb'), title=title[:64], performer=artist[:64], thumbnail=open(thumb_path,'rb') if thumb_path and os.path.exists(thumb_path) else None, caption=cap, read_timeout=300, write_timeout=300, connect_timeout=30, pool_timeout=30)
            try:
                file_id = sent.audio.file_id
                file_unique = sent.audio.file_unique_id
                set_cache(vid, quality, file_id, file_unique, title, artist, int(size_mb*1024*1024))
                log.info(f"cache saved {vid} {quality} {file_id[:10]}")
            except Exception as e:
                log.warning(f"cache save fail {e}")
            add_history(q.from_user.id, vid, title, artist, quality)
            is_fav2 = db_exec("SELECT 1 FROM favorites WHERE user_id=? AND video_id=?", (q.from_user.id, vid), fetchone=True)
            fav_text2 = "✅ Favorited!" if is_fav2 else "⭐ Favorite"
            kb = [
                [InlineKeyboardButton(fav_text2, callback_data=f"fav_{idx}"),
                 InlineKeyboardButton("❌ Batal", callback_data="cancel")],
            ]
            await q.edit_message_text(f"✅ Done ({quality}): {title}\nSilakan cek audio di bawah ⬇️\n📦 {size_mb:.1f} MB", reply_markup=InlineKeyboardMarkup(kb))
        except Exception as e:
            await q.edit_message_text(f"❌ Gagal kirim: {e}\n\nCoba lagi atau hubungi @aeounn")
        finally:
            try:
                import os as _os2
                _os2.remove(filepath)
                if thumb_path and _os2.path.exists(thumb_path): _os2.remove(thumb_path)
                for p in DOWNLOAD_DIR.glob(safe_name+"*"):
                    try: _os2.remove(p)
                    except: pass
            except: pass

def main():
    if BOT_TOKEN == "ISI_TOKEN":
        print("❌ ISI BOT_TOKEN")
        return
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).read_timeout(600).write_timeout(600).connect_timeout(30).pool_timeout(60).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("ping", ping))
    app.add_handler(CommandHandler("play", play))
    app.add_handler(CommandHandler("artist", artist))
    app.add_handler(CommandHandler("lyrics", lyrics_cmd))
    app.add_handler(CommandHandler("history", history_cmd))
    app.add_handler(CommandHandler("favorites", favorites_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("settings", settings_cmd))
    app.add_handler(CommandHandler("queue", queue_cmd))
    app.add_handler(CommandHandler("clearqueue", clearqueue_cmd))
    # alias fav, hist
    app.add_handler(CommandHandler("fav", favorites_cmd))
    app.add_handler(CommandHandler("hist", history_cmd))
    app.add_handler(CallbackQueryHandler(menu_callback, pattern=r"^(help_|back_start|show_)"))
    app.add_handler(CallbackQueryHandler(pick_and_dl_handler, pattern=r"^(pick_|dl_|cancel|artist_page_|noop|preview_|lyrics_|fav_|favplay_|favdel_|search_page_|related_|queue_|tr_lyrics_|set_quality_)"))
    app.add_handler(InlineQueryHandler(inline_query_handler))
    # Shazam: voice/audio/video/video_note/document (filter all docs, mime check inside)
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO | filters.VIDEO | filters.VIDEO_NOTE | filters.Document.ALL, shazam_handler))
    # VKM style: handle text after /play
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_awaiting_text))
    print(f"🤖 Bot v6 jalan... @{BOT_USERNAME} | VKM-style /play | cache+history+fav+lyrics+preview+inline+stats+ratelimit | v6: settings+shazam+lyrCover+SC/Apple/TikTok+pagination+related+queue+translate")
    app.run_polling()

if __name__ == "__main__":
    main()