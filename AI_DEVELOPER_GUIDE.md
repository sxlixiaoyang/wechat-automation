# 微信AI自动化 — 模块化开发指南（给 AI 的说明书）

> **本文档面向 AI 编程助手**，教会你如何理解本项目的模块化架构，并按预设模式完成新增、修改、删除功能模块的操作。
>
> 人类开发者也可阅读，但语气和内容专为 AI 设计。

---

## 一、 项目架构速览

```
微信UI自动化/
├── auto_reply.py           ← 主入口（降级兼容加载）
├── config.py               ← 统一配置（API/超时/日志/窗口）
├── ai_reply_engine.py      ← [旧文件] 保留作为降级备用
├── wechat_automation_final.py ← 微信窗口自动化底层
├── .env                    ← 环境变量（API_KEY等）
│
├── modules/                ← ★ 模块化新架构（核心目录）
│   ├── __init__.py         ← 聚合入口 + 健康检查
│   ├── file_manager.py     ← 沙盒文件操作（ai_workspace/）
│   ├── memory_manager.py   ← 长期记忆（.memories/每人一个JSON）
│   ├── web_search.py       ← Web搜索 + 自适应页面提取
│   ├── tool_registry.py    ← 工具注册表（定义+调度）
│   └── ai_engine.py        ← AI对话引擎（编排对话+工具调用）
│
└── ai_workspace/           ← AI 文件操作的沙盒目录
```

### 核心设计原则（必须遵守）

| 原则 | 说明 |
|------|------|
| **模块独立** | 每个 `modules/*.py` 可单独 `import`，不依赖其他同层模块 |
| **故障隔离** | 任一模块初始化/加载失败不影响其他模块（`_safe_init` 模式） |
| **降级兼容** | `auto_reply.py` 优先加载 `modules/`，失败回退到 `ai_reply_engine.py` |
| **工具可插拔** | 新增工具只需在 `tool_registry.py` 注册，引擎代码零改动 |
| **安全沙盒** | 文件操作限定在 `ai_workspace/`，路径穿越自动拦截 |

---

## 二、 各模块职责与边界

### 2.1 `modules/__init__.py` — 聚合入口
- 职责：独立加载所有子模块，任一失败不阻塞其他
- 导出：`FileManager`, `MemoryManager`, `WebSearchTool`, `ToolRegistry`, `AIReplyEngine`
- 提供 `get_modules_health()` 用于运行时诊断

### 2.2 `modules/file_manager.py` — 文件管理器
- 类：`FileManager`
- 方法：`write_file(path, content)`, `read_file(path, max_lines)`, `edit_file(path, old_str, new_str)`, `delete_file(path)`, `list_files(subdir)`
- 安全：`_resolve()` 拦截路径穿越（`..`、`~`、绝对路径）
- 限制：单文件最大 200KB

### 2.3 `modules/memory_manager.py` — 长期记忆
- 类：`MemoryManager`
- 方法：`load(name)`, `save(name, facts)`, `add_fact(name, fact)`, `format_for_prompt(name)`
- 每人独立 JSON，最多 20 条事实，自动去重
- 存储：`.memories/{安全文件名}.json`

### 2.4 `modules/web_search.py` — Web搜索
- 类：`WebSearchTool`
- 方法：`search(query, num_results)` — DuckDuckGo优先，Bing备用，120秒缓存
- 方法：`visit_url(url, query_hint)` — 自适应6策略提取，300秒缓存
- 内置：代理支持、编码检测、重试、质量评分

### 2.5 `modules/tool_registry.py` — 工具注册表
- 类：`ToolRegistry`
- 方法：`register(definition, handler)` — 注册一个工具
- 方法：`execute(name, arguments)` — 按名调度
- 工厂函数：`create_standard_registry(...)` — 一键创建标准工具集

### 2.6 `modules/ai_engine.py` — AI对话引擎
- 类：`AIReplyEngine`
- 依赖注入：通过 `_safe_init()` 安全加载各工具模块
- 核心方法：`generate_reply(contact_name, message)` — 多轮工具调用编排
- 对话持久化：`.ai_conversations.json`

---

## 三、 预设操作模式

以下每种操作都提供了标准流程，**严格按照模式执行，不要自由发挥**。

---

### 模式 A：新增一个功能模块（最简单）

**场景**：要加一个新能力，比如「股票实时行情」「翻译」「图片生成」等。

#### 步骤 1：在 `modules/` 下创建新文件

文件命名：`modules/{feature_name}.py`

**模板代码**：

```python
"""
{功能名称} — 简短描述
可独立使用，也可作为模块嵌入 AI 引擎
"""
import os
import json
from typing import Optional

# ⚠️ 安全导入外部依赖
try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False


class {ClassName}:
    """{功能描述}"""

    # 类级别常量
    MAX_RESULTS = 10
    CACHE_TTL = 60

    def __init__(self, **kwargs):
        # 初始化资源，允许传入配置覆盖默认值
        self.config = kwargs

        # ⚠️ 安全加载项目配置（失败不崩溃）
        try:
            import sys
            sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            from config import PROXY_URL
            self.proxy = PROXY_URL
        except ImportError:
            self.proxy = None

    # —— 公开方法（工具可调用的入口）——
    def {main_method}(self, **params) -> str:
        """
        核心功能方法，返回字符串结果。
        签名必须兼容 tool_registry 的 handler 调用方式。
        """
        # 实现逻辑
        return "结果字符串"

    # —— 私有辅助方法 ——
    def _helper(self):
        pass


# ⚠️ 模块级别自测（直接运行此文件时可验证）
if __name__ == "__main__":
    tool = {ClassName}()
    print(tool.{main_method}(**{"key": "value"}))
```

#### 步骤 2：在 `modules/tool_registry.py` 中注册工具

在 `create_standard_registry()` 函数末尾（`return registry` 之前）添加：

```python
# ---- {feature_name} ----
if {module_instance}:
    registry.register({
        "type": "function",
        "function": {
            "name": "{tool_name}",
            "description": "{给AI看的工具描述，说清楚什么时候用、怎么用、返回什么}",
            "parameters": {
                "type": "object",
                "properties": {
                    "{param1}": {
                        "type": "{类型:string/integer/number/boolean}",
                        "description": "{参数说明}"
                    }
                },
                "required": ["{param1}"]   # 必填参数列表
            }
        }
    }, lambda {param1}: {module_instance}.{main_method}({param1}={param1}))
```

**注册规则**：
- `definition` 必须符合 OpenAI function calling 格式
- `handler` 是 `lambda` 或函数，签名为 `(**kwargs) -> str`
- 工具名称用 `snake_case`，描述用中文
- 如果模块实例可能为 `None`，用 `if module_instance:` 包裹

#### 步骤 3：在 `modules/ai_engine.py` 中加载模块

在 `__init__` 方法中，找到 `_safe_init` 调用区域，添加：

```python
from .{feature_name} import {ClassName}   # 顶部导入
self.{instance_name} = self._safe_init({ClassName})  # __init__ 中初始化
```

#### 步骤 4：在系统提示词中添加使用说明

在 `_build_system_prompt()` 方法中，找到工具使用策略区域，添加新工具的调用时机和约束。

#### 步骤 5：在 `modules/__init__.py` 中声明

```python
{feature_name}_mod = _safe_import('{feature_name}')
{ClassName} = getattr({feature_name}_mod, '{ClassName}', None) if {feature_name}_mod else None
```

**完整示例 — 假设要加一个「天气查询」专用模块**：

见文件：不实际创建，以下为参考

```python
# modules/weather.py
class WeatherTool:
    def get_weather(self, city: str) -> str:
        # 请求天气API，返回格式化结果
        return f"{city} 今天晴，25°C，微风"

# modules/tool_registry.py 中注册：
if weather:
    registry.register({
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "查询指定城市的实时天气。返回温度、天气状况、风力等信息。",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "城市名，如'北京'、'上海'"}
                },
                "required": ["city"]
            }
        }
    }, lambda city: weather.get_weather(city))

# modules/ai_engine.py
from .weather import WeatherTool
self.weather = self._safe_init(WeatherTool)

# modules/__init__.py
weather_mod = _safe_import('weather')
WeatherTool = getattr(weather_mod, 'WeatherTool', None) if weather_mod else None
```

---

### 模式 B：修改现有模块

**触发条件**：优化功能、修bug、调整行为。

#### 修改 `modules/file_manager.py` 等独立模块
- 直接编辑对应文件
- **不要动其他模块**
- 保持公开方法的签名不变（否则需要同步更新 `tool_registry.py` 中的 handler）
- 保持 `__init__` 的降级兼容性

#### 修改 `modules/tool_registry.py` 的工具定义
- 只改定义或 handler，不改注册逻辑
- 工具名一旦投入使用**禁止改名**（AI 的系统提示词会依赖它）
- 可以改 `description` 来优化 AI 的使用策略

#### 修改 `modules/ai_engine.py` 的系统提示词
- 系统提示词决定了 AI 的行为策略
- 修改时保持 markdown 结构（`##` 为章节，`###` 为子策略）
- 示例格式必须准确（AI 会模仿示例）

#### 修改 `auto_reply.py`（主入口）
- 这是最上层，修改需谨慎
- 保持 `_load_ai_engine()` 的降级逻辑
- 任何顶层异常都要被 `try/except` 包裹

---

### 模式 C：删除一个模块

**步骤**：

1. **从 `modules/ai_engine.py` 中移除**：
   - 删除顶部的 `from .xxx import XXX`
   - 删除 `__init__` 中的 `self.xxx = self._safe_init(XXX)`

2. **从 `modules/tool_registry.py` 中移除**：
   - 在 `create_standard_registry()` 中删除该模块对应的注册代码块

3. **从 `modules/__init__.py` 中移除**：
   - 删除 `_safe_import('xxx')` 和对应的 `getattr` 行

4. **从系统提示词中移除**：
   - 在 `_build_system_prompt()` 中删除该工具的使用说明

5. **（可选）删除文件**：
   - `modules/xxx.py`
   - 确认没有其他文件引用它

---

### 模式 D：新增一个不需要模块的纯逻辑工具

**场景**：AI 需要调一个简单的工具，不需要独立类，比如「获取当前时间」「生成随机数」。

直接在 `modules/tool_registry.py` 的 `create_standard_registry()` 中注册：

```python
# ---- 无模块工具 ----
registry.register({
    "type": "function",
    "function": {
        "name": "get_current_time",
        "description": "获取当前精确时间（含日期和星期）",
        "parameters": {"type": "object", "properties": {}}
    }
}, lambda: f"当前时间：{datetime.now().strftime('%Y年%m月%d日 %H:%M:%S')}"
     f" 星期{['一','二','三','四','五','六','日'][datetime.now().weekday()]}")
```

同时在 `_build_system_prompt()` 中添加使用说明即可。**不需要动 `ai_engine.py` 和 `__init__.py`**。

---

## 四、 安全规则（强制执行）

### 4.1 路径安全
- 所有文件操作必须经过 `FileManager._resolve()` 安全校验
- 禁止在任何地方拼接用户输入到文件路径而不校验
- 工作区路径：`ai_workspace/` 目录，禁止外泄

### 4.2 异常安全
- 任何模块的 `__init__` 不能抛异常到外部（用 `_safe_init` 包裹）
- 工具 handler 的异常在 `ToolRegistry.execute()` 中已被捕获，但自身仍应做好防御
- 网络请求必须有超时（默认 15 秒）和重试（最多 2 次）

### 4.3 数据安全
- `.env` 文件中的 API Key 绝对不输出到日志
- 对话历史文件 `.ai_conversations.json` 只保存 user/assistant 消息
- 长期记忆 `.memories/` 每人独立，互不可见

### 4.4 降级安全
- 任一模块 `import` 失败 → 功能降级（`None`），不影响主流程
- API 调用失败 → 返回兜底回复（如"好的"），不崩溃
- 配置文件缺失 → 使用内置默认值

### 4.5 修改禁忌
- **禁止**修改 `modules/__init__.py` 的 `_safe_import` 机制
- **禁止**在模块间建立循环导入
- **禁止**修改已注册工具的名称（会破坏已有对话的兼容性）
- **禁止**在 `auto_reply.py` 的主循环中做长时间阻塞操作

---

## 五、 快速参考卡

### 文件对应关系

```
要加功能 → 创建 modules/xxx.py
要注册工具 → 编辑 modules/tool_registry.py
要接入引擎 → 编辑 modules/ai_engine.py
要导出/声明 → 编辑 modules/__init__.py
要AI会用 → 编辑 ai_engine.py 的 _build_system_prompt()
要改入口 → 编辑 auto_reply.py
```

### 工具 Handler 签名规范

```python
# 所有 handler 必须返回 str
# 参数通过 lambda 传递
lambda 参数1, 参数2: 实例.方法(参数1=参数1, 参数2=参数2)
```

### OpenAI 工具定义模板

```json
{
    "type": "function",
    "function": {
        "name": "tool_name",
        "description": "中文描述，说明何时使用、返回什么",
        "parameters": {
            "type": "object",
            "properties": {
                "param_name": {
                    "type": "string",
                    "description": "参数的中文说明"
                }
            },
            "required": ["param_name"]
        }
    }
}
```

### 健康检查命令

```python
from modules import get_modules_health
for name, status in get_modules_health().items():
    print(f"{name}: {'OK' if status['loaded'] else 'FAIL - ' + status['error']}")
```

---

## 六、 当前已注册的工具清单

| 工具名 | 所属模块 | 功能 |
|--------|----------|------|
| `web_search` | web_search.py | 多引擎搜索（DuckDuckGo/Bing） |
| `visit_url` | web_search.py | 直接访问URL，自适应提取 |
| `save_memory` | memory_manager.py | 存入长期记忆 |
| `read_file` | file_manager.py | 读取沙盒文件 |
| `write_file` | file_manager.py | 创建/覆盖沙盒文件 |
| `edit_file` | file_manager.py | 精准替换文件片段 |
| `delete_file` | file_manager.py | 删除沙盒文件 |
| `list_files` | file_manager.py | 列出沙盒目录 |

---

*最后更新：2025-06-01 — 版本 1.0*
