# QQ DeepSeek Bot

一个**通过 NapCat 接入 QQ 消息**的机器人,由本地 Python 脚本驱动。

- **文字对话**:DeepSeek 大模型(`deepseek-flash`),默认开启思考模式
- **图片识别**:阿里云通义千问视觉模型(`qwen3.7-flash`)
- 两个模型均可通过 `config.json` 换成任意 OpenAI 兼容接口

> ⚠️ 本项目基于 MIT 许可证开源。接入 QQ 属于非官方行为,请自行遵守 QQ 平台规范及 NapCat 使用条款,由此产生的一切风险与后果由使用者自行承担。

---

## ✨ 功能特性

| 功能 | 说明 |
|---|---|
| **私聊对话** | 白名单控制、可调回复概率 |
| **群聊互动** | @ 触发 / 关键词触发 / 活跃期连续对话 / 低频冒泡,防止刷屏 |
| **私聊长期记忆** | **仅私聊**。滚动窗口 + 事实库双层结构,超出窗口的早期对话会自动整理成"关于对方的事实"长期留存;不使用向量检索;每个好友独立;支持"清空记忆"彻底删除 |
| **图片与表情包识别** | 自动描述图片内容、提取图中文字、解释表情包梗,并以**文字形式**存入记忆(省 token 且可读) |
| **主动打招呼** | 空闲时随机发起开场白,私聊/群聊可分别配置;带**聊天避让**(最近在对话就不打扰) |
| **掉线自愈** | 账号被踢下线或 QQ 进程退出时,**自动无黑窗快速登录**把机器人拉回线上 |
| **消息送达保障** | 生成前检查在线状态(省 token);发送后等回执,**没送达就不写记忆** |
| **人设系统** | `persona.txt` 独立维护角色设定,支持 `{bot_name}` 占位符 |
| **静音时段** | 可配置北京时间凌晨时段不主动打扰 |
| **调试模式** | `python bot.py --debug` 在终端测试人设与回复,不连 QQ |

## 🏗️ 工作原理

```
用户消息 ──> QQ ──> NapCat(正向 WebSocket) ──> bot.py
                                                  ├── 文本 -> DeepSeek API -> 回复
                                                  └── 图片 -> 通义千问 VL API -> 图片描述
bot.py ──> NapCat ──> QQ ──> 用户收到回复
```

- **NapCat** 负责与 QQ 协议交互,以正向 WebSocket(默认 `ws://127.0.0.1:3001`)推送事件给 `bot.py`
- **bot.py** 只做两件事:把消息加工后交给大模型,再把回复发回 NapCat
- NapCat 是**注入进本机 QQ 客户端**运行的(`launcher` 启动 `QQ.exe` 并注入 DLL),所以"让 QQ 起来"就等于"让 NapCat 回来" —— 掉线自愈正是利用这一点

## 📦 环境要求与部署

环境:Python 3.9+ · 一个 QQ 号(建议小号) · [NapCat](https://github.com/NapNeko/NapCatQQ) · DeepSeek 与阿里云 DashScope 的 API Key

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 首次登录 NapCat(**每个新号只需做一次**)

参考 [NapCatQQ 文档](https://github.com/NapNeko/NapCatQQ) 安装,在网络配置里开启 **WebSocket 服务端**(正向 WebSocket,默认端口 `3001`),然后运行 NapCat 的 launcher **扫码登录一次**,确认 `ws://127.0.0.1:3001` 可用。

> 这一步建立了本机登录凭证,之后才能使用"快速登录"(掉线自愈依赖它)。

### 3. 配置密钥

```bash
cp .env.example .env
```

```ini
TEXT_API_KEY=你的DeepSeek密钥
VISION_API_KEY=你的阿里云DashScope密钥
```

### 4. 配置 config.json

```bash
cp config.example.json config.json
```

至少修改:`bot_qq`(机器人 QQ 号)、`private_whitelist`(允许私聊的 QQ 号)、`group_whitelist`(允许发言的群号)。

### 5. 配置人设

```bash
cp persona.example.txt persona.txt
```

`{bot_name}` 会被替换为 `bot_name` 的值。

### 6. (可选)配置掉线自愈

1. 完成第 2 步的授权登录(建立凭证)
2. 准备快速登录脚本 `napcat-autologin.bat` 放在 `bot.py` 同目录(模板见 [掉线自愈](#-掉线自愈自动快速登录))

> ✅ 配置完成后,**日常只需运行 `python bot.py` 就能一键唤起机器人** —— 即使 NapCat 没在运行,程序也会自动快速登录把 QQ 和 NapCat 一起拉起来。

### 7. 启动

```bash
python bot.py            # 正常启动
python bot.py --debug    # 调试模式(不连 QQ,直接测人设与回复)
```

看到 `已连接 NapCat,机器人上线喵~` 即成功。


## ⚙️ 配置详解(config.json)

### 基础

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `bot_name` | `小深` | 机器人名字,替换人设中的 `{bot_name}` |
| `persona_file` | `persona.txt` | 人设文件路径(也可用 `persona` 字段内联) |
| `ws_url` | `ws://127.0.0.1:3001` | NapCat 正向 WebSocket 地址 |
| `bot_qq` | — | 机器人 QQ 号 |
| `private_whitelist` | `[]` | 私聊白名单 |
| `group_whitelist` | `[]` | 群聊白名单 |
| `reply_probability` | `0.7` | 私聊回复概率 |
| `fallback_reply` | `喵……刚才网络开小差了，再说一次好不好？` | 模型调用失败时的兜底回复 |
| `clear_memory_reply` | `喵~ 记忆已经清空啦，我们重新开始吧！` | 执行"清空记忆"后的回复 |

### 模型

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `text_base_url` | `https://api.deepseek.com` | 文本模型接口地址 |
| `text_model` | `deepseek-flash` | 文本模型名 |
| `enable_thinking` | `true` | 是否开启思考模式(见 [思考模式](#-思考模式enable_thinking)) |
| `vision_base_url` | `https://dashscope.aliyuncs.com/compatible-mode/v1` | 视觉模型接口地址 |
| `vision_model` | `qwen3.7-flash` | 视觉模型名 |
| `max_images_per_message` | `3` | 单条消息最多识别几张图 |

### 群聊

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `group_at_only` | `true` | 是否仅在被 @ 时回复 |
| `group_reply_probability` | `0.8` | 被 @ 时的回复概率 |
| `group_keyword_probability` | `0.7` | 命中关键词时的回复概率 |
| `group_active_probability` | `0.6` | 活跃期内的回复概率 |
| `group_default_probability` | `0.1` | 默认(潜水)时的回复概率 |
| `group_active_window` | `600` | 活跃期时长(秒) |
| `group_max_consecutive_replies` | `5` | 活跃期内最多连续回复条数 |
| `keywords` | `["小深", "猫娘"]` | 触发回复的关键词 |

### 主动消息与静音时段

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `proactive_interval_private` | `[1800, 7200]` | 私聊主动消息间隔范围(秒),随机取值 |
| `proactive_interval_group` | `[10800, 21600]` | 群聊主动消息间隔范围(秒) |
| `proactive_to_each` | `0.5` | 每轮对每个对象发起主动消息的概率 |
| `proactive_quiet_private` | `60` | 私聊静默期(秒):最近这么久内聊过就不主动打扰 |
| `proactive_quiet_group` | `300` | 群聊静默期(秒) |
| `enable_silent_hours` | `true` | 是否启用静音时段 |
| `silent_hours_start` / `silent_hours_end` | `0` / `10` | 静音时段起止(小时,北京时间) |

### 记忆

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `memory_max_messages` | `40` | **群聊**保留的最大消息条数(私聊由长期记忆接管) |
| `memory_file` | `memory.json` | 对话记忆文件(自动生成,勿手动编辑) |
| `long_memory_enabled` | `true` | 是否启用私聊长期记忆 |
| `lm_file` | `memory_long.json` | 长期记忆事实库文件(自动生成) |
| `lm_l0_max` | `160` | 私聊对话达到多少条时触发一次整理 |
| `lm_compress_count` | `80` | 每次整理掉最早多少条(须为偶数,且 ≤ `lm_l0_max` 的一半) |
| `lm_compress_delay` | `3` | 触发后延迟几秒再整理(合并连续消息,避开回复请求) |
| `lm_l1_target_chars` | `1000` | 整理后希望事实库控制在多少字以内(只数事实正文) |
| `lm_l1_accept_chars` | `1500` | 超过此字数才打告警日志(留的余量) |
| `lm_l1_inject_chars` | `800` | 每轮最多把多少字记忆注入对话(建议调到 `1200`) |
| `lm_thinking` | `true` | 整理记忆时是否开启思考(建议开启) |
| `lm_max_tokens` | `16000` | 整理调用的输出上限(开启思考时推理也占额度,**勿调太低**) |

### 掉线自愈

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `auto_relogin` | `true` | 账号掉线时是否自动快速登录 |
| `qq_client_path` | `D:\Tencent\QQNT\QQ.exe` | 本机 QQ 客户端路径 |
| `autologin_script` | `napcat-autologin.bat` | 快速登录脚本(相对路径则相对 bot.py 所在目录) |
| `health_check_interval` | `30` | 在线巡检间隔(秒) |
| `relogin_wait_seconds` | `180` | 触发重登后等待上线的最长时间(秒) |
| `relogin_max_per_hour` | `2` | 每小时最多自动重登次数 |
| `kill_qq_on_exit` | `true` | 停止 bot.py 时是否一并结束 QQ / NapCat 进程(见下方说明) |
| `qrcode_image` | `D:\tools\NapCat\cache\qrcode.png` | 需要扫码时使用的二维码图片路径 |
| `qrconsole_log` | `napcat-autologin.log` | 快速登录脚本的输出日志(用于提取二维码链接) |

## 🧩 核心机制

### 💭 思考模式(`enable_thinking`)

`deepseek-flash` 默认开启思考模式:回复前先输出思维链,回答更周到自然,代价是**每轮多花约 70~120 个推理 token**。

⚠️ **思考模式下 `temperature` / `presence_penalty` / `frequency_penalty` 不生效** —— 传了不报错,但会被忽略。所以项目里的 `temperature=1.3` 只在 `enable_thinking: false` 时才真正起作用。

记忆整理由 `lm_thinking` 单独控制(默认跟随)。**建议保持开启**:实测开启思考后,整理能正确区分"已撤销/已放弃"与"仍有效"的事件,也不会漏掉生日、家人健康这类重要信息。

> ⚠️ 开启思考时必须给足 `lm_max_tokens`(默认 16000):推理 token 也计入该额度,设得太小(实测 8000)会导致推理吃光额度、返回空内容,整理直接失败。

### 🗃️ 长期记忆(仅私聊)

分两层:

| 层 | 存储 | 内容 |
|---|---|---|
| **L0 对话窗口** | `memory.json` | 每个好友最近的原始对话(带时间戳),默认保留到 160 条 |
| **L1 事实库** | `memory_long.json` | 从对话中提炼的"关于对方的事实",按类型分组长期留存 |

**流程:**

```
聊天中 → 消息持续写入 L0
      → L0 达到 lm_l0_max(默认 160 条)
      → 后台延迟 lm_compress_delay 秒,把最早的 80 条与现有事实库交给模型重新整理
      → 先写回事实库,再从 L0 删掉这 80 条(最坏只是重复整理,不会丢消息)
```

**设计取舍:**

- **不使用向量检索**:让模型在**压缩时**就把信息提炼成独立事实,并合并去重、处理状态变化(如"从杭州搬到上海"会替换掉旧的居住地)
- **只喂用户消息**:机器人的回复不参与整理,避免把自己现编的内容当成对方的事实
- **注入按类型截断**:优先保留"基本信息/重要关系",过期事件自动过滤
- **不记录关于自己身份的对话**:涉及"你是不是 AI/程序"的话题一律跳过,避免记忆反过来破坏人设
- **触发只看条数**:整理次数 ≈ 累计消息条数 ÷ `lm_compress_count`,费用可预期

**两个字数口径不同,别混淆:**

| 参数 | 口径 |
|---|---|
| `lm_l1_target_chars` | **只数事实正文**(`facts[].c`),不含 JSON 键名等结构开销。实际文件大小约为它的 10 倍 |
| `lm_l1_inject_chars` | **数注入块**,含类型标题、`- ` 前缀、换行、敏感提示,约为正文的 2 倍 |

所以 `lm_l1_target_chars: 1000` 的事实,注入满约 1200 字符 —— 想让 L1 里的东西都能进对话,建议把 `lm_l1_inject_chars` 设为 **1200**。

### 🙋 主动消息的"聊天避让"

主动消息到点时如果**刚好还在聊天**,直接冒一句开场白会明显出戏。程序为此记录每个会话的最后活动时间:

- 距上次对话不足 `proactive_quiet_private`(私聊 60 秒)/ `proactive_quiet_group`(群聊 300 秒)→ **跳过本次**
- 按会话**独立判断**:A 正在聊天不影响给 B 发主动消息
- 群聊的"正在聊天"按**任何群消息**计算(不只是 @ 或需要回复的)
- 两个值设为 `0` 可关闭该机制

### 🔌 掉线自愈(自动快速登录)

账号被踢下线、或 QQ 进程意外退出时,机器人会**自动把它拉回线上**。

**两个触发点:**

| 检测方式 | 触发时机 | 覆盖的情况 |
|---|---|---|
| 主连接断开 | WebSocket 一断即触发(约 5 秒) | QQ 进程死了 / NapCat 崩了 |
| 定期巡检 | 每 `health_check_interval` 秒 | 连接还在、但**账号已离线**(被踢) |

**两种处理方式:**

| 情况 | 判断依据 | 处理 |
|---|---|---|
| QQ 进程已死 | 端口 3001 未监听 | 直接执行快速登录拉起 QQ(同时带回 NapCat) |
| 账号被踢下线 | 端口在监听,但连接被拒 / `get_status.online=false` | 先结束 QQ 进程(否则 launcher 不会真正重新注入),再快速登录 |

**实测效果**:从 `QQ.exe=0`、端口未监听的完全离线状态,到自动恢复上线约 **19 秒**,全程**不弹出黑窗口**。

**快速登录脚本 `napcat-autologin.bat`:**

内容等价于 NapCat 自带的 `launcher-user.bat`,只是去掉末尾的 `pause`(否则自动调用会一直挂住),也不需要管理员权限:

```bat
@echo off
rem 路径需按你的环境修改：<NapCat目录>、<QQ安装目录>
cd /d <NapCat目录>
set NAPCAT_PATCH_PACKAGE=<NapCat目录>\qqnt.json
set NAPCAT_LOAD_PATH=<NapCat目录>\loadNapCat.js
set NAPCAT_INJECT_PATH=<NapCat目录>\NapCatWinBootHook.dll
set NAPCAT_LAUNCHER_PATH=<NapCat目录>\NapCatWinBootMain.exe
set NAPCAT_MAIN_PATH=<NapCat目录>\napcat.mjs
echo (async () =^> {await import("file:///<NapCat目录>/napcat.mjs")})() > "<NapCat目录>\loadNapCat.js"
"<NapCat目录>\NapCatWinBootMain.exe" "<QQ安装目录>\QQ.exe" "<NapCat目录>\NapCatWinBootHook.dll" <机器人QQ号>
exit /b 0
```

> ⚠️ 三处占位符都要替换:`<NapCat目录>`(如 `D:\tools\NapCat`)、`<QQ安装目录>`(如 `D:\Tencent\QQNT`)、`<机器人QQ号>`。QQ 号传给 `NapCatWinBootMain.exe` 即触发**快速登录**。

**快速登录失败时(需要扫码):**

本机凭证失效时(例如在别处登录过同一个号),快速登录会被要求扫码。程序会把二维码渲染成网页并**自动用浏览器打开**(内嵌图片,附解码链接兜底),然后在终端询问:

```
  [Y/回车] 先手动登录一次建立凭证，让「自动快速登录」以后能继续用
            （会结束 NapCat/QQ 进程并停止 bot.py）
            ⚠️ 若你平时不用 QQ 客户端，选这项可能让机器人再也无法自动上线
  [N]      就现在扫上面这个二维码登录（需要人工点授权，不支持无人值守）
```

- 选 **N**:直接扫码,成功后 bot 自动继续运行
- 选 **Y(或回车)**:结束 NapCat/QQ 进程并停止 bot,你手动登录 QQ 客户端重建凭证,之后重新运行 `bot.py` 即恢复无人值守

**安全边界:** 需要扫码时无法自动恢复(账号侧限制);每小时最多重登 `relogin_max_per_hour` 次,失败按 1/5/15 分钟退避,避免"重启→被踢→再重启"死循环;设 `auto_relogin: false` 可关闭。

### 🛑 安全停止

直接关掉 bot.py 的话,**NapCat 和 QQ 会继续留在后台**跑(占内存、占着登录状态)。所以停止时会自动清理:

- **Ctrl+C** 停止 → 通过 `atexit` 钩子结束 `QQ.exe` 与 `NapCatWinBootMain.exe`
- **扫码流程选 Y** → 无条件清理(因为就是要手动重建凭证)
- 日志会打印结束了几个进程
- 不想让它动 QQ 客户端?把 `kill_qq_on_exit` 设为 `false`

下次运行 `python bot.py` 时会自动快速登录重新拉起,无需手动准备。

### 📬 消息送达保障

账号掉线时如果照常生成回复,会有两个后果:白烧 API token,以及**把没送出去的话写进记忆**(模型后续会以为自己说过并等过回应,导致对话错位)。为此有两道防线:

**① 生成前在线检查** —— 每次生成消息前先问一次 NapCat"账号还在线吗":

- 复用已有连接查询,实测单次约 **0.6ms**,对回复速度无可感知影响
- 离线则**直接跳过,不调用模型** —— 省 token,也不写入任何记忆
- 日志会记录:`账号离线，跳过本次私聊回复 xxx（不调用模型，不写入记忆）`

**② 送达确认** —— 发送改用带 `echo` 的请求-响应模式,**只有拿到 `message_id` 才算送达**:

- 送达成功 → 写入对话记忆
- 送达失败 → **不写记忆**,并记录 `私聊发送失败 ... 回执={}` 错误日志

> 分工:生成前检查拦住"已知离线"(省 token);送达确认兜住"检查通过后、发送那一刻才失败"的漏网情况(防污染)。

## 🧠 内置指令

| 指令 | 效果 |
|---|---|
| `清空记忆` | 彻底清空当前会话:私聊会**同时删除**对话窗口与长期事实库;群聊清空该群上下文 |

## ❓ 常见问题

**Q: 启动报 `缺少 API Key!请在 .env 中配置`**
A: 确认已创建 `.env` 并填好两个 Key;确认当前目录就是项目根目录。

**Q: 日志一直显示连接断开/重连**
A: 确认 NapCat 已开启 WebSocket 服务端(正向)且端口与 `ws_url` 一致(默认 `3001`);若已配置掉线自愈,程序会尝试自动拉起,失败时看日志提示。

**Q: 收到图片不识别或显示"图片加载失败"**
A: 程序通过 NapCat 的 `get_file`/`get_image` API 读取图片**本地缓存**再交给视觉模型,不依赖 QQ 图片链接。若仍失败,检查 NapCat 是否正常运行、图片是否已缓存。

**Q: 图片为什么先转成文字,而不是直接把图片存进记忆?**
A: (1) **省 token** —— 一张图若按原图塞进后续每轮对话最多占 1024 个图片 token,文字描述通常只要几十个;(2) **可读** —— 记忆文件里是"【图片】一只橘猫趴在键盘上",而不是一长串无法直读的 base64;(3) **可整理** —— 长期记忆压缩时文字描述能被直接理解并提炼成事实。

**Q: 群聊里机器人不回复**
A: 确认群号在白名单、`group_at_only` 与回复概率符合预期;概率机制下部分消息会故意不回复,这是特性不是 Bug。

**Q: 回复开头带 `[时间戳]` 或 `小深:` 前缀**
A: 程序已内置前缀清理逻辑;若仍出现,多为模型偶发输出,可忽略或调整人设描述。

**Q: 长期记忆什么时候触发?会不会很费钱?**
A: 只有私聊、且对话累积到 `lm_l0_max`(默认 160 条)时才整理一次,**整理次数 ≈ 累计消息条数 ÷ `lm_compress_count`**,与聊天快慢无关。整理只把"新片段 + 现有事实库"发给模型,不重发全部历史。想更省可调大 `lm_l0_max`、调小 `lm_l1_inject_chars`,或把 `lm_thinking` 设为 `false`(但会漏记重要信息)。

**Q: 说了"清空记忆"以后,机器人还会记得以前的事吗?**
A: 不会。私聊的"清空记忆"会**同时**删除对话窗口(`memory.json`)和长期事实库(`memory_long.json`)里该好友的全部内容,并作废进行中的整理任务。

**Q: 开了思考模式后,调 `temperature` 好像没反应?**
A: 这是官方行为:**思考模式下 `temperature`、`presence_penalty`、`frequency_penalty` 都不生效**。想真正用这些采样参数,把 `enable_thinking` 设为 `false`。

## 🔒 隐私与安全

- 密钥存放在 `.env`(`TEXT_API_KEY` / `VISION_API_KEY`),已在 `.gitignore` 中排除,**切勿提交**
- 实际配置 `config.json`、人设 `persona.txt` / `me.txt`、记忆 `memory.json` 与 `memory_long.json`、快速登录脚本 `napcat-autologin.bat` 及其日志,均含个人/隐私信息,**已全部加入 `.gitignore`**
- ⚠️ 启用长期记忆后,私聊内容会被**长期留存**并整理成事实库(不再是只留最近几十条)。请确认对方知情且同意,必要时用"清空记忆"彻底删除
- 建议给机器人使用小号,并在白名单中严格控制可对话对象
- **不要用 `git add -f`**:`-f` 会无视 `.gitignore` 把上述隐私文件提交上去

## 📁 目录结构

```
qq-deepseek-bot/
├── bot.py                    # 主程序(唯一入口)
├── config.example.json       # 配置示例(复制为 config.json)
├── config.json               # 实际配置(本地,已被 gitignore)
├── .env.example              # 密钥示例(复制为 .env)
├── .env                      # 实际密钥(本地,已被 gitignore)
├── persona.example.txt       # 人设示例(复制为 persona.txt)
├── persona.txt / me.txt      # 实际人设(本地,已被 gitignore)
├── memory.json               # 对话窗口记忆(自动生成,已被 gitignore)
├── memory_long.json          # 私聊长期记忆事实库(自动生成,已被 gitignore)
├── napcat-autologin.bat      # 快速登录脚本(本机专属,已被 gitignore)
├── requirements.txt          # Python 依赖
└── README.md
```

## 📚 依赖

- [websockets](https://pypi.org/project/websockets/) — 连接 NapCat WebSocket
- [openai](https://pypi.org/project/openai/) — 调用 DeepSeek / 通义千问(OpenAI 兼容接口)
- [python-dotenv](https://pypi.org/project/python-dotenv/) — 读取 `.env`

## 🙏 致谢

- [NapCatQQ](https://github.com/NapNeko/NapCatQQ) — QQ 协议接入
- [DeepSeek](https://www.deepseek.com/) — 文本大模型
- [阿里云百炼(通义千问)](https://bailian.console.aliyun.com/) — 视觉大模型

## 📄 许可证

本项目基于 [MIT License](LICENSE) 开源。

Copyright (c) 2026 ruoqian-0205
