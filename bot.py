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

load_dotenv()   # 读 .env

# ---- 机密：从 .env 读取 ----
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
ALI_API_KEY = os.getenv("ALI_API_KEY", "")

# 启动校验，防止忘了配 Key
if not DEEPSEEK_API_KEY or not ALI_API_KEY:
    raise SystemExit("缺少 API Key！请在 .env 中配置")

# ---- 普通配置：从 config.json 读取 ----
with open("config.json", "r", encoding="utf-8") as _f:
    CFG = json.load(_f)

WS_URL = CFG["ws_url"]
DEEPSEEK_BASE_URL = CFG["deepseek_base_url"]
DEEPSEEK_MODEL = CFG["deepseek_model"]
ALI_BASE_URL = CFG["ali_base_url"]
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

MEMORY_MAX_MESSAGES = CFG["memory_max_messages"]
MEMORY_FILE = CFG["memory_file"]

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

# DeepSeek 客户端
client = AsyncOpenAI(
    api_key=DEEPSEEK_API_KEY,
    base_url=DEEPSEEK_BASE_URL,
    timeout=30.0,
    max_retries=2
)

# 视觉模型客户端（通义千问 VL）
vision_client = AsyncOpenAI(
    api_key=ALI_API_KEY,
    base_url=ALI_BASE_URL,
    timeout=30.0,
    max_retries=1
)

# 记忆：key 为 "私聊QQ号" 或 "g:群号"
memories: dict[str, list[dict]] = {}

# 群聊活跃期记录：群号 -> 到期时间戳
group_active_until: dict[int, float] = {}

# 群聊连续回复计数：群号 -> 次数
group_consecutive_replies: dict[int, int] = {}

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
def load_memory():
    global memories
    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            memories = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        memories = {}

def save_memory():
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(memories, f, ensure_ascii=False, indent=2)

def append_memory(key: str, role: str, content: str):
    time_str = get_beijing_time_str()
    content = f"[{time_str}] {content}"
    memories.setdefault(key, []).append({"role": role, "content": content})
    if len(memories[key]) > MEMORY_MAX_MESSAGES:
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

# ---------- DeepSeek 调用 ----------
async def chat_with_deepseek(key: str, user_text: str | None = None) -> str:
    if user_text is not None:
        if "清空记忆" in user_text:
            memories[key] = []
            save_memory()
            return CLEAR_MEMORY_REPLY
        append_memory(key, "user", user_text)

    system_content = build_system_content(key)
    msgs = [{"role": "system", "content": system_content}] + memories.get(key, [])

    try:
        resp = await client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=msgs,
            temperature=1.3,
            top_p=0.9,
            max_tokens=500
        )
        reply = resp.choices[0].message.content.strip()
        reply = clean_reply(reply)
        append_memory(key, "assistant", reply)
        return reply
    except Exception as e:
        log.error(f"DeepSeek 调用失败: {e}")
        fallback = FALLBACK_REPLY
        append_memory(key, "assistant", fallback)
        return fallback

# ---------- 视觉模型调用（图片转文字） ----------
async def image_to_text(image_url: str, sub_type: int = 0) -> str:
    """
    根据 sub_type 选择提示词，识别图片或表情包。
    sub_type: 0=普通图片, 2/7=表情包（QQ常见）
    """
    if sub_type in (2, 7):
        prompt = (
            "请识别这个QQ表情包，用中文描述其画面内容、提取图中文字，"
            "并简要说明这个表情包可能表达的情绪或梗的含义。直接描述，不要解释过程。"
        )
    else:
        prompt = "请用中文描述这张图片的内容及必要的文字原文消息内容。直接描述，不要解释过程。"

    try:
        resp = await vision_client.chat.completions.create(
            model=VISION_MODEL,
            messages=[
                {"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_url}}
                ]}
            ],
            max_tokens=500
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        log.error(f"视觉模型调用失败: {e}")
        return ""

# ---------- 主动消息 ----------
async def proactive_chat(key: str) -> str | None:
    system_content = build_system_content(key)
    msgs = [{"role": "system", "content": system_content}] + memories.get(key, [])
    now_str = get_beijing_time_str()
    msgs.append({"role": "user",
                 "content": f"【当前时间】北京时间 {now_str}\n"
                            "（现在是空闲时间，你心血来潮想主动说句话。"
                            "说一句简短、自然、贴合人设的开场白，不要提到'自动'或'机器人'）"})
    try:
        resp = await client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=msgs,
            temperature=1.3,
            max_tokens=200
        )
        reply = resp.choices[0].message.content.strip()
        reply = clean_reply(reply)
        append_memory(key, "assistant", reply)
        return reply
    except Exception as e:
        log.error(f"主动消息生成失败: {e}")
        return None

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
                    images.append({"url": url, "sub_type": sub_type})
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
                desc = await image_to_text(img["url"], sub_type)
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

        if "清空记忆" in text:
            memories[key] = []
            save_memory()
            await send_private_msg(ws, uid, CLEAR_MEMORY_REPLY)
            return

        append_memory(key, "user", combined_text)

        if random.random() > REPLY_PROBABILITY:
            log.info(f"私聊跳过回复 {uid}")
            return

        reply = await chat_with_deepseek(key)
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
            memories[key] = []
            save_memory()
            await send_group_msg(ws, gid, CLEAR_MEMORY_REPLY,
                                 at_qq=uid if mentioned else None)
            return

        append_memory(key, "user", formatted_text)

        if GROUP_AT_ONLY and not mentioned:
            return

        prob = group_reply_probability(gid, mentioned, combined_text, nickname)
        if random.random() > prob:
            log.info(f"群 {gid} 按概率跳过回复")
            group_consecutive_replies[gid] = 0
            return

        group_consecutive_replies[gid] = group_consecutive_replies.get(gid, 0) + 1
        reply = await chat_with_deepseek(key)
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
            reply = await proactive_chat(str(uid))
            if reply:
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
            reply = await proactive_chat(f"g:{gid}")
            if reply:
                await send_group_msg(ws, gid, reply)

# ---------- 调试模式 ----------
async def debug_console():
    load_memory()
    print("=== 调试模式 ===")
    print("输入内容测试人设；输入 清空记忆 忘记上下文；输入 q 退出\n")
    while True:
        text = input("你: ").strip()
        if text.lower() == "q":
            break
        print(f"{ROBOT_NAME}: {await chat_with_deepseek('debug', text)}\n")

# ---------- 主程序 ----------
async def main():
    load_memory()
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
