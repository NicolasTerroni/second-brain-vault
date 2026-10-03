#!/usr/bin/env python3
"""Telegram -> Obsidian Inbox. Text, links, voice/audio, photos and files become notes in `00 - Inbox`.

Uses local optional packages from `scripts/vendor`: yt-dlp and faster-whisper.
Config (scripts/.env or environment):
  TELEGRAM_BOT_TOKEN=...        from @BotFather
  TELEGRAM_ALLOWED_IDS=123,456  your Telegram user id(s); only these can write to the vault
  WHISPER_MODEL=small           optional (tiny/base/small/medium)
  WHISPER_DEVICE=cpu            CPU is the safe default; CUDA needs separate NVIDIA libraries
  Telegram voice/audio transcripts are constrained to English or Spanish; Reel transcripts keep automatic language detection.
Run: python scripts/telegram_bot.py
"""
import json, os, re, sys, time, html, tempfile, urllib.request, urllib.parse
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENDOR = Path(__file__).with_name("vendor")
if VENDOR.is_dir() and os.name == "nt":  # vendor/ holds Windows-only wheels; Linux/Docker use pip packages
    sys.path.insert(0, str(VENDOR))
INBOX = ROOT / "00 - Inbox"
ATTACH = INBOX / "attachments"
URL_RE = re.compile(r"https?://\S+")


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


def api(method, **params):
    data = urllib.parse.urlencode(params).encode()
    with urllib.request.urlopen(urllib.request.Request(f"{API}/{method}", data=data), timeout=70) as r:
        return json.load(r)["result"]


def reply(chat, text):
    try:
        api("sendMessage", chat_id=chat, text=text)
    except Exception as e:
        print("reply failed:", e)


def download(file_id, dest_dir, name_hint):
    info = api("getFile", file_id=file_id)
    ext = Path(info["file_path"]).suffix or Path(name_hint).suffix
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{name_hint}{ext}"
    urllib.request.urlretrieve(f"https://api.telegram.org/file/bot{TOKEN}/{info['file_path']}", dest)
    return dest


def transcribe(path, allowed_languages=None):
    global _whisper
    try:
        if _whisper is None:
            from faster_whisper import WhisperModel
            _whisper = WhisperModel(
                os.environ.get("WHISPER_MODEL", "small"),
                device=os.environ.get("WHISPER_DEVICE", "cpu"),
                compute_type="int8",
            )

        if allowed_languages:
            # Whisper auto-detects over its full language set. Use its language
            # probabilities to choose only among the languages allowed for
            # personal Telegram audio, then force decoding in that language.
            detected_segments, detection = _whisper.transcribe(str(path))
            probabilities = dict(detection.all_language_probs or [])
            if detection.language in allowed_languages:
                language = detection.language
                segs = detected_segments
            else:
                language = max(allowed_languages, key=lambda code: probabilities.get(code, 0.0))
                segs, _ = _whisper.transcribe(str(path), language=language)
            print(
                f"Telegram audio: transcribing as {language} "
                f"(detector probability {probabilities.get(language, 0.0):.2f})"
            )
        else:
            segs, _ = _whisper.transcribe(str(path))
        return " ".join(s.text.strip() for s in segs).strip()
    except ImportError:
        return None
    except Exception as e:
        print("transcription failed:", e)
        return None


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


def capture_x_post(url):
    """Capture a post from X's public embed; use X API when configured for quoted posts."""
    captured = _capture_x_api(url)
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
            "quote_status": ("X API lookup failed; quoted-post text may be missing." if token_configured else "Quoted-post text requires X API access; set X_API_BEARER_TOKEN in scripts/.env."),
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
            "quote_status": "Post text could not be fetched; the original URL is preserved. Configure X_API_BEARER_TOKEN for API access.",
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

    if media:
        kind, m = media
        if m.get("file_size", 0) > 20_000_000:
            reply(chat, "File over 20 MB: Telegram bots cannot download it.")
            return
        f = download(m["file_id"], ATTACH, f"tg-{stamp}")
        body = f"![[{f.name}]]\n"
        title = {"voice": "Voice note", "audio": "Audio", "video_note": "Video note"}.get(kind, kind.capitalize())
        tags = ["telegram", kind]
        if kind in ("voice", "audio", "video_note", "video"):
            tr = transcribe(f, allowed_languages=("es", "en"))
            if tr:
                body += f"\n## Transcript\n{tr}\n"
                title = tr
            else:
                body += "\n_Not transcribed (install faster-whisper to enable)._\n"
        if text:
            body += f"\n{text}\n"
            title = text
        p = write_note(title, body, tags=tags)
    else:
        urls = URL_RE.findall(text)
        short_video_links = [url for url in urls if instagram_url(url) or tiktok_url(url)]
        if short_video_links:  # Instagram Reels and TikToks: metadata + temporary-audio transcript
            url = short_video_links[0]
            platform = "tiktok" if tiktok_url(url) else "instagram"
            media, capture, error = capture_instagram(url, stamp)
            info = capture.get("info", capture) if capture else instagram_metadata(url)
            title = (info or {}).get("title") or page_title(url) or ("TikTok" if platform == "tiktok" else "Instagram Reel")
            if title.startswith("TikTok video #") and info and _handle(info):
                title = f"TikTok by {_handle(info)}"
            body = text + "\n" + video_metadata_section(info)
            if media:
                try:
                    transcript = transcribe(media)
                finally:
                    capture["temp_dir"].cleanup()
                if transcript:
                    body += f"\n## Transcript\n{transcript}\n"
                else:
                    body += "\n_Transcript was not generated (faster-whisper is unavailable or transcription failed). The temporary media was discarded; the original link is preserved above._\n"
            else:
                body += f"\n## Capture status\n{error}. The original link is preserved above.\n"
            p = write_note(title, body, source=url, tags=["telegram", "link", platform])
        elif len(urls) == 1 and youtube_url(urls[0]):
            url = urls[0]
            metadata, error = capture_youtube_metadata(url)
            title = (metadata or {}).get("title") or page_title(url) or "YouTube video"
            body = text + "\n"
            if metadata:
                body += video_metadata_section(metadata)
            else:
                body += f"\n## Metadata status\n{error}. The original link is preserved above.\n"
            p = write_note(title, body, source=url, tags=["telegram", "link", "youtube"])
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
            p = write_note(title, body, source=url, tags=["telegram", "link", "x"])
        elif urls and len(text.strip()) == len(urls[0]) and len(urls) == 1:
            t = page_title(urls[0]) or urls[0]
            p = write_note(t, text, source=urls[0], tags=["telegram", "link"])
        elif urls:
            p = write_note(text.split("\n")[0], text, source=urls[0], tags=["telegram", "link"])
        else:
            p = write_note(text.split("\n")[0] or "Note", text)
    reply(chat, f"Saved: {p.name}")
    print("saved", p)


def main():
    if not TOKEN:
        sys.exit("Set TELEGRAM_BOT_TOKEN in scripts/.env")
    print("Bot running. Ctrl+C to stop. Allowed ids:", ALLOWED or "none (setup mode)")
    offset_file = Path(__file__).with_name("telegram.offset")
    try:
        offset = int(offset_file.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        offset = None
    while True:
        try:
            params = {"timeout": 50, "allowed_updates": json.dumps(["message"])}
            if offset:
                params["offset"] = offset
            for u in api("getUpdates", **params):
                if "message" in u:
                    try:
                        handle(u["message"])
                    except Exception as e:
                        print("error:", e)
                        reply(u["message"]["chat"]["id"], f"Error: {e}")
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
