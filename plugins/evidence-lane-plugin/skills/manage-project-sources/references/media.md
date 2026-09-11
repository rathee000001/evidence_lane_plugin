# Images and media

Images/media has its separate `images_ocr` database and files. Use
`media_index`/`media_refresh` for raster frames, passive SVG facts or bounded
FFmpeg container metadata. `media_query` and `media_read` return exact stored
facts and bytes. `media_transform` publishes a selected raster derivative;
original bytes remain readable. `media_ocr` and `media_extract` create separate
frame OCR, PNG frame or PCM WAV artifacts with their original snapshot binding.
Their read actions do not advance current source state. Recognition accuracy,
visual equivalence, speech transcription and source frame PTS are separate
claims. `media_export` preserves the format and destination frame/pixel limits,
parses before writing and renews Sources, destination facts and any selected
`images_ocr.structure` MMD/DOT/pointer view. Its destination locator and post-write
recovery semantics match the PDF export described above. Neither action executes
SVG scripts or follows external media resources.

Raster intake covers PNG, JPEG, TIFF, BMP, GIF and WebP. A transform emits one
PNG, JPEG, TIFF or WebP frame; animated input requires an explicit one-based
frame. Orientation, crop, explicit resize, right-angle rotation, flip and color
mode run in that order. JPEG alpha needs a chosen background. Metadata may be
stripped or retained only for supported EXIF, ICC, DPI and PNG text fields;
complete metadata or color-profile conversion is not claimed.

OCR runs on selected raster frames using measured RapidOCR/OpenCV or Tesseract
routes and locally verified assets. Inspect low-confidence lines and empty-frame
review regions; an empty OCR result does not prove the image has no text.
`media_query` honors `match_mode` for all-term or any-term OCR searches. OCR
coordinates refer to the frame after EXIF orientation. Historical OCR remains
bound to its original snapshot after a transform or refresh.

Audio/video intake covers AVI, FLAC, M4A, MKV, MOV, MP3, MP4, OGG, WAV and WebM
containers. Windows FFmpeg routes inspect metadata and extract a bounded PNG
frame or PCM WAV segment. Container support does not assert every codec, full
decoding or timestamp accuracy. The public limits and live tool readiness still
apply; only metadata and passive structure are available without the matching
transformation or OCR route.
