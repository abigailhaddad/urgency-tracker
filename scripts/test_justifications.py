"""Run: python -m pytest scripts/test_justifications.py  (no network)."""
import pathlib
import sys
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import justifications as J


def test_empty_pdf_does_not_shift_text_onto_wrong_pdf():
    atts = [{"name": "scan.pdf", "resourceId": "1"},
            {"name": "Justification and Approval.pdf", "resourceId": "2"},
            {"name": "other.pdf", "resourceId": "3"}]
    texts = {b"1": "", b"2": "JA TEXT " * 30, b"3": "OTHER TEXT " * 30}
    with mock.patch.object(J, "_sam_attachments", return_value=atts), \
         mock.patch.object(J, "_sam_get", side_effect=lambda url, *a, **k: url.split("/")[-2].encode()), \
         mock.patch.object(J, "_pdf_text", side_effect=lambda raw: texts[raw]), \
         mock.patch.object(J, "_justification_notices", return_value=[{"notice_id": "abc-def"}]):
        rec = J.fetch_justification(None, "P1", "S1")
    assert rec["status"] == "ok"
    assert "JA TEXT" in rec["text"] and "OTHER TEXT" not in rec["text"]
