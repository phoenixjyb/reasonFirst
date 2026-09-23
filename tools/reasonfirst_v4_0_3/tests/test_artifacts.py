from __future__ import annotations

from pathlib import Path
import json
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from reasonfirst_codex_bridge.artifacts import scan_artifacts


def main():
    from PIL import Image, ImageDraw
    import fitz

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "reports").mkdir()
        (root / "reports" / "metrics.json").write_text(json.dumps({"latency_ms": 10.2, "ap": 0.91}), encoding="utf-8")

        image = Image.new("RGB", (1200, 800), "white")
        draw = ImageDraw.Draw(image)
        draw.line((40, 700, 1100, 100), fill="black", width=8)
        draw.text((50, 50), "latency curve", fill="black")
        image.save(root / "reports" / "curve.png")

        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), "Benchmark report: latency 10.2 ms, AP 0.91")
        doc.save(root / "reports" / "report.pdf")
        doc.close()

        docx = root / "reports" / "notes.docx"
        with zipfile.ZipFile(docx, "w") as archive:
            archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Optimization notes</w:t></w:r></w:p></w:body></w:document>')

        result = scan_artifacts(root, relative_path="reports", changed_only=False, max_visual_previews=2)
        items = {item["name"]: item for item in result["items"]}
        assert "latency_ms" in items["metrics.json"]["content"]
        assert items["curve.png"]["visual_preview"]["base64"]
        assert len(items["curve.png"]["visual_preview"]["base64"]) <= 9000
        assert "Benchmark report" in items["report.pdf"]["content"]
        assert items["report.pdf"]["page_count"] == 1
        assert items["report.pdf"]["visual_preview"]["base64"]
        assert "Optimization notes" in items["notes.docx"]["content"]
    print("artifact extraction/preview flow: OK")


if __name__ == "__main__":
    main()
