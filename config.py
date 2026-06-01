"""
统一配置文件
集中管理微信窗口标题、超时参数、UI路径、日志、API等所有配置
"""
import os
import logging

# ============================================================
# 微信窗口配置
# ============================================================
WECHAT_WINDOW_TITLE = "微信"

# ============================================================
# 超时与等待配置（秒）
# ============================================================
TIMEOUTS = {
    "window_connect": 10,       # 连接窗口超时
    "search_result": 2,         # 搜索结果等待
    "click_interval": 0.1,      # 点击间隔
    "input_clear": 0.3,         # 清空输入框后等待
    "send_wait": 0.5,           # 发送后等待
    "poll_interval": 5,         # 未读消息轮询间隔
}

# ============================================================
# UI 元素路径配置
# ============================================================
UI_PATHS = {
    "search_box": {
        "class_name": "mmui::XValidatorTextEdit",
        "name": "搜索",
    },
    "input_box": {
        "automation_id": "chat_input_field",
        "class_name": "mmui::ChatInputField",
    },
    "send_button": {
        "name": "发送",
    },
    "session_list": {
        "name": "会话",
    },
    "message_list": {
        "name": "消息",
    },
    "search_result_item": {
        "class_name": "mmui::SearchContentCellView",
    },
}

# ============================================================
# 日志配置
# ============================================================
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

LOG_CONFIG = {
    "level": "INFO",
    "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    "file": os.path.join(PROJECT_ROOT, "wechat_automation.log"),
    "max_bytes": 10 * 1024 * 1024,  # 10MB
    "backup_count": 5,
}

# ============================================================
# 日志初始化函数（统一入口）
# ============================================================
def setup_logging(
    name: str = None,
    level: str = None,
    log_file: str = None,
    console: bool = True,
) -> logging.Logger:
    """
    统一日志初始化
    :param name: logger 名称，默认使用 root
    :param level: 日志级别，默认使用 LOG_CONFIG["level"]
    :param log_file: 日志文件路径，为 None 时只输出到控制台
    :param console: 是否输出到控制台
    """
    import logging.handlers

    logger = logging.getLogger(name)
    log_level = getattr(logging, (level or LOG_CONFIG["level"]).upper(), logging.INFO)
    logger.setLevel(log_level)

    # 避免重复添加 handler
    if logger.handlers:
        return logger

    fmt = logging.Formatter(LOG_CONFIG["format"])

    if log_file or LOG_CONFIG["file"]:
        file_path = log_file or LOG_CONFIG["file"]
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            file_path,
            maxBytes=LOG_CONFIG["max_bytes"],
            backupCount=LOG_CONFIG["backup_count"],
            encoding="utf-8",
        )
        fh.setLevel(log_level)
        fh.setFormatter(fmt)
        logger.addHandler(fh)

    if console:
        ch = logging.StreamHandler()
        ch.setLevel(log_level)
        ch.setFormatter(fmt)
        logger.addHandler(ch)

    return logger

# ============================================================
# API 配置（从环境变量读取，支持 .env 文件回退）
# ============================================================
def _load_dotenv():
    """尝试从 .env 文件加载环境变量"""
    env_path = os.path.join(PROJECT_ROOT, ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, _, value = line.partition("=")
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    if key and key not in os.environ:
                        os.environ[key] = value

_load_dotenv()

API_URL = os.getenv("WECHAT_AI_API_URL", "http://192.168.10.18:10030/v1")
API_KEY = os.getenv("WECHAT_AI_API_KEY", "")
MODEL = os.getenv("WECHAT_AI_MODEL", "mistralai/mistral-medium-3.5-128b")
PROXY_URL = os.getenv("WECHAT_PROXY_URL", "http://127.0.0.1:10808")
WECHAT_MY_NICKNAME = os.getenv("WECHAT_MY_NICKNAME", "")  # 我的微信昵称（用于检测群聊@我）
IMAGE_GEN_API_KEY = os.getenv("IMAGE_GEN_API_KEY", "")  # AI 生图 API 密钥
IMAGE_GEN_ENABLED = bool(IMAGE_GEN_API_KEY)

# ============================================================
# AI 对话配置
# ============================================================
AI_CONFIG = {
    "max_context_length": 40,       # 每个联系人最多保留的对话消息条数（加大以提供更多上下文）
    "max_conversations": 200,       # 保存的联系人数量上限
    "conversations_file": os.path.join(PROJECT_ROOT, ".ai_conversations.json"),
    "processed_sessions_file": os.path.join(PROJECT_ROOT, ".processed_sessions.txt"),
    # 消息读取与上下文配置
    "personal_chat_read_count": 15,     # 个人聊天读取消息条数（默认5→15）
    "group_chat_read_count": 40,        # 群聊读取消息条数（默认30→40）
    "group_context_window": 20,         # 群聊上下文传给AI的消息条数（默认8→20）
}

# ============================================================
# 通用工具
# ============================================================
def get_project_dir() -> str:
    """获取项目根目录"""
    return PROJECT_ROOT
