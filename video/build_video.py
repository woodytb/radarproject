"""Baut das Erklärvideo (MP4, 1920x1080, ~7 min) samt Untertiteln.

Ablauf: Sätze einzeln vertonen (macOS `say`) -> Szenenlänge = Tonlänge ->
Standbilder per Headless-Chrome, Karte als Bildfolge (map_frames.py) ->
je Szene ein Videoclip -> Clips aneinanderhängen, Ton und Untertitel dazu.

    .venv/bin/python video/build_video.py [--voice Anna] [--rate 158]
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import wave
from pathlib import Path

import imageio_ffmpeg

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

from map_frames import render_all  # noqa: E402
from scenes import html_for  # noqa: E402
from script import SHOTS, spoken  # noqa: E402

OUT = ROOT / "output" / "video"
BUILD = OUT / "_build"
FF = imageio_ffmpeg.get_ffmpeg_exe()
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
FPS, RATE_HZ = 30, 48000
LEAD, GAP, TAIL = 0.6, 0.45, 0.9   # Sekunden Stille: Szenenanfang, zwischen Sätzen, Szenenende
FADE = 0.45


def run(*cmd: str) -> None:
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def tts(text: str, out: Path, voice: str, rate: int) -> float:
    aiff = out.with_suffix(".aiff")
    run("say", "-v", voice, "-r", str(rate), "-o", str(aiff), spoken(text))
    run(FF, "-y", "-i", str(aiff), "-ar", str(RATE_HZ), "-ac", "1", "-sample_fmt", "s16", str(out))
    aiff.unlink()
    with wave.open(str(out)) as w:
        return w.getnframes() / RATE_HZ


def silence(sec: float) -> bytes:
    return b"\x00\x00" * int(round(sec * RATE_HZ))


def screenshot(shot: str) -> Path:
    html = BUILD / "html" / f"{shot}.html"
    html.parent.mkdir(parents=True, exist_ok=True)
    html.write_text(html_for(shot))
    png = BUILD / "stills" / f"{shot}.png"
    png.parent.mkdir(parents=True, exist_ok=True)
    run(CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--allow-file-access-from-files",
        "--window-size=1920,1080", "--virtual-time-budget=5000", f"--screenshot={png}", html.as_uri())
    return png


def _srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _chunks(text: str, limit: int = 84) -> list[str]:
    """Lange Sätze in gleich lange Untertitel-Häppchen (max. 2 Zeilen à ~42 Zeichen) teilen."""
    def split(words: list[str], parts: int) -> list[str]:
        target, out, cur = len(" ".join(words)) / parts, [], []
        for w in words:
            cur.append(w)
            if len(out) < parts - 1 and len(" ".join(cur)) >= target:
                out.append(" ".join(cur))
                cur = []
        return out + [" ".join(cur)] if cur else out

    words = text.split()
    cues = split(words, -(-len(text) // limit))
    return [c if len(c) <= limit // 2 else "\n".join(split(c.split(), 2)) for c in cues]


def build(voice: str, rate: int) -> Path:
    BUILD.mkdir(parents=True, exist_ok=True)
    map_dir = BUILD / "map"
    n_map = len(list(map_dir.glob("*.png"))) or render_all(map_dir)

    clips, audio, cues, t0 = [], bytearray(), [], 0.0
    for i, shot in enumerate(SHOTS):
        sid = shot["id"]
        print(f"[{i + 1:2d}/{len(SHOTS)}] {sid}", flush=True)
        pcm = bytearray(silence(LEAD))
        for j, line in enumerate(shot["lines"]):
            wav = BUILD / "audio" / f"{sid}_{j}.wav"
            wav.parent.mkdir(parents=True, exist_ok=True)
            dur = tts(line, wav, voice, rate)
            start = t0 + len(pcm) / 2 / RATE_HZ
            pieces = _chunks(line)
            total = sum(len(p) for p in pieces)
            for p in pieces:
                share = dur * len(p) / total
                cues.append((start, start + share, p))
                start += share
            with wave.open(str(wav)) as w:
                pcm += w.readframes(w.getnframes())
            if j < len(shot["lines"]) - 1:
                pcm += silence(GAP)
        pcm += silence(TAIL)
        n_frames = -(-len(pcm) // 2 * FPS // RATE_HZ)   # aufrunden auf ganze Videobilder
        dur = n_frames / FPS
        pcm += silence(dur - len(pcm) / 2 / RATE_HZ)
        audio += pcm

        clip = BUILD / "clips" / f"{i:02d}_{sid}.mp4"
        clip.parent.mkdir(parents=True, exist_ok=True)
        fade = f"fade=t=in:st=0:d={FADE},fade=t=out:st={dur - FADE:.3f}:d={FADE},format=yuv420p"
        if shot["visual"] == "map":
            src = ["-framerate", f"{n_map / dur:.5f}", "-i", str(map_dir / "%05d.png")]
            vf = f"fps={FPS},{fade}"
        else:
            src = ["-loop", "1", "-framerate", str(FPS), "-i", str(screenshot(sid))]
            vf = fade
        run(FF, "-y", *src, "-frames:v", str(n_frames), "-vf", vf, "-c:v", "libx264", "-preset", "medium",
            "-crf", "18", "-r", str(FPS), "-an", str(clip))
        clips.append(clip)
        t0 += dur

    # Ton, Untertitel, Clipliste
    track = BUILD / "voice.wav"
    with wave.open(str(track), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE_HZ)
        w.writeframes(bytes(audio))
    srt = OUT / "gps-stoerradar.srt"
    srt.write_text("\n".join(f"{k}\n{_srt_time(a)} --> {_srt_time(b)}\n{txt}\n" for k, (a, b, txt) in enumerate(cues, 1)))
    listing = BUILD / "clips.txt"
    listing.write_text("".join(f"file '{c}'\n" for c in clips))

    out = OUT / "gps-stoerradar.mp4"
    run(FF, "-y", "-f", "concat", "-safe", "0", "-i", str(listing), "-i", str(track), "-i", str(srt),
        "-map", "0:v", "-map", "1:a", "-map", "2:s", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
        "-c:s", "mov_text", "-metadata:s:s:0", "language=ger", "-metadata", "title=GPS-Störradar Europa",
        "-movflags", "+faststart", str(out))
    print(f"{out}  ({t0 / 60:.1f} min, {out.stat().st_size / 2**20:.0f} MB)")
    return out


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--voice", default="Anna")
    p.add_argument("--rate", type=int, default=158, help="Wörter pro Minute")
    a = p.parse_args()
    build(a.voice, a.rate)
