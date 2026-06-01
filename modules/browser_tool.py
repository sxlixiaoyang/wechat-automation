"""
浏览器自动化工具 — 基于 Playwright 操作真实浏览器
支持：打开页面提取内容、截图、点击元素、填写表单
内置反检测伪装，降低被目标网站识别为机器人的概率
"""
import os
import time
import logging
from typing import Optional

PLAYWRIGHT_AVAILABLE = False
try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    pass

# 反检测脚本：注入到每个页面，隐藏自动化痕迹
STEALTH_SCRIPT = """
// 1. 移除 webdriver 标识
Object.defineProperty(navigator, 'webdriver', { get: () => false });

// 2. 伪造 chrome 对象
window.chrome = {
    runtime: {},
    loadTimes: function() {},
    csi: function() {},
    app: {}
};

// 3. 伪造 plugins 数组（真实 Chrome 通常有插件）
Object.defineProperty(navigator, 'plugins', {
    get: () => [1, 2, 3, 4, 5]
});

// 4. 伪造 languages
Object.defineProperty(navigator, 'languages', {
    get: () => ['zh-CN', 'zh', 'en']
});

// 5. 伪造 hardwareConcurrency
Object.defineProperty(navigator, 'hardwareConcurrency', {
    get: () => 8
});

// 6. 重写 permissions.query，防止检测自动化
const originalQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (parameters) => (
    parameters.name === 'notifications' ?
        Promise.resolve({ state: Notification.permission }) :
        originalQuery(parameters)
);

// 7. 移除 phantomjs 等特征
delete window.__nightmare;
delete window.__webdriver_evaluate;
delete window.__webdriver_script_function;
delete window.__webdriver_script_func;
delete window.__webdriver_script_fn;
delete window.__fxdriver_evaluate;
delete window.__driver_evaluate;
delete window.__webdriver_unwrapped;
delete window.__selenium_evaluate;
delete window.__selenium_unwrapped;
"""

logger = logging.getLogger("browser_tool")


class BrowserTool:
    """
    浏览器自动化工具
    使用 Playwright 驱动 Chromium，能处理 JS 渲染页面
    """

    def __init__(self, headless: bool = True, timeout: int = 15000):
        self.headless = headless
        self.timeout = timeout
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._available = PLAYWRIGHT_AVAILABLE

        if not self._available:
            logger.warning("Playwright 未安装，浏览器功能不可用。安装: pip install playwright && playwright install chromium")

        # 截图保存目录
        workspace = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.screenshot_dir = os.path.join(workspace, "ai_workspace", "screenshots")
        os.makedirs(self.screenshot_dir, exist_ok=True)

    # ================================================================
    # 浏览器生命周期
    # ================================================================

    def _ensure_browser(self):
        """确保浏览器已启动"""
        if not self._available:
            raise RuntimeError("Playwright 未安装，无法使用浏览器功能")

        if self._browser and self._browser.is_connected():
            return

        try:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(
                headless=self.headless,
                args=[
                    "--no-sandbox",
                    "--disable-gpu",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-features=IsolateOrigins,site-per-process",
                    "--disable-site-isolation-trials",
                    "--disable-web-security",
                    "--disable-features=BlockInsecurePrivateNetworkRequests",
                ]
            )
            self._context = self._browser.new_context(
                viewport={"width": 1366, "height": 768},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
            )
            self._page = self._context.new_page()
            # 注入反检测脚本
            self._page.add_init_script(STEALTH_SCRIPT)
            logger.info("浏览器已启动（反检测模式）")
        except Exception as e:
            logger.error(f"浏览器启动失败: {e}")
            raise

    def _close_browser(self):
        """关闭浏览器，释放资源"""
        try:
            if self._page:
                self._page.close()
            if self._context:
                self._context.close()
            if self._browser:
                self._browser.close()
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass
        finally:
            self._page = None
            self._context = None
            self._browser = None
            self._playwright = None

    # ================================================================
    # 工具方法
    # ================================================================

    def browse_url(self, url: str, timeout: int = None) -> str:
        """
        打开 URL 并提取渲染后的页面文本内容
        适用于 JS 渲染页面（如 SPA、动态加载内容的网站）
        内置反检测，对百度等反爬严格的网站自动添加 Referer
        """
        if not self._available:
            return "[浏览器不可用] Playwright 未安装。请运行: pip install playwright && playwright install chromium"

        t = timeout or self.timeout
        try:
            self._ensure_browser()
            logger.info(f"浏览: {url}")

            # 对百度系域名设置 Referer，降低验证概率
            goto_opts = {"timeout": t, "wait_until": "domcontentloaded"}
            if any(d in url for d in ["baidu.com", "baijiahao.baidu.com", "zhidao.baidu.com"]):
                goto_opts["referer"] = "https://www.baidu.com"

            self._page.goto(url, **goto_opts)

            # 等待额外渲染（SPA 动态内容）
            time.sleep(2)

            # 提取页面文本
            body = self._page.locator("body")
            text = body.inner_text(timeout=5000) if body.count() > 0 else ""

            # 获取标题
            title = self._page.title()

            # 检测验证/拦截页面
            if self._is_browser_captcha(text, title):
                logger.warning(f"浏览器访问仍遇验证页面: {title}")
                self._close_browser()
                return "[浏览失败] 页面触发安全验证（滑块/验证码），浏览器也无法绕过。请通过搜索引擎搜索相关内容，或换一个来源。"

            # 截断过长内容
            max_chars = 6000
            if len(text) > max_chars:
                text = text[:max_chars] + f"\n\n... [内容过长，已截断，共 {len(text)} 字符]"

            logger.info(f"浏览完成: {title} ({len(text)} 字符)")
            self._close_browser()

            return f"📄 页面标题: {title}\n\n{text}"

        except Exception as e:
            self._close_browser()
            return f"[浏览失败] {url}: {e}"

    @staticmethod
    def _is_browser_captcha(text: str, title: str = "") -> bool:
        """检测浏览器获取的页面是否为验证/拦截页"""
        captcha_signs = [
            "安全验证", "验证码", "人机验证", "滑块验证",
            "请输入验证码", "点击进行验证", "请完成安全验证",
            "环境异常", "访问验证", "为了您的账号安全",
            "当前访问疑似攻击行为", "系统检测到您的访问异常",
            "请先完成验证", "请拖动滑块", "请点击下方",
            "请完成下方验证", "验证一下", "需要滑块",
            "行为验证", "智能验证",
        ]
        # 前 300 字符检测（优先检查页面头部，通常验证信息在开头）
        head = text[:300] if len(text) > 300 else text
        for kw in captcha_signs:
            if kw in head:
                return True
        # 短内容全文检测
        if len(text) < 200:
            for kw in captcha_signs:
                if kw in text:
                    return True
        # 标题含验证关键词
        if title:
            for kw in ["验证", "安全检测", "访问限制", "拦截", "人机校验"]:
                if kw in title and len(title) < 60:
                    return True
        return False

    def screenshot(self, url: str, filename: str = None, timeout: int = None) -> str:
        """
        打开 URL 并截取全页截图
        """
        if not self._available:
            return "[浏览器不可用] Playwright 未安装。"

        t = timeout or self.timeout
        try:
            self._ensure_browser()
            logger.info(f"截图: {url}")

            self._page.goto(url, timeout=t, wait_until="domcontentloaded")
            time.sleep(2)

            # 生成文件名
            if not filename:
                safe_name = url.replace("https://", "").replace("http://", "").replace("/", "_")[:50]
                filename = f"{safe_name}_{int(time.time())}.png"
            if not filename.endswith(".png"):
                filename += ".png"

            filepath = os.path.join(self.screenshot_dir, filename)
            self._page.screenshot(path=filepath, full_page=True)

            title = self._page.title()
            logger.info(f"截图已保存: {filepath}")
            self._close_browser()

            rel_path = os.path.join("screenshots", filename)
            return f"📸 截图已保存: {rel_path}\n页面标题: {title}\n文件路径: {filepath}"

        except Exception as e:
            self._close_browser()
            return f"[截图失败] {url}: {e}"

    def click_and_extract(self, url: str, selector: str, timeout: int = None) -> str:
        """
        打开页面 → 点击指定元素 → 提取点击后的页面内容
        """
        if not self._available:
            return "[浏览器不可用] Playwright 未安装。"

        t = timeout or self.timeout
        try:
            self._ensure_browser()
            logger.info(f"点击操作: {url} -> {selector}")

            self._page.goto(url, timeout=t, wait_until="domcontentloaded")
            time.sleep(1)

            # 点击元素
            self._page.locator(selector).first.click(timeout=5000)
            time.sleep(1.5)

            # 提取内容
            body = self._page.locator("body")
            text = body.inner_text(timeout=5000) if body.count() > 0 else ""
            title = self._page.title()

            max_chars = 4000
            if len(text) > max_chars:
                text = text[:max_chars] + f"\n\n... [已截断，共 {len(text)} 字符]"

            logger.info(f"点击完成: {title}")
            self._close_browser()

            return f"🖱️ 已点击 '{selector}'\n📄 页面标题: {title}\n\n{text}"

        except Exception as e:
            self._close_browser()
            return f"[点击失败] {selector}: {e}"

    def fill_and_submit(self, url: str, fill_selector: str, text: str,
                        submit_selector: str = None, timeout: int = None) -> str:
        """
        打开页面 → 填写输入框 → 提交表单 → 提取结果
        """
        if not self._available:
            return "[浏览器不可用] Playwright 未安装。"

        t = timeout or self.timeout
        try:
            self._ensure_browser()
            logger.info(f"填表操作: {url} -> {fill_selector}")

            self._page.goto(url, timeout=t, wait_until="domcontentloaded")
            time.sleep(1)

            # 填写
            self._page.locator(fill_selector).first.fill(text, timeout=5000)
            time.sleep(0.5)

            # 提交（按 Enter 或点击按钮）
            if submit_selector:
                self._page.locator(submit_selector).first.click(timeout=5000)
            else:
                self._page.locator(fill_selector).first.press("Enter")
            time.sleep(2)

            # 提取结果
            body = self._page.locator("body")
            result_text = body.inner_text(timeout=5000) if body.count() > 0 else ""
            title = self._page.title()

            max_chars = 4000
            if len(result_text) > max_chars:
                result_text = result_text[:max_chars] + f"\n\n... [已截断，共 {len(result_text)} 字符]"

            logger.info(f"填表完成: {title}")
            self._close_browser()

            return f"⌨️ 已在 '{fill_selector}' 填入文本并提交\n📄 页面标题: {title}\n\n{result_text}"

        except Exception as e:
            self._close_browser()
            return f"[填表失败] {url}: {e}"

    def __del__(self):
        """析构时释放浏览器资源"""
        self._close_browser()
