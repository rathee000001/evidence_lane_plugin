"""Tiny synthetic local containers from the hash-verified fixture FFmpeg binary."""
import hashlib
import os
import subprocess

from evidence_lane_plugin.shared_tool_assets import resolve_shared_asset

MEDIA_CASES = [
    ('.wav', 'audio', 'pcm_s16le'), ('.flac', 'audio', 'flac'),
    ('.mp3', 'audio', 'libmp3lame'), ('.ogg', 'audio', 'libvorbis'), ('.m4a', 'audio', 'aac'),
    ('.mp4', 'video', 'libx264'), ('.mov', 'video', 'libx264'), ('.mkv', 'video', 'ffv1'),
    ('.avi', 'video', 'ffv1'), ('.webm', 'video', 'libvpx-vp9'),
]


def container_bytes(folder, extension, kind, codec):
    asset, receipt = resolve_shared_asset('ffmpeg_runtime')
    binary = asset / 'ffmpeg.exe'
    expected = next(row['sha256'] for row in receipt['files'] if row['path'] == 'ffmpeg.exe')
    destination = folder / ('fixture' + extension)
    arguments = [str(binary), '-hide_banner', '-nostdin', '-loglevel', 'error', '-f', 'lavfi', '-i']
    if kind == 'video':
        arguments += ['color=c=red:size=64x48:rate=10', '-c:v', codec, '-pix_fmt', 'yuv420p']
    else:
        arguments += ['sine=frequency=440:sample_rate=16000', '-c:a', codec]
    arguments += ['-t', '0.8', '-threads', '1', '-metadata', 'title=Media fixture 4827', '-y', str(destination)]
    completed = subprocess.run(arguments, capture_output=True, timeout=30, check=False,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), env={**os.environ, 'AV_LOG_FORCE_NOCOLOR': '1'})
    assert completed.returncode == 0, completed.stderr.decode(errors='replace')
    assert hashlib.sha256(binary.read_bytes()).hexdigest() == expected
    return destination.read_bytes(), receipt
