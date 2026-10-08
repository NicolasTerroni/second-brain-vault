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
  BRIEF_TIME=08:00              morning brief, in the bot's TZ ("off" disables it)
  REVIEW_TIME=Sun 18:00         weekly review prompt ("off" disables it)
Messages that look like they hold secrets (PIN, password, card number, token...) are held until you tap Save or Discard.
Replying to a "Saved" message adds to that same note. /help lists the commands (tasks, questions, standup, brief...);
their vault logic is in companion.py.
Run: python scripts/telegram_bot.py
"""
import json, os, re, sys, time, html, secrets, shutil, tempfile, threading, urllib.request, urllib.parse
from datetime import date, datetime, timedelta
from pathlib import Path
import botkit
import coach
import companion
import habits
import team

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
                v = v.strip()
                if not v.startswith(("'", '"')):
                    v = re.split(r"\s+#", v, maxsplit=1)[0]  # inline comment, as in .env.example
                os.environ.setdefault(k.strip(), v.strip().strip('"\''))


load_env()
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
ALLOWED_LIST = [int(x) for x in os.environ.get("TELEGRAM_ALLOWED_IDS", "").replace(" ", "").split(",") if x]
ALLOWED = set(ALLOWED_LIST)
OWNER = ALLOWED_LIST[0] if ALLOWED_LIST else None   # private chat that gets the brief and the weekly review
BRIEF_TIME = os.environ.get("BRIEF_TIME", "08:00").strip()
REVIEW_TIME = os.environ.get("REVIEW_TIME", "Sun 18:00").strip()
API = f"https://api.telegram.org/bot{TOKEN}"
_whisper = None
_whisper_lock = threading.Lock()
STATE = companion.load_state()   # bot message id -> what a reply to it means; last brief/review sent
AREAS = [[("Training", "area:training"), ("English", "area:english"), ("Career", "area:career"), ("Discipline", "area:discipline")],
         [("Soft skills", "area:soft-skills"), ("Software & AI", "area:software-ai"), ("About me", "area:about-me")]]


def api(method, **params):
    data = urllib.parse.urlencode(params).encode()
    with urllib.request.urlopen(urllib.request.Request(f"{API}/{method}", data=data), timeout=70) as r:
        return json.load(r)["result"]


def reply(chat, text, message_id=None, buttons=None):
    """Send a message, or edit `message_id` in place (a status line becoming the result). Returns the message id.
    `buttons`: one row [(label, data), ...] or several rows [[...], [...]]."""
    params = {"chat_id": chat, "text": text}
    if buttons:
        rows = buttons if isinstance(buttons[0], list) else [buttons]
        params["reply_markup"] = json.dumps({"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in row] for row in rows]})
    try:
        if message_id:
            api("editMessageText", message_id=message_id, **params)
            return message_id
        return api("sendMessage", **params)["message_id"]
    except Exception as e:
        print("reply failed:", e)
        return None


def send_document(chat, path, caption=None, buttons=None):
    """Send a vault file as a Telegram document, preserving the original Markdown and wikilinks."""
    boundary = "----VaultBot" + secrets.token_hex(16)
    fields = [("chat_id", str(chat))]
    if caption:
        fields.append(("caption", caption))
    if buttons:
        rows = buttons if isinstance(buttons[0], list) else [buttons]
        markup = {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in row] for row in rows]}
        fields.append(("reply_markup", json.dumps(markup, ensure_ascii=False)))
    body = bytearray()
    for name, value in fields:
        body.extend(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode("utf-8"))
    path = Path(path)
    content_type = "text/markdown"
    body.extend(f"--{boundary}\r\nContent-Disposition: form-data; name=\"document\"; filename=\"{path.name}\"\r\n"
                f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"))
    body.extend(path.read_bytes())
    body.extend(f"\r\n--{boundary}--\r\n".encode("ascii"))
    request = urllib.request.Request(f"{API}/sendDocument", data=bytes(body), method="POST",
                                     headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(request, timeout=70) as response:
            result = json.load(response)
        return result["result"]["message_id"] if result.get("ok") else None
    except Exception:
        print("document send failed")
        return None


def remember(message_id, context):
    """What a reply to this bot message means: add to a capture, answer a question, a standup or the weekly review."""
    if not message_id:
        return
    replies = STATE.setdefault("replies", {})
    replies[str(message_id)] = context
    for key in list(replies)[:-300]:  # keep the last 300
        del replies[key]
    companion.save_state(STATE)


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
    if msg.get("text", "").startswith("/"):
        command(chat, msg["text"])
        return
    context = STATE.get("replies", {}).get(str((msg.get("reply_to_message") or {}).get("message_id")))
    media = None
    for kind in ("voice", "audio", "video_note", "video", "document"):
        if kind in msg:
            media = (kind, msg[kind])
            break
    if "photo" in msg:
        media = ("photo", msg["photo"][-1])
    if context and context.get("kind") == "reason":
        save_reason(chat, context, text, media, stamp)
        return
    if not context and not media and quick_log(chat, text):
        return

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
    if context:
        apply_context(item, context)
    finish(chat, item, check, status)
    auto_tick(chat, item)


# ---------- habit tracker: log, tick, reasons ----------
def quick_log(chat, text):
    """A short message naming one habit ("water 500", "did mobility") logs it in the app. True when it did."""
    if not text or not habits.configured():
        return False
    rep = coach.report()
    found = coach.parse(text, rep) if rep else None
    if not found:
        return False
    s, value = found
    try:
        entry, created = coach.log(s, value, note=None)
    except Exception as e:
        reply(chat, f"⚠️ Couldn't log {s['name']} in the app ({e}). Saving your message as a note instead.")
        return False
    buttons = None
    if created and entry:
        logged = STATE.setdefault("logged", {})
        logged[entry["id"]] = text
        for k in list(logged)[:-50]:
            del logged[k]
        companion.save_state(STATE)
        buttons = [("↩️ Undo", f"hu:{entry['id']}"), ("📝 It was a note", f"hn:{entry['id']}")]
    reply(chat, coach.logged_text(s, value, created), buttons=buttons)
    if created:
        announce_unlock(chat)
    return True


def announce_unlock(chat):
    """After a habit is logged, say so once a day when it unlocked the apps (the app's focus lock)."""
    status = habits.lock_status()
    if status and not status["locked"] and status.get("goalMet") and STATE.get("unlocked_day") != status.get("date"):
        STATE["unlocked_day"] = status.get("date")
        companion.save_state(STATE)
        reply(chat, habits.lock_line(status))


def auto_tick(chat, item):
    """A Workout note ticks the strength habit; an English voice note ticks the English one."""
    if not habits.configured():
        return
    words = f"{item.get('title', '')}\n{item.get('body', '')}".lstrip().lower()
    alias = "workout" if re.match(r"(?:!\[\[[^\]]*\]\]\s*(?:## transcript\s*)?)?workout\b", words) else \
        "#english-practice" if "english-practice" in item.get("tags", []) else None
    if not alias:
        return
    try:
        s = coach.by_alias(alias, coach.report())
        if s and not s["done_today"]:
            entry, created = coach.log(s, 1)
            if created:
                reply(chat, coach.logged_text(s, 1, created) + " (from your note)")
                announce_unlock(chat)
    except Exception as e:
        print("auto tick failed:", e)


def save_reason(chat, context, text, media, stamp):
    """A reply to "what got in the way?": text, or a voice note transcribed, goes to Habit Reasons."""
    said = text
    if media and media[0] in AUDIO_KINDS:
        f = download(media[1]["file_id"], STAGE, f"tg-{stamp}")
        try:
            said, _ = transcribe(f, allowed_languages=("es", "en"))
        finally:
            Path(f).unlink(missing_ok=True)
    if not said or not said.strip():
        reply(chat, "I couldn't get any words from that. Reply again with text?")
        return
    if sensitive_hits(said):
        reply(chat, "That looks like it holds a secret, so I didn't save it.")
        return
    coach.add_reason(context["habits"], said, context.get("type", "skipped"), date.fromisoformat(context["day"]))
    reply(chat, f"🗣 Noted under {', '.join(context['habits'])} in Habit Reasons. The weekly review uses it to pick the next fix.")


def apply_context(item, context):
    """A reply to one of the bot's messages: add to that capture, or file it as an answer, standup or weekly review."""
    kind = context.get("kind")
    if kind == "capture":
        item["append_to"] = context["path"]
    elif kind == "question":
        n = context["n"]
        item.update(title=f"Answer to Question {n}", body=f"Answer to **Question {n}:** {context['text']}\n\n{item['body']}")
        item["tags"] = [*item["tags"], "about-me", f"question-{n}"]
    elif kind == "standup":
        item.update(title=f"Standup {date.today().isoformat()}",
                    body="Daily standup: what I did at work today. English corrections come at the next organize.\n\n" + item["body"])
        item["tags"] = [*item["tags"], "standup"]
    elif kind == "review":
        week = date.today().isocalendar()
        item.update(title=f"Weekly review {week[0]}-W{week[1]:02d}", body="Weekly review (Discipline).\n\n" + item["body"])
        item["tags"] = [*item["tags"], "weekly-review"]


def add_tags(path, tags):
    """Add tags to a note's inline `tags: [...]` frontmatter list."""
    text = path.read_text(encoding="utf-8")
    m = re.search(r"^tags: \[(.*)\]$", text, re.M)
    if not m:
        return
    current = [t.strip() for t in m.group(1).split(",") if t.strip()]
    merged = ", ".join(dict.fromkeys([*current, *tags]))
    path.write_text(text[:m.start()] + f"tags: [{merged}]" + text[m.end():], encoding="utf-8")


def save(item):
    """Write the capture. Returns (path, appended): appended when it was added to an earlier capture still in the Inbox."""
    for f in item.get("files", []):
        ATTACH.mkdir(parents=True, exist_ok=True)
        shutil.move(str(f), str(ATTACH / f.name))
    target = ROOT / item["append_to"] if item.get("append_to") else None
    if target and target.exists() and INBOX in target.parents:
        with target.open("a", encoding="utf-8") as f:
            f.write(f"\n## Added {datetime.now():%Y-%m-%d %H:%M}\n{item['body'].strip()}\n")
        add_tags(target, [t for t in item["tags"] if t not in ("telegram", "raw")])
        print("appended to", target)
        return target, True
    if target:  # the capture was already organized: raw sources are immutable, so make a linked new capture
        item = {**item, "body": f"Comment on [[{target.stem}]]:\n\n{item['body']}", "tags": [*item["tags"], "comment"]}
    p = write_note(item["title"], item["body"], source=item["source"], tags=item["tags"])
    print("saved", p)
    return p, False


def announce(chat, saved, status=None, note=""):
    """Tell the user where the capture went; replies to this message add to it."""
    path, appended = saved
    if appended:
        text = f"➕ Added to: {path.name}{note}\n↩️ Reply again to keep adding to it."
        buttons = None
    else:
        text = (f"✅ Saved: {path.name}{note}\n↩️ Reply to this message to add more to this same note (text, voice, photo). "
                "Anything sent without replying becomes a new note.\nArea? (optional, tap one or more)")
        buttons = AREAS
    message_id = reply(chat, text, status, buttons=buttons)
    remember(message_id, {"kind": "capture", "path": path.relative_to(ROOT).as_posix()})


def discard(item):
    for f in item.get("files", []):
        Path(f).unlink(missing_ok=True)


def finish(chat, item, check, status=None):
    """Save the capture, or hold it and ask first when the user's text, transcript or photo looks like it holds a secret."""
    hits = sensitive_hits(check)
    if not hits:
        announce(chat, save(item), status)
        return
    token = secrets.token_hex(6)
    PENDING[token] = {**item, "time": time.time()}
    reply(chat, f"⚠️ This looks like it contains {', '.join(hits)}. Saving puts it in your vault and its Google Drive "
                "backup; a password manager is a safer place. Save it anyway?",
          status, buttons=[("Save anyway", f"save:{token}"), ("Discard", f"drop:{token}")])
    print("held for confirmation:", ", ".join(hits))


def handle_button(query):
    """Inline buttons: Save/Discard a held capture, tag an area, skip a question, start a standup, tick a task."""
    message = query.get("message") or {}
    chat, message_id = message.get("chat", {}).get("id"), message.get("message_id")
    try:
        api("answerCallbackQuery", callback_query_id=query["id"])
    except Exception as e:
        print("answerCallbackQuery failed:", e)
    if query.get("from", {}).get("id") not in ALLOWED or not chat:
        return
    action, _, arg = (query.get("data") or "").partition(":")
    if action in ("save", "drop"):
        item = PENDING.pop(arg, None)
        if not item:
            reply(chat, "This capture is no longer held (the bot restarted or 24 h passed). Send it again if you want it.", message_id)
        elif action == "save":
            item["tags"] = [*item["tags"], "sensitive"]  # agents leave #sensitive captures in the Inbox (AGENTS.md rule 8)
            announce(chat, save(item), message_id, note=" (tagged #sensitive)")
        else:
            discard(item)
            reply(chat, "Discarded. Nothing was saved.", message_id)
    elif action == "area":
        context = STATE.get("replies", {}).get(str(message_id), {})
        path = ROOT / context.get("path", "")
        tag = f"area/{arg}"
        if context.get("kind") != "capture" or not path.is_file() or INBOX not in path.parents:
            reply(chat, "That note has already been organized, so its tags can't change from here.")
            return
        add_tags(path, [tag])
        text = message.get("text", "")
        if f"#{tag}" not in text:
            reply(chat, f"{text}\n🏷 #{tag}", message_id, buttons=AREAS)
    elif action == "q":
        send_question(chat, after=int(arg), message_id=message_id)
    elif action == "standup":
        send_standup(chat)
    elif action == "done":
        task = STATE.get("done_choices", {}).get(arg)
        if task and companion.tick_task(int(arg), task):
            reply(chat, f"✅ Done: {companion.task_title(task)}", message_id)
        else:
            reply(chat, "That task changed or was already ticked. Try /done again.", message_id)
    elif action == "cmd" and arg in ("train", "habits", "todo", "q", "goals", "team"):
        command(chat, f"/{arg}")
    elif action in ("hd", "hs"):
        coach.report()
        s = coach.find(arg)
        if not s:
            reply(chat, "That habit isn't in the app anymore, or the app can't be reached right now.")
        elif action == "hd":
            try:
                value, _ = habits.remaining(s)
                entry, created = coach.log(s, value)
                buttons = [("↩️ Undo", f"hu:{entry['id']}")] if created and entry else None
                reply(chat, coach.logged_text(s, value, created), buttons=buttons)
            except Exception as e:
                reply(chat, f"⚠️ Couldn't log it in the app: {e}")
        else:
            coach.skip(STATE, s["id"])
            companion.save_state(STATE)
            sent = reply(chat, f"⏭ {s['icon']} {s['name']} skipped today, so no more reminders for it.\n"
                               "↩️ What got in the way? Reply to this message (voice is fine). It's how the fixes get better.")
            remember(sent, {"kind": "reason", "habits": [s["name"]], "type": "skipped", "day": date.today().isoformat()})
    elif action == "hz":
        coach.snooze(STATE)
        companion.save_state(STATE)
        reply(chat, f"😴 Quiet for {coach.SNOOZE_HOURS} hours. Then I'll ask again.", message_id)
    elif action in ("hu", "hn"):
        try:
            habits.undo_entry(arg)
            coach._cache["at"] = 0  # re-read the app next time
            text = STATE.get("logged", {}).pop(arg, None)
            companion.save_state(STATE)
            if action == "hn" and text:
                finish(chat, {"title": text.split("\n")[0], "body": text, "source": "telegram", "tags": ["telegram"]}, text)
            else:
                reply(chat, "↩️ Undone: removed from the app.", message_id)
        except Exception as e:
            reply(chat, f"⚠️ Couldn't undo it in the app: {e}")
    elif action == "rv":
        proposal = STATE.pop("review_proposal", None)
        companion.save_state(STATE)
        if proposal:
            coach.approve(proposal)
            reply(chat, f"✅ Next week's one change: {proposal['habit']}. It's in your Discipline review log.", message_id)
        else:
            reply(chat, "That proposal was already handled.", message_id)


# ---------- commands ----------
HELP = """🤖 I'm your assistant: your vault, goals, tasks and daily habits. Training is your Coach's job ({coach}) and English your Teacher's ({teacher}); I keep the three of us in sync.

📥 Send anything (text, link, voice, photo, file): it lands in your Inbox. Reply to a "Saved" message to add to it.
✅ Log a habit by saying it: "water 500", "did mobility", "read 20 min".
⏰ I push the daily habits: after your app's reminder I nag about what's still open, then a check-in at night. ⏭ Skip asks what got in the way.
☀️ Every morning: one habit focus, a goal, this week's top task, deadlines and what your Coach and Teacher have planned. Sunday: the review.

🎯 /goals · 📌 /todo, /add, /done · 👥 /team · ❓ /ask <question> (answers from your whole vault) · 🔒 /lock · /habits · /review · /status · /backup

⏰ {schedule}"""

SOON = {
    "/organize": "🔜 /organize is coming soon.\nIt will process the Inbox the way you now ask for it in chat: file each raw capture, "
                 "write the distilled notes, add English corrections from standups, update the indexes and the log, and send you a "
                 "summary. It will ask for confirmation first, because it spends tokens.",
}

COMMANDS = [("brief", "Today: focus, goals, team"), ("todo", "This week's tasks"), ("goals", "Your goals"),
            ("team", "What your Coach and Teacher have today"), ("ask", "Ask anything about your vault"),
            ("habits", "This week's habits"), ("review", "Weekly review now"), ("help", "How I work")]
ASSISTANT_NOTE = ROOT / "02 - Areas" / "About Me" / "Assistant.md"
ASSIST = botkit.Bot("Assistant", "TELEGRAM_BOT_TOKEN", ASSISTANT_NOTE, "assistant")  # only its Claude Code voice is used


def answer_question(chat, question, status):
    """/ask: Claude Code reads the vault (read-only) and answers with the notes it used."""
    text = ASSIST.claude(f"My question: {question}\nAnswer from my vault: read index.md first, then the notes that matter. "
                         "Reply in under 150 words, plain text, and end with the note names you used. If the vault doesn't say, "
                         "say so; don't invent.", tools=True, model="sonnet", timeout=300)
    reply(chat, text or "I couldn't get an answer this time. Try again in a minute.", status)


def schedule_text():
    parts = []
    if BRIEF_TIME.lower() != "off":
        parts.append(f"Every morning at {BRIEF_TIME}: your brief.")
    if REVIEW_TIME.lower() != "off":
        parts.append(f"{REVIEW_TIME.split()[0]}days at {REVIEW_TIME.split()[-1]}: the weekly review.")
    if habits.configured() and coach.NAG_EVERY > 0:
        parts.append(f"Habit nags every {coach.NAG_EVERY} min after each app reminder until {coach.NAG_UNTIL}; check-in at {coach.CHECKIN_TIME}.")
    return " ".join(parts) or "No scheduled messages."


def send_question(chat, after=None, message_id=None):
    q = companion.next_question(after)
    if not q:
        reply(chat, "🎉 No open questions (answers waiting in the Inbox count as answered).", message_id)
        return
    n, text, _ = q
    sent = reply(chat, f"❓ Question {n}\n{text}\n\n↩️ Reply to this message to answer. An English voice note is best: "
                       "it's also your speaking practice.", message_id,
                 buttons=[("⏭ Another question", f"q:{n}"), ("🗣 Today's standup instead", "standup")])
    remember(sent, {"kind": "question", "n": n, "text": text})


def send_standup(chat):
    sent = reply(chat, "🗣 Today's standup\n↩️ Reply to this message with an English voice note: what you did at work today, "
                       "what blocked you, what's next. I'll transcribe it now, and the agent adds corrections to Daily Speaking "
                       "Practice at the next organize.")
    remember(sent, {"kind": "standup"})


def habit_report():
    """Fresh consistency report from the habit tracker app (also rewrites Habit Consistency.md), or None."""
    if not habits.configured():
        return None
    try:
        return coach.report(max_age=60)
    except Exception as e:  # never let the app break the brief or the review
        print("habit report failed:", e)
        return None


BRIEF_BUTTONS = [[("📌 Tasks", "cmd:todo"), ("🎯 Goals", "cmd:goals")], [("📊 Habits", "cmd:habits"), ("👥 Team", "cmd:team")]]


def send_brief(chat):
    """The morning brief. Replying to it answers "what got in the way?" for yesterday's misses."""
    rep = habit_report()
    owned = team.owned_aliases()  # Strength and English belong to the Coach and the Teacher when they're on
    view = dict(rep, habits=[s for s in rep["habits"] if not owned & set(s["aliases"])]) if rep else None
    lines = habits.brief_lines(view) if view else None
    lock = habits.lock_status() if rep else None
    if lock:
        lines.append(habits.lock_line(lock))
    team_lines = team.lines()
    sent = reply(chat, companion.brief_text(habit_lines=lines, team_lines=team_lines)
                 + ("\n\n↩️ Missed something yesterday? Reply with what got in the way." if rep else ""), buttons=BRIEF_BUTTONS)
    if companion.ROUTINE.is_file() and not team.on("Coach"):
        send_document(chat, companion.ROUTINE, caption=companion.training_caption())
    if rep:
        yesterday = rep["today"] - timedelta(days=1)
        missed = [s["name"] for s in rep["habits"] if s["per_week"] == 7 and s["active_yesterday"] and not s["done_yesterday"]]
        if missed:
            remember(sent, {"kind": "reason", "habits": missed, "type": "missed", "day": yesterday.isoformat()})
    return sent


def send_review(chat):
    rep = habit_report()
    proposal, buttons, lines = None, None, None
    if rep:
        lines, proposal = coach.review_lines(rep)
        if team.on("Coach") or team.on("Teacher"):
            lines = lines + ["", "👥 Training and English: your Coach and Teacher send their own Sunday reports."]
    if proposal:
        STATE["review_proposal"] = proposal
        companion.save_state(STATE)
        buttons = [("✅ Use this change", "rv:")]
    remember(reply(chat, companion.review_text(habit_lines=lines), buttons=buttons), {"kind": "review"})


def send_coach(chat):
    """Nags and the evening check-in from coach.tick."""
    for text, buttons, attach_routine in coach.tick(STATE):
        if attach_routine and companion.ROUTINE.is_file():
            sent = send_document(chat, companion.ROUTINE, caption=text, buttons=buttons)
            if not sent:
                reply(chat, "I couldn't attach the Obsidian routine note. Please try /train.")
        else:
            reply(chat, text, buttons=buttons)
    companion.save_state(STATE)


def command(chat, text):
    cmd, _, arg = text.strip().partition(" ")
    cmd, arg = cmd.split("@")[0].lower(), arg.strip()
    if cmd in ("/start", "/help"):
        reply(chat, HELP.format(schedule=schedule_text(), coach=team.handle("Coach"), teacher=team.handle("Teacher")))
    elif cmd in SOON:
        reply(chat, SOON[cmd])
    elif cmd == "/todo":
        groups = {}  # every open task, titles only, by section
        for _, section, text, _ in companion.open_tasks():
            groups.setdefault(section.lstrip("# ").strip(), []).append(f"• {companion.task_title(text)}")
        parts = [f"{name}\n" + "\n".join(items) for name, items in groups.items()]
        reply(chat, "📌 Your tasks\n\n" + ("\n\n".join(parts) if parts else "Nothing pending. 🎉")
              + "\n\nTick one: /done <words>")
    elif cmd == "/add":
        if not arg:
            reply(chat, "Usage: /add <task>, e.g. /add book the C1 exam")
        elif sensitive_hits(arg):
            reply(chat, "That looks like it contains a secret. Keep it in a password manager, not in a task.")
        elif companion.add_task(arg):
            reply(chat, f"📌 Added to Pending Tasks: {arg}\nThe agent files it into the right section at the next organize.")
        else:
            reply(chat, "Pending Tasks note not found.")
    elif cmd == "/done":
        matches = companion.find_tasks(arg) if arg else companion.this_week()
        if arg and len(matches) == 1:
            line, _, task, _ = matches[0]
            ok = companion.tick_task(line, task)
            reply(chat, f"✅ Done: {companion.task_title(task)}" if ok else "That task just changed. Try again.")
        elif not matches:
            reply(chat, f"No open task matches \"{arg}\". /todo lists them." if arg else "Nothing open this week. 🎉")
        else:
            STATE["done_choices"] = {str(t[0]): t[2] for t in matches[:8]}
            companion.save_state(STATE)
            reply(chat, "Which one is done?", buttons=[[(companion.task_title(t[2])[:60], f"done:{t[0]}")] for t in matches[:8]])
    elif cmd == "/q":
        send_question(chat)
    elif cmd == "/standup" and team.on("Teacher"):
        reply(chat, f"🗣 Standups live with your English Teacher now: open {team.handle('Teacher')} and send /speak (or just a voice "
                    "note there). It corrects you and ticks English. " + team.english_line())
    elif cmd == "/standup":
        send_standup(chat)
    elif cmd == "/train" and team.on("Coach"):
        reply(chat, f"🏋️ Training is your Coach's: open {team.handle('Coach')} (/today, /train there). " + team.training_line())
    elif cmd == "/goals":
        reply(chat, companion.goals_text())
    elif cmd == "/team":
        reply(chat, "👥 Your team today\n" + "\n".join(team.lines() or ["Only me so far."])
              + "\n\n🤖 Me: vault, goals, tasks and daily habits. /help")
    elif cmd == "/ask":
        if not arg:
            reply(chat, "Usage: /ask <question>, e.g. /ask what did I decide about the DDIA pace?")
        elif not ASSIST.ai_on():
            reply(chat, "❓ /ask needs Claude Code connected: run `claude setup-token` on your PC, put the result in scripts/.env as "
                        "CLAUDE_CODE_OAUTH_TOKEN=, then restart the bot.")
        else:
            status = reply(chat, "🔎 Reading your vault…")
            threading.Thread(target=answer_question, args=(chat, arg, status), daemon=True).start()
    elif cmd == "/train":
        day = int(arg) if arg in ("1", "2") else None
        caption = companion.training_caption(day)
        if not caption or not companion.ROUTINE.is_file():
            reply(chat, "The routine canvas wasn't found (02 - Areas/Training/Full Training Routine.canvas).")
        else:
            if not send_document(chat, companion.ROUTINE, caption=caption):
                reply(chat, "I couldn't attach the Obsidian routine note. Please try again later.")
    elif cmd == "/habits":
        if not habits.configured():
            reply(chat, "The habit tracker app isn't connected: set HABITS_API_URL and HABITS_API_TOKEN in scripts/.env.")
        else:
            rep = habit_report()
            reply(chat, habits.week_text(rep) if rep else "📊 The habit tracker app couldn't be reached, and there's no saved copy yet.")
    elif cmd == "/lock":
        if not habits.configured():
            reply(chat, "The habit tracker app isn't connected: set HABITS_API_URL and HABITS_API_TOKEN in scripts/.env.")
        else:
            status = habits.lock_status()
            reply(chat, habits.lock_line(status) if status else
                  "🔓 The focus lock is off. Turn it on in the app: Settings → Focus lock.")
    elif cmd == "/brief":
        send_brief(chat)
    elif cmd == "/review":
        send_review(chat)
    elif cmd == "/inbox":
        titles = companion.inbox_titles()
        reply(chat, f"📥 Inbox: {len(titles)} capture(s)\n" + "\n".join(f"• {t}" for t in titles[:20])
              + ("\n…" if len(titles) > 20 else "") if titles else "📥 The Inbox is empty.")
    elif cmd == "/status":
        loaded = "loaded" if _whisper is not None else "not loaded yet"
        reply(chat, f"🟢 Bot running · Whisper {loaded} · {len(PENDING)} capture(s) waiting for Save/Discard\n"
                    f"📥 Inbox: {len(companion.inbox_titles())} · 💾 {companion.backup_status()}\n⏰ {schedule_text()}")
    elif cmd == "/backup":
        if companion.request_backup():
            reply(chat, "💾 Backup requested. The backup service starts it within a minute and messages you when it's done.")
        else:
            reply(chat, "Backup isn't set up on this machine (no scripts/rclone folder).")
    else:
        reply(chat, f"Unknown command {cmd}. /help lists what I can do.")


# ---------- scheduled messages ----------
def due(spec, key, window_hours):
    """True once per period when now is inside [time, time + window). spec: "08:00" (daily) or "Sun 18:00" (weekly)."""
    if spec.lower() == "off":
        return False
    parts = spec.split()
    now = datetime.now()
    if len(parts) == 2 and parts[0][:3].lower() != ["mon", "tue", "wed", "thu", "fri", "sat", "sun"][now.weekday()]:
        return False
    hour, minute = map(int, parts[-1].split(":"))
    start = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    iso = now.isocalendar()
    period = f"{iso[0]}-W{iso[1]:02d}" if len(parts) == 2 else now.date().isoformat()
    return start <= now < start + timedelta(hours=window_hours) and STATE.get(key) != period and period


TASK_TIMES = ("13:00", "19:00")  # this week's tasks that no teammate owns, twice a day


def send_tasks(chat):
    """At each TASK_TIMES, the open tasks of this week that belong to the Assistant (team.py), with a ✅ per task."""
    now = datetime.now()
    sent = STATE.setdefault("tasks_sent", [])
    for t in TASK_TIMES:
        key = f"{now.date()}:{t}"
        h, m = map(int, t.split(":"))
        if now >= now.replace(hour=h, minute=m, second=0) and key not in sent:
            sent.append(key)
            del sent[:-20]
            items = team.related_tasks("Assistant")[:5]
            if items:
                STATE["done_choices"] = {str(line): text for line, text in items}
                reply(chat, "📌 Still pending this week:\n" + "\n".join(f"• {companion.task_title(x)}" for _, x in items)
                      + "\nTap the ones you've done.", buttons=[[("✅ " + companion.task_title(x)[:40], f"done:{line}")] for line, x in items])
            companion.save_state(STATE)
            return


def scheduled():
    if not OWNER:
        return
    try:
        period = due(BRIEF_TIME, "brief", 3)
        if period and send_brief(OWNER):
            STATE["brief"] = period
            companion.save_state(STATE)
        send_coach(OWNER)
        send_tasks(OWNER)
        period = due(REVIEW_TIME, "review", 6)
        if period:
            send_review(OWNER)
            STATE["review"] = period
            companion.save_state(STATE)
    except Exception as e:
        print("scheduled message failed:", e)


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
    if os.environ.get("TRAINER_BOT_TOKEN"):  # the personal trainer: its own Telegram bot, same process (trainer.py)
        import trainer
        threading.Thread(target=trainer.main, name="trainer", daemon=True).start()
    if os.environ.get("TEACHER_BOT_TOKEN"):  # the English teacher: its own bot, sharing this process's Whisper (teacher.py)
        import teacher
        threading.Thread(target=teacher.main, kwargs={"transcribe": transcribe}, name="teacher", daemon=True).start()
    try:
        api("setMyCommands", commands=json.dumps([{"command": c, "description": d} for c, d in COMMANDS]))
    except Exception as e:
        print("setMyCommands failed:", e)
    offset_file = Path(__file__).with_name("telegram.offset")
    try:
        offset = int(offset_file.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        offset = None
    while True:
        try:
            beat()
            expire_pending()
            scheduled()
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
