"""端到端测试脚本:通过 QQ 官方 OpenAPI 向指定用户私聊发送 MiMo 合成语音。

链路:MiMo TTS 合成 → FFmpeg 转 PCM(系统缺失时自动下载)→ SILK 编码
      → 上传 QQ 官方富媒体(/v2/users/{openid}/files,file_type=3)
      → 发送消息(msg_type=7 富媒体)。

用法(在插件目录下运行,使用 MaiBot 的 python 环境):

    python test_send_voice.py [目标OpenID] [回复文本]

默认目标为测试账号 665833726E2795DB835D8C68192918D4。
注意:该脚本会真实调用 MiMo API 并向 QQ 发送语音。
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import logging
import sys
import tomllib
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import aiohttp

PLUGIN_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PLUGIN_DIR))

from silk_service import audio_to_silk, ensure_ffmpeg_binary
from tts_service import MiMoTTSService

DEFAULT_TARGET = "665833726E2795DB835D8C68192918D4"
DEFAULT_TEXT = "这是一条语音测试,如果你听到我,说明语音链路已经打通了。"
TEXT_STYLE = "温柔自然,像在轻声说话"

APP_ACCESS_TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"
API_BASE_PRODUCTION = "https://api.sgroup.qq.com"
API_BASE_SANDBOX = "https://sandbox.api.sgroup.qq.com"

LOGGER = logging.getLogger("test_send_voice")


def setup_logging() -> None:
    """配置控制台日志。"""

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def load_plugin_config(plugin_dir: Path) -> dict[str, Any]:
    """读取本插件的 config.toml 配置。"""

    config_path = plugin_dir / "config.toml"
    if not config_path.is_file():
        raise FileNotFoundError(f"未找到插件配置: {config_path}")
    with config_path.open("rb") as config_file:
        config = tomllib.load(config_file)
    return config.get("voice", {})


def load_adapter_config() -> tuple[str, str, bool]:
    """读取 QQ 官方适配器配置,返回 (appid, app_secret, sandbox)。"""

    adapter_config_path = PLUGIN_DIR.parent / "qq-official-adapter" / "config.toml"
    if not adapter_config_path.is_file():
        raise FileNotFoundError(f"未找到 QQ 官方适配器配置: {adapter_config_path}")
    with adapter_config_path.open("rb") as config_file:
        config = tomllib.load(config_file)
    credentials = config.get("credentials", {})
    appid = str(credentials.get("appid") or "").strip()
    app_secret = str(credentials.get("app_secret") or "").strip()
    if not appid or not app_secret:
        raise RuntimeError("QQ 官方适配器配置缺少 appid 或 app_secret")
    return appid, app_secret, bool(config.get("credentials", {}).get("sandbox", False))


def load_reference_audio(voice_config: dict[str, Any]) -> tuple[str, str]:
    """加载音色复刻参考音频,返回 (音色名, base64数据URL);未找到返回空。"""
    voice_mode = str(voice_config.get("voice_mode") or "preset")
    if voice_mode != "clone":
        return "", ""
    voice_name = str(voice_config.get("clone_voice") or voice_config.get("default_voice") or "")
    voices_dir = PLUGIN_DIR / str(voice_config.get("voices_dir") or "voices")
    if not voice_name:
        candidates = sorted(voices_dir.glob("*.wav")) + sorted(voices_dir.glob("*.mp3"))
        if not candidates:
            return "", ""
        reference_path = candidates[0]
        voice_name = reference_path.stem
    else:
        reference_path = None
        for suffix in ("wav", "mp3"):
            candidate = voices_dir / f"{voice_name}.{suffix}"
            if candidate.is_file():
                reference_path = candidate
                break
        if reference_path is None:
            raise FileNotFoundError(f"音色复刻参考音频不存在: {voices_dir}/{voice_name}.(wav|mp3)")
    mime_type = "audio/wav" if reference_path.suffix == ".wav" else "audio/mpeg"
    audio_b64 = base64.b64encode(reference_path.read_bytes()).decode("ascii")
    LOGGER.info("已加载参考音色: %s (%dKB)", voice_name, reference_path.stat().st_size // 1024)
    return voice_name, f"data:{mime_type};base64,{audio_b64}"


async def fetch_access_token(
    session: aiohttp.ClientSession,
    appid: str,
    app_secret: str,
) -> str:
    """获取 QQ 官方 access_token。"""

    LOGGER.info("正在获取 QQ access_token (appid=%s)", appid)
    body = {"appId": appid, "clientSecret": app_secret}
    async with session.post(APP_ACCESS_TOKEN_URL, json=body) as response:
        if response.status != 200:
            raise RuntimeError(f"获取 access_token 失败: HTTP {response.status} {await response.text()}")
        data = await response.json()
    access_token = str(data.get("access_token") or "").strip()
    if not access_token:
        raise RuntimeError("access_token 响应缺少 access_token 字段")
    return access_token


async def upload_voice(session: aiohttp.ClientSession, base_url: str, access_token: str, target_openid: str, silk_bytes: bytes) -> str:
    """上传语音富媒体,返回 file_info。"""

    LOGGER.info("正在上传语音富媒体 (silk=%dKB, target=%s)", len(silk_bytes) // 1024, target_openid)
    upload_url = f"{base_url}/v2/users/{quote(target_openid, safe='')}/files"
    body = {
        "file_type": 3,
        "file_data": base64.b64encode(silk_bytes).decode("ascii"),
    }
    headers = {"Authorization": f"QQBot {access_token}"}
    async with session.post(upload_url, json=body, headers=headers) as response:
        response_text = await response.text()
        if response.status != 200:
            raise RuntimeError(f"上传语音失败: HTTP {response.status} {response_text[:300]}")
    data = _parse_json_response(response_text)
    if not _is_ok(response_text):
        raise RuntimeError(f"上传语音失败: {response_text[:300]}")
    file_info = str(data.get("file_info") or "").strip()
    if not file_info:
        raise RuntimeError("上传语音响应缺少 file_info")
    LOGGER.info("上传成功: file_info 长度=%d", len(file_info))
    return file_info


async def send_voice_message(session: aiohttp.ClientSession, base_url: str, access_token: str, target_openid: str, file_info: str) -> None:
    """以 msg_type=7 富媒体消息发送语音。"""

    LOGGER.info("正在发送语音消息到 %s", target_openid)
    send_url = f"{base_url}/v2/users/{quote(target_openid, safe='')}/messages"
    body = {
        "content": "",
        "msg_type": 7,
        "media": {"file_info": file_info},
    }
    headers = {"Authorization": f"QQBot {access_token}"}
    async with session.post(send_url, json=body, headers=headers) as response:
        response_text = await response.text()
        if response.status != 200:
            raise RuntimeError(f"发送语音失败: HTTP {response.status} {response_text[:300]}")
    data = _parse_json_response(response_text)
    if not _is_ok(response_text):
        raise RuntimeError(f"发送语音失败: {response_text[:300]}")
    LOGGER.info("发送成功: message_id=%s", data.get("id", ""))
    return


def _parse_json_response(response_text: str) -> dict[str, Any]:
    """解析 QQ OpenAPI JSON 响应。"""

    import json

    try:
        data = json.loads(response_text)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _is_ok(response_text: str) -> bool:
    """判断 QQ OpenAPI 响应是否成功(无错误字段)。"""

    data = _parse_json_response(response_text)
    code = data.get("code")
    return code is None or int(code) in {0, 200}


async def main() -> int:
    """测试入口。"""

    parser = argparse.ArgumentParser(description="向 QQ 官方机器人私聊目标发送 MiMo 合成语音")
    parser.add_argument("target", nargs="?", default=DEFAULT_TARGET, help=f"目标 OpenID(默认 {DEFAULT_TARGET})")
    parser.add_argument("text", nargs="?", default=DEFAULT_TEXT, help="要合成并发送的文本")
    parser.add_argument("--style", default=TEXT_STYLE, help="语音风格指令")
    parser.add_argument("--save-only", action="store_true", help="只合成并存档,不执行上传与发送")
    args = parser.parse_args()

    target_openid = args.target.strip()
    reply_text = args.text.strip()
    if not target_openid or not reply_text:
        parser.error("目标 OpenID 与回复文本不能为空")

    voice_config = load_plugin_config(PLUGIN_DIR)
    api_key = str(voice_config.get("mimo_api_key") or "").strip()
    if not api_key:
        print("错误: 插件 config.toml 未配置 mimo_api_key", file=sys.stderr)
        return 1
    api_base_url = str(voice_config.get("api_base_url") or "").strip()

    appid, app_secret, sandbox = load_adapter_config()
    base_url = API_BASE_SANDBOX if sandbox else API_BASE_PRODUCTION

    style_instruction = args.style
    LOGGER.info("TTS style_instruction: %s", style_instruction[:120])

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=180)) as session:
        tts = MiMoTTSService(api_key=api_key, api_base_url=api_base_url, logger=LOGGER)
        try:
            voice_mode = str(voice_config.get("voice_mode") or "preset").strip().lower()
            voice_name = ""
            if voice_mode == "design":
                design_prompt = str(voice_config.get("design_voice") or "").strip()
                if not design_prompt:
                    print("错误: design 模式未配置 design_voice", file=sys.stderr)
                    return 1
                LOGGER.info("音色设计描述: %s", design_prompt[:120])
                tts_result = await tts.synthesize_with_design(
                    text=reply_text,
                    design_prompt=design_prompt,
                    style_instruction=style_instruction,
                )
            else:
                voice_name, reference_audio = load_reference_audio(voice_config)
                if reference_audio:
                    tts_result = await tts.synthesize_with_voice_clone(
                        text=reply_text,
                        reference_audio_base64=reference_audio,
                        style_instruction=style_instruction,
                    )
                else:
                    preset_voice = str(voice_config.get("preset_voice") or "mimo_default")
                    voice_name = preset_voice
                    tts_result = await tts.synthesize_with_preset(
                        text=reply_text,
                        voice_id=preset_voice,
                        style_instruction=style_instruction,
                    )
        finally:
            await tts.close()

        if not tts_result.get("success"):
            print(f"MiMo TTS 失败: {tts_result.get('error', '未知错误')}", file=sys.stderr)
            return 1

        audio_b64 = str(tts_result.get("audio_base64") or "")
        if "base64," in audio_b64:
            audio_b64 = audio_b64.split("base64,", 1)[1].strip()
        audio_bytes = base64.b64decode(audio_b64)
        LOGGER.info("MiMo 合成完成: audio=%dKB", len(audio_bytes) // 1024)

        # 合成音频立即存档到本地,确认 MiMo 链路可用
        save_dir = PLUGIN_DIR / str(voice_config.get("save_dir") or "output")
        save_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        wav_path = save_dir / f"tts_{timestamp}_{voice_name or 'default'}.wav"
        wav_path.write_bytes(audio_bytes)
        LOGGER.info("合成音频已存档: %s (%dKB)", wav_path, len(audio_bytes) // 1024)

        if args.save_only:
            print(f"✅ MiMo 合成成功,音频已存档: {wav_path}")
            return 0

        ffmpeg_path = await ensure_ffmpeg_binary(PLUGIN_DIR / "bin", LOGGER)
        silk_bytes = await audio_to_silk(audio_bytes, ffmpeg_path)
        LOGGER.info("SILK 编码完成: %dKB", len(silk_bytes) // 1024)

        access_token = await fetch_access_token(session, appid, app_secret)
        file_info = await upload_voice(session, base_url, access_token, target_openid, silk_bytes)
        await send_voice_message(session, base_url, access_token, target_openid, file_info)

    print(f"✅ 已向 {target_openid} 发送语音: {reply_text[:40]}")
    return 0


if __name__ == "__main__":
    setup_logging()
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as exc:  # noqa: BLE001 - 测试脚本需要对任意失败给出明确退出
        LOGGER.error("测试失败: %s", exc, exc_info=True)
        raise SystemExit(1)
