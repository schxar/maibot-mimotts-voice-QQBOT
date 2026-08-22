"""SILK 音频转换与 FFmpeg 管理。

QQ 官方机器人语音消息要求使用腾讯 silk 格式,而 FFmpeg 自身不提供 SILK
编码器。因此本模块使用 graiax-silkcoder(封装自 kn007/silk-v3-decoder
的预编译编码器)将音频编码为 silk,FFmpeg 用于把任意输入音频统一转换为
24kHz 单声道 PCM 后交给 silk 编码器;当系统没有安装 FFmpeg 时,自动从
受信 HTTPS 源下载静态构建到插件的 bin 目录。

下载安全约束:仅允许受信白名单的 HTTPS 地址,预检主机名与解析出的 IP,
拒绝 localhost、环回、私有、链路本地、保留、组播等不可信地址。
"""

from __future__ import annotations

import ipaddress
import shutil
import socket
import stat
import sys
import tarfile
import zipfile
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urljoin, urlsplit

import aiohttp

# 各平台的 FFmpeg 静态构建下载地址(仅受信 HTTPS 源)
_FFMPEG_DOWNLOAD_SOURCES: dict[str, str] = {
    "win32": "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
    "linux": "https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz",
}
_TRUSTED_FFMPEG_HOSTS = {
    "gyan.dev",
    "www.gyan.dev",
    "johnvansickle.com",
    "www.johnvansickle.com",
}
_MAX_FFMPEG_ARCHIVE_BYTES = 512 * 1024 * 1024


def _validate_download_url(url: str) -> None:
    """校验 FFmpeg 下载地址:仅受信 HTTPS 主机,且解析 IP 不为私网地址。"""

    parsed = urlsplit(url)
    if parsed.scheme != "https":
        raise RuntimeError(f"FFmpeg 下载地址必须使用 HTTPS: {url}")
    host = (parsed.hostname or "").rstrip(".").lower()
    if host not in _TRUSTED_FFMPEG_HOSTS:
        raise RuntimeError(f"FFmpeg 下载地址主机不在受信列表: {host}")
    try:
        port = parsed.port
    except ValueError as exc:
        raise RuntimeError("FFmpeg 下载地址端口无效") from exc
    if port not in {None, 443}:
        raise RuntimeError("FFmpeg 下载地址端口无效")
    try:
        addresses = [
            address_info[4][0]
            for address_info in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        ]
    except socket.gaierror as exc:
        raise RuntimeError(f"FFmpeg 下载主机解析失败: {host}") from exc
    for address in addresses:
        parsed_ip = ipaddress.ip_address(address)
        if (
            parsed_ip.is_loopback
            or parsed_ip.is_private
            or parsed_ip.is_link_local
            or parsed_ip.is_reserved
            or parsed_ip.is_multicast
            or parsed_ip.is_unspecified
            or not parsed_ip.is_global
        ):
            raise RuntimeError(f"FFmpeg 下载主机解析到不可信地址: {address}")


async def _download_archive(session: aiohttp.ClientSession, url: str, dest: Path) -> None:
    """下载压缩包到目标文件,逐跳校验重定向地址并限制文件大小。"""

    current_url = url
    for _ in range(6):
        _validate_download_url(current_url)
        async with session.get(current_url, allow_redirects=False) as response:
            if response.status in {301, 302, 303, 307, 308}:
                location = response.headers.get("Location")
                if not location:
                    raise RuntimeError("FFmpeg 下载重定向缺少 Location")
                current_url = urljoin(current_url, location)
                continue
            if response.status != 200:
                raise RuntimeError(f"FFmpeg 下载失败: HTTP {response.status}")
            total_size = 0
            with dest.open("wb") as dest_file:
                async for chunk in response.content.iter_chunked(64 * 1024):
                    total_size += len(chunk)
                    if total_size > _MAX_FFMPEG_ARCHIVE_BYTES:
                        raise RuntimeError("FFmpeg 压缩包超过大小限制")
                    dest_file.write(chunk)
            return
    raise RuntimeError("FFmpeg 下载重定向次数过多")


def _extract_ffmpeg_archive(archive_path: Path, bin_dir: Path) -> None:
    """按平台解压 FFmpeg 压缩包,提取可执行文件与动态库到 bin 目录。"""

    if archive_path.suffix == ".zip":
        with zipfile.ZipFile(archive_path) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                # 只取扁平文件名,避免压缩包路径穿越
                file_name = Path(info.filename).name
                if not file_name:
                    continue
                if (
                    file_name in {"ffmpeg.exe", "ffprobe.exe", "ffplay.exe"}
                    or file_name.lower().endswith(".dll")
                ):
                    target_path = bin_dir / file_name
                    with archive.open(info) as source, target_path.open("wb") as target:
                        shutil.copyfileobj(source, target)
        return

    with tarfile.open(archive_path, "r:xz") as archive:
        for member in archive.getmembers():
            if not member.isfile() or Path(member.name).name != "ffmpeg":
                continue
            target_path = bin_dir / "ffmpeg"
            with archive.extractfile(member) as source, target_path.open("wb") as target:
                shutil.copyfileobj(source, target)
            target_path.chmod(
                stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR
                | stat.S_IRGRP | stat.S_IXGRP
                | stat.S_IROTH | stat.S_IXOTH
            )
            return
    raise RuntimeError("未在压缩包中找到 FFmpeg 可执行文件")


async def _download_and_extract_ffmpeg(url: str, bin_dir: Path) -> None:
    """下载并解压 FFmpeg,完成后删除临时压缩包。"""

    validate_url = url
    _validate_download_url(validate_url)
    bin_dir.mkdir(parents=True, exist_ok=True)
    archive_path = bin_dir / f"ffmpeg-download{'.zip' if sys.platform == 'win32' else '.tar.xz'}"
    timeout = aiohttp.ClientTimeout(total=600)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        await _download_archive(session, validate_url, archive_path)
    try:
        _extract_ffmpeg_archive(archive_path, bin_dir)
    finally:
        archive_path.unlink(missing_ok=True)


async def ensure_ffmpeg_binary(bin_dir: Path, logger: Any) -> Optional[str]:
    """返回可用的 FFmpeg 路径:PATH 优先,其次插件 bin 目录,最后自动下载。

    返回 None 表示当前环境无法取得 FFmpeg(仅影响需要 FFmpeg 的转换路径)。
    """

    in_path = shutil.which("ffmpeg")
    if in_path:
        logger.info("检测到系统 FFmpeg: %s", in_path)
        return in_path

    platform_bin = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
    local_bin = bin_dir / platform_bin
    if local_bin.is_file():
        logger.info("使用插件目录中的 FFmpeg: %s", local_bin)
        return str(local_bin)

    source_url = _FFMPEG_DOWNLOAD_SOURCES.get(sys.platform)
    if source_url is None:
        logger.error("当前系统 %s 不支持自动下载 FFmpeg,请手动安装 FFmpeg 后重试", sys.platform)
        return None

    try:
        await _download_and_extract_ffmpeg(source_url, bin_dir)
    except Exception as exc:
        logger.error(f"FFmpeg 自动下载失败: {exc}", exc_info=True)
        return None

    if local_bin.is_file():
        logger.info("FFmpeg 自动下载完成: %s", local_bin)
        return str(local_bin)
    logger.error("FFmpeg 下载完成但未找到可执行文件: %s", local_bin)
    return None


async def audio_to_silk(audio_bytes: bytes, ffmpeg_path: Optional[str]) -> bytes:
    """将任意音频转换为腾讯 silk 格式。

    Args:
        audio_bytes: 待转换的音频二进制数据。
        ffmpeg_path: 可用的 FFmpeg 路径;为 None 时仅当输入是标准 WAV 才转换。

    Returns:
        silk 格式的二进制数据。

    Raises:
        RuntimeError: 缺少 graiax-silkcoder 依赖或音频格式不受支持。
    """

    try:
        from graiax import silkcoder
    except ImportError as exc:
        raise RuntimeError(
            "缺少 silk 编码依赖 graiax-silkcoder,请在插件环境执行: pip install graiax-silkcoder"
        ) from exc

    if ffmpeg_path:
        silkcoder.set_ffmpeg_path(ffmpeg_path)
        from graiax.silkcoder.utils import Codec

        return await silkcoder.async_encode(audio_bytes, codec=Codec.ffmpeg)
    return await silkcoder.async_encode(audio_bytes)
