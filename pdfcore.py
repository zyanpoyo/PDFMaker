"""pdfcore.py - 複合機 印刷拡縮検証用PDF生成コアモジュール.

用紙定義、入力値バリデーション、テキスト・スケールバーの自動レイアウト、
およびReportLabを用いた高精度ベクターPDF描画を提供します。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re
import unicodedata
from typing import Sequence

from reportlab.lib.units import mm
from reportlab.pdfbase import pdfdoc, pdfmetrics
from reportlab.pdfgen import canvas


# ==============================================================================
# 用紙サイズ定義 (ミリメートル単位・JIS規格厳密指定)
# ==============================================================================
PAPER_SIZES_MM: dict[str, tuple[float, float]] = {
    "A3": (297.0, 420.0),
    "A4": (210.0, 297.0),
    "A5": (148.0, 210.0),
    "B4_JIS": (257.0, 364.0),
    "B5_JIS": (182.0, 257.0),
}

AVAILABLE_PAPER_SIZES: list[str] = list(PAPER_SIZES_MM.keys())

DEFAULT_OUTER_MARGIN_MM = 0.5
DEFAULT_INNER_MARGIN_MM = 50.0
LINE_WIDTH_PT = 0.25
MIN_INNER_BOX_MM = 10.0  # 内枠の最小幅・高さ (mm)
INNER_PADDING_MM = 4.0   # 内枠からテキスト・スケールバーまでのマージン (mm)


def page_size_mm(name: str, landscape: bool) -> tuple[float, float]:
    """用紙名と向きに応じた寸法 (幅mm, 高さmm) を返す。"""
    if name not in PAPER_SIZES_MM:
        raise ValueError(f"未知の用紙サイズです: {name}")
    w, h = PAPER_SIZES_MM[name]
    return (h, w) if landscape else (w, h)


# ==============================================================================
# データクラス定義
# ==============================================================================
@dataclass
class PageConfig:
    """1ページ分の構成設定。"""
    size: str
    landscape: bool
    outer_margin_mm: float
    inner_margin_mm: float

    @property
    def dimensions_mm(self) -> tuple[float, float]:
        return page_size_mm(self.size, self.landscape)

    @property
    def orientation_label(self) -> str:
        return "Landscape" if self.landscape else "Portrait"

    def to_dict(self) -> dict:
        return {
            "size": self.size,
            "landscape": self.landscape,
            "outer_margin_mm": self.outer_margin_mm,
            "inner_margin_mm": self.inner_margin_mm,
        }

    @classmethod
    def from_dict(cls, data: dict) -> PageConfig:
        return cls(
            size=str(data["size"]),
            landscape=bool(data["landscape"]),
            outer_margin_mm=float(data["outer_margin_mm"]),
            inner_margin_mm=float(data["inner_margin_mm"]),
        )


@dataclass
class LayoutResult:
    """テキストおよびスケールバーの配置計算結果。"""
    font_size: float | None
    lines: list[str]
    bar_mm: int | None
    warning: str | None


@dataclass
class GenerationSummary:
    """PDF生成結果のサマリー。"""
    total_pages: int
    output_path: str
    page_warnings: list[tuple[int, str]]  # (ページ番号 1-indexed, 警告メッセージ)


# ==============================================================================
# 入力バリデーション
# ==============================================================================
_NUMBER_PATTERN = re.compile(r"^\d+(\.\d)?$")


def parse_margin_input(text: str, field_name: str = "マージン") -> Decimal:
    """マージン入力文字列を検証・正規化し、Decimal として返す。

    - 全角文字は半角に正規化
    - 0以上の数値、小数第1位まで許容
    - 負数、空欄、小数第2位以上、カンマ、文字混入などは ValueError
    """
    normalized = unicodedata.normalize("NFKC", text).strip()
    if not normalized:
        raise ValueError(f"{field_name}の値を入力してください。")

    if not _NUMBER_PATTERN.match(normalized):
        if "." in normalized:
            parts = normalized.split(".")
            if len(parts) == 2 and len(parts[1]) > 1:
                raise ValueError(f"{field_name}は小数第1位までで入力してください。(入力値: {text})")
        raise ValueError(f"{field_name}には0以上の数値を入力してください。(入力値: {text})")

    try:
        val = Decimal(normalized)
    except InvalidOperation:
        raise ValueError(f"{field_name}の数値変換に失敗しました。(入力値: {text})")

    if val < 0:
        raise ValueError(f"{field_name}には0以上の値を指定してください。")

    return val


def validate_page_margins(
    size_name: str,
    landscape: bool,
    outer_mm: float,
    inner_mm: float,
) -> None:
    """用紙寸法に対してマージン値が有効か検証する。

    条件: min(幅 - 2*(外+内), 高さ - 2*(外+内)) >= MIN_INNER_BOX_MM
    """
    if outer_mm < 0 or inner_mm < 0:
        raise ValueError("外枠マージンおよび内枠マージンは0以上である必要があります。")

    w_mm, h_mm = page_size_mm(size_name, landscape)
    min_dim = min(w_mm, h_mm)
    max_margin_sum = (min_dim - MIN_INNER_BOX_MM) / 2.0

    current_sum = outer_mm + inner_mm
    if current_sum > max_margin_sum:
        orient = "横 (Landscape)" if landscape else "縦 (Portrait)"
        raise ValueError(
            f"用紙「{size_name} ({orient})」の内枠サイズが不足します。\n"
            f"外枠＋内枠の合計は {max_margin_sum:.1f} mm 以下にしてください。"
            f"(現在値の合計: {current_sum:.1f} mm)"
        )


# ==============================================================================
# レイアウト計算エンジン (狭い内枠への自動適応)
# ==============================================================================
def layout_label_and_scale(
    page: PageConfig,
    page_index: int,
    total_pages: int,
) -> LayoutResult:
    """内枠内に収まるようにテキストのフォントサイズ・折り返しおよびスケールバー長を決定する。"""
    w_mm, h_mm = page.dimensions_mm
    outer = page.outer_margin_mm
    inner = page.inner_margin_mm

    inner_w_mm = w_mm - 2 * (outer + inner)
    inner_h_mm = h_mm - 2 * (outer + inner)

    avail_w_mm = inner_w_mm - 2 * INNER_PADDING_MM
    avail_h_mm = inner_h_mm - 2 * INNER_PADDING_MM

    if avail_w_mm <= 0 or avail_h_mm <= 0:
        return LayoutResult(
            font_size=None,
            lines=[],
            bar_mm=None,
            warning="内枠が極小のためラベルとスケールバーを省略しました",
        )

    avail_w_pt = avail_w_mm * mm
    avail_h_pt = avail_h_mm * mm

    # スケールバーの長さを決定 (100, 50, 30, 20, 10 mm のうち収まる最大値)
    scale_bar_candidates = [100, 50, 30, 20, 10]
    selected_bar_mm: int | None = None
    for bar_len in scale_bar_candidates:
        if bar_len <= avail_w_mm:
            selected_bar_mm = bar_len
            break

    # フォントサイズ候補: 10, 9, 8, 7 pt
    font_candidates = [10.0, 9.0, 8.0, 7.0]

    for fsize in font_candidates:
        lines: list[str] = []

        # 1. Size
        lines.append(f"Size: {page.size} ({page.orientation_label})")

        # 2. Dimensions
        dim_line = f"Dimensions: {w_mm:.1f} mm x {h_mm:.1f} mm"
        if pdfmetrics.stringWidth(dim_line, "Courier", fsize) <= avail_w_pt:
            lines.append(dim_line)
        else:
            lines.append("Dimensions:")
            lines.append(f"  {w_mm:.1f} mm x {h_mm:.1f} mm")

        # 3. Margins
        margin_full = f"Outer: {outer:.1f} mm / Inner: {inner:.1f} mm"
        if pdfmetrics.stringWidth(margin_full, "Courier", fsize) <= avail_w_pt:
            lines.append(margin_full)
        else:
            lines.append(f"Outer: {outer:.1f} mm")
            lines.append(f"Inner: {inner:.1f} mm")

        # 4. Page
        lines.append(f"Page: {page_index} / {total_pages}")

        # 全行が幅に収まっているか確認
        all_fit_w = all(
            pdfmetrics.stringWidth(line, "Courier", fsize) <= avail_w_pt
            for line in lines
        )
        if not all_fit_w:
            continue

        # 高さ計算
        line_height = fsize * 1.25
        text_height = len(lines) * line_height
        bar_height_pt = (4.0 * mm + 3.0 * mm + 7.0) if selected_bar_mm else 0.0
        total_needed_pt = text_height + bar_height_pt

        if total_needed_pt <= avail_h_pt:
            warning = None
            if fsize < 10.0 or selected_bar_mm != 100:
                warning = f"縮小表示: {int(fsize)}pt / バー{selected_bar_mm or 0}mm"
            return LayoutResult(
                font_size=fsize,
                lines=lines,
                bar_mm=selected_bar_mm,
                warning=warning,
            )

    # どのフォントサイズでも収まらない場合
    return LayoutResult(
        font_size=None,
        lines=[],
        bar_mm=None,
        warning="スペース不足のためラベルとスケールバーを省略しました",
    )


# ==============================================================================
# PDF描画ルーチン
# ==============================================================================
def draw_scale_bar(
    c: canvas.Canvas,
    x_pt: float,
    y_pt: float,
    bar_mm: int,
) -> None:
    """指定座標に高精度スケールバーを描画する。

    x_pt, y_pt: 主線の左端座標
    """
    bar_len_pt = bar_mm * mm

    c.saveState()
    c.setLineWidth(LINE_WIDTH_PT)
    c.setStrokeColorRGB(0, 0, 0)
    c.setLineCap(0)  # Butt Cap (端点での余計な延長を防止)
    c.setLineJoin(0)

    # 主線
    c.line(x_pt, y_pt, x_pt + bar_len_pt, y_pt)

    # 目盛りティック
    step_mm = 10
    num_ticks = bar_mm // step_mm
    for tick_idx in range(num_ticks + 1):
        tick_x = x_pt + (tick_idx * step_mm) * mm
        is_endpoint = (tick_idx == 0 or tick_idx == num_ticks)
        tick_h = 2.5 * mm if is_endpoint else 1.5 * mm
        c.line(tick_x, y_pt, tick_x, y_pt + tick_h)

    # 1mm刻みサブティック (bar_mm <= 50 の場合)
    if bar_mm <= 50:
        for mm_idx in range(bar_mm + 1):
            if mm_idx % 10 != 0:
                sub_x = x_pt + mm_idx * mm
                c.line(sub_x, y_pt, sub_x, y_pt + 0.8 * mm)

    # 数値ラベル (ティック直上)
    c.setFont("Courier", 7.0)
    label_y = y_pt + 3.0 * mm

    # 0 mm
    c.drawCentredString(x_pt, label_y, "0")

    # 中間点
    mid_mm = bar_mm // 2
    if mid_mm >= 10:
        c.drawCentredString(x_pt + mid_mm * mm, label_y, f"{mid_mm}")

    # 終点
    c.drawCentredString(x_pt + bar_len_pt, label_y, f"{bar_mm}mm")

    c.restoreState()


def draw_page(
    c: canvas.Canvas,
    page: PageConfig,
    page_index: int,
    total_pages: int,
) -> LayoutResult:
    """1ページ分の枠線・ラベル・スケールバーを描画する。"""
    w_mm, h_mm = page.dimensions_mm
    w_pt = w_mm * mm
    h_pt = h_mm * mm
    outer_pt = page.outer_margin_mm * mm
    inner_pt = page.inner_margin_mm * mm
    sum_pt = outer_pt + inner_pt

    # 基本線画スタイル設定
    c.setLineWidth(LINE_WIDTH_PT)
    c.setStrokeColorRGB(0, 0, 0)
    c.setLineCap(0)   # Butt Cap
    c.setLineJoin(0)  # Miter Join

    # ① 外枠 (Outer Frame)
    outer_x = outer_pt
    outer_y = outer_pt
    outer_w = w_pt - 2 * outer_pt
    outer_h = h_pt - 2 * outer_pt
    c.rect(outer_x, outer_y, outer_w, outer_h, stroke=1, fill=0)

    # ② 内枠 (Inner Frame)
    inner_x = sum_pt
    inner_y = sum_pt
    inner_w = w_pt - 2 * sum_pt
    inner_h = h_pt - 2 * sum_pt
    c.rect(inner_x, inner_y, inner_w, inner_h, stroke=1, fill=0)

    # ③ レイアウト計算
    layout = layout_label_and_scale(page, page_index, total_pages)

    if layout.font_size is not None and layout.lines:
        c.setFont("Courier", layout.font_size)
        pad_pt = INNER_PADDING_MM * mm
        text_x = inner_x + pad_pt
        ascent = layout.font_size * 0.8
        line_height = layout.font_size * 1.25

        current_y = inner_y + inner_h - pad_pt - ascent

        for line in layout.lines:
            c.drawString(text_x, current_y, line)
            current_y -= line_height

        # ④ スケールバー描画
        if layout.bar_mm is not None:
            bar_y = current_y - (layout.font_size * 0.25) - (4.0 * mm)
            draw_scale_bar(c, text_x, bar_y, layout.bar_mm)

    return layout


def apply_viewer_preferences(c: canvas.Canvas) -> None:
    """PDF CatalogにPrintScaling=Noneを設定し、自動拡縮を抑止する。"""
    try:
        c._doc.Catalog.ViewerPreferences = pdfdoc.PDFDictionary({
            "PrintScaling": pdfdoc.PDFName("None")
        })
    except Exception:
        # 万一内部構造が変わった場合のフェイルセーフ
        pass


def generate_pdf(
    pages: Sequence[PageConfig],
    output_path: str,
) -> GenerationSummary:
    """指定されたページ構成に基づきPDFファイルを生成する。"""
    if not pages:
        raise ValueError("生成するページが指定されていません。")

    c = canvas.Canvas(output_path, pageCompression=0)

    # PDFドキュメントメタデータ設定
    c.setTitle("Print Scale Calibration Sheet")
    c.setAuthor("PDFMaker")
    c.setCreator("PDFMaker")
    c.setSubject("Printer scale and margin calibration")

    # 印刷時自動拡縮の抑止設定
    apply_viewer_preferences(c)

    total_pages = len(pages)
    warnings: list[tuple[int, str]] = []

    for idx, page in enumerate(pages, start=1):
        w_mm, h_mm = page.dimensions_mm
        # ページごとに厳密な用紙寸法を設定
        c.setPageSize((w_mm * mm, h_mm * mm))

        layout = draw_page(c, page, idx, total_pages)
        if layout.warning:
            warnings.append((idx, layout.warning))

        c.showPage()

    c.save()

    return GenerationSummary(
        total_pages=total_pages,
        output_path=output_path,
        page_warnings=warnings,
    )
