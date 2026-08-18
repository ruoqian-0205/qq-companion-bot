# QQ DeepSeek Bot

一个基于 **NapCat** 正向 WebSocket 的 QQ 机器人,由本地 Python 脚本驱动。

- **文字对话**:DeepSeek 纯文本大模型(`deepseek-v4-flash`)
- **图片识别**:阿里云通义千问视觉模型(`qwen3.7-flash`)
- 两个模型均可通过 `config.json` 一键更换为任意 OpenAI 兼容接口

只需运行 NapCat 正向代理 + 本地运行 `bot.py`,即可让 QQ 号上线成为会聊天的 AI 机器人。

> ⚠️ 本项目基于 MIT 许可证开源,可自由使用、修改与二次开发。接入 QQ 属于非官方行为,请自行遵守 QQ 平台规范及 NapCat 项目的使用条款,由此产生的一切风险与后果由使用者自行承担。

---

## ✨ 功能特性

- **私聊对话**:白名单控制、可调回复概率、主动发起话题(可配置间隔)
- **群聊互动**:支持 @ 触发 / 关键词触发 / 活跃期连续对话 / 低频冒泡,防止刷屏
- **图片与表情包识别**:自动调用视觉模型描述图片内容、提取图中文字、解释表情包梗
- **长期记忆**:基于 JSON 文件保存对话上下文(每个好友/群独立),支持"清空记忆"指令
- **人设系统**:`persona.txt` 独立维护角色设定,支持 `{bot_name}` 占位符,换人设零成本
- **主动打招呼**:空闲时随机发起开场白,私聊/群聊可分别配置间隔
- **静音时段**:可配置北京时间凌晨时段不主动打扰
- **调试模式**:`python bot.py --debug` 在终端直接测试人设与回复,不连 QQ

## 🏗️ 工作原理

```
用户消息 ──> QQ ──> NapCat(正向 WebSocket) ──> bot.py
                                                  ├── 文本 -> DeepSeek API -> 回复
                                                  └── 图片 -> 通义千问 VL API -> 图片描述
bot.py ──> NapCat ──> QQ ──> 用户收到回复
```

- **NapCat** 负责与 QQ 协议的交互,以正向 WebSocket(默认 `ws://127.0.0.1:3001`)推送事件给 `bot.py`
- **bot.py** 只做两件事:把消息加工后交给大模型,再把回复发回 NapCat

## 📦 环境要求

- Python 3.9+
- 一个可登录的 QQ 号(建议小号)
- [NapCat](https://github.com/NapNeko/NapCatQQ)(napcat 本体,独立运行)
- DeepSeek 与阿里云 DashScope(通义千问)的 API Key（也可以根据需要换用其他平台的大模型API）

## 🚀 快速开始

### 1. 克隆与安装依赖

```bash
git clone https://github.com/ruoqian-0205/qq-deepseek-bot.git
cd qq-deepseek-bot
pip install -r requirements.txt
```

### 2. 运行 NapCat 正向代理

参考 [NapCatQQ](https://github.com/NapNeko/NapCatQQ) 官方文档完成安装与登录,并开启**正向 WebSocket**(默认端口 `3001`,与 `config.json` 中的 `ws_url` 保持一致)。

### 3. 配置密钥(.env)

```bash
cp .env.example .env
```

编辑 `.env`,填入两个 API Key:

```ini
TEXT_API_KEY=你的DeepSeek密钥
VISION_API_KEY=你的阿里云DashScope密钥
```

> `VISION_API_KEY` 在 [阿里云百炼控制台](https://bailian.console.aliyun.com/) 获取;`TEXT_API_KEY` 在 [DeepSeek 开放平台](https://platform.deepseek.com/) 获取。

### 4. 配置文件(config.json)

```bash
cp config.example.json config.json
```

至少需要修改:

| 配置项                 | 说明                              |
|---------------------|---------------------------------|
| `bot_qq`            | 机器人的 QQ 号                       |
| `private_whitelist` | 允许私聊的 QQ 号数组,如 `[10001, 10002]` |
| `group_whitelist`   | 允许机器人发言的群号数组,如 `[123456789]`    |

### 5. 配置人设(persona.txt)

```bash
cp persona.example.txt persona.txt
```

`persona.example.txt` 是一份"猫娘少女"示例人设,可按喜好修改;文件中的 `{bot_name}` 会被自动替换为 `config.json` 中的 `bot_name`。

### 6. 启动

```bash
python bot.py
```

看到日志 `已连接 NapCat,机器人上线喵~` 即成功。调试模式(不连 QQ,直接在终端测试人设):

```bash
python bot.py --debug
```

## ⚙️ 配置详解(config.json)

| 配置项                             | 默认值                                                 | 说明                           |
|---------------------------------|-----------------------------------------------------|------------------------------|
| `bot_name`                      | `小深`                                                | 机器人名字,会替换人设中的 `{bot_name}`   |
| `persona_file`                  | `persona.txt`                                       | 人设文件路径(也可直接用 `persona` 字段内联) |
| `ws_url`                        | `ws://127.0.0.1:3001`                               | NapCat 正向 WebSocket 地址       |
| `text_base_url`                 | `https://api.deepseek.com`                          | 文本模型接口地址(OpenAI 兼容)          |
| `text_model`                    | `deepseek-v4-flash`                                 | 文本模型名,可换成其他模型                |
| `vision_base_url`               | `https://dashscope.aliyuncs.com/compatible-mode/v1` | 视觉模型接口地址(OpenAI 兼容)          |
| `vision_model`                  | `qwen3.7-flash`                                     | 视觉模型名,可换成 `qwen-vl-plus` 等   |
| `max_images_per_message`        | `3`                                                 | 单条消息最多识别几张图,超出部分不识别          |
| `bot_qq`                        | —                                                   | 机器人 QQ 号                     |
| `private_whitelist`             | `[]`                                                | 私聊白名单                        |
| `group_whitelist`               | `[]`                                                | 群聊白名单                        |
| `group_at_only`                 | `true`                                              | 群聊是否仅在被 @ 时回复                |
| `group_reply_probability`       | `0.8`                                               | 被 @ 时的回复概率                   |
| `group_keyword_probability`     | `0.7`                                               | 命中关键词时的回复概率                  |
| `group_active_probability`      | `0.6`                                               | 活跃期内(刚聊过)的回复概率               |
| `group_default_probability`     | `0.1`                                               | 默认(潜水)时的回复概率                 |
| `group_active_window`           | `600`                                               | 活跃期时长(秒),机器人发过消息后计入活跃        |
| `group_max_consecutive_replies` | `5`                                                 | 活跃期内最多连续回复条数,防止刷屏            |
| `keywords`                      | `["小深", "猫娘"]`                                      | 触发回复的关键词                     |
| `reply_probability`             | `0.7`                                               | 私聊回复概率                       |
| `fallback_reply`                | `喵……刚才网络开小差了，再说一次好不好？`                              | 模型调用失败时的兜底回复                 |
| `clear_memory_reply`            | `喵~ 记忆已经清空啦，我们重新开始吧！`                               | 执行"清空记忆"指令后的回复               |
| `proactive_interval_private`    | `[1800, 7200]`                                      | 私聊主动开场白间隔范围(秒),随机取值          |
| `proactive_interval_group`      | `[10800, 21600]`                                    | 群聊主动开场白间隔范围(秒)               |
| `proactive_to_each`             | `0.5`                                               | 每轮主动消息中,对每个对象发起概率            |
| `memory_max_messages`           | `40`                                                | 每个会话保留的最大消息条数                |
| `memory_file`                   | `memory.json`                                       | 记忆文件路径(自动生成,勿手动编辑)           |
| `enable_silent_hours`           | `true`                                              | 是否启用静音时段                     |
| `silent_hours_start`            | `0`                                                 | 静音时段开始(小时,北京时间)              |
| `silent_hours_end`              | `10`                                                | 静音时段结束(小时,北京时间)              |

## 🧠 内置指令

| 指令     | 效果                         |
|--------|----------------------------|
| `清空记忆` | 清空当前会话(私聊:该好友;群聊:该群)的上下文记忆 |

## 🔒 隐私与安全

- 所有密钥存放在 `.env`(`TEXT_API_KEY` / `VISION_API_KEY`),已在 `.gitignore` 中排除,**切勿提交**
- 实际配置 `config.json`、人设 `persona.txt`、记忆 `memory.json` 均含个人/隐私信息,已加入 `.gitignore`,**不会推送到仓库**
- 公开仓库只包含示例文件(`config.example.json`、`persona.example.txt`、`.env.example`)
- 建议给机器人使用小号,并在 `whitelist` 中严格控制可对话对象

## 📁 目录结构

```
qq-deepseek-bot/
├── bot.py                  # 主程序(唯一入口)
├── config.example.json     # 配置示例(复制为 config.json)
├── config.json             # 实际配置(本地,已被 gitignore)
├── .env.example            # 密钥示例(复制为 .env)
├── .env                    # 实际密钥(本地,已被 gitignore)
├── persona.example.txt     # 人设示例(复制为 persona.txt)
├── persona.txt             # 实际人设(本地,已被 gitignore)
├── memory.json             # 记忆文件(自动生成,已被 gitignore)
├── requirements.txt        # Python 依赖
└── README.md
```

## ❓ 常见问题

**Q: 启动报 `缺少 API Key!请在 .env 中配置`**
A: 确认已创建 `.env` 并填写两个 Key;确认当前目录就是项目根目录。

**Q: 日志一直显示连接断开/重连**
A: 确认 NapCat 已开启正向 WebSocket,且端口与 `ws_url` 一致(默认 `3001`);检查防火墙是否放行。

**Q: 收到图片不识别或显示"图片加载失败"**
A: 视觉模型需要能访问图片 URL。若 QQ 图片链接无法访问(如内网/区域限制),可尝试更换 NapCat 的图片下载/上报配置。

**Q: 群聊里机器人不回复**
A: 确认群号在白名单、`group_at_only` 与回复概率符合预期;概率机制下部分消息会故意不回复,这是特性不是 Bug。

**Q: 回复开头带 `[时间戳]` 或 `小深:` 前缀**
A: 程序已内置前缀清理逻辑;若仍出现,多为模型偶发输出,可忽略或调整人设描述。

## 📄 依赖

- [websockets](https://pypi.org/project/websockets/) — 连接 NapCat WebSocket
- [openai](https://pypi.org/project/openai/) — 调用 DeepSeek / 通义千问(OpenAI 兼容接口)
- [python-dotenv](https://pypi.org/project/python-dotenv/) — 读取 `.env`

## 🙏 致谢

- [NapCatQQ](https://github.com/NapNeko/NapCatQQ) — 提供 QQ 协议接入能力
- [DeepSeek](https://www.deepseek.com/) — 文本大模型
- [阿里云百炼(通义千问)](https://bailian.console.aliyun.com/) — 视觉大模型

## 📄 许可证

本项目基于 [MIT License](LICENSE) 开源。

Copyright (c) 2026 ruoqian-0205
