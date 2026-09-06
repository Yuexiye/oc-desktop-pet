"""插件级 KV 存储 — 模块级持久存储，插件自己存状态

竞品参考（FaustBot）：`AgileStorage` 模块级 KV 持久存储，插件自己存状态，
不必挤进主配置。

功能：
- 插件自带 KV 存储（JSON 文件）
- 线程安全读写
- 自动保存到磁盘

用法：
    kv = PluginKV("my-plugin")
    kv.set("key", "value")
    value = kv.get("key")
    kv.delete("key")
    data = kv.get_all()
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

DEFAULT_KV_DIR = Path.home() / ".oc-pet" / "plugins" / "kv"


class PluginKV:
    """插件级 KV 存储"""
    
    def __init__(self, plugin_id: str, kv_dir: str | Path | None = None):
        self._plugin_id = plugin_id
        self._dir = Path(kv_dir) if kv_dir else DEFAULT_KV_DIR
        self._path = self._dir / f"{plugin_id}.json"
        self._data: dict[str, Any] = {}
        self._lock = threading.Lock()
        
        # 加载现有数据
        self._load()
    
    def _load(self):
        """加载数据"""
        if not self._path.exists():
            return
        
        try:
            self._data = json.loads(self._path.read_text(encoding="utf-8"))
            logger.debug("PluginKV loaded %d keys from %s", len(self._data), self._path)
        except Exception as e:
            logger.warning("PluginKV load failed for %s: %s", self._plugin_id, e)
            self._data = {}
    
    def _save(self):
        """保存到磁盘"""
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
        except Exception as e:
            logger.warning("PluginKV save failed for %s: %s", self._plugin_id, e)
    
    def get(self, key: str, default: Any = None) -> Any:
        """获取值"""
        with self._lock:
            return self._data.get(key, default)
    
    def set(self, key: str, value: Any) -> None:
        """设置值（自动保存）"""
        with self._lock:
            self._data[key] = value
            self._save()
    
    def delete(self, key: str) -> bool:
        """删除键"""
        with self._lock:
            if key in self._data:
                del self._data[key]
                self._save()
                return True
            return False
    
    def get_all(self) -> dict[str, Any]:
        """获取所有数据"""
        with self._lock:
            return dict(self._data)
    
    def set_all(self, data: dict[str, Any]) -> None:
        """设置所有数据"""
        with self._lock:
            self._data = data
            self._save()
    
    def keys(self) -> list[str]:
        """获取所有键"""
        with self._lock:
            return list(self._data.keys())
    
    def __contains__(self, key: str) -> bool:
        with self._lock:
            return key in self._data
    
    def __len__(self) -> int:
        with self._lock:
            return len(self._data)
    
    @property
    def path(self) -> Path:
        """存储文件路径"""
        return self._path


# ════════════════════════════════════════════════════════════
#  全局 KV 注册表
# ════════════════════════════════════════════════════════════

_global_kvs: dict[str, PluginKV] = {}
_registry_lock = threading.Lock()


def get_plugin_kv(plugin_id: str) -> PluginKV:
    """获取插件 KV 存储（单例）"""
    global _global_kvs
    with _registry_lock:
        if plugin_id not in _global_kvs:
            _global_kvs[plugin_id] = PluginKV(plugin_id)
        return _global_kvs[plugin_id]


# ════════════════════════════════════════════════════════════
#  导出
# ════════════════════════════════════════════════════════════

__all__ = [
    "PluginKV",
    "get_plugin_kv",
]