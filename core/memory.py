"""会话记忆：多轮对话消息、结构化事实（用户画像）、任务树、事件日志的持久化与滚动摘要。"""
import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import List, Optional


@dataclass
class Task:
    id: str
    title: str
    detail: str = ""
    tool: str = "none"          # none=由模型直接作答
    args: dict = field(default_factory=dict)
    depends_on: List[str] = field(default_factory=list)
    condition: dict = field(default_factory=dict)   # 条件执行：不满足则跳过
    status: str = "pending"     # pending/running/done/failed/waiting/skipped
    result: str = ""
    error: str = ""
    steps: List[dict] = field(default_factory=list)   # [{thought,tool,args,observation,ok}]
    retries: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Task":
        return Task(**{k: v for k, v in d.items() if k in Task.__dataclass_fields__})


@dataclass
class Session:
    id: str
    created_at: float = field(default_factory=time.time)
    title: str = "新会话"
    status: str = "idle"        # idle/running/awaiting_input/done
    messages: List[dict] = field(default_factory=list)  # [{role, content, ts}]
    facts: dict = field(default_factory=dict)           # 结构化记忆：目标岗位/城市/文件等
    summary: str = ""                                   # 早期对话滚动摘要
    tasks: List[Task] = field(default_factory=list)
    artifacts: dict = field(default_factory=dict)       # 任务间传递的产物（jd_text/match/...）
    pending_question: str = ""
    waiting_task_id: str = ""
    event_log: List[dict] = field(default_factory=list)
    llm_calls: int = 0

    def add_message(self, role: str, content: str):
        self.messages.append({"role": role, "content": content, "ts": time.time()})

    def task(self, task_id: str) -> Optional[Task]:
        return next((t for t in self.tasks if t.id == task_id), None)

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    @staticmethod
    def from_dict(d: dict) -> "Session":
        s = Session(id=d["id"])
        for k in ("created_at", "title", "status", "messages", "facts", "summary", "artifacts",
                  "pending_question", "waiting_task_id", "event_log", "llm_calls"):
            if k in d:
                setattr(s, k, d[k])
        s.tasks = [Task.from_dict(t) for t in d.get("tasks", [])]
        return s


class Memory:
    """会话持久化到 data/sessions/*.json，重启不丢；超长对话滚动摘要压缩上下文。
    privacy_mode=True 时 save() 为空操作（会话不落盘，适合敏感材料）。"""

    def __init__(self, sessions_dir: Path, llm=None, max_messages: int = 40,
                 privacy_mode: bool = False):
        self.dir = sessions_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.llm = llm
        self.max_messages = max_messages
        self.privacy_mode = privacy_mode

    def new_session(self, title: str = "新会话") -> Session:
        return Session(id=uuid.uuid4().hex[:12], title=title[:40])

    def save(self, session: Session):
        """落盘即加密（Fernet），密钥见 core/secure_store.py；兼容旧明文读取。
        改动：原实现的这段说明被写在 return 之后（处于函数体中部），
        既不是 docstring 也无法被 help() 看到，这里移到函数首行。"""
        if self.privacy_mode:
            return  # 隐私模式：不落盘
        from core import secure_store
        path = self.dir / f"{session.id}.json"
        secure_store.write_bytes(path, json.dumps(session.to_dict(), ensure_ascii=False,
                                                  indent=1).encode("utf-8"))

    def delete(self, session_id: str) -> bool:
        path = self.dir / f"{session_id}.json"
        if path.exists():
            path.unlink()
            return True
        return False

    def load(self, session_id: str) -> Optional[Session]:
        from core import secure_store
        path = self.dir / f"{session_id}.json"
        if not path.exists():
            return None
        try:
            raw = secure_store.read_bytes(path)
            return Session.from_dict(json.loads(raw.decode("utf-8")))
        except Exception:
            return None

    def compact(self, session: Session):
        """上下文压缩：消息超限时把旧消息滚动摘要成 summary，保留近 12 条原文。
        注意：summary 由 agent._react_payload/_finalize 注入执行与综合上下文
        （改动：原实现只写不读，压缩后的历史实际被丢弃）。"""
        if len(session.messages) <= self.max_messages:
            return
        old = session.messages[:-12]
        text = "\n".join(f"[{m['role']}] {m['content'][:200]}" for m in old)
        if self.llm is not None:
            try:
                session.summary = (session.summary + "\n" + self.llm.chat(
                    [{"role": "user", "content":
                        "请把以下历史对话压缩为要点摘要（保留：目标岗位、城市、文件、用户偏好、已给结论）：\n" + text}],
                    purpose="summarize"))[-2000:]
            except Exception:
                session.summary = (session.summary + "\n" + text)[-2000:]
        else:
            session.summary = (session.summary + "\n" + text)[-2000:]
        session.messages = session.messages[-12:]
