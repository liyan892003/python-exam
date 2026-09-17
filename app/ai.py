"""DeepSeek AI 出题（OpenAI 兼容接口）。

密钥从环境变量 DEEPSEEK_API_KEY 注入，绝不硬编码。
返回严格校验后的题目，失败时抛异常供上层回退。
"""
import json
import random

from openai import OpenAI
from pydantic import ValidationError

from .config import get_settings
from .schemas import AIQuestionOut

settings = get_settings()

_SYSTEM_PROMPT = """你是一位资深 Python 教师，负责为初学者出练习题。
请围绕以下 Python 知识点出 1 道选择题（4 个选项，恰好 1 个正确）：
{topic}

要求：
1. 题干清晰、贴近真实编程场景，难度适中。
2. 干扰项要有迷惑性但明确错误，不能有歧义。
3. 必须输出严格 JSON，不要输出任何额外文字、不要 markdown 代码块标记。
4. JSON 结构如下：
{{
  "stem": "题干文本",
  "choices": [
    {{"text": "选项A", "is_correct": true}},
    {{"text": "选项B", "is_correct": false}},
    {{"text": "选项C", "is_correct": false}},
    {{"text": "选项D", "is_correct": false}}
  ],
  "explanation": "为什么正确答案是这个，简明说明"
}}
5. 恰好 4 个选项，且恰好 1 个 is_correct 为 true。
6. 所有文本用中文。
"""

# 题目知识点池（随机抽一个让题目更多样）
TOPIC_POOL = [
    "变量与基本数据类型（int/str/float/bool）",
    "字符串操作与格式化（f-string、切片、常用方法）",
    "列表 list 的增删改查与切片",
    "字典 dict 的键值操作",
    "元组 tuple 与集合 set 的特性",
    "if/elif/else 条件判断",
    "for 循环与 while 循环",
    "range() 与 enumerate() 的使用",
    "函数定义、参数与返回值",
    "默认参数、关键字参数、*args/**kwargs",
    "列表推导式与字典推导式",
    "文件读写（open、with 语句）",
    "异常处理 try/except/finally",
    "面向对象：类与对象、__init__",
    "继承与方法重写",
    "模块导入 import 与 from...import",
    "常用内置函数 map/filter/zip/sorted",
    "Python 作用域 LEGB 规则",
]


def _client() -> OpenAI:
    """构造 DeepSeek（OpenAI 兼容）客户端。"""
    if not settings.DEEPSEEK_API_KEY:
        raise RuntimeError("未配置 DEEPSEEK_API_KEY，无法 AI 出题。请在 .env 中设置。")
    return OpenAI(api_key=settings.DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")


def _raw_generate(topic: str) -> dict:
    """调用 DeepSeek，返回原始 JSON dict。"""
    client = _client()
    resp = client.chat.completions.create(
        model=settings.DEEPSEEK_MODEL,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT.format(topic=topic)},
            {"role": "user", "content": "请生成 1 道 Python 选择题，严格按要求输出 JSON。"},
        ],
        response_format={"type": "json_object"},
        temperature=0.9,
    )
    content = resp.choices[0].message.content or "{}"
    return json.loads(content)


def generate_mc_question() -> AIQuestionOut:
    """生成 1 道选择题。失败重试 1 次后抛 ValueError。"""
    topic = random.choice(TOPIC_POOL)
    last_err = None
    for attempt in range(2):
        try:
            data = _raw_generate(topic if attempt == 0 else random.choice(TOPIC_POOL))
            return AIQuestionOut(**data)
        except (json.JSONDecodeError, ValidationError) as e:
            last_err = e
            continue
        except Exception as e:
            last_err = e
            break
    raise ValueError(f"AI 生成的题目格式校验失败：{last_err}")


def generate_tf_question() -> dict:
    """生成 1 道判断题。返回 {stem, correct_bool, explanation}。"""
    topic = random.choice(TOPIC_POOL)
    prompt = f"""你是资深 Python 教师。围绕"{topic}"出 1 道判断题。
要求：题干是一个陈述句，学生需判断对错。输出严格 JSON，不要额外文字：
{{
  "stem": "判断题题干陈述",
  "is_correct": true,
  "explanation": "简明解释"
}}
is_correct 表示该陈述本身是否正确（即正确答案是"对"还是"错"）。用中文。"""
    client = _client()
    last_err = None
    for attempt in range(2):
        try:
            resp = client.chat.completions.create(
                model=settings.DEEPSEEK_MODEL,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0.9,
            )
            data = json.loads(resp.choices[0].message.content or "{}")
            if "stem" not in data or "is_correct" not in data:
                raise ValueError("缺少字段 stem/is_correct")
            return {
                "stem": str(data["stem"]),
                "is_correct": bool(data["is_correct"]),
                "explanation": str(data.get("explanation", "")),
            }
        except (json.JSONDecodeError, ValueError, KeyError) as e:
            last_err = e
            continue
        except Exception as e:
            last_err = e
            break
    raise ValueError(f"AI 生成判断题失败：{last_err}")
