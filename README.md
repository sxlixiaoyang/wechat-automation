# 微信AI自动回复

基于 Windows UI 自动化的微信智能回复机器人。自动检测未读消息，群聊仅 @我 时回复，支持 AI 对话、联网搜索、网页浏览、文件操作、AI 生图等能力。

---

## 功能特性

- **自动检测未读消息**：轮询微信会话列表，识别带未读标记的会话
- **智能回复**：接入 OpenAI 兼容 API，支持多轮对话、上下文记忆
- **群聊/私聊区分**：群聊仅在 @我 时回复，私聊自动回复
- **工具调用**：AI 可自主调用工具获取实时信息
  - 🔍 联网搜索（DuckDuckGo + Bing 双引擎）
  - 🌐 网页访问（静态 + JS 动态渲染）
  - 📸 网页截图
  - 🎨 AI 生图（支持 7 种比例，中文提示词自动翻译）
  - 📁 文件读写（个人聊天可用）
  - 🧠 长期记忆（自动记住对方信息）
- **图片自动发送**：生图后自动发送到聊天窗口
- **消息去重**：避免重复处理同一条消息

---

## 技术栈

| 类别 | 技术 |
|------|------|
| **语言** | Python 3.9+ |
| **UI 自动化** | [uiautomation](https://github.com/yinkaisheng/Python-UIAutomation-for-Windows)（Windows UIA 接口） |
| **AI 引擎** | OpenAI 兼容 API（支持 stepfun / mistral 等模型） |
| **网页浏览** | Playwright（无头浏览器，处理 JS 动态页面） |
| **网络搜索** | DuckDuckGo + Bing（双引擎自动切换） |
| **AI 生图** | 独立生图 API（支持中英文提示词，中文自动翻译为英文） |
| **运行环境** | Windows 10/11 + 微信 PC 版 |

---

## 项目结构

```
├── auto_reply.py              # 主程序入口，消息检测与回复调度
├── config.py                  # 统一配置（API、超时、日志等）
├── wechat_automation_final.py # 微信 UI 自动化核心（窗口连接、消息读写、发送）
├── requirements.txt           # Python 依赖
├── .env.example               # 环境变量模板
├── .gitignore
├── AI_DEVELOPER_GUIDE.md      # AI 开发指南
│
├── modules/                   # 核心模块（独立可插拔）
│   ├── __init__.py
│   ├── ai_engine.py           # AI 对话引擎（工具调度 + 多轮对话编排）
│   ├── tool_registry.py       # 工具注册表（定义 + 调度）
│   ├── web_search.py           # 联网搜索（DuckDuckGo + Bing + 网页访问）
│   ├── browser_tool.py         # 浏览器工具（Playwright 无头浏览器）
│   ├── image_generator.py      # AI 生图（调用生图 API + 中文翻译）
│   ├── memory_manager.py       # 长期记忆（按联系人存储事实）
│   └── file_manager.py         # 文件管理（读写编辑工作区文件）
│
└── ai_workspace/              # AI 工作区（运行时生成）
    ├── fetch_github_issue.py   # GitHub Issue 抓取工具
    └── url_fetcher.py          # URL 内容抓取工具
```

---

## 文件说明

### 核心文件

| 文件 | 说明 |
|------|------|
| `auto_reply.py` | **主程序**。启动后持续轮询微信未读消息，群聊仅 @我 时回复，私聊自动回复。支持 `--reset`（清除处理记录）、`--clear-ai`（清除对话历史）、`--clear 联系人`（清除指定联系人对话） |
| `config.py` | **统一配置**。管理 API 地址/密钥、超时参数、UI 路径、日志、AI 对话参数等。自动从 `.env` 文件加载环境变量 |
| `wechat_automation_final.py` | **微信自动化核心**。通过 Windows UIA 接口连接微信窗口，实现：搜索联系人、点击会话、读取聊天消息、发送文字消息、发送图片 |

### modules/ 模块

| 模块 | 说明 |
|------|------|
| `ai_engine.py` | **AI 对话引擎**。管理对话上下文、构建系统提示词、编排多轮工具调用（最多 8 轮）。群聊/私聊使用不同的安全策略和提示词 |
| `tool_registry.py` | **工具注册表**。定义所有可用工具（函数签名 + 描述），提供统一的注册和调用接口。工厂函数 `create_standard_registry()` 根据可用依赖自动构建工具集 |
| `web_search.py` | **联网搜索**。DuckDuckGo 优先，失败自动切换 Bing。内置 URL 访问（提取网页正文）、120 秒结果缓存、自动去重 |
| `browser_tool.py` | **浏览器工具**。基于 Playwright 无头浏览器，支持：打开网页、截图、点击元素、填写表单。用于处理 JS 动态渲染的网站 |
| `image_generator.py` | **AI 生图**。调用生图 API 生成图片并保存到本地。中文提示词自动通过 AI 翻译为英文以获得更好效果。支持 7 种比例 |
| `memory_manager.py` | **长期记忆**。按联系人存储关键事实（姓名、喜好、经历等），AI 在对话中自然引用。自动持久化到 JSON 文件 |
| `file_manager.py` | **文件管理**。读写编辑 `ai_workspace/` 目录下的文件，用于帮对方写代码、创建配置等 |

---

## 安装与运行

### 1. 环境要求

- Windows 10/11
- Python 3.9+
- 微信 PC 版 4.1.9.62（需已登录）

### 2. 安装依赖

```bash
pip install -r requirements.txt

# 浏览器工具还需要安装 Playwright 浏览器
playwright install chromium
```

### 3. 配置环境变量

```bash
# 复制模板并编辑
copy .env.example .env
```

编辑 `.env` 文件，填入你的配置：

```ini
# AI API（必填）
WECHAT_AI_API_URL=http://your-api-url/v1
WECHAT_AI_API_KEY=your-api-key
WECHAT_AI_MODEL=your-model-name

# 代理（可选）
WECHAT_PROXY_URL=http://127.0.0.1:10808

# AI 生图密钥
IMAGE_GEN_API_KEY=your-image-gen-key

# 你的微信昵称（群聊中 @此昵称 时触发回复）
WECHAT_MY_NICKNAME=你的微信昵称
```

### 4. 启动

```bash
python auto_reply.py
```

程序会自动连接微信窗口并开始检测未读消息。

### 5. 命令行参数

```bash
python auto_reply.py              # 正常启动
python auto_reply.py --reset       # 清除已处理会话记录
python auto_reply.py --clear-ai    # 清除所有 AI 对话历史
python auto_reply.py --clear 张三  # 清除指定联系人的对话历史
```

---

## 工作流程

```
┌─────────────────────────────────────────────┐
│              主循环（每 5 秒）                │
├─────────────────────────────────────────────┤
│  1. 扫描微信会话列表，找未读消息              │
│  2. 过滤已处理的会话                         │
│  3. 逐个点击未读会话                         │
│  4. 判断群聊/私聊                            │
│     ├─ 群聊：检测 @我 → 提取上下文+@消息     │
│     └─ 私聊：读取最新消息 → 过滤自己发的      │
│  5. 调用 AI 引擎生成回复                     │
│     ├─ 构建系统提示词（群聊/私聊不同策略）     │
│     ├─ AI 自主决定是否调用工具                │
│     └─ 最多 8 轮工具调用                     │
│  6. 发送回复到微信                           │
│  7. 自动补发生成的图片                       │
└─────────────────────────────────────────────┘
```

---

## AI 工具一览

| 工具 | 说明 | 群聊 | 私聊 |
|------|------|:----:|:----:|
| `web_search` | 搜索互联网 | ✅ | ✅ |
| `visit_url` | 访问网页提取正文 | ✅ | ✅ |
| `browse_url` | 无头浏览器打开网页 | ✅ | ✅ |
| `screenshot_page` | 网页截图 | ✅ | ✅ |
| `click_page` | 浏览器中点击元素 | ✅ | ✅ |
| `fill_form` | 浏览器中填写表单 | ✅ | ✅ |
| `generate_image` | AI 生成图片 | ✅ | ✅ |
| `send_image` | 发送图片到聊天 | ✅ | ✅ |
| `save_memory` | 保存对方信息到记忆 | ✅ | ✅ |
| `read_file` | 读取工作区文件 | ❌ | ✅ |
| `write_file` | 写入文件 | ❌ | ✅ |
| `edit_file` | 编辑文件 | ❌ | ✅ |
| `delete_file` | 删除文件 | ❌ | ✅ |
| `list_files` | 列出目录 | ❌ | ✅ |

---

## AI 生图

支持通过自然语言描述生成图片：

- **中文提示词**：自动翻译为英文以获得更好效果
- **7 种比例**：`1:1`（默认）、`16:9`、`9:16`、`4:3`、`3:4`、`3:2`、`2:3`
- **自动发送**：生成后自动发送到聊天窗口
- **群聊可用**：在群聊中 @机器人 + 描述即可

示例对话：
```
用户：生成一张柴犬在月球上奔跑的图片
用户：画一张 16:9 的日落风景
用户：@菜鸟 帮我画一只戴宇航头盔的猫
```

---

## 注意事项

1. **微信窗口**：运行期间微信窗口需保持可见，不要最小化
2. **微信版本**：适配微信 PC 版 4.1.9.62，其他版本未测试，UI 结构变化可能导致功能异常
3. **安全策略**：群聊中禁用文件操作（防泄密），私聊中允许
4. **频率控制**：多条 @消息回复间自动间隔 1 秒，避免微信限频
5. **API 费用**：AI 对话和生图均消耗 API 额度，请注意用量

---

## License

MIT
