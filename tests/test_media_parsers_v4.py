"""Representative raster/vector inputs and independently decoded derivative bytes."""
import io

import pytest
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.media_contracts import MediaTransform
from evidence_lane_plugin.media_parsers import digest, parse_media
from evidence_lane_plugin.media_transform import transform_image
from PIL import Image, PngImagePlugin


def raster_bytes(format='PNG', *, mode='RGB', size=(12, 8), color='navy', **options):
    output = io.BytesIO()
    with Image.new(mode, size, color) as image:
        image.save(output, format=format, **options)
    return output.getvalue()


def transform_request(raw, **arguments):
    return MediaTransform(snapshot_id='a' * 64, expected_sha256=digest(raw),
        **{'logical_name': 'result.png', **arguments})


@pytest.mark.parametrize(('extension', 'format'), [('.png', 'PNG'), ('.jpg', 'JPEG'),
    ('.jpeg', 'JPEG'), ('.tif', 'TIFF'), ('.tiff', 'TIFF'), ('.bmp', 'BMP'),
    ('.gif', 'GIF'), ('.webp', 'WEBP')])
def test_raster_facts_match_independently_created_source(extension, format):
    raw = raster_bytes(format)
    facts = parse_media('fixture' + extension, raw)
    assert facts['metadata']['format'] == format
    assert (facts['metadata']['width'], facts['metadata']['height']) == (12, 8)
    assert facts['counts']['frame'] == 1
    assert facts['native_evidence']['source_sha256'] == digest(raw)
    assert not facts['fidelity']['ocr_performed']
    with pytest.raises(LaneError, check=lambda e: e.code == 'MEDIA_FORMAT_EXTENSION_MISMATCH'):
        parse_media('wrong.bmp' if extension != '.bmp' else 'wrong.png', raw)


def test_animated_frames_require_explicit_selection_and_keep_source_durations():
    output = io.BytesIO()
    first, second = Image.new('RGB', (5, 3), 'red'), Image.new('RGB', (5, 3), 'blue')
    first.save(output, format='GIF', save_all=True, append_images=[second], duration=[80, 140], loop=0)
    first.close()
    second.close()
    raw = output.getvalue()
    facts = parse_media('frames.gif', raw)
    assert [r['duration_ms'] for r in facts['items'] if r['kind'] == 'frame'] == [80, 140]
    with pytest.raises(LaneError, check=lambda e: e.code == 'MEDIA_ANIMATED_FRAME_SELECTION_REQUIRED'):
        transform_image(raw, transform_request(raw))
    result, evidence = transform_image(raw, transform_request(raw, frame=2))
    with Image.open(io.BytesIO(result)) as image:
        assert image.convert('RGB').getpixel((2, 1)) == (0, 0, 255)
        assert getattr(image, 'n_frames', 1) == 1
    assert evidence['source_frames'] == 2 and evidence['frame'] == 2


def test_svg_passive_structure_and_external_references_are_only_facts():
    raw = b'<svg xmlns="http://www.w3.org/2000/svg" width="30" onload="alert(1)"><script>bad()</script><image href="https://invalid.example/image"/><text>Hello <tspan>media</tspan></text></svg>'
    facts = parse_media('fixture.svg', raw)
    assert facts['metadata']['active_content_present']
    assert facts['metadata']['reference_count'] == 1
    assert not facts['metadata']['references_followed'] and not facts['metadata']['scripts_executed']
    assert [r['text'] for r in facts['items'] if r['kind'] == 'svg_text'] == ['Hello media']
    with pytest.raises(LaneError, check=lambda e: e.code == 'MEDIA_SVG_XML_INVALID'):
        parse_media('bad.svg', b'<!DOCTYPE svg [<!ENTITY e SYSTEM "file:///secret">]><svg xmlns="http://www.w3.org/2000/svg">&e;</svg>')


@pytest.mark.parametrize(('format', 'extension'), [('PNG', '.png'), ('JPEG', '.jpg'), ('WEBP', '.webp'), ('TIFF', '.tiff')])
@pytest.mark.parametrize('metadata', ['strip', 'preserve_supported'])
def test_output_formats_reopen_and_metadata_policy_is_real(format, extension, metadata):
    exif = Image.Exif()
    exif[315] = 'Private photographer'
    pnginfo = PngImagePlugin.PngInfo()
    pnginfo.add_text('Comment', 'Private source comment')
    raw = raster_bytes(exif=exif, pnginfo=pnginfo)
    transformed, evidence = transform_image(raw, transform_request(raw,
        logical_name='output' + extension, output_format=format, metadata=metadata))
    with Image.open(io.BytesIO(transformed)) as image:
        image.load()
        assert image.format == format and image.size == (12, 8)
        if metadata == 'strip':
            assert image.getexif().get(315) is None and 'Comment' not in image.info
        else:
            assert image.getexif().get(315) == 'Private photographer'
            if format == 'PNG':
                assert image.info['Comment'] == 'Private source comment'
    assert evidence['source_sha256'] == digest(raw) and not evidence['source_bytes_mutated']


def test_exif_orientation_crop_rotation_flip_have_explicit_coordinates():
    output = io.BytesIO()
    exif = Image.Exif()
    exif[274] = 6
    with Image.new('RGB', (3, 2)) as source:
        source.putdata([(255, 0, 0), (0, 255, 0), (0, 0, 255),
            (255, 255, 0), (0, 255, 255), (255, 0, 255)])
        source.save(output, format='PNG', exif=exif)
    raw = output.getvalue()
    result, evidence = transform_image(raw, transform_request(raw,
        crop={'left': 0, 'top': 1, 'right': 2, 'bottom': 3}, rotate_clockwise=90, flip='horizontal'))
    with Image.open(io.BytesIO(result)) as image:
        assert image.size == (2, 2)
        assert list(image.get_flattened_data()) == [(0, 255, 255), (255, 0, 255), (0, 255, 0), (0, 0, 255)]
        assert image.getexif().get(274) in (None, 1)
    assert evidence['lossless_pixels_verified']


def test_jpeg_transparency_requires_explicit_background():
    raw = raster_bytes(mode='RGBA', color=(255, 0, 0, 0))
    request = transform_request(raw, output_format='JPEG', logical_name='output.jpg')
    with pytest.raises(LaneError, check=lambda e: e.code == 'MEDIA_JPEG_ALPHA_BACKGROUND_REQUIRED'):
        transform_image(raw, request)
    result, evidence = transform_image(raw, request.model_copy(update={'jpeg_background': '#ffffff'}))
    with Image.open(io.BytesIO(result)) as image:
        assert all(v >= 254 for v in image.getpixel((0, 0)))
    assert evidence['alpha_composited']


def test_pixel_frame_and_invalid_input_limits_reject_without_partial_facts():
    raw = raster_bytes()
    with pytest.raises(LaneError, check=lambda e: e.code == 'MEDIA_PIXEL_BUDGET'):
        parse_media('fixture.png', raw, max_pixels_per_frame=10)
    with pytest.raises(LaneError, check=lambda e: e.code == 'MEDIA_RASTER_INVALID'):
        parse_media('broken.png', b'not a picture')
    with pytest.raises(LaneError, check=lambda e: e.code == 'MEDIA_FORMAT_UNSUPPORTED'):
        parse_media('program.exe', raw)
