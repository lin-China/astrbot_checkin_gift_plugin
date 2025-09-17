from astrbot.api.all import *
from astrbot.api.event.filter import command
import json
import os
import datetime
import logging
import random
import hashlib
import uuid
import threading
from typing import Dict, Any, List

logger = logging.getLogger("CheckInGiftPlugin")

# 数据存储路径
DATA_DIR = os.path.join("data", "plugins", "astrbot_checkin_gift_plugin")
os.makedirs(DATA_DIR, exist_ok=True)
DATA_FILE = os.path.join(DATA_DIR, "checkin_gift_data.json")
DATA_LOCK = threading.Lock()

# 随机励志语
MOTIVATIONAL_MESSAGES = [
    "不相信自己的人，连努力的价值都没有 💪",
    "人的梦想，是不会终结的！ ✨",
    "不要为你的失败找借口！ ⚔️",
    "这个世界是残酷的，但依然美丽 🌸",
    "痛苦的时候，就是成长的时候 🌱",
    "不要忘记相信你所坚信的自己的道路 🧭",
    "只要不放弃，总有一天会找到答案 🔍⌛",
    "越是痛苦的时候，越要笑得灿烂 😄🌧️",
    "前进吧，向着深渊的尽头 ⛰️➡️",
    "命运是可以改变的，用自己的双手 👐🔧",
]


def _load_data() -> dict:
    try:
        if not os.path.exists(DATA_FILE):
            # 初始化默认结构
            default = {
                "meta": {
                    "signin_base": 10,
                    "signin_bonus_square": True,
                    "admins": [],  # 管理员ID白名单
                    "version": "1.0.0"
                },
                "contexts": {}
            }
            with open(DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(default, f, ensure_ascii=False, indent=2)
            return default
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"数据加载失败: {e}")
        return {"meta": {"signin_base": 10, "signin_bonus_square": True, "admins": [], "version": "1.0.0"}, "contexts": {}}


def _save_data(data: dict):
    try:
        with DATA_LOCK:
            with open(DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"数据保存失败: {e}")


def _get_context_id(event: AstrMessageEvent) -> str:
    try:
        # 兼容多平台结构（参考原插件）
        if hasattr(event, 'message') and hasattr(event.message, 'source'):
            source = event.message.source
            if hasattr(source, 'group_id') and source.group_id:
                return f"group_{source.group_id}"
            if hasattr(source, 'user_id') and source.user_id:
                return f"private_{source.user_id}"

        if hasattr(event, 'group_id') and event.group_id:
            return f"group_{event.group_id}"
        if hasattr(event, 'user_id') and event.user_id:
            return f"private_{event.user_id}"

        event_str = f"{getattr(event, 'message_id', '')}-{getattr(event, 'time', '')}-{random.random()}"
        return f"ctx_{hashlib.md5(event_str.encode()).hexdigest()[:6]}"
    except Exception as e:
        logger.error(f"上下文ID生成异常: {e}")
        return "default_ctx"


def _get_text_from_event(event) -> str:
    """从事件里尽可能提取原始文本（命令参数解析用）"""
    try:
        if hasattr(event, 'get_plain_text'):
            return event.get_plain_text() or ""
        if hasattr(event, 'get_text'):
            return event.get_text() or ""
        if hasattr(event, 'message'):
            # 有些平台 message 是字符串或对象
            msg = event.message
            if isinstance(msg, str):
                return msg
            if hasattr(msg, 'text'):
                return msg.text or ""
        return getattr(event, 'raw_message', '') or getattr(event, 'text', '') or ""
    except Exception:
        return ""


@register("astrbot_checkin_gift_plugin", "Kimi&Meguminlove", "积分与礼品兑换插件", "1.0.0", "https://github.com/Meguminlove/astrbot_checkin_gift_plugin")
class CheckInGiftPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.data = _load_data()
        # 确保结构
        self.data.setdefault('meta', {"signin_base": 10, "signin_bonus_square": True, "admins": [], "version": "1.0.0"})
        self.data.setdefault('contexts', {})
        _save_data(self.data)

    def _is_admin(self, event: AstrMessageEvent) -> bool:
        try:
            # 平台原生管理员判断
            if hasattr(event, 'is_admin') and getattr(event, 'is_admin'):
                return True
            if hasattr(event, 'get_sender_role') and getattr(event, 'get_sender_role') == 'admin':
                return True
            # 插件配置的管理员白名单
            admins = self.data.get('meta', {}).get('admins', []) or []
            if event.get_sender_id() in admins:
                return True
            return False
        except Exception as e:
            logger.error(f"管理员检查异常: {e}")
            return False

    def _ensure_ctx(self, ctx_id: str) -> Dict[str, Any]:
        ctxs = self.data.setdefault('contexts', {})
        ctx = ctxs.setdefault(ctx_id, {})
        ctx.setdefault('users', {})
        ctx.setdefault('gifts', {})
        return ctx

    def _get_user(self, ctx_id: str, user_id: str) -> Dict[str, Any]:
        ctx = self._ensure_ctx(ctx_id)
        users = ctx['users']
        u = users.setdefault(user_id, {
            'username': '',
            'total_checkins': 0,
            'continuous_days': 0,
            'last_checkin': None,
            'points': 0,
            'purchases': {}
        })
        return u

    def _generate_gift_id(self) -> str:
        return uuid.uuid4().hex[:8]

    def _send_private(self, event: AstrMessageEvent, target_user_id: str, message: str) -> bool:
        """尝试以若干常见方式私聊目标用户，成功返回 True，失败返回 False。"""
        try:
            # 尝试 bot 风格接口
            bot = getattr(event, 'bot', None)
            if bot is not None:
                # 常见方法名
                if hasattr(bot, 'send_private_msg'):
                    bot.send_private_msg(user_id=target_user_id, message=message)
                    return True
                if hasattr(bot, 'send_private_message'):
                    bot.send_private_message(target_user_id, message)
                    return True

            # 尝试 event 自身方法
            if hasattr(event, 'send_private_message'):
                event.send_private_message(target_user_id, message)
                return True
            if hasattr(event, 'send_private_msg'):
                event.send_private_msg(user_id=target_user_id, message=message)
                return True

            # 平台无私聊接口，返回 False
            return False
        except Exception as e:
            logger.error(f"私聊发送失败: {e}")
            return False

    @command("签到", alias=["打卡"])
    async def check_in(self, event: AstrMessageEvent):
        """用户签到，获得积分"""
        try:
            ctx_id = _get_context_id(event)
            user_id = event.get_sender_id()
            username = event.get_sender_name()
            today = datetime.date.today().isoformat()

            ctx = self._ensure_ctx(ctx_id)
            user = self._get_user(ctx_id, user_id)

            # 更新用户名
            user['username'] = username

            if user.get('last_checkin') == today:
                yield event.plain_result("⚠️ 今日已签到，明天再来吧~")
                return

            last_date = user.get('last_checkin')
            if last_date:
                try:
                    last_day = datetime.date.fromisoformat(last_date)
                    if (datetime.date.today() - last_day).days == 1:
                        user['continuous_days'] = user.get('continuous_days', 0) + 1
                    else:
                        user['continuous_days'] = 1
                except Exception:
                    user['continuous_days'] = 1
            else:
                user['continuous_days'] = 1

            # 计算积分（可通过管理员设置基础积分和是否使用连续天数平方为额外加成）
            meta = self.data.setdefault('meta', {})
            base = int(meta.get('signin_base', 10))
            bonus_square = bool(meta.get('signin_bonus_square', True))
            bonus = user['continuous_days'] ** 2 if bonus_square else 0
            points_awarded = base + bonus

            user['total_checkins'] = user.get('total_checkins', 0) + 1
            user['points'] = user.get('points', 0) + points_awarded
            user['last_checkin'] = today

            _save_data(self.data)

            selected_msg = random.choice(MOTIVATIONAL_MESSAGES)
            yield event.plain_result(
                f"✨【契约成立】\n"
                f"📅 连续签到: {user['continuous_days']}天\n"
                f"🎁 获得积分: {points_awarded}分（基础 {base} + 加成 {bonus}）\n"
                f"💳 当前积分: {user['points']}分\n"
                f"💬 契约寄语: {selected_msg}"
            )
        except Exception as e:
            logger.error(f"签到处理异常: {e}", exc_info=True)
            yield event.plain_result("🔧 签到失败，请联系管理员")

    @command("查看礼品", alias=["礼品列表", "礼品查看"])
    async def list_gifts(self, event: AstrMessageEvent):
        try:
            ctx_id = _get_context_id(event)
            ctx = self._ensure_ctx(ctx_id)
            gifts = ctx.get('gifts', {})
            if not gifts:
                yield event.plain_result("当前没有可兑换的礼品。管理员可以使用 /礼品添加 来添加礼品。")
                return

            lines = ["🎁 当前可兑换礼品:"]
            for gid, g in gifts.items():
                lines.append(f"[{gid}] {g.get('name')} | 积分: {g.get('points')} | 库存: {g.get('stock')} | 单人限购: {g.get('per_user_limit', 0)} | 类型: {g.get('type')}")
            lines.append("\n兑换示例：/兑换 礼品ID [数量]")
            yield event.plain_result("\n".join(lines))
        except Exception as e:
            logger.error(f"查看礼品异常: {e}")
            yield event.plain_result("🔧 无法获取礼品列表")

    @command("兑换")
    async def redeem(self, event: AstrMessageEvent):
        try:
            ctx_id = _get_context_id(event)
            user_id = event.get_sender_id()
            username = event.get_sender_name()
            args = _get_text_from_event(event).strip().split()
            # 去掉命令本身（有的 platform 把命令和参数连在一行）
            if len(args) >= 1 and args[0] in ["/兑换", "兑换"]:
                args = args[1:]

            if not args:
                yield event.plain_result("用法: /兑换 <礼品ID> [数量]")
                return

            gift_id = args[0]
            qty = 1
            if len(args) >= 2:
                try:
                    qty = max(1, int(args[1]))
                except Exception:
                    qty = 1

            ctx = self._ensure_ctx(ctx_id)
            gifts = ctx.get('gifts', {})
            gift = gifts.get(gift_id)
            if not gift:
                yield event.plain_result("❌ 未找到该礼品，请检查礼品ID")
                return

            # 检查库存
            if gift.get('stock', 0) < qty:
                yield event.plain_result("❌ 库存不足")
                return

            # 检查用户积分
            user = self._get_user(ctx_id, user_id)
            cost = int(gift.get('points', 0)) * qty
            if user.get('points', 0) < cost:
                yield event.plain_result(f"❌ 积分不足，所需 {cost} 分，当前 {user.get('points',0)} 分")
                return

            # 检查单用户限购
            per_limit = int(gift.get('per_user_limit', 0))
            already = int(user.get('purchases', {}).get(gift_id, 0))
            if per_limit > 0 and already + qty > per_limit:
                yield event.plain_result(f"❌ 超过单用户限购（已兑换 {already}，限购 {per_limit}）")
                return

            # 对于卡密类型，先准备卡密
            if gift.get('type') == 'card':
                codes = gift.get('codes', [])
                if len(codes) < qty:
                    yield event.plain_result("❌ 卡密不足，无法兑换")
                    return
                # 预取 codes
                send_codes = codes[:qty]
                msg = f"🎉 您已成功兑换【{gift.get('name')}】 x{qty}\n卡密如下（请妥善保存）：\n" + "\n".join(send_codes)

                sent = self._send_private(event, user_id, msg)
                if not sent:
                    yield event.plain_result("❌ 私聊发送失败，兑换已取消，请确保机器人可以私聊或联系管理员")
                    return

                # 私聊成功后更新数据（写磁盘）
                with DATA_LOCK:
                    # 移除卡密
                    gift['codes'] = gift.get('codes', [])[qty:]
                    gift['stock'] = gift.get('stock', 0) - qty
                    user['points'] = user.get('points', 0) - cost
                    user['purchases'][gift_id] = user['purchases'].get(gift_id, 0) + qty
                    user['username'] = username
                    _save_data(self.data)

                yield event.plain_result(f"✅ 兑换成功，卡密已私聊发送，请注意查收（若未收到，请检查是否开启了私聊或联系管理员）")
                return

            else:
                # 非卡密类型，直接减库存和积分，私聊发送说明：请联系管理员领取/物品正在处理
                msg = f"🎉 您已成功兑换【{gift.get('name')}】 x{qty}\n请联系群管理员领取或等待后台发放。"
                sent = self._send_private(event, user_id, msg)
                # 即便私聊失败也继续处理（因为有时平台不支持私聊）
                with DATA_LOCK:
                    gift['stock'] = gift.get('stock', 0) - qty
                    user['points'] = user.get('points', 0) - cost
                    user['purchases'][gift_id] = user['purchases'].get(gift_id, 0) + qty
                    user['username'] = username
                    _save_data(self.data)

                if sent:
                    yield event.plain_result("✅ 兑换成功，具体信息已私聊您，请注意查收")
                else:
                    yield event.plain_result("✅ 兑换成功，请联系管理员领取（机器人无法私聊）")
                return

        except Exception as e:
            logger.error(f"兑换异常: {e}", exc_info=True)
            yield event.plain_result("🔧 兑换过程中出现错误，请联系管理员")

    @command("礼品添加")
    async def admin_add_gift(self, event: AstrMessageEvent):
        """管理员命令：礼品添加 名称|积分|库存|单人限购|类型(card/item)|卡密1,卡密2"""
        try:
            if not self._is_admin(event):
                yield event.plain_result("❌ 权限不足，仅管理员可用")
                return

            raw = _get_text_from_event(event).strip()
            # 去掉命令名
            if raw.startswith("/礼品添加") or raw.startswith("礼品添加"):
                raw = raw.split(maxsplit=1)[1] if len(raw.split(maxsplit=1)) > 1 else ""

            if not raw:
                yield event.plain_result("用法: /礼品添加 名称|积分|库存|单人限购|类型(card/item)|卡密1,卡密2\n示例: /礼品添加 50元充值卡|200|10|1|card|ABCD123,EFGH456")
                return

            parts = raw.split('|', 5)
            name = parts[0].strip()
            points = int(parts[1].strip()) if len(parts) > 1 and parts[1].strip().isdigit() else 0
            stock = int(parts[2].strip()) if len(parts) > 2 and parts[2].strip().isdigit() else 0
            per_limit = int(parts[3].strip()) if len(parts) > 3 and parts[3].strip().isdigit() else 0
            gtype = parts[4].strip() if len(parts) > 4 and parts[4].strip() else 'item'
            codes = []
            if len(parts) > 5 and parts[5].strip():
                codes = [c.strip() for c in parts[5].split(',') if c.strip()]

            gift_id = self._generate_gift_id()
            ctx_id = _get_context_id(event)
            ctx = self._ensure_ctx(ctx_id)
            gifts = ctx['gifts']
            gifts[gift_id] = {
                'id': gift_id,
                'name': name,
                'points': points,
                'stock': stock,
                'per_user_limit': per_limit,
                'type': gtype,
                'codes': codes
            }
            _save_data(self.data)
            yield event.plain_result(f"✅ 礼品已添加，ID: {gift_id}")
        except Exception as e:
            logger.error(f"礼品添加异常: {e}")
            yield event.plain_result("🔧 添加礼品失败，请检查参数格式")

    @command("礼品删除")
    async def admin_remove_gift(self, event: AstrMessageEvent):
        try:
            if not self._is_admin(event):
                yield event.plain_result("❌ 权限不足，仅管理员可用")
                return
            raw = _get_text_from_event(event).strip()
            if raw.startswith("/礼品删除") or raw.startswith("礼品删除"):
                raw = raw.split(maxsplit=1)[1] if len(raw.split(maxsplit=1)) > 1 else ""
            gift_id = raw.strip()
            if not gift_id:
                yield event.plain_result("用法: /礼品删除 <礼品ID>")
                return
            ctx_id = _get_context_id(event)
            ctx = self._ensure_ctx(ctx_id)
            gifts = ctx.get('gifts', {})
            if gift_id not in gifts:
                yield event.plain_result("❌ 未找到礼品")
                return
            del gifts[gift_id]
            _save_data(self.data)
            yield event.plain_result("✅ 礼品已删除")
        except Exception as e:
            logger.error(f"礼品删除异常: {e}")
            yield event.plain_result("🔧 删除礼品失败")

    @command("礼品加入卡密")
    async def admin_add_codes(self, event: AstrMessageEvent):
        """用法: /礼品加入卡密 礼品ID|code1,code2"""
        try:
            if not self._is_admin(event):
                yield event.plain_result("❌ 权限不足，仅管理员可用")
                return
            raw = _get_text_from_event(event).strip()
            if raw.startswith("/礼品加入卡密") or raw.startswith("礼品加入卡密"):
                raw = raw.split(maxsplit=1)[1] if len(raw.split(maxsplit=1)) > 1 else ""
            if '|' not in raw:
                yield event.plain_result("用法: /礼品加入卡密 礼品ID|code1,code2")
                return
            gid, codes_raw = raw.split('|', 1)
            gid = gid.strip()
            codes = [c.strip() for c in codes_raw.split(',') if c.strip()]
            if not gid or not codes:
                yield event.plain_result("参数错误")
                return
            ctx_id = _get_context_id(event)
            ctx = self._ensure_ctx(ctx_id)
            gifts = ctx.get('gifts', {})
            gift = gifts.get(gid)
            if not gift:
                yield event.plain_result("❌ 未找到礼品")
                return
            gift.setdefault('codes', [])
            gift['codes'].extend(codes)
            gift['stock'] = gift.get('stock', 0) + len(codes)
            _save_data(self.data)
            yield event.plain_result(f"✅ 成功加入 {len(codes)} 条卡密到礼品 {gid}")
        except Exception as e:
            logger.error(f"加入卡密异常: {e}")
            yield event.plain_result("🔧 操作失败")

    @command("设置签到积分")
    async def admin_set_signin(self, event: AstrMessageEvent):
        """用法: /设置签到积分 基础积分 [square|nosquare]"""
        try:
            if not self._is_admin(event):
                yield event.plain_result("❌ 权限不足，仅管理员可用")
                return
            raw = _get_text_from_event(event).strip()
            if raw.startswith("/设置签到积分") or raw.startswith("设置签到积分"):
                raw = raw.split(maxsplit=1)[1] if len(raw.split(maxsplit=1)) > 1 else ""
            if not raw:
                yield event.plain_result("用法: /设置签到积分 基础积分 [square|nosquare]\n示例: /设置签到积分 20 square")
                return
            parts = raw.split()
            base = int(parts[0]) if parts and parts[0].isdigit() else None
            if base is None:
                yield event.plain_result("基础积分必须为整数")
                return
            square = True
            if len(parts) >= 2 and parts[1].lower() in ('nosquare', 'no', 'false'):
                square = False
            self.data.setdefault('meta', {})['signin_base'] = base
            self.data['meta']['signin_bonus_square'] = square
            _save_data(self.data)
            yield event.plain_result(f"✅ 设置成功：基础积分 {base}，连续加成平方: {'开启' if square else '关闭'}")
        except Exception as e:
            logger.error(f"设置签到积分异常: {e}")
            yield event.plain_result("🔧 操作失败")

    @command("用户加分")
    async def admin_add_points(self, event: AstrMessageEvent):
        """用法: /用户加分 用户ID|数量"""
        try:
            if not self._is_admin(event):
                yield event.plain_result("❌ 权限不足，仅管理员可用")
                return
            raw = _get_text_from_event(event).strip()
            if raw.startswith("/用户加分") or raw.startswith("用户加分"):
                raw = raw.split(maxsplit=1)[1] if len(raw.split(maxsplit=1)) > 1 else ""
            if '|' not in raw:
                yield event.plain_result("用法: /用户加分 用户ID|数量")
                return
            uid, amt = raw.split('|', 1)
            uid = uid.strip(); amt = int(amt.strip())
            ctx_id = _get_context_id(event)
            user = self._get_user(ctx_id, uid)
            user['points'] = user.get('points', 0) + amt
            _save_data(self.data)
            yield event.plain_result(f"✅ 已为用户 {uid} 添加 {amt} 分，当前积分 {user['points']}")
        except Exception as e:
            logger.error(f"用户加分异常: {e}")
            yield event.plain_result("🔧 操作失败")

    @command("用户扣分")
    async def admin_sub_points(self, event: AstrMessageEvent):
        """用法: /用户扣分 用户ID|数量"""
        try:
            if not self._is_admin(event):
                yield event.plain_result("❌ 权限不足，仅管理员可用")
                return
            raw = _get_text_from_event(event).strip()
            if raw.startswith("/用户扣分") or raw.startswith("用户扣分"):
                raw = raw.split(maxsplit=1)[1] if len(raw.split(maxsplit=1)) > 1 else ""
            if '|' not in raw:
                yield event.plain_result("用法: /用户扣分 用户ID|数量")
                return
            uid, amt = raw.split('|', 1)
            uid = uid.strip(); amt = int(amt.strip())
            ctx_id = _get_context_id(event)
            user = self._get_user(ctx_id, uid)
            user['points'] = max(0, user.get('points', 0) - amt)
            _save_data(self.data)
            yield event.plain_result(f"✅ 已为用户 {uid} 扣除 {amt} 分，当前积分 {user['points']}")
        except Exception as e:
            logger.error(f"用户扣分异常: {e}")
            yield event.plain_result("🔧 操作失败")

    @command("查看用户")
    async def admin_view_user(self, event: AstrMessageEvent):
        """用法: /查看用户 用户ID"""
        try:
            if not self._is_admin(event):
                yield event.plain_result("❌ 权限不足，仅管理员可用")
                return
            raw = _get_text_from_event(event).strip()
            if raw.startswith("/查看用户") or raw.startswith("查看用户"):
                raw = raw.split(maxsplit=1)[1] if len(raw.split(maxsplit=1)) > 1 else ""
            uid = raw.strip()
            if not uid:
                yield event.plain_result("用法: /查看用户 用户ID")
                return
            ctx_id = _get_context_id(event)
            user = self._get_user(ctx_id, uid)
            lines = [
                f"用户: {user.get('username','未知')} ({uid})",
                f"签到总天数: {user.get('total_checkins',0)}",
                f"连续签到: {user.get('continuous_days',0)}",
                f"当前积分: {user.get('points',0)}",
                f"已兑换记录: {json.dumps(user.get('purchases', {}), ensure_ascii=False)}"
            ]
            yield event.plain_result("\n".join(lines))
        except Exception as e:
            logger.error(f"查看用户异常: {e}")
            yield event.plain_result("🔧 操作失败")

    @command("积分排行榜")
    async def points_rank(self, event: AstrMessageEvent):
        try:
            ctx_id = _get_context_id(event)
            ctx = self._ensure_ctx(ctx_id)
            users = ctx.get('users', {})
            ranked = sorted(users.items(), key=lambda x: x[1].get('points', 0), reverse=True)[:10]
            lines = ["🏆 积分排行榜"]
            for i, (uid, data) in enumerate(ranked):
                lines.append(f"{i+1}. {data.get('username','未知')} ({uid}) - {data.get('points',0)} 分")
            yield event.plain_result("\n".join(lines))
        except Exception as e:
            logger.error(f"积分排行榜异常: {e}")
            yield event.plain_result("🔧 获取排行榜失败")


# End of plugin
