# QQ DeepSeek Bot

一个**通过 NapCat 接入 QQ 消息**的机器人,由本地 Python 脚本驱动。

- **文字对话**:DeepSeek 大模型(`deepseek-flash`),默认开启思考模式
- **图片识别**:阿里云通义千问视觉模型(`qwen3.7-flash`)
- 两个模型均可通过 `config.json` 一键更换为任意 OpenAI 兼容接口

只需运行 NapCat(开启 **WebSocket 服务端**,即社区常说的"正向 WebSocket")+ 本地运行 `bot.py`,即可让 QQ 号上线成为会聊天的 AI 机器人。

> ⚠️ 本项目基于 MIT 许可证开源,可自由使用、修改与二次开发。接入 QQ 属于非官方行为,请自行遵守 QQ 平台规范及 NapCat 项目的使用条款,由此产生的一切风险与后果由使用者自行承担。

---

## ✨ 功能特性

- **私聊对话**:白名单控制、可调回复概率、主动发起话题(可配置间隔)
- **群聊互动**:支持 @ 触发 / 关键词触发 / 活跃期连续对话 / 低频冒泡,防止刷屏
- **私聊长期记忆**:**仅私聊启用**。滚动窗口 + 事实库双层结构,超出窗口的早期对话会被自动整理成"关于对方的事实"长期留存,不依赖向量检索;每个好友独立,支持"清空记忆"彻底删除
- **图片与表情包识别**:自动调用视觉模型描述图片内容、提取图中文字、解释表情包梗,并以文字形式存进对话记忆
- **人设系统**:`persona.txt` 独立维护角色设定,支持 `{bot_name}` 占位符,换人设零成本
- **主动打招呼**:空闲时随机发起开场白,私聊/群聊可分别配置间隔;带**聊天避让**——若最近还在对话中则跳过本次主动消息,不会出现"刚聊完又冒一句"
- **静音时段**:可配置北京时间凌晨时段不主动打扰
- **调试模式**:`python bot.py --debug` 在终端直接测试人设与回复,不连 QQ

## 🏗️ 工作原理

```
用户消息 ──> QQ ──> NapCat(正向 WebSocket) ──> bot.py
                                                  ├── 文本 -> DeepSeek API -> 回复
                                                  └── 图片 -> 通义千问 VL API -> 图片描述
bot.py ──> NapCat ──> QQ ──> 用户收到回复
```

- **NapCat** 负责与 QQ 协议的交互,以正向 WebSocket(默认 `ws://127.0.0.1:3001`)推送事件给 `bot.py`(**NapCat 为服务端,bot.py 为客户端,由 bot.py 主动连接**)
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

### 2. 运行 NapCat(开启正向 WebSocket)

参考 [NapCatQQ](https://github.com/NapNeko/NapCatQQ) 官方文档完成安装与登录,然后在 NapCat 的网络配置中开启 **WebSocket 服务端**(即"正向 WebSocket"),监听端口默认 `3001`。

这里的"正向"指连接方向:**NapCat 作为服务端监听端口,`bot.py` 作为客户端主动连接它**(即 `ws_url`)。与之相对的是"反向 WebSocket"(NapCat 主动连你的程序,常用于机器人跑在远程服务器的场景),本项目不需要,请勿混淆。

请确保 NapCat 监听的端口与 `config.json` 中的 `ws_url` 保持一致。

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
| `text_model`                    | `deepseek-flash`                                    | 文本模型名,可换成其他模型                |
| `enable_thinking`               | `true`                                              | 是否开启思考模式(见下方说明)              |
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
| `proactive_quiet_private`       | `60`                                                | 私聊静默期(秒):最近这么久内聊过天就不主动打扰     |
| `proactive_quiet_group`         | `300`                                               | 群聊静默期(秒):群里最近这么久内有人说话就不主动插话  |
| `memory_max_messages`           | `40`                                                | **群聊**保留的最大消息条数(私聊由长期记忆接管)   |
| `memory_file`                   | `memory.json`                                       | 对话记忆文件路径(自动生成,勿手动编辑)        |
| `long_memory_enabled`           | `true`                                              | 是否启用私聊长期记忆                   |
| `lm_file`                       | `memory_long.json`                                  | 长期记忆(事实库)文件路径(自动生成,勿手动编辑)   |
| `lm_l0_max`                     | `160`                                               | 私聊对话达到多少条时触发一次记忆整理           |
| `lm_compress_count`             | `80`                                                | 每次整理掉最早多少条(须为偶数,且 ≤ `lm_l0_max` 的一半) |
| `lm_compress_delay`             | `3`                                                 | 触发后延迟几秒再整理(合并连续消息,并避开回复请求)   |
| `lm_l1_target_chars`            | `1000`                                              | 整理后希望记忆库控制在多少字以内             |
| `lm_l1_accept_chars`            | `1500`                                              | 超过此字数才告警(给模型留的字数余量)          |
| `lm_l1_inject_chars`            | `800`                                               | 每轮最多把多少字记忆注入对话(超出部分留存在文件里)   |
| `lm_thinking`                   | `true`                                              | 整理记忆时是否开启思考(建议开启,见上方说明)         |
| `lm_max_tokens`                 | `16000`                                             | 整理调用的输出上限(开启思考时推理也占额度,勿调太低)    |
| `enable_silent_hours`           | `true`                                              | 是否启用静音时段                     |
| `silent_hours_start`            | `0`                                                 | 静音时段开始(小时,北京时间)              |
| `silent_hours_end`              | `10`                                                | 静音时段结束(小时,北京时间)              |
| `auto_relogin`                  | `true`                                              | 账号掉线时是否自动快速登录(见下方说明)         |
| `qq_client_path`                | `D:\Tencent\QQNT\QQ.exe`                            | 本机 QQ 客户端路径(快速登录需要)           |
| `autologin_script`              | `napcat-autologin.bat`                              | 快速登录脚本(相对路径则相对 bot.py 所在目录)    |
| `health_check_interval`         | `30`                                                | 在线巡检间隔(秒)                     |
| `relogin_wait_seconds`          | `180`                                               | 触发重登后等待上线的最长时间(秒)            |
| `relogin_max_per_hour`          | `2`                                                 | 每小时最多自动重登次数(防止被踢时无限重启)       |

## 📬 消息送达保障

账号掉线时如果照常生成回复,会有两个后果:白烧 API token(内容根本发不出去),以及**把没送出去的话写进记忆**——模型后续会以为自己说过并等过回应,导致对话错位。

为此有两道防线:

**① 生成前在线检查**

每次准备生成消息前(私聊回复、群聊回复、主动消息)先问一次 NapCat"账号还在线吗":

- 复用已有连接查询,实测单次约 **0.6ms**,对回复速度无可感知影响
- 离线则**直接跳过,不调用模型** —— 省下 token,也不写入任何记忆
- 日志会明确记录:`账号离线，跳过本次私聊回复 xxx（不调用模型，不写入记忆）`

**② 送达确认**

发送改用带 `echo` 的请求-响应模式,**只有拿到 NapCat 返回的 `message_id` 才算送达**:

- 送达成功 → 写入对话记忆(L0)
- 送达失败 → **不写记忆**,并记录 `私聊发送失败 ... 回执={}` 错误日志

改造前发送是单向的、失败完全静默,所以会出现"记忆里存着一条从未送达的消息"。现在这种情况不会再发生。

> 分工:生成前检查拦住"已知离线"的情况(省 token);送达确认兜住"检查通过后、发送那一刻才失败"的漏网情况(防污染)。

## 🙋 主动消息的"聊天避让"

主动消息到点时,如果**刚好还在和对方聊天**,直接冒一句开场白会很明显地出戏。为此程序会记录每个会话的最后活动时间,主动消息发出前先看一眼:

- 距上次对话不足 `proactive_quiet_private`(私聊默认 60 秒)/ `proactive_quiet_group`(群聊默认 300 秒)→ **跳过本次**,不打扰
- 按会话**独立判断**:A 正在聊天不影响给 B 发主动消息;某个群在热聊也不影响其他群
- 群聊的"正在聊天"按**任何群消息**计算(不只是 @ 或需要机器人回复的),因为群里确实有人在说话
- 长期不联系的会话不受影响 —— 活动时间是很久以前的,照常触发
- 两个值都设为 `0` 可关闭该机制,退回"到点就发"的行为

跳过时会打一条日志,方便你根据实际体感微调这两个阈值。

## 🔌 掉线自愈(自动快速登录)

QQ 账号被踢下线、或 QQ 进程意外退出时,机器人会**自动把它拉回线上**,无需人工干预。

**原理**:NapCat 是注入进本机 QQ 客户端的(`launcher` 启动 `QQ.exe` 再注入 DLL),所以"让 QQ 重新起来"就等于"让 NapCat 回来"。程序据此分两种情况处理:

| 情况 | 判断依据 | 处理方式 |
|---|---|---|
| QQ 进程已死 | NapCat 端口(3001)不在监听 | 直接执行快速登录脚本拉起 QQ |
| 账号被踢下线 | 端口在监听,但连接被拒/`get_status.online=false` | 先结束 QQ 进程,再执行快速登录重新注入 |

两个触发点:
- **连接断开**时立即触发(主连接断开就是 NapCat 不可用的最强信号)
- **每 `health_check_interval` 秒**巡检一次,兜住"连接还在但账号已离线"的情况

**实测效果**:从 `QQ.exe=0`、端口未监听的完全离线状态,到自动恢复上线约 **19 秒**,且**不弹出任何黑窗口**(用 `CREATE_NO_WINDOW` 启动)。

### 快速登录脚本

`napcat-autologin.bat` 是配套的启动脚本,内容等价于 NapCat 自带的 `launcher-user.bat`,只是去掉了末尾的 `pause`(否则自动调用时会一直挂住):

```bat
@echo off
cd /d D:\tools\NapCat
set NAPCAT_PATCH_PACKAGE=D:\tools\NapCat\qqnt.json
set NAPCAT_LOAD_PATH=D:\tools\NapCat\loadNapCat.js
set NAPCAT_INJECT_PATH=D:\tools\NapCat\NapCatWinBootHook.dll
set NAPCAT_LAUNCHER_PATH=D:\tools\NapCat\NapCatWinBootMain.exe
set NAPCAT_MAIN_PATH=D:\tools\NapCat\napcat.mjs
echo (async () =^> {await import("file:///D:/tools/NapCat/napcat.mjs")})() > "D:\tools\NapCat\loadNapCat.js"
"D:\tools\NapCat\NapCatWinBootMain.exe" "D:\Tencent\QQNT\QQ.exe" "D:\tools\NapCat\NapCatWinBootHook.dll" <机器人QQ号>
exit /b 0
```

> ⚠️ 脚本里的**路径和末尾的 QQ 号需要按你的环境修改**(分别是 NapCat 安装目录、QQ 安装路径、机器人 QQ 号)。QQ 号传给 `NapCatWinBootMain.exe` 即触发**快速登录**,失败时仍会退回扫码登录界面。

### 安全边界

- **需要重新扫码的情况无法自动恢复**:如果本机快速登录凭证已失效(比如在别处登录过同一个号),脚本会停在扫码界面,程序在 `relogin_wait_seconds` 后放弃并在日志里提示
- **限流保护**:每小时最多自动重登 `relogin_max_per_hour` 次,失败后按 1/5/15 分钟退避,避免"重启→被踢→再重启"的死循环
- **管理员权限**:`napcat-autologin.bat` 走的是 `launcher-user.bat` 的逻辑(**不需要管理员**)。若你的环境必须用需要提权的 `launcher.bat`,自动调用时会弹 UAC,无法无人值守
- 设 `auto_relogin: false` 可完全关闭该功能

## 💭 关于思考模式(`enable_thinking`)

`deepseek-flash` 默认开启思考模式:回复前模型会先输出一段思维链,回答更周到、语气更自然,代价是**每轮多花约 70~120 个推理 token**,首字延迟也略高。设为 `false` 可换取更快的响应和更低的费用。

需要注意:**思考模式下 `temperature` / `presence_penalty` / `frequency_penalty` 参数不生效**(官方文档说明:传了不报错,但会被忽略)。本项目里 `temperature=1.3` 只在 `enable_thinking=false` 时才真正起作用。

记忆整理那次调用由 `lm_thinking` 单独控制(默认跟随 `enable_thinking`)。**建议保持开启**:实测开启思考后,整理能正确区分"已撤销/已放弃"与"仍有效"的事件,也不会漏掉生日、家人健康这类重要信息(关闭思考时会漏)。代价是每次整理多约 1500 个推理 token。

> ⚠️ 开启思考时**必须给足 `lm_max_tokens`**(默认 16000):推理 token 也计入这个额度,设得太小(实测 8000)会导致推理把额度吃光、返回空内容,整理直接失败。程序内置了一次"加大预算重试",但仍建议不要调低。


## 🗃️ 长期记忆是怎么工作的

只有**私聊**启用长期记忆(群聊仍沿用 `memory_max_messages` 的滚动窗口),分两层:

| 层 | 存放 | 内容 |
|---|---|---|
| **L0 对话窗口** | `memory.json` | 每个好友最近的原始对话(带时间戳),私聊默认保留到 160 条 |
| **L1 事实库** | `memory_long.json` | 从对话里提炼出的"关于对方的事实",按类型分组长期留存 |

流程:

```
聊天中 → 消息持续写入 L0
      → L0 达到 lm_l0_max(默认 160 条)
      → 后台(延迟 lm_compress_delay 秒)把最早的 lm_compress_count(80)条
        与现有事实库一起交给 deepseek-flash 重新整理
      → 先写回事实库,再从 L0 删掉这 80 条(最坏情况只是重复整理,不会丢消息)
```

几个设计取舍:

- **不使用向量/Embedding 检索**,而是让模型在**压缩时**就把信息提炼成一条条独立事实,并合并去重、处理状态变化(例如"从杭州搬到上海"会替换掉旧的居住地)。省掉了向量库,也不需要 embedding 接口。
- **压缩只喂用户消息**,机器人的回复不参与整理 —— 避免把自己现编的内容当成对方的事实记下来。
- **注入时按类型截断**:始终优先保留"基本信息/重要关系",事件类事实会过期自动过滤,总注入量受 `lm_l1_inject_chars` 限制,不会随聊天史无限膨胀。
- **每轮注入的记忆块是稳定的**,因此能命中 DeepSeek 的前缀缓存;整段记忆的开销通常只有主对话的一小部分。
- **关于自己的身份对话不会被记入**:凡涉及"你是不是 AI/程序"之类的话题,整理时一律跳过,避免记忆反过来破坏人设。
- 到达上限才会触发一次整理,因此整理次数 = 累计消息条数 / `lm_compress_count`,费用可预期。

## 🧠 内置指令

| 指令     | 效果                                          |
|--------|---------------------------------------------|
| `清空记忆` | 彻底清空当前会话:私聊会同时删除对话窗口与长期事实库;群聊清空该群上下文 |

## 🔒 隐私与安全

- 所有密钥存放在 `.env`(`TEXT_API_KEY` / `VISION_API_KEY`),已在 `.gitignore` 中排除,**切勿提交**
- 实际配置 `config.json`、人设 `persona.txt`、记忆 `memory.json` 与 `memory_long.json` 均含个人/隐私信息,已加入 `.gitignore`,**不会推送到仓库**
- ⚠️ 启用长期记忆后,私聊内容会被**长期留存**并整理成事实库(不再是只留最近几十条)。请确认对方知情且同意,并在必要时用"清空记忆"彻底删除;`memory_long.json` 与 `memory.json` 都不要分享给他人
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
├── memory.json             # 对话窗口记忆(自动生成,已被 gitignore)
├── memory_long.json        # 私聊长期记忆事实库(自动生成,已被 gitignore)
├── requirements.txt        # Python 依赖
└── README.md
```

## ❓ 常见问题

**Q: 启动报 `缺少 API Key!请在 .env 中配置`**
A: 确认已创建 `.env` 并填写两个 Key;确认当前目录就是项目根目录。

**Q: 日志一直显示连接断开/重连**
A: 确认 NapCat 已开启 WebSocket 服务端(正向),且端口与 `ws_url` 一致(默认 `3001`);检查防火墙是否放行。

**Q: 收到图片不识别或显示"图片加载失败"**
A: 程序会通过 NapCat 的 `get_file`/`get_image` API 读取图片**本地缓存**再交给视觉模型,不依赖 QQ 图片链接,通常无需额外配置。若仍失败,请检查 NapCat 是否正常运行、图片是否已缓存成功(重启 NapCat 后重试)。

**Q: 群聊里机器人不回复**
A: 确认群号在白名单、`group_at_only` 与回复概率符合预期;概率机制下部分消息会故意不回复,这是特性不是 Bug。

**Q: 回复开头带 `[时间戳]` 或 `小深:` 前缀**
A: 程序已内置前缀清理逻辑;若仍出现,多为模型偶发输出,可忽略或调整人设描述。

**Q: 图片为什么先转成文字,而不是直接把图片存进记忆?**
A: 图片在发给模型识别后,只把**文字描述**写进记忆(`memory.json`),原因是:(1) 省 token —— 一张图若按原图塞进后续每轮对话,最多可占 1024 个图片 token,而一段文字描述通常只要几十个;(2) 可读 —— 记忆文件里是"【图片】一只橘猫趴在键盘上",而不是一长串无法直读的 base64;(3) 可整理 —— 长期记忆压缩时,文字描述能被直接理解并提炼成事实,base64 做不到。识别本身仍由视觉模型完成,其费用也有免费额度。

**Q: 长期记忆什么时候才会触发?会不会很费钱?**
A: 只有私聊、且对话累积到 `lm_l0_max`(默认 160 条)时才整理一次,所以**整理次数 ≈ 累计消息条数 ÷ `lm_compress_count`**,与聊天快慢无关,费用可预期。整理过程只把"新片段 + 现有事实库"发给模型,不重发全部历史。想更省可以:调大 `lm_l0_max`(整理更少但记忆更粗)、调小 `lm_l1_inject_chars`(每轮注入更少)、或把 `lm_thinking` 设为 `false`(每次省约 1500 个推理 token,但会漏掉部分重要信息)。

**Q: 说了"清空记忆"以后,机器人还会记得以前的事吗?**
A: 不会。私聊的"清空记忆"会**同时**删除对话窗口(`memory.json`)和长期事实库(`memory_long.json`)里该好友的全部内容,并作废正在进行中的整理任务。群聊则清空该群的上下文。

**Q: 开了思考模式后,调 `temperature` 好像没反应?**
A: 这是官方行为:**思考模式下 `temperature`、`presence_penalty`、`frequency_penalty` 都不生效**(传了不报错,但会被忽略)。想真正用这些采样参数,把 `enable_thinking` 设为 `false`。

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

