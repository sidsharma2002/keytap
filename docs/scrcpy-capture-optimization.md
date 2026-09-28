# Capture Pipeline Optimization: 10x FPS Improvement

## Background

keytap mirrors an Android device screen to a pygame window for keyboard-driven navigation. The capture pipeline is the critical path — every frame has to travel from the device GPU to a pygame surface.

We started with Android's `screenrecord` tool, then switched to `scrcpy-server` as the device-side capture source. Even with scrcpy-server (which hooks SurfaceFlinger directly), our first Python implementation was bottlenecked on the **client side**. This doc covers what that bottleneck was and how we fixed it.

---

## Old Pipeline (screenrecord backend, 2-10 fps)

```
Android device
  └─ screenrecord --output-format=h264 (MediaCodec API)
       └─ adb shell stdout
            └─ ffmpeg subprocess (stdin pipe)
                 └─ -pix_fmt rgb24 -f rawvideo (stdout pipe)
                      └─ Python: ffmpeg_proc.stdout.read(W*H*3)
                           └─ pygame.image.frombytes()
                                └─ pygame surface → render
```

### Why it was slow

**1. screenrecord uses MediaCodec, not SurfaceFlinger**

`screenrecord` goes through Android's `MediaCodec` API, which adds encoding overhead and is capped by the OS at a lower frame rate than the raw compositor output. Typical result: 2-10 fps, even on a fast device.

**2. OS pipe bandwidth**

ffmpeg decoded H264 → YUV420p internally, then converted to RGB24 before writing to stdout. That's `W × H × 3` bytes per frame through an OS pipe.

At 800×450 resolution and 30 fps target: `450 × 800 × 3 × 30 = ~32 MB/s` through a pipe. The pipe itself isn't the limiting factor at that bandwidth, but combined with the other costs it adds up.

**3. CPU YUV→RGB inside ffmpeg**

ffmpeg did the YUV→RGB color conversion on CPU before writing to the pipe. This is a full-frame pixel transformation on every frame.

**4. Subprocess overhead**

Two processes (`adb shell screenrecord` + `ffmpeg`) had to be spawned, managed, and torn down on each restart (screenrecord has a 3-minute limit). Restart latency was 1-3 seconds.

---

## New Pipeline (scrcpy-server backend, ~30 fps)

```
Android device
  └─ scrcpy-server (SurfaceFlinger hook via DisplayEventReceiver)
       └─ raw H264 Annex B → abstract socket → adb reverse → TCP
            └─ Python: sock.recv(65536)
                 └─ av.CodecContext.parse()  → AVPacket
                      └─ av.CodecContext.decode() → AVFrame (YUV420p)
                           └─ frame.reformat(rgb24, win_w, win_h)  [swscale in-process]
                                └─ ndarray.tobytes()
                                     └─ pygame.image.frombytes()
                                          └─ pygame surface → render
```

### What changed

**1. scrcpy-server replaces screenrecord**

`scrcpy-server` is the server-side component of the open-source [scrcpy](https://github.com/Genymobile/scrcpy) project. Instead of going through MediaCodec, it registers a `DisplayEventReceiver` callback directly with SurfaceFlinger — the Android compositor. Every time a new frame is composited to the display, scrcpy-server gets it immediately. No MediaCodec overhead, no frame-rate cap imposed by the recording API.

Result: device-side fps matches the actual display refresh rate (~30-60 fps depending on content).

**2. In-process decode via PyAV (no subprocess, no pipe)**

We studied how scrcpy's C client handles the stream (`decoder.c`, `demuxer.c`, `screen.c`, `texture.c`):

```c
// decoder.c
avcodec_send_packet(decoder->ctx, packet);
avcodec_receive_frame(decoder->ctx, decoder->frame);

// texture.c — uploads YUV planes directly to GPU texture
SDL_UpdateYUVTexture(tex->texture, NULL,
    frame->data[0], frame->linesize[0],  // Y plane
    frame->data[1], frame->linesize[1],  // U plane
    frame->data[2], frame->linesize[2]); // V plane
```

Key insight: **scrcpy never converts YUV→RGB on CPU**. It uploads raw YUV planes to an SDL2 streaming texture, and the GPU shader does the color conversion during rendering.

We replicated this with PyAV (Python bindings to libav, the same library ffmpeg uses):

```python
# capture_scrcpy.py
codec = av.CodecContext.create("h264", "r")
codec.flags |= 0x00080000  # AV_CODEC_FLAG_LOW_DELAY (same as scrcpy's decoder.c)
codec.open()

chunk = sock.recv(65536)
packets = codec.parse(chunk)          # equivalent to sc_demuxer
for packet in packets:
    for frame in codec.decode(packet): # equivalent to sc_decoder
        rgb_frame = frame.reformat(    # swscale in-process
            width=win_w, height=win_h,
            format="rgb24",
        )
        enqueue(rgb_frame.to_ndarray().tobytes())
```

No ffmpeg subprocess. No stdin/stdout pipes. Decode and scale happen inside the same Python process via libav. The only inter-process communication is the TCP socket receiving compressed H264 — which is tiny (~1-8 Mbit/s vs 32 MB/s for raw RGB24).

**3. AV_CODEC_FLAG_LOW_DELAY**

scrcpy sets `AV_CODEC_FLAG_LOW_DELAY` on the codec context. This disables B-frame reordering — the decoder emits a frame as soon as it's decoded rather than buffering it waiting for a future reference frame. We do the same. Without this, the decoder can hold back 1-2 frames waiting for B-frames that never come (scrcpy encodes P-frames only).

---

## Results

| Metric | screenrecord backend | scrcpy backend (old, pipe) | scrcpy backend (PyAV) |
|---|---|---|---|
| Device-side fps | 2-10 fps | ~30 fps (server) | ~30 fps (server) |
| Client-side fps | 2-10 fps | 2-10 fps (pipe bottleneck) | ~25-30 fps |
| First frame | 1-3s | 6-7s | 3-4s |
| stall_ms | 100-300ms | ~0.8ms | <5ms |
| Restart cost | 1-3s | 1-2s | <1s |
| Inter-process data | ~32 MB/s (RGB24 pipe) | ~32 MB/s (RGB24 pipe) | ~1-8 Mbit/s (H264) |

---

## Architecture Diagram

```
BEFORE                                   AFTER
------                                   -----

[Android: MediaCodec]                    [Android: SurfaceFlinger hook]
        |                                        |
    H264 stream                              H264 stream
        |                                        |
[adb shell screenrecord]                  [scrcpy-server JAR]
        |                                        |
    stdout pipe                            raw H264 Annex B
        |                                        |
[ffmpeg process]                          [adb reverse → TCP]
  - decode H264                                  |
  - YUV→RGB (CPU)                      [Python sock.recv()]
  - write RGB24 to stdout                        |
        |                               [PyAV codec.parse()]
    32 MB/s pipe                                 |
        |                               [PyAV codec.decode()]
[Python stdout.read()]                           |
        |                               [frame.reformat(rgb24)] ← swscale
[pygame.image.frombytes()]                       |
        |                               [numpy.tobytes()]
    pygame surface                               |
                                        [pygame.image.frombytes()]
                                                 |
                                             pygame surface
```

---

## Dependencies

```
pip install av numpy
```

- `av` (PyAV 18.x) — Python bindings to libavcodec/libavformat/libswscale
- `numpy` — used only for `to_ndarray().tobytes()` (contiguous RGB24 bytes with no stride padding)
