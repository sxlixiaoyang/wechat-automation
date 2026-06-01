"""
文件管理器 — 安全沙盒到 ai_workspace 目录
可独立使用，不依赖项目其他模块
"""
import os
import re
import shutil


class FileManager:
    """AI 可调用的文件读写编辑删除，所有操作限制在工作区内"""

    MAX_FILE_SIZE = 200 * 1024  # 单文件最大 200KB

    def __init__(self, workspace_dir: str = None):
        if workspace_dir:
            self.workspace = workspace_dir
        else:
            # 从调用方推断项目根目录
            self.workspace = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                'ai_workspace')
        os.makedirs(self.workspace, exist_ok=True)

    def _resolve(self, path: str) -> str:
        """安全路径解析：禁止 ..、~、绝对路径穿越"""
        normalized = os.path.normpath(os.path.join(self.workspace, path))
        if not normalized.startswith(os.path.normpath(self.workspace)):
            raise PermissionError(f'禁止访问工作区外的路径: {path}')
        return normalized

    def write_file(self, path: str, content: str) -> str:
        """创建或覆盖文件，自动创建父目录"""
        full = self._resolve(path)
        size = len(content.encode('utf-8'))
        if size > self.MAX_FILE_SIZE:
            return f'错误：内容过大 ({size}B)，单文件上限 {self.MAX_FILE_SIZE}B'
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, 'w', encoding='utf-8') as f:
            f.write(content)
        rel = os.path.relpath(full, self.workspace)
        return f'文件已写入: {rel} ({len(content)}字符, {size}B)'

    def read_file(self, path: str, max_lines: int = 200) -> str:
        """读取文件内容，默认最多200行"""
        full = self._resolve(path)
        if not os.path.exists(full):
            return f'文件不存在: {path}'
        if os.path.isdir(full):
            return f'{path} 是一个目录'
        try:
            with open(full, 'r', encoding='utf-8') as f:
                lines = f.readlines()
        except UnicodeDecodeError:
            return '错误：文件不是文本格式（可能是二进制文件）'
        if len(lines) > max_lines:
            result = ''.join(lines[:max_lines])
            return f'{result}\n\n... (共{len(lines)}行，仅显示前{max_lines}行)'
        return ''.join(lines)

    def edit_file(self, path: str, old_str: str, new_str: str) -> str:
        """精准替换文件中一段文本，old_str 必须在文件中唯一出现"""
        full = self._resolve(path)
        if not os.path.exists(full):
            return f'文件不存在: {path}'
        try:
            with open(full, 'r', encoding='utf-8') as f:
                content = f.read()
        except UnicodeDecodeError:
            return '错误：无法读取文件内容（编码问题）'
        count = content.count(old_str)
        if count == 0:
            return f'错误：文件中未找到要替换的内容。请确认 old_str 与原文完全一致（含空格和换行）。'
        if count > 1:
            return f'错误：old_str 出现了 {count} 次，不唯一。请提供更多上下文使匹配唯一。'
        new_content = content.replace(old_str, new_str)
        with open(full, 'w', encoding='utf-8') as f:
            f.write(new_content)
        rel = os.path.relpath(full, self.workspace)
        return f'文件已编辑: {rel} (成功替换1处)'

    def delete_file(self, path: str) -> str:
        """删除文件或空目录"""
        full = self._resolve(path)
        if not os.path.exists(full):
            return f'文件不存在: {path}'
        if os.path.isfile(full):
            os.remove(full)
            rel = os.path.relpath(full, self.workspace)
            return f'已删除文件: {rel}'
        if os.path.isdir(full):
            try:
                os.rmdir(full)
                return f'已删除空目录: {path}'
            except OSError:
                shutil.rmtree(full)
                return f'已删除目录（含内容）: {path}'

    def list_files(self, subdir: str = '') -> str:
        """列出工作区文件列表（最多展示100项）"""
        target = self._resolve(subdir) if subdir else self.workspace
        if not os.path.exists(target):
            return f'目录不存在: {subdir or "."}'
        result = []
        for root, dirs, files in os.walk(target):
            rel_root = os.path.relpath(root, self.workspace)
            if rel_root == '.':
                rel_root = ''
            dirs.sort()
            files.sort()
            for d in dirs:
                if not d.startswith('.'):
                    result.append(f'  [{os.path.join(rel_root, d)}/]')
            for f in files:
                if not f.startswith('.'):
                    fpath = os.path.join(rel_root, f)
                    fsize = os.path.getsize(os.path.join(root, f))
                    result.append(f'  {fpath}  ({self._human_size(fsize)})')
            break
        if len(result) > 100:
            result = result[:100]
            result.append(f'  ... (共 {len(result)} 项，截断显示)')
        return '\n'.join(result) if result else '(空目录)'

    @staticmethod
    def _human_size(size: int) -> str:
        for unit in ['B', 'KB', 'MB']:
            if size < 1024:
                return f'{size}{unit}'
            size //= 1024
        return f'{size}GB'
