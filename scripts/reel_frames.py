#!/usr/bin/env python3
"""Contact sheets of a Reel/TikTok/short video, so an agent can SEE moves the transcript doesn't name.
Downloads the video to a temp folder, samples ~24 frames, writes 4x3 JPG sheets with timestamps, deletes the video.
The sheets are for viewing during a distill only: never store them in the vault (AGENTS.md, Reels rule).

Run in the bot container (has yt-dlp, PyAV and Pillow):
  docker compose exec -T bot python reel_frames.py <url> [--out /tmp/reelframes] [--name label]
  docker cp vault-telegram-bot:/tmp/reelframes <scratch dir>   # then view, then delete both copies
"""
import argparse, glob, os, shutil, tempfile
import av, yt_dlp
from PIL import Image, ImageDraw


def sheets(url, out, name, frames_wanted=24):
    os.makedirs(out, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="reel-")
    opts = {"format": "best[height<=720]/best", "outtmpl": f"{tmp}/video.%(ext)s", "quiet": True,
            "no_warnings": True, "noprogress": True, "noplaylist": True, "max_filesize": 100_000_000}
    cookies = os.environ.get("YTDLP_COOKIES")
    if cookies and os.path.isfile(cookies):
        opts["cookiefile"] = cookies
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
        container = av.open(glob.glob(f"{tmp}/video.*")[0])
        stream = container.streams.video[0]
        duration = (float(info.get("duration") or 0)
                    or (container.duration / 1_000_000 if container.duration else 0)
                    or (float(stream.duration * stream.time_base) if stream.duration else 60))
        step = max(1.0, duration / frames_wanted)
        frames, next_t = [], 0.0
        for frame in container.decode(stream):
            t = float(frame.pts * stream.time_base)
            if t >= next_t:
                image = frame.to_image()
                image.thumbnail((320, 320))
                frames.append((t, image))
                next_t += step
        container.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)  # the video itself is never kept
    paths = []
    for start in range(0, len(frames), 12):
        chunk = frames[start:start + 12]
        w, h = chunk[0][1].size
        rows = (len(chunk) + 3) // 4
        canvas = Image.new("RGB", (4 * w, rows * (h + 18)), "white")
        draw = ImageDraw.Draw(canvas)
        for i, (t, image) in enumerate(chunk):
            x, y = (i % 4) * w, (i // 4) * (h + 18)
            canvas.paste(image, (x, y + 18))
            draw.text((x + 4, y + 3), f"{t:5.1f}s", fill="black")
        path = os.path.join(out, f"{name}_sheet{start // 12 + 1}.jpg")
        canvas.save(path, quality=80)
        paths.append(path)
    print(f"{name}: {duration:.0f}s video, {len(frames)} frames every {step:.1f}s -> {', '.join(paths)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("url")
    parser.add_argument("--out", default="/tmp/reelframes")
    parser.add_argument("--name", default="reel")
    args = parser.parse_args()
    sheets(args.url, args.out, args.name)
