#!/usr/bin/env python3
"""Telegram -> Obsidian Inbox. Text, links, voice/audio, photos and files become notes in `00 - Inbox`.

Uses local optional packages from `scripts/vendor`: yt-dlp and faster-whisper.
Config (scripts/.env or environment):
  TELEGRAM_BOT_TOKEN=...        from @BotFather
  TELEGRAM_ALLOWED_IDS=123,456  your Telegram user id(s); only these can write to the vault
  WHISPER_MODEL=small           optional (tiny/base/small/medium)
  WHISPER_DEVICE=cpu            CPU is the safe default; CUDA needs separate NVIDIA libraries
  WHISPER_PRELOAD=1             load the model at startup (0 = on first use)
  OCR_LANGS=eng+spa             Tesseract languages for the sensitive-data check on photos (Docker image only)
  Telegram voice/audio transcripts are constrained to English or Spanish; Reel transcripts keep automatic language detection.
  English voice notes and video notes are tagged `english-practice` (the Daily Speaking Practice check-in).
Messages that look like they hold secrets (PIN, password, card number, token...) are held until you tap Save or Discard.
Run: python scripts/telegram_bot.py
"""
import json, os, re, sys, time, html, secrets, shutil, tempfile, threading, urllib.request, urllib.parse
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENDOR = Path(__file__).with_name("vendor")
if VENDOR.is_dir() and os.name == "nt":  # vendor/ holds Windows-only wheels; Linux/Docker use pip packages
    sys.path.insert(0, str(VENDOR))
INBOX = ROOT / "00 - Inbox"
ATTACH = INBOX / "attachments"
STAGE = Path(tempfile.gettempdir()) / "vault-bot-staging"  # media waits here until the note is saved
HEARTBEAT = Path(os.environ.get("BOT_HEARTBEAT") or Path(tempfile.gettempdir()) / "vault-bot.heartbeat")
URL_RE = re.compile(r"https?://\S+")
AUDIO_KINDS = ("voice", "audio", "video_note", "video")
PENDING = {}            # captures held for confirmation: token -> item (lost if the bot restarts)
PENDING_TTL = 24 * 3600


def load_env():
    f = Path(__file__).with_name(".env")
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"\''))


load_env()
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
ALLOWED = {int(x) for x in os.environ.get("TELEGRAM_ALLOWED_IDS", "").replace(" ", "").split(",") if x}
API = f"https://api.telegram.org/bot{TOKEN}"
_whisper = None
_whisper_lock = threading.Lock()


def api(method, **params):
    data = urllib.parse.urlencode(params).encode()
    with urllib.request.urlopen(urllib.request.Request(f"{API}/{method}", data=data), timeout=70) as r:
        return json.load(r)["result"]


def reply(chat, text, message_id=None, buttons=None):
    """Send a message, or edit `message_id` in place (a status line becoming the result). Returns the message id."""
    params = {"chat_id": chat, "text": text}
    if buttons:
        params["reply_markup"] = json.dumps({"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in buttons]]})
    try:
        if message_id:
            api("editMessageText", message_id=message_id, **params)
            return message_id
        return api("sendMessage", **params)["message_id"]
    except Exception as e:
        print("reply failed:", e)
        return None


def beat():
    """Heartbeat for the Docker health check: touched on every poll."""
    try:
        HEARTBEAT.touch()
    except OSError:
        pass


def download(file_id, dest_dir, name_hint):
    info = api("getFile", file_id=file_id)
    ext = Path(info["file_path"]).suffix or Path(name_hint).suffix
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{name_hint}{ext}"
    urllib.request.urlretrieve(f"https://api.telegram.org/file/bot{TOKEN}/{info['file_path']}", dest)
    return dest


def whisper():
    """The Whisper model, loaded once. A transcription that arrives during the startup preload waits for it."""
    global _whisper
    with _whisper_lock:
        if _whisper is None:
            from faster_whisper import WhisperModel
            _whisper = WhisperModel(
                os.environ.get("WHISPER_MODEL", "small"),
                device=os.environ.get("WHISPER_DEVICE", "cpu"),
                compute_type="int8",
            )
    return _whisper


def preload_whisper():
    start = time.time()
    try:
        whisper()
        print(f"Whisper model loaded in {time.time() - start:.0f} s")
    except ImportError:
        print("faster-whisper is not installed; audio will not be transcribed")
    except Exception as e:
        print("Whisper preload failed:", e)


def transcribe(path, allowed_languages=None):
    """Return (transcript, language), or (None, None) when transcription is unavailable or fails."""
    try:
        model = whisper()
        if allowed_languages:
            # Whisper auto-detects over its full language set. Use its language
            # probabilities to choose only among the languages allowed for
            # personal Telegram audio, then force decoding in that language.
            detected_segments, detection = model.transcribe(str(path))
            probabilities = dict(detection.all_language_probs or [])
            if detection.language in allowed_languages:
                language = detection.language
                segs = detected_segments
            else:
                language = max(allowed_languages, key=lambda code: probabilities.get(code, 0.0))
                segs, _ = model.transcribe(str(path), language=language)
            print(
                f"Telegram audio: transcribing as {language} "
                f"(detector probability {probabilities.get(language, 0.0):.2f})"
            )
        else:
            segs, detection = model.transcribe(str(path))
            language = detection.language
        return " ".join(s.text.strip() for s in segs).strip(), language
    except ImportError:
        return None, None
    except Exception as e:
        print("transcription failed:", e)
        return None, None


def ocr_text(path):
    """Text in an image, only for the sensitive-data check (never saved). Needs Tesseract (installed in the Docker image)."""
    try:
        import pytesseract
        from PIL import Image
        with Image.open(path) as image:
            return pytesseract.image_to_string(image, lang=os.environ.get("OCR_LANGS", "eng+spa"), timeout=60)
    except Exception as e:
        print("OCR skipped:", type(e).__name__)
        return ""


def _luhn(digits):
    total = 0
    for i, d in enumerate(reversed(digits)):
        n = int(d) * (2 if i % 2 else 1)
        total += n - 9 if n > 9 else n
    return total % 10 == 0


SENSITIVE = [
    ("a PIN or PUK", re.compile(r"\b(?:pin|puk)\d?\b[^\d\n]{0,20}\d{4,8}\b", re.I)),
    ("a password", re.compile(r"\b(?:password|passwd|pwd|contraseña|contrasena)\b\s*(?:[:=]|is\b|es\b)\s*\S+", re.I)),
    ("a password", re.compile(r"\b(?:clave|passcode|passphrase)\b\s*[:=]\s*\S+", re.I)),
    ("a card security code", re.compile(r"\b(?:cvv2?|cvc)\b[^\d\n]{0,15}\d{3,4}\b", re.I)),
    ("a bank account number", re.compile(r"\b(?:cbu|cvu)\b\D{0,15}\d{22}\b|\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){3,7}\b", re.I)),
    ("an API key or token", re.compile(r"\b(?:sk-[\w-]{20,}|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_\w{20,}|xox[abpr]-[\w-]{10,}"
                                       r"|AKIA[0-9A-Z]{16}|AIza[\w-]{35}|\d{8,10}:AA[\w-]{33})")),
    ("a token or secret", re.compile(r"\b(?:token|api[ _-]?key|secret|private key)\b\s*[:=]\s*\S{8,}", re.I)),
    ("a seed or recovery phrase", re.compile(r"\b(?:seed phrase|recovery phrase|frase semilla|mnemonic)\b", re.I)),
]
CARD_RE = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")


def sensitive_hits(text):
    """What kinds of secrets the user's own text, transcript or photo seems to hold. URLs are ignored (ids look like numbers)."""
    text = URL_RE.sub(" ", text or "")
    hits = [label for label, rx in SENSITIVE if rx.search(text)]
    if any(_luhn(re.sub(r"\D", "", m)) for m in CARD_RE.findall(text)):
        hits.append("a card number")
    return list(dict.fromkeys(hits))


def page_title(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            head = r.read(200_000).decode("utf-8", "ignore")
        m = re.search(r"<title[^>]*>(.*?)</title>", head, re.S | re.I)
        return html.unescape(" ".join(m.group(1).split())) if m else None
    except Exception:
        return None


def instagram_url(url):
    return bool(re.match(r"https?://(?:www\.)?(?:instagram\.com|instagr\.am)/", url, re.I))


def tiktok_url(url):
    return bool(re.match(r"https?://(?:(?:www|m|vm|vt)\.)?tiktok\.com/", url, re.I))


def youtube_url(url):
    return bool(re.match(r"https?://(?:(?:www|m|music)\.)?(?:youtube\.com|youtu\.be)/", url, re.I))


def x_post_url(url):
    return bool(re.match(r"https?://(?:(?:www|mobile)\.)?(?:x\.com|twitter\.com)/.+/status/\d+", url, re.I))


def _clean_embed_html(fragment):
    fragment = re.sub(r"<br\s*/?>", "\n", fragment, flags=re.I)
    fragment = re.sub(r"</p\s*>", "\n", fragment, flags=re.I)
    fragment = re.sub(r"<[^>]+>", " ", fragment)
    return "\n".join(" ".join(html.unescape(line).split()) for line in fragment.splitlines()).strip()


def _x_post_text(post):
    note = post.get("note_tweet") or {}
    return note.get("text") or post.get("text") or ""


def _capture_x_api(url):
    token = os.environ.get("X_API_BEARER_TOKEN", "").strip()
    match = re.search(r"/status/(\d+)", urllib.parse.urlparse(url).path, re.I)
    if not token or not match:
        return None
    post_id = match.group(1)
    query = urllib.parse.urlencode({
        "tweet.fields": "text,note_tweet,author_id,created_at,referenced_tweets",
        "expansions": "author_id,referenced_tweets.id,referenced_tweets.id.author_id",
        "user.fields": "name,username",
    })
    request = urllib.request.Request(
        f"https://api.x.com/2/tweets/{post_id}?{query}",
        headers={"Authorization": f"Bearer {token}", "User-Agent": "ObsidianTelegramCapture/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.load(response)
        post = payload.get("data")
        if not post:
            return None
        users = {user["id"]: user for user in payload.get("includes", {}).get("users", [])}
        author = users.get(post.get("author_id"), {})
        quoted_ids = [ref["id"] for ref in post.get("referenced_tweets", []) if ref.get("type") == "quoted"]
        included_posts = {item["id"]: item for item in payload.get("includes", {}).get("tweets", [])}
        quote = included_posts.get(quoted_ids[0]) if quoted_ids else None
        quote_author = users.get(quote.get("author_id"), {}) if quote else {}
        quote_handle = quote_author.get("username")
        quote_url = f"https://x.com/{quote_handle}/status/{quote['id']}" if quote and quote_handle else (f"https://x.com/i/status/{quote['id']}" if quote else None)
        post_text = _x_post_text(post)
        quote_text = _x_post_text(quote) if quote else ""
        return {
            "title": (post_text or "X post").splitlines()[0][:80],
            "text": post_text,
            "author": author.get("name") or author.get("username"),
            "handle": author.get("username"),
            "created_at": post.get("created_at"),
            "quote": ({
                "id": quote["id"], "text": quote_text,
                "author": quote_author.get("name") or quote_author.get("username"),
                "handle": quote_handle, "created_at": quote.get("created_at"), "url": quote_url,
            } if quote else None),
            "quote_status": "The quoted post was referenced but could not be retrieved." if quoted_ids and not quote else None,
        }
    except Exception as e:
        print("X API capture failed:", type(e).__name__)
        return None


def _first_line(text, n=80):
    return next((line.strip()[:n] for line in (text or "").splitlines() if line.strip()), "")


def _capture_fxtwitter(url):
    """Full post text (no "…" cut-off) and the quoted post, from the public fxtwitter API; no key needed."""
    match = re.search(r"/([^/]+)/status/(\d+)", urllib.parse.urlparse(url).path, re.I)
    if not match:
        return None
    user, post_id = match.groups()
    request = urllib.request.Request(f"https://api.fxtwitter.com/{user}/status/{post_id}",
                                     headers={"User-Agent": "ObsidianTelegramCapture/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            post = json.load(response).get("tweet")
        if not post:
            return None

        def fields(p):
            author = p.get("author") or {}
            handle = author.get("screen_name")
            return {
                "text": p.get("text") or (p.get("raw_text") or {}).get("text") or "",
                "author": author.get("name") or handle, "handle": handle, "created_at": p.get("created_at"),
                "id": p.get("id"),
                "url": p.get("url") or (f"https://x.com/{handle}/status/{p.get('id')}" if handle else None),
            }

        captured = fields(post)
        quote = post.get("quote")
        captured.update(title=_first_line(captured["text"]) or "X post", quote=fields(quote) if quote else None, quote_status=None)
        return captured
    except Exception as e:
        print("fxtwitter capture failed:", type(e).__name__)
        return None


def capture_x_post(url):
    """Capture a post: X API when configured, then the public fxtwitter API, then X's public embed (text may be cut off)."""
    captured = _capture_x_api(url) or _capture_fxtwitter(url)
    if captured:
        return captured
    try:
        endpoint = "https://publish.x.com/oembed?" + urllib.parse.urlencode({"url": url, "omit_script": "true"})
        request = urllib.request.Request(endpoint, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(request, timeout=15) as response:
            embed = json.load(response)
        blocks = [_clean_embed_html(fragment) for fragment in re.findall(r"<p\b[^>]*>(.*?)</p>", embed.get("html", ""), re.S | re.I)]
        blocks = [block for block in blocks if block]
        text = blocks[0] if blocks else ""
        author = embed.get("author_name")
        first_line = text.splitlines()[0].strip()[:80] if text else ""
        title = first_line or (f"X post by {author}" if author else "X post")
        token_configured = bool(os.environ.get("X_API_BEARER_TOKEN", "").strip())
        return {
            "title": title,
            "text": text,
            "author": author,
            "handle": None,
            "created_at": None,
            "quote": None,
            "quote_status": ("fxtwitter and the X API failed; text may be cut off and quoted-post text may be missing." if token_configured
                             else "fxtwitter was unavailable; text may be cut off and quoted-post text may be missing."),
            "extra_blocks": blocks[1:],
        }
    except Exception as e:
        print("X public embed capture failed:", type(e).__name__)
        return {
            "title": page_title(url) or "X post",
            "text": "",
            "author": None,
            "created_at": None,
            "quote": None,
            "quote_status": "Post text could not be fetched (fxtwitter and X's embed failed); the original URL is preserved.",
        }


def capture_youtube_metadata(url):
    """Fetch metadata only (title, creator, description, chapters, tags...); never download or transcribe the video."""
    try:
        import yt_dlp
    except ImportError:
        return None, "yt-dlp is not installed"
    options = {"skip_download": True, "noplaylist": True, "quiet": True, "no_warnings": True, "socket_timeout": 25}
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
        return info, None
    except Exception as e:
        print("YouTube metadata capture failed:", type(e).__name__, e)
        return None, f"YouTube metadata could not be fetched ({type(e).__name__})"


def capture_instagram(url, stamp):
    """Download temporary audio for an accessible Reel; never retain the media.

    yt-dlp is optional. If Instagram blocks access, the caller still saves the URL.
    Set YTDLP_COOKIES to a Netscape-format cookies file for content requiring login.
    """
    try:
        import yt_dlp
    except ImportError:
        return None, None, "yt-dlp is not installed"

    temp_dir = tempfile.TemporaryDirectory(prefix=f"telegram-reel-{stamp}-")
    options = {
        # Prefer an audio-only stream; fall back to the best available stream
        # and remove it after transcription if the extractor offers no audio-only format.
        "format": "bestaudio/best",
        "outtmpl": str(Path(temp_dir.name) / "reel.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "noprogress": True,
        "no_warnings": True,
        "socket_timeout": 25,
        "max_filesize": 50_000_000,
    }
    cookiefile = os.environ.get("YTDLP_COOKIES")
    if cookiefile and Path(cookiefile).is_file():
        options["cookiefile"] = cookiefile

    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=True)
            media = Path(ydl.prepare_filename(info))
            if not media.exists():
                downloads = info.get("requested_downloads") or []
                media = Path(downloads[0]["filepath"]) if downloads and downloads[0].get("filepath") else media
            if not media.exists():
                temp_dir.cleanup()
                return None, info, "yt-dlp finished but its temporary media file was not found"

        return media, {"info": info, "temp_dir": temp_dir}, None
    except Exception as e:
        temp_dir.cleanup()
        print("Instagram capture failed:", type(e).__name__, e)
        return None, None, f"Instagram media could not be downloaded ({type(e).__name__})"


def instagram_metadata(url):
    """Reel/TikTok metadata without downloading media (used when the audio download fails)."""
    try:
        import yt_dlp
        options = {"skip_download": True, "quiet": True, "no_warnings": True, "socket_timeout": 25}
        cookiefile = os.environ.get("YTDLP_COOKIES")
        if cookiefile and Path(cookiefile).is_file():
            options["cookiefile"] = cookiefile
        with yt_dlp.YoutubeDL(options) as ydl:
            return ydl.extract_info(url, download=False)
    except Exception as e:
        print("Instagram metadata failed:", type(e).__name__, e)
        return None


def _clock(seconds):
    s = int(seconds or 0)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def _handle(info):
    """@username across platforms: YouTube uploader_id, TikTok uploader_url, Instagram channel."""
    for key in ("uploader_id", "uploader_url"):
        m = re.search(r"@([\w.\-]+)", str(info.get(key) or ""))
        if m and not m.group(1).startswith("MS4w"):  # TikTok internal ids look like @MS4w...
            return m.group(1)
    if info.get("extractor_key") == "Instagram":
        return info.get("channel")
    return None


def video_metadata_section(info):
    """Everything useful to describe or classify a Reel, TikTok or YouTube video, without the media itself.

    The caption/description is kept verbatim and in full; generic titles ("Video by x") are skipped.
    """
    if not info:
        return ""
    lines = []
    title = info.get("title") or ""
    if title and not re.match(r"^(Video by |TikTok video #)", title):
        lines.append(f"**Title:** {title}")
    creator, handle = info.get("uploader") or info.get("channel"), _handle(info)
    if creator or handle:
        who = creator or f"@{handle}"
        if handle and handle != creator:
            who += f" (@{handle})"
        url = info.get("uploader_url") or info.get("channel_url")
        lines.append(f"**Creator:** {who}" + (f" · {url}" if url and "@MS4w" not in url else ""))
    date = info.get("upload_date")
    if date:
        lines.append(f"**Published:** {date[:4]}-{date[4:6]}-{date[6:8]}")
    if info.get("duration"):
        lines.append(f"**Duration:** {_clock(info['duration'])}")
    music = " — ".join(x for x in (info.get("track"), info.get("artist")) if x)
    if music:
        lines.append(f"**Music:** {music}")
    for label, key in (("Language", "language"), ("Location", "location"), ("Categories", "categories")):
        v = info.get(key)
        if v:
            lines.append(f"**{label}:** {', '.join(v) if isinstance(v, list) else v}")
    if info.get("tags"):
        lines.append(f"**Tags:** {', '.join(info['tags'][:40])}")
    caption = info.get("description") or ""
    hashtags = list(dict.fromkeys(re.findall(r"#(?=\w*[^\W\d])\w+", f"{title} {caption}")))  # not "#123"
    if hashtags:
        lines.append(f"**Hashtags:** {' '.join(hashtags)}")
    stats = [f"{info[k]:,} {n}" for k, n in (("view_count", "views"), ("like_count", "likes"), ("comment_count", "comments"))
             if isinstance(info.get(k), int)]
    if stats:
        lines.append(f"**Engagement:** {' · '.join(stats)}")
    section = "\n## Metadata\n" + "".join(f"- {l}\n" for l in lines)
    chapters = info.get("chapters") or []
    if chapters:
        section += "\n### Chapters\n" + "".join(f"- {_clock(c.get('start_time'))} {c.get('title', '')}\n" for c in chapters)
    if re.sub(r"#\w+", "", caption).strip():  # skip when the caption is only hashtags (already listed)
        section += f"\n### Caption\n{caption.strip()}\n"
    return section


def safe(s, n=60):
    s = re.sub(r'[\/:*?"<>|#^\[\]\n\r]', " ", s)
    return " ".join(s.split())[:n].strip() or "note"


def write_note(title, body, source="telegram", tags=("telegram",)):
    now = datetime.now()
    INBOX.mkdir(exist_ok=True)
    path = INBOX / f"{now:%Y-%m-%d %H%M} {safe(title)}.md"
    i = 2
    while path.exists():
        path = INBOX / f"{now:%Y-%m-%d %H%M} {safe(title)} {i}.md"
        i += 1
    source_value = json.dumps(source, ensure_ascii=False)
    tag_values = ", ".join(dict.fromkeys(["telegram", "raw", *tags]))
    fm = (
        f"---" + chr(10) + f"created: {now:%Y-%m-%d}" + chr(10) + "type: source" + chr(10) + "status: inbox" + chr(10)
        + f"tags: [{tag_values}]" + chr(10) + f"source: {source_value}" + chr(10) + "---" + chr(10)
    )
    summary = "Raw Telegram capture awaiting classification; the captured content below is preserved."
    path.write_text(fm + summary + chr(10) + chr(10) + body + chr(10), encoding="utf-8")
    return path


def handle(msg):
    chat = msg["chat"]["id"]
    uid = msg.get("from", {}).get("id")
    if uid not in ALLOWED:
        reply(chat, f"Not authorised. Your Telegram id is {uid}. Add it to TELEGRAM_ALLOWED_IDS in scripts/.env.")
        return
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    text = msg.get("text") or msg.get("caption") or ""
    media = None
    for kind in ("voice", "audio", "video_note", "video", "document"):
        if kind in msg:
            media = (kind, msg[kind])
            break
    if "photo" in msg:
        media = ("photo", msg["photo"][-1])

    status = None       # "working…" message, edited into the result
    check = text        # the user's own words, checked for secrets before saving
    if media:
        kind, m = media
        if m.get("file_size", 0) > 20_000_000:
            reply(chat, "File over 20 MB: Telegram bots cannot download it.")
            return
        if kind in AUDIO_KINDS:
            status = reply(chat, "⏳ Transcribing…")
        f = download(m["file_id"], STAGE, f"tg-{stamp}")
        body = f"![[{f.name}]]\n"
        title = {"voice": "Voice note", "audio": "Audio", "video_note": "Video note"}.get(kind, kind.capitalize())
        tags = ["telegram", kind]
        if kind in AUDIO_KINDS:
            tr, language = transcribe(f, allowed_languages=("es", "en"))
            if tr:
                body += f"\n## Transcript\n{tr}\n"
                title = tr
                check += "\n" + tr
                if language == "en" and kind in ("voice", "video_note"):
                    tags.append("english-practice")  # check-in for Daily Speaking Practice
            else:
                body += "\n_Not transcribed (install faster-whisper to enable)._\n"
        if kind == "photo" or (kind == "document" and str(m.get("mime_type", "")).startswith("image/")):
            check += "\n" + ocr_text(f)
        if text:
            body += f"\n{text}\n"
            title = text
        item = {"title": title, "body": body, "source": "telegram", "tags": tags, "files": [f]}
    else:
        urls = URL_RE.findall(text)
        short_video_links = [url for url in urls if instagram_url(url) or tiktok_url(url)]
        if short_video_links:  # Instagram Reels and TikToks: metadata + temporary-audio transcript
            url = short_video_links[0]
            platform = "tiktok" if tiktok_url(url) else "instagram"
            status = reply(chat, "⏳ Fetching the video and transcribing…")
            media, capture, error = capture_instagram(url, stamp)
            info = capture.get("info", capture) if capture else instagram_metadata(url)
            title = (info or {}).get("title") or page_title(url) or ("TikTok" if platform == "tiktok" else "Instagram Reel")
            if title.startswith("TikTok video #") and info and _handle(info):
                title = f"TikTok by {_handle(info)}"
            body = text + "\n" + video_metadata_section(info)
            if media:
                try:
                    transcript, _ = transcribe(media)
                finally:
                    capture["temp_dir"].cleanup()
                if transcript:
                    body += f"\n## Transcript\n{transcript}\n"
                else:
                    body += "\n_Transcript was not generated (faster-whisper is unavailable or transcription failed). The temporary media was discarded; the original link is preserved above._\n"
            else:
                body += f"\n## Capture status\n{error}. The original link is preserved above.\n"
            item = {"title": title, "body": body, "source": url, "tags": ["telegram", "link", platform]}
        elif len(urls) == 1 and youtube_url(urls[0]):
            url = urls[0]
            metadata, error = capture_youtube_metadata(url)
            title = (metadata or {}).get("title") or page_title(url) or "YouTube video"
            body = text + "\n"
            if metadata:
                body += video_metadata_section(metadata)
            else:
                body += f"\n## Metadata status\n{error}. The original link is preserved above.\n"
            item = {"title": title, "body": body, "source": url, "tags": ["telegram", "link", "youtube"]}
        elif len(urls) == 1 and x_post_url(urls[0]):
            url = urls[0]
            post = capture_x_post(url)
            title = post.get("title") or "X post"
            body = text + "\n\n## Original post\n"
            if post.get("author"):
                body += f"\n**Author:** {post['author']}"
                if post.get("handle"):
                    body += f" (@{post['handle']})"
                body += "\n"
            if post.get("created_at"):
                body += f"**Published:** {post['created_at']}\n"
            body += "\n" + (post.get("text") or "Post text could not be extracted from the public embed.") + "\n"
            if post.get("extra_blocks"):
                body += "\n## Additional text in X embed\n\n" + "\n\n".join(post["extra_blocks"]) + "\n"
            quote = post.get("quote")
            if quote:
                body += "\n## Quoted post\n\n"
                body += f"**URL:** {quote['url']}\n"
                if quote.get("author"):
                    body += f"**Author:** {quote['author']}" + (f" (@{quote['handle']})" if quote.get("handle") else "") + "\n"
                if quote.get("created_at"):
                    body += f"**Published:** {quote['created_at']}\n"
                body += "\n" + (quote.get("text") or "") + "\n"
            elif post.get("quote_status"):
                body += f"\n## Quote capture status\n{post['quote_status']}\n"
            item = {"title": title, "body": body, "source": url, "tags": ["telegram", "link", "x"]}
        elif urls and len(text.strip()) == len(urls[0]) and len(urls) == 1:
            t = page_title(urls[0]) or urls[0]
            item = {"title": t, "body": text, "source": urls[0], "tags": ["telegram", "link"]}
        elif urls:
            item = {"title": text.split("\n")[0], "body": text, "source": urls[0], "tags": ["telegram", "link"]}
        else:
            item = {"title": text.split("\n")[0] or "Note", "body": text, "source": "telegram", "tags": ["telegram"]}
    finish(chat, item, check, status)


def save(item):
    for f in item.get("files", []):
        ATTACH.mkdir(parents=True, exist_ok=True)
        shutil.move(str(f), str(ATTACH / f.name))
    p = write_note(item["title"], item["body"], source=item["source"], tags=item["tags"])
    print("saved", p)
    return p


def discard(item):
    for f in item.get("files", []):
        Path(f).unlink(missing_ok=True)


def finish(chat, item, check, status=None):
    """Save the capture, or hold it and ask first when the user's text, transcript or photo looks like it holds a secret."""
    hits = sensitive_hits(check)
    if not hits:
        reply(chat, f"Saved: {save(item).name}", status)
        return
    token = secrets.token_hex(6)
    PENDING[token] = {**item, "time": time.time()}
    reply(chat, f"⚠️ This looks like it contains {', '.join(hits)}. Saving puts it in your vault and its Google Drive "
                "backup; a password manager is a safer place. Save it anyway?",
          status, buttons=[("Save anyway", f"save:{token}"), ("Discard", f"drop:{token}")])
    print("held for confirmation:", ", ".join(hits))


def handle_button(query):
    """Save or Discard tapped on a held capture."""
    message = query.get("message") or {}
    chat, message_id = message.get("chat", {}).get("id"), message.get("message_id")
    try:
        api("answerCallbackQuery", callback_query_id=query["id"])
    except Exception as e:
        print("answerCallbackQuery failed:", e)
    if query.get("from", {}).get("id") not in ALLOWED or not chat:
        return
    action, _, token = (query.get("data") or "").partition(":")
    item = PENDING.pop(token, None)
    if not item:
        reply(chat, "This capture is no longer held (the bot restarted or 24 h passed). Send it again if you want it.", message_id)
    elif action == "save":
        item["tags"] = [*item["tags"], "sensitive"]  # agents leave #sensitive captures in the Inbox (AGENTS.md rule 8)
        reply(chat, f"Saved, tagged #sensitive: {save(item).name}", message_id)
    else:
        discard(item)
        reply(chat, "Discarded. Nothing was saved.", message_id)


def expire_pending():
    for token, item in list(PENDING.items()):
        if time.time() - item["time"] > PENDING_TTL:
            discard(PENDING.pop(token))


def main():
    if not TOKEN:
        sys.exit("Set TELEGRAM_BOT_TOKEN in scripts/.env")
    print("Bot running. Ctrl+C to stop. Allowed ids:", ALLOWED or "none (setup mode)")
    shutil.rmtree(STAGE, ignore_errors=True)  # media of captures held before a restart
    if os.environ.get("WHISPER_PRELOAD", "1") != "0":
        threading.Thread(target=preload_whisper, daemon=True).start()
    offset_file = Path(__file__).with_name("telegram.offset")
    try:
        offset = int(offset_file.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        offset = None
    while True:
        try:
            beat()
            expire_pending()
            params = {"timeout": 50, "allowed_updates": json.dumps(["message", "callback_query"])}
            if offset:
                params["offset"] = offset
            for u in api("getUpdates", **params):
                if "message" in u:
                    try:
                        handle(u["message"])
                    except Exception as e:
                        print("error:", e)
                        reply(u["message"]["chat"]["id"], f"Error: {e}")
                elif "callback_query" in u:
                    try:
                        handle_button(u["callback_query"])
                    except Exception as e:
                        print("button error:", e)
                beat()
                offset = u["update_id"] + 1
                tmp_offset = offset_file.with_suffix(".offset.tmp")
                tmp_offset.write_text(str(offset), encoding="utf-8")
                tmp_offset.replace(offset_file)
        except KeyboardInterrupt:
            break
        except Exception as e:
            print("poll error:", e)
            time.sleep(5)


if __name__ == "__main__":
    main()
