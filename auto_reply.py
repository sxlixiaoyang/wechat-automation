"""
微信自动检测并智能回复未读消息
使用大模型根据消息内容生成回复
每个联系人维护独立的对话上下文

模块化架构：所有功能模块独立加载，任一模组故障不影响主程序
"""
import sys
import time
import re
import os

# 使用项目目录而非硬编码路径
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

from wechat_automation_final import WeChatAutomation
from config import API_URL, API_KEY, MODEL, AI_CONFIG, WECHAT_MY_NICKNAME, setup_logging

# 初始化日志（最先初始化，确保后续模块能记录错误）
logger = setup_logging("auto_reply")

# ── 模块化加载：优先使用 modules/ 下的独立模块，失败降级到旧文件 ──
def _load_ai_engine():
    """安全加载 AI 引擎，支持新旧两种路径"""
    # 优先尝试新模块化路径
    try:
        from modules.ai_engine import AIReplyEngine
        logger.info("AI引擎已从模块化路径加载 (modules.ai_engine)")
        return AIReplyEngine
    except Exception as e:
        logger.warning(f"模块化路径加载失败: {e}，尝试旧路径")

    # 降级到旧路径
    try:
        from ai_reply_engine import AIReplyEngine
        logger.info("AI引擎已从旧路径加载 (ai_reply_engine)")
        return AIReplyEngine
    except Exception as e:
        logger.error(f"AI引擎加载完全失败: {e}")
        raise ImportError("无法加载AI引擎，请检查依赖") from e

AIReplyEngine = _load_ai_engine()

# 已处理会话记录文件
PROCESSED_FILE = AI_CONFIG["processed_sessions_file"]


def _norm_key(raw: str) -> str:
    """将 full_name 中的换行转为 | 分隔，确保一行一条记录"""
    return raw.replace("\n", " | ").strip()


def load_processed_sessions():
    if os.path.exists(PROCESSED_FILE):
        try:
            with open(PROCESSED_FILE, "r", encoding="utf-8") as f:
                return set(line.strip() for line in f if line.strip())
        except Exception as e:
            logger.error(f"加载已处理会话记录失败: {e}")
    return set()


def save_processed_session(session_name):
    try:
        with open(PROCESSED_FILE, "a", encoding="utf-8") as f:
            f.write(_norm_key(session_name) + "\n")
    except Exception as e:
        logger.error(f"保存已处理会话记录失败: {e}")


def clear_processed_sessions():
    if os.path.exists(PROCESSED_FILE):
        try:
            os.remove(PROCESSED_FILE)
            logger.info("已清除所有处理记录")
        except OSError as e:
            logger.error(f"清除处理记录失败: {e}")


def _extract_contact_name(raw_name: str) -> str:
    """
    从 ChatSessionCell 的 Name 属性中提取真实联系人姓名。
    Name 格式类似: "矛\\n[1条] \\n消息预览\\n21:34"
    拆分为多行，过滤掉 [N条]、时间格式，取第一行作为联系人姓名。
    """
    lines = [l.strip() for l in raw_name.split('\n') if l.strip()]
    # 过滤掉 "[数字条]" 和 "HH:MM" 时间格式
    clean = [l for l in lines
             if not re.match(r'^\[\d+条?\]', l)
             and not re.match(r'^\d{1,2}:\d{2}', l)
             and not re.match(r'^\d{1,2}:\d{2}:\d{2}', l)]
    if clean:
        return clean[0]
    # 兜底：取第一行或原始名称
    return lines[0] if lines else raw_name


def detect_unread_sessions(wechat: WeChatAutomation):
    """检测带未读消息的会话"""
    try:
        LIST_ITEM_TYPE = 50007
        found_controls = []

        def find_all_list_items(control, depth=0, max_depth=30):
            if depth > max_depth:
                return
            try:
                if hasattr(control, "ControlType") and control.ControlType == LIST_ITEM_TYPE:
                    found_controls.append({
                        "name": control.Name or "",
                        "class": control.ClassName or "",
                        "depth": depth,
                        "element": control,
                    })
                children = control.GetChildren()
                for child in children:
                    find_all_list_items(child, depth + 1, max_depth)
            except Exception:
                pass

        find_all_list_items(wechat.window)

        unread_sessions = []
        seen_names = set()  # 同一联系人多条消息只保留一条
        for item in found_controls:
            try:
                name = item["name"]
                class_name = item["class"]
                if "ChatSessionCell" in class_name:
                    unread_match = re.search(r"\[(\d+)条?\]", name)
                    if unread_match:
                        unread_count = unread_match.group(1)
                        contact_name = _extract_contact_name(name)
                        if contact_name and contact_name not in seen_names:
                            seen_names.add(contact_name)
                            unread_sessions.append({
                                "name": contact_name,
                                "unread_count": unread_count,
                                "element": item["element"],
                                "full_name": name,
                            })
            except Exception:
                continue

        return unread_sessions

    except Exception as e:
        logger.error(f"检测未读会话失败: {e}")
        return []


def _make_at_pattern(nickname: str) -> str:
    """
    构建严格的 @昵称 匹配正则。
    确保 @昵称 后面不是字母/数字/下划线/中文（避免子串误判）。
    如 @菜鸟 不会误匹配 @菜鸟窝、邮箱@菜鸟互动.com 等。
    """
    escaped = re.escape(nickname)
    # 负向前瞻：@昵称 之后不能紧跟 字母数字下划线或中文字符
    return rf"@\s*{escaped}(?![a-zA-Z0-9_\u4e00-\u9fff])"


def _is_at_me(messages: list, my_nickname: str) -> bool:
    """
    检查消息列表中是否有人 @ 了我。
    遍历全部消息（不限制条数），确保不漏掉任何 @。
    支持 @所有人。
    """
    if not my_nickname:
        logger.warning("  ⚠️ 未配置 WECHAT_MY_NICKNAME，群聊中无法检测 @我，跳过回复")
        return False

    at_pattern = _make_at_pattern(my_nickname)

    for msg in messages:  # 检查全部消息，不止最近5条
        content = msg.get("content", "")
        if not content:
            continue
        if "@所有人" in content:
            logger.info(f"    检测到 @所有人")
            return True
        if re.search(at_pattern, content):
            logger.info(f"    检测到 @我: {content[:60]}")
            return True
    return False


def _find_at_me_messages(messages: list, my_nickname: str) -> list:
    """
    找出所有 @我 的消息，每条返回 (发送者名, 消息内容)。
    遍历全部消息，确保不遗漏任何 @。
    发送者名优先使用 UI 元素提取的 sender 字段。
    """
    if not my_nickname:
        return []

    at_pattern = _make_at_pattern(my_nickname)

    results = []
    for msg in messages:  # 检查全部消息，不限制条数
        content = msg.get("content", "")
        if not content:
            continue
        # 严格匹配 @昵称 或 @所有人
        if not re.search(at_pattern, content) and "@所有人" not in content:
            continue

        sender = (msg.get("sender") or "").strip()
        results.append((sender, content))

    return results


def read_and_reply(wechat: WeChatAutomation, ai_engine: AIReplyEngine, session_name: str) -> bool:
    """读取消息并用AI生成回复（群聊仅 @我 时回复，多人 @我 逐条处理并 @发送者）"""
    try:
        # ── 群聊检测（双重保险）──
        is_group = wechat.is_group_chat()

        # 兜底：控件检测失败时，通过会话名判断（群名末尾带 (数字) 如 "xxx群(189)"）
        if not is_group and re.search(r"\(\d+\)$", session_name.strip()):
            logger.info(f"  ⚡ 控件检测未识别为群聊，但会话名符合群特征: {session_name}")
            is_group = True

        messages = []  # 确保始终有定义

        if is_group:
            logger.info(f"  检测到群聊: {session_name}")
            messages = wechat.read_current_chat(count=30)

            if not messages:
                logger.info(f"  群聊无消息，跳过")
                return False

            if not _is_at_me(messages, WECHAT_MY_NICKNAME):
                # 打印最近3条帮定位
                for i, msg in enumerate(messages[-3:], 1):
                    logger.info(f"    [{i}] {msg['content'][:60]}")
                logger.info(f"  群聊未 @我，跳过")
                return False

            # ── 群聊：找到所有 @我 的消息，逐条处理 ──
            at_msgs = _find_at_me_messages(messages, WECHAT_MY_NICKNAME)
            # 过滤掉自己发的消息（自己发的回复里也可能包含 @昵称）
            at_msgs = [(s, c) for s, c in at_msgs if s != WECHAT_MY_NICKNAME]
            # 去重：相同内容的消息只处理一次（UI 可能重复读取）
            seen_contents = set()
            deduped = []
            for s, c in at_msgs:
                if c not in seen_contents:
                    seen_contents.add(c)
                    deduped.append((s, c))
            at_msgs = deduped
            if not at_msgs:
                logger.info(f"  群聊未找到 @我 的消息，跳过")
                return False

            logger.info(f"  群聊中 {len(at_msgs)} 条消息 @了我:")
            for sender, content in at_msgs:
                logger.info(f"    @{sender or '?'}: {content[:50]}...")

            # 构建群聊上下文（所有消息）
            context_lines = []
            for i, msg in enumerate(messages[-8:], 1):
                context_lines.append(f"[消息{i}] {msg['content']}")
            group_context = "\n".join(context_lines)

            # 逐条处理每个 @我 的消息
            any_sent = False
            for at_sender, at_content in at_msgs:
                logger.info(f"  处理 @{at_sender or '?'} 的消息...")

                # 构建 AI 输入：上下文 + 当前要回复的消息
                ai_input = (
                    f"以下是最新的群聊消息上下文:\n{group_context}\n\n"
                    f"现在我需要回复这条 @我 的消息（来自 {at_sender or '未知用户'}）:\n{at_content}"
                )

                reply = ai_engine.generate_reply(session_name, ai_input, is_group_chat=True,
                                                  send_image_fn=wechat.send_image)
                logger.info(f"  AI回复: {reply[:80]}...")

                # 自动 @发送者
                if at_sender and not reply.startswith(f"@{at_sender}") and f"@{at_sender}" not in reply[:20]:
                    final_reply = f"@{at_sender} {reply}"
                else:
                    final_reply = reply

                logger.info(f"  最终发送: {final_reply[:80]}...")
                if wechat.send_message(final_reply):
                    logger.info(f"  → @{at_sender or '?'} 回复成功")
                    any_sent = True
                    time.sleep(1)  # 多条回复之间间隔一下，避免微信限频
                else:
                    logger.error(f"  → @{at_sender or '?'} 回复失败")

            return any_sent

        else:
            # ── 个人聊天 ──
            logger.info(f"  个人聊天: {session_name}")
            messages = wechat.read_current_chat(count=5)

            if not messages:
                logger.warning(f"  没有读取到消息")
                return False

            logger.info(f"  读取到 {len(messages)} 条消息:")
            for i, msg in enumerate(messages, 1):
                logger.info(f"    {i}. {msg['content'][:50]}...")

            # ── 过滤自己的消息：如果最后一条是自己发的，不回复 ──
            last_msg = messages[-1]
            last_sender = (last_msg.get("sender") or "").strip()
            if last_sender and last_sender == WECHAT_MY_NICKNAME:
                logger.info(f"  最后一条是自己发的，跳过")
                return False

            # ── 只取对方发的最新消息作为 AI 输入 ──
            # 从后往前找第一条对方发的消息
            ai_input = None
            for msg in reversed(messages):
                sender = (msg.get("sender") or "").strip()
                if not sender or sender != WECHAT_MY_NICKNAME:
                    ai_input = msg["content"]
                    break

            if not ai_input:
                logger.info(f"  未找到对方消息，跳过")
                return False

            logger.info(f"  AI正在生成回复...")
            reply = ai_engine.generate_reply(session_name, ai_input, is_group_chat=False,
                                              send_image_fn=wechat.send_image)
            logger.info(f"  AI回复: {reply}")

            logger.info(f"  正在发送...")
            if wechat.send_message(reply):
                logger.info(f"  发送成功")
                return True
            else:
                logger.error(f"  发送失败")
                return False

    except Exception as e:
        logger.error(f"  读取/回复失败: {e}")
        return False


def main():
    print("=" * 60)
    print("微信AI智能自动回复")
    print("   (按 Ctrl+C 退出)")
    print("=" * 60)

    # 检查API Key
    if not API_KEY:
        logger.error("未设置 API Key！请在 .env 文件中配置 WECHAT_AI_API_KEY")
        sys.exit(1)

    # 初始化AI引擎
    print("\n[0] 初始化AI引擎...")
    try:
        ai_engine = AIReplyEngine(API_URL, API_KEY, MODEL)
        print("  AI引擎初始化成功")
    except Exception as e:
        logger.error(f"AI引擎初始化失败: {e}")
        sys.exit(1)

    wechat = WeChatAutomation()

    try:
        print("\n[1] 连接微信...")
        if not wechat.connect():
            logger.error("连接微信失败")
            sys.exit(1)
        print("  连接成功")

        print("\n[2] 开始持续检测未读消息...")
        check_count = 0

        while True:
            check_count += 1
            print(f"\n--- 第 {check_count} 次检测 ---")

            processed = load_processed_sessions()
            unread_sessions = detect_unread_sessions(wechat)
            # 用 full_name（含消息预览）做去重，同联系人发新消息仍能检测到
            new_unread = [s for s in unread_sessions if _norm_key(s.get("full_name", s["name"])) not in processed]

            if not new_unread:
                print("  没有新未读消息，等待 5 秒...")
                time.sleep(5)
                continue

            print(f"\n  发现 {len(new_unread)} 个新未读会话")

            for i, session in enumerate(new_unread, 1):
                print(f"\n[处理 {i}/{len(new_unread)}] {session['name']}")

                try:
                    session["element"].Click()
                except Exception as e:
                    logger.error(f"  点击会话失败: {e}")
                    continue

                time.sleep(0.5)

                if read_and_reply(wechat, ai_engine, session["name"]):
                    save_processed_session(session.get("full_name", session["name"]))
                    print(f"  处理完成")

                time.sleep(0.3)

            print(f"\n  处理完成，等待 5 秒...")
            time.sleep(5)

    except KeyboardInterrupt:
        print("\n\n用户退出")
    finally:
        wechat.cleanup()


if __name__ == "__main__":
    if len(sys.argv) > 1:
        if sys.argv[1] == "--reset":
            clear_processed_sessions()
            print("已清除所有处理记录")
        elif sys.argv[1] in ("--clear-ai", "--clear-all"):
            try:
                ai_engine = AIReplyEngine(API_URL, API_KEY, MODEL)
                ai_engine.clear_all()
                print("已清除所有AI对话历史")
            except Exception as e:
                logger.error(f"清除AI对话历史失败: {e}")
        elif sys.argv[1] == "--clear":
            try:
                ai_engine = AIReplyEngine(API_URL, API_KEY, MODEL)
                ai_engine.clear_conversation(sys.argv[2])
                print(f"已清除 {sys.argv[2]} 的对话历史")
            except Exception as e:
                logger.error(f"清除对话历史失败: {e}")
    else:
        main()
