"""app.py - 複合機 印刷拡縮検証用PDF生成ツール GUIメインアプリケーション.

Windows 11 ネイティブ調のGUI (Tkinter/ttk)、高DPI対応、リアルタイムプレビュー、
各種キーボードショートカット、およびPyInstaller単一exe化に対応しています。
"""

from __future__ import annotations

import datetime
import os
from pathlib import Path
import subprocess
import sys
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import traceback

from pdfcore import (
    AVAILABLE_PAPER_SIZES,
    DEFAULT_OUTER_MARGIN_MM,
    DEFAULT_INNER_MARGIN_MM,
    PageConfig,
    generate_pdf,
    parse_margin_input,
    validate_page_margins,
    layout_label_and_scale,
)
from config_mgr import (
    load_app_settings,
    save_app_settings,
    export_pages_to_json,
    import_pages_from_json,
)


# ==============================================================================
# Windows 11 高DPI対応初期化
# ==============================================================================
def enable_high_dpi_awareness():
    """Windowsの高DPI環境において文字や線のぼやけを防止する。"""
    try:
        import ctypes
        # Per-Monitor v2 Awareness (Windows 10 Creators Update以降 / Windows 11)
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass


# ==============================================================================
# 生成完了カスタムダイアログ
# ==============================================================================
class CompletionDialog(tk.Toplevel):
    """PDF生成完了時に「PDFを開く」「フォルダを開く」「閉じる」を提供するダイアログ。"""

    def __init__(self, parent, output_path: str, warnings: list[tuple[int, str]]):
        super().__init__(parent)
        self.output_path = output_path
        self.title("PDF生成完了")
        self.geometry("540x360")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        # ウィンドウ中央配置
        self.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width() - 540) // 2
        y = parent.winfo_y() + (parent.winfo_height() - 360) // 2
        self.geometry(f"+{max(0, x)}+{max(0, y)}")

        self._build_ui(warnings)

    def _build_ui(self, warnings: list[tuple[int, str]]):
        frame = ttk.Frame(self, padding=16)
        frame.pack(fill=tk.BOTH, expand=True)

        # アイコンとメッセージ
        top_frame = ttk.Frame(frame)
        top_frame.pack(fill=tk.X, pady=(0, 10))

        ttk.Label(
            top_frame,
            text="✓ PDFの生成が正常に完了しました！",
            font=("Segoe UI", 12, "bold"),
            foreground="#007a00",
        ).pack(anchor=tk.W)

        # パス表示
        path_box = ttk.LabelFrame(frame, text="保存先ファイル", padding=8)
        path_box.pack(fill=tk.X, pady=(0, 10))

        path_entry = ttk.Entry(path_box)
        path_entry.insert(0, self.output_path)
        path_entry.configure(state="readonly")
        path_entry.pack(fill=tk.X)

        # 印刷注意事項
        notice_box = ttk.LabelFrame(frame, text="重要：印刷時の注意点", padding=8)
        notice_box.pack(fill=tk.X, pady=(0, 10))
        notice_text = (
            "・プリンターの印刷ダイアログで「実際のサイズ (100%)」を選択してください。\n"
            "・「用紙に合わせる」「ページ縮小」等の自動拡縮設定を必ず無効化してください。"
        )
        ttk.Label(notice_box, text=notice_text, foreground="#444444", justify=tk.LEFT).pack(anchor=tk.W)

        # 警告一覧 (もしあれば)
        if warnings:
            warn_box = ttk.LabelFrame(frame, text="レイアウト警告 (ラベル省略等)", padding=8)
            warn_box.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
            warn_text = "\n".join([f"P.{p}: {w}" for p, w in warnings])
            ttk.Label(warn_box, text=warn_text, foreground="#b05000", justify=tk.LEFT).pack(anchor=tk.W)

        # ボタングループ
        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill=tk.X, pady=(10, 0))

        ttk.Button(btn_frame, text="📄 PDFを開く", command=self._on_open_pdf, width=15).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(btn_frame, text="📁 フォルダを開く", command=self._on_open_folder, width=15).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(btn_frame, text="閉じる", command=self.destroy, width=12).pack(side=tk.RIGHT)

    def _on_open_pdf(self):
        try:
            os.startfile(self.output_path)
        except Exception as e:
            messagebox.showerror("エラー", f"PDFを開くことができませんでした。\n{e}", parent=self)

    def _on_open_folder(self):
        try:
            norm_path = os.path.normpath(self.output_path)
            subprocess.run(["explorer", f"/select,{norm_path}"])
        except Exception as e:
            messagebox.showerror("エラー", f"フォルダを開くことができませんでした。\n{e}", parent=self)


# ==============================================================================
# メインアプリケーションクラス
# ==============================================================================
class PDFMakerApp(tk.Tk):
    """PDFMaker メインGUIウィンドウ。"""

    def __init__(self):
        super().__init__()
        self.title("複合機 印刷拡縮検証用PDF生成ツール")
        self.geometry("1020x760")
        self.minsize(880, 640)

        # 設定のロード
        self.settings = load_app_settings()

        # データモデル
        self.pages: list[PageConfig] = []

        # アイコン設定
        self._setup_icon()

        # スタイル設定
        self._setup_style()

        # 未捕捉例外のトラップ
        self.report_callback_exception = self._on_unhandled_exception

        # UI構築
        self._build_menu()
        self._build_ui()
        self._bind_shortcuts()

        # 初期プリセット (A4縦 1枚)
        initial_outer = float(self.settings.get("default_outer_margin", DEFAULT_OUTER_MARGIN_MM))
        initial_inner = float(self.settings.get("default_inner_margin", DEFAULT_INNER_MARGIN_MM))
        self.pages.append(PageConfig("A4", False, initial_outer, initial_inner))
        self._refresh_page_list()
        self._select_page_index(0)

    def _setup_icon(self):
        """アプリアイコンを設定する。"""
        # PyInstaller実行時のパス考慮
        base_dir = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
        icon_path = base_dir / "app.ico"
        if icon_path.exists():
            try:
                self.iconbitmap(str(icon_path))
            except Exception:
                pass

    def _setup_style(self):
        """ttkスタイルをWindows 11調に整える。"""
        self.style = ttk.Style(self)
        available_themes = self.style.theme_names()
        if "vista" in available_themes:
            self.style.theme_use("vista")
        elif "clam" in available_themes:
            self.style.theme_use("clam")

        default_font = ("Segoe UI", 9)
        self.option_add("*Font", default_font)

        # ボタン等のカスタムスタイル
        self.style.configure("Accent.TButton", font=("Segoe UI", 10, "bold"))
        self.style.configure("Treeview", rowheight=24)
        self.style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"))

    def _build_menu(self):
        """メニューバーを構築する。"""
        menubar = tk.Menu(self)

        # ファイルメニュー
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="ページ構成を開く... (Ctrl+O)", command=self._on_import_json)
        file_menu.add_command(label="ページ構成を保存...", command=self._on_export_json)
        file_menu.add_separator()
        file_menu.add_command(label="PDFを生成して保存... (Ctrl+S)", command=self._on_generate_pdf)
        file_menu.add_separator()
        file_menu.add_command(label="終了", command=self.destroy)
        menubar.add_cascade(label="ファイル (F)", menu=file_menu)

        # プリセットメニュー
        preset_menu = tk.Menu(menubar, tearoff=0)
        preset_menu.add_command(label="全サイズ縦横一括追加 (10ページ)", command=lambda: self._add_preset("both"))
        preset_menu.add_command(label="全サイズ縦のみ一括追加 (5ページ)", command=lambda: self._add_preset("portrait"))
        preset_menu.add_command(label="全サイズ横のみ一括追加 (5ページ)", command=lambda: self._add_preset("landscape"))
        menubar.add_cascade(label="プリセット (P)", menu=preset_menu)

        # ヘルプメニュー
        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="印刷時の注意点", command=self._show_print_guide)
        help_menu.add_command(label="バージョン情報 (About)", command=self._show_about)
        menubar.add_cascade(label="ヘルプ (H)", menu=help_menu)

        self.config(menu=menubar)

    def _build_ui(self):
        """UI全体を構築する。"""
        main_frame = ttk.Frame(self, padding=12)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # 1. ページ編集パネル (上部)
        self._build_edit_panel(main_frame)

        # 2. 中央ペイン (ページ一覧 + プレビュー)
        center_paned = ttk.PanedWindow(main_frame, orient=tk.HORIZONTAL)
        center_paned.pack(fill=tk.BOTH, expand=True, pady=8)

        # 2-A. 左側: ページ一覧
        list_container = ttk.Frame(center_paned)
        self._build_list_panel(list_container)
        center_paned.add(list_container, weight=3)

        # 2-B. 右側: プレビューCanvas
        preview_container = ttk.Frame(center_paned)
        self._build_preview_panel(preview_container)
        center_paned.add(preview_container, weight=1)

        # 3. 下部ペイン (一括マージン変更 ＆ PDF生成ボタン)
        self._build_bottom_panel(main_frame)

        # 4. 最下部ステータスバー
        self._build_status_bar(main_frame)

    def _build_edit_panel(self, parent):
        """上部のページ追加・編集・プリセットパネル。"""
        box = ttk.LabelFrame(parent, text=" ページ設定・追加 ", padding=10)
        box.pack(fill=tk.X, pady=(0, 6))

        # 1行目: 入力項目
        row1 = ttk.Frame(box)
        row1.pack(fill=tk.X, pady=(0, 8))

        # 用紙サイズ
        ttk.Label(row1, text="用紙サイズ:").pack(side=tk.LEFT, padx=(0, 4))
        self.var_size = tk.StringVar(value=self.settings.get("last_paper_size", "A4"))
        self.combo_size = ttk.Combobox(
            row1, textvariable=self.var_size, values=AVAILABLE_PAPER_SIZES, state="readonly", width=10
        )
        self.combo_size.pack(side=tk.LEFT, padx=(0, 16))
        self.combo_size.bind("<<ComboboxSelected>>", lambda e: self._update_preview())

        # 用紙の向き
        ttk.Label(row1, text="向き:").pack(side=tk.LEFT, padx=(0, 4))
        self.var_landscape = tk.BooleanVar(value=bool(self.settings.get("last_landscape", False)))
        rb_p = ttk.Radiobutton(
            row1, text="縦 (Portrait)", variable=self.var_landscape, value=False, command=self._update_preview
        )
        rb_p.pack(side=tk.LEFT, padx=(0, 6))
        rb_l = ttk.Radiobutton(
            row1, text="横 (Landscape)", variable=self.var_landscape, value=True, command=self._update_preview
        )
        rb_l.pack(side=tk.LEFT, padx=(0, 20))

        # 外枠マージン
        ttk.Label(row1, text="外枠余白:").pack(side=tk.LEFT, padx=(0, 4))
        self.var_outer = tk.StringVar(value=str(self.settings.get("default_outer_margin", DEFAULT_OUTER_MARGIN_MM)))
        self.entry_outer = ttk.Entry(row1, textvariable=self.var_outer, width=7)
        self.entry_outer.pack(side=tk.LEFT)
        ttk.Label(row1, text="mm").pack(side=tk.LEFT, padx=(2, 16))
        self.var_outer.trace_add("write", lambda *args: self._update_preview())

        # 内枠マージン
        ttk.Label(row1, text="内枠余白:").pack(side=tk.LEFT, padx=(0, 4))
        self.var_inner = tk.StringVar(value=str(self.settings.get("default_inner_margin", DEFAULT_INNER_MARGIN_MM)))
        self.entry_inner = ttk.Entry(row1, textvariable=self.var_inner, width=7)
        self.entry_inner.pack(side=tk.LEFT)
        ttk.Label(row1, text="mm").pack(side=tk.LEFT, padx=(2, 10))
        self.var_inner.trace_add("write", lambda *args: self._update_preview())

        # 2行目: 操作ボタン群 & プリセットボタン群
        row2 = ttk.Frame(box)
        row2.pack(fill=tk.X)

        ttk.Button(row2, text="＋ 新規ページ追加", command=self._on_add_page, width=16).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(row2, text="💾 選択行を更新", command=self._on_update_page, width=14).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(row2, text="📄 複製 (Ctrl+D)", command=self._on_duplicate_page, width=14).pack(side=tk.LEFT, padx=(0, 20))

        ttk.Separator(row2, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=4)

        ttk.Label(row2, text="プリセット:", foreground="#555555").pack(side=tk.LEFT, padx=(8, 4))
        ttk.Button(row2, text="全サイズ縦横 (10p)", command=lambda: self._add_preset("both")).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(row2, text="全縦 (5p)", command=lambda: self._add_preset("portrait")).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(row2, text="全横 (5p)", command=lambda: self._add_preset("landscape")).pack(side=tk.LEFT)

    def _build_list_panel(self, parent):
        """中央左側のページ構成一覧パネル。"""
        box = ttk.LabelFrame(parent, text=" 構成ページ一覧 ", padding=8)
        box.pack(fill=tk.BOTH, expand=True)

        # Treeview テーブル
        columns = ("no", "size", "orient", "outer", "inner", "dim", "status")
        self.tree = ttk.Treeview(box, columns=columns, show="headings", selectmode="extended")

        self.tree.heading("no", text="No.")
        self.tree.heading("size", text="用紙サイズ")
        self.tree.heading("orient", text="向き")
        self.tree.heading("outer", text="外枠余白")
        self.tree.heading("inner", text="内枠余白")
        self.tree.heading("dim", text="実寸法 (幅 x 高 mm)")
        self.tree.heading("status", text="レイアウト状態")

        self.tree.column("no", width=40, anchor=tk.CENTER)
        self.tree.column("size", width=80, anchor=tk.CENTER)
        self.tree.column("orient", width=90, anchor=tk.CENTER)
        self.tree.column("outer", width=80, anchor=tk.E)
        self.tree.column("inner", width=80, anchor=tk.E)
        self.tree.column("dim", width=140, anchor=tk.CENTER)
        self.tree.column("status", width=140, anchor=tk.W)

        # スクロールバー
        scrollbar = ttk.Scrollbar(box, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)

        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.tree.bind("<Double-1>", lambda e: self._load_selected_to_form())

        # 一覧の下部操作ボタンバー
        btn_bar = ttk.Frame(box)
        btn_bar.pack(side=tk.BOTTOM, fill=tk.X, pady=(6, 0))

        ttk.Button(btn_bar, text="▲ 上へ移動", command=lambda: self._move_page(-1), width=10).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(btn_bar, text="▼ 下へ移動", command=lambda: self._move_page(1), width=10).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Button(btn_bar, text="✕ 選択削除 (Del)", command=self._delete_selected_pages, width=15).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(btn_bar, text="🗑️ 全消去", command=self._clear_all_pages, width=10).pack(side=tk.LEFT, padx=(0, 16))

        ttk.Button(btn_bar, text="構成保存...", command=self._on_export_json, width=11).pack(side=tk.RIGHT)
        ttk.Button(btn_bar, text="構成読込...", command=self._on_import_json, width=11).pack(side=tk.RIGHT, padx=(0, 4))

    def _build_preview_panel(self, parent):
        """中央右側のリアルタイムプレビューパネル。"""
        box = ttk.LabelFrame(parent, text=" プレビュー ", padding=8)
        box.pack(fill=tk.BOTH, expand=True)

        self.preview_canvas = tk.Canvas(box, bg="#f5f5f5", highlightthickness=1, highlightbackground="#d0d0d0")
        self.preview_canvas.pack(fill=tk.BOTH, expand=True)

        self.lbl_preview_info = ttk.Label(box, text="選択ページの寸法情報", anchor=tk.CENTER)
        self.lbl_preview_info.pack(fill=tk.X, pady=(4, 0))

        self.preview_canvas.bind("<Configure>", lambda e: self._draw_canvas_preview())

    def _build_bottom_panel(self, parent):
        """下部の一括操作および生成ボタンパネル。"""
        container = ttk.Frame(parent)
        container.pack(fill=tk.X, pady=(6, 0))

        # 一括適用パネル
        batch_box = ttk.LabelFrame(container, text=" 全ページ余白の一括上書き ", padding=8)
        batch_box.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10))

        batch_row = ttk.Frame(batch_box)
        batch_row.pack(fill=tk.X)

        ttk.Label(batch_row, text="外枠:").pack(side=tk.LEFT, padx=(0, 4))
        self.var_batch_outer = tk.StringVar()
        ttk.Entry(batch_row, textvariable=self.var_batch_outer, width=6).pack(side=tk.LEFT)
        ttk.Label(batch_row, text="mm").pack(side=tk.LEFT, padx=(2, 12))

        ttk.Label(batch_row, text="内枠:").pack(side=tk.LEFT, padx=(0, 4))
        self.var_batch_inner = tk.StringVar()
        ttk.Entry(batch_row, textvariable=self.var_batch_inner, width=6).pack(side=tk.LEFT)
        ttk.Label(batch_row, text="mm").pack(side=tk.LEFT, padx=(2, 12))

        ttk.Button(batch_row, text="全ページに一括適用", command=self._on_apply_batch_margins).pack(side=tk.LEFT, padx=(4, 8))
        ttk.Label(batch_row, text="※空欄の項目は変更されません", foreground="#777777").pack(side=tk.LEFT)

        # PDF生成ボタン
        self.btn_generate = tk.Button(
            container,
            text="📄 PDFを生成して保存... (Ctrl+S)",
            command=self._on_generate_pdf,
            font=("Segoe UI", 11, "bold"),
            bg="#0066cc",
            fg="white",
            activebackground="#0052a3",
            activeforeground="white",
            relief=tk.RAISED,
            padx=16,
            pady=8,
            cursor="hand2",
        )
        self.btn_generate.pack(side=tk.RIGHT, fill=tk.Y)

    def _build_status_bar(self, parent):
        """最下部のステータスバー。"""
        status_bar = ttk.Frame(parent)
        status_bar.pack(fill=tk.X, pady=(6, 0))

        self.lbl_status = ttk.Label(status_bar, text="準備完了", foreground="#444444")
        self.lbl_status.pack(side=tk.LEFT)

        guide_text = "ショートカット: [Ctrl+S] PDF生成  [Ctrl+D] 複製  [Del] 削除  [Ctrl+↑/↓] 移動"
        ttk.Label(status_bar, text=guide_text, foreground="#888888").pack(side=tk.RIGHT)

    def _bind_shortcuts(self):
        """各種ショートカットキーをバインドする。"""
        self.bind("<Control-s>", lambda e: self._on_generate_pdf())
        self.bind("<Control-S>", lambda e: self._on_generate_pdf())
        self.bind("<Control-o>", lambda e: self._on_import_json())
        self.bind("<Control-O>", lambda e: self._on_import_json())
        self.bind("<Control-d>", lambda e: self._on_duplicate_page())
        self.bind("<Control-D>", lambda e: self._on_duplicate_page())
        self.bind("<Delete>", lambda e: self._delete_selected_pages())
        self.bind("<Control-Up>", lambda e: self._move_page(-1))
        self.bind("<Control-Down>", lambda e: self._move_page(1))

    # ==========================================================================
    # データ操作・イベントハンドラ
    # ==========================================================================
    def _get_form_page_config(self) -> PageConfig:
        """入力フォームからPageConfigを生成して検証する。"""
        size = self.var_size.get()
        landscape = self.var_landscape.get()
        outer = float(parse_margin_input(self.var_outer.get(), "外枠余白"))
        inner = float(parse_margin_input(self.var_inner.get(), "内枠余白"))

        validate_page_margins(size, landscape, outer, inner)
        return PageConfig(size, landscape, outer, inner)

    def _on_add_page(self):
        """入力フォームの内容を新規ページとして追加する。"""
        try:
            cfg = self._get_form_page_config()
            self.pages.append(cfg)
            self._refresh_page_list()
            self._select_page_index(len(self.pages) - 1)
            self._save_current_settings()
        except ValueError as ex:
            messagebox.showerror("入力エラー", str(ex), parent=self)

    def _on_update_page(self):
        """選択中のページを入力フォームの値で更新する。"""
        selected_indices = self._get_selected_indices()
        if not selected_indices:
            messagebox.showwarning("選択なし", "更新対象のページを一覧から選択してください。", parent=self)
            return

        try:
            cfg = self._get_form_page_config()
            for idx in selected_indices:
                self.pages[idx] = PageConfig(cfg.size, cfg.landscape, cfg.outer_margin_mm, cfg.inner_margin_mm)
            self._refresh_page_list()
            self._select_page_index(selected_indices[0])
            self._save_current_settings()
        except ValueError as ex:
            messagebox.showerror("入力エラー", str(ex), parent=self)

    def _on_duplicate_page(self):
        """選択中のページを複製して末尾または直下に追加する。"""
        selected_indices = self._get_selected_indices()
        if not selected_indices:
            return

        new_items = []
        for idx in selected_indices:
            p = self.pages[idx]
            new_items.append(PageConfig(p.size, p.landscape, p.outer_margin_mm, p.inner_margin_mm))

        insert_pos = max(selected_indices) + 1
        for item in reversed(new_items):
            self.pages.insert(insert_pos, item)

        self._refresh_page_list()
        self._select_page_index(insert_pos)

    def _add_preset(self, mode: str):
        """プリセットページを一括追加する。"""
        try:
            outer = float(parse_margin_input(self.var_outer.get(), "外枠余白"))
            inner = float(parse_margin_input(self.var_inner.get(), "内枠余白"))
        except ValueError as ex:
            messagebox.showerror("入力エラー", f"プリセット追加前のマージン値が不正です:\n{ex}", parent=self)
            return

        new_pages = []
        sizes = AVAILABLE_PAPER_SIZES

        if mode == "both":
            for s in sizes:
                new_pages.append(PageConfig(s, False, outer, inner))
                new_pages.append(PageConfig(s, True, outer, inner))
        elif mode == "portrait":
            for s in sizes:
                new_pages.append(PageConfig(s, False, outer, inner))
        elif mode == "landscape":
            for s in sizes:
                new_pages.append(PageConfig(s, True, outer, inner))

        # バリデーション
        for p in new_pages:
            try:
                validate_page_margins(p.size, p.landscape, p.outer_margin_mm, p.inner_margin_mm)
            except ValueError as ex:
                messagebox.showerror("プリセットエラー", str(ex), parent=self)
                return

        self.pages.extend(new_pages)
        self._refresh_page_list()
        self._select_page_index(len(self.pages) - 1)

    def _move_page(self, delta: int):
        """選択行を上下に移動する。"""
        indices = self._get_selected_indices()
        if not indices:
            return

        if delta < 0 and min(indices) > 0:
            for idx in sorted(indices):
                self.pages[idx], self.pages[idx - 1] = self.pages[idx - 1], self.pages[idx]
            self._refresh_page_list()
            self._select_indices([i - 1 for i in indices])
        elif delta > 0 and max(indices) < len(self.pages) - 1:
            for idx in sorted(indices, reverse=True):
                self.pages[idx], self.pages[idx + 1] = self.pages[idx + 1], self.pages[idx]
            self._refresh_page_list()
            self._select_indices([i + 1 for i in indices])

    def _delete_selected_pages(self):
        """選択行を削除する。"""
        indices = self._get_selected_indices()
        if not indices:
            return

        for idx in sorted(indices, reverse=True):
            del self.pages[idx]

        self._refresh_page_list()
        new_sel = min(indices[0], len(self.pages) - 1)
        if new_sel >= 0:
            self._select_page_index(new_sel)

    def _clear_all_pages(self):
        """全ページを消去する。"""
        if not self.pages:
            return
        if messagebox.askyesno("全消去確認", "すべてのページを消去しますか？", parent=self):
            self.pages.clear()
            self._refresh_page_list()
            self._draw_canvas_preview()

    def _on_apply_batch_margins(self):
        """全ページのマージンを一括上書きする。"""
        if not self.pages:
            messagebox.showinfo("情報", "設定済みページがありません。", parent=self)
            return

        outer_text = self.var_batch_outer.get().strip()
        inner_text = self.var_batch_inner.get().strip()

        if not outer_text and not inner_text:
            messagebox.showwarning("入力なし", "外枠または内枠のマージン値を入力してください。", parent=self)
            return

        new_outer: float | None = None
        new_inner: float | None = None

        try:
            if outer_text:
                new_outer = float(parse_margin_input(outer_text, "外枠余白"))
            if inner_text:
                new_inner = float(parse_margin_input(inner_text, "内枠余白"))
        except ValueError as ex:
            messagebox.showerror("入力エラー", str(ex), parent=self)
            return

        # 事前検証 (1ページでも不正なら全体拒否)
        errors = []
        for idx, p in enumerate(self.pages, start=1):
            chk_outer = new_outer if new_outer is not None else p.outer_margin_mm
            chk_inner = new_inner if new_inner is not None else p.inner_margin_mm
            try:
                validate_page_margins(p.size, p.landscape, chk_outer, chk_inner)
            except ValueError as ex:
                errors.append(f"P.{idx} ({p.size}): {ex}")

        if errors:
            msg = "一括適用により一部ページで制約違反が発生するため中止しました:\n\n" + "\n".join(errors[:5])
            if len(errors) > 5:
                msg += f"\n...他 {len(errors)-5} 件"
            messagebox.showerror("適用エラー", msg, parent=self)
            return

        # 確認ダイアログ
        desc_parts = []
        if new_outer is not None:
            desc_parts.append(f"外枠: {new_outer:.1f} mm")
        if new_inner is not None:
            desc_parts.append(f"内枠: {new_inner:.1f} mm")
        desc = " / ".join(desc_parts)

        if not messagebox.askyesno(
            "一括適用確認",
            f"全 {len(self.pages)} ページのマージンを [{desc}] に上書きします。\nよろしいですか？",
            parent=self,
        ):
            return

        # 適用
        for p in self.pages:
            if new_outer is not None:
                p.outer_margin_mm = new_outer
            if new_inner is not None:
                p.inner_margin_mm = new_inner

        self._refresh_page_list()
        messagebox.showinfo("完了", f"全 {len(self.pages)} ページのマージンを更新しました。", parent=self)

    # ==========================================================================
    # PDF生成処理
    # ==========================================================================
    def _on_generate_pdf(self):
        """PDF生成・保存を実行する。"""
        if not self.pages:
            messagebox.showwarning("警告", "生成するページがありません。ページを追加してください。", parent=self)
            return

        # 大量ページ警告 (50ページ以上)
        if len(self.pages) >= 50:
            if not messagebox.askyesno(
                "確認",
                f"ページ数が {len(self.pages)} ページあります。\n生成に少し時間がかかる場合がありますが続行しますか？",
                parent=self,
            ):
                return

        # 既定ファイル名の生成 (scale_test_YYYYMMDD_HHMM.pdf)
        now_str = datetime.datetime.now().strftime("%Y%m%d_%H%M")
        default_filename = f"scale_test_{now_str}.pdf"

        last_dir = self.settings.get("last_save_dir", "")
        if not last_dir or not os.path.exists(last_dir):
            last_dir = str(Path.home() / "Desktop")
            if not os.path.exists(last_dir):
                last_dir = str(Path.home())

        file_path = filedialog.asksaveasfilename(
            parent=self,
            title="PDFファイルの保存先を指定",
            initialdir=last_dir,
            initialfile=default_filename,
            filetypes=[("PDF Documents (*.pdf)", "*.pdf"), ("All Files (*.*)", "*.*")],
            defaultextension=".pdf",
        )

        if not file_path:
            return

        # 生成実行
        try:
            self.lbl_status.config(text="PDFを生成中...")
            self.update_idletasks()

            summary = generate_pdf(self.pages, file_path)

            # 設定保存
            self.settings["last_save_dir"] = str(Path(file_path).parent)
            self._save_current_settings()

            self.lbl_status.config(text=f"生成完了: {summary.total_pages} ページ")

            # 完了ダイアログの表示
            CompletionDialog(self, summary.output_path, summary.page_warnings)

        except PermissionError:
            messagebox.showerror(
                "ファイルアクセスエラー",
                f"ファイルへの書き込みに失敗しました。\n"
                f"指定のPDFファイルが他のアプリケーション（Acrobat Readerなど）で開かれていないか確認し、"
                f"閉じてから再度お試しください。\n\n対象: {file_path}",
                parent=self,
            )
            self.lbl_status.config(text="エラー: 書き込み失敗")
        except Exception as ex:
            messagebox.showerror(
                "生成エラー",
                f"PDF生成中に予期せぬエラーが発生しました:\n{ex}",
                parent=self,
            )
            self.lbl_status.config(text="エラー発生")

    # ==========================================================================
    # JSON設定のインポート / エクスポート
    # ==========================================================================
    def _on_export_json(self):
        """ページ構成をJSONファイルに保存する。"""
        if not self.pages:
            messagebox.showwarning("警告", "保存するページがありません。", parent=self)
            return

        filepath = filedialog.asksaveasfilename(
            parent=self,
            title="ページ構成の保存",
            filetypes=[("JSON Files (*.json)", "*.json")],
            defaultextension=".json",
            initialfile="pdf_pages_config.json",
        )
        if not filepath:
            return

        try:
            export_pages_to_json(self.pages, filepath)
            messagebox.showinfo("保存完了", f"構成ファイルを保存しました:\n{filepath}", parent=self)
        except Exception as ex:
            messagebox.showerror("保存エラー", f"構成の保存に失敗しました:\n{ex}", parent=self)

    def _on_import_json(self):
        """JSONファイルからページ構成を読み込む。"""
        filepath = filedialog.askopenfilename(
            parent=self,
            title="ページ構成の読込",
            filetypes=[("JSON Files (*.json)", "*.json"), ("All Files (*.*)", "*.*")],
        )
        if not filepath:
            return

        try:
            new_pages = import_pages_from_json(filepath)
            # 各ページの検証
            for p in new_pages:
                validate_page_margins(p.size, p.landscape, p.outer_margin_mm, p.inner_margin_mm)

            self.pages = new_pages
            self._refresh_page_list()
            if self.pages:
                self._select_page_index(0)
            messagebox.showinfo("読込完了", f"{len(self.pages)} ページの構成を読み込みました。", parent=self)
        except Exception as ex:
            messagebox.showerror("読込エラー", f"構成の読み込みに失敗しました:\n{ex}", parent=self)

    # ==========================================================================
    # UIヘルパー
    # ==========================================================================
    def _refresh_page_list(self):
        """Treeviewの一覧表示をデータモデルと同期する。"""
        self.tree.delete(*self.tree.get_children())
        total = len(self.pages)

        for idx, page in enumerate(self.pages, start=1):
            w_mm, h_mm = page.dimensions_mm
            layout = layout_label_and_scale(page, idx, total)
            status_text = layout.warning if layout.warning else "正常"

            self.tree.insert(
                "",
                tk.END,
                iid=str(idx - 1),
                values=(
                    idx,
                    page.size,
                    page.orientation_label,
                    f"{page.outer_margin_mm:.1f} mm",
                    f"{page.inner_margin_mm:.1f} mm",
                    f"{w_mm:.1f} x {h_mm:.1f}",
                    status_text,
                ),
            )

        self.lbl_status.config(text=f"全 {total} ページ 設定済み")
        self._update_preview()

    def _get_selected_indices(self) -> list[int]:
        """Treeviewで選択されている行インデックスのリストを返す。"""
        selected = self.tree.selection()
        return [int(item_id) for item_id in selected if item_id.isdigit()]

    def _select_page_index(self, idx: int):
        """指定した単一インデックスを選択する。"""
        if 0 <= idx < len(self.pages):
            self.tree.selection_set(str(idx))
            self.tree.see(str(idx))
            self._load_selected_to_form()

    def _select_indices(self, indices: list[int]):
        """複数のインデックスを選択する。"""
        valid_iids = [str(i) for i in indices if 0 <= i < len(self.pages)]
        self.tree.selection_set(valid_iids)
        if valid_iids:
            self.tree.see(valid_iids[0])
            self._load_selected_to_form()

    def _on_tree_select(self, event):
        """行選択変更時にプレビューとフォームを更新する。"""
        self._load_selected_to_form()
        self._update_preview()

    def _load_selected_to_form(self):
        """選択行のデータを入力フォームに反映する。"""
        indices = self._get_selected_indices()
        if not indices:
            return
        page = self.pages[indices[0]]
        self.var_size.set(page.size)
        self.var_landscape.set(page.landscape)
        self.var_outer.set(f"{page.outer_margin_mm:.1f}")
        self.var_inner.set(f"{page.inner_margin_mm:.1f}")

    def _save_current_settings(self):
        """現在のフォーム値を初期設定として保存する。"""
        try:
            self.settings["default_outer_margin"] = float(self.var_outer.get())
            self.settings["default_inner_margin"] = float(self.var_inner.get())
            self.settings["last_paper_size"] = self.var_size.get()
            self.settings["last_landscape"] = self.var_landscape.get()
            save_app_settings(self.settings)
        except Exception:
            pass

    # ==========================================================================
    # プレビューCanvas描画
    # ==========================================================================
    def _update_preview(self):
        """フォームまたは選択行に基づきプレビューを再描画する。"""
        self._draw_canvas_preview()

    def _draw_canvas_preview(self):
        """Canvas上に用紙、外枠、内枠、スケールバーの簡易縮小描画を行う。"""
        canvas = self.preview_canvas
        canvas.delete("all")

        # 対象ページの決定 (選択行があればそれ、なければフォームの入力値)
        indices = self._get_selected_indices()
        if indices and indices[0] < len(self.pages):
            page = self.pages[indices[0]]
        else:
            try:
                page = self._get_form_page_config()
            except Exception:
                page = PageConfig("A4", False, DEFAULT_OUTER_MARGIN_MM, DEFAULT_INNER_MARGIN_MM)

        w_mm, h_mm = page.dimensions_mm
        outer_mm = page.outer_margin_mm
        inner_mm = page.inner_margin_mm

        c_w = canvas.winfo_width()
        c_h = canvas.winfo_height()
        if c_w < 50 or c_h < 50:
            return

        # パディング
        pad = 20
        max_draw_w = c_w - 2 * pad
        max_draw_h = c_h - 2 * pad

        scale = min(max_draw_w / w_mm, max_draw_h / h_mm)

        paper_draw_w = w_mm * scale
        paper_draw_h = h_mm * scale

        ox = pad + (max_draw_w - paper_draw_w) / 2
        oy = pad + (max_draw_h - paper_draw_h) / 2

        # 用紙背景 (白)
        canvas.create_rectangle(
            ox, oy, ox + paper_draw_w, oy + paper_draw_h, fill="#ffffff", outline="#aaaaaa", width=1
        )

        # 外枠 (青色細線)
        outer_draw_x = ox + outer_mm * scale
        outer_draw_y = oy + outer_mm * scale
        outer_draw_w = (w_mm - 2 * outer_mm) * scale
        outer_draw_h = (h_mm - 2 * outer_mm) * scale
        if outer_draw_w > 0 and outer_draw_h > 0:
            canvas.create_rectangle(
                outer_draw_x,
                outer_draw_y,
                outer_draw_x + outer_draw_w,
                outer_draw_y + outer_draw_h,
                outline="#0066cc",
                width=1,
            )

        # 内枠 (赤色細線)
        sum_m = outer_mm + inner_mm
        inner_draw_x = ox + sum_m * scale
        inner_draw_y = oy + sum_m * scale
        inner_draw_w = (w_mm - 2 * sum_m) * scale
        inner_draw_h = (h_mm - 2 * sum_m) * scale
        if inner_draw_w > 0 and inner_draw_h > 0:
            canvas.create_rectangle(
                inner_draw_x,
                inner_draw_y,
                inner_draw_x + inner_draw_w,
                inner_draw_y + inner_draw_h,
                outline="#cc0000",
                width=1,
            )

            # ラベル＆スケールバーの簡易表示 (内枠左上)
            label_x = inner_draw_x + 4 * scale
            label_y = inner_draw_y + 4 * scale
            canvas.create_text(
                label_x,
                label_y,
                text=f"{page.size} ({page.orientation_label[:4]})\nOuter: {outer_mm:.1f} / Inner: {inner_mm:.1f}",
                anchor=tk.NW,
                font=("Consolas", max(7, int(9 * min(scale, 1.2)))),
                fill="#333333",
            )

        self.lbl_preview_info.config(
            text=f"{page.size} ({page.orientation_label}) - {w_mm:.1f} x {h_mm:.1f} mm  [外枠:{outer_mm:.1f}mm / 内枠:{inner_mm:.1f}mm]"
        )

    # ==========================================================================
    # ヘルプ / About ダイアログ
    # ==========================================================================
    def _show_print_guide(self):
        """印刷時の注意事項ダイアログを表示する。"""
        msg = (
            "【複合機 印刷拡縮検証時の重要ガイド】\n\n"
            "1. 印刷ダイアログの倍率設定:\n"
            "   Acrobat Reader等の印刷ダイアログで「実際のサイズ (100%)」を必ず選択してください。\n"
            "   「用紙に合わせる」「ページ縮小」などが有効になっていると正確な測定ができません。\n\n"
            "2. 測定手順:\n"
            "   ・外枠: 用紙エッジからの欠け量や左右・上下の余白バランスを確認します。\n"
            "   ・内枠 / スケールバー: 金属定規等を用いて、スケールバーの長さ(100mmまたは50mm/30mm)や\n"
            "     内枠の矩形寸法をミリ単位で実測し、複合機の倍率ズレ(%)を計算します。\n"
        )
        messagebox.showinfo("印刷時の注意点", msg, parent=self)

    def _show_about(self):
        """Aboutダイアログを表示する。"""
        about_text = (
            "複合機 印刷拡縮検証用PDF生成ツール v1.0.0\n\n"
            "Windows 11 オフラインスタンドアロン環境対応\n"
            "JIS規格B列寸法・高精度ベクター描画対応\n\n"
            "【オープンソースライセンス】\n"
            "・ReportLab PDF Library (BSD License)\n"
            "  Copyright (c) 2000-2025, ReportLab Inc.\n"
            "・Python Standard Library (PSF License)\n"
        )
        messagebox.showinfo("バージョン情報", about_text, parent=self)

    def _on_unhandled_exception(self, exc, val, tb):
        """未捕捉例外をトラップしてGUIダイアログで表示する。"""
        err_msg = "".join(traceback.format_exception(exc, val, tb))
        messagebox.showerror(
            "システムエラー (未捕捉例外)",
            f"予期せぬエラーが発生しました:\n\n{val}\n\n詳細:\n{err_msg}",
            parent=self,
        )


# ==============================================================================
# エントリポイント
# ==============================================================================
def main():
    enable_high_dpi_awareness()
    app = PDFMakerApp()
    app.mainloop()


if __name__ == "__main__":
    main()
