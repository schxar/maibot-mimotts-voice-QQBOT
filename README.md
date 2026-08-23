# MiMo TTS Voice Plugin for MaiBot

基于小米 MiMo-V2.5-TTS 的 AI 语音服务插件，为 MaiBot 提供智能语音回复功能。

> ⚠️ **本 Fork 说明**：语音**只使用英文**（中文内容会自动翻译成英文后再合成，翻译后朗读质量较差，不建议中文），主要支持 `design`（一句话英文描述生成音色）模式。`clone`（音色复刻）与 `preset`（预置音色）模式仅保留兼容，**不建议切换使用**。


## 功能特性(好用的话请点个star⭐~)

- 🎨 **音色设计（design，本 Fork 主推）**：一句话英文描述凭空生成全新音色，无需任何参考音频，支持年龄、性别、口音、音色、语速、气质、录音质感等任意维度
- 🎤 **预置音色（preset）**：内置 9 种精品音色（中文/英文，男声/女声），仅保留兼容，**非本 Fork 主推**（中文音色本 Fork 不适用）
- 🎭 **音色复刻（clone）**：通过音频样本复刻音色，放入 `voices/` 目录即可，仅保留兼容，**非本 Fork 主推**
- 🤖 **AI 工具链暴露**：`send_voice_reply` 工具暴露在 AI 工具链中（`activation_type=ALWAYS`），AI 可自主决定何时使用语音回复
- 🎨 **AI 智能风格控制**：风格指令由 AI 根据角色人设和对话情境自行决定，无需用户手动配置
- 🎬 **导演模式**：支持从角色、场景、指导三个维度全方位刻画语音表演，AI 自动判断何时需要深度演绎
- ⚡ **异步发送**：语音合成和发送异步执行，不阻塞 AI 主循环
- 🚦 **双适配器自动路由**：`voice_adapter = "auto"` 按目标 ID 形态自动选择发送方式——官方机器人 openid 走官方 API（先转 silk），OneBot v11 纯数字 QQ 号把原始 wav 交给 SnowLuma 等链路自行转码，语音时长显示正确
- 🔧 **自动配置初始化**：首次启动时自动从 `config.example.toml` 生成 `config.toml`，不覆盖用户已有配置

## 安装

### 1. 复制插件到 MaiBot

```bash
cd /path/to/MaiBot/plugins/
git clone https://github.com/schxar/maibot-mimotts-voice-QQBOT.git
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

（或手动安装：`pip install aiohttp graiax-silkcoder`；`graiax-silkcoder` 提供 SILK 编码器，QQ 官方机器人语音消息需要 silk 格式。）

### 3. 配置

编辑 `config.toml`（可从 `config.example.toml` 复制）：

```toml
[plugin]
enabled = true
config_version = "1.0.0"

[voice]
mimo_api_key = "your_api_key_here"
api_base_url = "https://token-plan-cn.xiaomimimo.com/v1"
voice_mode = "design"
design_voice_en = "A sweet young woman with a soft clear voice, british accent, gentle and a little playful, like whispering in a lover's ear."
voice_adapter = "auto"
```

### 4. 重启 MaiBot

插件会自动加载。

## 配置说明

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `mimo_api_key` | MiMo API Key（必填） | - |
| `api_base_url` | API 地址 | `https://token-plan-cn.xiaomimimo.com/v1` |
| `voice_mode` | 音色模式：`design`（主推） / `preset`、`clone`（仅兼容，不推荐） | `design` |
| `preset_voice` | 预置音色名称（preset 模式，不推荐） | `mimo_default` |
| `voices_dir` | 音色复刻目录（clone 模式，不推荐，相对于插件目录） | `voices` |
| `clone_voice` | 指定复刻音色文件名（clone 模式不推荐，留空使用第一个文件） | - |
| `design_voice_en` | 音色设计模式的一句话英文描述（推荐填写；固定英文发音，支持不同国家口音） | 见 config.example.toml |
| `design_voice` | 音色设计模式的一句话中文描述（不推荐填写，中文朗读质量差；仅当英文描述为空时兜底，且会先翻译成英文） | 空 |
| `llm_task` | 自动翻译使用的模型任务名（如 `replyer`/`planner`/`utils`），需与主程序 `model_config.toml` 的任务名一致 | `replyer` |
| `voice_adapter` | 语音适配器：`auto`（默认，按目标 ID 自动路由：官方机器人 openid 含大写字母→先转 silk 后上传；OneBot v11 纯数字 QQ 号→原始音频直接交给适配器/SnowLuma 转码，避免时长被固定为 1 秒） / `qq_official` / `onebot` / `napcat`（旧 record+base64，保留兼容） | `auto` |
| `save_audio` | 是否把生成与发送的语音存档到本地 | `true` |
| `save_dir` | 语音存档目录（相对于插件目录） | `output` |

语音固定为英文发音：文本、风格指令或音色描述中含中文的内容会自动调用主程序 LLM 翻译成英文后再合成，避免中文炸音；**建议文本、语气与音色描述一律使用英文**，中文经翻译后再朗读质量明显下降。

### 语音适配器说明

- **`auto`（默认）**：按聊天流目标 ID 形态自动路由——QQ 官方机器人 openid 必然包含大写字母，自动走 `qq_official`（先转 silk）；OneBot v11 的 QQ 号为纯数字，自动走 `onebot`（原始音频直接交给适配器/SnowLuma 转码，避免 silk 时长元数据被固定为 1 秒的问题）。
- **`qq_official`（QQ 官方机器人）**：语音发送前会把合成音频统一转换为腾讯 silk 格式（通过 FFmpeg 重采样 + SILK 编码器），再交由官方适配器上传并发送，适配 QQ 官方富媒体消息格式。官方 API 强制要求 silk，无法直接上传 wav。
- **`onebot`（OneBot v11 / SnowLuma）**：直接以 `voice` 段发送原始合成音频（wav），由 SnowLuma 等链路自行转码为 silk 并计算真实时长。
- **`napcat`（旧 NapCat 模式，保留兼容）**：使用 OneBot v11 的 `record` 段 + `base64://` 发送。

FFmpeg 查找顺序为：系统 PATH → 插件目录 `bin/` → 自动下载。系统未安装 FFmpeg 时，插件会从受信 HTTPS 源自动下载静态构建到 `bin/` 目录（Windows / Linux），无需手动安装。

### API 地址

| 用户类型 | 地址 |
|---------|------|
| Plan 用户（包月/套餐） | `https://token-plan-cn.xiaomimimo.com/v1` |
| 按量计费用户 | `https://api.xiaomimimo.com/v1` |

## 音色模式

### 预置音色（preset，仅保留兼容，不推荐）

> ⚠️ 本 Fork 不推荐使用 preset 模式：语音固定英文，中文预置音色不适用；以下音色列表仅作保留记录。

无需额外配置，直接在 `preset_voice` 中填入音色名称：

| 音色名 | 语言 | 性别 |
|--------|------|------|
| `mimo_default` | 中文 | 女性（冰糖） |
| `冰糖` | 中文 | 女性 |
| `茉莉` | 中文 | 女性 |
| `苏打` | 中文 | 男性 |
| `白桦` | 中文 | 男性 |
| `Mia` | 英文 | 女性 |
| `Chloe` | 英文 | 女性 |
| `Milo` | 英文 | 男性 |
| `Dean` | 英文 | 男性 |

### 音色设计（design，主推）

无需任何参考音频，在 `design_voice_en` 里写一句话英文描述即可凭空生成全新音色（支持年龄、性别、口音、音色、语速、气质、录音质感等任意维度）：

```
design_voice_en = "A sweet young woman with a soft clear voice, british accent, gentle and a little playful, like whispering in a lover's ear."
```

**建议一律使用英文描述**：插件固定以英文朗读，中文描述会先经主程序 LLM 翻译成英文再用，朗读质量明显不如直接写英文。`design_voice`（中文）仅作兜底，不建议填写。

每次合成时，语音风格指令（`style_instruction`）还会自动并入音色描述，用于指导语气与情绪。`voice_mode = "design"` 时忽略 `voices/` 目录与预置音色配置。

### 音色复刻（clone，仅保留兼容，不推荐）

> ⚠️ 本 Fork 不推荐使用 clone 模式（仅保留兼容，未重点维护）；且语音固定英文，参考音色只用于复刻音色质感。

1. 将 `voice_mode` 改为 `clone`
2. 将参考音频文件放入 `voices/` 目录（支持 WAV 和 MP3）
3. 文件名即为音色名称，建议录音时长 30 秒以上

```
plugins/maibot-mimotts-voice/voices/
└── salt.wav    # 音色名称: salt
```

## 工具说明

插件提供 `send_voice_reply` 工具给 AI，AI 会根据对话上下文自行判断是否调用。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `reply_text` | string | ✅ | 要合成语音的回复文本 |
| `msg_id` | string | ✅ | 当前消息 ID，用于自动查找聊天流 |
| `style_instruction` | string | ❌ | 语音风格指令（建议用英文） |

## 命令说明

### `/tts` 手动发送语音

```
/tts 说话内容+语气
/tts help
```

参数用 `+` 分隔（也支持 `|` 或全角`＋`），共 2 段：

| 段 | 参数 | 说明 |
|----|------|------|
| 1 | 说话内容 | 要朗读的文字，建议直接用英文（效果最佳）；中文会先翻译成英文再朗读，质量较差 |
| 2 | 语气 | 风格描述（英文），如 `gentle sweet tone, slightly slower` |

示例：

- `/tts No need to thank me~+gentle sweet tone, a little clingy` ← 发回给发送者
- `/tts 不用谢啦~+gentle sweet tone, a little clingy` ← 发回给发送者（中文自动翻译，建议直接写英文）
- `/tts help` ← 查看完整用法

语音固定为英文发音（中文输入会自动翻译，建议尽量用英文输入以保证最好效果），默认发回当前会话的发送者。

### 端到端测试脚本

`test_send_voice.py` 可直接验证完整链路（MiMo 合成 → silk 转换 → QQ 官方 OpenAPI 上传与发送），
运行方式（使用 MaiBot 的 python 环境）：

```bash
python test_send_voice.py [目标OpenID] [回复文本]
```

默认目标为 665833726E2795DB835D8C68192918D4。注意该脚本会真实调用 MiMo API 并向 QQ 发送语音。

### 风格控制方式

风格控制由 AI 根据当前扮演的角色人设和对话情境**自行决定**，无需用户手动指定。AI 会自动判断何时需要风格指令，并选择合适的表达方式。

支持三种风格控制方式，AI 可自由组合使用：

**1. 自然语言风格指令**（AI 通过 `style_instruction` 参数传入）

AI 用自然语言描述语气、情绪、语速等，像给演员说戏一样：

- `一位温柔的少女，声音清甜软糯，语速偏慢，用安慰的语气`
- `语气俏皮活泼，带点小得意，语速偏快`
- `声音低沉严肃，像在教训人，语速慢一些`

**2. 导演模式**（AI 根据场景需要自动启用）

AI 从角色、场景、指导三个维度全方位刻画表演：

```
角色：一位温柔的大姐姐，性格体贴温暖，声音甜美有亲和力。
场景：安慰失恋的朋友。
指导：语调柔和温暖，气息松弛，偶尔带叹息，语速偏慢，尾音上扬带笑意。
```

**3. 音频标签**（AI 在 `reply_text` 中自动插入）

AI 在文本任意位置用括号标注语气/情绪/声音动作：

- 中文：`（紧张）呼……冷静。（叹气）算了。（轻笑）好吧好吧。`
- 英文：`(sighs) I don't know. (laughs) That's funny!`
- 整段风格：`（温柔）你好呀~` `（东北话）哎呀妈呀~` `（唱歌）歌词...`
- 常用标签：开心/悲伤/愤怒/温柔/慵懒/俏皮/磁性/沙哑/甜美/冷漠/叹气/轻笑/哽咽/深呼吸 等

> `style_instruction`（整体风格）+ `reply_text` 中的音频标签（句内细节）可同时使用，两者不冲突。

## 目录结构

```
maibot-mimotts-voice/
├── plugin.py           # 插件主入口
├── tts_service.py      # MiMo TTS 服务封装
├── silk_service.py     # SILK 音频转换与 FFmpeg 管理
├── config.toml         # 插件配置（gitignored）
├── config.example.toml # 配置示例
├── _manifest.json      # 插件清单
├── README.md           # 本文档
├── LICENSE             # MIT 许可证
├── .gitignore          # Git 忽略规则
├── requirements.txt    # Python 依赖
├── voices/             # 音色复刻文件目录（gitignored）
└── bin/                # 自动下载的 FFmpeg（gitignored）
```

## 更新日志

### v1.7.9

- 🚦 **双适配器自动路由**：新增 `voice_adapter = "auto"`（默认），按目标 ID 形态自动选择发送方式——官方机器人 openid（含大写字母）先转 silk 上传，OneBot v11 纯数字 QQ 号把原始 wav 直接交给适配器/SnowLuma 转码，修复 SnowLuma 上语音被显示为 1 秒的问题
- ➕ 新增 `onebot` 适配器模式（voice 段 + 原始音频），`napcat` 旧模式保留兼容

### v1.7.8

- 🔒 **强制英文**：语音固定英文发音,移除语言切换;文本/风格/音色描述含中文一律自动翻译为英文后合成,禁止中文朗读

### v1.7.7

- 🔁 **自动翻译**：英文模式下若文本、风格指令或音色描述含中文，自动调用 LLM 翻译为英文后再合成（含括号语气标签保留），不再出现中文破音
- ⚙️ 新增 `auto_translate`、`llm_task` 配置

### v1.7.6

- 🌐 **默认语言配置**：新增 `voice_language` 配置项（`en`/`zh`，默认英文，避免参考音色读中文破音），`/tts` 与工具语言参数可临时覆盖
- 📢 工具提示词同步为默认英文语音：reply_text 默认写英文，明确要中文时才传 `reply_language='zh'`

### v1.7.5

- 🇬🇧 **英文音色设计**：`/tts` 新增第三段 `+英文` 参数，音色按 `design_voice_en` 英文描述设计（支持不同国家口音），中英文描述可自由切换

### v1.7.4

- 🎨 **音色设计模式**：新增 `voice_mode = "design"`，无需参考音频，一句话描述即可生成全新音色（MiMo-V2.5-TTS-VoiceDesign），风格指令自动并入音色描述
- ⚙️ 新增 `design_voice` 配置项，测试脚本与配置模板同步支持 design 模式

### v1.7.3

- ✂️ **移除多语言与 LLM 翻译**：取消日语谐音拟合与 AI 翻译，语音按原文（中文）直接朗读
- 💬 **/tts 命令简化**：只保留 `说话内容+语气` 两段参数，默认发回给输入命令的人

### v1.7.2

- 🌏 **日语谐音拟合**：日文文本先转读音再映射为读音相近的中文字（如 `こんにちは` → `阔恩妮七哈`），交给 MiMo 用参考音色朗读出近似日语
- ✂️ **移除其它多语言支持**：仅保留中文（原样朗读）与日语（谐音拟合），英语/韩语等不再支持

### v1.7.1

- 🐛 **能力授权修复**：插件清单补充 `llm.generate`、`llm.get_available_models` 与 `chat.*` 会话解析能力声明，修复翻译与 `/tts` 目标解析被 Host 拒绝的问题

### v1.7.0

- 🐧 **QQ 官方机器人支持**：新增 `voice_adapter` 配置，可切换 `qq_official`（官方机器人）与 `napcat`（OneBot v11）两种语音发送方式
- 🔊 **SILK 转换**：官方机器人语音自动转码为腾讯 silk 格式后上传，适配 QQ 官方富媒体消息
- 📦 **FFmpeg 自动下载**：系统无 FFmpeg 时自动从受信 HTTPS 源下载静态构建到插件 `bin/` 目录
- 🐛 **错误暴露修复**：发送失败不再静默返回成功，插件日志会记录真实发送结果
- 🗂️ **语音本地存档**：生成的 wav 音频自动存档到 `output/` 目录（silk 仅用于发送，不再存档）
- 💬 **/tts 命令**：向指定的官方 OpenID 或 OneBot QQ 号手动发送语音，省略目标时默认私聊发给发送者，支持 `--lang` 临时指定语言

### v1.6.0

- 🔧 **工具链暴露**：`send_voice_reply` 工具暴露在 AI 工具链中，AI 可自主决定何时使用语音回复
- 🎬 **AI 智能风格控制**：风格指令由 AI 根据角色人设和对话情境自行决定，支持自然语言指令、导演模式、音频标签三种方式
- 🎨 **style_instruction 可选化**：AI 自行判断何时需要风格指令，无需每次填写
- 📋 **配置自动初始化**：首次启动自动从 `config.example.toml` 生成 `config.toml`

### v1.0.0

- 初始版本：预置音色、音色复刻、基础语音回复功能

## 注意事项

1. **API Key**：需要在 [小米 MiMo 平台](https://platform.xiaomimimo.com) 申请
2. **音色复刻**：建议录音时长不低于 30 秒，确保无背景噪音
3. **Base64 大小**：音色参考音频的 Base64 编码不能超过 10MB
4. **费用**：MiMo-V2.5-TTS 系列当前限时免费

## 许可证

MIT License
