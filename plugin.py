"""AI Voice Service Plugin for MaiBot."""

from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Tuple

import asyncio
import base64
import binascii

from maibot_sdk import (
    API,
    CONFIG_RELOAD_SCOPE_SELF,
    Command,
    Field,
    MaiBotPlugin,
    PluginConfigBase,
    Tool,
)
from maibot_sdk.types import ActivationType, ToolParameterInfo, ToolParamType

try:
    from .silk_service import audio_to_silk, ensure_ffmpeg_binary
    from .tts_service import MiMoTTSService
except ImportError:
    from silk_service import audio_to_silk, ensure_ffmpeg_binary
    from tts_service import MiMoTTSService


class PluginSectionConfig(PluginConfigBase):
    __ui_label__ = "插件"
    enabled: bool = Field(default=True, description="是否启用")
    config_version: str = Field(default="1.0.0", description="配置版本")


class VoiceSectionConfig(PluginConfigBase):
    __ui_label__ = "语音设置"
    mimo_api_key: str = Field(default="", description="MiMo API Key")
    api_base_url: str = Field(
        default="https://token-plan-cn.xiaomimimo.com/v1",
        description="MiMo API地址",
        json_schema_extra={"label": "API地址"},
    )
    voice_mode: str = Field(default="clone", description="语音模式: 'clone'(音色复刻) / 'preset'(预置音色) / 'design'(音色设计,一句话描述生成音色)")
    preset_voice: str = Field(default="mimo_default", description="预置音色ID（仅preset模式生效）")
    voices_dir: str = Field(default="voices", description="音色目录路径（相对于插件目录）")
    default_voice: str = Field(default="", description="默认音色名称（clone模式下为音频文件名）")
    clone_voice: str = Field(default="", description="复刻音色文件名（clone模式下优先使用）")
    design_voice: str = Field(
        default="一位年轻女性,温柔甜美的声音,像在耳边轻声说话",
        description="音色设计模式的一句话中文描述（仅voice_mode=design时生效,可包含口音/年龄/气质/录音质感等）",
    )
    design_voice_en: str = Field(
        default="A sweet young woman with a soft clear voice, gentle and a little playful, like whispering in a lover's ear.",
        description="音色设计模式的一句话英文描述（支持不同国家口音）",
    )
    llm_task: str = Field(
        default="replyer",
        description="自动翻译使用的模型任务名(如 replyer/planner/utils),需与主程序 model_config.toml 的任务名一致",
    )
    voice_adapter: str = Field(
        default="qq_official",
        description="语音适配器: 'qq_official'(QQ官方机器人,音频自动转silk后上传) 或 'napcat'(NapCat/OneBot v11)",
    )
    save_audio: bool = Field(default=True, description="是否把生成与发送的语音存档到本地")
    save_dir: str = Field(default="output", description="语音存档目录(相对于插件目录)")


class VoicePluginConfig(PluginConfigBase):
    plugin: PluginSectionConfig = Field(default_factory=PluginSectionConfig)
    voice: VoiceSectionConfig = Field(default_factory=VoiceSectionConfig)


class AIVoicePlugin(MaiBotPlugin):
    config_model = VoicePluginConfig

    TTS_HELP_TEXT = (
        "语音命令 /tts 用法:\n"
        "  /tts 说话内容+语气\n"
        "  /tts help\n\n"
        "参数用 + 分隔(也支持 | 或全角＋),共 2 段:\n"
        "  1. 说话内容  要朗读的文字,英文效果最佳;中文会自动翻译成英文再朗读\n"
        "  2. 语气      风格描述,如:gentle sweet tone, slightly slower\n\n"
        "语音固定为英文发音,发送目标为当前会话的发送者(默认发回给输入命令的人)。\n"
        "示例:\n"
        "  /tts No need to thank me~+gentle sweet tone, a little clingy\n"
        "  /tts 不用谢啦~+温柔甜蜜的恋人语气,带点撒娇"
    )

    def __init__(self) -> None:
        super().__init__()
        self.tts_service: Optional[MiMoTTSService] = None
        self.voices: dict[str, str] = {}
        self.default_voice: str = ""
        self._ffmpeg_path: Optional[str] = None
        self._ffmpeg_lock = asyncio.Lock()

    async def on_load(self) -> None:
        self._ensure_config_exists()
        self.ctx.logger.info("AI Voice Plugin loading...")
        api_key = self.config.voice.mimo_api_key
        if not api_key:
            self.ctx.logger.warning("MiMo API Key not configured")
        self.tts_service = MiMoTTSService(api_key=api_key, api_base_url=self.config.voice.api_base_url, logger=self.ctx.logger)
        self.default_voice = self.config.voice.default_voice or self.config.voice.clone_voice
        await self._load_voices()
        self.ctx.logger.info("AI Voice Plugin loaded: mode=%s, default_voice=%s, voices=%s, adapter=%s",
            self.config.voice.voice_mode, self.default_voice, list(self.voices.keys()), self.config.voice.voice_adapter)

    def _ensure_config_exists(self) -> None:
        """如果用户目录下不存在 config.toml，则从 config.example.toml 复制生成。不会覆盖用户已有配置。"""
        import shutil
        plugin_dir = Path(__file__).parent
        config_path = plugin_dir / "config.toml"
        example_path = plugin_dir / "config.example.toml"
        if config_path.exists():
            return
        if example_path.exists():
            shutil.copy2(example_path, config_path)
            self.ctx.logger.info("Generated config.toml from config.example.toml")
        else:
            self.ctx.logger.warning("Neither config.toml nor config.example.toml found")

    async def on_unload(self) -> None:
        if self.tts_service:
            await self.tts_service.close()
            self.tts_service = None
        self.voices.clear()

    async def on_config_update(self, scope: str, config_data: dict[str, object], version: str) -> None:
        if scope == CONFIG_RELOAD_SCOPE_SELF:
            self.ctx.logger.info("Plugin config updated: version=%s", version)
            if self.tts_service and self.config.voice.mimo_api_key:
                self.tts_service.update_api_key(self.config.voice.mimo_api_key)
                self.tts_service.update_api_base_url(self.config.voice.api_base_url)
            self.default_voice = self.config.voice.default_voice or self.config.voice.clone_voice
            await self._load_voices()
            self.ctx.logger.info("Config reloaded: mode=%s, default_voice=%s", self.config.voice.voice_mode, self.default_voice)

    async def _load_voices(self) -> None:
        voices_dir_str = self.config.voice.voices_dir
        plugin_dir = Path(__file__).parent
        voices_dir = plugin_dir / voices_dir_str
        self.ctx.logger.info("Loading voices from: %s (plugin_dir=%s)", voices_dir, plugin_dir)

        if not voices_dir.exists():
            self.ctx.logger.warning("Voices dir not found: %s, creating it", voices_dir)
            voices_dir.mkdir(parents=True, exist_ok=True)
            return

        self.ctx.logger.info("Voices dir exists: %s, contents: %s", voices_dir, [f.name for f in voices_dir.iterdir()])

        audio_files = list(voices_dir.glob("*.wav")) + list(voices_dir.glob("*.mp3"))
        if not audio_files:
            self.ctx.logger.warning("No audio files found in: %s", voices_dir)
            return

        self.voices.clear()
        for audio_file in audio_files:
            voice_name = audio_file.stem
            file_size = audio_file.stat().st_size
            # Detect MIME type from extension
            suffix = audio_file.suffix.lower()
            mime_type = "audio/wav" if suffix == ".wav" else "audio/mpeg"
            with open(audio_file, "rb") as f:
                audio_bytes = f.read()
            b64_data = base64.b64encode(audio_bytes).decode("ascii")
            # Store with MIME type prefix for API call
            self.voices[voice_name] = f"data:{mime_type};base64,{b64_data}"
            self.ctx.logger.info("Loaded voice: %s, raw=%dKB, b64=%dKB, mime=%s", voice_name, file_size // 1024, len(b64_data) // 1024, mime_type)

        if not self.default_voice and self.voices:
            self.default_voice = next(iter(self.voices))
            self.ctx.logger.info("Using default voice: %s", self.default_voice)

        self.ctx.logger.info("Voice loading complete, total %d voices, names=%s", len(self.voices), list(self.voices.keys()))

    async def _find_stream_id(self, kwargs: dict) -> str:
        """Find stream_id from various sources."""
        # Method 1: from kwargs (system may pass it)
        sid = kwargs.get("stream_id", "")
        if sid:
            self.ctx.logger.info("Got stream_id from kwargs: %s", sid)
            return str(sid)

        # Method 2: from kwargs message
        msg = kwargs.get("message", {})
        if isinstance(msg, dict):
            sid = msg.get("stream_id", "")
            if sid:
                self.ctx.logger.info("Got stream_id from message: %s", sid)
                return str(sid)

        # Method 3: try to find group stream
        try:
            streams = await self.ctx.chat.get_group_streams()
            if streams:
                sid = streams[0].get("stream_id", "")
                self.ctx.logger.info("Using first group stream: %s", sid)
                return str(sid)
        except Exception as e:
            self.ctx.logger.warning("chat.get_group_streams failed: %s", e)

        return ""

    def _save_audio_bytes(self, audio_bytes: bytes, prefix: str, suffix: str, voice_key: str) -> Optional[str]:
        """把音频字节写入本地存档目录,返回文件路径;失败仅记录日志。"""

        if not self.config.voice.save_audio:
            return None
        save_dir = Path(__file__).parent / (self.config.voice.save_dir or "output")
        try:
            save_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            file_path = save_dir / f"{prefix}_{timestamp}_{voice_key or 'default'}.{suffix}"
            file_path.write_bytes(audio_bytes)
            self.ctx.logger.info("语音已存档: %s (%dKB)", file_path, len(audio_bytes) // 1024)
            return str(file_path)
        except OSError as exc:
            self.ctx.logger.warning(f"语音存档失败: {exc}")
            return None

    async def _ensure_ffmpeg(self) -> Optional[str]:
        """准备 FFmpeg 可执行文件:查找系统 PATH、插件 bin 目录,最后自动下载。"""

        if self._ffmpeg_path is not None:
            return self._ffmpeg_path
        async with self._ffmpeg_lock:
            if self._ffmpeg_path is not None:
                return self._ffmpeg_path
            bin_dir = Path(__file__).parent / "bin"
            self._ffmpeg_path = (await ensure_ffmpeg_binary(bin_dir, self.ctx.logger)) or ""
            return self._ffmpeg_path or None

    async def _send_voice(self, audio_b64: str, stream_id: str) -> bool:
        """按语音适配器配置发送语音音频,成功返回 True。"""

        adapter = (self.config.voice.voice_adapter or "qq_official").strip().lower()
        if adapter == "qq_official":
            return await self._send_voice_qq_official(audio_b64, stream_id)
        if adapter == "napcat":
            return await self._send_voice_napcat(audio_b64, stream_id)
        self.ctx.logger.error("不支持的语音适配器配置: %s (可选 qq_official / napcat)", adapter)
        return False

    async def _send_voice_qq_official(self, audio_b64: str, stream_id: str) -> bool:
        """发送 QQ 官方机器人语音:先转 silk,再以 voice 段交给官方适配器上传。"""

        self.ctx.logger.info("Sending QQ official voice to stream=%s, b64_len=%d", stream_id, len(audio_b64))
        try:
            audio_bytes = base64.b64decode(audio_b64)
        except (binascii.Error, ValueError) as exc:
            self.ctx.logger.error("音频 base64 解析失败: %s", exc)
            return False

        ffmpeg_path = await self._ensure_ffmpeg()
        try:
            silk_bytes = await audio_to_silk(audio_bytes, ffmpeg_path)
        except Exception as exc:
            self.ctx.logger.error(f"音频转 silk 失败: {exc}", exc_info=True)
            return False

        self._save_audio_bytes(silk_bytes, "tts_silk", "silk", "official")
        silk_b64 = base64.b64encode(silk_bytes).decode("ascii")
        self.ctx.logger.info("QQ official voice converted: silk_len=%dKB", len(silk_bytes) // 1024)

        sent = await self.ctx.send.custom("voice", silk_b64, stream_id)
        if not sent:
            self.ctx.logger.error("QQ 官方语音发送失败: voice 段未送达, stream=%s", stream_id)
            return False
        self.ctx.logger.info("QQ official voice sent via voice segment, stream=%s", stream_id)
        return True

    async def _send_voice_napcat(self, audio_b64: str, stream_id: str) -> bool:
        """发送 NapCat 语音:使用 OneBot v11 的 record 段 + base64 文件。"""

        self.ctx.logger.info("Sending NapCat voice to stream=%s, b64_len=%d", stream_id, len(audio_b64))

        b64_url = f"base64://{audio_b64}"
        try:
            sent = await self.ctx.send.custom("record", {"file": b64_url}, stream_id)
        except Exception as exc:
            self.ctx.logger.warning("record+base64 发送异常: %s", exc)
            sent = False
        if not sent:
            self.ctx.logger.error("NapCat 语音发送失败: record+base64 未送达, stream=%s", stream_id)
            return False
        self.ctx.logger.info("NapCat voice sent via record+base64, stream=%s", stream_id)
        return True

    @staticmethod
    def _contains_chinese(text: str) -> bool:
        """判断文本是否包含常用汉字(CJK 统一表意文字)。"""

        return any(0x4E00 <= ord(character) <= 0x9FFF for character in text)

    async def _translate_to_english(self, text: str) -> str:
        """调用 Host LLM 把文本翻译成英文,保留括号语气标签。"""

        task_name = (self.config.voice.llm_task or "").strip() or "replyer"
        prompt = (
            "请把下面的一段话翻译成英文,保持口语化与原有的情绪语气,"
            "直接输出翻译结果,不要任何解释、引号或额外内容。\n"
            "若原文包含括号语气标签(如(轻笑)(叹气)(温柔)),请保留在原位。\n\n"
            f"{text}"
        )
        result = await self.ctx.llm.generate(prompt, model=task_name)
        if not isinstance(result, dict) or not result.get("success"):
            raise RuntimeError(str((result or {}).get("error") or "LLM 无法响应"))
        translated_text = str(result.get("response") or "").strip()
        if not translated_text:
            raise RuntimeError("LLM 翻译结果为空")
        return translated_text

    def _resolve_voice(self, voice_name: str) -> tuple[str, str, str]:
        """解析音色名称，返回 (voice_key, audio_base64, mode)。
        mode: 'clone' 使用音色复刻, 'preset' 使用预置音色。
        """
        voice_mode = self.config.voice.voice_mode

        # 确定要使用的音色 key
        voice_key = voice_name if voice_name and voice_name != "default" else self.default_voice

        # clone 模式：从本地音频文件中查找参考音频
        if voice_mode == "clone":
            if voice_key and voice_key in self.voices:
                return voice_key, self.voices[voice_key], "clone"

            # clone_voice 配置兜底
            clone_fallback = self.config.voice.clone_voice
            if clone_fallback and clone_fallback in self.voices:
                self.ctx.logger.warning("Voice '%s' not found, using clone_voice config: '%s'", voice_key, clone_fallback)
                return clone_fallback, self.voices[clone_fallback], "clone"

            # 如果 voices 非空但指定的没有，报错而非静默切换
            if self.voices:
                available = list(self.voices.keys())
                self.ctx.logger.error("Voice '%s' not found in voices! Available: %s. "
                    "请在 config.toml 的 clone_voice 或 default_voice 中指定一个已有的音色名。", voice_key, available)
                return "", "", ""

            self.ctx.logger.error("No voices loaded! Please put .wav/.mp3 files in the voices/ directory.")
            return "", "", ""

        # design 模式:音色描述即音色来源,无需参考音频;固定使用英文描述
        if voice_mode == "design":
            design_prompt = (
                voice_name.strip()
                if voice_name and voice_name != "default"
                else (self.config.voice.design_voice_en or self.config.voice.design_voice or "").strip()
            )
            if not design_prompt:
                self.ctx.logger.error("design 模式缺少音色描述,请在 config.toml 的 design_voice 或 design_voice_en 中填写一句话描述。")
                return "", "", ""
            return design_prompt, "", "design"

        # preset 模式
        preset_id = voice_key or self.config.voice.preset_voice or "mimo_default"
        return preset_id, "", "preset"

    @API("voice_clone_tts", description="TTS with specified voice", version="1", public=True)
    async def voice_clone_tts(
        self,
        text: str,
        style_instruction: str = "",
        stream_id: str = "",
        voice_name: str = "",
    ) -> dict[str, Any]:
        if not self.tts_service:
            return {"success": False, "error": "TTS service not initialized"}

        if not text or not text.strip():
            return {"success": False, "error": "Empty text"}

        # 强制英文:任何含中文的输入都会翻译为英文后再进 MiMo,避免中文炸音
        voice_key, ref_audio, mode = self._resolve_voice(voice_name)

        tts_text = text
        tts_style = style_instruction
        try:
            if self._contains_chinese(text):
                tts_text = await self._translate_to_english(text)
                self.ctx.logger.info("文本已翻译为英文: %s -> %s", text[:60], tts_text[:80])
            if self._contains_chinese(tts_style):
                tts_style = await self._translate_to_english(tts_style)
                self.ctx.logger.info("风格已翻译为英文: %s", tts_style[:80])
            if mode == "design" and self._contains_chinese(voice_key):
                voice_key = await self._translate_to_english(voice_key)
                self.ctx.logger.info("音色描述已翻译为英文: %s", voice_key[:80])
        except Exception as exc:
            self.ctx.logger.error(f"自动翻译失败: {exc}", exc_info=True)
            return {"success": False, "error": f"自动翻译失败,请检查主程序模型配置: {exc}"}
        if not voice_key:
            return {"success": False, "error": "No voice configured. Check voice config."}

        self.ctx.logger.info("TTS request: mode=%s, voice_key=%s, text_len=%d, style_len=%d",
            mode, voice_key, len(text), len(style_instruction))

        # 语言处理:仅支持 日语(LLM翻译成日语 -> 中文谐音拟合) 与 中文(原样朗读)
        try:
            self.ctx.logger.info("TTS style_instruction: %s", tts_style[:120])
            if mode == "clone":
                result = await self.tts_service.synthesize_with_voice_clone(
                    text=tts_text, reference_audio_base64=ref_audio, style_instruction=tts_style,
                )
            elif mode == "design":
                result = await self.tts_service.synthesize_with_design(
                    text=tts_text, design_prompt=voice_key, style_instruction=tts_style,
                )
            else:
                result = await self.tts_service.synthesize_with_preset(
                    text=tts_text, voice_id=voice_key, style_instruction=tts_style,
                )

            # 释放参考音频引用
            ref_audio = ""

            success = result.get("success")
            error = result.get("error", "")
            audio_b64 = result.get("audio_base64", "")
            self.ctx.logger.info("TTS result: success=%s, audio_len=%d, error=%s", success, len(audio_b64), error)

            if success and audio_b64:
                # 提取纯 base64 数据
                if "base64," in audio_b64:
                    audio_b64 = audio_b64.split("base64,", 1)[1]
                    audio_b64 = audio_b64.strip()

                # 合成成功后先存档原始音频到本地,便于排查与复核
                try:
                    raw_audio_bytes = base64.b64decode(audio_b64)
                    self._save_audio_bytes(raw_audio_bytes, "tts", "wav", voice_key)
                except (binascii.Error, ValueError) as exc:
                    self.ctx.logger.warning(f"TTS 音频 base64 无效,跳过存档: {exc}")

                # 立即从 result 中移除大字段，避免双重内存占用
                result.pop("audio_base64", None)

                if stream_id:
                    sent = await self._send_voice(audio_b64, stream_id)
                    audio_b64 = ""
                    if not sent:
                        result["success"] = False
                        result["error"] = "语音发送失败,请查看插件日志"
                        self.ctx.logger.error("TTS synthesized but voice send failed: stream=%s", stream_id)
                else:
                    audio_b64 = ""
            elif audio_b64:
                # 不需要发送时也清理
                result.pop("audio_base64", None)

            return result
        except Exception as e:
            self.ctx.logger.error("TTS failed: %s", e)
            return {"success": False, "error": str(e)}

    @Tool(
        "send_voice_reply",
        brief_description="使用语音回复用户，可选传入风格指令控制语气情绪",
        detailed_description=(
            "使用语音进行回复。当用户要求语音、用户发送了语音消息、或你认为当前场景适合语音回复时调用。\n"
            "必填参数：reply_text（回复文本）、msg_id（当前消息ID）。\n"
            "可选参数：style_instruction（语音风格指令），不传则使用默认音色自然朗读，需要更生动的表达时可填写。\n\n"
            "【强制英文】语音固定使用英文发音,回复文本请直接写成英文原文,质量最佳。"
            "如果你给出的 reply_text 是中文,插件会自动翻译成英文后再朗读,请放心使用中文描述,"
            "但英文原文的效果更好。style_instruction 也建议用英文,中文会自动翻译。\n\n"
            "【风格控制】可通过 style_instruction 参数精细控制语音演绎效果，"
            "根据你当前扮演的角色人设和对话情境，主动提供完整的风格描述，让语音更拟人、更有感染力。\n\n"
            "支持三种风格控制方式，可自由组合：\n\n"
            "1. 自然语言风格指令（推荐）：用自然语言描述语气、情绪、语速等，像给演员说戏一样。\n"
            "   示例：'一位温柔的少女，声音清甜软糯，语速偏慢，用安慰的语气，带点关切'\n"
            "   示例：'语气俏皮活泼，带点小得意，语速偏快，声音明亮有活力'\n"
            "   示例：'声音低沉严肃，像在教训人，语速慢一些，带点长辈的威严'\n\n"
            "2. 导演模式（高级）：从角色、场景、指导三个维度全方位刻画表演，适合需要高度拟人化的场景。\n"
            "   示例：'角色：一位温柔的大姐姐，性格体贴温暖，声音甜美有亲和力。"
            "场景：安慰失恋的朋友。指导：语调柔和温暖，气息松弛，偶尔带叹息，语速偏慢，尾音上扬带笑意。'\n"
            "   示例：'角色：百年门阀的大小姐，声音冷冽有威压，说话语速极慢，每个字都像在舌尖滚过。"
            "场景：在祠堂面对企图带她私奔的男人。指导：实音重且硬，尾音处加入轻微气音透出疲惫。'\n\n"
            "3. 音频标签（在 reply_text 中使用）：在文本任意位置用括号标注语气/情绪/声音动作。\n"
            "   中文全角/半角均可：（紧张）呼……冷静。（叹气）算了。（轻笑）好吧好吧。\n"
            "   英文：(sighs) I don't know. (laughs) That's funny!\n"
            "   整段风格标签：在文本开头加（温柔）你好呀~ 或（东北话）哎呀妈呀~ 或（唱歌）歌词...\n"
            "   常用风格标签：开心/悲伤/愤怒/温柔/慵懒/俏皮/磁性/沙哑/甜美/冷漠/严肃/活泼/深沉 等\n"
            "   常用动作标签：叹气/轻笑/哽咽/深呼吸/咳嗽/打哈欠/低语/提高音量 等\n\n"
            "提示：style_instruction（整体风格）+ reply_text 中的音频标签（句内细节）可同时使用，两者不冲突。"
        ),
        activation_type=ActivationType.ALWAYS,
        parameters=[
            ToolParameterInfo(name="reply_text", param_type=ToolParamType.STRING, description="回复文本。可在文本中插入音频标签控制句内语气细节，如（叹气）（轻笑）（温柔）（紧张）等。", required=True),
            ToolParameterInfo(name="msg_id", param_type=ToolParamType.STRING, description="当前消息ID", required=True),
            ToolParameterInfo(
                name="style_instruction",
                param_type=ToolParamType.STRING,
                description=(
                    "（可选）语音风格指令，用自然语言描述语气、情绪、语速等，让语音更拟人。\n"
                    "不传则默认朗读。当你觉得需要更生动的表达时再填写。\n"
                    "简单用法：'gentle sweet tone, slightly slower'\n"
                    "也可配合 reply_text 中的括号语气标签（(sighs)/(laughs) 等）使用。"
                ),
                required=False,
                default="",
            ),
        ],
    )
    async def send_voice_reply(self, reply_text: str, msg_id: str = "", style_instruction: str = "", **kwargs: Any) -> dict[str, Any]:
        # Find the correct stream_id
        stream_id = await self._find_stream_id(kwargs)
        if not stream_id:
            self.ctx.logger.error("Cannot find stream_id for msg_id=%s, kwargs_keys=%s", msg_id, list(kwargs.keys()))
            return {"success": False, "method": "voice", "error": "Cannot find chat stream"}

        self.ctx.logger.info("Using stream_id=%s for voice reply, text_len=%d", stream_id, len(reply_text))

        # Always use the configured default voice
        asyncio.create_task(self._async_voice_reply(reply_text, style_instruction, stream_id, ""))
        return {"success": True, "method": "voice", "error": ""}

    @Command(
        "tts",
        description="发送语音: /tts 说话内容+语气;默认发回给发送者;输入 /tts help 查看用法",
        pattern=r"^/tts(?:\s|$)",
    )
    async def handle_tts_command(self, stream_id: str = "", **kwargs: Any) -> Tuple[bool, str, int]:
        """/tts 命令:按 + 分隔的 说话内容+语气 发送语音,默认发回给当前会话发送者。"""

        raw_text = self._extract_plain_text(kwargs)
        parts = self._split_tts_args(raw_text)

        # /tts help 或缺少参数时输出用法说明
        if parts and parts[0].lower() in {"help", "--help", "-h"}:
            await self._reply_text_in_stream(self.TTS_HELP_TEXT, stream_id)
            return True, self.TTS_HELP_TEXT, 2
        if len(parts) < 2 or not parts[0] or not parts[1]:
            reply_text = "缺少说话内容或语气参数。\n\n" + self.TTS_HELP_TEXT
            await self._reply_text_in_stream(reply_text, stream_id)
            return True, reply_text, 2

        speech_text = parts[0]
        tone = parts[1]

        if not stream_id:
            error_text = "找不到当前会话,无法发送语音。"
            await self._reply_text_in_stream(error_text, stream_id)
            return True, error_text, 2

        self.ctx.logger.info(f"/tts 命令: stream={stream_id} text_len={len(speech_text)} tone_len={len(tone)}")
        try:
            result = await self.voice_clone_tts(
                text=speech_text,
                style_instruction=tone,
                stream_id=stream_id,
            )
        except Exception as exc:
            self.ctx.logger.error(f"/tts 语音生成失败: {exc}", exc_info=True)
            error_text = f"语音生成失败: {exc}"
            await self._reply_text_in_stream(error_text, stream_id)
            return True, error_text, 2

        if not result.get("success"):
            error_text = f"语音发送失败: {result.get('error', '未知错误')}"
            await self._reply_text_in_stream(error_text, stream_id)
            return True, error_text, 2

        success_text = "语音已发送。"
        await self._reply_text_in_stream(success_text, stream_id)
        return True, success_text, 2

    @staticmethod
    def _extract_plain_text(kwargs: dict) -> str:
        """从 Command 调用参数中提取消息纯文本。"""

        text = str(kwargs.get("text") or "")
        message = kwargs.get("message") or {}
        if not text and isinstance(message, dict):
            text = str(message.get("plain_text") or message.get("processed_plain_text") or "")
        return text.strip()

    @staticmethod
    def _split_tts_args(raw_text: str) -> list[str]:
        """切分 /tts 参数,返回 说话内容/语气 的段落列表。

        主分隔符为英文 +,同时兼容 | 与全角加号 ＋。
        """

        if raw_text.startswith("/tts"):
            raw_text = raw_text[len("/tts"):].strip()
        if not raw_text:
            return []
        normalized_text = raw_text.replace("|", "+").replace("＋", "+")
        return [part.strip() for part in normalized_text.split("+")]

    async def _reply_text_in_stream(self, text: str, stream_id: str) -> None:
        """向指定流发送提示文本,失败仅记录日志,不影响命令结果。"""

        if not stream_id:
            return
        try:
            await self.ctx.send.text(text, stream_id)
        except Exception as exc:
            self.ctx.logger.warning(f"/tts 回复文本发送失败: {exc}")

    async def _async_voice_reply(
        self,
        text: str,
        style_instruction: str,
        stream_id: str,
        voice_name: str,
    ) -> None:
        """Send voice reply asynchronously."""
        try:
            result = await self.voice_clone_tts(
                text=text,
                style_instruction=style_instruction,
                stream_id=stream_id,
                voice_name=voice_name,
            )
            self.ctx.logger.info("Async voice reply result: success=%s, error=%s", result.get("success"), result.get("error", ""))
        except Exception as e:
            self.ctx.logger.error("Async voice reply failed: %s", e)


def create_plugin() -> AIVoicePlugin:
    return AIVoicePlugin()