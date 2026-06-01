"""
微信UI自动化 - 基于实际UI元素定位
搜索 -> 选择会话 -> 读取消息 -> 发送消息
"""

import uiautomation as auto
import time
import os
import re
import logging
import subprocess
from typing import Optional, List, Dict, Any

from config import WECHAT_WINDOW_TITLE, setup_logging

logger = setup_logging("wechat_automation_final", console=False)


def _set_clipboard_text(text: str) -> bool:
    """将文本写入 Windows 剪贴板"""
    try:
        import win32clipboard
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardText(text, win32clipboard.CF_UNICODETEXT)
        finally:
            win32clipboard.CloseClipboard()
        return True
    except Exception:
        return False


class WeChatAutomation:
    """微信自动化主类"""

    def __init__(self):
        self.window = None
        self.is_connected = False
        self.logger = logging.getLogger(__name__)

    def connect(self) -> bool:
        """连接到微信窗口"""
        try:
            self.logger.info("正在查找微信窗口...")
            self.window = auto.WindowControl(searchDepth=1, Name=WECHAT_WINDOW_TITLE)

            if self.window.Exists():                # 将微信窗口置顶
                self.window.SetActive()
                time.sleep(0.1)
                self.is_connected = True
                self.logger.info("✓ 成功连接到微信窗口")
                return True

            self.logger.error("✗ 未找到微信窗口")
            return False

        except Exception as e:
            self.logger.error(f"连接失败: {e}")
            return False

    def find_search_box(self):
        """查找搜索框"""
        try:
            self.logger.info("正在查找搜索框...")

            search_box = self.window.EditControl(searchDepth=20, ClassName="mmui::XValidatorTextEdit")
            if search_box.Exists():
                self.logger.info("✓ 找到搜索框 (类名定位)")
                return search_box

            search_box = self.window.EditControl(searchDepth=20, Name="搜索")
            if search_box.Exists():
                self.logger.info("✓ 找到搜索框 (名称定位)")
                return search_box

            self.logger.warning("✗ 未找到搜索框")
            return None

        except Exception as e:
            self.logger.error(f"查找搜索框失败: {e}")
            return None

    def search_and_select(self, keyword: str) -> bool:
        """搜索并选择会话"""
        try:
            self.logger.info(f"正在搜索: {keyword}")

            # 1. 找到并点击搜索框
            t0 = time.time()
            search_box = self.find_search_box()
            if not search_box:
                return False
            self.logger.info(f"  - 找到搜索框: {time.time()-t0:.3f}s")

            search_box.Click()
            time.sleep(0.1)

            # 清空搜索框 - 直接多次按Backspace
            for _ in range(15):
                auto.SendKeys("{BACK}")
                time.sleep(0.02)

            # 输入搜索关键词
            search_box.SendKeys(keyword)
            time.sleep(0.3)  # 等待搜索结果出现

            # 4. 尝试点击搜索结果项
            search_items = self.window.ListItemControl(searchDepth=15, ClassName="mmui::SearchContentCellView")
            if search_items.Exists():
                self.logger.info("✓ 点击搜索结果项")
                search_items.Click()
                time.sleep(0.2)
                return True

            # 备选：点击会话项
            session_item = self.window.ListItemControl(searchDepth=15, ClassName="mmui::ChatSessionCell")
            if session_item.Exists():
                self.logger.info(f"✓ 点击会话项: {session_item.Name}")
                session_item.Click()
                time.sleep(0.2)
                return True

            self.logger.error("✗ 未找到可点击的搜索结果")
            return False

        except Exception as e:
            self.logger.error(f"搜索失败: {e}")
            return False

    def read_current_chat(self, count: int = 10) -> List[Dict[str, Any]]:
        """读取当前聊天消息，同时尝试提取发送者名"""
        try:
            self.logger.info("正在读取当前聊天消息...")

            msg_list = self.window.ListControl(searchDepth=25, Name="消息")
            if not msg_list.Exists():
                msg_list = self.window.ListControl(searchDepth=25, Name="chatMessageList")

            if not msg_list.Exists():
                self.logger.error("✗ 未找到消息列表")
                return []

            self.logger.info("✓ 找到消息列表")

            items = msg_list.GetChildren()
            recent_items = items[-count:] if len(items) > count else items

            messages = []
            prev_sender_name = ""  # 上一条可能是独立发送者名（纯昵称 TextControl）

            # 每次调用重置诊断计数器
            WeChatAutomation._diagnose_count = 0

            for item in recent_items:
                msg_text = (item.Name or "").strip()
                if not msg_text:
                    continue

                # 跳过系统消息
                if self._is_system_message(msg_text, item):
                    continue

                sender = self._extract_sender_from_item(item, msg_text)

                # ── 关键：prev_sender_name 关联 ──
                # 微信 UI 中群聊发送者名有时是独立的一条 ListItem（纯昵称）
                # 例如:  ["矛"] → ["@菜鸟 今天的百度热点"]
                # prev_sender_name 只接受像真人昵称的短文本（1-8字符，纯中文/英文/数字）
                if not sender and prev_sender_name:
                    # 当前消息不是以 @ 开头（即不是机器人回复）
                    if not msg_text.startswith("@"):
                        sender = prev_sender_name

                messages.append({
                    "content": msg_text,
                    "sender": sender,
                    "element": item
                })

                # ── 更新 prev_sender_name ──
                # 机器人自己发的消息（以 @ 开头）不能作为发送者名
                if re.match(r"^[\u4e00-\u9fff\w]{1,8}$", msg_text):
                    # 纯昵称（如 "矛"）→ 存为候选发送者
                    prev_sender_name = msg_text
                elif msg_text.startswith("@"):
                    # 机器人回复（以 @ 开头）→ 清空候选（防止污染）
                    prev_sender_name = ""
                else:
                    # 普通消息 → 保留当前候选（因为下一条消息可能同一个发送者）
                    pass

            self.logger.info(f"✓ 读取到 {len(messages)} 条消息")
            return messages

        except Exception as e:
            self.logger.error(f"读取消息失败: {e}")
            return []

    # 诊断计数器：只在前几条消息打印 UI 结构详情
    _diagnose_count = 0

    def _extract_sender_from_item(self, item, msg_text: str) -> str:
        """从消息 ListItem 中提取发送者昵称（多种策略）"""
        msg_text = msg_text.strip()

        # ── 初次诊断: dump item 的底层结构 ──
        if WeChatAutomation._diagnose_count < 2:
            try:
                # 用 repr 看是否有隐藏字符（换行等）
                parent_name = getattr(item, '_control', None)
                if parent_name is None:
                    parent_name = item.__class__.__name__
                self.logger.info(f"    [诊断] item class={item.__class__.__name__}, "
                                 f"Name=repr({repr(item.Name)[:120]}), "
                                 f"ClassName={getattr(item, 'ClassName', '?')}, "
                                 f"AutomationId={getattr(item, 'AutomationId', '?')}")
                children = item.GetChildren()
                self.logger.info(f"    [诊断] Children数量={len(children) if children else 0}")
                for ci, c in enumerate(children[:6]):
                    try:
                        self.logger.info(f"    [诊断]   child[{ci}]: class={c.__class__.__name__}, "
                                         f"Name=repr({repr(c.Name)[:80]}), "
                                         f"ClassName={getattr(c, 'ClassName', '?')}, "
                                         f"ControlType={getattr(c, 'ControlType', '?')}")
                    except Exception as ex:
                        self.logger.info(f"    [诊断]   child[{ci}]: 读取异常 {ex}")
                WeChatAutomation._diagnose_count += 1
            except Exception as e:
                self.logger.info(f"    [诊断] dump失败: {e}")
                WeChatAutomation._diagnose_count += 1

        # ── 策略1: Name 自己可能含发送者（"发送者\n消息" 或 "发送者：消息"）──
        if "\n" in msg_text:
            first_line, rest = msg_text.split("\n", 1)
            if len(first_line) <= 20 and not first_line.startswith("http"):
                return first_line.strip()

        # ── 策略2: 遍历子控件 ──
        try:
            children = item.GetChildren()
            for child in children:
                try:
                    child_name = (child.Name or "").strip()
                    if not child_name:
                        continue
                    # 发送者名：≤ 20 字符，不同于消息正文
                    if 1 <= len(child_name) <= 20 and child_name != msg_text:
                        return child_name
                except Exception:
                    continue
        except Exception:
            pass

        # ── 策略3: 用 TextControl 搜索 ──
        try:
            tc = item.TextControl(searchDepth=2)
            if tc and tc.Exists():
                tc_name = (tc.Name or "").strip()
                if tc_name and len(tc_name) <= 20 and tc_name != msg_text:
                    return tc_name
        except Exception:
            pass

        # ── 策略4: item.Text 不等于 Name ──
        try:
            item_text = (item.Text or "").strip()
            if item_text and item_text != msg_text:
                if "\n" in item_text:
                    first_line = item_text.split("\n")[0].strip()
                    if len(first_line) <= 20:
                        return first_line
        except Exception:
            pass

        # ── 策略5: 从内容解析 "XXX: 正文" ──
        if ": " in msg_text and not msg_text.startswith("http"):
            prefix = msg_text.split(": ", 1)[0].strip()
            if len(prefix) <= 20 and not re.search(r"[/\\]", prefix) and " " not in prefix:
                return prefix

        return ""

    @staticmethod
    def _is_system_message(text: str, item) -> bool:
        """判断是否为系统消息（时间戳、提示等）"""
        if re.match(r"^\d{1,2}:\d{2}$", text):
            return True
        if re.match(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}$", text):
            return True
        system_keywords = ["你撤回了一条消息", "撤回了一条消息", "加入群聊", "退出了群聊",
                           "修改群名为", "被移出群聊", "成为新群主"]
        for kw in system_keywords:
            if kw in text:
                return True
        return False

    def _search_group_title(self, control, depth: int = 0, max_depth: int = 30) -> str:
        """递归搜索控件树，找到标题中带 (数字) 的 TextControl（群聊特征）"""
        if depth > max_depth:
            return ""
        try:
            name = control.Name
            if name and re.search(r"\(\d+\)$", name.strip()):
                return name.strip()
        except Exception:
            pass
        try:
            for child in control.GetChildren():
                result = self._search_group_title(child, depth + 1, max_depth)
                if result:
                    return result
        except Exception:
            pass
        return ""

    def is_group_chat(self) -> bool:
        """
        判断当前聊天窗口是否为群聊。
        三层检测：
        1. 控件类型：ChatTitleBarChatSingleView / ChatTitleBarChatGroupView
        2. 标题文本：遍历控件树，查找 Name 带 (数字) 的 TextControl
        3. 都没命中 → 默认为非群聊（保守）
        """
        try:
            single = self.window.GroupControl(
                searchDepth=20, ClassName="mmui::ChatTitleBarChatSingleView")
            if single.Exists():
                return False

            group = self.window.GroupControl(
                searchDepth=20, ClassName="mmui::ChatTitleBarChatGroupView")
            if group.Exists():
                self.logger.info("  检测到群聊（控件类型）")
                return True

            # 兜底：递归搜索控件树，找标题中带 (数字) 的 TextControl
            title = self._search_group_title(self.window)
            if title:
                self.logger.info(f"  检测到群聊（标题模式）: {title}")
                return True

            # 最终兜底：都没命中，默认为非群聊
            return False
        except Exception:
            return False

    def find_input_box(self):
        """查找输入框"""
        try:
            self.logger.info("正在查找输入框...")

            input_box = self.window.EditControl(searchDepth=20, AutomationId="chat_input_field")
            if input_box.Exists():
                self.logger.info("✓ 找到输入框 (ID定位)")
                return input_box

            input_box = self.window.EditControl(searchDepth=20, ClassName="mmui::ChatInputField")
            if input_box.Exists():
                self.logger.info("✓ 找到输入框 (类名定位)")
                return input_box

            self.logger.warning("✗ 未找到输入框")
            return None

        except Exception as e:
            self.logger.error(f"查找输入框失败: {e}")
            return None

    def find_send_button(self):
        """查找发送按钮"""
        try:
            self.logger.info("正在查找发送按钮...")

            send_btn = self.window.ButtonControl(searchDepth=20, Name="发送")
            if send_btn.Exists():
                self.logger.info("✓ 找到发送按钮 (名称定位)")
                return send_btn

            send_btn = self.window.TextControl(searchDepth=20, Name="发送")
            if send_btn.Exists():
                self.logger.info("✓ 找到发送按钮 (TextControl定位)")
                return send_btn

            self.logger.warning("✗ 未找到发送按钮")
            return None

        except Exception as e:
            self.logger.error(f"查找发送按钮失败: {e}")
            return None

    def send_message(self, message: str) -> bool:
        """发送消息（使用剪贴板粘贴，避免 SendKeys 中文乱码）"""
        try:
            self.logger.info(f"正在发送消息: {message}")

            input_box = self.find_input_box()
            if not input_box:
                self.logger.error("✗ 未找到输入框")
                return False

            input_box.Click()
            time.sleep(0.1)

            # 清空输入框：先 Ctrl+A 全选，再删除
            auto.SendKeys("{Ctrl}A")
            time.sleep(0.05)
            auto.SendKeys("{DELETE}")
            time.sleep(0.05)

            # 通过剪贴板粘贴发送中文文本（避免 SendKeys 无法处理 IME 输入导致乱码）
            if not _set_clipboard_text(message):
                self.logger.warning("剪贴板写入失败，回退到 SendKeys")
                input_box.SendKeys(message)
            else:
                auto.SendKeys("{Ctrl}V")
            time.sleep(0.1)

            send_btn = self.find_send_button()
            if send_btn:
                send_btn.Click()
                time.sleep(0.2)
                self.logger.info("✓ 消息发送成功")
                return True

            auto.SendKeys("{ENTER}")
            time.sleep(0.2)
            self.logger.info("✓ 消息发送成功 (Enter)")
            return True

        except Exception as e:
            self.logger.error(f"发送消息失败: {e}")
            return False

    def send_image(self, image_path: str) -> bool:
        """发送图片（通过剪贴板粘贴）"""
        try:
            import subprocess

            if not os.path.exists(image_path):
                self.logger.error(f"图片文件不存在: {image_path}")
                return False

            self.logger.info(f"正在发送图片: {image_path}")

            # 通过 PowerShell 将图片复制到剪贴板
            ps_script = (
                f'Add-Type -AssemblyName System.Windows.Forms;'
                f'$img = [System.Drawing.Image]::FromFile("{image_path}");'
                f'[System.Windows.Forms.Clipboard]::SetImage($img);'
                f'$img.Dispose()'
            )
            result = subprocess.run(
                ["powershell", "-Command", ps_script],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode != 0:
                self.logger.error(f"剪贴板写入失败: {result.stderr}")
                return False

            time.sleep(0.2)

            # 激活窗口，聚焦输入框
            if not self.window:
                self.logger.error("窗口未连接")
                return False
            self.window.SetFocus()
            time.sleep(0.1)

            input_box = self.find_input_box()
            if input_box:
                input_box.Click()
                time.sleep(0.1)

            # Ctrl+V 粘贴图片
            auto.SendKeys("{Ctrl}V")
            time.sleep(0.3)

            # 点击发送
            send_btn = self.find_send_button()
            if send_btn:
                send_btn.Click()
                time.sleep(0.2)
                self.logger.info("✓ 图片发送成功")
                return True

            auto.SendKeys("{ENTER}")
            time.sleep(0.2)
            self.logger.info("✓ 图片发送成功 (Enter)")
            return True

        except Exception as e:
            self.logger.error(f"发送图片失败: {e}")
            return False

    def cleanup(self):
        """清理资源"""
        self.is_connected = False
        self.logger.info("已清理资源")


def main():
    """主函数"""
    print("=" * 60)
    print("微信UI自动化 - 完整流程测试")
    print("=" * 60)

    wechat = WeChatAutomation()

    try:
        print("\n[步骤1] 连接微信...")
        if not wechat.connect():
            print("✗ 连接失败")
            return
        print("✓ 连接成功")

        print("\n[步骤2] 搜索'矛'...")
        if not wechat.search_and_select("矛"):
            print("✗ 搜索失败")
            return
        print("✓ 搜索并选择成功")

        print("\n[步骤3] 读取消息...")
        messages = wechat.read_current_chat(count=5)
        if messages:
            print(f"✓ 读取到 {len(messages)} 条消息:")
            for i, msg in enumerate(messages, 1):
                content = msg['content'][:50] + "..." if len(msg['content']) > 50 else msg['content']
                print(f"  {i}. {content}")
        else:
            print("⚠ 没有读取到消息或读取失败")

        print("\n[步骤4] 发送'你好呀'...")
        if wechat.send_message("你好呀"):
            print("✓ 发送成功")
        else:
            print("✗ 发送失败")

        print("\n" + "=" * 60)
        print("全部流程完成！")
        print("=" * 60)

    except Exception as e:
        print(f"\n✗ 发生错误: {e}")
        import traceback
        traceback.print_exc()

    finally:
        wechat.cleanup()

    input("\n按回车键退出...")


if __name__ == "__main__":
    main()
