# -*- coding: utf-8 -*-
"""
Retro Recoil - intro animation.

If start.gif exists in the server root, it is converted once into two small
binary files: ascii.bin (flexible-resolution ASCII art) and petscii.bin
(fixed Commodore-native resolution). Each file stores a base-resolution
animation as an EXACT cell-for-cell delta between consecutive frames (the
same "only resend what actually changed, no threshold" idea used for the
live network transport in rr_render.Presenter - no fading/ghost-line
artifacts, because a cell is either identical to the previous frame or it is
resent in full).

If a .bin is missing or unreadable but start.gif is present, it is rebuilt
from the gif automatically. If start.gif is missing, no intro is converted
and no intro screen is shown.

At playback time the stored base grid is nearest-neighbour resampled (per
client) to that client's actual terminal size, so it always fills the
screen whether the viewer is an 80x40 ASCII window or a fixed 40x25 C64.
"""
import os
import struct

import numpy as np

from rr_render import PAL_C64, Grid, WHITE, BLACK

MAGIC = b"RRI1"
RAMP = " .:-=+*#%@"
CHAR_ASPECT = 2.0  # a terminal character cell is ~2x taller than it is wide

ASCII_BASE = (80, 40)      # (cols, rows) - flexible-resolution clients stretch to fit anyway
PETSCII_BASE = (40, 25)    # native Commodore 64 BBS screen size


# ---------------------------------------------------------------- gif -> frames
def _load_gif_frames(path):
    from PIL import Image, ImageSequence
    im = Image.open(path)
    frames, durations = [], []
    for frame in ImageSequence.Iterator(im):
        rgb = frame.convert("RGB")
        frames.append(np.asarray(rgb, dtype=np.uint8))
        durations.append(max(20, frame.info.get("duration", 80)))
    return frames, durations


def _fit_dims(img_w, img_h, max_cols, max_rows, char_aspect=CHAR_ASPECT):
    box_w, box_h = max_cols, max_rows * char_aspect
    img_aspect = img_w / max(1, img_h)
    box_aspect = box_w / box_h
    if img_aspect > box_aspect:
        cols = max_cols
        rows = max(1, int(round(max_cols / img_aspect / char_aspect)))
    else:
        rows = max_rows
        cols = max(1, int(round(max_rows * char_aspect * img_aspect)))
    return min(cols, max_cols), min(rows, max_rows)


def _quantize(rgb_rc3):
    """rgb (rows, cols, 3) uint8 -> (char_grid uint8, color_grid uint8) at the C64 16-colour palette."""
    lum = (0.299 * rgb_rc3[..., 0].astype(np.float32) + 0.587 * rgb_rc3[..., 1].astype(np.float32)
           + 0.114 * rgb_rc3[..., 2].astype(np.float32))
    idx = np.clip((lum / 255.0 * (len(RAMP) - 1)).round().astype(np.int32), 0, len(RAMP) - 1)
    ramp_ord = np.array([ord(c) for c in RAMP], np.uint8)
    chars = ramp_ord[idx]
    d = ((rgb_rc3[..., None, :].astype(np.int32) - PAL_C64[None, None, :, :]) ** 2).sum(-1)
    colors = d.argmin(-1).astype(np.uint8)
    colors = np.where(chars == 32, 0, colors).astype(np.uint8)  # a plain space carries no colour
    return chars, colors


def _frame_grid(img, base_cols, base_rows):
    """One gif frame (rows,cols,3) -> full (base_rows, base_cols) char/colour arrays, letterboxed
    (squeezed/stretched to fit, centred) so every stored animation is exactly base_cols x base_rows."""
    from PIL import Image
    h, w = img.shape[:2]
    fit_c, fit_r = _fit_dims(w, h, base_cols, base_rows)
    pil = Image.fromarray(img).resize((max(1, fit_c), max(1, fit_r)), Image.LANCZOS)
    small = np.asarray(pil, dtype=np.uint8)
    chars = np.full((base_rows, base_cols), 32, np.uint8)
    colors = np.zeros((base_rows, base_cols), np.uint8)
    c0 = (base_cols - fit_c) // 2
    r0 = (base_rows - fit_r) // 2
    fc, fcol = _quantize(small)
    chars[r0:r0 + fit_r, c0:c0 + fit_c] = fc
    colors[r0:r0 + fit_r, c0:c0 + fit_c] = fcol
    return chars, colors


# ---------------------------------------------------------------- bin format
def _write_bin(path, base_cols, base_rows, frames_chars, frames_colors, frame_ms):
    with open(path, "wb") as f:
        f.write(MAGIC)
        f.write(struct.pack("<HHHH", base_cols, base_rows, len(frames_chars), min(frame_ms, 65535)))
        prev_c = prev_col = None
        for chars, colors in zip(frames_chars, frames_colors):
            if prev_c is None:
                f.write(b"\x01")  # full frame marker
                f.write(chars.tobytes())
                f.write(colors.tobytes())
            else:
                changed = np.nonzero((chars != prev_c) | (colors != prev_col))
                idxs = (changed[0].astype(np.int64) * base_cols + changed[1]).astype(np.uint16)
                f.write(b"\x00")
                f.write(struct.pack("<I", len(idxs)))
                f.write(idxs.tobytes())
                f.write(chars.flatten()[idxs].tobytes())
                f.write(colors.flatten()[idxs].tobytes())
            prev_c, prev_col = chars, colors


def convert_gif(gif_path, out_path, base_cols, base_rows, frame_ms=None):
    frames, durations = _load_gif_frames(gif_path)
    if not frames:
        return False
    fchars, fcolors = [], []
    for img in frames:
        c, k = _frame_grid(img, base_cols, base_rows)
        fchars.append(c)
        fcolors.append(k)
    ms = frame_ms or int(round(sum(durations) / len(durations)))
    _write_bin(out_path, base_cols, base_rows, fchars, fcolors, ms)
    return True


def load_bin(path):
    """-> (base_cols, base_rows, frame_ms, [ (chars, colors), ... ]) or None if unreadable."""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return None
    if len(data) < 12 or data[:4] != MAGIC:
        return None
    base_cols, base_rows, nframes, frame_ms = struct.unpack_from("<HHHH", data, 4)
    n = base_cols * base_rows
    off = 12
    frames = []
    prev_c = prev_col = None
    try:
        for _ in range(nframes):
            kind = data[off]
            off += 1
            if kind == 1:
                chars = np.frombuffer(data, np.uint8, n, off).reshape(base_rows, base_cols).copy()
                off += n
                colors = np.frombuffer(data, np.uint8, n, off).reshape(base_rows, base_cols).copy()
                off += n
            else:
                (cnt,) = struct.unpack_from("<I", data, off)
                off += 4
                idxs = np.frombuffer(data, np.uint16, cnt, off)
                off += cnt * 2
                chs = np.frombuffer(data, np.uint8, cnt, off)
                off += cnt
                cols_ = np.frombuffer(data, np.uint8, cnt, off)
                off += cnt
                chars = prev_c.copy()
                colors = prev_col.copy()
                flat_c, flat_k = chars.reshape(-1), colors.reshape(-1)
                flat_c[idxs] = chs
                flat_k[idxs] = cols_
            frames.append((chars, colors))
            prev_c, prev_col = chars, colors
    except (ValueError, IndexError):
        return None
    return base_cols, base_rows, frame_ms, frames


# ---------------------------------------------------------------- ensure / resample / playback
def ensure_bins(cfg, log=None):
    """Rebuild ascii.bin / petscii.bin from start.gif if either is missing/broken,
    and the gif is present. Returns True if an intro is available to play."""
    gif = cfg.paths_intro_gif
    ascii_bin = cfg.paths_intro_ascii_bin
    pet_bin = cfg.paths_intro_petscii_bin
    have_gif = os.path.isfile(gif)
    if not have_gif:
        return False
    need_ascii = not os.path.isfile(ascii_bin) or load_bin(ascii_bin) is None
    need_pet = not os.path.isfile(pet_bin) or load_bin(pet_bin) is None
    if need_ascii:
        if log:
            log("rebuilding %s from %s", ascii_bin, gif)
        convert_gif(gif, ascii_bin, *ASCII_BASE)
    if need_pet:
        if log:
            log("rebuilding %s from %s", pet_bin, gif)
        convert_gif(gif, pet_bin, *PETSCII_BASE)
    return True


_resample_cache = {}


def _resample_idx(base_n, out_n):
    key = (base_n, out_n)
    idx = _resample_cache.get(key)
    if idx is None:
        idx = np.minimum((np.arange(out_n) * base_n) // max(1, out_n), base_n - 1)
        _resample_cache[key] = idx
    return idx


def frame_to_grid(chars, colors, out_cols, out_rows):
    base_rows, base_cols = chars.shape
    ci = _resample_idx(base_cols, out_cols)
    ri = _resample_idx(base_rows, out_rows)
    g = Grid(out_rows, out_cols, fg=WHITE, bg=BLACK)
    sub_c = chars[ri][:, ci]
    sub_k = colors[ri][:, ci]
    g.ch[:, :] = sub_c
    g.fg[:, :] = sub_k
    g.fg[sub_c == 32] = WHITE
    return g


async def play_intro(session, bin_path, hold_seconds=2.0):
    """Play once, then hold the last frame for hold_seconds. Returns normally when done
    (including if nothing could be loaded - the caller just proceeds to the main menu)."""
    import asyncio

    loaded = load_bin(bin_path)
    if loaded is None:
        return
    base_cols, base_rows, frame_ms, frames = loaded
    dt = max(0.02, frame_ms / 1000.0)
    for chars, colors in frames:
        if session.closed:
            return
        g = frame_to_grid(chars, colors, session.cols, session.rows)
        await session.present(g)
        await asyncio.sleep(dt)
    if not session.closed:
        await asyncio.sleep(hold_seconds)
