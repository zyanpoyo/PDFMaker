"""config_mgr.py - 設定管理およびページ構成JSON入出力モジュール."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Sequence

from pdfcore import PageConfig, DEFAULT_OUTER_MARGIN_MM, DEFAULT_INNER_MARGIN_MM


def _get_config_path() -> Path:
    """ユーザーのAppData/Local配下（またはホームディレクトリ）に設定ファイルパスを返す。"""
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        base_dir = Path(local_app_data) / "PDFMaker"
    else:
        base_dir = Path.home() / ".pdfmaker"

    try:
        base_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        base_dir = Path.home()

    return base_dir / "settings.json"


def load_app_settings() -> dict:
    """前回のアプリケーション設定（マージン既定値、前回保存ディレクトリなど）を読み込む。"""
    config_file = _get_config_path()
    default_settings = {
        "last_save_dir": "",
        "default_outer_margin": DEFAULT_OUTER_MARGIN_MM,
        "default_inner_margin": DEFAULT_INNER_MARGIN_MM,
        "last_paper_size": "A4",
        "last_landscape": False,
    }

    if not config_file.exists():
        return default_settings

    try:
        with open(config_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                default_settings.update(data)
    except Exception:
        # 読み込み失敗時は既定値のまま
        pass

    return default_settings


def save_app_settings(settings: dict) -> None:
    """アプリケーション設定を保存する。"""
    config_file = _get_config_path()
    try:
        with open(config_file, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def export_pages_to_json(pages: Sequence[PageConfig], filepath: str) -> None:
    """ページ構成リストをJSONファイルに出力する。"""
    data = {
        "version": 1,
        "pages": [p.to_dict() for p in pages],
    }
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def import_pages_from_json(filepath: str) -> list[PageConfig]:
    """JSONファイルからページ構成リストを読み込む。"""
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict) or "pages" not in data:
        raise ValueError("無効なページ構成ファイルです。'pages' キーが見つかりません。")

    raw_pages = data["pages"]
    if not isinstance(raw_pages, list):
        raise ValueError("無効な形式です。'pages' は配列である必要があります。")

    pages: list[PageConfig] = []
    for item in raw_pages:
        pages.append(PageConfig.from_dict(item))

    return pages
