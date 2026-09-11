"""Each advertised container family through the real owned media child."""
import base64
import io
import sys
import wave
from pathlib import Path

import pytest
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.media_contracts import MediaExtract
from evidence_lane_plugin.media_parsers import digest
from evidence_lane_plugin.media_process import invoke_media
from PIL import Image

from .media_fixtures import MEDIA_CASES, container_bytes
from .test_pdf_parsers_v4 import pdf_assets as pdf_assets  # noqa: PLC0414


def arguments(raw, name):
    return {'logical_name': name, 'content_base64': base64.b64encode(raw).decode(),
        'expected_sha256': digest(raw)}


@pytest.mark.parametrize(('extension', 'kind', 'codec'), MEDIA_CASES)
def test_real_container_probe_and_bounded_derivative(pdf_assets, tmp_path, extension, kind, codec):
    raw, runtime = container_bytes(tmp_path, extension, kind, codec)
    args = arguments(raw, 'fixture' + extension)
    response = invoke_media('parse', {**args, 'parse_options': {'max_frames': 5, 'max_pixels_per_frame': 100000}})
    facts = response['facts']
    assert facts['metadata']['kind'] == 'media'
    streams = [row for row in facts['items'] if row['kind'] == 'stream']
    assert len(streams) == 1 and streams[0]['stream_type'] == kind
    assert facts['native_evidence']['runtime_sha256'] == runtime['files_sha256']
    assert not facts['metadata']['full_decode_verified'] and not facts['metadata']['speech_transcribed']
    assert response['process_evidence']['owner'] == 'engine_owned_media_child'
    assert response['process_evidence']['interpreter_sha256'] == digest(Path(sys._base_executable).read_bytes())
    request = MediaExtract(snapshot_id='a' * 64, kind='video_frame' if kind == 'video' else 'audio_segment',
        start_seconds=0.1, duration_seconds=0.25, max_width=32, max_height=24, sample_rate=8000)
    extracted = invoke_media('extract', {**args, 'request': request.model_dump(mode='json')})
    output = base64.b64decode(extracted['content_base64'])
    assert digest(output) == extracted['sha256'] and extracted['input_sha256'] == digest(raw)
    assert extracted['evidence']['metadata_stripped'] and not extracted['evidence']['source_bytes_mutated']
    if kind == 'video':
        with Image.open(io.BytesIO(output)) as image:
            assert image.format == 'PNG' and image.size == (32, 24)
            red, green, blue = image.convert('RGB').getpixel((16, 12))
            assert red > 180 and green < 40 and blue < 40
        assert extracted['details']['timestamp_basis'] == 'requested_seek_time_exact_source_pts_not_claimed'
    else:
        with wave.open(io.BytesIO(output)) as audio:
            assert audio.getnchannels() == 1 and audio.getsampwidth() == 2 and audio.getframerate() == 8000
            assert 1900 <= audio.getnframes() <= 2000
            samples = memoryview(audio.readframes(audio.getnframes())).cast('h')
            assert max(samples) > 1000 and min(samples) < -1000
    assert (tmp_path / ('fixture' + extension)).read_bytes() == raw


@pytest.mark.parametrize('fault', ['mismatch', 'missing_stream', 'past_end', 'bad_digest', 'unsafe_playlist'])
def test_owned_native_failures_do_not_return_partial_media(pdf_assets, tmp_path, fault):
    raw, _ = container_bytes(tmp_path, '.wav', 'audio', 'pcm_s16le')
    args = arguments(raw, 'fixture.wav')
    if fault in {'mismatch', 'unsafe_playlist'}:
        if fault == 'mismatch':
            args['logical_name'] = 'fixture.mp4'
        else:
            raw = b'#EXTM3U\n#EXTINF:1\nhttps://invalid.example/media\n'
            args = arguments(raw, 'fixture.mp4')
        operation, payload = 'parse', {**args, 'parse_options': {'max_frames': 5, 'max_pixels_per_frame': 100000}}
    else:
        request = MediaExtract(snapshot_id='a' * 64, kind='audio_segment', stream=1 if fault == 'missing_stream' else 0,
            start_seconds=5 if fault == 'past_end' else 0)
        if fault == 'bad_digest':
            args['expected_sha256'] = '0' * 64
        operation, payload = 'extract', {**args, 'request': request.model_dump(mode='json')}
    with pytest.raises(LaneError) as error:
        invoke_media(operation, payload)
    allowed = {'mismatch': {'MEDIA_NATIVE_OPERATION_FAILED', 'MEDIA_FORMAT_EXTENSION_MISMATCH'},
        'unsafe_playlist': {'MEDIA_NATIVE_OPERATION_FAILED'}, 'missing_stream': {'MEDIA_NATIVE_OPERATION_FAILED'},
        'past_end': {'MEDIA_EXTRACTION_VERIFICATION'}, 'bad_digest': {'MEDIA_INPUT_CHANGED'}}
    assert error.value.code in allowed[fault]
