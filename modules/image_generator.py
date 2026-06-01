"""
AI 生图模块 — 调用生图 API 根据文本描述生成图片
生成后自动保存到 ai_workspace/images/，供 send_image 工具发送
中文提示词会先通过 AI 翻译为英文再请求 API，以获得更好的生图效果
"""
import os
import re
import time
import hashlib
import requests
from urllib.parse import urlencode

# openai 可选导入（用于 AI 翻译）
try:
    import openai
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False


class ImageGenerator:
    """AI 图片生成器，调用外部生图 API，中文提示词通过 AI 自动翻译为英文"""

    API_URL = "https://shengtu.superman.qzz.io/generate"

    # 支持的比例 → 像素尺寸（供描述用）
    ASPECT_INFO = {
        "1:1":   "1024x1024 (方形)",
        "16:9":  "1152x648 (横版/桌面壁纸)",
        "9:16":  "648x1152 (竖版/手机壁纸)",
        "4:3":   "1024x768 (传统照片)",
        "3:4":   "768x1024 (竖版照片)",
        "3:2":   "1024x683 (风景照)",
        "2:3":   "683x1024 (人像照)",
    }

    # 中文检测正则
    _CHINESE_RE = re.compile(r'[\u4e00-\u9fff\u3400-\u4dbf]')

    # 翻译系统提示词
    _TRANSLATE_SYSTEM_PROMPT = (
        "You are a professional translator. "
        "Your task is to translate Chinese image prompts into English for an AI image generator. "
        "Rules:\n"
        "1. Output ONLY the English translation, no explanations, no quotes, no markdown.\n"
        "2. Make the prompt vivid, descriptive, and suitable for image generation.\n"
        "3. Preserve all details: style, colors, composition, lighting, mood, artist references.\n"
        "4. Keep it concise but expressive — aim for 1-3 sentences."
    )

    def __init__(self, api_key: str = "", save_dir: str = None,
                 ai_api_url: str = "", ai_api_key: str = "", model: str = ""):
        """
        :param api_key: 生图 API 密钥
        :param save_dir: 图片保存目录，默认 ai_workspace/images/
        :param ai_api_url: AI 翻译接口地址（OpenAI 兼容格式）
        :param ai_api_key: AI 翻译接口密钥
        :param model: AI 翻译模型名称
        """
        self.api_key = api_key
        self.save_dir = save_dir or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'ai_workspace', 'images'
        )
        os.makedirs(self.save_dir, exist_ok=True)

        # AI 翻译客户端
        self._translator = None
        self._translate_model = model
        if OPENAI_AVAILABLE and ai_api_url and ai_api_key:
            try:
                self._translator = openai.OpenAI(
                    api_key=ai_api_key,
                    base_url=ai_api_url.rstrip('/'),
                    timeout=30.0
                )
            except Exception:
                pass

    @classmethod
    def _contains_chinese(cls, text: str) -> bool:
        """检测文本是否包含中文字符"""
        return bool(cls._CHINESE_RE.search(text))

    def _translate_to_english(self, text: str) -> str:
        """
        使用项目 AI 将中文提示词翻译为英文

        :param text: 中文文本
        :return: 英文翻译结果，失败时返回原文
        """
        if not self._translator or not self._translate_model:
            print("    [WARN] AI 翻译未配置，使用原文提示词")
            return text

        try:
            # thinking 模型会消耗大量推理 tokens，给足配额确保正文不被截断
            # 同时尝试禁用思考模式以加速简单翻译
            kwargs = {
                "model": self._translate_model,
                "messages": [
                    {"role": "system", "content": self._TRANSLATE_SYSTEM_PROMPT},
                    {"role": "user", "content": text}
                ],
                "temperature": 0.3,
                "max_tokens": 4096,
            }
            # 禁用 thinking 模式（stepfun 等支持该参数的模型会跳过推理直接输出）
            try:
                kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
            except Exception:
                pass

            resp = self._translator.chat.completions.create(**kwargs)

            msg = resp.choices[0].message
            # 优先取 content，为空时尝试 thinking 模型的 reasoning_content
            content = msg.content
            if content is None:
                reasoning = getattr(msg, 'reasoning_content', None)
                if reasoning is None and hasattr(msg, 'model_extra'):
                    reasoning = msg.model_extra.get('reasoning_content') or msg.model_extra.get('reasoning')
                if reasoning:
                    # reasoning 末尾通常包含真正的翻译结果，取最后一段
                    content = reasoning.strip().split('\n')[-1].strip()
                    if content and len(content) < 500:
                        print(f"    [TR] AI翻译(从推理中提取): {content[:100]}...")
                        return content
                print("    [WARN] AI 翻译返回空内容，使用原文提示词")
                return text

            translated = content.strip()
            if translated:
                print(f"    [TR] AI翻译: {translated[:100]}...")
                return translated
        except Exception as e:
            print(f"    [WARN] AI 翻译失败 ({e})，使用原文提示词")
        return text

    def generate(self, prompt: str, aspect: str = "1:1") -> str:
        """
        根据文本描述生成图片并保存到本地

        :param prompt: 图片描述，支持中英文（中文会自动翻译为英文以获得更好效果）
        :param aspect: 图片比例 (1:1/16:9/9:16/4:3/3:4/3:2/2:3)，默认 1:1
        :return: 结果描述字符串，包含保存的文件名和路径
        """
        # 校验比例
        if aspect not in self.ASPECT_INFO:
            supported = ", ".join(self.ASPECT_INFO.keys())
            return f"不支持的比例 '{aspect}'，可选: {supported}"

        # 中文提示词通过 AI 翻译为英文（生图 API 对英文响应更好）
        original_prompt = prompt
        if self._contains_chinese(prompt):
            print(f"    [TR] 检测到中文提示词，AI 翻译中...")
            prompt = self._translate_to_english(prompt)

        params = {"prompt": prompt, "aspect": aspect}

        try:
            resp = requests.get(
                self.API_URL,
                params=params,
                headers={"X-API-Key": self.api_key},
                timeout=120.0
            )
            resp.raise_for_status()

            # 检查返回是否为图片
            content_type = resp.headers.get("Content-Type", "")
            if "image" not in content_type:
                snippet = resp.text[:300]
                return f"生图失败：API 未返回图片 (Content-Type: {content_type})，返回内容: {snippet}"

            # 用 prompt 哈希 + 时间戳作为文件名
            safe_hash = hashlib.md5(prompt.encode("utf-8")).hexdigest()[:12]
            timestamp = int(time.time())
            filename = f"ai_{safe_hash}_{timestamp}.png"
            filepath = os.path.join(self.save_dir, filename)

            with open(filepath, "wb") as f:
                f.write(resp.content)

            size_kb = len(resp.content) / 1024
            aspect_desc = self.ASPECT_INFO.get(aspect, aspect)

            result = (
                f"图片已生成 [OK]\n"
                f"文件名: {filename}\n"
                f"比例: {aspect} ({aspect_desc})\n"
                f"大小: {size_kb:.1f} KB\n"
                f"英文提示词: {prompt[:100]}\n"
            )
            if original_prompt != prompt:
                result += f"中文原描述: {original_prompt[:80]}\n"
            result += f"如需发送给用户，请调用 send_image(filename=\"{filename}\")"
            return result

        except requests.ConnectionError:
            return f"生图失败：无法连接到生图服务器 {self.API_URL}"
        except requests.Timeout:
            return "生图失败：请求超时（120秒），请换一个更简单的描述重试"
        except requests.HTTPError as e:
            body = ""
            try:
                body = e.response.text[:200]
            except Exception:
                pass
            return f"生图失败：HTTP {e.response.status_code} - {body}" if e.response is not None else f"生图失败: {e}"
        except Exception as e:
            return f"生图异常: {type(e).__name__}: {e}"
