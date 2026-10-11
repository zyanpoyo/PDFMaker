"""test_pdfcore.py - pdfcore モジュールの自動検証テストスイート."""

from decimal import Decimal
import tempfile
from pathlib import Path
import pytest
from pypdf import PdfReader

from pdfcore import (
    PAPER_SIZES_MM,
    PageConfig,
    generate_pdf,
    parse_margin_input,
    validate_page_margins,
    layout_label_and_scale,
)
from config_mgr import export_pages_to_json, import_pages_from_json


def test_paper_sizes_and_mediabox():
    """全5サイズ×縦横の計10パターンでMediaBoxがJIS寸法と完全一致（0.05pt以内）することを検証。"""
    pages = []
    for size_name in PAPER_SIZES_MM:
        pages.append(PageConfig(size=size_name, landscape=False, outer_margin_mm=0.5, inner_margin_mm=50.0))
        pages.append(PageConfig(size=size_name, landscape=True, outer_margin_mm=0.5, inner_margin_mm=50.0))

    with tempfile.TemporaryDirectory() as tmpdir:
        pdf_path = str(Path(tmpdir) / "test_sizes.pdf")
        summary = generate_pdf(pages, pdf_path)
        assert summary.total_pages == 10

        reader = PdfReader(pdf_path)
        assert len(reader.pages) == 10

        page_idx = 0
        for size_name, (w_mm, h_mm) in PAPER_SIZES_MM.items():
            # Portrait
            p_port = reader.pages[page_idx]
            expected_w_pt = w_mm * 72.0 / 25.4
            expected_h_pt = h_mm * 72.0 / 25.4
            assert abs(float(p_port.mediabox.width) - expected_w_pt) < 0.05
            assert abs(float(p_port.mediabox.height) - expected_h_pt) < 0.05
            page_idx += 1

            # Landscape
            p_land = reader.pages[page_idx]
            assert abs(float(p_land.mediabox.width) - expected_h_pt) < 0.05
            assert abs(float(p_land.mediabox.height) - expected_w_pt) < 0.05
            page_idx += 1

        # B4_JIS の寸法が ISO B4 (708.66 x 1000.63 pt) ではなく JIS B4 (728.50 x 1031.81 pt) であることを明示確認
        b4_page = reader.pages[6]  # B4_JIS Portrait
        assert abs(float(b4_page.mediabox.width) - (257.0 * 72 / 25.4)) < 0.05
        assert abs(float(b4_page.mediabox.height) - (364.0 * 72 / 25.4)) < 0.05
        assert float(b4_page.mediabox.width) > 720.0  # ISO B4(約708pt)とは異なる


def test_stream_drawing_attributes():
    """PDFストリーム内に 0.25pt線幅、Butt Cap(0 J)、Miter Join(0 j)、黒色(0 0 0 RG) が存在することを検証。"""
    page = PageConfig(size="A4", landscape=False, outer_margin_mm=0.5, inner_margin_mm=50.0)

    with tempfile.TemporaryDirectory() as tmpdir:
        pdf_path = str(Path(tmpdir) / "test_stream.pdf")
        generate_pdf([page], pdf_path)

        with open(pdf_path, "rb") as f:
            content = f.read().decode("latin1")

        # 0.25 pt 線幅 (ReportLabでは .25 w または 0.25 w)
        assert ".25 w" in content or "0.25 w" in content
        # Butt Cap
        assert "0 J" in content
        # Miter Join
        assert "0 j" in content
        # 黒色ストローク
        assert "0 0 0 RG" in content or "0 0 0 rg" in content


def test_viewer_preferences_print_scaling():
    """PDF Catalog に /ViewerPreferences << /PrintScaling /None >> が設定されていることを検証。"""
    page = PageConfig(size="A4", landscape=False, outer_margin_mm=0.5, inner_margin_mm=50.0)

    with tempfile.TemporaryDirectory() as tmpdir:
        pdf_path = str(Path(tmpdir) / "test_vp.pdf")
        generate_pdf([page], pdf_path)

        with open(pdf_path, "rb") as f:
            content = f.read().decode("latin1")

        assert "/ViewerPreferences" in content
        assert "/PrintScaling" in content
        assert "/None" in content


def test_pdf_metadata():
    """PDFドキュメント情報（Title, Author等）が設定されていることを検証。"""
    page = PageConfig(size="A4", landscape=False, outer_margin_mm=0.5, inner_margin_mm=50.0)

    with tempfile.TemporaryDirectory() as tmpdir:
        pdf_path = str(Path(tmpdir) / "test_meta.pdf")
        generate_pdf([page], pdf_path)

        reader = PdfReader(pdf_path)
        meta = reader.metadata
        assert meta.title == "Print Scale Calibration Sheet"
        assert meta.author == "PDFMaker"


def test_margin_validation_success():
    """全角・半角・整数・小数第1位の正常なパースを検証。"""
    assert parse_margin_input("0.5") == Decimal("0.5")
    assert parse_margin_input("５０．０") == Decimal("50.0")
    assert parse_margin_input("  10  ") == Decimal("10")
    assert parse_margin_input("0") == Decimal("0")


def test_margin_validation_errors():
    """異常値（負数、空欄、小数第2位以上、カンマ、文字）が正しく弾かれることを検証。"""
    with pytest.raises(ValueError, match="値を入力してください"):
        parse_margin_input("")

    with pytest.raises(ValueError, match="小数第1位まで"):
        parse_margin_input("0.55")

    with pytest.raises(ValueError, match="数値を入力してください"):
        parse_margin_input("0,5")

    with pytest.raises(ValueError, match="数値を入力してください"):
        parse_margin_input("-1")

    with pytest.raises(ValueError, match="数値を入力してください"):
        parse_margin_input("abc")

    # 用紙寸法オーバーの検証
    with pytest.raises(ValueError, match="外枠＋内枠の合計"):
        # A5縦 (幅148mm) で外枠0.5 + 内枠70 = 70.5mm -> (148 - 10)/2 = 69.0mm を超過
        validate_page_margins("A5", False, 0.5, 70.0)


def test_layout_adaptation_a5():
    """A5縦（外枠0.5mm, 内枠50mm）での内枠幅47mmに対するスケールバー・フォント自動縮小を検証。"""
    page_a5 = PageConfig(size="A5", landscape=False, outer_margin_mm=0.5, inner_margin_mm=50.0)
    layout = layout_label_and_scale(page_a5, page_index=1, total_pages=1)

    assert layout.font_size is not None
    # 幅39mmに入るスケールバー長として 30mm が選択されること
    assert layout.bar_mm == 30
    assert layout.warning is not None
    assert "30mm" in layout.warning


def test_layout_adaptation_a4():
    """A4縦（外枠0.5mm, 内枠50mm）では標準100mmスケールバーと10ptフォントが維持されることを検証。"""
    page_a4 = PageConfig(size="A4", landscape=False, outer_margin_mm=0.5, inner_margin_mm=50.0)
    layout = layout_label_and_scale(page_a4, page_index=1, total_pages=1)

    assert layout.font_size == 10.0
    assert layout.bar_mm == 100
    assert layout.warning is None


def test_config_json_export_and_import():
    """ページ構成JSONのエクスポートとインポートによる復元を検証。"""
    original_pages = [
        PageConfig(size="A4", landscape=False, outer_margin_mm=0.5, inner_margin_mm=50.0),
        PageConfig(size="B4_JIS", landscape=True, outer_margin_mm=1.0, inner_margin_mm=40.0),
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        json_path = str(Path(tmpdir) / "pages.json")
        export_pages_to_json(original_pages, json_path)
        loaded_pages = import_pages_from_json(json_path)

        assert len(loaded_pages) == 2
        assert loaded_pages[0].size == "A4"
        assert loaded_pages[0].landscape is False
        assert loaded_pages[0].outer_margin_mm == 0.5
        assert loaded_pages[0].inner_margin_mm == 50.0

        assert loaded_pages[1].size == "B4_JIS"
        assert loaded_pages[1].landscape is True
        assert loaded_pages[1].outer_margin_mm == 1.0
        assert loaded_pages[1].inner_margin_mm == 40.0
