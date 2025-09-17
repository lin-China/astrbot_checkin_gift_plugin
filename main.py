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
import shlex
from typing import Dict, Any, List

logger = logging.getLogger("CheckInGiftPlugin")

# 数据存储路径
DATA_DIR = os.path.join("data", "plugins", "astrbot_checkin_gift_plugin")
os.makedirs(DATA_DIR, exist_ok=True)
DATA_FILE = os.path.join(DATA_DIR, "checkin_data.json")

# 默认签到获得积分
DEFAULT_POINTS_PER_CHECKIN = 10

# 励志语录
MOTIVATIONAL_MESSAGES = [
    "不相信自己的人，连努力的价值都没有 💪",
    "孤独，不是被父母责备后难过的那种程度比得上的 🌌",
    "人的梦想，是不会终结的！ ✨",
    "不要为你的失败找借口！ ⚔️",
    "这个世界是残酷的，但依然美丽 🌸",
    "没有伴随着痛苦的教训是毫无意义的 💥",
    "已经无法回来的东西，拥有和舍弃都很痛苦 💔",
    "纵使我身形俱灭，也要将恶鬼斩杀 🔥",
    "所谓今天的自己，正是由昨天的自己积累而成 🧱",
    "痛苦的时候，就是成长的时候 🌱",
]

# 线程锁，确保进程内线程安全（如果你的运行环境是多进程，请使用数据库）
_file_lock = threading.RLock()


def _load_data() -> dict:
    try:
        if not os.path.exists(DATA_FILE):
            return {"contexts": {}}
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"数据加载失败: {e}")
        return {"contexts": {}}


def _save_data(data: dict):
    try:
        # 使用原子写入（写入临时文件然后替换）
        tmp = DATA_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, DATA_FILE)
    except Exception as e:
        logger.error(f"数据保存失败: {e}")


def _get_context_id(event: AstrMessageEvent) -> str:
    """生成多平台兼容的上下文ID（默认：群优先，私聊次之，最后生成备份ID）"""
    try:
        # 优先处理QQ官方Webhook结构
        if hasattr(event, 'message') and hasattr(event.message, 'source'):
            source = event.message.source
            if hasattr(source, 'group_id') and source.group_id:
                return f"group_{source.group_id}"
            if hasattr(source, 'user_id') and source.user_id:
                return f"private_{source.user_id}"

        # 标准事件结构
        if hasattr(event, 'group_id') and getattr(event, 'group_id'):
            return f"group_{event.group_id}"
        if hasattr(event, 'user_id') and getattr(event, 'user_id'):
            return f"private_{event.user_id}"

        # 后备ID
        event_str = f"{getattr(event, 'get_message_id', lambda: 'nomid')()}-{getattr(event, 'get_time', lambda: str(datetime.datetime.now()))()}"
        return f"ctx_{hashlib.md5(event_str.encode()).hexdigest()[:6]}"
    except Exception as e:
        logger.error(f"上下文ID生成异常: {e}")
        return "default_ctx"


def _get_message_text(event: AstrMessageEvent) -> str:
    """尝试从 event 中抽取用户发送的纯文本（兼容多种事件实现）"""
    # 常见方法优先
    for fn in ("get_plain_text", "get_message_text", "get_text"):
        if hasattr(event, fn):
            try:
                text = getattr(event, fn)()
                if text:
                    return str(text)
            except Exception:
                pass
    # event.message
    if hasattr(event, 'message'):
        msg = event.message
        for attr in ("text", "content", "raw", "plain"):
            if hasattr(msg, attr):
                try:
                    t = getattr(msg, attr)
                    if callable(t):
                        t = t()
                    if t:
                        return str(t)
                except Exception:
                    pass
        try:
            return str(msg)
        except Exception:
            pass
    # fallback
    try:
        return str(event)
    except Exception:
        return ""


def _parse_kv_args(s: str) -> Dict[str, str]:
    """解析 key=value 风格的参数：支持引号和空格分隔
    例如：name=卡密 points=100 qty=10 codes=code1,code2 description='hello world'
    返回 dict
    """
    res = {}
    if not s:
        return res
    try:
        parts = shlex.split(s)
        for p in parts:
            if '=' in p:
                k, v = p.split('=', 1)
                res[k.strip()] = v.strip()
    except Exception:
        # 最简单拆分（回退）
        for p in s.split():
            if '=' in p:
                k, v = p.split('=', 1)
                res[k.strip()] = v.strip()
    return res


@register("签到兑换插件", "Kimi&Meguminlove", "积分兑换签到系统", "1.0.0", "https://github.com/Meguminlove/astrbot_checkin_gift_plugin")
class CheckInGiftPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.data = _load_data()
        self._lock = _file_lock

    # ----------------- 内部辅助 -----------------
    def _ensure_ctx(self, ctx_id: str) -> Dict[str, Any]:
        """确保上下文结构存在并返回它的引用"""
        with self._lock:
            ctxs = self.data.setdefault("contexts", {})
            ctx = ctxs.setdefault(ctx_id, {})
            # users, gifts, config, admins
            ctx.setdefault("users", {})
            ctx.setdefault("gifts", {})
            ctx.setdefault("config", {"points_per_checkin": DEFAULT_POINTS_PER_CHECKIN})
            ctx.setdefault("admins", [])
            return ctx

    def _is_admin(self, ctx: Dict[str, Any], user_id: str) -> bool:
        """判断是否为管理员：在ctx的admins列表中"""
        admins = ctx.get("admins", [])
        return str(user_id) in [str(a) for a in admins]

    async def _try_send_private(self, event: AstrMessageEvent, user_id: str, message: str) -> bool:
        """尝试多种方式私聊用户（若平台支持）
        返回 True 表示尝试发送且未报错（不保证接收端最终可见），False 表示所有尝试失败
        """
        # 可能的方法集合（按优先级）
        candidates = []
        if hasattr(event, 'send_private_message'):
            candidates.append(getattr(event, 'send_private_message'))
        if hasattr(event, 'send_private'):
            candidates.append(getattr(event, 'send_private'))
        if hasattr(event, 'reply_private'):
            candidates.append(getattr(event, 'reply_private'))
        # context 层面的发送方法
        if hasattr(self.context, 'send_private'):
            candidates.append(getattr(self.context, 'send_private'))
        if hasattr(self.context, 'bot') and hasattr(self.context.bot, 'send_private'):
            candidates.append(getattr(self.context.bot, 'send_private'))

        for fn in candidates:
            try:
                # 尝试不同签名
                try:
                    # 一参数签名
                    await fn(message)
                    return True
                except TypeError:
                    # 二参数签名 (user_id, message)
                    try:
                        await fn(user_id, message)
                        return True
                    except TypeError:
                        # 三参数或其它签名不尝试
                        pass
            except Exception:
                # 继续尝试下一个
                logger.debug("私聊尝试失败，继续下一个方法", exc_info=True)
                continue
        return False

    def _save(self):
        with self._lock:
            _save_data(self.data)

    # ----------------- 用户命令 -----------------
    @command("签到", alias=["打卡"]) 
    async def check_in(self, event: AstrMessageEvent):
        """签到：获得积分（管理员可配置），并记录连续签到与总天数"""
        try:
            ctx_id = _get_context_id(event)
            user_id = str(event.get_sender_id())
            username = event.get_sender_name() if hasattr(event, 'get_sender_name') else str(user_id)
            today = datetime.date.today().isoformat()

            ctx = self._ensure_ctx(ctx_id)
            users = ctx["users"]
            user = users.setdefault(user_id, {
                "username": username,
                "points": 0,
                "total_days": 0,
                "continuous_days": 0,
                "month_days": 0,
                "last_checkin": None,
                "purchases": {}
            })

            # 更新用户名
            user["username"] = username

            # 重复签到检测
            if user.get("last_checkin") == today:
                yield event.plain_result("⚠️ 今日已签到，请勿重复操作")
                return

            # 连续签到计算
            last_date = user.get("last_checkin")
            current_month = today[:7]
            if last_date:
                last_day = datetime.date.fromisoformat(last_date)
                if (datetime.date.today() - last_day).days == 1:
                    user["continuous_days"] = user.get("continuous_days", 0) + 1
                else:
                    user["continuous_days"] = 1
                # 跨月重置
                if last_date[:7] != current_month:
                    user["month_days"] = 0
            else:
                user["continuous_days"] = 1

            # 积分发放
            points = int(ctx.get("config", {}).get("points_per_checkin", DEFAULT_POINTS_PER_CHECKIN))
            user["points"] = user.get("points", 0) + points

            # 更新统计
            user["total_days"] = user.get("total_days", 0) + 1
            user["month_days"] = user.get("month_days", 0) + 1
            user["last_checkin"] = today

            self._save()

            selected_msg = random.choice(MOTIVATIONAL_MESSAGES)
            yield event.plain_result(
                f"✨【契约成立】\n"
                f"📅 连续签到: {user['continuous_days']} 天\n"
                f"🎁 获得积分: {points} 点（当前积分: {user['points']}）\n"
                f"💬 契约寄语: {selected_msg}"
            )
        except Exception as e:
            logger.error(f"签到处理异常: {e}", exc_info=True)
            yield event.plain_result("🔧 签到服务暂时不可用，请联系管理员")

    @command("礼品列表", alias=["查看礼品", "gifts"]) 
    async def list_gifts(self, event: AstrMessageEvent):
        """列出当前上下文可兑换礼品（公开）"""
        try:
            ctx_id = _get_context_id(event)
            ctx = self._ensure_ctx(ctx_id)
            gifts = ctx.get("gifts", {})
            if not gifts:
                yield event.plain_result("当前没有上架任何礼品。")
                return
            lines = ["🎁 可兑换礼品列表："]
            for gid, g in gifts.items():
                lines.append(f"[{gid}] {g.get('name')} ➤ {g.get('points_required', 0)} 积分 | 库存: {g.get('quantity',0)} | 每人限购: {g.get('per_user_limit',0)} | 类型: {g.get('type','manual')}")
            yield event.plain_result("\n".join(lines))
        except Exception as e:
            logger.error(f"礼品列表异常: {e}", exc_info=True)
            yield event.plain_result("无法获取礼品列表，请稍后再试")

    @command("兑换", alias=["buy", "exchange"]) 
    async def redeem_gift(self, event: AstrMessageEvent):
        """兑换礼品：/兑换 gift_id count(可选，默认1)"""
        try:
            ctx_id = _get_context_id(event)
            user_id = str(event.get_sender_id())
            ctx = self._ensure_ctx(ctx_id)

            text = _get_message_text(event)
            # 去掉命令部分
            parts = text.strip().split(None, 1)
            args_str = parts[1] if len(parts) > 1 else ""
            args = args_str.split()
            if not args:
                yield event.plain_result("用法：/兑换 礼品ID [数量]\n示例：/兑换 abc123 1")
                return
            gift_id = args[0]
            count = 1
            if len(args) > 1:
                try:
                    count = max(1, int(args[1]))
                except Exception:
                    count = 1

            gifts = ctx.get("gifts", {})
            gift = gifts.get(gift_id)
            if not gift:
                yield event.plain_result("未找到指定礼品，请检查礼品ID")
                return
            # 检查库存
            if gift.get("quantity", 0) < count:
                yield event.plain_result(f"库存不足：剩余 {gift.get('quantity',0)} 件")
                return
            # 每人限购
            per_limit = int(gift.get("per_user_limit", 0) or 0)
            user = ctx["users"].setdefault(user_id, {"username": event.get_sender_name(), "points": 0, "purchases": {}})
            bought = int(user.get("purchases", {}).get(gift_id, 0) or 0)
            if per_limit > 0 and bought + count > per_limit:
                yield event.plain_result(f"购买失败：每人限购 {per_limit} 件，你已购买 {bought} 件")
                return
            # 积分检查
            need = int(gift.get("points_required", 0)) * count
            if user.get("points", 0) < need:
                yield event.plain_result(f"兑换失败：需要 {need} 积分，你当前有 {user.get('points',0)} 积分")
                return

            # 如果是卡密类型，检查卡密是否足够
            if gift.get("type") == "code":
                codes = gift.get("codes", [])
                if len(codes) < count:
                    yield event.plain_result("卡密数量不足，无法完成兑换，请联系管理员补充")
                    return

            # 扣除积分与库存
            user["points"] = user.get("points", 0) - need
            gift["quantity"] = gift.get("quantity", 0) - count
            user.setdefault("purchases", {})[gift_id] = bought + count

            # 如果是code，弹出codes并私聊
            if gift.get("type") == "code":
                codes = gift.get("codes", [])
                sent_codes = []
                for _ in range(count):
                    sent_codes.append(codes.pop(0))
                gift["codes"] = codes
                self._save()
                # 尝试私聊
                code_text = "\n".join([f"兑换礼品：{gift.get('name')} -> {c}" for c in sent_codes])
                ok = await self._try_send_private(event, user_id, f"【礼品兑换成功】\n{code_text}")
                if ok:
                    yield event.plain_result("兑换成功，卡密已私聊发送给你。")
                else:
                    # 回退一点安全提示：把卡密放在回复（如果你不想公开可以手动找管理员）
                    yield event.plain_result("兑换成功，但无法私聊发送（平台可能不支持私聊）。以下是你的卡密：\n" + code_text)
                return

            # 否则为手动处理型礼品（例如实物），记录待处理订单并通知管理员（如果有）
            self._save()
            admin_notify = []
            for aid in ctx.get("admins", []):
                admin_notify.append(str(aid))
            notify_msg = f"用户 {user.get('username',user_id)}({user_id}) 兑换了 {gift.get('name')} x{count}，请尽快处理。"
            # 尝试私聊管理员（若平台支持）
            for aid in admin_notify:
                try:
                    await self._try_send_private(event, aid, notify_msg)
                except Exception:
                    pass
            yield event.plain_result("兑换成功，管理员已收到处理请求（如果平台支持私聊的话）。")
        except Exception as e:
            logger.error(f"兑换异常: {e}", exc_info=True)
            yield event.plain_result("兑换失败，请稍后再试或联系管理员")

    # ----------------- 管理员命令 -----------------
    @command("绑定管理员", alias=["bind_admin"]) 
    async def bind_admin(self, event: AstrMessageEvent):
        """当上下文没有任何管理员时，允许第一位调用者绑定为管理员（用于初始化）。之后请使用添加管理员命令管理。"""
        try:
            ctx_id = _get_context_id(event)
            user_id = str(event.get_sender_id())
            ctx = self._ensure_ctx(ctx_id)
            if ctx.get("admins"):
                yield event.plain_result("当前已有管理员，若需要添加管理员请使用 添加管理员 命令")
                return
            ctx.setdefault("admins", []).append(user_id)
            self._save()
            yield event.plain_result("绑定成功：你已成为当前上下文的管理员")
        except Exception as e:
            logger.error("绑定管理员异常", exc_info=True)
            yield event.plain_result("绑定失败，请稍后再试")

    @command("添加管理员", alias=["add_admin"]) 
    async def add_admin(self, event: AstrMessageEvent):
        """管理员添加：/添加管理员 qq号（仅管理员可用）"""
        try:
            ctx_id = _get_context_id(event)
            caller = str(event.get_sender_id())
            ctx = self._ensure_ctx(ctx_id)
            if not self._is_admin(ctx, caller):
                yield event.plain_result("权限不足：仅管理员可添加新管理员")
                return
            text = _get_message_text(event)
            parts = text.strip().split(None, 1)
            if len(parts) < 2:
                yield event.plain_result("用法：/添加管理员 QQ号")
                return
            target = parts[1].strip()
            if target in [str(a) for a in ctx.get('admins', [])]:
                yield event.plain_result("该用户已是管理员")
                return
            ctx.setdefault('admins', []).append(target)
            self._save()
            yield event.plain_result(f"添加成功：{target} 已成为管理员")
        except Exception as e:
            logger.error("添加管理员异常", exc_info=True)
            yield event.plain_result("操作失败")

    @command("删除管理员", alias=["remove_admin"]) 
    async def remove_admin(self, event: AstrMessageEvent):
        try:
            ctx_id = _get_context_id(event)
            caller = str(event.get_sender_id())
            ctx = self._ensure_ctx(ctx_id)
            if not self._is_admin(ctx, caller):
                yield event.plain_result("权限不足：仅管理员可移除管理员")
                return
            text = _get_message_text(event)
            parts = text.strip().split(None, 1)
            if len(parts) < 2:
                yield event.plain_result("用法：/删除管理员 QQ号")
                return
            target = parts[1].strip()
            if target not in [str(a) for a in ctx.get('admins', [])]:
                yield event.plain_result("该用户不是管理员")
                return
            ctx['admins'] = [a for a in ctx.get('admins', []) if str(a) != target]
            self._save()
            yield event.plain_result(f"移除成功：{target} 已被移除管理员")
        except Exception as e:
            logger.error("移除管理员异常", exc_info=True)
            yield event.plain_result("操作失败")

    @command("添加礼品", alias=["addgift"]) 
    async def add_gift(self, event: AstrMessageEvent):
        """添加礼品（管理员）
        用法示例：
        /添加礼品 name=卡密A points=100 qty=10 per_user_limit=1 type=code codes=AAA,BBB,CCC description='测试卡密'
        """
        try:
            ctx_id = _get_context_id(event)
            caller = str(event.get_sender_id())
            ctx = self._ensure_ctx(ctx_id)
            if not self._is_admin(ctx, caller):
                yield event.plain_result("权限不足：仅管理员可添加礼品")
                return
            text = _get_message_text(event)
            parts = text.strip().split(None, 1)
            args = _parse_kv_args(parts[1] if len(parts) > 1 else "")
            name = args.get('name') or args.get('title')
            if not name:
                yield event.plain_result("添加礼品失败：缺少 name 参数")
                return
            points = int(args.get('points', 0))
            qty = int(args.get('qty', args.get('quantity', 0) or 0))
            per_user = int(args.get('per_user_limit', args.get('limit', 0) or 0))
            gtype = args.get('type', 'manual')
            description = args.get('description', '')
            codes = []
            if gtype == 'code' and 'codes' in args:
                # codes 使用逗号分割
                codes = [c.strip() for c in args['codes'].split(',') if c.strip()]

            gift_id = uuid.uuid4().hex[:8]
            gift = {
                'name': name,
                'points_required': points,
                'quantity': qty,
                'per_user_limit': per_user,
                'type': gtype,
                'codes': codes,
                'description': description
            }
            ctx['gifts'][gift_id] = gift
            self._save()
            yield event.plain_result(f"添加成功：礼品ID {gift_id}，名称 {name}")
        except Exception as e:
            logger.error("添加礼品异常", exc_info=True)
            yield event.plain_result("添加礼品失败")

    @command("编辑礼品", alias=["editgift"]) 
    async def edit_gift(self, event: AstrMessageEvent):
        """编辑礼品（管理员）
        用法：/编辑礼品 gift_id key=value ... 支持: name, points, qty, per_user_limit, type, codes (覆写)
        示例：/编辑礼品 abc123 points=200 qty=20
        """
        try:
            ctx_id = _get_context_id(event)
            caller = str(event.get_sender_id())
            ctx = self._ensure_ctx(ctx_id)
            if not self._is_admin(ctx, caller):
                yield event.plain_result("权限不足：仅管理员可编辑礼品")
                return
            text = _get_message_text(event)
            parts = text.strip().split(None, 2)
            if len(parts) < 3:
                yield event.plain_result("用法：/编辑礼品 gift_id key=value ...")
                return
            gift_id = parts[1]
            args = _parse_kv_args(parts[2])
            gift = ctx['gifts'].get(gift_id)
            if not gift:
                yield event.plain_result("未找到指定礼品ID")
                return
            if 'name' in args:
                gift['name'] = args['name']
            if 'points' in args:
                gift['points_required'] = int(args['points'])
            if 'qty' in args or 'quantity' in args:
                gift['quantity'] = int(args.get('qty', args.get('quantity')))
            if 'per_user_limit' in args or 'limit' in args:
                gift['per_user_limit'] = int(args.get('per_user_limit', args.get('limit')))
            if 'type' in args:
                gift['type'] = args['type']
            if 'codes' in args:
                gift['codes'] = [c.strip() for c in args['codes'].split(',') if c.strip()]
            if 'description' in args:
                gift['description'] = args['description']
            self._save()
            yield event.plain_result(f"编辑成功：{gift_id}")
        except Exception as e:
            logger.error("编辑礼品异常", exc_info=True)
            yield event.plain_result("编辑礼品失败")

    @command("删除礼品", alias=["removegift"]) 
    async def remove_gift(self, event: AstrMessageEvent):
        try:
            ctx_id = _get_context_id(event)
            caller = str(event.get_sender_id())
            ctx = self._ensure_ctx(ctx_id)
            if not self._is_admin(ctx, caller):
                yield event.plain_result("权限不足：仅管理员可删除礼品")
                return
            text = _get_message_text(event)
            parts = text.strip().split(None, 1)
            if len(parts) < 2:
                yield event.plain_result("用法：/删除礼品 gift_id")
                return
            gift_id = parts[1].strip()
            if gift_id not in ctx['gifts']:
                yield event.plain_result("未找到礼品ID")
                return
            del ctx['gifts'][gift_id]
            self._save()
            yield event.plain_result(f"礼品 {gift_id} 已删除")
        except Exception as e:
            logger.error("删除礼品异常", exc_info=True)
            yield event.plain_result("删除礼品失败")

    @command("设置签到积分", alias=["set_points_per_checkin"]) 
    async def set_points_per_checkin(self, event: AstrMessageEvent):
        """管理员设置每次签到获得的积分：/设置签到积分 10"""
        try:
            ctx_id = _get_context_id(event)
            caller = str(event.get_sender_id())
            ctx = self._ensure_ctx(ctx_id)
            if not self._is_admin(ctx, caller):
                yield event.plain_result("权限不足：仅管理员可设置签到积分")
                return
            text = _get_message_text(event)
            parts = text.strip().split()
            if len(parts) < 2:
                yield event.plain_result("用法：/设置签到积分 数值")
                return
            try:
                v = int(parts[1])
            except Exception:
                yield event.plain_result("积分必须为整数")
                return
            ctx.setdefault('config', {})['points_per_checkin'] = v
            self._save()
            yield event.plain_result(f"设置成功：每次签到可获得 {v} 积分")
        except Exception as e:
            logger.error("设置签到积分异常", exc_info=True)
            yield event.plain_result("设置失败")

    @command("查询用户", alias=["userinfo"]) 
    async def query_user(self, event: AstrMessageEvent):
        """查询用户积分与购买记录（管理员）/ 查询自己的信息（用户）
        /查询用户 [QQ]
        """
        try:
            ctx_id = _get_context_id(event)
            caller = str(event.get_sender_id())
            ctx = self._ensure_ctx(ctx_id)
            text = _get_message_text(event)
            parts = text.strip().split()
            target = None
            if len(parts) > 1:
                target = parts[1].strip()
            else:
                target = caller

            user = ctx['users'].get(str(target))
            if not user:
                yield event.plain_result("未找到该用户数据")
                return
            lines = [f"用户：{user.get('username',target)} ({target})", f"积分：{user.get('points',0)}", f"累计签到：{user.get('total_days',0)}", f"连续签到：{user.get('continuous_days',0)}", f"本月签到：{user.get('month_days',0)}"]
            if user.get('purchases'):
                lines.append("购买记录：")
                for gid, cnt in user.get('purchases', {}).items():
                    g = ctx['gifts'].get(gid)
                    lines.append(f" - {g.get('name','未知')}({gid}) x{cnt}")
            yield event.plain_result("\n".join(lines))
        except Exception as e:
            logger.error("查询用户异常", exc_info=True)
            yield event.plain_result("查询失败")

    @command("管理员加分", alias=["addpoints"]) 
    async def admin_add_points(self, event: AstrMessageEvent):
        """管理员为用户加/减积分：/管理员加分 QQ 数量（可为负数扣分）"""
        try:
            ctx_id = _get_context_id(event)
            caller = str(event.get_sender_id())
            ctx = self._ensure_ctx(ctx_id)
            if not self._is_admin(ctx, caller):
                yield event.plain_result("权限不足：仅管理员可操作")
                return
            text = _get_message_text(event)
            parts = text.strip().split()
            if len(parts) < 3:
                yield event.plain_result("用法：/管理员加分 QQ 数量（数量可为负数）")
                return
            target = parts[1].strip()
            try:
                delta = int(parts[2])
            except Exception:
                yield event.plain_result("数量必须为整数")
                return
            user = ctx['users'].setdefault(target, {"username": target, "points": 0, 'purchases':{}})
            user['points'] = user.get('points',0) + delta
            self._save()
            yield event.plain_result(f"操作成功：{target} 的积分已变更为 {user['points']}")
        except Exception as e:
            logger.error("加分异常", exc_info=True)
            yield event.plain_result("操作失败")

    @command("礼品详情", alias=["giftinfo"]) 
    async def gift_info(self, event: AstrMessageEvent):
        """显示指定礼品的详细信息：/礼品详情 gift_id"""
        try:
            ctx_id = _get_context_id(event)
            ctx = self._ensure_ctx(ctx_id)
            text = _get_message_text(event)
            parts = text.strip().split()
            if len(parts) < 2:
                yield event.plain_result("用法：/礼品详情 gift_id")
                return
            gid = parts[1].strip()
            gift = ctx['gifts'].get(gid)
            if not gift:
                yield event.plain_result("未找到指定礼品")
                return
            lines = [f"礼品：{gift.get('name')} ({gid})", f"所需积分：{gift.get('points_required',0)}", f"库存：{gift.get('quantity',0)}", f"每人限购：{gift.get('per_user_limit',0)}", f"类型：{gift.get('type','manual')}"]
            if gift.get('description'):
                lines.append(f"说明：{gift.get('description')}")
            if gift.get('type') == 'code':
                lines.append(f"剩余卡密数：{len(gift.get('codes',[]))}")
            yield event.plain_result("\n".join(lines))
        except Exception as e:
            logger.error("礼品详情异常", exc_info=True)
            yield event.plain_result("查询失败")

    @command("补充卡密", alias=["addcodes"]) 
    async def add_codes(self, event: AstrMessageEvent):
        """为卡密型礼品补充卡密（管理员）
        用法：/补充卡密 gift_id code1,code2,code3
        或：/补充卡密 gift_id codes="code1,code2"
        """
        try:
            ctx_id = _get_context_id(event)
            caller = str(event.get_sender_id())
            ctx = self._ensure_ctx(ctx_id)
            if not self._is_admin(ctx, caller):
                yield event.plain_result("权限不足：仅管理员可补充卡密")
                return
            text = _get_message_text(event)
            parts = text.strip().split(None, 2)
            if len(parts) < 3:
                yield event.plain_result("用法：/补充卡密 gift_id codes...（以逗号分隔）")
                return
            gid = parts[1].strip()
            codes_str = parts[2].strip()
            gift = ctx['gifts'].get(gid)
            if not gift:
                yield event.plain_result("未找到礼品ID")
                return
            if gift.get('type') != 'code':
                yield event.plain_result("该礼品不是卡密类型")
                return
            new_codes = [c.strip() for c in codes_str.split(',') if c.strip()]
            gift.setdefault('codes', []).extend(new_codes)
            # 可选：若未设置数量则自动增加库存
            gift['quantity'] = gift.get('quantity', 0) + len(new_codes)
            self._save()
            yield event.plain_result(f"补充成功：新增 {len(new_codes)} 条卡密，库存 +{len(new_codes)}")
        except Exception as e:
            logger.error("补充卡密异常", exc_info=True)
            yield event.plain_result("补充失败")


# 结束 of plugin
# 注意：
# - 本插件使用本地 JSON 持久化（适用于单进程运行）。若你的机器人是多进程部署，请替换为数据库持久化以避免数据竞争。
# - 私聊发送依赖运行环境/平台是否支持私聊接口，插件尝试了多种常见接口方法并在失败时回退为公共回复。
# - 管理员初始绑定请使用 /绑定管理员 在无管理员时完成初始化。
# - 指令参数使用 key=value 风格较稳定（支持引号），也支持部分简单位置参数。
