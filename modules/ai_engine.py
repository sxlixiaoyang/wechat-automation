"""
AI对话引擎 — 对话编排 + 工具调度
依赖：tool_registry, web_search, memory_manager, file_manager（均独立模块）
"""
import os
import re
import json
import time
import random
from typing import Dict, List
from datetime import datetime

try:
    import openai
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

# 安全导入配置
try:
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from config import AI_CONFIG, PROXY_URL, IMAGE_GEN_API_KEY, API_URL, API_KEY, MODEL as CONFIG_MODEL
except ImportError:
    AI_CONFIG = {"max_context_length": 20, "max_conversations": 200}
    PROXY_URL = "http://127.0.0.1:10808"
    IMAGE_GEN_API_KEY = ""
    API_URL = ""
    API_KEY = ""
    CONFIG_MODEL = ""

# 安全导入模块依赖
from .web_search import WebSearchTool
from .memory_manager import MemoryManager
from .file_manager import FileManager
from .browser_tool import BrowserTool
from .image_generator import ImageGenerator
from .tool_registry import ToolRegistry, create_standard_registry


class AIReplyEngine:
    """
    AI回复引擎 — 对话管理 + 工具调用编排
    依赖通过构造注入，各模块独立降级
    """

    def __init__(self, api_url: str, api_key: str, model: str,
                 conversations_file: str = None,
                 max_context: int = None,
                 max_contacts: int = None):
        if not OPENAI_AVAILABLE:
            raise ImportError("请安装 openai 库: pip install openai")

        self.api_url = api_url.rstrip('/')
        self.model = model
        self.max_context = max_context or AI_CONFIG.get("max_context_length", 20)
        self.max_contacts = max_contacts or AI_CONFIG.get("max_conversations", 200)

        self.client = openai.OpenAI(
            api_key=api_key,
            base_url=self.api_url,
            timeout=120.0
        )

        # 依赖模块（独立初始化，失败不影响主程序）
        self.web_search = self._safe_init(WebSearchTool)
        self.memory = self._safe_init(MemoryManager)
        self.file_mgr = self._safe_init(FileManager)
        self.browser = self._safe_init(BrowserTool, headless=True)
        self.image_gen = self._safe_init(
            ImageGenerator, IMAGE_GEN_API_KEY,
            ai_api_url=API_URL, ai_api_key=API_KEY, model=CONFIG_MODEL
        )
        self.tool_registry = ToolRegistry()

        # 对话历史
        self.conversations: Dict[str, List[Dict]] = {}
        self.conversations_file = conversations_file or os.path.join(
            os.path.dirname(os.path.dirname(__file__)), '.ai_conversations.json')
        self.load_conversations()

    @staticmethod
    def _safe_init(cls, *args, **kwargs):
        """安全初始化模块：失败返回 None 但不崩溃"""
        try:
            return cls(*args, **kwargs)
        except Exception as e:
            print(f"  ⚠ 模块 {cls.__name__} 初始化失败: {e}，相关功能已禁用")
            return None

    # ==================================================================
    # 对话持久化
    # ==================================================================
    def load_conversations(self):
        if os.path.exists(self.conversations_file):
            try:
                with open(self.conversations_file, 'r', encoding='utf-8') as f:
                    self.conversations = json.load(f)
                print(f"  加载了 {len(self.conversations)} 个对话历史")
            except Exception as e:
                print(f"  加载对话历史失败: {e}")
                self.conversations = {}

    def save_conversations(self):
        try:
            if len(self.conversations) > self.max_contacts:
                sorted_contacts = sorted(
                    self.conversations.items(),
                    key=lambda x: len(x[1]), reverse=True)
                self.conversations = dict(sorted_contacts[:self.max_contacts])
            with open(self.conversations_file, 'w', encoding='utf-8') as f:
                json.dump(self.conversations, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"  保存对话历史失败: {e}")

    def get_or_create_conversation(self, contact_name: str) -> List[Dict]:
        if contact_name not in self.conversations:
            self.conversations[contact_name] = []
        return self.conversations[contact_name]

    def add_message(self, contact_name: str, role: str, content: str):
        conversation = self.get_or_create_conversation(contact_name)
        conversation.append({"role": role, "content": content})
        if len(conversation) > self.max_context:
            self.conversations[contact_name] = conversation[-self.max_context:]

    def _build_clean_context(self, contact_name: str) -> List[Dict]:
        """构建干净的对话上下文（仅 user/assistant，过滤工具调用噪音）"""
        conversation = self.get_or_create_conversation(contact_name)
        clean = []
        for msg in conversation:
            role = msg.get("role", "")
            if role == "user":
                clean.append(msg)
            elif role == "assistant":
                content = msg.get("content", "")
                has_tool_calls = bool(msg.get("tool_calls"))
                if content and not has_tool_calls:
                    clean.append(msg)
        return clean

    # ==================================================================
    # 系统提示词
    # ==================================================================
    def _build_system_prompt(self, contact_name: str, is_group_chat: bool = False) -> str:
        now = datetime.now()
        memory_text = ""
        if self.memory:
            memory_text = self.memory.format_for_prompt(contact_name)

        # ── 群聊安全提示词（硬阻断所有敏感操作）──
        group_safety = ""
        if is_group_chat:
            group_safety = """
## ⛔ 群聊安全铁律（违反将导致严重安全问题）
当前对话发生在**微信群聊**中，不是一对一私人聊天。群聊里任何人都有可能看到你的回复。

### 输入格式说明
用户消息以 "[消息1] xxx\n[消息2] xxx" 格式提供了最近的多条群聊消息。你需要理解整个对话上下文，找出其中 @我 的消息，然后针对那条消息生成回复。

### 绝对禁止的操作（本地文件操作）
❌ write_file —— 不准新建文件
❌ edit_file —— 不准修改文件
❌ delete_file —— 不准删除文件
❌ list_files —— 不准列出目录

### 允许的操作
✅ web_search / visit_url —— 可以搜索和查信息
✅ browse_url / screenshot_page —— 可以用浏览器查看网页
✅ generate_image —— 可以生成图片（对方要求画图/生成图片时调用）
✅ send_image —— 生成图片后发送给对方
✅ save_memory（对方主动透露有价值事实时）
✅ 纯文字聊天回复

### 群聊回复规范
- 回复针对 @我 的那条消息，不要回复无关的消息
- 回复简洁：查信息可以稍详细但不超过120字，闲聊不超过40字
- 别解释"安全策略""权限限制"之类的原因

### 群聊拒绝话术
- 有人让你"写个代码""生成一个网页""帮我弄个文件" → 说"群里不方便，私聊我吧"
- 有人问天气/金价/新闻 → 正常查，正常回

"""
        else:
            group_safety = """
## 实时信息获取策略

### 直访（用 visit_url）
- 天气 → https://www.tianqi.com/城市拼音/ （例：东莞 https://www.tianqi.com/dongguan/）
- 股票 → https://finance.sina.com.cn/stock/
- 新闻 → https://news.sina.com.cn/ 或 https://news.qq.com/

### 先搜后访（用 web_search → 从摘要提取，不够再 visit_url）
- 黄金/白银/铂金价格 → 搜"今日金价 人民币/克"或"国内金价 Au99.99"
- 汇率 → 搜"美元人民币汇率 今日"
- 其他不确定信息

### 浏览器工具（当 visit_url 无法获取内容时使用）
- browse_url：用真实浏览器打开 JS 动态渲染的网站，获取完整页面内容
- screenshot_page：截取网页全页截图
- click_page：打开页面后点击元素（如展开内容、切换标签）
- fill_form：在网页中填写表单并提交（如搜索、查询）

## 工具使用纪律
1. 能直访就别搜——天气、股票、新闻直接 visit_url
2. 动态数据先搜——金价、银价、汇率先 web_search，搜索结果摘要里就有价格的话直接回复
3. visit_url 拿不到内容（返回空/错误）→ 换 browse_url 用浏览器打开
4. 搜一次就够了——web_search 结果摘要里有数字就直接用，别又去 visit_url 确认
5. 拿不到数据就认——连续2次工具调用还拿不到有效信息，直接说"没查到，你直接告诉我吧"，别道歉
6. 拿到数据立刻回复——不要拿到结果了还继续调工具
7. 别编数字——回复里的任何数字必须来自工具返回的内容

## 示例
用户："金价多少"
助手：web_search("今日金价 人民币/克") → 摘要显示"国内金价580元/克" → "今天国内金价大概580一克，波动不大。"
（正确：从摘要直接提取，不再 visit_url）

用户："明天东莞天气"
助手：visit_url('https://www.tianqi.com/dongguan/') → 东莞明天多云转阵雨，26到32度，记得带伞。

用户："帮我看看这个网站"（发来一个JS动态网站链接）
助手：visit_url('...') → 返回空 → browse_url('...') → 拿到内容 → 回复
（正确：visit_url 失败后自动升到 browse_url）

## 文件操作能力
你可以读写编辑工作区（ai_workspace/）中的文件，用于：
- 帮对方写代码、脚本、程序
- 创建配置文件、笔记、技能文件
- 修改已有文件、清理无用文件

## AI生图能力
当对方要求生成图片、画图、画一张xxx时，调用 generate_image 工具：
- prompt 参数：图片描述，支持中英文（中文会自动翻译为英文以获得更好效果）
- aspect 参数：图片比例，可选 1:1(默认)/16:9/9:16/4:3/3:4/3:2/2:3
- 生成后图片会自动保存，你只需把生成结果中的文件名告诉对方即可
- 对方没有指定比例时不要填 aspect，让它默认 1:1
- 生成图片后直接告诉对方"图片生成好了"即可，不要说"我无法生成图片"

### 文件操作硬纪律（违反就出bug）
1. **单文件原则**：游戏、网页、小程序等统统写进一个 HTML 文件，CSS 和 JS 全部内联（style 标签和 script 标签），不要拆成多个文件
2. **写前看一眼**：write_file 前先 list_files 看目录，但 list_files 看到是空目录就直接写，别犹豫
3. **写完就停**：write_file 成功后立刻用文字回复告知对方"做好了，在 xxx 文件里"，不要再继续调工具
4. **最多2次文件操作**：整个对话中 list_files + write_file 加起来最多 2 次工具调用，然后就文字回复
5. **编辑用 edit_file**：修改已有文件用 edit_file，别 write_file 覆盖
6. **删除前确认**：确认是对方要删的再删
7. **创建新文件用 write_file**：内容一次性给完整，别分多次写


### 反例（绝对不能这样做）
❌ 写 snake.html → 又写 style.css → 又写 script.js（拆成多个文件 → 工具调用耗尽）
❌ list_files → write_file → 又 write_file → 又 write_file（连续写文件不停 → 耗尽）
❌ 文件写成功了还继续调工具

### 正例
✅ list_files → write_file（一次性写完，CSS/JS全内联）→ 文字回复"贪吃蛇写好了，在 snake.html"
✅ list_files → edit_file（精准替换）→ 文字回复"已更新"
"""

        return f"""## 角色
你是微信上的一个真人好友，性格：随和、有点小幽默、不啰嗦。你和对方是熟人关系，聊天风格轻松自然，像朋友之间闲聊。

## 风格要求
- 日常闲聊回复不超过40字，一句话说完就行，别废话
- 查实时数据（价格/天气/股票）可以稍详细，但不超过120字
- 千万别用 emoji 表情符号（对方可能显示乱码）
- 别用"呢""哦""哈"这类语气词结尾，显假
- 如果对方连续问同一件事，可以适当吐槽两句，但别烦

## 当前时间
{now.strftime('%Y年%m月%d日 %H:%M')} 星期{['一','二','三','四','五','六','日'][now.weekday()]}

{memory_text}

## 长期记忆使用规则
- 上面有"关于对方的长期记忆"时，在聊天中自然地引用这些信息，让对方觉得你记得他
- 当对方主动说出关于自己的新事实（姓名/年龄/职业/喜好/经历/宠物等），用 save_memory 记下来
- 闲聊寒暄不需要存，只记有价值的事实
- 每次最多存一条
{group_safety}"""

    # ==================================================================
    # 核心：生成回复（工具调用编排）
    # ==================================================================
    def generate_reply(self, contact_name: str, new_message: str, is_group_chat: bool = False,
                       send_image_fn=None) -> str:
        """
        为指定联系人生成回复
        :param contact_name: 联系人名称
        :param new_message: 新消息内容
        :param is_group_chat: 是否为群聊（群聊禁用文件操作/搜索/URL访问）
        支持多轮工具调用，只持久化 user/assistant 消息
        """
        # 图片目录（整个方法共用，send_image 和自动补发都需要）
        image_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'ai_workspace', 'images')
        os.makedirs(image_dir, exist_ok=True)

        # 图片追踪：记录本轮生成/发送的图片，用于自动补发
        generated_images = set()
        sent_images = set()

        # 构建工具注册表（每次调用重新构建，确保上下文正确）
        # 注意：不能 if self.web_search 包裹，否则 web_search 为 None 时所有工具都丢失
        visited_urls = set()
        self.tool_registry = create_standard_registry(
            web_search=self.web_search,
            memory=self.memory,
            file_mgr=self.file_mgr,
            browser=self.browser,
            image_generator=self.image_gen,
            contact_name=contact_name,
            visited_urls=visited_urls,
            is_group_chat=is_group_chat,
        )

        # ── 注册图片发送工具 ──
        if send_image_fn:
            self.tool_registry.register({
                "type": "function",
                "function": {
                    "name": "send_image",
                    "description": "发送一张图片到当前微信聊天。图片文件必须已存在于 ai_workspace/images/ 目录中。适用场景：对方要求发张图片、你想分享之前保存的截图或生成的图片。参数为图片文件名（不带路径），如 'screenshot_20260601.png'。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "filename": {"type": "string", "description": "图片文件名，位于 ai_workspace/images/ 目录下，如 'chart.png'、'photo.jpg'"}
                        },
                        "required": ["filename"]
                    }
                }
            }, lambda filename: (
                f"图片已发送: {filename}" if send_image_fn(
                    os.path.join(image_dir, filename)
                ) else f"图片发送失败: {filename}"
            ))

        system_prompt = self._build_system_prompt(contact_name, is_group_chat=is_group_chat)
        self.add_message(contact_name, "user", new_message)
        clean_context = self._build_clean_context(contact_name)

        try:
            max_iterations = 8
            iteration = 0

            api_messages = [
                {"role": "system", "content": system_prompt}
            ] + clean_context.copy()

            tools = self.tool_registry.get_definitions() if self.tool_registry.count() > 0 else None

            while iteration < max_iterations:
                iteration += 1

                if iteration == max_iterations:
                    # 统计前面的工具调用结果，给出针对性的兜底指令
                    tool_names_used = set()
                    for msg in api_messages:
                        if msg.get("role") == "tool":
                            content = msg.get("content", "")
                            # 从工具结果中识别文件名（写文件成功会返回"文件已写入: xxx"）
                            if "文件已写入" in content or "文件已编辑" in content:
                                tool_names_used.add("file_done")
                    has_file_output = "file_done" in tool_names_used
                    fallback_instruction = (
                        "工具调用已达上限。前面如果已经创建了文件，直接告诉对方'写好了，在 xxx 文件里'即可。"
                        if has_file_output else
                        "工具调用已达上限。如果前面的工具已经拿到数据，直接报数据。确实没拿到任何有效信息才说'没查到，你直接告诉我吧'。"
                        "不要道歉，不要解释原因。"
                    )
                    api_messages.append({
                        "role": "system",
                        "content": fallback_instruction
                    })

                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=api_messages,
                    tools=tools if iteration < max_iterations else None,
                    temperature=0.6,
                    max_tokens=500
                )

                assistant_message = response.choices[0].message

                # 无工具调用 → 最终回复
                if not assistant_message.tool_calls:
                    reply = assistant_message.content.strip() if assistant_message.content else "抱歉，我没查到相关信息，要不你直接告诉我？"

                    # ── 自动补发：AI 生成了图片但忘记调用 send_image ──
                    auto_sent_count = 0
                    for img in generated_images:
                        if img not in sent_images and send_image_fn:
                            img_path = os.path.join(image_dir, img)
                            if os.path.exists(img_path):
                                print(f"    [AUTO] 自动补发图片: {img}")
                                try:
                                    send_image_fn(img_path)
                                    auto_sent_count += 1
                                except Exception as e:
                                    print(f"    [WARN] 自动补发图片失败: {e}")

                    # 如果补发了图片，且 AI 回复里只有文件名文本，替换为自然短句
                    if auto_sent_count > 0:
                        if re.search(r'ai_[a-f0-9]+_\d+\.png', reply):
                            reply = random.choice([
                                "画好了", "好了", "画好了你看看",
                                "画好了，你看看", "给你画好了"
                            ])

                    self.add_message(contact_name, "assistant", reply)
                    self.save_conversations()
                    return reply

                # 执行工具调用
                print(f"  🔧 第{iteration}轮工具调用...")
                for tool_call in assistant_message.tool_calls:
                    name = tool_call.function.name
                    try:
                        args = json.loads(tool_call.function.arguments)
                    except json.JSONDecodeError:
                        args = {}

                    print(f"    工具: {name}({str(args)[:80]})")
                    result = self.tool_registry.execute(name, args)
                    print(f"    结果: {len(result)}字符")

                    # 图片追踪：记录生成/发送的图片，用于自动补发
                    if name == "generate_image":
                        for line in result.split('\n'):
                            if line.startswith("文件名: "):
                                generated_images.add(line.split(": ", 1)[1].strip())
                                break
                    elif name == "send_image":
                        sent_images.add(args.get("filename", ""))

                    # 追加到 API 消息（不持久化）
                    api_messages.append({
                        "role": "assistant", "content": "",
                        "tool_calls": [{
                            "id": tool_call.id, "type": "function",
                            "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}
                        }]
                    })
                    api_messages.append({
                        "role": "tool", "tool_call_id": tool_call.id, "content": result
                    })

                print(f"    继续对话获取最终回复...")

            # 兜底（理论上不会到这里，进入此分支说明 token 被截断等异常）
            reply = "抱歉，我没查到相关信息，要不你直接告诉我？"
            self.add_message(contact_name, "assistant", reply)
            self.save_conversations()
            return reply

        except Exception as e:
            print(f"  AI生成回复失败: {e}")
            return "好的"

    def generate_reply_simple(self, contact_name: str, new_message: str) -> str:
        """简单回复（不带搜索功能，使用干净上下文）"""
        self.add_message(contact_name, "user", new_message)
        clean_context = self._build_clean_context(contact_name)

        memory_text = ""
        if self.memory:
            memory_text = self.memory.format_for_prompt(contact_name)
        memory_section = f"\n{memory_text}" if memory_text else ""

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{
                    "role": "system",
                    "content": f"""你是微信上的真人好友，性格随和、有点幽默。
回复不超过40字，像真人闲聊，别官方。
别用 emoji 表情符号。别用"呢""哦""哈"结尾。
根据之前的对话上下文给出连贯回复。{memory_section}"""
                }] + clean_context,
                temperature=0.8,
                max_tokens=200
            )
            reply = response.choices[0].message.content.strip()
            self.add_message(contact_name, "assistant", reply)
            self.save_conversations()
            return reply
        except Exception as e:
            print(f"  AI生成回复失败: {e}")
            return "好的"

    # ==================================================================
    # 管理接口
    # ==================================================================
    def clear_conversation(self, contact_name: str):
        if contact_name in self.conversations:
            del self.conversations[contact_name]
            self.save_conversations()
            print(f"  已清除 {contact_name} 的对话历史")

    def clear_all(self):
        self.conversations = {}
        self.save_conversations()
        print("  已清除所有对话历史")

    def clear_memory(self, contact_name: str = None):
        if not self.memory:
            print("  记忆模块未初始化")
            return
        if contact_name:
            self.memory.save(contact_name, [])
            print(f"  已清除 {contact_name} 的长期记忆")
        else:
            import shutil
            if os.path.exists(self.memory.memory_dir):
                shutil.rmtree(self.memory.memory_dir)
                os.makedirs(self.memory.memory_dir, exist_ok=True)
            print("  已清除所有长期记忆")
