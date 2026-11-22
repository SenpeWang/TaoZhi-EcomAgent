"""验证手机配件 PDF 原件和来源清单的一致性。"""

import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest

CORPUS = Path(__file__).resolve().parents[1] / "data" / "accessory_pdfs"
MANIFEST = json.loads((CORPUS / "manifest.json").read_text(encoding="utf-8"))
PDF_FILES = MANIFEST["files"]
OFFICIAL_LINKS = MANIFEST["official_links"]


def test_corpus_lists_originals_and_official_links():
    assert MANIFEST["schema_version"] == 1
    files = [item["file"] for item in PDF_FILES]
    hashes = [item["sha256"] for item in PDF_FILES]
    assert len(files) == len(set(files))
    assert len(hashes) == len(set(hashes))
    assert set(files) == {path.name for path in CORPUS.glob("*.pdf")}
    assert OFFICIAL_LINKS
    pdf_urls = set()
    for item in PDF_FILES + OFFICIAL_LINKS:
        assert item["title"] and item["publisher"] and item["categories"] and item["languages"]
        for key in ("source_page_url", "pdf_url"):
            url = urlsplit(item[key])
            assert url.scheme == "https" and url.hostname and url.username is None
        assert item["pdf_url"] not in pdf_urls
        pdf_urls.add(item["pdf_url"])
    for item in OFFICIAL_LINKS:
        assert "file" not in item and "redistribution" not in item


@pytest.mark.parametrize("item", PDF_FILES, ids=[item["file"] for item in PDF_FILES])
def test_original_pdf_bytes_and_permission(item):
    path = CORPUS / item["file"]
    content = path.read_bytes()
    assert not path.is_symlink()
    assert content.startswith(b"%PDF-")
    assert len(content) == item["size_bytes"]
    assert 0 < item["size_bytes"] <= 20 * 1024 * 1024
    assert hashlib.sha256(content).hexdigest() == item["sha256"]
    permission = item["redistribution"]
    assert permission["basis"] and permission["attribution"]
    assert permission["modified"] is False
    assert permission["terms_url"].startswith("https://")
    if "notice_file" in permission:
        notice = CORPUS / permission["notice_file"]
        assert notice.parent == CORPUS and notice.is_file()


@pytest.mark.parametrize("item", PDF_FILES, ids=[item["file"] for item in PDF_FILES])
def test_original_pdf_text_can_be_ingested(item):
    import fitz

    with fitz.open(CORPUS / item["file"]) as pdf:
        assert not pdf.is_encrypted
        assert 1 <= len(pdf) == item["pages"] <= 200
        body = "\n\n".join(page.get_text("text") for page in pdf)
        assert body.strip() and len(body) <= 200_000
