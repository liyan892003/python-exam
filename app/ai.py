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


# —— 分组作业 AI 评分 ——
# 评分方案（总分 100）：
#   问题解决度 50（代码是否真正解决所选问题、结果是否正确、边界情况）
#   简洁性     25（无明显冗余、结构清晰、函数/循环用得恰当）
#   课程范围内 15（只允许 numbers/strings/lists、循环、if/else、自定义函数、文件、matplotlib；
#                 出现 dict/set/tuple 等未学内容只做提醒，最多扣 5 分）
#   代码规范   10（命名、注释、缩进、可运行）

SYLLABUS_SCOPE = "numbers、字符串 string、列表 list、循环 loops(for/while)、if/else、自定义函数 def、文件读写 open、matplotlib 绘图"
OUT_OF_SCOPE = "字典 dict、集合 set、元组 tuple（以及未学的 class/其他第三方库）"

_GROUP_SCORE_SYSTEM = f"""你是高校《Python 程序设计》课程的评阅老师，学生是大二本科生。学生提交了「要解决的问题」和「代码实现」，请把两者一起评阅。

本课程已学范围（只允许用这些）：{SYLLABUS_SCOPE}。
尚未学习：{OUT_OF_SCOPE}。若代码用了未学内容，只需在 out_of_syllabus 中温和提醒（如「本题可用列表实现，无需使用字典」），不要因此大幅扣分（最多扣 5 分）。

评分方案（总分 100）：
1. 问题解决度 50 分：代码是否真正解决了学生所写的问题？运行结果是否正确？是否考虑常见边界情况？
2. 简洁性 25 分：是否存在冗余/重复代码？结构是否清晰，函数与循环是否用得恰当？
3. 课程范围内实现 15 分：是否只使用已学语法；超纲只提醒、最多扣 5 分。
4. 代码规范 10 分：命名、注释、缩进、可直接运行。

要求：
- 只输出严格 JSON，不要任何额外文字：
{{"score": 85, "solved": "是", "comment": "总体评价：先说是否解决了问题，再指出简洁性与规范问题（<=120字）", "suggestions": "2 条可执行的改进建议（<=120字）", "out_of_syllabus": "无"}}
- solved 只能是 "是" / "部分" / "否"；out_of_syllabus 没有超纲内容时写 "无"，有则一句话说明替代写法。"""


def _detect_out_of_syllabus(text: str) -> str:
    """规则检测未学语法（用于无 API 时的提醒）。"""
    import re
    hits = []
    if re.search(r"\bdict\s*\(|\bcollections\b|=\s*\{|return\s*\{", text):
        hits.append("字典")
    if re.search(r"\bset\s*\(|\bfrozenset\b", text):
        hits.append("集合")
    if re.search(r"\btuple\s*\(|return\s+[^(\n]*,", text):
        hits.append("元组")
    if re.search(r"^\s*class\s+\w+", text, re.M):
        hits.append("自定义类")
    if not hits:
        return "无"
    return f"检测到可能使用了未学内容（{'、'.join(hits)}），建议改用列表/循环/函数实现；仅为提醒，不作为主要扣分项。"


def _fallback_group_score(problem: str, content: str) -> dict:
    """无 API / 调用失败时的规则粗评（保证上传流程不中断）。"""
    text = content or ""
    n_lines = len([ln for ln in text.splitlines() if ln.strip()])
    score = 55
    hits = []
    if "def " in text:
        score += 8; hits.append("使用函数封装")
    if any(k in text for k in ("for ", "while ")):
        score += 6; hits.append("使用循环")
    if any(k in text for k in ("if ", "elif ", "else")):
        score += 4; hits.append("有条件判断")
    if "open(" in text:
        score += 3; hits.append("使用文件读写")
    if "matplotlib" in text or "plt." in text:
        score += 4; hits.append("使用 matplotlib")
    if "#" in text:
        score += 3; hits.append("有注释")
    if n_lines >= 15:
        score += 8
    elif n_lines >= 5:
        score += 4
    if any(k in text for k in ("print(", "input(")):
        score += 3
    out = _detect_out_of_syllabus(text)
    if out != "无":
        score = max(score - 5, 50)
    score = min(score, 95)
    comment = ("未接入 AI（DEEPSEEK_API_KEY 未配置或调用失败），按代码特征粗评："
               + ("；".join(hits) if hits else "内容较短，暂未识别到结构化代码")
               + f"。共 {n_lines} 行有效代码，是否解决「{(problem or '未填写的问题')[:60]}」需老师人工确认。")
    suggestions = "1. 按所写问题逐条自测并附运行结果；2. 补充关键步骤注释并统一命名规范。"
    return {"score": score, "comment": comment[:300], "suggestions": suggestions,
            "solved": "未判定", "out_of_syllabus": out, "source": "fallback"}


def score_group_submission(title: str, description: str, team_name: str,
                           members: str, filename: str, note: str,
                           problem: str, content: str) -> dict:
    """根据「所解决的问题 + 代码」给小组打分。返回
    {score, comment, suggestions, solved, out_of_syllabus, source}；异常降级规则评分。"""
    content = (content or "")[:6000]
    problem = (problem or "").strip()[:600]
    if not settings.DEEPSEEK_API_KEY:
        return _fallback_group_score(problem, content)
    user_prompt = (f"作业任务：{title}\n任务说明：{description or '（无）'}\n"
                   f"小组名称：{team_name}\n组员：{members or '（未知）'}\n"
                   f"提交文件：{filename or '（直接粘贴文本）'}\n"
                   f"小组说明：{note or '（无）'}\n\n"
                   f"【要解决的问题】\n{problem or '（未填写）'}\n\n"
                   f"【代码实现】\n{content}\n\n请按评分方案评阅并输出 JSON。")
    try:
        client = _client()
        resp = client.chat.completions.create(
            model=settings.DEEPSEEK_MODEL,
            messages=[{"role": "user",
                       "content": _GROUP_SCORE_SYSTEM + "\n\n" + user_prompt}],
            response_format={"type": "json_object"},
            temperature=0.2,
            timeout=40,
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        score = int(data.get("score"))
        if not (0 <= score <= 100):
            raise ValueError("score 越界")
        solved = str(data.get("solved", "")).strip()
        if solved not in ("是", "部分", "否"):
            solved = "未判定"
        out = str(data.get("out_of_syllabus", "无")).strip() or "无"
        return {"score": score,
                "comment": str(data.get("comment", "")).strip()[:300],
                "suggestions": str(data.get("suggestions", "")).strip()[:300],
                "solved": solved,
                "out_of_syllabus": out[:300],
                "source": "ai"}
    except Exception:
        return _fallback_group_score(problem, content)


# —— 在线运行（浏览器无法加载 Pyodide 时，用 AI 推测运行结果） ——

_RUN_SYSTEM = """你是一个严谨的 Python 3 解释器，用来推测一段初学者代码的执行结果。
请按 Python 3 的真实语义逐行推演，并遵守：
1. output 只写程序真正会打印出来的内容（print 的结果，一行一行），不要写代码、解释或 Markdown。
2. 如果程序会抛异常，ok=false，error 写错误类型和最后一行信息，例如
   "TypeError: cannot unpack non-iterable NoneType object"。
3. 如果程序会死循环，ok=false，error 写 "运行超时（可能是死循环）"。
4. 如果代码调用了 input()，就按用户提供的「运行输入」依次读取，输入用完后继续执行会报
   EOFError: 输入已用完；如果用户没有提供输入，error 写 "EOFError: 输入已用完：请在「运行输入」里每行填一个值"。
5. 如果代码里没有任何输出也没有报错，ok=true，output 留空。
6. 只输出 JSON：{"ok": true/false, "output": "...", "error": "..."}
"""


def simulate_run(code: str, inputs: list[str] | None = None) -> dict:
    """让 DeepSeek 充当 Python 解释器，推测运行结果。返回 {ok, output, error, source}。"""
    code = (code or "")[:8000]
    inputs = [str(x)[:200] for x in (inputs or [])][:50]
    if not settings.DEEPSEEK_API_KEY:
        return {"ok": False, "output": "",
                "error": "当前没有配置 DEEPSEEK_API_KEY，无法使用 AI 模拟运行。",
                "source": "none"}
    try:
        client = _client()
        inputs_text = ("\n".join(inputs) if inputs else "（用户没有提供任何输入）")
        resp = client.chat.completions.create(
            model=settings.DEEPSEEK_MODEL,
            messages=[
                {"role": "system", "content": _RUN_SYSTEM},
                {"role": "user", "content":
                    "运行输入（input() 依次读取这些值，每行一个）：\n" + inputs_text +
                    "\n\n请推测下面代码的运行结果：\n```python\n" + code + "\n```"},
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
            timeout=45,
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        return {"ok": bool(data.get("ok")),
                "output": str(data.get("output", ""))[:4000],
                "error": str(data.get("error", ""))[:1000],
                "source": "ai"}
    except Exception as exc:  # 网络/Key/解析失败都给出可读提示
        return {"ok": False, "output": "",
                "error": f"AI 模拟运行失败：{exc}", "source": "error"}


# —— 代码原创性（AI 率）检测 + 超纲答辩 ——

_ORIGIN_SYSTEM = f"""你是高校《Python 程序设计》课程的助教。学生刚提交了实践项目的代码，
需要你判断这份代码有多可能是「直接由 AI 生成、学生只做了很少改动」，并找出超出课堂已学范围的写法。

本课程已学范围：{SYLLABUS_SCOPE}
尚未学习：{OUT_OF_SCOPE}

判断 ai_rate（0-100，只是"疑似程度"，不是定论）时综合看：
1. 风格：命名、注释语言、缩进、变量名抽象程度是否像初学者；
2. 超纲：是否出现未学语法（字典/集合/元组/自定义类/lambda/推导式/try-except/类型注解等）；
3. 过于完整：docstring、`if __name__ == "__main__"`、异常处理、类型注解、防御性代码等初学者一般不写的痕迹；
4. 与设计图是否一致（若提供了函数名列表，代码若另起炉灶也是信号）。

依据要求：
- signals 必须是代码里的具体现象，能让人对照核查，不要空泛。
- out_of_scope 里 snippet 必须是代码原文片段（可截断），concept 写语法名，why 一句话说明为什么超纲。

答辩题（questions）要求：
- 只在 ai_rate >= 60 或有超纲写法时出题，共 1~3 道；否则给空数组。
- 针对具体代码行提问，让学生用自己的话讲清"这行在干什么、不写会怎样、改一下会怎样"，例如
  「第 12 行 result.append(x) 里的 append 做了什么？如果删掉这一行程序会怎样？」
- 禁止问"这是不是你自己写的""你用了什么AI"之类无法判断的问题。
- 题目要能区分真懂和背答案：必须指向代码里的具体变量/数据变化。

只输出严格 JSON，不要任何额外文字：
{{"ai_rate": 72, "summary": "一句话结论（<=50字）",
  "signals": ["具体现象1", "具体现象2"],
  "out_of_scope": [{{"snippet": "代码片段", "concept": "字典", "why": "课程还没学"}}],
  "questions": [{{"snippet": "第12行 result.append(x)", "question": "..."}}]}}"""


def _fallback_origin_check(code: str) -> dict:
    """无 API 时的规则检测：只看可核实的痕迹，不下结论。"""
    import re
    text = code or ""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    signals: list[str] = []
    score = 25
    out_of_scope: list[dict] = []

    def hit(pattern: str, concept: str, why: str, weight: int, flags=0):
        nonlocal score
        m = re.search(pattern, text, flags)
        if m:
            snippet = m.group(0).strip()[:80]
            out_of_scope.append({"snippet": snippet, "concept": concept, "why": why})
            score += weight

    hit(r"\{[^{}\n]*:[^{}\n]*\}", "字典", "课程还没学字典这种键值结构", 12)
    hit(r"\bset\s*\(|\{[^:{}\n]+\}", "集合", "课程还没学集合", 10)
    hit(r"\btuple\s*\(|\blambda\b", "lambda/tuple", "课程还没学 lambda 与元组", 8)
    hit(r"^\s*class\s+\w+", "自定义类", "课程还没学面向对象的类", 15, re.M)
    hit(r"\btry\s*:", "try-except", "初学阶段通常不写异常处理", 10)
    hit(r"if\s+__name__\s*==", "main 保护", "初学阶段通常不写 main 保护", 8)
    hit(r"\[[^\]\n]*\bfor\b[^\]\n]*\]", "列表推导式", "课程还没学推导式", 10)
    hit(r"def\s+\w+\([^)]*:\s*\w+", "类型注解", "初学阶段通常不写类型注解", 8)

    if any(ln.strip().startswith('"""') or ln.strip().startswith("'''") for ln in lines):
        signals.append("使用了文档字符串 docstring，初学者较少写")
        score += 8
    if len(lines) > 80:
        signals.append(f"代码共 {len(lines)} 行，明显超出课堂练习篇幅")
        score += 10
    n_comment = sum(1 for ln in lines if ln.strip().startswith("#"))
    if lines and n_comment / len(lines) > 0.25:
        signals.append(f"注释占比 {n_comment}/{len(lines)}，且格式工整，像生成结果")
        score += 6
    if not signals and not out_of_scope:
        signals.append("未发现明显超纲或模板化痕迹")

    score = max(5, min(score, 90))
    questions = [
        {"snippet": item["snippet"],
         "question": f"你在代码里用了「{item['concept']}」（{item['snippet']}）。"
                     "请用你自己的话说明：它在这一步存了什么/做了什么？如果换成列表或循环会怎样？"}
        for item in out_of_scope[:2]
    ]
    if not questions and score >= 60:
        questions = [{"snippet": "main() 里的调用顺序",
                      "question": "请按顺序讲讲 main() 里每一行在做什么，变量在每一步分别变成了什么？"}]
    return {"ai_rate": score,
            "summary": "未接入 AI，按代码特征粗略估计（仅供参考），建议老师人工抽查。",
            "signals": signals, "out_of_scope": out_of_scope,
            "questions": questions, "source": "fallback"}


def _as_text_list(value, limit: int = 6, max_len: int = 120) -> list[str]:
    if not isinstance(value, list):
        return []
    out = []
    for item in value[:limit]:
        s = str(item).strip()
        if s:
            out.append(s[:max_len])
    return out


def assess_code_origin(code: str, diagram_names: list[str] | None = None) -> dict:
    """评估代码的「AI 参与度」并生成答辩题。

    返回 {ai_rate, summary, signals, out_of_scope, questions, source}；
    任何异常都降级为规则检测，保证提交流程不中断。
    """
    code = (code or "")[:8000]
    names = [str(n)[:40] for n in (diagram_names or []) if str(n).strip()][:30]
    if not settings.DEEPSEEK_API_KEY:
        return _fallback_origin_check(code)
    names_text = "、".join(names) if names else "（未提供）"
    user_prompt = (f"学生在设计图里定义的函数：{names_text}\n\n"
                   f"【学生提交的代码】\n```python\n{code}\n```\n\n请按要求输出 JSON。")
    try:
        client = _client()
        resp = client.chat.completions.create(
            model=settings.DEEPSEEK_MODEL,
            messages=[{"role": "system", "content": _ORIGIN_SYSTEM},
                      {"role": "user", "content": user_prompt}],
            response_format={"type": "json_object"},
            temperature=0.2,
            timeout=45,
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        rate = int(data.get("ai_rate"))
        if not (0 <= rate <= 100):
            raise ValueError("ai_rate 越界")
        signals = _as_text_list(data.get("signals"), 6, 120)
        out_of_scope = []
        for item in (data.get("out_of_scope") or [])[:4]:
            if not isinstance(item, dict):
                continue
            snippet = str(item.get("snippet", "")).strip()[:160]
            concept = str(item.get("concept", "")).strip()[:20]
            why = str(item.get("why", "")).strip()[:80]
            if snippet or concept:
                out_of_scope.append({"snippet": snippet, "concept": concept, "why": why})
        questions = []
        for i, item in enumerate((data.get("questions") or [])[:3], 1):
            if not isinstance(item, dict):
                continue
            q = str(item.get("question", "")).strip()[:200]
            if q:
                questions.append({"id": f"q{i}",
                                  "snippet": str(item.get("snippet", "")).strip()[:120],
                                  "question": q})
        return {"ai_rate": rate,
                "summary": str(data.get("summary", "")).strip()[:100],
                "signals": signals, "out_of_scope": out_of_scope,
                "questions": questions, "source": "ai"}
    except Exception:
        return _fallback_origin_check(code)


_DEFENSE_SYSTEM = """你是严格的 Python 课程助教。学生在「代码答辩」中解释自己代码里的几处写法，
请判断他是真正理解，还是只会背概念/照搬现成答案。

判定规则：
1. 必须结合代码里的具体变量、执行顺序、数据变化来回答；只讲抽象概念、与这段代码无关、
   明显是通用套话的，判为不懂。
2. 答非所问、回避问题、含糊其辞（如"就是一种数据结构""用来存储数据的"）判为不懂。
3. 能用大白话讲清"它是干什么的、不写会怎样、改了会怎样"的，即使术语不准确也算懂。
4. 不要因为口语化、错别字扣分；也不要因为答案很长就给高分。

输出严格 JSON，不要任何额外文字：
{{"understanding": 0-100, "verdict": "真懂|半懂|没懂",
  "comment": "<=120字，指出答得好的地方和含糊的地方",
  "per_question": [{{"index": 1, "ok": true, "comment": "<=50字"}}]}}"""


def grade_defense(code: str, questions: list[dict], answers: list[dict]) -> dict:
    """批改学生答辩：返回 {understanding, verdict, comment, per_question, source}。

    questions: [{id, snippet, question}]；answers: [{id, answer}]。
    """
    code = (code or "")[:8000]
    pairs = []
    for i, q in enumerate(questions[:3], 1):
        ans = next((a for a in answers if str(a.get("id")) == str(q.get("id"))), None)
        pairs.append({"index": i, "snippet": q.get("snippet", ""),
                      "question": q.get("question", ""),
                      "answer": (ans or {}).get("answer", "")})
    if not any(p["answer"].strip() for p in pairs):
        return {"understanding": 0, "verdict": "没懂",
                "comment": "没有作答，无法判断是否理解。", "per_question": [],
                "source": "none"}
    if not settings.DEEPSEEK_API_KEY:
        return {"understanding": 0, "verdict": "未判定",
                "comment": "未接入 AI，无法自动批改答辩，请老师课上当面抽查。",
                "per_question": [], "source": "fallback"}

    lines = ["【学生代码】", "```python", code, "```", "", "【答辩记录】"]
    for p in pairs:
        lines.append(f"{p['index']}. 代码处：{p['snippet']}\n   问题：{p['question']}\n"
                     f"   学生回答：{p['answer'].strip()[:600] or '（未作答）'}")
    try:
        client = _client()
        resp = client.chat.completions.create(
            model=settings.DEEPSEEK_MODEL,
            messages=[{"role": "system", "content": _DEFENSE_SYSTEM},
                      {"role": "user", "content": "\n".join(lines) + "\n\n请输出 JSON。"}],
            response_format={"type": "json_object"},
            temperature=0.2,
            timeout=45,
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        score = int(data.get("understanding"))
        if not (0 <= score <= 100):
            raise ValueError("understanding 越界")
        verdict = str(data.get("verdict", "")).strip()
        if verdict not in ("真懂", "半懂", "没懂"):
            verdict = "半懂" if score >= 50 else "没懂"
        per_question = []
        for item in (data.get("per_question") or [])[:3]:
            if not isinstance(item, dict):
                continue
            per_question.append({"index": int(item.get("index", 0) or 0),
                                 "ok": bool(item.get("ok")),
                                 "comment": str(item.get("comment", "")).strip()[:80]})
        return {"understanding": score, "verdict": verdict,
                "comment": str(data.get("comment", "")).strip()[:200],
                "per_question": per_question, "source": "ai"}
    except Exception as exc:
        return {"understanding": 0, "verdict": "未判定",
                "comment": f"AI 批改失败（{exc}），请老师课上当面抽查。",
                "per_question": [], "source": "error"}
