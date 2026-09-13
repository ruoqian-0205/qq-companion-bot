# -*- coding: utf-8 -*-
import asyncio
import json
import random
import logging
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
LM_L1_TARGET_CHARS = CFG.get("lm_l1_target_chars", 1000)
LM_L1_ACCEPT_CHARS = CFG.get("lm_l1_accept_chars", 1500)
LM_L1_INJECT_CHARS = CFG.get("lm_l1_inject_chars", 800)

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
        match = re.match(r'^{ROBOT_NAME}[（(]?\d*[)）]?\s*[:：]\s*', stripped)
        if match:
            text = stripped[match.end():].lstrip()
            continue  # 清理后可能还有残留，继续检查

        # 没有更多可清理的前缀，跳出
        break

    return text.strip()

# ---------- 记忆读写 ----------
def _atomic_write_json(path: str, data) -> None:
    """原子写：先写临时文件再替换，避免进程被杀时留下半截 JSON 把记忆文件弄坏。"""
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

def load_memory():
    global memories
    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            memories = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        memories = {}

def save_memory():
    _atomic_write_json(MEMORY_FILE, memories)

def load_long_memory():
    """载入 L1 长期记忆库。缺失或损坏时退化为空库（不会影响主对话）。"""
    global long_memories
    if not LONG_MEMORY_ENABLED:
        return
    try:
        with open(LM_FILE, "r", encoding="utf-8") as f:
            long_memories = json.load(f)
        if not isinstance(long_memories, dict):
            long_memories = {}
    except (FileNotFoundError, json.JSONDecodeError):
        long_memories = {}

def save_long_memory():
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
    群聊沿用 memory_max_messages 硬截断；私聊不截断，交给长期记忆压缩机制接管窗口长度。
    """
    time_str = get_beijing_time_str()
    content = f"[{time_str}] {content}"
    memories.setdefault(key, []).append({"role": role, "content": content})
    if key.startswith("g:") and len(memories[key]) > MEMORY_MAX_MESSAGES:
        memories[key] = memories[key][-MEMORY_MAX_MESSAGES:]
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

    base += (
        "\n\n【重要规则】对话记录中每条消息前面的时间戳（如[2026-08-09 19:00 周日]）"
        "是消息发送的时间，仅供你理解时间背景和对话先后顺序。"
        "大多数时候可以忽略时间戳，以更贴近真人闲聊的状态。"
        "你自己回复时绝对不要输出任何时间戳或类似格式的内容，不要模仿这种写法。"
        "你的每条回复都应该完全自然，像真人在QQ上聊天一样，不包含任何元信息或格式标记。"
    )
    return base

# ---------- 长期记忆：L1 载入/注入 ----------
LM_TYPE_ORDER = {"profile": 0, "relation": 1, "preference": 2, "sensitive": 3, "promise": 4, "event": 5}
LM_TYPE_LABEL = {
    "profile": "基本信息",
    "relation": "重要关系",
    "preference": "偏好与雷区",
    "sensitive": "需要保密的事",
    "promise": "约定与承诺",
    "event": "近期事件",
}
LM_TYPE_SHORT = {"profile": "p", "relation": "r", "preference": "f", "sensitive": "s", "promise": "m", "event": "e"}
LM_SHORT_TYPE = {v: k for k, v in LM_TYPE_SHORT.items()}

# 单类事实在注入块里的占比上限。阈值偏高，只用来兜底防止某一类(如条目最多的 profile)
# 把整个预算吃光，从而让重要关系/偏好/保密事项整类被挤出去。
LM_TYPE_BUDGET_RATIO = 0.6


def _now_beijing():
    return datetime.now(timezone(timedelta(hours=8)))


def _today_str() -> str:
    return _now_beijing().strftime("%Y-%m-%d")


def format_l1_block(key: str) -> str:
    """把 L1 渲染成注入主回复的记忆块（按类型分组、人话表达）。

    刻意与存储格式分离：磁盘上是结构化条目，注入时是人话短句。
    - 已过期的事件（exp 早于今天）直接过滤掉，纯本地字符串比较，不消耗 token
    - 按类型优先级 + 最近提及时间排序后截断，保证「基本信息」这类长期事实永远在最前面、
      不会因为事件条目堆积而被挤出预算
    """
    entry = long_memories.get(key) or {}
    facts = entry.get("facts") or []
    if not facts:
        return ""

    today = _today_str()

    def usable(f: dict) -> bool:
        if not f.get("c"):
            return False
        exp = f.get("exp") or ""
        return not (exp and exp < today)

    # 排序：类型优先级 → 提及次数多 → 最近提及
    ordered = sorted(
        [f for f in facts if usable(f)],
        key=lambda f: (
            LM_TYPE_ORDER.get(f.get("t", ""), 9),
            -f.get("n", 1),
            str(f.get("seen") or ""),
        ),
    )

    groups: dict[str, list[str]] = {}
    total = 0
    for f in ordered:
        t = f.get("t", "")
        line = f"- {f['c']}"
        if f.get("sensitive"):
            line += "（对方要求保密，别主动提起）"
        if total + len(line) > LM_L1_INJECT_CHARS:
            break
        # 单类占比上限：避免"基本信息"条目过多时把重要关系/偏好整类挤出去
        if sum(len(x) + 1 for x in groups.get(t, [])) + len(line) > LM_L1_INJECT_CHARS * LM_TYPE_BUDGET_RATIO:
            continue
        groups.setdefault(t, []).append(line)
        total += len(line) + 1

    if not groups:
        return ""

    body = "\n".join(
        f"{LM_TYPE_LABEL.get(t, t)}：\n" + "\n".join(groups[t])
        for t in sorted(groups, key=lambda x: LM_TYPE_ORDER.get(x, 9))
    )
    return (
        "\n\n【长期记忆·关于对方】\n"
        "以下是你以前和这个人聊天时记住的事，用来保持连贯和亲切。"
        "像真人一样自然使用：需要时自然带出来，不要复述、不要念清单、"
        "不要说“根据我的记忆”，也不要把这些当成本轮对方说的话。\n" + body
    )


def format_l0_for_compression(segment: list[dict]) -> str:
    """把待压缩的 L0 片段转成紧凑单行格式。

    只输出 user 消息：压缩的目标是「记住对方说过什么」，
    而 assistant 那侧是模型按人设现编的话，混进去会把虚构内容当成用户事实记下来。
    """
    lines = []
    for m in segment:
        if m.get("role") != "user":
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
        lines.append(f"{seen or '----'} | 对方：{text}")
    return "\n".join(lines)


LM_COMPRESS_SYSTEM = """你是长期记忆整理器，负责把「现有记忆库」与「新对话片段」合并成一份新的记忆库。
只输出 JSON，不要任何解释、不要 markdown 代码块。

抽取规则：
1. 只抽取关于「对方」（正在和你聊天的这个人）的事实，不要抽取寒暄和客套话。
2. 严禁记录任何关于你自己身份/属性的内容；凡涉及"你是不是AI/机器人/程序/模型"之类的话题，一律跳过。
3. 保留具体专有信息：人名、昵称、地名、日期、数字、物品名。宁可句子略长，也不要丢掉这些细节。
4. 合并重复项；同一件事信息有冲突时，以新片段里的为准（旧的直接丢弃）。
5. 特别注意「状态会被新信息取代」的情况：如果新片段说明某个旧事实已经改变（例如从杭州搬到上海、换了工作、分手了、猫送人了），必须把旧的那条删掉或改写成"从X变成Y"，绝不能旧的状态和新状态同时留着，否则会自相矛盾。
6. 对方明确要求保密、或属于隐私的事，用 sensitive 类型，并置 "sensitive": true。
7. 只处理标记为「对方：」的内容。

记忆类型：
- profile：对方的基本信息（名字、年龄、城市、职业、学业、生活习惯等）
- preference：喜好与厌恶（喜欢/讨厌什么、雷区）
- relation：对方生活中的重要关系与宠物
- promise：双方约定、答应过的事
- event：对方提到的一次性事件（考试、旅行、面试等），带日期
- sensitive：对方明确要求保密或敏感的私事

输出格式（严格按此结构）：
{"facts":[{"t":"profile","c":"名字叫阿哲","seen":"2026-09-12","exp":"","n":1,"sensitive":false}]}
字段：t=类型枚举；c=一句话内容（不超过40字）；seen=该事实最近提及日期 YYYY-MM-DD；exp=仅 event 填预计结束日期，没有就留空串；n=该事实累计出现次数；sensitive=是否敏感布尔值。

另外，「现有记忆库」用紧凑单行格式给你，字段依次是：
  类型缩写|内容|最近提及日期[|exp到期日][|n出现次数][|敏感]
类型缩写对应：p=profile、r=relation、f=preference、m=promise、e=event、s=sensitive。
你的输出必须仍然使用上面的 JSON 格式，不要沿用紧凑格式。"""


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


def parse_l1_facts(raw: str) -> list[dict] | None:
    """解析压缩模型返回的事实数组，任何异常都返回 None（由调用方放弃本次压缩、下轮重试）。"""
    text = raw.strip()
    if text.startswith("```"):  # 容错：去掉可能的代码块围栏
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        log.warning(f"长期记忆：压缩结果不是合法 JSON，本次跳过（前 120 字）：{raw[:120]}")
        return None

    raw_facts = data.get("facts") if isinstance(data, dict) else None
    if not isinstance(raw_facts, list):
        log.warning("长期记忆：压缩结果缺少 facts 数组，本次跳过")
        return None

    facts = []
    for item in raw_facts:
        if not isinstance(item, dict):
            continue
        t = str(item.get("t") or "").strip()
        c = str(item.get("c") or "").strip()
        if t not in LM_TYPE_ORDER or not c:
            continue
        seen = str(item.get("seen") or "").strip()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", seen):
            seen = _today_str()
        exp = str(item.get("exp") or "").strip()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", exp):
            exp = ""
        if t != "event":
            exp = ""
        try:
            n = max(1, int(item.get("n") or 1))
        except (TypeError, ValueError):
            n = 1
        facts.append({"t": t, "c": c[:200], "seen": seen, "n": n,
                      "exp": exp, "sensitive": bool(item.get("sensitive"))})
    return facts


def format_l1_for_prompt(facts: list[dict]) -> str:
    """把 L1 事实渲染成紧凑单行格式发给压缩模型（比 JSON 省不少 token）。"""
    if not facts:
        return "（空）"
    lines = []
    for f in facts:
        short = LM_TYPE_SHORT.get(f.get("t", ""), "p")
        line = f"{short}|{f.get('c','')}|{f.get('seen','')}"
        if f.get("exp"):
            line += f"|exp{f['exp']}"
        if f.get("n", 1) > 1:
            line += f"|n{f['n']}"
        if f.get("sensitive"):
            line += "|敏感"
        lines.append(line)
    return "\n".join(lines)


async def compress_memory(key: str, gen: int) -> None:
    """压缩一次 L0 → 自更新 L1。

    并发约定（本文件唯一的写入规则）：
    - 所有对 memories / long_memories 的读-改-写都在 get_mem_lock(key) 内完成
    - LLM 调用一律放在锁外，否则会长时间阻塞该用户
    - 锁内的提交段全程没有 await，因此在 asyncio 语义下天然原子
    - 提交顺序必须是「先写 L1、再裁 L0」，中途崩溃最坏只是重复压缩，不会丢消息
    """
    try:
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

        new_text = format_l0_for_compression(segment)
        if not new_text.strip():
            return  # 片段里没有用户消息（例如全是机器人主动发言），没什么可记的

        # 第二段：锁外调用模型（唯一的长耗时）
        # 顺序刻意把「新片段」放在前面：长上下文里靠后的内容更容易被忽略，
        # 而本轮真正需要处理的是新信息，旧记忆库只是合并参照物。
        user_prompt = (
            f"【新对话片段】（集中注意力处理这里，其中的新信息必须全部保留）\n{new_text}\n\n"
            f"【现有记忆库】（作为合并去重的参照，本身不需要复述）\n{format_l1_for_prompt(old_facts)}\n\n"
            f"请把新片段里的信息合并进记忆库，输出更新后的完整记忆库 JSON。"
            f"注意：新片段里只要出现关于对方的新信息或状态变化（例如搬了城市、换了工作、"
            f"宠物生病、新养成的习惯），都必须体现在结果里，一条也不能漏。"
            f"总字数控制在 {LM_L1_TARGET_CHARS} 字以内。"
        )
        try:
            raw = await memory_llm(LM_COMPRESS_SYSTEM, user_prompt)
        except Exception as e:
            log.error(f"长期记忆压缩调用异常（{key}）：{e}")
            return
        if not raw:
            # memory_llm 内部已记录原因；本次放弃，L0 与 L1 都不动，下轮达到阈值再试
            return

        facts = parse_l1_facts(raw)
        if facts is None:
            return

        total_chars = sum(len(f["c"]) for f in facts)
        if total_chars > LM_L1_ACCEPT_CHARS:
            # 留有余量：模型很难精确控制字数，超过容差才提示（不强制二次压缩，避免反复重写丢细节）
            log.warning(f"长期记忆（{key}）压缩后 {total_chars} 字，超过容差 {LM_L1_ACCEPT_CHARS} 字，"
                        "将依赖注入截断；如持续发生可调小 lm_l1_target_chars")

        # 第三段：锁内原子提交（全程无 await）
        async with get_mem_lock(key):
            if generations.get(key, 0) != gen:
                log.info(f"长期记忆（{key}）压缩结果已作废：期间执行过清空记忆")
                return
            long_memories[key] = {
                "version": (long_memories.get(key) or {}).get("version", 0) + 1,
                "updated": get_beijing_time_str(),
                "facts": facts,
            }
            save_long_memory()                      # 先写 L1
            del memories[key][:seg_len]             # 再裁 L0
            save_memory()
            log.info(f"长期记忆（{key}）已压缩：L0 -{seg_len} 条，现有 {len(memories.get(key, []))} 条；"
                     f"L1 {len(facts)} 条事实 / {total_chars} 字")
    except Exception as e:
        # 兜底：任何意外都不能把主对话链路带崩
        compress_scheduled.discard(key)
        log.exception(f"长期记忆压缩异常（{key}）：{e}")


def maybe_schedule_compress(key: str) -> None:
    """在锁内调用：私聊 L0 达到上限时，启动一次后台压缩。

    被压缩掉的消息一定在此之前就已写入 L0，所以这里不需要额外计数器，
    len(memories[key]) 本身就是状态。
    """
    if not LONG_MEMORY_ENABLED or key.startswith("g:"):
        return
    if len(memories.get(key, [])) < LM_L0_MAX:
        return
    if key in compress_scheduled:
        return  # 已有一次压缩排在队列里
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
            max_tokens=500,
            extra_body={"thinking": {"type": "enabled" if ENABLE_THINKING else "disabled"}},
        )
        reply = clean_reply((resp.choices[0].message.content or "").strip())
        if not reply:
            raise ValueError("模型返回了空内容")
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
async def send_private_msg(ws, uid: int, text: str):
    await ws.send(json.dumps({"action": "send_private_msg",
                              "params": {"user_id": uid, "message": text}},
                             ensure_ascii=False))

async def send_group_msg(ws, gid: int, text: str, at_qq: int | None = None):
    if at_qq is not None:
        message = [{"type": "at", "data": {"qq": str(at_qq)}},
                   {"type": "text", "data": {"text": " " + text}}]
    else:
        message = text
    await ws.send(json.dumps({"action": "send_group_msg",
                              "params": {"group_id": gid, "message": message}},
                             ensure_ascii=False))
    set_group_active(gid)

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

        image_desc = f"（你看到了{total_images}张图片/表情包，内容依次是：{'；'.join(descriptions)}）"

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
                await send_private_msg(ws, uid, CLEAR_MEMORY_REPLY)
                return

            await append_memory(key, "user", combined_text)
            maybe_schedule_compress(key)
            # 快照必须在写入本条消息之后取，否则当前这条会漏出上下文
            reply_msgs = build_reply_msgs(key, None)

        if random.random() > REPLY_PROBABILITY:
            log.info(f"私聊跳过回复 {uid}")
            return

        # 阶段 2（无锁）：调用模型。压缩任务此时可以自由读写 memories，不会影响本轮已取好的快照
        reply, _ok = await chat_with_deepseek(key, reply_msgs)

        # 阶段 3（持锁）：按序落盘回复
        async with get_mem_lock(key):
            await append_memory(key, "assistant", reply)

        await send_private_msg(ws, uid, reply)

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
            await send_group_msg(ws, gid, CLEAR_MEMORY_REPLY,
                                 at_qq=uid if mentioned else None)
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

        group_consecutive_replies[gid] = group_consecutive_replies.get(gid, 0) + 1
        reply, _ok = await chat_with_deepseek(key, reply_msgs)

        async with get_mem_lock(key):
            await append_memory(key, "assistant", reply)

        await send_group_msg(ws, gid, reply, at_qq=uid if mentioned else None)

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
            async with get_mem_lock(key):
                reply_msgs = build_reply_msgs(key, None)
            reply = await proactive_chat(reply_msgs)
            if reply:
                async with get_mem_lock(key):
                    await append_memory(key, "assistant", reply)
                await send_private_msg(ws, uid, reply)

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
            async with get_mem_lock(key):
                reply_msgs = build_reply_msgs(key, None)
            reply = await proactive_chat(reply_msgs)
            if reply:
                async with get_mem_lock(key):
                    await append_memory(key, "assistant", reply)
                await send_group_msg(ws, gid, reply)

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
async def main():
    load_memory()
    load_long_memory()
    if LONG_MEMORY_ENABLED:
        log.info(f"长期记忆已启用（仅私聊）：L0 上限 {LM_L0_MAX} 条，"
                 f"每次压缩 {LM_COMPRESS_COUNT} 条，L1 目标 {LM_L1_TARGET_CHARS} 字，"
                 f"存储于 {LM_FILE}")
    log.info(f"正在连接 NapCat: {WS_URL}")
    while True:
        try:
            async with websockets.connect(WS_URL, ping_interval=20) as ws:
                log.info("已连接 NapCat，机器人上线喵~")
                tasks = [
                    asyncio.create_task(proactive_loop_private(ws)),
                    asyncio.create_task(proactive_loop_group(ws))
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
            log.error(f"连接断开: {e}，5秒后重连...")
            await asyncio.sleep(5)

if __name__ == "__main__":
    if "--debug" in sys.argv:
        asyncio.run(debug_console())
    else:
        asyncio.run(main())
