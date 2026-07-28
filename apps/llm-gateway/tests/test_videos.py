from omnia_gateway.routers.videos import VideoGenerationRequest, _create_upstream_payload


def test_upstream_video_payload_is_silent_by_default() -> None:
    request = VideoGenerationRequest(
        model="seedance-2.0-fast",
        prompt="Slow orbit around a skincare bottle",
        duration=5,
        aspect="16:9",
        first_frame_url="https://media.example.com/source.png",
    )

    payload = _create_upstream_payload(request, "seedance-2.0-fast")

    assert payload["generate_audio"] is False
    assert payload["size"] == "1280x720"
    assert payload["frame_images"] == [
        {
            "type": "image_url",
            "image_url": {"url": "https://media.example.com/source.png"},
            "frame_type": "first_frame",
        }
    ]


def test_upstream_video_payload_allows_explicit_audio() -> None:
    request = VideoGenerationRequest(
        model="seedance-2.0-fast",
        prompt="A scene with native ambient audio",
        generate_audio=True,
    )

    payload = _create_upstream_payload(request, "seedance-2.0-fast")

    assert payload["generate_audio"] is True
