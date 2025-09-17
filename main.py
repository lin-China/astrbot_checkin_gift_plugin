from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from typing import Dict, Any, List  # 移除 Optional

from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
import astrbot.api.message_components as Comp


@register(
    "astrbot_checkin_gift_plugin",
    "your_name",
    "签到积分兑换礼品插件",
    "1.0.3",  # 版本号更新
    "https://example.com/your/repo",
)
class CheckinGiftPlugin(Star):
    """
    签到积分兑换礼品插件。
    数据持久化：
      data/astrbot_checkin_gift_plugin/gifts.json
      data/astrbot_checkin_gift_plugin/users.json
    """

    def __init__(self, context: Context):
        super().__init__(context)
        self._lock = asyncio.Lock()
        self._io_lock = asyncio.Lock()  # 新增：文件写入锁
        self.data_dir = os.path.join(
            self.context.get_data_folder(), "astrbot_checkin_gift_plugin"
        )
        os.makedirs(self.data_dir, exist_ok=True)
        self.gifts_file = os.path.join(self.data_dir, "gifts.json")
        self.users_file = os.path.join(self.data_dir, "users.json")
        self._gifts: Dict[str, Any] = {}
        self._users: Dict[str, Any] = {}
        self._load_all()

    # ---------------------- 数据读写 ----------------------
    def _load_all(self):
        self._gifts = self._load_json(
            self.gifts_file,
            default={
                "config": {"daily_checkin_points": 10},
                "gifts": {},
            },
        )
        self._users = self._load_json(self.users_file, default={"users": {}})

    def _load_json(self, path: str, default: Any) -> Any:
        if not os.path.exists(path):
            return default
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"加载文件失败 {path}: {e}")
            return default

    async def _save_all(self):
        # 原子写入
        await self._save_json(self.gifts_file, self._gifts)
        await self._save_json(self.users_file, self._users)

    async def _save_json(self, path: str, data: Any):
        tmp = path + ".tmp"
        try:
            async with self._io_lock:  # 复用同一个锁
                loop = asyncio.get_running_loop()
                content = json.dumps(data, ensure_ascii=False, indent=2)
                await loop.run_in_executor(
                    None, lambda: open(tmp, "w", encoding="utf-8").write(content)
                )
                if os.path.exists(path):
                    os.remove(path)
                os.replace(tmp, path)
        except Exception as e:
            logger.error(f"保存文件失败 {path}: {e}")

    # ---------------------- 工具方法 ----------------------
    def _get_daily_points(self) -> int:
        return int(self._gifts.get("config", {}).get("daily_checkin_points", 10))

    def _ensure_user(self, uid: str):
        users = self._users["users"]
        if uid not in users:
            users[uid] = {
                "points": 0,
                "last_checkin": "",
                "redeemed": {},
            }

    def _gift_exists(self, name: str) -> bool:
        return name in self._gifts["gifts"]

    def _ensure_runtime_objs(self):
        """运行期自修复，解决热重载/旧实例缺成员导致的 AttributeError。"""
        if not hasattr(self, "_lock") or self._lock is None:
            self._lock = asyncio.Lock()
        if not hasattr(self, "_io_lock") or self._io_lock is None:
            self._io_lock = asyncio.Lock()
        if not hasattr(self, "_gifts") or not hasattr(self, "_users"):
            self._gifts = {"config": {"daily_checkin_points": 10}, "gifts": {}}
            self._users = {"users": {}}
        # 若文件丢失或为空也可尝试补装载
        if not self._gifts.get("gifts"):
            # 轻量安全加载
            try:
                self._gifts = self._load_json(self.gifts_file, default=self._gifts)
            except Exception as e:
                logger.warning(f"重新加载 gifts 失败: {e}")
        if not self._users.get("users"):
            try:
                self._users = self._load_json(self.users_file, default=self._users)
            except Exception as e:
                logger.warning(f"重新加载 users 失败: {e}")

    # ---------------------- 普通用户指令 ----------------------
    @filter.command("checkin")
    async def checkin(self, event: AstrMessageEvent) -> MessageEventResult:
        """每日签到"""
        self._ensure_runtime_objs()  # 新增
        uid = event.get_sender_id()
        today = datetime.now().strftime("%Y-%m-%d")
        async with self._lock:
            self._ensure_user(uid)
            user = self._users["users"][uid]
            if user["last_checkin"] == today:
                yield event.plain_result("今天已经签过到了~")
                return
            points_add = self._get_daily_points()
            user["points"] += points_add
            user["last_checkin"] = today
            await self._save_all()
        yield event.plain_result(f"签到成功！获得 {points_add} 积分，当前积分 {user['points']}")

    @filter.command("gifts")
    async def list_gifts_public(self, event: AstrMessageEvent):
        """查看礼品列表（普通用户）"""
        self._ensure_runtime_objs()  # 新增
        uid = event.get_sender_id()
        async with self._lock:
            self._ensure_user(uid)
            user = self._users["users"][uid]
            gifts = self._gifts["gifts"]
            if not gifts:
                yield event.plain_result("暂无礼品。")
                return
            lines = []
            for name, g in gifts.items():
                remaining = g.get("remaining", 0)
                cost = g.get("cost", 0)
                limit_ = g.get("per_user_limit", 0)
                redeemed_times = user["redeemed"].get(name, 0)
                limit_str = "不限" if limit_ <= 0 else f"{redeemed_times}/{limit_}"
                lines.append(
                    f"{name} | 需要:{cost} | 剩余:{remaining} | 已兑:{limit_str}"
                )
            yield event.plain_result("礼品列表：\n" + "\n".join(lines))

    @filter.command("redeem")
    async def redeem_gift(self, event: AstrMessageEvent, gift_name: str):
        """兑换礼品"""
        self._ensure_runtime_objs()  # 新增
        uid = event.get_sender_id()
        async with self._lock:
            if not self._gift_exists(gift_name):
                yield event.plain_result("礼品不存在。")
                return
            self._ensure_user(uid)
            gift = self._gifts["gifts"][gift_name]
            user = self._users["users"][uid]

            cost = int(gift.get("cost", 0))
            remaining = int(gift.get("remaining", 0))
            per_limit = int(gift.get("per_user_limit", 0))
            user_redeemed = user["redeemed"].get(gift_name, 0)

            if remaining <= 0:
                yield event.plain_result("礼品库存不足。")
                return
            if user["points"] < cost:
                yield event.plain_result(f"积分不足，需要 {cost}，当前 {user['points']}")
                return
            if per_limit > 0 and user_redeemed >= per_limit:
                yield event.plain_result("已达到该礼品个人兑换上限。")
                return

            # 扣积分 / 更新库存 / 记录
            user["points"] -= cost
            gift["remaining"] = remaining - 1
            user["redeemed"][gift_name] = user_redeemed + 1

            code_sent = ""
            codes: List[str] = gift.get("codes", [])
            if codes:
                if len(codes) == 0:
                    yield event.plain_result("礼品卡密已发完。")
                    return
                code_sent = codes.pop(0)
                # 记录派发
                delivered = gift.setdefault("delivered_codes", {})
                delivered.setdefault(uid, []).append(code_sent)

            await self._save_all()

        if code_sent:
            # 尝试私聊
            ok = await self._send_private_code(event, uid, gift_name, code_sent)
            if ok:
                yield event.plain_result(
                    f"兑换成功！礼品 {gift_name} 已私聊发送卡密。剩余积分 {user['points']}"
                )
            else:
                yield event.plain_result(
                    f"兑换成功！(私发失败，下面公开显示) 卡密: {code_sent}\n剩余积分 {user['points']}"
                )
        else:
            yield event.plain_result(
                f"兑换成功！礼品 {gift_name} 已记录，剩余积分 {user['points']}"
            )

    # ---------------------- 管理员指令组 ----------------------
    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command_group("gift")
    def gift_group(self):
        """管理员礼品管理指令组"""
        self._ensure_runtime_objs()  # 新增
        pass

    @gift_group.command("add")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def gift_add(
        self,
        event: AstrMessageEvent,
        name: str,
        cost: int,
        total_quantity: int,
        per_user_limit: int = 0,
    ):
        """添加礼品"""
        self._ensure_runtime_objs()  # 新增
        async with self._lock:
            if self._gift_exists(name):
                yield event.plain_result("礼品已存在。")
                return
            self._gifts["gifts"][name] = {
                "cost": int(cost),
                "total_quantity": int(total_quantity),
                "remaining": int(total_quantity),
                "per_user_limit": int(per_user_limit),
                "codes": [],
                "delivered_codes": {},
            }
            await self._save_all()
        yield event.plain_result(f"礼品 {name} 已添加。")

    @gift_group.command("addcodes")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def gift_add_codes(self, event: AstrMessageEvent, name: str, codes: str):
        """批量添加卡密，逗号分隔"""
        self._ensure_runtime_objs()  # 新增
        code_list = [c.strip() for c in codes.split(",") if c.strip()]
        if not code_list:
            yield event.plain_result("未解析到卡密。")
            return
        async with self._lock:
            if not self._gift_exists(name):
                yield event.plain_result("礼品不存在。")
                return
            gift = self._gifts["gifts"][name]
            gift["codes"].extend(code_list)
            # 同步库存：如果礼品采用卡密模式，可选择把 remaining 同步为 codes 长度+已发?
            # 这里保持 remaining 不自动增，交由管理员 setqty 控制。
            await self._save_all()
        yield event.plain_result(f"已为礼品 {name} 添加 {len(code_list)} 个卡密。")

    @gift_group.command("setpoint")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def gift_set_point(self, event: AstrMessageEvent, name: str, new_cost: int):
        self._ensure_runtime_objs()  # 新增
        async with self._lock:
            if not self._gift_exists(name):
                yield event.plain_result("礼品不存在。")
                return
            self._gifts["gifts"][name]["cost"] = int(new_cost)
            await self._save_all()
        yield event.plain_result("已修改礼品所需积分。")

    @gift_group.command("setqty")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def gift_set_qty(
        self, event: AstrMessageEvent, name: str, new_total: int
    ):
        self._ensure_runtime_objs()  # 新增
        async with self._lock:
            if not self._gift_exists(name):
                yield event.plain_result("礼品不存在。")
                return
            gift = self._gifts["gifts"][name]
            diff = new_total - int(gift.get("total_quantity", 0))
            gift["total_quantity"] = int(new_total)
            gift["remaining"] = max(0, gift.get("remaining", 0) + diff)
            await self._save_all()
        yield event.plain_result("已修改礼品总量与剩余量。")

    @gift_group.command("setlimit")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def gift_set_limit(
        self, event: AstrMessageEvent, name: str, new_limit: int
    ):
        self._ensure_runtime_objs()  # 新增
        async with self._lock:
            if not self._gift_exists(name):
                yield event.plain_result("礼品不存在。")
                return
            self._gifts["gifts"][name]["per_user_limit"] = int(new_limit)
            await self._save_all()
        yield event.plain_result("已修改单用户限购。")

    @gift_group.command("del")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def gift_delete(self, event: AstrMessageEvent, name: str):
        self._ensure_runtime_objs()  # 新增
        async with self._lock:
            if not self._gift_exists(name):
                yield event.plain_result("礼品不存在。")
                return
            del self._gifts["gifts"][name]
            await self._save_all()
        yield event.plain_result("礼品已删除。")

    @gift_group.command("list")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def gift_list_admin(self, event: AstrMessageEvent):
        self._ensure_runtime_objs()  # 新增
        async with self._lock:
            gifts = self._gifts["gifts"]
            if not gifts:
                yield event.plain_result("暂无礼品。")
                return
            lines = []
            for name, g in gifts.items():
                lines.append(
                    f"{name} | cost:{g['cost']} | 剩余:{g['remaining']}/{g['total_quantity']} | 限购:{g['per_user_limit']} | 卡密:{len(g.get('codes', []))}"
                )
        yield event.plain_result("礼品(管理员视图):\n" + "\n".join(lines))

    @gift_group.command("grant")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def gift_grant_points(
        self, event: AstrMessageEvent, qq: str, points: int
    ):
        self._ensure_runtime_objs()  # 新增
        async with self._lock:
            self._ensure_user(qq)
            self._users["users"][qq]["points"] += int(points)
            await self._save_all()
        yield event.plain_result(f"已为 {qq} 增加 {points} 积分。")

    @gift_group.command("deduct")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def gift_deduct_points(
        self, event: AstrMessageEvent, qq: str, points: int
    ):
        self._ensure_runtime_objs()  # 新增
        async with self._lock:
            self._ensure_user(qq)
            user = self._users["users"][qq]
            user["points"] = max(0, user["points"] - int(points))
            await self._save_all()
        yield event.plain_result(f"已为 {qq} 扣除 {points} 积分。")

    @gift_group.command("setcheckin")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def gift_set_checkin(self, event: AstrMessageEvent, points: int):
        self._ensure_runtime_objs()  # 新增
        async with self._lock:
            self._gifts["config"]["daily_checkin_points"] = int(points)
            await self._save_all()
        yield event.plain_result(f"已设置每日签到积分为 {points}")

    @gift_group.command("info")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def gift_info(self, event: AstrMessageEvent, name: str):
        self._ensure_runtime_objs()  # 新增
        async with self._lock:
            if not self._gift_exists(name):
                yield event.plain_result("礼品不存在。")
                return
            g = self._gifts["gifts"][name]
            info = json.dumps(
                {
                    "name": name,
                    "cost": g["cost"],
                    "total_quantity": g["total_quantity"],
                    "remaining": g["remaining"],
                    "per_user_limit": g["per_user_limit"],
                    "codes_count": len(g.get("codes", [])),
                },
                ensure_ascii=False,
                indent=2,
            )
        yield event.plain_result("礼品信息:\n" + info)

    # ---------------------- 私聊发送卡密 ----------------------
    async def _send_private_code(
        self, event: AstrMessageEvent, uid: str, gift_name: str, code: str
    ) -> bool:
        self._ensure_runtime_objs()  # 新增
        """
        尝试私聊发送卡密。若无法构造私聊 session，则返回 False。
        仅在同平台支持私聊时有效，构造 heuristic: platform:private:uid
        """
        try:
            parts = event.unified_msg_origin.split(":")
            if len(parts) < 3:
                return False
            private_umo = f"{parts[0]}:private:{uid}"
            chain = [Comp.Plain(f"你兑换的礼品[{gift_name}]卡密：{code}\n(请妥善保管)")]
            await self.context.send_message(private_umo, chain)
            return True
        except Exception as e:
            logger.warning(f"私聊发送卡密失败: {e}")
            return False

    # ---------------------- 终止 ----------------------
    async def terminate(self):
        self._ensure_runtime_objs()  # 新增
        """插件卸载时保存数据"""
        async with self._lock:
            await self._save_all()


# 兼容旧类名（避免热更新时期引用旧名称导致 _lock 缺失）
CheckInGiftPlugin = CheckinGiftPlugin

