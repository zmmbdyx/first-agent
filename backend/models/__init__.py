"""SQLAlchemy 模型包：全部表在此汇总 import，`Base.metadata` 才是完整的。

注意：`db.init_db()` 只负责 create_all，模型注册靠这里；
漏 import 某个模块会导致对应表静默缺失——新增模型务必同步加入下面的清单。
"""
from __future__ import annotations

from models.base import Base, JSONType, gen_id, iso, utcnow
from models.custom_tool import CustomTool
from models.message import Message
from models.run import Run
from models.session import Session
from models.task import Task
from models.tool_call import ToolCall
from models.trajectory import TrajectoryNode
from models.workspace import Workspace

__all__ = [
    "Base", "JSONType", "gen_id", "iso", "utcnow",
    "Session", "Message", "Run", "Task", "ToolCall",
    "TrajectoryNode", "Workspace", "CustomTool",
]
