"""HTTP compression: text responses compressed, media and downloads untouched."""
import gzip


def test_html_and_css_are_compressed(client):
    page = client.get("/login-page", headers={"Accept-Encoding": "gzip"})
    assert page.headers["Content-Encoding"] == "gzip"
    assert b"Sign in with Microsoft" in gzip.decompress(page.data)
    # Static files are streamed: compressed with zstd/br/deflate (what browsers send), not gzip
    css = client.get("/static/css/bootstrap.min.css",
                     headers={"Accept-Encoding": "gzip, deflate, br, zstd"})
    assert css.headers["Content-Encoding"] in ("zstd", "br")
    assert len(css.data) < 60_000  # 233 KB raw
    assert "max-age=31536000" in css.headers["Cache-Control"]


def test_no_compression_without_accept_encoding(client):
    page = client.get("/login-page", headers={"Accept-Encoding": "identity"})
    assert "Content-Encoding" not in page.headers
    assert b"Sign in with Microsoft" in page.data


def test_images_are_not_recompressed(client):
    logo = client.get("/static/cloudfuze_logo.png", headers={"Accept-Encoding": "gzip"})
    assert "Content-Encoding" not in logo.headers
