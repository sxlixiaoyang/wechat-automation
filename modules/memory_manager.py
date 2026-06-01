"""
长期记忆管理器 — 每人独立 JSON 文件
可独立使用，不依赖项目其他模块
"""
import os
import re
import json
from typing import List
from datetime import datetime


class MemoryManager:
    """每个联系人独立的长期记忆文件，简洁准确"""

    MAX_FACTS = 20  # 每人最多记住 20 条

    def __init__(self, memory_dir: str = None):
        if memory_dir:
            self.memory_dir = memory_dir
        else:
            self.memory_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                '.memories')
        os.makedirs(self.memory_dir, exist_ok=True)

    @staticmethod
    def _safe_name(contact_name: str) -> str:
        """安全文件名：去非法字符，限制长度"""
        safe = re.sub(r'[\\/:*?"<>|\s]', '_', contact_name.strip())
        return safe[:80] if safe else 'unknown'

    def _file_path(self, contact_name: str) -> str:
        return os.path.join(self.memory_dir, f'{self._safe_name(contact_name)}.json')

    def load(self, contact_name: str) -> List[str]:
        """加载某人的记忆事实列表"""
        path = self._file_path(contact_name)
        if not os.path.exists(path):
            return []
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return data.get('facts', []) if isinstance(data, dict) else []
        except Exception:
            return []

    def save(self, contact_name: str, facts: List[str]):
        """保存记忆事实"""
        facts = facts[-self.MAX_FACTS:]
        path = self._file_path(contact_name)
        data = {
            'contact': contact_name,
            'facts': facts,
            'updated': datetime.now().isoformat(timespec='minutes'),
        }
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def add_fact(self, contact_name: str, fact: str) -> str:
        """
        添加一条新事实。自动去重（相似度过高跳过），返回操作说明。
        """
        fact = fact.strip()
        if not fact:
            return '空内容，已忽略'

        current = self.load(contact_name)

        # 简单去重：完全相同 或 简洁版（去标点后）相同
        normalized_new = re.sub(r'[，,。.！!？?\s]', '', fact)
        for existing in current:
            normalized_existing = re.sub(r'[，,。.！!？?\s]', '', existing)
            if normalized_new == normalized_existing:
                return f'与已有记忆重复，已跳过'

        current.append(fact)
        self.save(contact_name, current)
        return f'已记住 (共{len(current)}条)'

    def format_for_prompt(self, contact_name: str) -> str:
        """将记忆格式化为可直接嵌入系统提示的文本"""
        facts = self.load(contact_name)
        if not facts:
            return ''
        lines = ['## 关于对方的长期记忆']
        for i, fact in enumerate(facts, 1):
            lines.append(f'{i}. {fact}')
        return '\n'.join(lines)
