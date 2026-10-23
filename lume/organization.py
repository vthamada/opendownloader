"""Filename categories shared by destination suggestions and presentation."""
from pathlib import Path
from urllib.parse import urlsplit

FILE_CATEGORIES = {
    "Vídeo": {".mp4", ".webm", ".mkv", ".mov", ".avi", ".m4v", ".ts", ".m3u8", ".mpd"},
    "Áudio": {".mp3", ".m4a", ".aac", ".ogg", ".opus", ".wav", ".flac"},
    "Documentos": {".pdf", ".txt", ".doc", ".docx", ".xls", ".xlsx", ".csv", ".ppt", ".pptx", ".odt"},
    "Compactados": {".zip", ".7z", ".rar", ".gz", ".bz2", ".xz", ".tar"},
    "Programas": {".exe", ".msi", ".msix", ".apk", ".dmg", ".deb", ".rpm"},
    "Imagens": {".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg", ".avif", ".bmp"},
}


def filename_category(name: str) -> str:
    extension = Path(name).suffix.casefold()
    return next((category for category, suffixes in FILE_CATEGORIES.items() if extension in suffixes), "Outros")


def task_category(task: dict) -> str:
    name = task.get("filename")
    if not name:
        try:
            name = urlsplit(task.get("url", "")).path
        except ValueError:
            name = ""
    return filename_category(name)
