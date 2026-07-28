from omnia_api.services.depth_experience_gate import (
    FLAT_SVG_PSEUDO_3D,
    NO_DEPTH_EXPERIENCE,
    scan,
)


def test_managed_runtime_is_real_depth_evidence() -> None:
    report = scan({
        "index.html": (
            '<script src="assets/omnia-depth.js"></script>'
            '<main data-omnia-depth="product"><h1>Product</h1></main>'
        ),
        "assets/omnia-depth.js": "WebGLRenderingContext",
    })
    assert report.passed
    assert report.kind == "omnia-webgl"


def test_flat_svg_parallax_cannot_claim_3d() -> None:
    report = scan({
        "index.html": (
            '<main><h1>Fake 3D</h1><svg data-parallax="0.2" viewBox="0 0 10 10">'
            "<path d='M0 0h10v10z'/></svg></main>"
        )
    })
    assert not report.passed
    assert NO_DEPTH_EXPERIENCE in report.classes
    assert FLAT_SVG_PSEUDO_3D in report.classes


def test_authored_webgl_passes() -> None:
    report = scan({
        "src/Hero.tsx": (
            'export function Hero(){return <canvas/>};'
            'canvas.getContext("webgl2");requestAnimationFrame(render)'
        )
    })
    assert report.passed
    assert report.kind == "webgl"


def test_layered_media_requires_input_and_perspective() -> None:
    good = scan({
        "src/Hero.tsx": (
            '<section><img data-depth-layer="1" src="/product.webp"/></section>;'
            'addEventListener("pointermove", onMove);'
            'const style={transform:"perspective(900px) translateZ(20px)"}'
        )
    })
    assert good.passed
    assert good.kind == "layered-media"


def test_backend_only_is_inert() -> None:
    report = scan({"src/api/main.py": "def health(): return {'ok': True}"})
    assert report.passed
    assert not report.judged
