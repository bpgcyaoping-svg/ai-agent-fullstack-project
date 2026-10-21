"""模拟数据库 —— 正式课替换为 SQLAlchemy + PostgreSQL"""
from datetime import datetime
import copy


class FakeDB:
    def __init__(self):
        self._data: dict[int, dict] = {}
        self._next_id = 1

    def create(self, item: dict) -> dict:
        now = datetime.now()
        item["id"] = self._next_id
        item["created_at"] = now
        item["updated_at"] = now
        item.setdefault("status", "active")
        self._data[self._next_id] = copy.deepcopy(item)
        self._next_id += 1
        return dict(self._data[item["id"]])

    def get(self, id_: int) -> dict | None:
        item = self._data.get(id_)
        return dict(item) if item else None

    def list_all(self) -> list[dict]:
        return [dict(v) for v in self._data.values()]

    def update(self, id_: int, updates: dict) -> dict | None:
        item = self._data.get(id_)
        if item is None:
            return None
        for key, value in updates.items():
            if value is not None:
                item[key] = value
        item["updated_at"] = datetime.now()
        return dict(item)

    def delete(self, id_: int) -> bool:
        if id_ in self._data:
            del self._data[id_]
            return True
        return False


# 全局单例
agent_db = FakeDB()
