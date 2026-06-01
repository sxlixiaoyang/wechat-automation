"""
工具注册表 — 定义 AI 可调用的工具，独立于引擎
每个工具定义完全自描述，引擎只需按名调度
"""
from typing import Dict, List, Any, Callable


class ToolRegistry:
    """
    工具注册中心：管理工具定义 + 调度执行
    新增工具只需在此注册，无需修改 AI 引擎
    """

    def __init__(self):
        self._definitions: List[Dict] = []
        self._handlers: Dict[str, Callable] = {}

    def register(self, definition: Dict, handler: Callable):
        """
        注册一个工具
        :param definition: OpenAI function calling 格式的工具定义
        :param handler: 执行函数，签名为 handler(**arguments) -> str
        """
        name = definition["function"]["name"]
        self._definitions.append(definition)
        self._handlers[name] = handler

    def get_definitions(self) -> List[Dict]:
        """返回所有工具定义（供 API 调用）"""
        return list(self._definitions)

    def execute(self, name: str, arguments: Dict[str, Any]) -> str:
        """
        执行指定工具
        :param name: 工具名
        :param arguments: 参数字典
        :return: 工具执行结果字符串
        """
        handler = self._handlers.get(name)
        if not handler:
            return f"错误：未注册的工具 '{name}'"
        try:
            return handler(**arguments)
        except Exception as e:
            return f"工具执行失败 [{name}]: {e}"

    def has(self, name: str) -> bool:
        return name in self._handlers

    def count(self) -> int:
        return len(self._definitions)


# ============================================================
# 工厂函数：创建标准工具注册表
# ============================================================
def create_standard_registry(
    web_search=None,
    memory=None,
    file_mgr=None,
    browser=None,
    image_generator=None,
    contact_name: str = "",
    visited_urls: set = None,
    is_group_chat: bool = False,
) -> ToolRegistry:
    """
    创建标准工具注册表，绑定依赖实例
    :param web_search: WebSearchTool 实例
    :param memory: MemoryManager 实例
    :param file_mgr: FileManager 实例
    :param browser: BrowserTool 实例
    :param contact_name: 当前联系人（用于 save_memory 等需要上下文的工具）
    :param visited_urls: URL 去重集合（外部传入，跨轮次共享）
    :param is_group_chat: 是否为群聊（群聊禁用文件操作，但允许查询）
    """
    registry = ToolRegistry()
    _visited = visited_urls if visited_urls is not None else set()

    # ── 群聊安全：构造拒绝处理器 ──
    def _reject_file(name: str):
        return lambda *a, **kw: f"[安全拒绝] 群聊中禁止 {name} 操作。请用文字回复'群里不方便，私聊我吧'。"

    # ---- web_search（群聊中允许查询，只禁文件操作）----
    if web_search:
        registry.register({
            "type": "function",
            "function": {
                "name": "web_search",
                "description": "搜索互联网获取实时信息。自动使用 DuckDuckGo + Bing 双引擎（DDG优先，失败自动切换Bing）。结果自动缓存120秒，同一关键词不会重复请求。适用场景：(1)金价/银价/汇率等动态价格 (2)最新新闻/事件 (3)用户问到不确定的信息需要查证。不适用场景：天气和股票（有固定网站直接访问更快）。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "搜索关键词，用中文，比如'今日国际金价 人民币/克'、'2026年6月1日新闻'"},
                        "num_results": {"type": "integer", "description": "返回结果数量，默认3，最多5"}
                    },
                    "required": ["query"]
                }
            }
        }, lambda query, num_results=3: web_search.search(query, num_results))

        # ---- visit_url ----
        registry.register({
            "type": "function",
            "function": {
                "name": "visit_url",
                "description": "直接访问指定网站获取页面正文。内置自适应提取引擎：自动分析页面结构，并行尝试6种策略（数据密度/语义容器/文本聚类/标题跟随/结构化数据/全页回退），评分选最优结果。适用场景：(1)天气查询 (2)股票行情 (3)从搜索结果中深入查看某个链接的详情。注意：已缓存的同一URL+同一问题会瞬间返回，不需要重复访问。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "要访问的完整网址，必须以http://或https://开头"}
                    },
                    "required": ["url"]
                }
            }
        }, lambda url: _handle_visit_url(web_search, url, _visited))

    # ---- 浏览器工具（群聊中允许查询，只读操作）----
    if browser:
        registry.register({
            "type": "function",
            "function": {
                "name": "browse_url",
                "description": "用真实浏览器打开网页，获取 JS 渲染后的完整页面内容。适用于 visit_url 无法处理的动态网站（SPA、React/Vue 页面、需要登录才能看的页面）。自动等待页面渲染完成，提取可见文本。适用场景：(1)JS 动态加载的网站 (2)需要浏览器环境才能显示内容的页面 (3)网页截图前的准备。注意：每次调用会启动浏览器，耗时约3-5秒。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "要打开的完整网址，必须以 http:// 或 https:// 开头"}
                    },
                    "required": ["url"]
                }
            }
        }, lambda url: browser.browse_url(url))

        registry.register({
            "type": "function",
            "function": {
                "name": "screenshot_page",
                "description": "用浏览器打开网页并截取全页截图，保存到 ai_workspace/screenshots/ 目录。适用场景：需要查看页面视觉效果、分享页面外观给对方。返回截图文件路径。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "要截图的完整网址，必须以 http:// 或 https:// 开头"}
                    },
                    "required": ["url"]
                }
            }
        }, lambda url: browser.screenshot(url))

        registry.register({
            "type": "function",
            "function": {
                "name": "click_page",
                "description": "用浏览器打开网页，点击指定元素，然后提取点击后的页面内容。适用场景：需要展开折叠内容、切换标签页、点击'加载更多'按钮等交互操作。selector 支持 CSS 选择器，如 '.btn-more'、'#submit'、'text=登录'。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "要打开的完整网址"},
                        "selector": {"type": "string", "description": "要点击的元素的 CSS 选择器，如 '.btn'、'#id'、'text=按钮文字'"}
                    },
                    "required": ["url", "selector"]
                }
            }
        }, lambda url, selector: browser.click_and_extract(url, selector))

        registry.register({
            "type": "function",
            "function": {
                "name": "fill_form",
                "description": "用浏览器打开网页，在输入框中填写文字并提交表单，提取提交后的页面内容。适用场景：搜索框查询、登录、填写问卷等。selector 支持 CSS 选择器，submit_selector 可选（不填则按 Enter 提交）。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "要打开的完整网址"},
                        "fill_selector": {"type": "string", "description": "要填写的输入框的 CSS 选择器"},
                        "text": {"type": "string", "description": "要填入的文本内容"},
                        "submit_selector": {"type": "string", "description": "可选，提交按钮的 CSS 选择器，不填则按 Enter 键提交"}
                    },
                    "required": ["url", "fill_selector", "text"]
                }
            }
        }, lambda url, fill_selector, text, submit_selector=None: browser.fill_and_submit(url, fill_selector, text, submit_selector))

    # ---- generate_image（生图工具，群聊和个人聊天都可用）----
    if image_generator:
        registry.register({
            "type": "function",
            "function": {
                "name": "generate_image",
                "description": "根据文字描述生成图片。支持中英文提示词（中文会自动翻译为英文以获得更好效果）。生成后自动保存到本地，可接着用 send_image 发送给用户。支持7种比例：1:1(默认方形)、16:9(横版)、9:16(竖版)、4:3、3:4、3:2、2:3。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prompt": {"type": "string", "description": "图片描述文本，支持中英文，越详细效果越好"},
                        "aspect": {"type": "string", "description": "图片比例，默认1:1。可选: 1:1/16:9/9:16/4:3/3:4/3:2/2:3"}
                    },
                    "required": ["prompt"]
                }
            }
        }, lambda prompt, aspect="1:1": image_generator.generate(prompt, aspect))

    # ---- save_memory ----
    if memory and contact_name:
        registry.register({
            "type": "function",
            "function": {
                "name": "save_memory",
                "description": "把对方透露的重要信息存入长期记忆。仅当对方主动说出关于自己的事实时才调用，比如姓名、年龄、职业、喜好、经历等。闲聊内容不需要记。每次只记一条，用简洁的一句话概括。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "fact": {"type": "string", "description": "简洁的一句话事实，不超过30字。例：'喜欢喝咖啡'、'在北京做程序员'、'有只猫叫花花'"}
                    },
                    "required": ["fact"]
                }
            }
        }, lambda fact: memory.add_fact(contact_name, fact))

    # ---- 文件操作（群聊中全部替换为拒绝处理器）----
    if file_mgr:
        _fop = _reject_file if is_group_chat else lambda n: n  # 群聊返回拒绝函数，否则无操作

        registry.register({
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "读取工作区中已有文件的内容（默认最多200行）。适用场景：查看之前写的代码、配置文件、技能文件等。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "文件路径，相对于 ai_workspace 目录，如 'bot/skills.py'"}
                    },
                    "required": ["path"]
                }
            }
        }, _fop("read_file") if is_group_chat else lambda path: _safe_call(file_mgr, 'read_file', path))

        registry.register({
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "创建新文件或覆盖已有文件。适用场景：写代码、创建配置文件、保存笔记等。注意：这会覆盖已有文件，编辑已有文件请用 edit_file。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "文件路径（相对于 ai_workspace），如 'tools/my_script.py'"},
                        "content": {"type": "string", "description": "要写入文件的完整内容"}
                    },
                    "required": ["path", "content"]
                }
            }
        }, _fop("write_file") if is_group_chat else lambda path, content: _safe_call(file_mgr, 'write_file', path, content))

        registry.register({
            "type": "function",
            "function": {
                "name": "edit_file",
                "description": "在已有文件中精准替换一段文本。old_str 必须在文件中完全匹配且只出现一次。适用场景：修改代码中的某个函数、更新配置文件中的某个设置等。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "要编辑的文件路径（相对于 ai_workspace）"},
                        "old_str": {"type": "string", "description": "文件中要被替换的原文，必须与文件内容完全一致（含缩进、空格、换行）"},
                        "new_str": {"type": "string", "description": "替换后的新文本"}
                    },
                    "required": ["path", "old_str", "new_str"]
                }
            }
        }, _fop("edit_file") if is_group_chat else lambda path, old_str, new_str: _safe_call(file_mgr, 'edit_file', path, old_str, new_str))

        registry.register({
            "type": "function",
            "function": {
                "name": "delete_file",
                "description": "删除工作区中的文件或空目录。适用场景：清理无用文件、删除测试代码等。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "要删除的文件或目录路径（相对于 ai_workspace）"}
                    },
                    "required": ["path"]
                }
            }
        }, _fop("delete_file") if is_group_chat else lambda path: _safe_call(file_mgr, 'delete_file', path))

        registry.register({
            "type": "function",
            "function": {
                "name": "list_files",
                "description": "列出工作区中的文件和目录。适用场景：查看有哪些文件、检查项目结构等。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "subdir": {"type": "string", "description": "可选，列出哪个子目录的内容，不填则列出根目录"}
                    }
                }
            }
        }, _fop("list_files") if is_group_chat else lambda subdir="": file_mgr.list_files(subdir))

    return registry


# ============================================================
# 内部辅助
# ============================================================

def _handle_visit_url(web_search, url: str, visited: set) -> str:
    """visit_url 的特殊处理：URL 去重"""
    if url in visited:
        return f"[重复访问] 这个URL {url} 已经访问过了，请基于之前获取的内容回答，不要再重复访问同一个网址。"
    visited.add(url)
    return web_search.visit_url(url)


def _safe_call(obj, method_name: str, *args) -> str:
    """安全调用文件管理器方法，捕获权限错误"""
    try:
        method = getattr(obj, method_name)
        return method(*args)
    except PermissionError as e:
        return f"安全拒绝: {e}"
