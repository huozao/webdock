from __future__ import annotations

from src.browser.image_input import (
    MAX_INPUT_IMAGES,
    extract_image_urls,
    resolve_image,
    resolve_image_inputs,
)

# 1x1 PNG.
PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
PNG_DATA_URL = f"data:image/png;base64,{PNG_B64}"


def test_extract_image_urls_from_vision_content():
    urls = extract_image_urls(
        [
            {"type": "text", "text": "把这张图改成卡通风格"},
            {"type": "image_url", "image_url": {"url": PNG_DATA_URL}},
        ]
    )

    assert urls == [PNG_DATA_URL]


def test_extract_image_urls_ignores_text_only_content():
    assert extract_image_urls("just a string") == []
    assert extract_image_urls([{"type": "text", "text": "hi"}]) == []


def test_extract_image_urls_accepts_direct_string_image_url():
    assert extract_image_urls([{"image_url": "https://example.com/a.png"}]) == [
        "https://example.com/a.png"
    ]


def test_extract_image_urls_caps_count():
    many = [{"type": "image_url", "image_url": {"url": PNG_DATA_URL}} for _ in range(MAX_INPUT_IMAGES + 3)]

    assert len(extract_image_urls(many)) == MAX_INPUT_IMAGES


def test_resolve_image_decodes_base64_data_url():
    result = resolve_image(PNG_DATA_URL)

    assert result is not None
    data, ext = result
    assert ext == ".png"
    assert data.startswith(b"\x89PNG")


def test_resolve_image_sniffs_extension_when_mime_missing():
    result = resolve_image(f"data:;base64,{PNG_B64}")

    assert result is not None
    assert result[1] == ".png"


def test_resolve_image_prefers_declared_mime_for_extension():
    result = resolve_image(f"data:image/jpeg;base64,{PNG_B64}")

    assert result is not None
    assert result[1] == ".jpg"


def test_resolve_image_rejects_unknown_scheme():
    assert resolve_image("ftp://example.com/x.png") is None


def test_resolve_image_inputs_skips_failures():
    out = resolve_image_inputs([PNG_DATA_URL, "not-a-url", "data:nocomma"])

    assert len(out) == 1
