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


def generate_mc_question(topic: str | None = None) -> AIQuestionOut:
    """生成 1 道选择题。topic 指定知识点主题，否则随机。失败重试 1 次后抛 ValueError。"""
    base_topic = (topic or "").strip() or random.choice(TOPIC_POOL)
    last_err = None
    for attempt in range(2):
        try:
            data = _raw_generate(base_topic if attempt == 0 else (topic or random.choice(TOPIC_POOL)))
            return AIQuestionOut(**data)
        except (json.JSONDecodeError, ValidationError) as e:
            last_err = e
            continue
        except Exception as e:
            last_err = e
            break
    raise ValueError(f"AI 生成的题目格式校验失败：{last_err}")


# —— 知识点掌握度 AI 判定（严格模式）——

_REQUIREMENT_LABEL = {"know": "了解（知道概念）", "understand": "理解（能看懂代码）",
                      "master": "掌握（能独立运用、稳定做对）"}

_MASTERY_SYSTEM = """你是一位要求严格的 Python 老师，根据学生在某个知识点上的全部答题记录，判定他是否真正掌握。

判定标准（严格）：
- mastered：所有题目（重做后也可）都能答对，且没有表现出概念混淆；至少有 2 道不同的题做对，或该知识点只有 1 道题且一次答对。
- partial：答对一部分，或最初答错但经重做后才对，或存在明显但不严重的概念模糊。
- none：没有答对记录，或多次答错、存在根本性概念错误。

注意：
1. 考试答错但练习重做后答对，说明需要提示才能对，最高只能评 partial（除非该知识点只有 1 道题且其余表现无异常）。
2. 仅凭 1 道题答对不轻易给 mastered（题目仅 1 道的情况除外）。
3. 只输出严格 JSON，无任何额外文字：
{{"verdict": "mastered|partial|none", "reason": "不超过40字的中文评价"}}"""


def _fallback_judgement(records: list[dict]) -> dict:
    """API 不可用时的严格规则降级：最好成绩全对且≥2题→mastered；有对题→partial。"""
    if not records:
        return {"verdict": "none", "reason": "还没有答题记录", "source": "fallback"}
    n = len(records)
    n_best_ok = sum(1 for r in records if r["best_correct"])
    if n == 1 and n_best_ok == 1 and records[0]["first_correct"]:
        return {"verdict": "mastered", "reason": "唯一一题一次答对", "source": "fallback"}
    if n_best_ok >= 2 and n_best_ok == n:
        return {"verdict": "mastered", "reason": f"全部 {n} 题最好成绩均正确", "source": "fallback"}
    if n_best_ok >= 1:
        return {"verdict": "partial", "reason": f"答对 {n_best_ok}/{n} 题，尚不稳定", "source": "fallback"}
    return {"verdict": "none", "reason": f"{n} 道题均未答对", "source": "fallback"}


def judge_mastery(kp_name: str, requirement: str, records: list[dict],
                  n_total: int | None = None) -> dict:
    """判定学生对某知识点的掌握度。

    records: [{stem, picked, correct_text, first_correct, best_correct, attempts}]
    n_total: 该知识点题池总题数（含未作答）。
    返回 {verdict, reason, source}；任何异常都降级为规则判定，保证调用方永远可用。
    """
    if not settings.DEEPSEEK_API_KEY:
        return _fallback_judgement(records)
    lines = [f"知识点：{kp_name}", f"教学要求：{_REQUIREMENT_LABEL.get(requirement, requirement)}"]
    if n_total is not None and n_total > len(records):
        lines.append(f"该知识点题库共 {n_total} 题，学生只作答了 {len(records)} 题；"
                     f"未作答超过 {max(0, n_total - len(records) - 1)} 题以上时，不允许判 mastered。")
    lines.append("答题记录：")
    for i, r in enumerate(records, 1):
        lines.append(
            f"{i}. 题目：{r['stem']}\n"
            f"   学生选择：{r['picked']}；正确答案：{r['correct_text']}\n"
            f"   首次是否答对：{'是' if r['first_correct'] else '否'}；"
            f"重做后是否答对：{'是' if r['best_correct'] else '否'}；共作答 {r['attempts']} 次")
    user_prompt = "\n".join(lines) + "\n\n请严格判定并输出 JSON。"
    try:
        client = _client()
        resp = client.chat.completions.create(
            model=settings.DEEPSEEK_MODEL,
            messages=[{"role": "user", "content": _MASTERY_SYSTEM + "\n\n" + user_prompt}],
            response_format={"type": "json_object"},
            temperature=0.2,
            timeout=20,
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        verdict = str(data.get("verdict", "")).strip().lower()
        if verdict not in ("mastered", "partial", "none"):
            raise ValueError("verdict 非法")
        return {"verdict": verdict,
                "reason": str(data.get("reason", "")).strip()[:120],
                "source": "ai"}
    except Exception:
        return _fallback_judgement(records)


# —— 错题本 AI 学情总结（面向大二本科生，专业口吻）——

_WEAKNESS_SYSTEM = """你是高校《Python 程序设计》课程的助教，学生是大二本科生。请基于其错题本中仍未攻克的题目，做一份专业、客观的学情诊断报告，如同批改反馈。

诊断维度（对每个维度给出 级别：薄弱/一般/良好，以及一句话依据）：
1. Python 基本概念：动态类型与对象引用、可变性、作用域规则、序列切片、函数参数传递语义、内置函数行为等核心语言语义。
2. 计算思维：代码静态追踪与变量状态推导、循环/递归过程模拟、边界条件分析、问题分解与抽象能力、算法设计思路。

写作要求：
- 必须紧扣错题原文：若题目包含函数或代码，必须引用其中的关键代码行（原样摘录），逐点指出学生在该处的理解偏差；不得空泛评价。
- 涉及语言语义时给出准确的术语（如"实参按对象引用传递""切片产生新列表"）。
- 口吻专业简练，像实验报告的评阅意见；禁止"加油""太厉害了"之类口语化鼓励。
- advice 中如需代码示例，示例行以">>> "开头、单独成行（Python REPL 风格）。

只输出严格 JSON，不要任何额外文字：
{{
  "summary": "总体评估，2句以内：给出能力画像并指出核心短板",
  "dimensions": [
    {{"name": "Python基本概念", "level": "薄弱|一般|良好", "comment": "一句话依据，引用错题中的具体语义点"}},
    {{"name": "计算思维", "level": "薄弱|一般|良好", "comment": "一句话依据"}}
  ],
  "problems": "问题定位，逐条：先摘录错题关键代码或概念，再指出错误原因与涉及的语言语义；每条一行",
  "advice": "2-3条可执行的改进建议；需要示例时用 '>>> ' 开头的代码行"
}}
所有文本中文。"""


def _fallback_report(wrong: list[dict]) -> dict:
    """无 API 时的规则版学情总结（专业口吻）。"""
    kp_count: dict[str, int] = {}
    for w in wrong:
        for name in w["kp_names"]:
            kp_count[name] = kp_count.get(name, 0) + 1
    hot = sorted(kp_count, key=kp_count.get, reverse=True)[:2]
    n = len(wrong)
    if hot:
        problems = (f"错题集中于「{'、'.join(hot)}」（{n} 道未攻克），"
                    "相关语言语义尚未建立稳定理解，建议对照教材重读对应章节后再订正。")
        advice = (f"1. 重读「{hot[0]}」对应章节，重点区分易混语义（如引用与拷贝、可变与不可变）；\n"
                  "2. 订正时先在纸上手动推导代码执行过程，再运行验证；\n"
                  ">>> # 示例：用 REPL 验证切片语义\n"
                  ">>> lst = [1, 2, 3]; lst[1:]  # 思考：返回新列表还是视图？")
    else:
        problems = f"当前有 {n} 道错题未归类到知识点，无法定位薄弱章节，请先为题目补充知识点标签。"
        advice = "1. 逐题订正并标注出错原因（概念不清/推导错误/粗心）；2. 三天后重做一遍检验保持效果。"
    labels = ["Python基本概念:待巩固", "计算思维:一般"]
    if hot:
        labels.append(f"薄弱章节:{hot[0]}"[:24])
    return {"summary": f"未攻克错题 {n} 道，主要分布在「{'、'.join(hot) if hot else '未分类知识点'}」，建议按章节专项订正。",
            "problems": problems, "advice": advice, "labels": labels,
            "source": "fallback"}


def analyze_weakness(wrong: list[dict], n_resolved: int) -> dict:
    """分析未攻克错题，返回学情报告 dict；任何异常都降级规则版。

    labels 统一为 "名称:级别" 列表（级别：薄弱/一般/良好/待巩固）。
    """
    if not wrong:
        return {"summary": "", "problems": "", "advice": "", "labels": [],
                "source": "none"}
    if not settings.DEEPSEEK_API_KEY:
        return _fallback_report(wrong)
    lines = [f"背景：学生已攻克错题 {n_resolved} 道，仍有 {len(wrong)} 道未攻克。",
             "未攻克错题（含完整题干，代码以原文为准）："]
    for i, w in enumerate(wrong, 1):
        kps = "、".join(w["kp_names"]) or "未分类"
        entry = (f"{i}.【{kps}】{w['stem']}\n"
                 f"   学生选择：{w['picked']}；正确答案：{w['correct_text']}")
        if w.get("explanation"):
            entry += f"\n   题目解析：{w['explanation']}"
        lines.append(entry)
    user_prompt = "\n".join(lines) + "\n\n请输出 JSON。"
    try:
        client = _client()
        resp = client.chat.completions.create(
            model=settings.DEEPSEEK_MODEL,
            messages=[{"role": "user",
                       "content": _WEAKNESS_SYSTEM + "\n\n" + user_prompt}],
            response_format={"type": "json_object"},
            temperature=0.3,
            timeout=30,
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        labels = []
        dim_lines = []
        for d in data.get("dimensions", []):
            name = str(d.get("name", "")).strip()[:12]
            level = str(d.get("level", "")).strip()[:6]
            comment = str(d.get("comment", "")).strip()[:120]
            if name and level in ("薄弱", "一般", "良好", "待巩固"):
                labels.append(f"{name}:{level}")
            if name and comment:
                dim_lines.append(f"【{name}·{level or '—'}】{comment}")
        if not labels:
            labels = [str(x).strip()[:24] for x in data.get("labels", []) if str(x).strip()][:4]
        problems_body = str(data.get("problems", "")).strip()[:500]
        problems = ("\n".join(dim_lines) + "\n" + problems_body).strip()[:650] \
            if dim_lines else problems_body
        return {"summary": str(data.get("summary", "")).strip()[:300],
                "problems": problems,
                "advice": str(data.get("advice", "")).strip()[:500],
                "labels": labels[:4], "source": "ai"}
    except Exception:
        return _fallback_report(wrong)


def generate_tf_question(topic: str | None = None) -> dict:
    """生成 1 道判断题。topic 指定知识点主题，否则随机。返回 {stem, correct_bool, explanation}。"""
    topic = (topic or "").strip() or random.choice(TOPIC_POOL)
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
