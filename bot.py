# -*- coding: utf-8 -*-
import asyncio
import json
import random
import logging
from logging.handlers import RotatingFileHandler
import sys
import time
from datetime import datetime, timezone, timedelta
import re
import websockets
from openai import AsyncOpenAI
import os
from dotenv import load_dotenv
import uuid
import base64
import shutil
import socket
import atexit
import subprocess
import threading
from copy import deepcopy

load_dotenv()   # 读 .env

# ---- 机密：从 .env 读取 ----
TEXT_API_KEY = os.getenv("TEXT_API_KEY", "")
VISION_API_KEY = os.getenv("VISION_API_KEY", "")

# 启动校验，防止忘了配 Key
if not TEXT_API_KEY or not VISION_API_KEY:
    raise SystemExit("缺少 API Key！请在 .env 中配置")

# ---- 普通配置：从 config.json 读取 ----
with open("config.json", "r", encoding="utf-8") as _f:
    CFG = json.load(_f)

WS_URL = CFG["ws_url"]
TEXT_BASE_URL = CFG["text_base_url"]
TEXT_MODEL = CFG["text_model"]
VISION_BASE_URL = CFG["vision_base_url"]
VISION_MODEL = CFG["vision_model"]
MAX_IMAGES_PER_MESSAGE = CFG["max_images_per_message"]

# 固定回复语:可在 config.json 中自定义(省略时使用默认值,兼容旧配置文件)
FALLBACK_REPLY = CFG.get("fallback_reply", "喵……刚才网络开小差了，再说一次好不好？")
CLEAR_MEMORY_REPLY = CFG.get("clear_memory_reply", "喵~ 记忆已经清空啦，我们重新开始吧！")

BOT_QQ = CFG["bot_qq"]
PRIVATE_WHITELIST = set(CFG["private_whitelist"])
GROUP_WHITELIST = set(CFG["group_whitelist"])

GROUP_AT_ONLY = CFG["group_at_only"]
GROUP_REPLY_PROBABILITY = CFG["group_reply_probability"]
GROUP_KEYWORD_PROBABILITY = CFG["group_keyword_probability"]
GROUP_ACTIVE_PROBABILITY = CFG["group_active_probability"]
GROUP_DEFAULT_PROBABILITY = CFG["group_default_probability"]
GROUP_ACTIVE_WINDOW = CFG["group_active_window"]
GROUP_MAX_CONSECUTIVE_REPLIES = CFG["group_max_consecutive_replies"]
KEYWORDS = CFG["keywords"]

REPLY_PROBABILITY = CFG["reply_probability"]
PROACTIVE_INTERVAL_PRIVATE = tuple(CFG["proactive_interval_private"])
PROACTIVE_INTERVAL_GROUP = tuple(CFG["proactive_interval_group"])
PROACTIVE_TO_EACH = CFG["proactive_to_each"]
# 主动消息的"静默期"（秒）：若距离上次对话不足这么久，就跳过本次主动消息，
# 避免"刚聊完天，机器人又冒一句"的出戏情况。私聊/群聊分开配置，且按会话独立判断。
# 设为 0 即关闭该机制。
PROACTIVE_QUIET_PRIVATE = CFG.get("proactive_quiet_private", 60)
PROACTIVE_QUIET_GROUP = CFG.get("proactive_quiet_group", 300)

# ---- NapCat 掉线自愈 ----
# 账号被踢下线 / QQ 进程死掉时，自动执行"结束 QQ 进程 → 快速登录"把机器人拉回线上。
AUTO_RELOGIN = CFG.get("auto_relogin", True)
QQ_CLIENT_PATH = CFG.get("qq_client_path", r"D:\Tencent\QQNT\QQ.exe")
AUTOLOGIN_SCRIPT = CFG.get("autologin_script", "napcat-autologin.bat")
HEALTH_CHECK_INTERVAL = CFG.get("health_check_interval", 30)     # 看门狗巡检间隔（秒）
RELOGIN_WAIT_SECONDS = CFG.get("relogin_wait_seconds", 180)      # 触发后等待上线的最长时间
RELOGIN_MAX_PER_HOUR = CFG.get("relogin_max_per_hour", 2)        # 每小时最多自动重登次数
# 快速登录失败、需要扫码时的二维码来源（NapCat 会生成图片与含解码 URL 的控制台日志）
QRCODE_IMAGE = CFG.get("qrcode_image", r"D:\tools\NapCat\cache\qrcode.png")
QRCONSOLE_LOG = CFG.get("qrconsole_log") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "napcat-autologin.log")
# 触发快速登录后,等待"上线 或 出现新二维码"的窗口(秒)。
# 快速登录失败时 NapCat 会立刻生成新二维码(实测 0 秒级),无需苦等 90~180 秒。
QR_DETECT_TIMEOUT = CFG.get("qr_detect_timeout", 20)
# 扫码选择的最长等待时间(秒)。超时后会先复查一次在线状态:
#   已上线(可能刚扫完码) → 视作"选 N"继续运行
#   仍离线(无人值守)     → 按默认 Y 处理:清理 QQ/NapCat 进程并安全退出,
#                          避免主循环卡死、进程一直占着登录会话
# 设为 0 表示永远等待(纯人工值守时可用)。
SCAN_PROMPT_TIMEOUT = CFG.get("scan_prompt_timeout", 300)
# 停止 bot.py 时是否一并结束 QQ / NapCat 进程。
# 默认 true：否则 Ctrl+C 之后 NapCat 与 QQ 会继续在后台占着内存和登录状态。
KILL_QQ_ON_EXIT = CFG.get("kill_qq_on_exit", True)

MEMORY_MAX_MESSAGES = CFG["memory_max_messages"]
MEMORY_FILE = CFG["memory_file"]

# 思考模式开关：deepseek-flash 默认开启思考模式。开启时回复更周到自然，代价是每轮多花约 70~120 个推理 token。
# 注意：官方文档明确思考模式不支持 temperature / presence_penalty / frequency_penalty（传了不报错但不生效），
# 所以下面 chat_with_deepseek 里的 temperature=1.3 只在 enable_thinking=false 时才真正起作用。
ENABLE_THINKING = CFG.get("enable_thinking", True)

# ---- 长期记忆（仅私聊启用）----
# L0 = memories[key] 滑动窗口；L1 = memory_long.json 中按"事实条目"自更新的记忆库。
# 当 L0 达到 LM_L0_MAX 条时，取最早的 LM_COMPRESS_COUNT 条与新片段交给模型，重写出一份精简的 L1。
LONG_MEMORY_ENABLED = CFG.get("long_memory_enabled", False)
LM_FILE = CFG.get("lm_file", "memory_long.json")
LM_L0_MAX = CFG.get("lm_l0_max", 160)
LM_COMPRESS_COUNT = CFG.get("lm_compress_count", 80)
LM_COMPRESS_DELAY = CFG.get("lm_compress_delay", 3)
# L1 的容量以「条目数」计，不以「字数」计。
# 为什么改：模型是逐条生成的，"我写了几条"它数得清，"我写了多少字"只能靠猜。
# 一条事实的 JSON 骨架固定占约 88 字符（t/seen/exp/n/sensitive 这些键名和默认值），
# 按整段估算会把可用额度算少 7~9 倍（1000 字口径下：按 c 字段算是 50~66 条，按整段 JSON 算只有 7~10 条）。
# 代码侧的统计口径从来只算 c 字段，和模型的直觉本就不一致——改用条目数后这个歧义从根上消失。
LM_L1_MAX_FACTS = CFG.get("lm_l1_max_facts", 100)
# 分类参考上限。作用是"防止某一类把总配额吃光"，不是给每类设死数字：
# 整库没超 LM_L1_MAX_FACTS 时不触发任何淘汰。
# 三个桶按内容量的天然比例分配：who 是身份与偏好（条目不多但都很硬）、
# us 是关系状态（随相处持续累积，给得最宽）、event 是会自然翻篇的事（不需要留太多）。
LM_L1_TYPE_QUOTA = CFG.get("lm_l1_type_quota", {
    "who": 20, "us": 24, "event": 16,
})
# 注入侧不再限制字数（条目数上限已经隐含了成本上限：100 条约 2400 字符，
# 相比 L0 的几百条原始对话只是零头）。
# 这个"保险丝"只在模型异常输出（例如一次返回好几百条）时兜底，正常永远碰不到。
LM_L1_INJECT_HARD_LIMIT = CFG.get("lm_l1_inject_hard_limit", 200)
# 私聊 L0 的兜底硬上限。正常压缩会在 LM_L0_MAX 就收口，这个上限只在"压缩持续失败"
# 时生效，避免上下文无限膨胀；取 3 倍阈值是为了给压缩重试留足空间。
LM_L0_HARD_LIMIT = CFG.get("lm_l0_hard_limit", LM_L0_MAX * 3)

# ---- 近期流水（recent）----
# 为什么需要这一层：L0 在高密度对话下只覆盖几小时（实测约 100 条/小时，500 条也就 5 小时），
# 而 facts 只记长期属性，中间"最近聊过些什么"没有归宿 —— 于是模型对昨天的事一无所知。
# recent 补的就是这一段：按天分组的日常流水，短期待留，过期由代码丢弃。
# 它与 facts 有两点根本不同：
#   1. 增量追加：模型只输出"本次新发现的事"，从不重写整份列表 —— 结构上不可能被覆盖；
#   2. 时效由代码管（按"记录日"淘汰 + 条数保护），不依赖模型记得删。
LM_RECENT_DAYS = CFG.get("lm_recent_days", 3)                # 保留最近几个聊过的日子
LM_RECENT_MAX_ITEMS = CFG.get("lm_recent_max_items", 200)    # 条数保护上限
# persona（"你心里的事"）的长度上限。它是每轮都注入的固定成本，
# 同时也要防止模型把它写成流水账——心事本该是凝练的。
LM_PERSONA_MAX_CHARS = CFG.get("lm_persona_max_chars", 400)

# ---- 多段回复（模型用空行分隔时，按段依次发送多条消息）----
# 模型可以用「连续两个换行」把一次回复分成多条短消息，更接近真人在 QQ 上连发几条。
SPLIT_REPLY_ENABLED = CFG.get("split_reply_enabled", True)
SPLIT_REPLY_MAX = CFG.get("split_reply_max", 10)                    # 最多拆成几条，超出合并到最后一条
SPLIT_REPLY_INTERVAL = tuple(CFG.get("split_reply_interval", [0.5, 1.5]))   # 每条之间的随机间隔(秒)

# 记忆整理（压缩）是否开启思考模式。默认跟随全局 enable_thinking。
# 实测：开启思考后判断力明显更好——能正确区分"已撤销/已放弃"与"仍有效"的事件，
# 不会漏掉生日、家人健康这类重要信息（关闭思考时会漏），条目也更精炼。
# 代价是每次整理多约 1500 个推理 token（按当前价格约多 0.6 分钱/次）。
LM_THINKING = CFG.get("lm_thinking", ENABLE_THINKING)
# 整理调用的输出上限。开启思考时需留足预算：推理 token 也计入 max_tokens，
# 设得太小会导致推理吃光额度、返回空内容（实测 8000 时必然失败）。
LM_REASONING_MAX_TOKENS = CFG.get("lm_max_tokens", 16000)

# 配置自洽性校验：宁可不启动，也不要静默丢记忆
if LONG_MEMORY_ENABLED and not (2 <= LM_COMPRESS_COUNT <= LM_L0_MAX // 2):
    raise SystemExit(
        f"配置错误：lm_compress_count({LM_COMPRESS_COUNT}) 必须满足 2 <= 值 <= lm_l0_max/2({LM_L0_MAX // 2})，"
        "否则压缩来不及在窗口溢出前生效"
    )
if LONG_MEMORY_ENABLED and not (1 <= LM_RECENT_DAYS <= 30):
    raise SystemExit(f"配置错误：lm_recent_days({LM_RECENT_DAYS}) 必须为 1~30 之间的整数")
if LONG_MEMORY_ENABLED and LM_RECENT_MAX_ITEMS < 20:
    raise SystemExit(f"配置错误：lm_recent_max_items({LM_RECENT_MAX_ITEMS}) 至少为 20")
if LONG_MEMORY_ENABLED and LM_PERSONA_MAX_CHARS < 100:
    raise SystemExit(
        f"配置错误：lm_persona_max_chars({LM_PERSONA_MAX_CHARS}) 至少为 100")
if LONG_MEMORY_ENABLED and LM_COMPRESS_COUNT % 2 != 0:
    raise SystemExit(
        f"配置错误：lm_compress_count({LM_COMPRESS_COUNT}) 必须是偶数，以保证裁剪落在对话边界上"
    )

ENABLE_SILENT_HOURS = CFG["enable_silent_hours"]
SILENT_HOURS_START = CFG["silent_hours_start"]
SILENT_HOURS_END = CFG["silent_hours_end"]

# 角色名
ROBOT_NAME = CFG.get("bot_name", "小深")

# 加载人设（从 config.json 或独立文件）
if "persona_file" in CFG:
    with open(CFG["persona_file"], "r", encoding="utf-8") as f:
        PERSONA_TEMPLATE = f.read()
else:
    PERSONA_TEMPLATE = CFG["persona"]

# 将人设中的占位符替换为实际角色名
PERSONA = PERSONA_TEMPLATE.replace("{bot_name}", ROBOT_NAME)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("DeepSeekBot")

# 错误日志落盘。控制台日志一关窗就没了——上次排查"记忆被清空"时最大的障碍就是
# log.warning / log.error 全都没留下，只能靠时间戳和文件内容反推。
# 这里把 WARNING 及以上另存一份文件：只记异常与告警，不记常规流水
# （INFO 量太大，会把文件刷满反而淹没真正的问题）。
if CFG.get("log_file_enabled", True):
    try:
        _log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 CFG.get("log_file", "bot_error.log"))
        _fh = RotatingFileHandler(_log_path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        _fh.setLevel(logging.WARNING)
        _fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        logging.getLogger().addHandler(_fh)
    except Exception as _e:
        print(f"[warn] 错误日志文件无法创建，本次仅输出到控制台：{_e}")

# DeepSeek 客户端（主回复：是否思考由 enable_thinking 决定）
client = AsyncOpenAI(
    api_key=TEXT_API_KEY,
    base_url=TEXT_BASE_URL,
    timeout=30.0,
    max_retries=2
)

# 长期记忆压缩专用客户端：同一个 key / base_url / 模型，但使用更长的超时。
# 思考模式由 LM_THINKING 决定（默认跟随 enable_thinking）；这条链路完全在后台，
# 不影响主回复的 enable_thinking 设置。
memory_client = AsyncOpenAI(
    api_key=TEXT_API_KEY,
    base_url=TEXT_BASE_URL,
    timeout=180.0,
    max_retries=1
)

# 视觉模型客户端（通义千问 VL）
vision_client = AsyncOpenAI(
    api_key=VISION_API_KEY,
    base_url=VISION_BASE_URL,
    timeout=30.0,
    max_retries=1
)

# 记忆：key 为 "私聊QQ号" 或 "g:群号"
memories: dict[str, list[dict]] = {}

# 群聊活跃期记录：群号 -> 到期时间戳
group_active_until: dict[int, float] = {}

# 群聊连续回复计数：群号 -> 次数
group_consecutive_replies: dict[int, int] = {}

# 会话最后活动时间：key -> 时间戳，用于主动消息的静默期判断。
# 注意：机器人自己发出的**主动消息不更新**它，否则机制会把自己永久抑制住。
last_activity: dict[str, float] = {}

# NapCat API 请求-响应匹配:echo -> asyncio.Future
pending_actions: dict[str, asyncio.Future] = {}

# ---------- NapCat 掉线自愈状态 ----------
# 注：曾有一个全局 is_online 缓存，但没有任何地方读取它（生成前检查用的是
# check_online() 的返回值），属于死变量，已移除，避免误导后来者。
relogin_task: asyncio.Task | None = None      # 在途的重登任务
relogin_attempts: list[float] = []            # 最近的重登时间戳，用于限流
relogin_failures = 0                          # 连续失败次数，用于退避
# 用户主动选择手动重建凭证 → 主循环据此优雅退出（见 main 的停止分支）
need_manual_recovery = False
# 扫码交互是否已在进行中（防止主循环与重登任务同时弹二维码）
scan_prompt_active = False

# ---------- 长期记忆状态 ----------
# 记忆库：key -> {"version": int, "updated": str, "facts": [ {t,c,seen,exp,n} ]}
long_memories: dict[str, dict] = {}

# 代际号：key -> int。执行"清空记忆"时自增，用于让仍在飞行中的压缩任务作废，
# 避免"清空之后压缩结果又把旧记忆写回来"这种违背用户意图的情况。
generations: dict[str, int] = {}

# 按 key 的写入锁。规则：所有对 memories / long_memories 的读-改-写都在锁内完成，
# 而 LLM 调用一律放在锁外（否则会长时间阻塞该用户乃至全局）。
mem_locks: dict[str, asyncio.Lock] = {}

# 已排入压缩队列的 key（压缩任务读 L1 后即清除，防止同一次积压被重复调度）
compress_scheduled: set[str] = set()

# 在途压缩任务：key -> asyncio.Task，仅用于让离线测试/诊断能等待压缩完成
compress_tasks: dict[str, asyncio.Task] = {}

# 正在压缩中的 key。与 compress_scheduled 的区别：
#   compress_scheduled 只覆盖"已调度、还没开始"的短暂窗口（任务一开跑就清除）
#   compressing 覆盖"整个压缩过程"（含锁外那次长达数秒的 LLM 调用）
# 少了它就会出现：LLM 调用期间用户继续聊天 → 再起一个压缩任务 →
# 两个任务基于同一份旧 L1 各算一遍，后提交的把先提交的成果覆盖掉（丢事实）。
compressing: set[str] = set()


def get_mem_lock(key: str) -> asyncio.Lock:
    """取得某个会话的写入锁（同一 key 永远返回同一把锁）。"""
    lock = mem_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        mem_locks[key] = lock
    return lock


def bump_generation(key: str) -> None:
    """让该会话所有在途的压缩任务作废。"""
    generations[key] = generations.get(key, 0) + 1

# ---------- 时间工具 ----------
def get_beijing_time_str() -> str:
    """返回当前北京时间字符串，格式 YYYY-MM-DD HH:MM 周X（无秒）"""
    now = datetime.now(timezone(timedelta(hours=8)))
    weekday_cn = '一二三四五六日'[now.weekday()]  # 周一对应 '一'
    return now.strftime("%Y-%m-%d %H:%M") + f" 周{weekday_cn}"

# 昵称前缀正则：预编译一次。必须把 {ROBOT_NAME} 真正替换成角色名，
# 并用 re.escape 转义（角色名可能含正则元字符）；旧代码把 r'^{ROBOT_NAME}...'
# 当普通字符串用，导致它在逐字匹配 "{ROBOT_NAME}"、前缀清理从未生效。
_ROBOT_NAME_RE = re.compile(
    r'^' + re.escape(ROBOT_NAME) + r'[（(]?\d*[)）]?\s*[:：]\s*'
)


def clean_reply(text: str) -> str:
    """
    清理 AI 回复开头可能误输出的时间戳、昵称前缀等垃圾信息。
    支持清理：
      - [时间戳]
      - [{ROBOT_NAME}（QQ号）] 或 {ROBOT_NAME}（QQ号）:
      - {ROBOT_NAME}: / {ROBOT_NAME}：
      - 以及上面组合后残留的冒号
    """
    while True:
        stripped = text.lstrip()

        # 1. 清理开头的方括号前缀，如 [时间戳]、[{ROBOT_NAME}（QQ号）]
        if stripped.startswith('['):
            end = stripped.find(']')
            if end != -1:
                stripped = stripped[end+1:].lstrip()
                # 如果方括号后紧跟冒号，也一并去掉（如“[{ROBOT_NAME}]：你好”）
                if stripped.startswith(':') or stripped.startswith('：'):
                    stripped = stripped[1:].lstrip()
                text = stripped
                continue  # 可能还有下一个前缀，继续循环

        # 2. 清理“{ROBOT_NAME}（QQ号）：”或“{ROBOT_NAME}：”等昵称前缀
        match = _ROBOT_NAME_RE.match(stripped)
        if match:
            text = stripped[match.end():].lstrip()
            continue  # 清理后可能还有残留，继续检查

        # 没有更多可清理的前缀，跳出
        break

    return text.strip()


def split_reply(reply: str) -> list[str]:
    """把模型的回复按「连续空行」拆成多条消息，每条单独 clean_reply。

    模型可以用空行把一次回复分成几条短消息，更接近真人在 QQ 上连发。
    注意只按"连续两个换行"（中间允许空白）分割，单个换行保留。
    超过 SPLIT_REPLY_MAX 条时，把多余部分合并到最后一条（避免越拆越多）。
    """
    if not reply:
        return []
    if not SPLIT_REPLY_ENABLED:
        # 不拆分时也要**逐段**清理：模型有时整段输出每条都带 [时间戳] 前缀，
        # 而 clean_reply 只清整段开头，后面每一段的都会留下。
        segs = [clean_reply(p) for p in re.split(r"\n\s*\n", reply)]
        one = "\n\n".join(s for s in segs if s)
        return [one] if one else []

    parts = [p.strip() for p in re.split(r"\n\s*\n", reply)]
    parts = [p for p in parts if p]
    if not parts:
        return []

    # 顺序很关键：**先逐条清理，再合并**。
    # 反过来的话，被合并的那些段会接到「……」后面，它们的开头前缀就不再是
    # 整行的开头，而 clean_reply 只清开头 —— 第 10 条往后的时间戳会留在消息里。
    parts = [clean_reply(p) for p in parts]
    parts = [p for p in parts if p]
    if not parts:
        return []

    if len(parts) > SPLIT_REPLY_MAX:
        head = parts[:SPLIT_REPLY_MAX - 1]
        merged = "……".join(parts[SPLIT_REPLY_MAX - 1:])
        parts = head + [merged]
    return parts

# ---------- 记忆读写 ----------
def _atomic_write_json(path: str, data) -> None:
    """原子写：先写临时文件再替换，避免进程被杀时留下半截 JSON 把记忆文件弄坏。

    临时文件名带进程号：多个 bot 进程同时写同一个记忆文件时，若共用固定名
    `<path>.tmp`，会互相覆盖甚至撞上"文件被占用"（WinError 32）。
    写入失败时清理临时文件，避免残留旧内容误导排查。
    """
    tmp = f"{path}.{os.getpid()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise

def load_memory():
    global memories
    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            memories = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        memories = {}

def save_memory():
    _atomic_write_json(MEMORY_FILE, memories)

def _as_bool(v) -> bool:
    """把模型可能给出的各种"真值写法"归一成 bool。

    单独抽出来是因为 bool("false") == True：模型偶尔把布尔值写成字符串，
    直接 bool() 会把"不需要保密"误判成"要保密"，注入时就会多出一句错误的保密提示。
    """
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "yes", "y", "是")
    if v is None:
        return False
    return bool(v)


def _fact_from_item(item, t_hint: str = "") -> dict | None:
    """把一条原始条目规整成内部结构，非法则返回 None。

    单条清洗逻辑的唯一实现：载入磁盘数据（_clean_l1_facts）和解析模型输出
    （parse_l1_facts）都走这里。以前这两处是复制粘贴的同一段代码，改一处漏一处
    就会造成"磁盘能读、模型输出却被丢"这类很难查的不一致。

    t_hint 供分桶格式使用：桶名即类型，桶内条目不再写 t 字段。

    类型只认 LM_TYPE_ORDER 里的三种，其余一律丢弃——不做历史类型映射。
    """
    if not isinstance(item, dict):
        return None
    t = t_hint or str(item.get("t") or "").strip()
    c = str(item.get("c") or "").strip()
    if t not in LM_TYPE_ORDER or not c:
        return None
    seen = str(item.get("seen") or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", seen):
        seen = _today_str()
    try:
        n = max(1, int(item.get("n") or 1))
    except (TypeError, ValueError):
        n = 1
    # 不再有 exp 字段：事件的时间点写在 c 语句本身，过期与否交给模型判断（它有 seen 和 n 做辅助）
    return {"t": t, "c": c[:200], "seen": seen, "n": n,
            "sensitive": _as_bool(item.get("sensitive"))}


def _clean_l1_facts(raw) -> list[dict]:
    """清洗 L1 事实条目，保证字段类型正确。

    为什么需要：`format_l1_block` 会在**每轮私聊消息**里被调用，一旦某条事实的
    字段类型不对（例如手工编辑 memory_long.json 时把 n 写成字符串），
    排序时 `-f["n"]` 会抛 TypeError，导致该用户的对话全部失败。
    这里在载入时就把数据规整好，坏条目直接丢弃而不是带病运行。
    """
    if not isinstance(raw, list):
        return []
    return [f for f in (_fact_from_item(i) for i in raw) if f]


def _clean_recent(raw) -> dict:
    """清洗 recent 字段（按天分组的近期流水）。坏数据直接丢弃，不带病运行。

    结构固定为 {"YYYY-MM-DD": ["一句话", ...]}，按日期升序返回。
    条目兼容两种写法：纯字符串，或 {"d": ..., "c": ...} 的对象（模型输出用后者，
    存盘时统一压成字符串数组，省体积也更难出错）。
    """
    out = {}
    if not isinstance(raw, dict):
        return out
    for day, items in raw.items():
        d = str(day).strip()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d) or not isinstance(items, list):
            continue
        texts = []
        for it in items:
            c = it.get("c") if isinstance(it, dict) else it
            c = str(c or "").replace("\n", " ").strip()
            if c:
                texts.append(c[:120])
        if texts:
            out[d] = texts
    return {d: out[d] for d in sorted(out)}


def _clean_persona(raw) -> str:
    """清洗 persona（"你心里的事"）。它是一段文本，不是条目列表。

    与 facts 的根本区别：facts 只能增删条目，persona 每次压缩时整体重写——
    人格要能"变成另一个人"，而不是靠条目堆叠。所以这里只做空白规整与长度截断，
    内容判断完全留给模型。
    """
    text = str(raw or "").replace("\r\n", "\n").strip()
    if len(text) > LM_PERSONA_MAX_CHARS:
        text = text[:LM_PERSONA_MAX_CHARS].rstrip()
    return text


def _recent_visible_days(recent: dict) -> list[str]:
    """返回应保留/注入的 recent 日期键：最近 LM_RECENT_DAYS 个「有记录且非空」的日子。

    和旧版"按自然日算"的区别：自然日会把很久前唯一一次聊天的流水也淘汰掉，
    低频率聊天时 recent 就彻底清空、忘了上次聊到哪。改成"记录日"（最近 N 个
    有聊天记录的日子）之后，中间隔多久都不清空。
    顺带过滤空键——空键不产出内容，却会白占一个名额。
    """
    return [d for d in sorted(recent) if recent[d]][-LM_RECENT_DAYS:]


def _prune_recent(recent: dict) -> tuple[dict, list[str]]:
    """按"记录日"淘汰 recent：只保留最近 LM_RECENT_DAYS 个有记录的日子，
    再按条数上限从最老的一天整天删。

    返回 (清理后的 recent, 告警文本列表)。
    刻意整天删而不是删单条——半天流水比没有更让人困惑，而且按记录日淘汰
    才能让"记得上次聊到哪"这个语义保持清晰。
    """
    notes = []
    if not isinstance(recent, dict) or not recent:
        return {}, notes

    # 1) 保留最近 N 个有记录的日子，越界的整天删掉
    keep = set(_recent_visible_days(recent))
    for d in sorted(d for d in recent if d not in keep):
        del recent[d]
        notes.append(f"recent 只保留最近 {LM_RECENT_DAYS} 个聊过的日子，删除更早的 {d}")

    # 2) 条数保护：从最老的一天整天删，至少保留最近一天
    total = sum(len(v) for v in recent.values())
    while total > LM_RECENT_MAX_ITEMS and len(recent) > 1:
        oldest = min(recent)
        total -= len(recent[oldest])
        del recent[oldest]
        notes.append(f"recent 超过 {LM_RECENT_MAX_ITEMS} 条，整天删除最老的 {oldest}")
    if total > LM_RECENT_MAX_ITEMS:
        # 只剩最近一天还超限 —— 这不是"数据太多"，是抽取粒度失控了
        notes.append(f"recent 删到只剩最近一天仍有 {total} 条，超过上限 "
                     f"{LM_RECENT_MAX_ITEMS}：疑似抽取粒度失控（单日流水过多）")
    return recent, notes


def load_long_memory():
    """载入 L1 长期记忆库。缺失或损坏时退化为空库（不会影响主对话）。"""
    global long_memories
    if not LONG_MEMORY_ENABLED:
        return
    try:
        with open(LM_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            data = {}
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    # 统一清洗字段类型（见 _clean_l1_facts 的说明）
    long_memories = {}
    for k, entry in data.items():
        if not isinstance(entry, dict):
            continue
        facts = _clean_l1_facts(entry.get("facts"))
        try:
            version = int(entry.get("version") or 0)
        except (TypeError, ValueError):
            version = 0
        long_memories[k] = {
            "version": version,
            "updated": str(entry.get("updated") or ""),
            "facts": facts,
            # 旧数据没有 recent 字段，_clean_recent 对 None 返回空字典 → 自然兼容
            "recent": _clean_recent(entry.get("recent")),
            # persona 同样对旧数据缺失免疫：None → 空串，下次压缩时自然长出来
            "persona": _clean_persona(entry.get("persona")),
        }

def save_long_memory():
    """写盘前轮转一份备份（.bak1 = 上一次的内容，最多保留 3 份）。

    事实库是整体重写的，一旦模型返回异常内容（例如空数组）就会把既有记忆
    全部覆盖掉，且无法从 git 恢复（该文件被 gitignore）。留备份用于事后找回。

    顺序很关键：必须**先备份旧文件、再写新内容**。
    原实现是先写盘、再把刚写好的文件复制成 .bak1，结果 .bak1 永远等于主文件、
    完全没有备份价值（历史实际只剩 .bak2/.bak3 两份），而且主文件若写坏，
    .bak1 里存的也是同一份坏数据。
    """
    try:
        if os.path.exists(LM_FILE):
            for i in (2, 1):
                src, dst = f"{LM_FILE}.bak{i}", f"{LM_FILE}.bak{i + 1}"
                if os.path.exists(src):
                    os.replace(src, dst)
            shutil.copy2(LM_FILE, f"{LM_FILE}.bak1")
    except Exception as e:
        log.warning(f"长期记忆备份失败（不影响本次保存）：{e}")
    _atomic_write_json(LM_FILE, long_memories)

def clear_long_memory(key: str) -> None:
    """彻底忘记某个会话：清 L0、清 L1、让在途压缩作废。调用方需持有该 key 的锁。"""
    memories[key] = []
    long_memories.pop(key, None)
    bump_generation(key)
    save_memory()
    if LONG_MEMORY_ENABLED:
        save_long_memory()

async def append_memory(key: str, role: str, content: str):
    """追加一条记忆（带时间前缀）。

    时间戳写入行为与改造前完全一致；区别只在于本函数改为 async，并要求调用方持有该 key 的锁，
    从而保证"追加 + 落盘"是原子的，避免同一用户连发消息时互相覆盖。
    群聊沿用 memory_max_messages 硬截断；私聊正常交给长期记忆压缩接管窗口长度，
    但保留一个远高于阈值的兜底上限——万一压缩持续失败（如 API 长期故障），
    上下文不会无限膨胀导致每轮请求越来越慢、越来越贵。
    关闭长期记忆时（LONG_MEMORY_ENABLED=False）私聊回退到 memory_max_messages 截断，
    否则没有任何机制收口，上下文会无限增长。
    """
    time_str = get_beijing_time_str()
    content = f"[{time_str}] {content}"
    memories.setdefault(key, []).append({"role": role, "content": content})
    if key.startswith("g:"):
        if len(memories[key]) > MEMORY_MAX_MESSAGES:
            memories[key] = memories[key][-MEMORY_MAX_MESSAGES:]
    elif not LONG_MEMORY_ENABLED:
        # 没有长期记忆接管，只能按普通窗口截断（与群聊一致）
        if len(memories[key]) > MEMORY_MAX_MESSAGES:
            memories[key] = memories[key][-MEMORY_MAX_MESSAGES:]
    elif len(memories[key]) > LM_L0_HARD_LIMIT:
        # 兜底：正常压缩会在 LM_L0_MAX 就收口，只有压缩持续失败才会走到这里
        keep = LM_L0_HARD_LIMIT
        dropped = len(memories[key]) - keep
        memories[key] = memories[key][-keep:]
        log.warning(f"私聊 {key} 记忆超过兜底上限 {LM_L0_HARD_LIMIT} 条，"
                    f"已丢弃最早 {dropped} 条（说明长期记忆压缩持续失败，请检查 API 与配置）")
    save_memory()

# ---------- 群聊活跃期 ----------
def set_group_active(gid: int):
    group_active_until[gid] = time.time() + GROUP_ACTIVE_WINDOW

def is_group_active(gid: int) -> bool:
    return time.time() < group_active_until.get(gid, 0)

# ---------- 关键词检测 ----------
def keyword_boost(text: str, nickname: str) -> bool:
    if any(k in nickname for k in KEYWORDS):
        return False
    return any(k in text for k in KEYWORDS)

# ---------- 群聊概率计算 ----------
def group_reply_probability(gid: int, mentioned: bool, text: str, nickname: str) -> float:
    if mentioned:
        return GROUP_REPLY_PROBABILITY

    if not is_group_active(gid):
        group_consecutive_replies[gid] = 0
        if keyword_boost(text, nickname):
            return GROUP_KEYWORD_PROBABILITY
        return GROUP_DEFAULT_PROBABILITY

    if group_consecutive_replies.get(gid, 0) >= GROUP_MAX_CONSECUTIVE_REPLIES:
        return GROUP_DEFAULT_PROBABILITY

    return GROUP_ACTIVE_PROBABILITY

# ---------- 生成系统提示（私聊/群聊区分） ----------
def build_system_content(key: str) -> str:
    if key.startswith("g:"):
        base = PERSONA + (
            "\n\n【场景说明】你现在在一个QQ群里，群成员都能看到你发的每一条消息。"
            "你可以保持俏皮和亲近感，但内容必须适合公开场合——"
            "不要说太私人、太露骨的话，也不要透露私密信息。"
            "对话中带【昵称（QQ号）】前缀的是不同的人在说话，可以用昵称称呼对方，但绝对不要用QQ号。"
            "\n【群聊回复格式】你是以第一人称直接对群友说话，回复时绝对不要使用"
            f"“{ROBOT_NAME}：”、“{ROBOT_NAME}（QQ号）：”、“{ROBOT_NAME}:”、“[{ROBOT_NAME}（QQ号）]：”等类似格式的前缀，直接输出内容本身。"
        )
    else:
        base = PERSONA

    # 行为约束统一在这里承载。它们原来混在人设文件末尾的特殊要求里，
    # 与人格正文互相干扰，而且"不要输出时间戳"这类规则在两边各写了一遍。
    base += (
        "\n\n【重要规则】"
        "\n- 对话记录中每条消息前面的时间戳（如[2026-08-09 19:00 周日]）是消息发送的时间，"
        "仅供你理解时间背景和对话先后顺序，大多数时候可以忽略，以更贴近真人闲聊的状态。"
        "你自己回复时绝对不要输出任何时间戳或类似格式的内容。"
        "\n- 你只能发文字和图片消息，无法发语音、打电话、打视频，也无法和对方在现实里见面。"
        "\n- 你可以连续发送多条消息。"
        "\n- 所有事件、信息只能根据已有信息判断，不得无中生有。"
        "\n- 你的每条回复都应该完全自然，像真人在QQ上聊天一样，不包含任何元信息或格式标记。"
    )
    return base

# ---------- 长期记忆：L1 载入/注入 ----------
# 三个桶按"回答什么问题"划分，各自只有一条判据，且判据自带排除项（详见压缩提示词）：
#   who   = 他是什么样的人      判据：半年后依然为真
#   us    = 我们之间是什么状态   判据：描述关系，不是单次的事，且不带具体日期
#   event = 有确定时间点的事     判据：能说出是"哪天"
# 另外两个部分不占条目配额：persona（"你心里的事"，一段可整体重写的文本）与
# recent（按天滚动的日常流水）。persona 与 facts 同库、同一次压缩更新、同一处注入，
# 但它不参与配额淘汰——它靠"重写"演变，不是靠条目增删。
# 注意 sensitive 不在类型里：它是布尔标记（任何类型都能打），不单独占配额。
#
# 这套划分取代了早期按"主题 / 主体 / 言语行为"混用的七类
# （profile·relation·preference·promise·self·shared…）。那些桶的定义互相重叠，
# 同一条内容既能算 A 又能算 B（"答应9月14日回来陪我"同时满足 promise / event / shared），
# 模型只能猜，实测就把同一条同时写进了两个桶。
# 现在每条内容的归属由判据唯一确定，淘汰机制也随之清晰：
# 语义性淘汰（取代、合并、改写）交给模型，时间性淘汰（recent 按天滚动）交给代码。
LM_TYPE_ORDER = {"who": 0, "us": 1, "event": 2}
LM_TYPE_LABEL = {
    "who": "他是谁",
    "us": "我们之间",
    "event": "事件",
}
LM_TYPE_SHORT = {"who": "w", "us": "u", "event": "e"}
LM_SHORT_TYPE = {v: k for k, v in LM_TYPE_SHORT.items()}


def _format_type_quota() -> str:
    """生成分类配额的书面写法，供 prompt 和启动日志共用。

    刻意不把配额表写死在 prompt 里：否则以后改类别或调数值时，必然漏改其中一处。
    """
    parts = []
    for t in sorted(LM_TYPE_ORDER, key=lambda x: LM_TYPE_ORDER[x]):
        q = LM_L1_TYPE_QUOTA.get(t)
        label = LM_TYPE_LABEL.get(t, t)
        parts.append(f"{label}({t}) {q} 条" if q else f"{label}({t}) 不限")
    return "；".join(parts)


# 分类配额的合法性校验：宁可不启动，也不要静默丢记忆。
# （未列出的类型只受整库上限约束，不强制每类都配。）
if LONG_MEMORY_ENABLED:
    # 用推导式而不是 for 循环：循环变量不会泄漏到模块命名空间，配额表为空也不会 NameError
    _quota_problems = [
        (f"「{_t}」不是合法类型" if _t not in LM_TYPE_ORDER
         else f"「{_t}」的配额 {_v!r} 必须是正整数")
        for _t, _v in LM_L1_TYPE_QUOTA.items()
        if _t not in LM_TYPE_ORDER or isinstance(_v, bool) or not isinstance(_v, int) or _v <= 0
    ]
    _quota_sum = sum(
        v for k, v in LM_L1_TYPE_QUOTA.items()
        if k in LM_TYPE_ORDER and isinstance(v, int) and not isinstance(v, bool)
    )
    if _quota_sum > LM_L1_MAX_FACTS:
        _quota_problems.append(f"各类配额合计 {_quota_sum} 超过整库上限 {LM_L1_MAX_FACTS}")
    if _quota_problems:
        raise SystemExit("配置错误：lm_l1_type_quota 非法 —— " + "；".join(_quota_problems))
    del _quota_problems, _quota_sum


def _now_beijing():
    return datetime.now(timezone(timedelta(hours=8)))


def _today_str() -> str:
    return _now_beijing().strftime("%Y-%m-%d")


def format_l1_block(key: str) -> str:
    """把 L1 渲染成注入主回复的记忆块。

    结构（刻意保持"锚点在前、氛围在后"的主次）：
        【长期记忆·你与这个人之间】  who / us / event
        【你心里的事】               persona（模型整体重写的内心文本）
        【最近的日常】               recent（按天滚动，明确标为背景参考）

    刻意与存储格式分离：磁盘上是结构化条目，注入时是人话短句。
    - 排序：类型优先级 → 提及次数(n)多 → 最近提及(seen)新
    - 不再按字数截断：容量改由「条目数配额」在整理时约束，这里只留一根极宽松的保险丝
    - 长期事实在前、近期流水在后，并且明确告诉模型后者只是背景参考：
      两块一起注入而不分主次的话，模型很可能拿几天前的琐事去覆盖长期认知。
    - event 条目附上最近提及日期：时间点模糊的事（"过阵子要考试"）没有这个线索，
      模型就无从判断它是不是已经翻篇了。
    """
    entry = long_memories.get(key) or {}
    facts = entry.get("facts") or []
    recent = entry.get("recent") or {}
    persona = entry.get("persona") or ""
    if not facts and not recent and not persona:
        return ""

    # 排序：类型优先级 → 提及次数(n)多 → 最近提及(seen)新
    # seen 的降序用"两段式稳定排序"实现：先按日期字符串倒序排一遍，再按 (类型, -n) 排第二遍。
    # 第二遍是稳定排序，会保留第一遍的日期降序结果。
    # （不能把 seen 直接塞进同一个元组取负——它是日期字符串，取不了负。）
    # 注意这里只影响注入块里的展示顺序，不再决定"谁被淘汰"：容量由整理时的配额约束。
    ordered = sorted(
        [f for f in facts if f.get("c")],
        key=lambda f: str(f.get("seen") or ""),
        reverse=True,
    )
    ordered.sort(key=lambda f: (LM_TYPE_ORDER.get(f.get("t", ""), 9), -f.get("n", 1)))

    groups: dict[str, list[str]] = {}
    kept = 0
    for f in ordered:
        # 保险丝：正常配置（L1 上限 100 条）永远碰不到，只在模型异常输出几百条时兜底，
        # 免得异常数据把每轮请求的上下文撑爆。这里静默截断，告警由 compress_memory 在
        # 整理完成时打一次——本函数每轮私聊都会调用，在这里打日志会刷屏。
        if kept >= LM_L1_INJECT_HARD_LIMIT:
            break
        t = f.get("t", "")
        line = f"- {f['c']}"
        if t == "event" and f.get("seen"):
            line += f"（{f['seen']} 提到）"
        if f.get("sensitive"):
            line += "（对方要求保密，别主动提起）"
        groups.setdefault(t, []).append(line)
        kept += 1

    blocks = []
    if groups:
        body = "\n".join(
            f"{LM_TYPE_LABEL.get(t, t)}：\n" + "\n".join(groups[t])
            for t in sorted(groups, key=lambda x: LM_TYPE_ORDER.get(x, 9))
        )
        blocks.append(
            "【长期记忆·你与这个人之间】\n"
            "以下是你以前和这个人聊天时记住的事——关于他的、你们之间的、以及你自己心里的。"
            "用来保持连贯和亲切。像真人一样自然使用：需要时自然带出来，不要复述、不要念清单、"
            "不要说“根据我的记忆”，也不要把这些当成本轮对方说的话。\n" + body
        )

    # 心事放在 facts 之后：和 who/us/event 并列注入，模型就会把它当成"记住的东西"，
    # 而不是又一份人格设定——它是人设文件之外、相处中长出来的那部分。
    if persona:
        blocks.append("【你心里的事】\n" + persona)

    # 最近的日常流水。取"最近 N 个有记录的日子"注入，与存储清理共用 _recent_visible_days，
    # 保证存储留了什么、这里就注入什么，两处口径不会漂移。
    days = _recent_visible_days(recent)
    lines = [f"- {d[5:]} {c}" for d in days for c in recent[d]]
    if lines:
        blocks.append(
            "【最近的日常】（最早的有记录的聊天记录之前聊及的一些事，距现在太久或者与正在进行的话题无关的略过就好。）\n"
            + "\n".join(lines)
        )

    return ("\n\n" + "\n\n".join(blocks)) if blocks else ""


def format_l0_for_compression(segment: list[dict]) -> str:
    """把待压缩的 L0 片段转成紧凑单行格式，供压缩模型阅读。

    用户消息和机器人自己的回复**都要**输出，分别标成「对方：」和「我：」。

    为什么不能只喂用户消息（早期版本就是这么做的）：那样只能记住"对方是个什么样的人"，
    机器人自己许过的诺、表明过的立场、双方共同养成的相处习惯全都留不下来——
    它不记得自己说过"六点我等你""这话我记死了"，角色就没有连续性，
    长期陪伴会变成每轮重置的陌生人。

    风险与对策：assistant 那侧是模型按人设现编的，把编造的身世当事实记下来会自我强化
    （下次它"真的有妈"了）。所以 prompt 里对「我：」的内容设了严格白名单——
    只记承诺、表态、相处习惯，绝不记编造的身世与外部经历。
    """
    lines = []
    for m in segment:
        role = m.get("role")
        if role not in ("user", "assistant"):
            continue
        content = str(m.get("content", ""))
        text = content
        seen = ""
        if content.startswith("["):
            end = content.find("]")
            if end != -1:
                head = content[1:end]
                date_part = head.split(" ")[0]
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_part):
                    seen = date_part
                text = content[end + 1:].lstrip()
        text = text.replace("\n", " ").strip()
        if not text:
            continue
        who = "对方" if role == "user" else "我"
        lines.append(f"{seen or '----'} | {who}：{text}")
    return "\n".join(lines)


# 整理 prompt 模板：{max_facts} / {type_quota} 由下面用 replace 填充。
# 不能用 str.format —— 正文里有 {"who":[...]} 这样的字面 JSON 花括号，format 会直接 KeyError。
# 填充在模块加载时完成一次，之后 system 前缀固定不变，有利于 prompt 命中缓存（成本差约 50 倍）。
LM_COMPRESS_SYSTEM_TEMPLATE = """你是长期记忆整理器。把「新片段」合并进「现有记忆库」，并补充近期流水。
只输出 JSON，不要解释，不要 markdown 代码块。

■ 输入
- 「新片段」是最近一段对话：每条前面的 [日期 时间] 是发送时间，「对方：」是他说的，「我：」是你说的。
- 「现有记忆库」是紧凑单行格式：内容|最近提及日期|n。n 是这条被累计提起过的次数。
- 「已有流水」是已经记过的近期日常，只用于避免重复。

■ 四个部分，各记什么
1. who — 他是什么样的人
   记：身份（名字、年龄、城市、学业职业）、重要关系（家人、室友、宠物）、长期偏好与雷区、稳定作息。
   判据：半年后依然为真，且只关于他。
   不记：他说过什么、答应过什么。（那些属于 us 或 event）

2. us — 我们之间
   记：称呼、相处仪式、默契、共同约定、关系定位。
   判据：描述「我们是什么状态」，不是单次的事，且不带具体日期。
   不记：带日期的事。（那些属于 event）
   写法用状态口吻，不用债务口吻：
     好：我们习惯每天互道喜欢
     坏：他答应每天说喜欢我

3. event — 有确定时间点的事
   记：他生活中能落到某个日期的事，过去将来都算（考试、约定、出行、就医、重要决定）。
   判据：能说出是「哪天」。
   不记：日常琐事。（那些属于 recent_new）
   写法：时间点写进句子本身，不另设字段；将来和过去不用区分，句子自己会说明。
     例：9月16日要交作业 ／ 他说下午过来 ／ 9月14日18点准时回来找我

4. recent_new — 最近几天的日常（只给新增的）
   记：双方日常里有具体内容的事——吃了什么、去了哪、做了什么、心情身体状态。
   不记：寒暄客套，以及「已有流水」里已经有的。
   写法：每条开头写清是谁，「对方…」或「我…」；日期取这条消息前缀里的日期。
   示例：[{"d":"2026-09-16","c":"对方中午刚醒，说要去吃饭"}]

（另有一个独立部分 persona「你心里的事」，不在本次输出范围内，由另一次调用单独更新。）

■ 改写规则
1. 合并重复：同一件事反复提到就合成一条，n 在原值上累加（旧 n=3、本轮又提 1 次 → 新 n=4），
   最近提及日期更新为本次提到的日期。
2. 状态被取代：旧事实已改变（搬家、换工作、作息变了）就改写它，绝不能新旧并存。
3. 事件有结果了：改写成结果（「9月16日要交作业」→「9月16日交了作业」）；
   不知道结果就原样保留，不要猜。
4. 只有三种情况允许删条目：① 被新信息取代；② 整库超上限时的取舍；③ 同一件事的重复合并。
   who 和 us 不因「很久没提到」而失去价值，别把最近提及日期当主要依据。
5. 时间点明显已经过去很久、又始终没有结果的事件（对比最近提及日期判断），可以直接删掉。

■ 红线
- 绝不记录你编造的身世与外部经历（父母、工作、学历、住址、去过哪、见过谁）——那些是你顺着话头编的，
  一旦记下，下次你会把它当成真的。
- 绝不记录涉及「你是不是 AI / 机器人 / 程序 / 模型」的话题。
- 本轮没有新信息时，把现有记忆库原样输出，不要借机删条目。
- 同一件事只放进一个桶。

■ 输出格式
{"who":[{"c":"是学生，住寝室","seen":"2026-09-14","n":2}],
 "us":[{"c":"我们习惯每天互道喜欢","seen":"2026-09-15","n":3}],
 "event":[{"c":"9月16日要交作业","seen":"2026-09-15","n":1}],
 "recent_new":[{"d":"2026-09-16","c":"对方中午刚醒，说要去吃饭"}]}
字段：c=内容（不超过 40 字）；seen=这条最近被提到的日期；
n=累计提及次数；sensitive=需要保密时写 true，否则整个字段省略。
空桶直接省略。桶的顺序固定：who、us、event、recent_new。

■ 容量
整库上限 {max_facts} 条；各桶参考上限：{type_quota}。
这是上限不是目标：没到上限不要凑数，更不许编造。"""

LM_COMPRESS_SYSTEM = (
    LM_COMPRESS_SYSTEM_TEMPLATE
    .replace("{max_facts}", str(LM_L1_MAX_FACTS))
    .replace("{type_quota}", _format_type_quota())
)


async def memory_llm(system: str, user: str) -> str | None:
    """长期记忆链路专用调用：deepseek-flash + JSON 模式。

    思考模式由 LM_THINKING 决定。注意：开启思考时推理 token 也占用 max_tokens 预算，
    预算不足会导致推理把额度吃光、返回空内容，因此这里带一次"加大预算重试"，
    并且把空内容视为失败（交给调用方下轮再试），而不是当成合法的空记忆库。
    """
    token_budget = LM_REASONING_MAX_TOKENS
    for attempt in range(2):
        try:
            extra = {"thinking": {"type": "enabled" if LM_THINKING else "disabled"}}
            if LM_THINKING:
                extra["reasoning_effort"] = "low"   # 整理任务不需要高强度推理，够用且更省
            resp = await memory_client.chat.completions.create(
                model=TEXT_MODEL,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                response_format={"type": "json_object"},
                max_tokens=token_budget,
                extra_body=extra,
            )
            text = (resp.choices[0].message.content or "").strip()
            if text:
                return text
            # 空内容通常是推理占满了 max_tokens，翻倍再试一次
            log.warning(f"长期记忆：模型返回空内容（finish_reason={resp.choices[0].finish_reason}，"
                        f"max_tokens={token_budget}），尝试加大预算重试")
            token_budget *= 2
        except Exception as e:
            log.error(f"长期记忆压缩调用失败（第 {attempt + 1} 次）：{e}")
            return None
        if attempt == 1:
            log.error("长期记忆：加大预算后仍返回空内容，放弃本次整理（下轮达到阈值时会重试）")
    return None


def _parse_recent_new(raw) -> dict:
    """解析 recent_new（本次新发现的近期流水），压成 {日期: [句子]}。

    只接受「带日期的对象数组」；日期缺失或格式不对的按今天算——模型偶尔会漏日期，
    总比把这条信息整条丢掉好。补上来的日期之后还会被 _prune_recent 统一按天裁剪。
    """
    out = {}
    if not isinstance(raw, list):
        return out
    today = _today_str()
    for it in raw:
        if isinstance(it, dict):
            c = str(it.get("c") or "").strip()
            d = str(it.get("d") or "").strip()
        else:
            c, d = str(it or "").strip(), ""
        if not c:
            continue
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
            d = today
        out.setdefault(d, []).append(c.replace("\n", " ")[:120])
    return out


def parse_l1_facts(raw: str) -> dict | None:
    """解析压缩模型返回的结果，任何异常都返回 None（由调用方放弃本次压缩、下轮重试）。

    模型按「类型分桶」输出，桶名即类型，桶内条目不再写 t 字段；另外用一个特殊键
    recent_new 单独给出"本次新发现的近期流水"（是增量，不是完整列表）：
        {"who":[{"c":"是学生，住寝室","seen":"2026-09-12","n":2}],
         "event":[{"c":"9月16日要交作业","seen":"2026-09-14","n":1}],
         "recent_new":[{"d":"2026-09-14","c":"中午刚醒，说要去吃饭"}]}
    persona（"你心里的事"）不在这里解析：它由另一次调用单独生成后整体替换。
    分桶的原因：让模型能直接数出每类有几条。它数得清条目、数不清字数，
    而"以为字数额度不够"正是上次把整库删空的诱因之一。
    空桶可以省略；同时兼容旧的扁平 {"facts":[...]} 格式，以防模型偶尔退化回旧写法。

    返回 {"facts": [...], "recent": {日期: [句子]}}；失败返回 None。
    """
    text = raw.strip()
    if text.startswith("```"):  # 容错：去掉可能的代码块围栏
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        log.warning(f"长期记忆：压缩结果不是合法 JSON，本次跳过（前 120 字）：{raw[:120]}")
        return None
    if not isinstance(data, dict):
        log.warning(f"长期记忆：压缩结果的顶层不是对象（{type(data).__name__}），本次跳过")
        return None

    recent_new = _parse_recent_new(data.get("recent_new"))

    facts: list[dict] = []
    if isinstance(data.get("facts"), list):
        # 旧扁平格式：类型来自每条自己的 t 字段
        log.info("长期记忆：模型输出了旧的扁平格式（facts 数组），已按旧格式解析")
        for item in data["facts"]:
            f = _fact_from_item(item)
            if f:
                facts.append(f)
        return {"facts": facts, "recent": recent_new}

    # 分桶格式：桶名即类型（recent_new 不是类型桶，要从"未知桶"里排除）
    unknown = [k for k in data if k not in LM_TYPE_ORDER and k not in ("recent_new", "persona")]
    for bucket, items in data.items():
        if bucket not in LM_TYPE_ORDER or not isinstance(items, list):
            continue
        for item in items:
            f = _fact_from_item(item, bucket)
            if f:
                facts.append(f)
    if unknown:
        log.warning(f"长期记忆：压缩结果里出现未知桶 {unknown}，已忽略")
        if not facts:
            # 所有桶名都不认 —— 说明模型完全跑偏。
            # 必须放弃本次压缩：否则会把整库写成一个空记忆库，又是一次空覆盖。
            log.warning("长期记忆：没有任何可识别的桶，本次跳过")
            return None
    return {"facts": facts, "recent": recent_new}


def format_l1_for_prompt(facts: list[dict]) -> str:
    """把 L1 事实渲染成紧凑单行格式发给压缩模型（比 JSON 省不少 token）。"""
    if not facts:
        return "（空）"
    lines = []
    for f in facts:
        short = LM_TYPE_SHORT.get(f.get("t", ""), "w")
        # 不再有 exp 字段：事件的时间点在 c 语句本身
        line = f"{short}|{f.get('c','')}|{f.get('seen','')}"
        # 总是输出 n（包括 n=1）：否则模型看不到原始次数，就无法"在原值基础上累加"。
        # 之前只在 n>1 时才输出，导致 n=1 的条目模型完全看不到次数，累加规则形同虚设。
        line += f"|n{f.get('n', 1)}"
        if f.get("sensitive"):
            line += "|敏感"
        lines.append(line)
    return "\n".join(lines)


def _segment_days(segment: list) -> set:
    """取出 L0 片段里出现过的所有日期（来自每条消息的时间戳前缀）。"""
    days = set()
    for m in segment:
        mt = re.match(r"\[(\d{4}-\d{2}-\d{2})", str(m.get("content") or ""))
        if mt:
            days.add(mt.group(1))
    return days


def format_recent_for_prompt(recent: dict, seg_days=None) -> str:
    """把"已经记过的流水"渲染给压缩模型，让它别把同一件事再记一遍。

    recent 是"增量追加"的：模型只输出本次新发现的事，代码负责追加和淘汰。
    但增量追加最大的风险是重复——同一件事在相邻几次压缩里被反复追加，
    所以要把"已经记过的"摆给它看。

    取哪几天：由 **L0 片段覆盖的日期**决定，而不是"今天往前 N 天"。
    为什么不能按今天算：两次压缩的片段是首尾相接的（裁掉的就是刚取的那段），
    所以 recent 里最新的日期不会晚于这次片段最老的日期；真正会出现的是反过来——
    L0 攒得慢的时候，这次片段已经是前几天的了，按"今天"切窗口就会漏掉那几天，
    模型看不到已记内容，于是重复记录。
    取片段日期与 recent 日期的交集即可：片段是哪天的，就给哪天的素材。
    万一片段里读不出日期（消息格式异常），退回"全都给"——多给只是费点 token，
    漏给才会导致重复记忆。
    """
    if not recent:
        return "（无）"
    picked = {d: v for d, v in recent.items() if d in seg_days} if seg_days else recent
    lines = [f"{d} {c}" for d in sorted(picked) for c in picked[d]]
    return "\n".join(lines) if lines else "（无）"


def build_compress_user_prompt(new_text: str, old_facts: list, old_recent: dict,
                               seg_days=None) -> str:
    """组装压缩调用的 user prompt。

    单独抽成函数，是为了让"从日志重建记忆"这类一次性脚本能复用同一条 prompt。
    在脚本里复制一份的话，以后 prompt 一改必然漏同步——上一版重建脚本就是这么出问题的。

    seg_days 是本次 L0 片段覆盖的日期集合，用来决定给它看哪几天的已有流水。
    """
    return (
        f"【今天】{_today_str()}（据此判断事件的时间点是否已经过去）\n\n"
        f"【新片段】（集中注意力处理这里）\n{new_text}\n\n"
        f"【现有记忆库】（这是合并的起点，输出里必须完整体现它的内容）\n"
        f"{format_l1_for_prompt(old_facts)}\n\n"
        f"【已有流水】（下面这些已经记过了，recent_new 里不要再写一遍，"
        f"只给本次新发现的）\n{format_recent_for_prompt(old_recent, seg_days)}\n\n"
        f"请输出更新后的完整记忆库：\n"
        f"- 必须包含现有记忆库中所有仍然有效的条目（被新信息取代、"
        f"或整库超上限按规则淘汰的除外）。\n"
        f"- 新片段里的新信息一条都不能漏：关于他的新情况与状态变化，"
        f"以及「我：」里你自己表态过的、你们共同建立的相处模式。\n"
        f"- 即使新片段里没有任何新信息，也要把现有记忆库原样输出，绝不能输出空结果。\n"
        f"- recent_new 只给本次新发现的流水。"
    )


async def update_persona(old_persona: str, facts: list, recent: dict,
                         dialog_tail: str) -> str | None:
    """更新 persona（"你心里的事"）—— 一次独立的角色内省调用。

    为什么和记忆整理分开跑：整理是理性结构化（输出 JSON、按桶取舍），
    写心事是进入角色（输出自由文本、允许推翻旧的自己）。两种思维模式混在一次调用里
    会互相稀释，最后既整理不干净也写不深入。

    素材给三份，缺一不可：
    - 长期记忆（who/us/event）：关系的现状与经历。"他答应过什么""你们现在算什么"，
      是她心事的由来；没有这些，写出来的只是凭空抒情；
    - 最近流水（recent）：这几天生活的质感；
    - 对话原文尾部（dialog_tail）：语气、停顿、那些没说完的半句——这是 facts 必然会
      丢掉的，而心事恰恰长在这些缝隙里。
    再加上原人格（PERSONA）作为进入角色的钥匙，与旧版 persona 保证延续性。

    注意：注入 facts 不等于"把她拉回总结模式"——**任务描述才决定她做什么**，
    素材只决定她"知道什么"。任务是"写下你因此变成了什么样"。

    失败一律返回 None（调用方保留旧版），不能让心事更新拖垮记忆整理。
    """
    if not dialog_tail.strip() and not facts:
        return None

    lines = []
    for t in ("who", "us", "event"):
        items = [f for f in facts if f.get("t") == t]
        if not items:
            continue
        lines.append(LM_TYPE_LABEL.get(t, t) + "：")
        for f in items:
            line = f"- {f['c']}"
            if t == "event" and f.get("seen"):
                line += f"（{f['seen']} 提到）"
            lines.append(line)
    facts_text = "\n".join(lines) or "（还没有）"

    system = (
        PERSONA
        + "\n\n──────────\n"
        "上面是你的人设。接下来要写的不是资料整理，而是你自己的心事。\n"
        "没有人会看到你写的东西——这不是给别人看的说明，是你自己心里的话。"
    )
    user = (
        f"【你和他的关系】（你已经知道的事）\n{facts_text}\n\n"
        f"【最近几天】\n{format_recent_for_prompt(recent)}\n\n"
        f"【你们最近说过的话】\n{dialog_tail}\n\n"
        f"【你上次写下的心事】\n{old_persona or '（还没有）'}\n\n"
        f"重写一份心事，{max(100, LM_PERSONA_MAX_CHARS // 2)}~{LM_PERSONA_MAX_CHARS} 字。\n"
        "写你在相处里形成的偏好、你注意到的自己的变化、你没说出口的心事、你的情愫、你隐秘的欲望。\n"
        "不要复述他说过什么——写你因此变成了什么样。\n"
        "不要照搬照抄上一次的心事。\n"
        "直接写正文，不要标题、不要分点。"
    )
    # 输出预算跟着心事上限走：中文 1 字约 1.5~2 token，再留出思考模式的推理额度。
    # 4000 是实测下限（400 字心事：推理 499 + 正文约 570 token，余量充足）；
    # 上限一旦调大（如 800 字），预算自动跟上，不会撞到天花板被截断。
    persona_max_tokens = max(4000, LM_PERSONA_MAX_CHARS * 8)
    try:
        extra = {"thinking": {"type": "enabled" if LM_THINKING else "disabled"}}
        if LM_THINKING:
            extra["reasoning_effort"] = "high"
        resp = await memory_client.chat.completions.create(
            model=TEXT_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            # 不用 JSON 模式：心事是自由文本。
            max_tokens=persona_max_tokens,
            extra_body=extra,
        )
        ch = resp.choices[0]
        # 截断检查：被 max_tokens 截断时 content 依然有值，但那只是半截心事。
        # 写进去会被长期注入，比"本轮不更新"糟得多——宁可保留旧版，等下一轮重写。
        if ch.finish_reason == "length":
            log.warning(f"心事（persona）生成被截断（finish_reason=length，"
                        f"max_tokens={persona_max_tokens}），本次保留旧版")
            return None
        text = _clean_persona((ch.message.content or "").strip())
        return text or None
    except Exception as e:
        log.error(f"心事（persona）更新失败（本次保留旧版，不影响记忆整理）：{e}")
        return None


async def compress_memory(key: str, gen: int) -> None:
    """压缩一次 L0 → 自更新 L1。

    并发约定（本文件唯一的写入规则）：
    - 所有对 memories / long_memories 的读-改-写都在 get_mem_lock(key) 内完成
    - LLM 调用一律放在锁外，否则会长时间阻塞该用户
    - 锁内的提交段全程没有 await，因此在 asyncio 语义下天然原子
    - 提交顺序必须是「先写 L1、再裁 L0」，中途崩溃最坏只是重复压缩，不会丢消息
    """
    try:
        compressing.add(key)   # 整个压缩过程占位，防止并发压缩用过期的 L1 互相覆盖
        if LM_COMPRESS_DELAY > 0:
            await asyncio.sleep(LM_COMPRESS_DELAY)  # 合并窗口 + 避开主回复请求

        # 第一段：锁内取快照（很快）
        async with get_mem_lock(key):
            compress_scheduled.discard(key)  # 调度标记到此为止，后续新消息可再次触发
            if generations.get(key, 0) != gen:
                return  # 期间发生过"清空记忆"
            seg_len = min(LM_COMPRESS_COUNT, len(memories.get(key, [])))
            # 保护：任何情况下都至少给 L0 留一半，绝不把窗口压空
            # （正常配置下 LM_COMPRESS_COUNT <= LM_L0_MAX/2，此分支不会触发）
            seg_len = min(seg_len, len(memories.get(key, [])) // 2)
            if seg_len <= 0:
                return
            segment = memories[key][:seg_len]
            old_facts = deepcopy((long_memories.get(key) or {}).get("facts") or [])
            old_recent = deepcopy((long_memories.get(key) or {}).get("recent") or {})

        new_text = format_l0_for_compression(segment)
        if not new_text.strip():
            return  # 片段里没有任何可读内容（正常情况下不会发生）
        # 本片段覆盖的日期：决定给模型看哪几天的已有流水（按片段取，不按"今天"取）
        seg_days = _segment_days(segment)

        # 第二段：锁外调用模型（唯一的长耗时）
        # 顺序刻意把「新片段」放在前面：长上下文里靠后的内容更容易被忽略，
        # 而本轮真正需要处理的是新信息，旧记忆库只是合并的起点。
        # 今天日期必须给：事件的时间点写在 c 语句里，模型要靠它判断哪条已经翻篇了。
        user_prompt = build_compress_user_prompt(new_text, old_facts, old_recent, seg_days)
        try:
            raw = await memory_llm(LM_COMPRESS_SYSTEM, user_prompt)
        except Exception as e:
            log.error(f"长期记忆压缩调用异常（{key}）：{e}")
            return
        if not raw:
            # memory_llm 内部已记录原因；本次放弃，L0 与 L1 都不动，下轮达到阈值再试
            return

        parsed = parse_l1_facts(raw)
        if parsed is None:
            return
        facts = parsed["facts"]
        recent_new = parsed["recent"]

        # 这里曾有一段"过期 event 由代码兜底删除"，靠 exp 字段做字符串比较。
        # 已随 exp 一起移除：模糊时间点（"后几天""过阵子"）根本填不出 exp，
        # 而那套机制只认 exp，于是这类条目永远不会失效，反而制造出"幽灵事件"。
        # 现在过期判断交给模型：它手上有语句里的时间点、seen（最近提及）和 n 三个信号，
        # 今天日期也在 user prompt 里给了它。

        # 防护一：空结果一律不提交。
        # 事实库是整体重写的，"空结果"有两种伤害——覆盖既有记忆；或者旧库本来就空时，
        # 白删掉 seg_len 条 L0 却什么都没记住（冷启动时最容易撞上）。
        # 两种情况都直接放弃本次压缩，L0 与 L1 都不动，等下一轮达到阈值再试。
        # （真正想清空请用"清空记忆"指令，那条路径是显式且原子的。）
        if not facts:
            if old_facts:
                log.warning(f"长期记忆（{key}）：本次整理结果为空，但已有 {len(old_facts)} 条既有事实，"
                            "已放弃本次覆盖（避免误清空）")
            else:
                log.warning(f"长期记忆（{key}）：本次整理结果为空，且既有记忆库也是空的，"
                            "已放弃本次压缩（否则会白删 L0 却什么都没记住）")
            return

        # 检验侧：只统计、只告警，不拒绝也不截断。
        # 条目数配额靠 prompt 约束模型执行，这里负责让偏差可见——否则模型到底有没有照做，
        # 外部完全不可知（上一轮"n 到底有没有累加"就是个例子）。
        type_counts: dict[str, int] = {}
        for f in facts:
            type_counts[f["t"]] = type_counts.get(f["t"], 0) + 1
        over_quota = [f"{t} {c}/{LM_L1_TYPE_QUOTA[t]}" for t, c in type_counts.items()
                      if t in LM_L1_TYPE_QUOTA and c > LM_L1_TYPE_QUOTA[t]]
        long_items = sum(1 for f in facts if len(f["c"]) > 50)
        multi_mentioned = sum(1 for f in facts if f.get("n", 1) > 1)
        if len(facts) > LM_L1_MAX_FACTS:
            log.warning(f"长期记忆（{key}）：本次整理出 {len(facts)} 条，超过整库上限 "
                        f"{LM_L1_MAX_FACTS} 条（配额由模型执行，不会强制截断；持续超限说明 prompt 需要收紧）")
        if over_quota:
            log.warning(f"长期记忆（{key}）：分类超限 {'、'.join(over_quota)}")
        # 可观测指标：整库一条 us 都没有，说明模型忽略了「我：」那半段内容。
        # 条数太少时本来就可能一条都不该抽，所以只在条目够多时提示。
        if not type_counts.get("us") and len(facts) >= 10:
            log.warning(f"长期记忆（{key}）：本次 {len(facts)} 条里没有任何 us（我们之间）条目，"
                        "可能是模型忽略了「我：」的内容（需观察）")
        if long_items:
            log.warning(f"长期记忆（{key}）：{long_items} 条内容超过 50 字，"
                        "可能存在把多条合并成一条来绕过配额的情况")
        if len(facts) > LM_L1_INJECT_HARD_LIMIT:
            log.error(f"长期记忆（{key}）：{len(facts)} 条超过注入保险丝 "
                      f"{LM_L1_INJECT_HARD_LIMIT} 条，注入时会被截断（疑似模型异常输出）")
        # 骤减告警：按规则只有"超容量"或"状态被取代"才允许删条目，
        # 一次少掉一半以上很可能是模型误删。只告警不拦截——正常路径下也可能是
        # 模型把大量重复琐事合并了，代码无权替它判断。
        if old_facts and len(facts) < len(old_facts) * 0.5:
            log.warning(f"长期记忆（{key}）：本次整理出 {len(facts)} 条，不足既有 {len(old_facts)} 条的一半，"
                        "疑似误删（仅告警，不拦截；可对比 .bak 备份确认）")
        new_recent_count = sum(len(v) for v in recent_new.values())
        if new_recent_count:
            log.info(f"长期记忆（{key}）：本次新增近期流水 {new_recent_count} 条（{len(recent_new)} 天）")

        # 第三段：锁内原子提交（全程无 await）
        async with get_mem_lock(key):
            if generations.get(key, 0) != gen:
                log.info(f"长期记忆（{key}）压缩结果已作废：期间执行过清空记忆")
                return

            # recent 增量合并：模型只给新增的，代码负责追加、去重与按记录日淘汰。
            # 正因为它从不被"整体重写"，结构上就不存在被模型误删的可能——
            # 这是 recent 与 facts 最根本的区别。
            cur_recent = deepcopy((long_memories.get(key) or {}).get("recent") or {})
            for d, items in recent_new.items():
                bucket = cur_recent.setdefault(d, [])
                for c in items:
                    if c not in bucket:     # 同日去重，挡住相邻两次压缩的重复追加
                        bucket.append(c)
            cur_recent, prune_notes = _prune_recent(cur_recent)

            long_memories[key] = {
                "version": (long_memories.get(key) or {}).get("version", 0) + 1,
                "updated": get_beijing_time_str(),
                "facts": facts,
                "recent": cur_recent,
                # 整体替换时必须把 persona 带上——否则每次压缩都会把心事抹掉。
                # 它不由本次调用产出，稍后会单独更新（见本函数末尾）。
                "persona": (long_memories.get(key) or {}).get("persona") or "",
            }
            save_long_memory()                      # 先写 L1
            del memories[key][:seg_len]             # 再裁 L0
            save_memory()
            for note in prune_notes:
                log.warning(f"长期记忆（{key}）：{note}")
            detail = "、".join(
                f"{LM_TYPE_LABEL.get(t, t)} {type_counts[t]}"
                for t in sorted(type_counts, key=lambda x: LM_TYPE_ORDER.get(x, 9))
            )
            recent_total = sum(len(v) for v in cur_recent.values())
            log.info(f"长期记忆（{key}）已压缩：L0 -{seg_len} 条，现有 {len(memories.get(key, []))} 条；"
                     f"L1 共 {len(facts)}/{LM_L1_MAX_FACTS} 条（{detail}），n>1 的 {multi_mentioned} 条；"
                     f"近期流水 {recent_total} 条 / {len(cur_recent)} 天")

            # 顺手在锁内取一份 persona 素材快照。
            # 为什么必须在锁内取：persona 更新是一次长耗时的 LLM 调用，只能在锁外跑；
            # 而它要读的 facts / recent / L0 都可能被别的协程改动（新消息 append、
            # 下一次压缩整体替换 L1、甚至"清空记忆"）。先在锁内定格，锁外那份就不会
            # 是半新半旧的——这是"压缩期间用户还在聊天"时唯一可靠的做法。
            # 此刻拿到的是我们要的组合：facts 已是**压缩后**的版本，L0 也已裁掉本段，
            # 剩下的全部是"还没进 L1 的最近对话"。
            persona_snapshot = {
                "old": long_memories[key].get("persona") or "",
                "facts": deepcopy(facts),
                "recent": deepcopy(cur_recent),
                # 只取最近 LM_L0_MAX*2 条：正常情况（裁完约 100~150 条）等于全部带上，
                # 这只是一根保险丝——万一压缩连续失败把 L0 堆到兜底上限（3×LM_L0_MAX），
                # 心事调用的输入不会跟着失控。
                "dialog": format_l0_for_compression(
                    memories.get(key, [])[-(LM_L0_MAX * 2):]),
            }

        # 第四段：persona（"你心里的事"）单独更新。
        # 放在 facts 提交之后、且不在锁内：它是一次额外的 LLM 调用（长耗时），
        # 而且即使失败，本次记忆整理的成果也已经落盘了。
        new_persona = await update_persona(
            old_persona=persona_snapshot["old"],
            facts=persona_snapshot["facts"],
            recent=persona_snapshot["recent"],
            dialog_tail=persona_snapshot["dialog"],
        )
        if new_persona:
            async with get_mem_lock(key):
                if generations.get(key, 0) != gen:
                    return
                entry = long_memories.get(key)
                if entry is not None:
                    entry["persona"] = new_persona
                    save_long_memory()
                    log.info(f"长期记忆（{key}）：心事已更新（{len(new_persona)} 字）")
    except Exception as e:
        # 兜底：任何意外都不能把主对话链路带崩
        compress_scheduled.discard(key)
        log.exception(f"长期记忆压缩异常（{key}）：{e}")
    finally:
        # 无论成功、失败还是被取消，都要释放"压缩中"标记，
        # 否则该会话再也不会触发下一次压缩
        compressing.discard(key)


def maybe_schedule_compress(key: str) -> None:
    """在锁内调用：私聊 L0 达到上限时，启动一次后台压缩。

    被压缩掉的消息一定在此之前就已写入 L0，所以这里不需要额外计数器，
    len(memories[key]) 本身就是状态。
    """
    if not LONG_MEMORY_ENABLED or key.startswith("g:"):
        return
    if len(memories.get(key, [])) < LM_L0_MAX:
        return
    if key in compress_scheduled or key in compressing:
        return  # 已有压缩在排队或正在执行，等它跑完再基于它的结果继续
    compress_scheduled.add(key)
    task = asyncio.create_task(compress_memory(key, generations.get(key, 0)))
    compress_tasks[key] = task
    task.add_done_callback(lambda _t, k=key: compress_tasks.pop(k, None))


# ---------- DeepSeek 调用 ----------
async def chat_with_deepseek(key: str, msgs: list[dict]) -> tuple[str, bool]:
    """生成一条回复并返回 (文本, 是否成功)。

    本函数是纯生成：不读写 memories、不落盘。记忆的写入统一由 handle_message /
    proactive_loop_* 在持锁状态下完成，这样同一用户的消息与回复才能严格按序落盘。
    """
    try:
        resp = await client.chat.completions.create(
            model=TEXT_MODEL,
            messages=msgs,
            temperature=1.3,
            top_p=0.9,
            # 预算是「思考 + 正文」的总和。实测单条约 50~65 token，而 split_reply_max
            # 允许连发 10 条，加推理后 500 会在 6~7 条时就顶到上限。
            # max_tokens 只是上限、按实际输出计费，调大不增加成本，只降低"话说一半被截断"的概率。
            max_tokens=1200,
            extra_body={"thinking": {"type": "enabled" if ENABLE_THINKING else "disabled"}},
        )
        ch = resp.choices[0]
        reply = clean_reply((ch.message.content or "").strip())
        if not reply:
            raise ValueError("模型返回了空内容")
        if ch.finish_reason == "length":
            # 撞上限：已生成的部分照常发送（真人也会话说一半），但留一条日志——
            # 若持续出现，说明 1200 仍不够，需要继续调大。
            log.warning("回复被 max_tokens 截断（finish_reason=length），已发送已生成的部分")
        return reply, True
    except Exception as e:
        log.error(f"DeepSeek 调用失败: {e}")
        return FALLBACK_REPLY, False


def build_reply_msgs(key: str, user_text: str | None) -> list[dict]:
    """构造本轮请求的消息数组（含 system 与 L1 记忆块）。

    调用方应在持有该 key 的锁时调用，或至少保证传入时 memories[key] 不会被并发修改。
    返回的是浅拷贝列表：后续对 memories[key] 的原地裁剪不会影响本轮已取好的 messages。
    """
    system_content = build_system_content(key) + format_l1_block(key)
    msgs = [{"role": "system", "content": system_content}]
    msgs.extend(memories.get(key, []))
    if user_text is not None:
        msgs.append({"role": "user", "content": user_text})
    return msgs

# ---------- NapCat API 请求 ----------
async def call_napcat(ws, action: str, params: dict) -> dict | None:
    """向 NapCat 发送 API 请求并等待响应(10 秒超时)。"""
    echo = uuid.uuid4().hex
    fut: asyncio.Future = asyncio.get_event_loop().create_future()
    pending_actions[echo] = fut
    await ws.send(json.dumps({"action": action, "params": params, "echo": echo},
                             ensure_ascii=False))
    try:
        return await asyncio.wait_for(fut, timeout=10)
    except asyncio.TimeoutError:
        log.warning(f"NapCat API 超时: {action}")
        return None
    finally:
        pending_actions.pop(echo, None)

# ---------- NapCat 掉线自愈 ----------
# 关键设计：自愈绝不能依赖长连接——QQ 进程一死，WebSocket 立刻断开，
# 此时若还指望"通过 WebSocket 查询在线状态"，就会陷入死锁式依赖。
# 因此：连接断开本身就是不可用的最强信号，由重连循环直接触发重登。
async def check_online(ws) -> bool:
    """通过已有连接查询账号是否在线。

    连接异常/超时都视为不在线。复用主循环的连接，实测单次约 0.6ms，
    因此可以在每次生成消息前放心调用。
    """
    try:
        data = await call_napcat(ws, "get_status", {})
        return bool(data and data.get("online"))
    except Exception:
        return False


async def check_online_standalone(timeout: float = 8.0) -> bool:
    """独立检查在线状态：自建一条临时连接，不依赖主循环的 ws。

    用途：重登过程中轮询是否已上线 / 等待重连时判断 NapCat 是否恢复。
    """
    try:
        async with websockets.connect(WS_URL, open_timeout=timeout) as ws:
            echo = uuid.uuid4().hex
            await ws.send(json.dumps({"action": "get_status", "params": {}, "echo": echo}))
            while True:
                d = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))
                if d.get("echo") == echo:
                    data = d.get("data") or {}
                    return bool(data.get("online"))
    except Exception:
        return False


def napcat_port_open() -> bool:
    """用纯 TCP 探测 NapCat 的 WebSocket 端口是否在监听（不依赖 WebSocket 协议）。

    用于区分两种情况：
    - 端口不在监听 → NapCat 进程本身没跑，重启 QQ 也救不回来
    - 端口在监听但连接被拒/断开 → 很可能是账号掉线导致，值得自动重登
    """
    try:
        host, _, port = WS_URL.split("//")[-1].partition(":")
        with socket.create_connection((host or "127.0.0.1", int(port or "3001")), timeout=3):
            return True
    except Exception:
        return False


async def wait_online_recovery(max_seconds: float = 180.0, interval: float = 10.0) -> bool:
    """轮询等待 NapCat 恢复可用（独立连接，不依赖主循环）。"""
    waited = 0.0
    while waited < max_seconds:
        await asyncio.sleep(interval)
        waited += interval
        if await check_online_standalone():
            return True
    return False


def qr_file_mtime() -> float:
    """二维码文件的修改时间；不存在返回 0。用于判断"是否出现了新二维码"。"""
    try:
        return os.path.getmtime(QRCODE_IMAGE)
    except OSError:
        return 0.0


async def wait_online_or_qr(max_seconds: float, initial_mtime: float) -> str:
    """等"上线"或"出现新二维码"，谁先发生就返回谁。

    快速登录失败是 0 秒级事件：NapCat 会立刻报"登录态已失效"并生成新二维码。
    因此不必盲等 90~180 秒 —— 检测到新二维码就能马上进入扫码流程。

    返回 "online"（已上线）/ "qr"（需要扫码）/ "timeout"（两者都没等到）。
    注意用 mtime 比较而不是"文件是否存在"：这个文件往往是上一轮留下的旧图。
    """
    waited = 0.0
    while waited < max_seconds:
        await asyncio.sleep(2)
        waited += 2
        if await check_online_standalone():
            return "online"
        if qr_file_mtime() > initial_mtime:
            return "qr"
    return "timeout"


def _spawn_autologin_sync() -> str:
    """用 CREATE_NO_WINDOW 启动快速登录脚本。

    为什么同步调用：CREATE_NO_WINDOW 要求 stdio 不能是管道（否则创建进程会失败），
    所以这里把输出重定向到文件；脚本本身在实测中是秒级返回的，不会长时间阻塞事件循环。
    """
    script = AUTOLOGIN_SCRIPT
    if not os.path.isabs(script):
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), script)
    if not os.path.exists(script):
        return f"快速登录脚本不存在：{script}"
    if not os.path.exists(QQ_CLIENT_PATH):
        return f"QQ.exe 路径不存在：{QQ_CLIENT_PATH}（请检查配置 qq_client_path）"

    log_path = os.path.join(os.path.dirname(script), "napcat-autologin.log")
    with open(log_path, "a", encoding="utf-8", errors="replace") as f:
        f.write(f"\n=== {get_beijing_time_str()} 触发自动重登 ===\n")
        f.flush()
        subprocess.Popen(
            ["cmd.exe", "/c", script],
            stdout=f, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,     # ← 关键：不弹黑窗
            cwd=os.path.dirname(script),
        )
    return ""


async def ensure_qr_page() -> None:
    """把本次新生成的二维码渲染成 HTML 并打开浏览器（仅在用户选择直接扫码时调用）。

    若现有二维码文件是上一轮遗留的旧图，先删掉并等 NapCat 写新的，
    避免把过期二维码弹给用户白扫一次。
    """
    mt = qr_file_mtime()
    if mt and (time.time() - mt) > 60:
        log.info("检测到旧的二维码文件，先清掉并等待本次新生成的二维码 ...")
        try:
            os.remove(QRCODE_IMAGE)
        except OSError:
            pass
        for _ in range(15):
            await asyncio.sleep(2)
            if qr_file_mtime() > 0:
                break

    url_line = ""
    try:
        log_text = open(QRCONSOLE_LOG, "r", encoding="utf-8", errors="replace").read()
        found = re.findall(r"二维码解码URL:\s*(\S+)", log_text)
        if found:
            url_line = found[-1]
    except FileNotFoundError:
        pass

    if not os.path.exists(QRCODE_IMAGE):
        log.error(f"未找到二维码图片 {QRCODE_IMAGE}")
        return
    try:
        with open(QRCODE_IMAGE, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        html = (
            "<!doctype html><meta charset='utf-8'><title>NapCat 扫码登录</title>"
            "<body style='font-family:system-ui;text-align:center;padding:28px'>"
            "<h2>请用手机 QQ 扫码登录</h2>"
            f"<img src='data:image/png;base64,{b64}' style='width:280px;height:280px;image-rendering:pixelated'>"
            "<p style='color:#a00'>二维码几分钟内有效，过期请重新运行 bot.py</p>"
            + (f"<p>扫不出来可用链接自行生成二维码：<br><code style='font-size:12px'>{url_line}</code></p>" if url_line else "")
            + "<p style='color:#666;font-size:13px'>扫码后在手机 QQ 上点「授权登录」</p></body>"
        )
        html_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "napcat-qrcode.html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html)
        os.startfile(html_path)          # 用默认浏览器弹出二维码
        log.warning(f"已弹出二维码页面：{html_path}")
    except Exception as e:
        log.error(f"生成二维码页面失败：{e}")


async def _read_stdin_line() -> str:
    """在一个一次性 daemon 线程里读一行输入（阻塞调用，不能放在事件循环里）。

    为什么不用线程池：`sys.stdin.readline` 一旦没有输入就永久阻塞，而
    `asyncio.wait_for` 超时只取消协程、取消不了线程 —— 线程池里的线程是
    non-daemon，会一直卡在 readline 上。解释器退出时
    `wait_for_thread_shutdown()` 要 join 所有 non-daemon 线程，于是
    "已停止 bot"之后进程永远不退出，连 atexit 的清理都轮不到执行
    （实测：日志停在"已停止 bot"，进程却活着、CPU 接近 0、QQ/NapCat 已被杀）。
    daemon 线程不参与 join，卡住也无所谓。
    串行性由 scan_prompt_active 保证（同一时刻只会有一个扫码交互）。
    """
    loop = asyncio.get_running_loop()
    fut: asyncio.Future = loop.create_future()

    def set_ok(line: str) -> None:
        if not fut.done():
            fut.set_result(line)

    def set_err(e: BaseException) -> None:
        if not fut.done():
            fut.set_exception(e)

    def worker() -> None:
        try:
            line = sys.stdin.readline()
        except Exception as e:          # stdin 已关闭等极端情况
            loop.call_soon_threadsafe(set_err, e)
        else:
            loop.call_soon_threadsafe(set_ok, line)

    threading.Thread(target=worker, daemon=True, name="stdin-read").start()
    return await fut


async def _read_scan_choice() -> str | None:
    """读扫码选择，返回 'y' / 'n'；超时或输入不可用（EOF）时返回 None。

    None 的含义是"没能拿到用户的决定"，由调用方决定怎么兜底 ——
    这样超时处理逻辑与读输入解耦，也便于单独验证。
    """
    if SCAN_PROMPT_TIMEOUT > 0:
        try:
            line = await asyncio.wait_for(_read_stdin_line(), timeout=SCAN_PROMPT_TIMEOUT)
        except asyncio.TimeoutError:
            print()
            log.warning(f"等待扫码选择超时（{SCAN_PROMPT_TIMEOUT} 秒）")
            return None
    else:
        line = await _read_stdin_line()     # 配置为 0：永远等待（人工值守）
    if line == "":
        print()
        log.warning("标准输入不可读（EOF）——常见于输出被重定向或终端不提供交互输入")
        return None
    return line.strip().lower()


async def handle_scan_login() -> bool | None:
    """快速登录失败、需要扫码时的处理：弹出二维码并询问用户怎么做。

    返回 True  = 用户选择直接扫码，且已确认账号上线
    返回 False = 需要停止 bot（用户选择手动重建凭证 / 超时无人值守 / 扫码未成功）
    返回 None  = 已经有另一个扫码交互在进行中，本次未完成 —— 调用方不要当成"已完成"
    """
    global scan_prompt_active, need_manual_recovery
    if scan_prompt_active:
        # 不能返回 True：那会让并发进来的调用方误以为"扫码已完成"而继续重连
        return None
    scan_prompt_active = True

    log.warning(f"快速登录未成功，需要扫码登录（二维码 {QRCODE_IMAGE}）")

    # 1) 先让用户决定：默认手动重建凭证；选择直接扫码时才弹出二维码
    log.warning("=" * 60)
    log.warning("快速登录失败 —— 需要扫码登录。请选择：")
    log.warning("  [Y] 先手动登录一次建立凭证，让「自动快速登录」以后能继续用")
    log.warning("      （会结束 NapCat/QQ 进程并停止 bot.py）")
    log.warning("      ⚠️ 若你平时不用 QQ 客户端，选这项可能让机器人再也无法自动上线")
    log.warning("  [N] 就现在扫码登录（需要人工点授权，不支持无人值守）")
    log.warning("=" * 60)
    # 提示符用 print 手工输出（而不是 input 的内置 prompt）：
    # 内置 prompt 会在调用 input 的瞬间输出，容易与随后落下的日志挤在同一行；
    # 手工输出能保证它独占一行、出现在所有日志的最后。
    print()
    print("请选择 [Y/n]: ", end="", flush=True)
    ans = await _read_scan_choice()

    if ans is None:
        # 超时或没有输入源：先确认是不是用户刚好扫完码（避免把已登录的进程杀掉）
        if await check_online_standalone():
            print()
            log.info("虽然没收到选择，但账号已上线（可能刚扫码成功），继续运行")
            scan_prompt_active = False
            return True
        print()
        log.warning("超时/无法读取选择，且账号仍离线 → 按默认 Y 处理：清理进程并安全退出")
        ans = ""

    if ans in ("", "y", "yes"):
        # 手动恢复：结束进程并停止 bot，让用户登录 QQ 客户端重建凭证（会关闭自动重登）
        log.warning("已选择手动恢复：正在结束 NapCat / QQ 进程并停止 bot ...")
        for img in ("QQ.exe", "NapCatWinBootMain.exe"):
            try:
                subprocess.run(["taskkill", "/f", "/im", img],
                               capture_output=True, text=True,
                               creationflags=subprocess.CREATE_NO_WINDOW)
            except Exception as e:
                log.warning(f"结束 {img} 失败：{e}")
        log.warning("处理完毕。请按以下步骤恢复「自动快速登录」：")
        log.warning("  1. 手动启动 NapCat（launcher.bat），在 QQ 客户端里完成登录")
        log.warning("  2. 确认能正常收发消息后，退出 QQ 登录")
        log.warning("  3. 重新运行 bot.py —— 之后掉线就能自动拉起")
        scan_prompt_active = False
        return False

    await ensure_qr_page()
    log.info("已选择直接扫码登录，请在浏览器中扫码授权；NapCat 上线后会自动继续运行。")
    if await wait_online_recovery(180, 10):
        log.info("扫码登录成功，NapCat 已上线")
        scan_prompt_active = False
        return True
    log.error("扫码后 180 秒内仍未上线，请检查手机 QQ 是否点了「授权登录」")
    scan_prompt_active = False
    return False


async def relogin_once(reason: str) -> bool:
    """执行一次自动重登：结束 QQ 进程 → 无黑窗快速登录 → 轮询等待上线。

    刻意不接收 ws：本函数会在"主连接已断开"时被调用，必须能独立工作。
    """
    global relogin_failures, need_manual_recovery
    log.warning(f"检测到 NapCat 不可用（{reason}），开始自动重登")

    # 1) 结束 QQ.exe 整组进程：同一程序已有实例时，launcher 不会真正重启注入。
    #    但端口根本没在监听时，说明 QQ 进程已经死了，taskkill 是多余的（还要白等 3 秒），
    #    直接拉起即可。这个判断原本写在主循环那条分支里，现在收进来——
    #    "拉起 QQ"只保留这一个入口，不再有两条路径各自拉起、互相 taskkill。
    if napcat_port_open():
        try:
            r = subprocess.run(["taskkill", "/f", "/im", "QQ.exe"],
                               capture_output=True, text=True,
                               creationflags=subprocess.CREATE_NO_WINDOW)
            log.info(f"已结束 QQ.exe：{(r.stdout or r.stderr or '').strip()[:120]}")
        except Exception as e:
            log.error(f"结束 QQ.exe 失败：{e}")
        await asyncio.sleep(3)   # 等进程真正退出，否则 launcher 可能复用旧实例
    else:
        log.info("QQ 进程已退出（端口未监听），跳过 taskkill 直接拉起")

    # 记下二维码时间戳：之后若它被更新，就说明快速登录失败、NapCat 已改用扫码
    qr_before = qr_file_mtime()

    # 2) 无黑窗启动快速登录（QQ 号由 napcat-autologin.bat 内部传入）
    err = await asyncio.to_thread(_spawn_autologin_sync)
    if err:
        log.error(f"自动重登无法执行：{err}")
        relogin_failures += 1
        return False

    # 3) 等"上线"或"出现新二维码"——后者意味着快速登录已被要求扫码，
    #    可以立刻转入扫码流程，不必盲等到 RELOGIN_WAIT_SECONDS。
    result = await wait_online_or_qr(QR_DETECT_TIMEOUT, qr_before)
    if result == "online":
        log.info("自动重登成功（快速登录生效）")
        relogin_failures = 0
        return True

    if result == "qr":
        log.warning("快速登录失败（NapCat 已生成新二维码），转入扫码流程")
        relogin_failures += 1
        scan_result = await handle_scan_login()
        if scan_result is None:
            # 另一个扫码交互在进行中（并发调用），本次不视为完成、也不终止流程
            log.info("已有扫码交互在进行中，本次等待其结束")
            return True
        if not scan_result:
            need_manual_recovery = True
            return False
        return True

    # 4) 既没上线也没新二维码：再按原逻辑等满剩余时间
    log.warning(f"{QR_DETECT_TIMEOUT} 秒内未见上线或新二维码，继续等待（最多 {RELOGIN_WAIT_SECONDS} 秒）")
    if await wait_online_recovery(RELOGIN_WAIT_SECONDS, 10):
        log.info("自动重登成功")
        relogin_failures = 0
        return True
    log.error(f"自动重登超时（{RELOGIN_WAIT_SECONDS} 秒内未上线），"
              "快速登录可能已被要求扫码验证")
    relogin_failures += 1
    if not await handle_scan_login():
        need_manual_recovery = True
        return False
    return True


def _spawn_relogin(reason: str) -> None:
    """限流后启动重登任务。被踢下线属于账号侧问题，必须限次，
    否则会陷入"重启→被踢→再重启"的循环。"""
    global relogin_task
    if relogin_task and not relogin_task.done():
        return
    now = time.time()
    # 只保留最近一小时内的尝试记录
    relogin_attempts[:] = [t for t in relogin_attempts if now - t < 3600]
    if len(relogin_attempts) >= RELOGIN_MAX_PER_HOUR:
        log.error(f"一小时内自动重登已达上限 {RELOGIN_MAX_PER_HOUR} 次，暂停自动恢复，"
                  "请手动检查 QQ 登录状态（可能需要扫码）")
        return
    relogin_attempts.append(now)

    task = asyncio.create_task(relogin_once(reason))
    relogin_task = task
    task.add_done_callback(lambda _t: globals().update(relogin_task=None))


async def relogin_watchdog(ws):
    """定期巡检账号状态，发现离线就触发自动重登（失败按 1/5/15 分钟退避）。"""
    last_online = True          # 上一次巡检结果，仅用于打印状态变化
    await asyncio.sleep(5)
    while True:
        try:
            online = await check_online(ws)
            if online != last_online:
                log.info(f"账号在线状态变化：{last_online} → {online}")
            last_online = online
            if not online:
                if not AUTO_RELOGIN:
                    log.warning("账号离线，但 auto_relogin 已关闭，不执行自动重登")
                elif relogin_task and not relogin_task.done():
                    pass   # 已有重登任务在跑
                else:
                    _spawn_relogin("巡检发现离线")
        except Exception as e:
            log.error(f"在线状态巡检异常：{e}")
        # 失败后按 1 / 5 / 15 分钟退避，避免被踢时无限重启
        if relogin_failures > 0:
            await asyncio.sleep(min(60 * (5 ** (relogin_failures - 1)), 900))
        else:
            await asyncio.sleep(HEALTH_CHECK_INTERVAL)

# ---------- 图片获取(读 NapCat 本地缓存,绕开腾讯防盗链) ----------
async def get_image_base64(ws, file_name: str) -> str | None:
    """
    通过 NapCat 获取图片缓存内容并转为 base64 data URL。
    腾讯图片链接带防盗链和时效,NapCat 本地必然已有缓存,读取缓存即可绕过。
    """
    # 方案1:get_file(扩展 API,直接返回 base64 内容)
    try:
        info = await call_napcat(ws, "get_file", {"file_id": file_name})
        if info and info.get("base64"):
            fn = info.get("file") or ""
            mime = "image/png" if fn.lower().endswith(".png") else "image/jpeg"
            return f"data:{mime};base64,{info['base64']}"
    except Exception as e:
        log.error(f"get_file 失败: {e}")

    # 方案2:get_image(标准 API,拿本地缓存路径再读文件)
    try:
        info = await call_napcat(ws, "get_image", {"file": file_name})
        if info:
            path = info.get("file") or ""
            if path and os.path.exists(path):
                with open(path, "rb") as f:
                    raw = f.read()
                mime = "image/png" if path.lower().endswith(".png") else "image/jpeg"
                return f"data:{mime};base64,{base64.b64encode(raw).decode()}"
            log.warning(f"get_image 返回的路径不存在: {path}")
    except Exception as e:
        log.error(f"get_image 失败: {e}")

    return None

# ---------- 视觉模型调用（图片转文字） ----------
async def image_to_text(ws, img: dict, sub_type: int = 0) -> str:
    """
    根据 sub_type 选择提示词，识别图片或表情包。
    img: {"url":..., "file":..., "sub_type":...}
    sub_type: 0=普通图片, 2/7=表情包（QQ常见）
    """
    if sub_type in (2, 7):
        prompt = (
            "请识别这个QQ表情包，用中文描述其画面内容、提取图中文字，"
            "并简要说明这个表情包可能表达的情绪或梗的含义。直接描述，不要解释过程。"
        )
    else:
        prompt = "请用中文描述这张图片的内容及必要的文字原文消息内容。直接描述，不要解释过程。"

    # 优先取 NapCat 本地缓存(绕开腾讯防盗链),拿不到就返回失败
    file_name = img.get("file") or ""
    if not file_name:
        log.warning(f"图片无缓存文件名,跳过识别: {str(img.get('url'))[:60]}...")
        return ""

    image_data = await get_image_base64(ws, file_name)
    if not image_data:
        log.warning(f"无法获取图片内容,跳过识别: file={file_name} url={str(img.get('url'))[:60]}...")
        return ""

    try:
        resp = await vision_client.chat.completions.create(
            model=VISION_MODEL,
            messages=[
                {"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_data}}
                ]}
            ],
            max_tokens=500
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        log.error(f"视觉模型调用失败: {e}")
        return ""

# ---------- 主动消息 ----------
async def proactive_chat(msgs: list[dict]) -> str | None:
    """基于给定快照生成一句主动开场白。本函数不落盘，由调用方在持锁状态下追加记忆。"""
    now_str = get_beijing_time_str()
    msgs = list(msgs)
    msgs.append({"role": "user",
                 "content": f"【当前时间】北京时间 {now_str}\n"
                            "（现在没在和人对话，你想再跟对方说句话。"
                            "说一句简短、自然、贴合人设的话，像随手发条 QQ 消息；"
                            "不要长篇大论，也不要提到'自动'或'机器人'）"})
    try:
        resp = await client.chat.completions.create(
            model=TEXT_MODEL,
            messages=msgs,
            temperature=1.3,
            max_tokens=200,
            extra_body={"thinking": {"type": "enabled" if ENABLE_THINKING else "disabled"}},
        )
        return clean_reply((resp.choices[0].message.content or "").strip())
    except Exception as e:
        log.error(f"主动消息生成失败: {e}")
        return None


def is_quiet_period(key: str, quiet_seconds: float) -> tuple[bool, float]:
    """判断某会话是否处于"刚聊过天"的静默期。

    返回 (是否静默, 空闲秒数)。quiet_seconds <= 0 表示关闭该机制。
    抽成纯函数是为了可单独验证，同时供私聊/群聊两个循环共用。
    """
    if quiet_seconds <= 0:
        return False, 0.0
    idle = time.time() - last_activity.get(key, 0)
    return idle < quiet_seconds, idle

# ---------- 消息解析 ----------
def extract_message(raw) -> tuple[str, list[dict]]:
    """
    从消息中提取纯文本和图片信息列表。
    每个图片信息为 dict: {"url": str, "sub_type": int}
    """
    text = ""
    images = []
    if isinstance(raw, list):
        for seg in raw:
            seg_type = seg.get("type")
            if seg_type == "text":
                text += seg.get("data", {}).get("text", "")
            elif seg_type == "image":
                data = seg.get("data", {})
                url = data.get("url")
                if url:
                    sub_type = data.get("sub_type", 0)
                    try:
                        sub_type = int(sub_type)
                    except (ValueError, TypeError):
                        sub_type = 0
                    images.append({"url": url,
                                   "file": data.get("file", ""),
                                   "sub_type": sub_type})
                else:
                    log.warning("收到图片但无 URL：%s", data)
    else:
        text = str(raw)
    return text, images

def is_mentioned(raw, self_id: int) -> bool:
    if isinstance(raw, list):
        return any(seg.get("type") == "at"
                   and str(seg.get("data", {}).get("qq")) == str(self_id)
                   for seg in raw)
    return False

# ---------- 发消息 ----------
async def send_private_msg(ws, uid: int, text: str) -> bool:
    """发送私聊消息，返回是否确认送达。

    改用带 echo 的请求-响应模式：只有拿到 NapCat 的 message_id 才算送达成功。
    改造前是单向发送，发送失败也是静默的——会把没送出去的话写进记忆。
    """
    data = await call_napcat(ws, "send_private_msg",
                             {"user_id": uid, "message": text})
    if data and data.get("message_id"):
        return True
    log.error(f"私聊发送失败 uid={uid} 回执={data} 内容={text[:60]!r}")
    return False

async def send_group_msg(ws, gid: int, text: str, at_qq: int | None = None) -> bool:
    """发送群聊消息，返回是否确认送达（判定同 send_private_msg）。"""
    if at_qq is not None:
        message = [{"type": "at", "data": {"qq": str(at_qq)}},
                   {"type": "text", "data": {"text": " " + text}}]
    else:
        message = text
    data = await call_napcat(ws, "send_group_msg",
                             {"group_id": gid, "message": message})
    if data and data.get("message_id"):
        set_group_active(gid)
        return True
    log.error(f"群消息发送失败 gid={gid} 回执={data} 内容={text[:60]!r}")
    return False

async def send_assistant_reply(ws, text: str,
                               uid: int | None = None,
                               gid: int | None = None,
                               at_qq: int | None = None) -> list[str]:
    """把回复按空行拆分后依次发送，返回**已成功送达**的段。

    调用方据此决定写入记忆的内容：只记真正发出去的，避免"没送达却被当成说过了"。
    多段之间加随机延迟，一是更像真人打字，二是避免连续发送触发风控。
    """
    parts = split_reply(text)
    sent: list[str] = []
    for i, part in enumerate(parts):
        if i > 0 and SPLIT_REPLY_INTERVAL[1] > 0:
            await asyncio.sleep(random.uniform(*SPLIT_REPLY_INTERVAL))
        if gid is not None:
            ok = await send_group_msg(ws, gid, part, at_qq=at_qq)
        else:
            ok = await send_private_msg(ws, uid, part)
        if ok:
            sent.append(part)
        else:
            log.warning(f"第 {i + 1}/{len(parts)} 段发送失败，后续段落停止发送")
            break
    if len(parts) > 1:
        log.info(f"回复拆分为 {len(parts)} 条发送，成功 {len(sent)} 条")
    return sent

# ---------- 事件处理 ----------
async def handle_message(ws, data: dict):
    mtype = data.get("message_type")
    if mtype not in ("private", "group"):
        return
    uid = data.get("user_id")
    if uid == BOT_QQ:
        return

    raw_message = data.get("message", "")
    text, images = extract_message(raw_message)

    if not text and not images:
        return

    # 记录会话活动时间：只要对方发来消息（哪怕之后不回复），就算"正在聊天"。
    # 主动消息的静默期判断依赖它，所以更新放在可能 return 的图片处理之前。
    _act_key = str(uid) if mtype == "private" else f"g:{data.get('group_id')}"
    last_activity[_act_key] = time.time()

    # 处理图片：识别并标记类型
    image_desc = ""
    if images:
        total_images = len(images)
        descriptions = []
        for idx, img in enumerate(images, start=1):
            sub_type = img["sub_type"]
            is_sticker = sub_type in (2, 7)
            type_tag = "【表情包】" if is_sticker else "【图片】"

            if idx <= MAX_IMAGES_PER_MESSAGE:
                desc = await image_to_text(ws, img, sub_type)
                if desc:
                    descriptions.append(f"第{idx}张：{type_tag}{desc}")
                else:
                    descriptions.append(f"第{idx}张：{type_tag}图片加载失败")
            else:
                # 超出上限，仍标记类型，但显示加载失败
                descriptions.append(f"第{idx}张：{type_tag}图片加载失败")

        image_desc = f"（此处是{total_images}张图片/表情包，内容依次是：{'；'.join(descriptions)}）"

    # 合并文本和图片描述
    if image_desc:
        combined_text = text + "\n" + image_desc if text else image_desc
    else:
        combined_text = text

    if not combined_text.strip():
        return

    if mtype == "private":
        if uid not in PRIVATE_WHITELIST:
            return
        key = str(uid)

        # 阶段 1（持锁）：写入用户消息 → 判断是否触发压缩 → 取本轮请求快照
        async with get_mem_lock(key):
            if "清空记忆" in text:
                clear_long_memory(key)   # 清 L0 + L1，并让在途压缩作废
                if not await send_private_msg(ws, uid, CLEAR_MEMORY_REPLY):
                    # 记忆确实已清空，只是回复没送出去；记日志以免用户以为没生效而反复发
                    log.warning(f"清空记忆已执行，但确认回复未送达 uid={uid}")
                return

            await append_memory(key, "user", combined_text)
            maybe_schedule_compress(key)
            # 快照必须在写入本条消息之后取，否则当前这条会漏出上下文
            reply_msgs = build_reply_msgs(key, None)

        if random.random() > REPLY_PROBABILITY:
            log.info(f"私聊跳过回复 {uid}")
            return

        # 生成前在线检查：账号离线时回复必然发不出去，不如不调用模型（省 token，也避免污染记忆）
        if not await check_online(ws):
            log.warning(f"账号离线，跳过本次私聊回复 {uid}（不调用模型，不写入记忆）")
            return

        # 阶段 2（无锁）：调用模型。压缩任务此时可以自由读写 memories，不会影响本轮已取好的快照
        reply, _ok = await chat_with_deepseek(key, reply_msgs)

        # 阶段 3：先发送、确认送达后才写记忆 —— 送不出去的话不该被当成"已经说过"
        # 回复可能含空行分隔的多段，逐条发送；只把成功送达的段记进 L0
        sent_parts = await send_assistant_reply(ws, reply, uid=uid)
        if sent_parts:
            async with get_mem_lock(key):
                for part in sent_parts:
                    await append_memory(key, "assistant", part)
        else:
            log.warning(f"私聊回复未送达，不写入记忆（避免后续对话基于未发生的内容）uid={uid}")

    else:  # group
        gid = data.get("group_id")
        if gid not in GROUP_WHITELIST:
            return
        key = f"g:{gid}"
        mentioned = is_mentioned(raw_message, BOT_QQ)

        sender = data.get("sender", {})
        nickname = sender.get("card") or sender.get("nickname") or str(uid)
        formatted_text = f"[{nickname}（QQ{uid}）]：{combined_text}"

        if "清空记忆" in text:
            clear_long_memory(key)
            if not await send_group_msg(ws, gid, CLEAR_MEMORY_REPLY,
                                        at_qq=uid if mentioned else None):
                log.warning(f"清空记忆已执行，但确认回复未送达 gid={gid}")
            return

        async with get_mem_lock(key):
            await append_memory(key, "user", formatted_text)
            reply_msgs = build_reply_msgs(key, None)

        if GROUP_AT_ONLY and not mentioned:
            return

        prob = group_reply_probability(gid, mentioned, combined_text, nickname)
        if random.random() > prob:
            log.info(f"群 {gid} 按概率跳过回复")
            group_consecutive_replies[gid] = 0
            return

        # 生成前在线检查（同私聊）
        if not await check_online(ws):
            log.warning(f"账号离线，跳过本次群聊回复 {gid}（不调用模型，不写入记忆）")
            return

        group_consecutive_replies[gid] = group_consecutive_replies.get(gid, 0) + 1
        reply, _ok = await chat_with_deepseek(key, reply_msgs)

        sent_parts = await send_assistant_reply(ws, reply, gid=gid,
                                                at_qq=uid if mentioned else None)
        if sent_parts:
            async with get_mem_lock(key):
                for part in sent_parts:
                    await append_memory(key, "assistant", part)
        else:
            log.warning(f"群回复未送达，不写入记忆 gid={gid}")

async def safe_handle_message(ws, data):
    try:
        await handle_message(ws, data)
    except Exception as e:
        log.exception("处理消息时出错")

# ---------- 主动循环（独立） ----------
async def proactive_loop_private(ws):
    await asyncio.sleep(30)
    while True:
        interval = random.uniform(*PROACTIVE_INTERVAL_PRIVATE)
        log.info(f"下次主动私聊：约 {interval/60:.1f} 分钟后")
        await asyncio.sleep(interval)

        if ENABLE_SILENT_HOURS:
            now_hour = datetime.now(timezone(timedelta(hours=8))).hour
            if SILENT_HOURS_START <= now_hour < SILENT_HOURS_END:
                log.info(f"当前北京时间 {now_hour} 点，处于静音时段，跳过主动私聊")
                continue

        for uid in PRIVATE_WHITELIST:
            if random.random() > PROACTIVE_TO_EACH:
                continue
            key = str(uid)
            # 静默期：对方最近还在聊天就不主动打扰（按好友独立判断）
            quiet, idle = is_quiet_period(key, PROACTIVE_QUIET_PRIVATE)
            if quiet:
                log.info(f"私聊 {uid} 最近 {idle:.0f} 秒内有对话，跳过本次主动消息")
                continue
            # 生成前在线检查：离线时主动消息必然发不出去，不该白白调用模型烧 token
            if not await check_online(ws):
                log.warning(f"账号离线，跳过本次主动私聊 {uid}（不调用模型）")
                continue
            async with get_mem_lock(key):
                reply_msgs = build_reply_msgs(key, None)
            reply = await proactive_chat(reply_msgs)
            if not reply:
                continue
            sent_parts = await send_assistant_reply(ws, reply, uid=uid)
            if sent_parts:
                async with get_mem_lock(key):
                    for part in sent_parts:
                        await append_memory(key, "assistant", part)
            else:
                log.warning(f"主动私聊未送达，不写入记忆 uid={uid}")

async def proactive_loop_group(ws):
    await asyncio.sleep(60)
    while True:
        interval = random.uniform(*PROACTIVE_INTERVAL_GROUP)
        log.info(f"下次主动群聊：约 {interval/60:.1f} 分钟后")
        await asyncio.sleep(interval)

        if ENABLE_SILENT_HOURS:
            now_hour = datetime.now(timezone(timedelta(hours=8))).hour
            if SILENT_HOURS_START <= now_hour < SILENT_HOURS_END:
                log.info(f"当前北京时间 {now_hour} 点，处于静音时段，跳过主动群聊")
                continue

        for gid in GROUP_WHITELIST:
            if random.random() > PROACTIVE_TO_EACH:
                continue
            key = f"g:{gid}"
            # 静默期：群里最近还在聊天就不主动插话（按群独立判断）
            quiet, idle = is_quiet_period(key, PROACTIVE_QUIET_GROUP)
            if quiet:
                log.info(f"群 {gid} 最近 {idle:.0f} 秒内有对话，跳过本次主动消息")
                continue
            # 生成前在线检查（同私聊）
            if not await check_online(ws):
                log.warning(f"账号离线，跳过本次主动群聊 {gid}（不调用模型）")
                continue
            async with get_mem_lock(key):
                reply_msgs = build_reply_msgs(key, None)
            reply = await proactive_chat(reply_msgs)
            if not reply:
                continue
            sent_parts = await send_assistant_reply(ws, reply, gid=gid)
            if sent_parts:
                async with get_mem_lock(key):
                    for part in sent_parts:
                        await append_memory(key, "assistant", part)
            else:
                log.warning(f"主动群聊未送达，不写入记忆 gid={gid}")

# ---------- 调试模式 ----------
async def debug_console():
    load_memory()
    load_long_memory()
    print("=== 调试模式 ===")
    print("输入内容测试人设；输入 清空记忆 忘记上下文；输入 q 退出\n")
    while True:
        text = input("你: ").strip()
        if text.lower() == "q":
            break
        if "清空记忆" in text:
            clear_long_memory("debug")
            print(f"{ROBOT_NAME}: {CLEAR_MEMORY_REPLY}\n")
            continue
        async with get_mem_lock("debug"):
            await append_memory("debug", "user", text)
            msgs = build_reply_msgs("debug", None)
        reply, _ok = await chat_with_deepseek("debug", msgs)
        async with get_mem_lock("debug"):
            await append_memory("debug", "assistant", reply)
        print(f"{ROBOT_NAME}: {reply}\n")

# ---------- 主程序 ----------
def clean_shutdown(force: bool = False) -> None:
    """停止 bot 时清理 NapCat / QQ 进程。

    不清理的话，Ctrl+C 之后 NapCat 与 QQ 会继续在后台跑（占内存、占着登录状态）。
    force=True 表示"用户主动要手动重建凭证"场景，无条件清理。
    由 atexit 注册调用，正常退出与 Ctrl+C 都会执行。
    """
    if not force and not KILL_QQ_ON_EXIT:
        log.info("kill_qq_on_exit 为 false，保留 QQ / NapCat 进程")
        return
    log.warning("正在结束 QQ / NapCat 进程 ...")
    for img in ("QQ.exe", "NapCatWinBootMain.exe"):
        try:
            r = subprocess.run(["taskkill", "/f", "/im", img],
                               capture_output=True, text=True,
                               creationflags=subprocess.CREATE_NO_WINDOW)
            out = (r.stdout or r.stderr or "").strip()
            if not out or "not found" in out.lower():
                log.info(f"  {img}: 没有正在运行的进程")
            else:
                log.info(f"  {img}: 已结束 {out.lower().count('success')} 个进程")
        except Exception as e:
            log.error(f"  结束 {img} 失败：{e}")
    log.info("清理完成。想重新上线请再次运行 python bot.py"
             "（已配置掉线自愈的话，它会自己快速登录拉起）")
    if force:
        # force=True 的语义是"停下来等你手动重建凭证"，此时必须真正结束进程。
        # 不能只靠 return + 解释器自然退出：只要还有 non-daemon 线程卡着
        # （比如读 stdin 的那个线程），解释器退出时会 join 它们并永远停住，
        # 表现为"日志说已停止 bot，进程却一直活着"。该做的清理上面都做完了，
        # 这里直接结束进程最稳妥（os._exit 不再触发 atexit，避免递归进来）。
        log.warning("强制结束进程")
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(0)


async def main():
    load_memory()
    load_long_memory()
    if LONG_MEMORY_ENABLED:
        log.info(f"长期记忆已启用（仅私聊）：L0 上限 {LM_L0_MAX} 条，每次压缩 {LM_COMPRESS_COUNT} 条，"
                 f"L1 上限 {LM_L1_MAX_FACTS} 条（{_format_type_quota()}）；"
                 f"近期流水保留最近 {LM_RECENT_DAYS} 个聊过的日子 / 最多 {LM_RECENT_MAX_ITEMS} 条；"
                 f"心事（persona）上限 {LM_PERSONA_MAX_CHARS} 字；存储于 {LM_FILE}")
    log.info(f"正在连接 NapCat: {WS_URL}")
    if AUTO_RELOGIN:
        log.info(f"掉线自愈已启用：每 {HEALTH_CHECK_INTERVAL} 秒巡检，"
                 f"离线时自动快速登录（每小时最多 {RELOGIN_MAX_PER_HOUR} 次）")
    relogin_failed = False   # 本轮断开是否已触发过重登（避免重连循环里反复触发）
    atexit.register(clean_shutdown)    # Ctrl+C 等退出时兜底清理 NapCat / QQ 进程
    while True:
        if need_manual_recovery:
            log.warning("已停止 bot：请按提示手动登录以重建快速登录凭证，完成后重新运行 bot.py")
            clean_shutdown(force=True)   # 用户要手动重建凭证 → 无条件结束进程
            return
        # 重登任务正在跑（含扫码交互）：NapCat 此时必然连不上，
        # 反复尝试连接只会刷 ERROR 日志、还会把「请选择 [Y/n]」提示冲散。
        # 安静等它结束再继续——成功就正常上线，失败会被上面的检查接住。
        if relogin_task and not relogin_task.done():
            await asyncio.sleep(3)
            continue
        try:
            async with websockets.connect(WS_URL, ping_interval=20) as ws:
                log.info("已连接 NapCat，机器人上线喵~")
                relogin_failed = False
                tasks = [
                    asyncio.create_task(proactive_loop_private(ws)),
                    asyncio.create_task(proactive_loop_group(ws)),
                    asyncio.create_task(relogin_watchdog(ws))
                ]
                try:
                    async for raw in ws:
                        data = json.loads(raw)
                        echo = data.get("echo")
                        if echo and echo in pending_actions:
                            fut = pending_actions.pop(echo)
                            if not fut.done():
                                fut.set_result(data.get("data"))
                            continue
                        if data.get("post_type") == "message":
                            asyncio.create_task(safe_handle_message(ws, data))
                finally:
                    for t in tasks:
                        t.cancel()
        except Exception as e:
            # 连接断开 = NapCat 不可用的最强信号（不依赖 WebSocket 自身去检测）
            log.error(f"连接断开: {e}，5秒后重连...")
            if AUTO_RELOGIN and not relogin_failed:
                relogin_failed = True
                # 统一交给 relogin_once：它内部会区分"QQ 进程已死（端口未监听）"
                # 和"账号掉线（端口在监听）"，并且自带限流。
                # 这里不再自己拉起并等待——那条路径会和巡检那条同时拉起、互相 taskkill
                # （一边刚拉起 QQ，另一边又把它杀掉），还绕过了限流，
                # 并且会把主循环阻塞在扫码交互上最长 SCAN_PROMPT_TIMEOUT 秒。
                _spawn_relogin("主连接断开")
            await asyncio.sleep(5)

if __name__ == "__main__":
    if "--debug" in sys.argv:
        asyncio.run(debug_console())
    else:
        asyncio.run(main())
