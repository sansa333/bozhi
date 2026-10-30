"""Find LibreOffice in PATH, an explicit setting or standard Windows locations."""
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from .config import Settings


def find_libreoffice(settings: Settings) -> str | None:
    if settings.libreoffice_path:
        path = Path(settings.libreoffice_path)
        if not path.is_file():
            raise ValueError("LIBREOFFICE_PATH_NOT_FOUND")
        return str(path)
    binary = shutil.which("soffice") or shutil.which("libreoffice")
    if binary:
        return binary
    if os.name == "nt":
        for root in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
            if root:
                path = Path(root) / "LibreOffice/program/soffice.exe"
                if path.is_file():
                    return str(path)
    return None


def convert_office(data: bytes, suffix: str, target: str, settings: Settings) -> bytes:
    binary = find_libreoffice(settings)
    if not binary and os.name != "nt":
        raise ValueError("DOC_REQUIRES_LIBREOFFICE")
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        source = work / ("input" + suffix)
        source.write_bytes(data)
        flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        if binary:
            command = [binary, f"-env:UserInstallation={(work / 'profile').as_uri()}", "--headless",
                       "--convert-to", target, "--outdir", tmp, str(source)]
        else:
            import sys
            command = [sys.executable, "-m", "shipping_agent.word_worker", str(source), target]
        subprocess.run(command,
                       check=True, timeout=60, capture_output=True, **flags)
        return source.with_suffix("." + target.split(":")[0]).read_bytes()


def has_word() -> bool:
    if os.name != "nt":
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"Word.Application\CLSID"):
            return True
    except OSError:
        return False
