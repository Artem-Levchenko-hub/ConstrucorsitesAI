"""Tests for direct image-generation edit helpers."""

from __future__ import annotations

from omnia_api.services.image_edit import (
    find_first_img,
    is_image_request,
    rebuild_img_with_gen,
)


def test_is_image_request_true_for_generate() -> None:
    assert is_image_request("сгенерируй прям картинку, которая подчеркнёт стилистику")
    assert is_image_request("добавь фото интерьера")
    assert is_image_request("поменяй изображение на другое")
    assert is_image_request("generate an image of sushi")


def test_is_image_request_false_for_non_image_edits() -> None:
    assert not is_image_request("поменяй фон на тёмно-изумрудный")
    assert not is_image_request("сделай заголовок крупнее")
    assert not is_image_request("поменяй текст кнопки")
    assert not is_image_request("")


def test_find_first_img() -> None:
    block = (
        '<section><h1>x</h1>'
        '<img src="https://cdn/x.jpg" alt="Ролл" class="w-full rounded-full" />'
        "</section>"
    )
    hit = find_first_img(block)
    assert hit is not None
    start, end, tag = hit
    assert tag.startswith("<img")
    assert block[start:end] == tag
    assert find_first_img("<section><p>no image</p></section>") is None


def test_rebuild_img_with_gen_keeps_alt_and_class() -> None:
    old = '<img src="https://cdn/x.jpg" alt="Ролл Yūgen" class="w-full rounded-full" />'
    out = rebuild_img_with_gen(old, "luxury sushi, gold leaf, moody 85mm")
    assert 'data-omnia-gen="luxury sushi, gold leaf, moody 85mm"' in out
    assert 'alt="Ролл Yūgen"' in out
    assert 'class="w-full rounded-full"' in out
    assert "https://cdn/x.jpg" not in out  # old src dropped


def test_rebuild_escapes_quotes_in_prompt() -> None:
    out = rebuild_img_with_gen('<img src="x" alt="a">', 'a "fancy" shot')
    # double quotes in the prompt must not break the attribute
    assert 'data-omnia-gen="a \'fancy\' shot"' in out
