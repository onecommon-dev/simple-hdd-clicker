#!/usr/bin/env python3

import ctypes
import time
import wave
import random
import threading
from pathlib import Path

# ---------------- CONFIG ----------------

BASE_DIR = Path(__file__).resolve().parent
CLICK_DIR = BASE_DIR / "Samples"
LOOP_SAMPLE = BASE_DIR / "looping.wav"

PCM_DEVICE = b"pipewire"

POLL_INTERVAL = 0.05
MIN_ACTIVITY_DELTA = 2
COOLDOWN = 0.015

# ---------------- ALSA SETUP ----------------

alsa = ctypes.cdll.LoadLibrary("libasound.so.2")

snd_pcm_t = ctypes.c_void_p
click_handle = snd_pcm_t()
ambient_handle = snd_pcm_t()

SND_PCM_STREAM_PLAYBACK = 0
SND_PCM_ACCESS_RW_INTERLEAVED = 3
SND_PCM_FORMAT_S16_LE = 2

# IMPORTANT: prevent ctypes from guessing wrong signatures
alsa.snd_pcm_open.argtypes = [
    ctypes.POINTER(snd_pcm_t),
    ctypes.c_char_p,
    ctypes.c_int,
    ctypes.c_int,
]
alsa.snd_pcm_open.restype = ctypes.c_int

alsa.snd_pcm_set_params.argtypes = [
    snd_pcm_t,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_uint,
    ctypes.c_ulong,
    ctypes.c_int,
    ctypes.c_ulong,
]
alsa.snd_pcm_set_params.restype = ctypes.c_int

alsa.snd_pcm_writei.argtypes = [
    snd_pcm_t,
    ctypes.c_void_p,
    ctypes.c_ulong,
]
alsa.snd_pcm_writei.restype = ctypes.c_long

alsa.snd_pcm_prepare.argtypes = [snd_pcm_t]
alsa.snd_pcm_prepare.restype = ctypes.c_int


# ---------------- LOAD CLICK SOUNDS ----------------

click_frames = []

for wavfile in sorted(CLICK_DIR.glob("*.wav")):
    with wave.open(str(wavfile), "rb") as wf:
        channels = wf.getnchannels()
        rate = wf.getframerate()
        frames = wf.readframes(wf.getnframes())

        click_frames.append({
            "frames": frames,
            "frame_count": len(frames) // (channels * 2),
        })

if not click_frames:
    raise RuntimeError("No click WAV files found")

# use click format from first sample (assume consistency)
click_channels = channels
click_rate = rate


# ---------------- OPEN CLICK PCM ----------------

if alsa.snd_pcm_open(
    ctypes.byref(click_handle),
    PCM_DEVICE,
    SND_PCM_STREAM_PLAYBACK,
    0,
) < 0:
    raise RuntimeError("Failed to open click PCM")

if alsa.snd_pcm_set_params(
    click_handle,
    SND_PCM_FORMAT_S16_LE,
    SND_PCM_ACCESS_RW_INTERLEAVED,
    click_channels,
    click_rate,
    1,
    50000,
) < 0:
    raise RuntimeError("Failed to configure click PCM")


# ---------------- OPEN AMBIENT PCM ----------------

with wave.open(str(LOOP_SAMPLE), "rb") as wf:
    amb_channels = wf.getnchannels()
    amb_rate = wf.getframerate()

if alsa.snd_pcm_open(
    ctypes.byref(ambient_handle),
    PCM_DEVICE,
    SND_PCM_STREAM_PLAYBACK,
    0,
) < 0:
    raise RuntimeError("Failed to open ambient PCM")

if alsa.snd_pcm_set_params(
    ambient_handle,
    SND_PCM_FORMAT_S16_LE,
    SND_PCM_ACCESS_RW_INTERLEAVED,
    amb_channels,
    amb_rate,
    1,
    50000,
) < 0:
    raise RuntimeError("Failed to configure ambient PCM")


# ---------------- CLICK ENGINE ----------------

last_sample_index = -1

def play_click():
    global last_sample_index

    count = len(click_frames)

    if count == 1:
        idx = 0
    else:
        idx = random.randrange(count)
        if idx == last_sample_index:
            idx = (idx + 1) % count

    last_sample_index = idx
    sample = click_frames[idx]

    buf = ctypes.create_string_buffer(sample["frames"])

    result = alsa.snd_pcm_writei(
        click_handle,
        buf,
        sample["frame_count"],
    )

    if result < 0:
        alsa.snd_pcm_prepare(click_handle)


# ---------------- DISK ACTIVITY ----------------

def get_disk_activity():
    total = 0
    with open("/proc/diskstats") as f:
        for line in f:
            parts = line.split()
            if len(parts) < 14:
                continue

            dev = parts[2]
            if dev.startswith(("loop", "ram")):
                continue

            total += int(parts[3]) + int(parts[7])

    return total


# ---------------- AMBIENT LOOP ----------------

def ambient_loop():
    wf = wave.open(str(LOOP_SAMPLE), "rb")
    chunk_frames = 2048

    channels = wf.getnchannels()

    while True:
        data = wf.readframes(chunk_frames)

        if not data:
            wf.rewind()
            continue

        buf = ctypes.create_string_buffer(data)

        frames = len(data) // (channels * 2)

        result = alsa.snd_pcm_writei(
            ambient_handle,
            buf,
            frames,
        )

        if result < 0:
            alsa.snd_pcm_prepare(ambient_handle)


# ---------------- MAIN LOOP ----------------

last_total = get_disk_activity()
last_click = 0

print("diskclickd running...")

threading.Thread(target=ambient_loop, daemon=True).start()

while True:
    current = get_disk_activity()
    delta = current - last_total
    now = time.time()

    if delta >= MIN_ACTIVITY_DELTA:
        if now - last_click > COOLDOWN:
            if random.random() > 0.15:
                play_click()
                last_click = now

    last_total = current
    time.sleep(POLL_INTERVAL)
