"""
微信AI自动化 — 模块化架构
每个模块独立加载、独立降级，任何模块故障不影响主程序稳定性

模块说明：
  file_manager   — 安全文件操作（沙盒到 ai_workspace）
  memory_manager — 长期记忆（每人独立 JSON 文件）
  web_search     — Web搜索 + 自适应页面提取
  tool_registry  — 工具定义与注册
  ai_engine      — AI对话引擎（编排对话与工具调用）
"""

# 每个模块独立导入，失败不阻塞其他模块
import importlib
import sys

_modules_status = {}

def _safe_import(module_name: str):
    """安全导入：失败时记录状态，不抛异常"""
    try:
        mod = importlib.import_module(f'.{module_name}', __package__)
        _modules_status[module_name] = {'loaded': True, 'error': None}
        return mod
    except Exception as e:
        _modules_status[module_name] = {'loaded': False, 'error': str(e)}
        print(f'  ⚠ 模块 {module_name} 加载失败: {e}')
        return None

# 各模块独立加载
file_manager_mod = _safe_import('file_manager')
memory_manager_mod = _safe_import('memory_manager')
web_search_mod = _safe_import('web_search')
tool_registry_mod = _safe_import('tool_registry')
ai_engine_mod = _safe_import('ai_engine')

# 导出（只导出成功加载的）
FileManager = getattr(file_manager_mod, 'FileManager', None) if file_manager_mod else None
MemoryManager = getattr(memory_manager_mod, 'MemoryManager', None) if memory_manager_mod else None
WebSearchTool = getattr(web_search_mod, 'WebSearchTool', None) if web_search_mod else None
ToolRegistry = getattr(tool_registry_mod, 'ToolRegistry', None) if tool_registry_mod else None
AIReplyEngine = getattr(ai_engine_mod, 'AIReplyEngine', None) if ai_engine_mod else None

def get_modules_health() -> dict:
    """返回各模块健康状态"""
    return dict(_modules_status)

# 兼容旧导入路径
__all__ = ['FileManager', 'MemoryManager', 'WebSearchTool', 'ToolRegistry', 'AIReplyEngine']
