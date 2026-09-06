"""备份恢复服务 — 完整备份 + 清单 + SHA-256 校验 + 迁移账本 + 一键恢复

竞品参考（Mio）：`backup_service.py` 完整备份 + 清单 + SHA-256 校验 +
迁移账本 + 一键恢复。验收：换机器/升级失败能完整回滚。

功能：
- 完整备份：备份指定目录到 zip 文件
- 清单生成：生成备份清单（文件列表 + SHA-256）
- SHA-256 校验：验证备份完整性
- 迁移账本：记录备份历史（时间、版本、文件数等）
- 一键恢复：从备份恢复数据
"""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_BACKUP_DIR = Path.home() / ".oc-pet" / "backups"
DEFAULT_DATA_DIR = Path.home() / ".oc-pet"


@dataclass
class BackupManifest:
    """备份清单"""
    timestamp: float = 0.0          # 备份时间戳
    backup_path: str = ""           # 备份文件路径
    source_dirs: list[str] = field(default_factory=list)  # 源目录列表
    file_count: int = 0             # 文件数
    total_size: int = 0             # 总大小（字节）
    files: list[dict] = field(default_factory=list)  # 文件清单 [{path, size, sha256}]
    version: str = "1.0"            # 备份格式版本


@dataclass
class BackupLedger:
    """迁移账本"""
    backup_id: str = ""             # 备份 ID
    timestamp: float = 0.0          # 备份时间
    backup_path: str = ""           # 备份文件路径
    file_count: int = 0             # 文件数
    total_size: int = 0             # 总大小
    sha256: str = ""                # 备份文件 SHA-256
    success: bool = True            # 是否成功
    error: str = ""                 # 错误信息


class BackupService:
    """备份恢复服务"""
    
    def __init__(
        self,
        data_dir: str | Path = DEFAULT_DATA_DIR,
        backup_dir: str | Path = DEFAULT_BACKUP_DIR,
    ):
        self._data_dir = Path(data_dir)
        self._backup_dir = Path(backup_dir)
        self._backup_dir.mkdir(parents=True, exist_ok=True)
        self._ledger_path = self._backup_dir / "ledger.json"
        self._ledger: list[dict] = []
        
        # 加载账本
        self._load_ledger()
    
    def _load_ledger(self):
        """加载迁移账本"""
        if not self._ledger_path.exists():
            return
        try:
            self._ledger = json.loads(self._ledger_path.read_text(encoding="utf-8"))
            logger.info("Loaded %d backup records", len(self._ledger))
        except Exception as e:
            logger.warning("Failed to load backup ledger: %s", e)
            self._ledger = []
    
    def _save_ledger(self):
        """保存迁移账本"""
        try:
            self._ledger_path.write_text(
                json.dumps(self._ledger, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
        except Exception as e:
            logger.warning("Failed to save backup ledger: %s", e)
    
    def _sha256(self, path: str | Path) -> str:
        """计算文件 SHA-256"""
        sha256 = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                sha256.update(chunk)
        return sha256.hexdigest()
    
    def backup(
        self,
        source_dirs: list[str | Path] | None = None,
        label: str = "",
    ) -> Optional[BackupManifest]:
        """执行备份
        
        Args:
            source_dirs: 要备份的目录列表（默认备份 .oc-pet）
            label: 备份标签（可选）
        
        Returns:
            BackupManifest 或 None（失败时）
        """
        if not source_dirs:
            source_dirs = [self._data_dir]
        
        # 生成备份文件名
        timestamp = time.time()
        timestamp_str = time.strftime("%Y%m%d_%H%M%S", time.localtime(timestamp))
        backup_id = f"backup_{timestamp_str}"
        if label:
            backup_id = f"{backup_id}_{label}"
        backup_path = self._backup_dir / f"{backup_id}.zip"
        
        logger.info("Starting backup: %s", backup_id)
        
        try:
            manifest = BackupManifest(
                timestamp=timestamp,
                backup_path=str(backup_path),
            )
            
            # 收集文件
            all_files = []
            for src_dir in source_dirs:
                src_dir = Path(src_dir)
                if not src_dir.exists():
                    logger.warning("Source dir not found: %s", src_dir)
                    continue
                manifest.source_dirs.append(str(src_dir))
                
                # 遍历目录
                for file_path in src_dir.rglob("*"):
                    if file_path.is_file():
                        rel_path = file_path.relative_to(src_dir)
                        all_files.append((src_dir, file_path, rel_path))
            
            if not all_files:
                logger.warning("No files to backup")
                return None
            
            # 创建 zip 文件
            with zipfile.ZipFile(backup_path, "w", zipfile.ZIP_DEFLATED) as zf:
                total_size = 0
                for src_dir, file_path, rel_path in all_files:
                    try:
                        # 添加到 zip（保持相对路径）
                        arcname = str(src_dir.name / rel_path)
                        zf.write(file_path, arcname)
                        
                        # 计算 SHA-256
                        sha256 = self._sha256(file_path)
                        file_size = file_path.stat().st_size
                        total_size += file_size
                        
                        manifest.files.append({
                            "path": str(rel_path),
                            "size": file_size,
                            "sha256": sha256,
                        })
                    except Exception as e:
                        logger.warning("Failed to backup %s: %s", file_path, e)
            
            manifest.file_count = len(manifest.files)
            manifest.total_size = total_size
            
            # 计算备份文件 SHA-256
            backup_sha256 = self._sha256(backup_path)
            
            # 记录到账本
            ledger_entry = {
                "backup_id": backup_id,
                "timestamp": timestamp,
                "backup_path": str(backup_path),
                "file_count": manifest.file_count,
                "total_size": manifest.total_size,
                "sha256": backup_sha256,
                "success": True,
                "error": "",
            }
            self._ledger.append(ledger_entry)
            self._save_ledger()
            
            logger.info("Backup completed: %s (%d files, %d bytes)", 
                       backup_id, manifest.file_count, manifest.total_size)
            
            return manifest
            
        except Exception as e:
            logger.error("Backup failed: %s", e)
            # 记录失败到账本
            ledger_entry = {
                "backup_id": backup_id,
                "timestamp": timestamp,
                "backup_path": str(backup_path),
                "file_count": 0,
                "total_size": 0,
                "sha256": "",
                "success": False,
                "error": str(e),
            }
            self._ledger.append(ledger_entry)
            self._save_ledger()
            return None
    
    def verify_backup(self, backup_path: str | Path) -> bool:
        """验证备份完整性（检查 SHA-256）"""
        backup_path = Path(backup_path)
        if not backup_path.exists():
            logger.warning("Backup file not found: %s", backup_path)
            return False
        
        try:
            # 读取备份文件内的清单
            with zipfile.ZipFile(backup_path, "r") as zf:
                # 查找清单文件
                if "_manifest.json" not in zf.namelist():
                    logger.warning("No manifest in backup: %s", backup_path)
                    return False
                
                manifest_data = json.loads(zf.read("_manifest.json"))
                
                # 验证每个文件
                for file_info in manifest_data.get("files", []):
                    path = file_info.get("path")
                    expected_sha256 = file_info.get("sha256")
                    
                    if path not in zf.namelist():
                        logger.warning("File missing in backup: %s", path)
                        return False
                    
                    # 计算实际 SHA-256
                    actual_sha256 = hashlib.sha256(zf.read(path)).hexdigest()
                    if actual_sha256 != expected_sha256:
                        logger.warning("SHA-256 mismatch: %s (expected=%s, actual=%s)", 
                                     path, expected_sha256, actual_sha256)
                        return False
            
            logger.info("Backup verified: %s", backup_path)
            return True
            
        except Exception as e:
            logger.error("Backup verification failed: %s", e)
            return False
    
    def restore(
        self,
        backup_path: str | Path,
        target_dir: str | Path = DEFAULT_DATA_DIR,
        verify: bool = True,
    ) -> bool:
        """从备份恢复数据
        
        Args:
            backup_path: 备份文件路径
            target_dir: 目标目录（默认 .oc-pet）
            verify: 是否先验证备份完整性
        
        Returns:
            是否成功
        """
        backup_path = Path(backup_path)
        target_dir = Path(target_dir)
        
        if not backup_path.exists():
            logger.error("Backup file not found: %s", backup_path)
            return False
        
        # 验证备份
        if verify:
            if not self.verify_backup(backup_path):
                logger.error("Backup verification failed: %s", backup_path)
                return False
        
        logger.info("Restoring from %s to %s", backup_path, target_dir)
        
        try:
            # 确保目标目录存在
            target_dir.mkdir(parents=True, exist_ok=True)
            
            # 解压备份
            with zipfile.ZipFile(backup_path, "r") as zf:
                for member in zf.namelist():
                    # 安全检查：防止 zip slip 攻击
                    if member.startswith("/") or ".." in member:
                        logger.warning("Skipping unsafe path: %s", member)
                        continue
                    
                    target_path = target_dir / member
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    
                    with zf.open(member) as src, open(target_path, "wb") as dst:
                        shutil.copyfileobj(src, dst)
            
            logger.info("Restore completed: %s", backup_path)
            return True
            
        except Exception as e:
            logger.error("Restore failed: %s", e)
            return False
    
    def get_ledger(self) -> list[dict]:
        """获取迁移账本"""
        return list(self._ledger)
    
    def get_latest_backup(self) -> Optional[dict]:
        """获取最新备份"""
        if not self._ledger:
            return None
        return self._ledger[-1]
    
    def list_backups(self) -> list[dict]:
        """列出所有备份（从账本读取）"""
        return self._ledger
    
    def delete_backup(self, backup_id: str) -> bool:
        """删除备份文件"""
        for entry in self._ledger:
            if entry.get("backup_id") == backup_id:
                backup_path = Path(entry.get("backup_path", ""))
                if backup_path.exists():
                    backup_path.unlink()
                    logger.info("Deleted backup: %s", backup_id)
                # 从账本移除
                self._ledger = [e for e in self._ledger if e.get("backup_id") != backup_id]
                self._save_ledger()
                return True
        return False


# ════════════════════════════════════════════════════════════
#  全局服务
# ════════════════════════════════════════════════════════════

_global_service: Optional[BackupService] = None


def get_backup_service() -> BackupService:
    """获取全局备份服务（单例）"""
    global _global_service
    if _global_service is None:
        _global_service = BackupService()
    return _global_service


# ════════════════════════════════════════════════════════════
#  导出
# ════════════════════════════════════════════════════════════

__all__ = [
    "BackupManifest",
    "BackupLedger",
    "BackupService",
    "get_backup_service",
]