"""Explicit download of the official optional tunnel binary, verified against release SHA256."""
import hashlib
import platform
import stat
from pathlib import Path

import requests


def main():
    system, machine = platform.system(), platform.machine().lower()
    if system not in {"Windows", "Linux"} or machine not in {"amd64", "x86_64"}:
        raise SystemExit("请通过 Cloudflare 官方安装说明为当前系统安装 cloudflared")
    name = "cloudflared-windows-amd64.exe" if system == "Windows" else "cloudflared-linux-amd64"
    session = requests.Session()
    response = session.get("https://api.github.com/repos/cloudflare/cloudflared/releases/latest", timeout=30)
    response.raise_for_status()
    release = response.json()
    asset = next(a for a in release["assets"] if a["name"] == name)
    expected = asset.get("digest", "").removeprefix("sha256:")
    if len(expected) != 64:
        raise SystemExit("官方发布未提供 SHA256，未下载运行文件")
    destination = Path("output/tools") / name
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".download")
    try:
        with session.get(asset["browser_download_url"], stream=True, timeout=(15, 120)) as download:
            download.raise_for_status()
            with temporary.open("wb") as file:
                for block in download.iter_content(1024*1024):
                    file.write(block)
        if hashlib.sha256(temporary.read_bytes()).hexdigest() != expected:
            raise SystemExit("SHA256 不匹配，未安装")
        temporary.replace(destination)
        destination.chmod(destination.stat().st_mode | stat.S_IXUSR)
    finally:
        temporary.unlink(missing_ok=True)
    print(f"官方版本 {release['tag_name']}，SHA256 已验证。请在 .env 设置 CLOUDFLARED_PATH={destination.resolve()}")


if __name__ == "__main__":
    main()
