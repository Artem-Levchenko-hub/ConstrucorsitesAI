"""Render the Russian Yleum lessons on Windows.

Run with Python 3.12, Pillow, FFmpeg with libx264, and the installed
Microsoft Irina Desktop Russian voice. Pass --scratch an external working directory.
The published assets contain only flattened screenshots and lesson content.
"""

import argparse
import base64
import json
import os
import subprocess
import textwrap
import wave
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from lessons import LESSONS, Scene


HERE = Path(__file__).resolve().parent
WEB = HERE.parents[1]
DESTINATION = WEB / "public/guide/lessons/2026-11-06"
FONT_DIR = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
WIDTH, HEIGHT = 1280, 720
SCREEN = (36, 155, 746, 608)
BACKGROUND = "#101724"
PANEL = "#162235"
EDGE = "#31435c"
ACCENT = "#8cbaff"
TEXT = "#f6f8fd"
MUTED = "#b6c7dc"
LEAD = 0.32
TAIL = 0.68


@lru_cache
def font(size: int, bold: bool = False):
    return ImageFont.truetype(str(FONT_DIR / ("segoeuib.ttf" if bold else "segoeui.ttf")), size)


def write_lines(draw, text, x, y, width, size, color, *, bold=False):
    face = font(size, bold)
    line = ""
    for word in text.split():
        candidate = f"{line} {word}".strip()
        if line and draw.textlength(candidate, font=face) > width:
            draw.text((x, y), line, font=face, fill=color, anchor="lt")
            y += size + 7
            line = word
        else:
            line = candidate
    if line:
        draw.text((x, y), line, font=face, fill=color, anchor="lt")
        y += size + 7
    return y


def captures():
    source = (WEB / "src/components/max/guide/guide-screenshots.ts").read_text(encoding="utf-8")
    records = json.loads(source.split("export const guideScreenshots", 1)[1].split("= ", 1)[1].strip().removesuffix(";"))
    for record in records.values():
        path = record["src"]
        if not path.startswith("/guide/2026-10-30-redacted/") or ".." in path:
            raise ValueError(f"Expected a flattened redacted screenshot: {path}")
        record["file"] = WEB / "public" / path.removeprefix("/")
    records["checkout-preview"] = {
        "file": HERE / "assets/checkout-preview.jpg",
        "callouts": [{"targetX": 50, "targetY": 85}],
    }
    return records


def screenshot_layer(canvas, record, marker):
    x, y, right, bottom = SCREEN
    width, height = right - x, bottom - y
    with Image.open(record["file"]) as original:
        source = original.convert("RGB")
    target = record["callouts"][marker]
    point_x = source.width * target["targetX"] / 100
    point_y = source.height * target["targetY"] / 100
    if source.width > 600:
        crop_width = min(source.width, 670)
        crop_height = min(source.height, round(crop_width * height / width))
        left = round(max(0, min(source.width - crop_width, point_x - crop_width / 2)))
        top = round(max(0, min(source.height - crop_height, point_y - crop_height / 2)))
        source = source.crop((left, top, left + crop_width, top + crop_height))
        point_x -= left
        point_y -= top
    scale = min(width / source.width, height / source.height)
    fitted_width, fitted_height = round(source.width * scale), round(source.height * scale)
    image_x = x + (width - fitted_width) // 2
    image_y = y + (height - fitted_height) // 2
    canvas.paste(source.resize((fitted_width, fitted_height), Image.Resampling.LANCZOS), (image_x, image_y))
    return round(image_x + point_x * scale), round(image_y + point_y * scale)


def path_layer(draw, path):
    x, y, right, _ = SCREEN
    for index, label in enumerate(path):
        top = y + 30 + index * 122
        last = index == len(path) - 1
        draw.rounded_rectangle((x + 25, top, right - 25, top + 88), radius=15,
                               fill="#253a59" if last else "#1c2b42", outline=ACCENT if last else EDGE, width=2)
        draw.ellipse((x + 44, top + 24, x + 84, top + 64), fill=ACCENT if last else "#3b5270")
        draw.text((x + 64, top + 44), str(index + 1), font=font(22, True),
                  fill=BACKGROUND if last else TEXT, anchor="mm")
        write_lines(draw, label, x + 111, top + 25, right - x - 157, 32, TEXT, bold=last)
        if not last:
            draw.line((x + 64, top + 88, x + 64, top + 120), fill=EDGE, width=3)
    return x + 64, top + 44


def scene_base(title, index, count, scene: Scene, screenshots):
    canvas = Image.new("RGB", (WIDTH, HEIGHT), BACKGROUND)
    draw = ImageDraw.Draw(canvas)
    draw.text((36, 28), "YLEUM  /  ВИДЕОУРОК", font=font(20, True), fill=ACCENT, anchor="lt")
    write_lines(draw, title, 36, 60, 940, 43, TEXT, bold=True)
    draw.text((1242, 31), f"ШАГ {index + 1} ИЗ {count}", font=font(21, True), fill=MUTED, anchor="rt")
    label = "ПРИМЕР КАРТОЧКИ В MAX" if scene.picture == "checkout-preview" else "СНИМОК ИНТЕРФЕЙСА"
    draw.text((36, 124), label if scene.picture else "ПОРЯДОК ДЕЙСТВИЙ",
              font=font(18, True), fill=MUTED, anchor="lt")
    draw.rounded_rectangle((34, 153, 748, 610), radius=14, fill=PANEL, outline=EDGE, width=2)
    point = screenshot_layer(canvas, screenshots[scene.picture], scene.marker) if scene.picture else path_layer(draw, scene.path)
    draw.rounded_rectangle((768, 153, 1244, 610), radius=18, fill=PANEL, outline=EDGE, width=2)
    draw.text((794, 184), "ЧТО СДЕЛАТЬ", font=font(19, True), fill=ACCENT, anchor="lt")
    next_y = write_lines(draw, scene.action, 794, 220, 419, 37, TEXT, bold=True)
    next_y = max(361, next_y + 25)
    draw.text((794, next_y), "ЗАЧЕМ", font=font(19, True), fill=ACCENT, anchor="lt")
    last_y = write_lines(draw, scene.reason, 794, next_y + 34, 419, 28, MUTED)
    if last_y > 603:
        raise ValueError(f"Scene text does not fit: {title}, step {index + 1}")
    for step in range(count):
        start = 36 + step * 1210 / count
        end = 36 + (step + 1) * 1210 / count - 5
        draw.rounded_rectangle((start, 636, end, 642), radius=3, fill=ACCENT if step <= index else EDGE)
    return canvas, point


def cursor_frame(base, point, progress):
    frame = base.copy()
    draw = ImageDraw.Draw(frame)
    x, y = point
    if progress >= 0.8:
        radius = 19 + round(17 * (1 - progress))
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), outline="#14345b", width=7)
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), outline="#8edbff", width=4)
    ease = 1 - (1 - progress) ** 3
    start_x = min(SCREEN[2] - 28, max(SCREEN[0] + 25, x - 85))
    start_y = min(SCREEN[3] - 28, max(SCREEN[1] + 25, y + 65))
    x = round(start_x + (x - start_x) * ease)
    y = round(start_y + (y - start_y) * ease)
    shape = [(x, y), (x, y + 26), (x + 7, y + 20), (x + 13, y + 33),
             (x + 19, y + 30), (x + 12, y + 18), (x + 23, y + 18)]
    draw.polygon(shape, fill="#ffffff", outline="#101724", width=2)
    return frame


def speech_text(text):
    for original, pronunciation in (
        ("Yleum", "Илеум"), ("ЮKassa", "Юкасса"), ("amoCRM", "амо си эр эм"),
        ("CRM", "си эр эм"), ("MAX", "Макс"), ("СМС", "эс эм эс"), ("email", "имейл"),
    ):
        text = text.replace(original, pronunciation)
    return text


def ps_string(value):
    return "'" + str(value).replace("'", "''") + "'"


def synthesize(ids, scratch, voice):
    manifest = scratch / "speech.json"
    segments = [
        {"path": str(scratch / lesson / f"{index:02d}.wav"), "text": speech_text(scene.narration)}
        for lesson in ids for index, scene in enumerate(LESSONS[lesson][1])
    ]
    manifest.write_text(json.dumps(segments, ensure_ascii=False), encoding="utf-8")
    command = f"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
Add-Type -AssemblyName System.Speech
$narrator = [System.Speech.Synthesis.SpeechSynthesizer]::new()
try {{
    $narrator.SelectVoice({ps_string(voice)})
    $narrator.Rate = 0
    $narrator.Volume = 100
    $format = [System.Speech.AudioFormat.SpeechAudioFormatInfo]::new(
        22050, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,
        [System.Speech.AudioFormat.AudioChannel]::Mono)
    foreach ($segment in (Get-Content -Raw -Encoding UTF8 -LiteralPath {ps_string(manifest)} | ConvertFrom-Json)) {{
        $narrator.SetOutputToWaveFile($segment.path, $format)
        $narrator.Speak($segment.text)
        $narrator.SetOutputToNull()
    }}
}} finally {{
    $narrator.Dispose()
}}
"""
    encoded = base64.b64encode(command.encode("utf-16-le")).decode("ascii")
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-OutputFormat", "Text",
                             "-EncodedCommand", encoded], check=False)
    if result.returncode:
        raise RuntimeError(f"Speech synthesis failed for {voice}")


def vtt_time(seconds):
    millis = round(seconds * 1000)
    minutes, millis = divmod(millis, 60_000)
    hours, minutes = divmod(minutes, 60)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def caption_entries(text, start, duration):
    lines = textwrap.wrap(text, 43, break_long_words=False, break_on_hyphens=False)
    groups = ["\n".join(lines[index:index + 2]) for index in range(0, len(lines), 2)]
    size = sum(map(len, groups))
    elapsed = 0
    for group in groups:
        end = elapsed + duration * len(group) / size
        yield f"{vtt_time(start + elapsed)} --> {vtt_time(start + end)}\n{group}"
        elapsed = end


def audio_and_captions(lesson_dir, scenes):
    captions = ["WEBVTT"]
    durations = []
    elapsed = 0
    with wave.open(str(lesson_dir / "narration.wav"), "wb") as output:
        output.setparams((1, 2, 22050, 0, "NONE", "not compressed"))
        for index, scene in enumerate(scenes):
            with wave.open(str(lesson_dir / f"{index:02d}.wav"), "rb") as segment:
                if (segment.getnchannels(), segment.getsampwidth(), segment.getframerate()) != (1, 2, 22050):
                    raise ValueError("Unexpected speech audio format")
                speech_frames = segment.getnframes()
                samples = segment.readframes(speech_frames)
            if speech_frames < 22050 or not any(samples):
                raise ValueError(f"Empty narration: {lesson_dir.name} step {index + 1}")
            lead_frames = round(LEAD * 22050)
            tail_frames = round(TAIL * 22050)
            output.writeframesraw(bytes(lead_frames * 2) + samples + bytes(tail_frames * 2))
            captions.extend(caption_entries(scene.narration, elapsed + lead_frames / 22050, speech_frames / 22050))
            duration = (lead_frames + speech_frames + tail_frames) / 22050
            durations.append(duration)
            elapsed += duration
    return durations, "\n\n".join(captions) + "\n"


def render_lesson(lesson, scratch, ffmpeg, screenshots):
    title, scenes = LESSONS[lesson]
    lesson_dir = scratch / lesson
    durations, captions = audio_and_captions(lesson_dir, scenes)
    frames = []
    for index, (scene, duration) in enumerate(zip(scenes, durations, strict=True)):
        base, point = scene_base(title, index, len(scenes), scene, screenshots)
        for frame_index in range(10):
            progress = frame_index / 9
            frame = cursor_frame(base, point, progress)
            filename = f"frame-{index:02d}-{frame_index:02d}.jpg"
            frame.save(lesson_dir / filename, quality=94, subsampling=0)
            frames.append(f"file '{filename}'\nduration {(duration - 0.9 if frame_index == 9 else 0.1):.6f}")
            if index == 0 and frame_index == 9:
                frame.save(DESTINATION / f"{lesson}.jpg", quality=91, optimize=True)
    timeline = lesson_dir / "frames.txt"
    timeline.write_text("\n".join([*frames, frames[-1].splitlines()[0]]) + "\n", encoding="ascii")
    target = DESTINATION / f"{lesson}.mp4"
    subprocess.run([
        str(ffmpeg), "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "concat", "-safe", "1", "-i", str(timeline), "-i", str(lesson_dir / "narration.wav"),
        "-t", f"{sum(durations):.6f}", "-r", "24", "-c:v", "libx264", "-preset", "veryfast",
        "-crf", "23", "-threads", "3", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ac", "1",
        "-af", "loudnorm=I=-18:TP=-1.5:LRA=7", "-ar", "44100", "-b:a", "72k",
        "-movflags", "+faststart", "-metadata:s:a:0", "language=rus", "-metadata", f"title={title}", str(target),
    ], check=True)
    (DESTINATION / f"{lesson}.vtt").write_text(captions, encoding="utf-8")
    print(f"{lesson}: {len(scenes)} steps, {sum(durations):.1f}s, {target.stat().st_size:,} bytes", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ffmpeg", type=Path, required=True)
    parser.add_argument("--scratch", type=Path, required=True)
    parser.add_argument("--voice", default="Microsoft Irina Desktop")
    parser.add_argument("--jobs", type=int, choices=range(1, 5), default=2)
    parser.add_argument("--lesson", choices=LESSONS, nargs="+")
    options = parser.parse_args()
    ids = options.lesson or list(LESSONS)
    scratch = options.scratch.resolve()
    ffmpeg = options.ffmpeg.resolve(strict=True)
    screenshots = captures()
    DESTINATION.mkdir(parents=True, exist_ok=True)
    for lesson in ids:
        (scratch / lesson).mkdir(parents=True, exist_ok=True)
    synthesize(ids, scratch, options.voice)
    with ThreadPoolExecutor(max_workers=options.jobs) as pool:
        renders = [pool.submit(render_lesson, lesson, scratch, ffmpeg, screenshots) for lesson in ids]
        for render in renders:
            render.result()


if __name__ == "__main__":
    main()
