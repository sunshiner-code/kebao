"""计划与待办：同一时刻只有一项执行中；连续多轮未更新时由主循环注入提醒。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

STATUS_LABELS = {"pending": "待执行", "in_progress": "执行中", "completed": "已完成", "skipped": "已跳过", "failed": "失败"}


@dataclass
class TodoItem:
    id: str
    content: str
    status: str = "pending"
    tool: str | None = None
    args: dict = field(default_factory=dict)
    stage_eval: bool = False
    hitl: str | None = None
    origin: str = "plan"  # plan / reflection / human
    step_key: str | None = None
    attempts: int = 0
    background: bool = False
    note: str = ""
    condition: str | None = None  # 开始执行时才判断的条件（依赖前序步骤的结果）


class TodoList:
    def __init__(self, items: list[TodoItem] | None = None) -> None:
        self.items: list[TodoItem] = items or []
        self.rounds_since_update = 0
        self._counter = len(self.items)

    def new_id(self) -> str:
        self._counter += 1
        return f"T{self._counter:02d}"

    def write(self, items: list[TodoItem]) -> None:
        in_progress = [i for i in items if i.status == "in_progress"]
        if len(in_progress) > 1:
            raise ValueError("同一时刻只能有一项待办处于执行中")
        self.items = items
        self._counter = max([self._counter] + [int(i.id[1:]) for i in items if i.id[1:].isdigit()])
        self.rounds_since_update = 0

    def get(self, todo_id: str) -> TodoItem | None:
        return next((i for i in self.items if i.id == todo_id), None)

    def current(self) -> TodoItem | None:
        return next((i for i in self.items if i.status == "in_progress"), None)

    def next_pending(self) -> TodoItem | None:
        return next((i for i in self.items if i.status == "pending"), None)

    def set_status(self, todo_id: str, status: str, note: str = "") -> None:
        item = self.get(todo_id)
        if item is None:
            raise KeyError(todo_id)
        if status == "in_progress":
            for other in self.items:
                if other.status == "in_progress" and other.id != todo_id:
                    other.status = "pending"
        item.status = status
        if note:
            item.note = note
        self.rounds_since_update = 0

    def insert_after(self, todo_id: str | None, new_items: list[TodoItem]) -> None:
        if todo_id is None:
            self.items.extend(new_items)
            return
        idx = next((i for i, t in enumerate(self.items) if t.id == todo_id), len(self.items) - 1)
        self.items[idx + 1 : idx + 1] = new_items
        self.rounds_since_update = 0

    def all_done(self) -> bool:
        return bool(self.items) and all(i.status in ("completed", "skipped", "failed") for i in self.items)

    def progress(self) -> float:
        if not self.items:
            return 0.0
        done = sum(1 for i in self.items if i.status in ("completed", "skipped", "failed"))
        return done / len(self.items)

    def to_list(self) -> list[dict]:
        return [asdict(i) | {"status_label": STATUS_LABELS[i.status]} for i in self.items]

    @classmethod
    def from_list(cls, data: list[dict]) -> "TodoList":
        items = [TodoItem(**{k: v for k, v in d.items() if k in TodoItem.__dataclass_fields__}) for d in data]
        return cls(items)
