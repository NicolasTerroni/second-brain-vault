#!/usr/bin/env python3
"""Contact sheets of a Reel/TikTok/short video, so an agent can SEE moves the transcript doesn't name.
Downloads the video to a temp folder, samples frames, writes JPG sheets (4 per row) with timestamps, deletes the video.
By default the sheets are for viewing during a distill only and are deleted afterwards. Store them in the vault
only when the user asks for frames in a note (AGENTS.md, Reels rule).

Run in the bot container (has yt-dlp, PyAV and Pillow):
  docker compose exec -T bot python reel_frames.py <url> [--name label] [--frames 24]
  docker compose exec -T bot python reel_frames.py <url> --segment ex1:0-4.9 --segment ex2:5-9 --frames 12 --size 400
      one sheet per segment (e.g. one collage per exercise), all frames of a segment on one sheet
  docker cp vault-telegram-bot:/tmp/reelframes <dir>    # then view; clean up the container copy
"""
import argparse, glob, math, os, shutil, tempfile
import av, yt_dlp
from PIL import Image, ImageDraw


def download(url, tmp):
    opts = {"format": "bv*[height<=720]/b[height<=720]/b", "outtmpl": f"{tmp}/video.%(ext)s", "quiet": True,
            "no_warnings": True, "noprogress": True, "noplaylist": True, "max_filesize": 100_000_000}
    cookies = os.environ.get("YTDLP_COOKIES")
    if cookies and os.path.isfile(cookies):
        opts["cookiefile"] = cookies
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
    return info, glob.glob(f"{tmp}/video.*")[0]


def save_sheet(frames, path, per_row=4):
    w, h = frames[0][1].size
    rows = math.ceil(len(frames) / per_row)
    canvas = Image.new("RGB", (per_row * w, rows * (h + 22)), "white")
    draw = ImageDraw.Draw(canvas)
    for i, (t, image) in enumerate(frames):
        x, y = (i % per_row) * w, (i // per_row) * (h + 22)
        canvas.paste(image, (x, y + 22))
        draw.text((x + 4, y + 4), f"{i + 1}. {t:4.1f}s", fill="black")
    canvas.save(path, quality=82)
    return path


def sheets(url, out, name, frames_wanted=24, segments=None, size=320, per_sheet=12):
    """segments: [(label, start_s, end_s)] -> one sheet per segment; None -> the whole video in sheets of per_sheet."""
    os.makedirs(out, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="reel-")
    try:
        info, path = download(url, tmp)
        container = av.open(path)
        stream = container.streams.video[0]
        duration = (float(info.get("duration") or 0)
                    or (container.duration / 1_000_000 if container.duration else 0)
                    or (float(stream.duration * stream.time_base) if stream.duration else 60))
        ranges = segments or [(name, 0.0, duration)]
        picks = {label: [] for label, _, _ in ranges}
        nexts = {label: start for label, start, _ in ranges}
        steps = {label: max(0.05, (end - start) / frames_wanted) for label, start, end in ranges}
        for frame in container.decode(stream):
            t = float(frame.pts * stream.time_base)
            for label, start, end in ranges:
                if start <= t <= end and t >= nexts[label] and len(picks[label]) < frames_wanted:
                    image = frame.to_image()
                    image.thumbnail((size, size))
                    picks[label].append((t, image))
                    nexts[label] += steps[label]
        container.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)  # the video itself is never kept
    written = []
    for label, start, end in ranges:
        frames = picks[label]
        if not frames:
            continue
        if segments:
            written.append(save_sheet(frames, os.path.join(out, f"{label}.jpg")))
        else:
            for i in range(0, len(frames), per_sheet):
                written.append(save_sheet(frames[i:i + per_sheet], os.path.join(out, f"{label}_sheet{i // per_sheet + 1}.jpg")))
        print(f"{label}: {start:.1f}-{end:.1f}s, {len(frames)} frames every {steps[label]:.2f}s")
    print(f"{duration:.0f}s video -> {', '.join(written)}")


def segment(text):
    label, _, span = text.rpartition(":")
    start, _, end = span.partition("-")
    return label, float(start), float(end)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("url")
    parser.add_argument("--out", default="/tmp/reelframes")
    parser.add_argument("--name", default="reel")
    parser.add_argument("--frames", type=int, default=24, help="frames per video, or per segment (e.g. 60 for detail)")
    parser.add_argument("--segment", action="append", type=segment, help="label:start-end in seconds; one sheet each")
    parser.add_argument("--size", type=int, default=320, help="frame size in px (longest side)")
    args = parser.parse_args()
    sheets(args.url, args.out, args.name, args.frames, args.segment, args.size)
