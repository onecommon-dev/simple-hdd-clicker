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
AMBIENT_LOOP_SAMPLE = BASE_DIR / "ambient_loop.wav"

PCM_DEVICE = b"pipewire"

POLL_INTERVAL = 0.05
MIN_ACTIVITY_DELTA = 2

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

click_samples = []

for wavfile in sorted(CLICK_DIR.glob("*.wav")):
    # with wave.open(str(wavfile), "rb") as wf:
    wf = wave.open(str(wavfile), "rb")
    channels = wf.getnchannels()
    rate = wf.getframerate()
    click_samples.append({
        "sample": wf,
        "channels": channels
    })

if not click_samples:
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

with wave.open(str(AMBIENT_LOOP_SAMPLE), "rb") as wf:
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

# ---------------- CLICK LOOP ------------------

def play_click():
    chunk_frames = 4096
    count = len(click_samples)
    click_loop_sample = click_samples[random.randrange(count)]
    
    sample = click_loop_sample['sample']
    channels = click_loop_sample['channels']
    data = sample.readframes(chunk_frames)

    if not data:
        sample.rewind()

    buf = ctypes.create_string_buffer(data)

    frames = len(data) // (channels * 2)

    result = alsa.snd_pcm_writei(
        click_handle,
        buf,
        frames,
    )

    if result < 0:
        alsa.snd_pcm_prepare(click_handle)

# ---------------- AMBIENT LOOP ----------------

def ambient_loop():
    chunk_frames = 2048
    wf = wave.open(str(AMBIENT_LOOP_SAMPLE), "rb")
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
clicked = False

print("hddclicker running...")

threading.Thread(target=ambient_loop, daemon=True).start()

while True:
    current = get_disk_activity()
    delta = current - last_total
    now = time.time()
    
    # Lengthen the clicking a bit at random since SSD activity is too fast to make it realistic
    if delta >= MIN_ACTIVITY_DELTA or (clicked and random.random() > 0.2): 
        play_click()
        clicked = True
    else:
        clicked = False
        time.sleep(POLL_INTERVAL)

    last_total = current
    
