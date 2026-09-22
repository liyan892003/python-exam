"""徽章体系（依据 badge-system-spec.md v1.0）。

四大家族共 58 枚：
  A 知识点进阶线：8 线 × 4 级 = 32（青铜/白银/紫金/黄金，题数+难度+正确率门槛）
  B 计算思维：12 枚（8 道相关题，暂以知识点关键词近似）
  C 成就趣味：7 枚（事件触发）
  D 单枚知识点：7 枚（15 题、正确率 70%）

勋章 SVG：深色珐琅章面 + 金属外圈 + 刻度环 + 顶部宝石挂头。
"""
import math
from html import escape

POS = "#7fd89a"  # 章内绿色「正确/达成」点缀

TIERS = {
    "bronze": {"name": "青铜", "en": "Bronze",
               "mA": "#f8ddb5", "mB": "#c07d43", "mC": "#6a3c1f",
               "gem": "#e6ad72", "glow": "rgba(224,164,106,.5)", "color": "#e0a46a"},
    "silver": {"name": "白银", "en": "Silver",
               "mA": "#f2fbff", "mB": "#9cbccd", "mC": "#4c6070",
               "gem": "#c3e2f0", "glow": "rgba(160,210,232,.45)", "color": "#a9d2e8"},
    "epic": {"name": "紫金", "en": "Epic",
             "mA": "#efe2ff", "mB": "#9269e6", "mC": "#3a2a6b",
             "gem": "#bd9ff5", "glow": "rgba(147,105,230,.5)", "color": "#b99df2"},
    "legend": {"name": "黄金", "en": "Legendary",
               "mA": "#fff6d6", "mB": "#e2b641", "mC": "#795612",
               "gem": "#f5d678", "glow": "rgba(245,214,120,.55)", "color": "#f5d678"},
}
TIER_ORDER = ["bronze", "silver", "epic", "legend"]

# 各级门槛（spec 2.1）：累计答对数、进阶下限、挑战下限、正确率
TIER_GATES = {
    "bronze": {"total": 10, "advanced": 0,  "challenge": 0,  "acc": 0.60,
               "gate": "累计答对 10 题·正确率 ≥60%"},
    "silver": {"total": 25, "advanced": 8,  "challenge": 0,  "acc": 0.65,
               "gate": "累计 25 题（含 8 进阶）·正确率 ≥65%"},
    "epic":   {"total": 45, "advanced": 18, "challenge": 0,  "acc": 0.75,
               "gate": "累计 45 题（含 18 进阶）·正确率 ≥75%"},
    "legend": {"total": 70, "advanced": 0,  "challenge": 10, "acc": 0.80,
               "gate": "累计 70 题（含 10 挑战）·正确率 ≥80%"},
}

# ============================== 目录数据（spec 第 5 节） ==============================

# A 家族：track id → (中文名, emblem, [(tier, code, en, zh, knowledge), ...])
A_TRACKS = {
    "loop": ("循环", "loop", [
        ("bronze", "loop_1", "First Loop", "初次循环试探", "for 循环基础、range"),
        ("silver", "loop_2", "Loop Handler", "循环好手", "while、break/continue、嵌套循环"),
        ("epic", "loop_3", "Loop Master", "循环大师", "enumerate/zip、列表推导式"),
        ("legend", "loop_4", "Loop Warrior", "循环战士", "向量化/生成器、何时避免显式循环"),
    ]),
    "function": ("函数", "fx", [
        ("bronze", "function_1", "First Function", "初写函数", "def、参数、return"),
        ("silver", "function_2", "Function Crafter", "函数熟手", "默认参数、*args/**kwargs、多返回值"),
        ("epic", "function_3", "Function Master", "函数大师", "高阶函数、lambda、map/filter、作用域与闭包"),
        ("legend", "function_4", "Function Sage", "函数宗师", "装饰器、函数式思维"),
    ]),
    "data_structure": ("数据结构", "flask", [
        ("bronze", "data_structure_1", "First List", "初识列表", "list 增删改查、索引切片"),
        ("silver", "data_structure_2", "Structure Handler", "结构好手", "dict、set、tuple、解包"),
        ("epic", "data_structure_3", "Structure Master", "结构大师", "嵌套结构、推导式"),
        ("legend", "data_structure_4", "Structure Sage", "结构宗师", "复杂度直觉与结构选型"),
    ]),
    "string": ("字符串", "text", [
        ("bronze", "string_1", "First Words", "初识字符串", "拼接、索引、f-string"),
        ("silver", "string_2", "Text Handler", "文本好手", "split/join/replace/查找/大小写"),
        ("epic", "string_3", "Text Master", "文本大师", "切片技巧、格式化进阶、编码"),
        ("legend", "string_4", "Regex Ranger", "正则游侠", "正则表达式匹配与提取"),
    ]),
    "oop": ("面向对象", "cube", [
        ("bronze", "oop_1", "First Class", "初建类", "class/__init__/self、实例化"),
        ("silver", "oop_2", "Object Handler", "对象好手", "封装、property、实例vs类属性"),
        ("epic", "oop_3", "Inheritance Master", "继承大师", "继承、多态、魔术方法"),
        ("legend", "oop_4", "Design Sage", "设计宗师", "dataclass、组合优于继承、OOP 设计"),
    ]),
    "debug": ("调试", "bug", [
        ("bronze", "debug_1", "First Catch", "初次捉虫", "print 调试、定位首个 bug"),
        ("silver", "debug_2", "Bug Reader", "读错高手", "读懂 Traceback"),
        ("epic", "debug_3", "Bug Hunter", "猎虫能手", "断点调试、assert 断言"),
        ("legend", "debug_4", "Bug Slayer", "除虫大师", "二分排查、最小可复现、logging"),
    ]),
    "pandas": ("数据分析", "quant", [
        ("bronze", "pandas_1", "First Frame", "初见数据", "读 CSV/Excel、查看 DataFrame"),
        ("silver", "pandas_2", "Data Cleaner", "清洗好手", "缺失值、筛选、排序、类型转换"),
        ("epic", "pandas_3", "Analysis Master", "分析大师", "groupby、透视表、merge/join"),
        ("legend", "pandas_4", "Insight Sage", "洞察宗师", "时间序列、可视化、完整分析报告"),
    ]),
    "computational_thinking": ("计算思维", "tree", [
        ("bronze", "ct_1", "Decomposer", "化整为零", "分解：把问题拆成清晰的小步骤"),
        ("silver", "ct_2", "Pattern Finder", "见微知著", "模式识别：发现重复与规律"),
        ("epic", "ct_3", "Abstractor", "去芜存菁", "抽象：抓本质、隐藏细节"),
        ("legend", "ct_4", "Algorithm Architect", "运筹帷幄", "算法设计：设计解法、比较方案、评估复杂度"),
    ]),
}

# B 家族：code → (tier, emblem, en, zh, mindset)
B_THINKING = {
    "rubber_duck":           ("bronze", "duck", "Rubber Duck Sensei", "小黄鸭大师", "小黄鸭调试法（讲解式排错）"),
    "test_believer":         ("bronze", "shield", "Test Believer", "测试信徒", "测试思维（写完必验）"),
    "dry_disciple":          ("bronze", "loop", "DRY Disciple", "绝不重复", "复用 / DRY"),
    "edge_hunter":           ("silver", "warn", "Edge Hunter", "边界猎人", "边界思维（空值/极端输入）"),
    "iteration_sage":        ("silver", "loop", "Iteration Sage", "小步快跑", "迭代思维（先跑通再优化）"),
    "code_archaeologist":    ("silver", "doc", "Code Archaeologist", "代码考古学家", "读代码（读懂他人/旧代码）"),
    "divide_conquer":        ("epic", "tree", "Divide & Conquer", "分而治之", "分解（计算思维支柱）"),
    "pattern_seer":          ("epic", "grid", "Pattern Seer", "模式先知", "模式识别（计算思维支柱）"),
    "automation_alchemist":  ("epic", "flask", "Automation Alchemist", "偷懒有道", "自动化思维"),
    "abstraction_architect": ("legend", "cube", "Abstraction Architect", "抽象建筑师", "抽象（计算思维支柱）"),
    "blueprint_thinker":     ("legend", "tree", "Blueprint Thinker", "谋定后动", "算法设计（计算思维支柱）"),
    "model_maker":           ("legend", "cube", "Model Maker", "建模师", "建模思维（现实→数据结构）"),
}

# C 家族：code → (emblem, en, zh, trigger)
C_ACHIEVE = {
    "first_light":      ("spark", "First Light", "初次点亮", "提交人生第一份 Python 作答"),
    "bug_whisperer":    ("bug", "Bug Whisperer", "除虫者", "考试答错的题，后来独立练对"),
    "pythonic_master":  ("laurel", "Pythonic Master", "优雅之道", "8 条知识点进阶线全部达到青铜"),
    "zen_seeker":       ("zen", "Zen Seeker", "禅意寻者", "运行 import this 并提交一段感悟"),
    "flawless_streak":  ("flame", "Flawless Streak", "全勤满分", "在一套不少于 5 题的试卷中全部答对"),
    "quant_rookie":     ("quant", "Quant Rookie", "量化新锐", "完成一个班全部题目（≥10）且正确率 ≥80%"),
    "one_liner_legend": ("fx", "One-Liner Legend", "一行传奇", "用一行可读代码完成他人十行的任务"),
}

# D 家族：code → (emblem, en, zh, focus)
D_SINGLE = {
    "conditionals":      ("branch", "Conditionals", "条件判断", "if/elif/else、三元表达式"),
    "truthiness":        ("toggle", "Truthiness", "真值与逻辑", "truthy/falsy、短路求值"),
    "modules":           ("blocks", "Modules", "模块与标准库", "import、math/random/datetime/collections/json"),
    "file_io":           ("doc", "File I/O", "文件与 I/O", "with 上下文、编码、CSV/JSON、pathlib"),
    "exceptions":        ("warn", "Exceptions", "异常处理", "try/except/finally、raise、自定义异常"),
    "environment":       ("gear", "Environment", "环境与生态", "pip、虚拟环境、__main__"),
    "advanced_features": ("bolt", "Advanced Features", "进阶特性", "生成器/yield、上下文管理器、typing"),
}

# 组装扁平 BADGES：(code, tier, emblem, en, zh, desc)
BADGES = []
for _tid, (_tname, _em, _levels) in A_TRACKS.items():
    for _tier, _code, _en, _zh, _know in _levels:
        BADGES.append((_code, _tier, _em, _en, _zh,
                       f"{_know} · {TIER_GATES[_tier]['gate']}"))
for _code, (_tier, _em, _en, _zh, _mind) in B_THINKING.items():
    BADGES.append((_code, _tier, _em, _en, _zh, f"{_mind} · 答对 8 道相关题目"))
for _code, (_em, _en, _zh, _trig) in C_ACHIEVE.items():
    _tier = "bronze"
    if _code in ("pythonic_master", "quant_rookie", "flawless_streak"):
        _tier = "legend" if _code == "pythonic_master" else "epic"
    BADGES.append((_code, _tier, _em, _en, _zh, _trig))
for _code, (_em, _en, _zh, _focus) in D_SINGLE.items():
    BADGES.append((_code, "bronze", _em, _en, _zh, f"{_focus} · 累计答对 15 题·正确率 ≥70%"))

BADGE_MAP = {b[0]: b for b in BADGES}
A_TRACK_IDS = list(A_TRACKS.keys())
# 8 条进阶线的青铜 code（pythonic_master 达成条件 / finale 用）
A_BRONZE_CODES = [lv[0][1] for lv in (t[2] for t in A_TRACKS.values())]


# ============================== 章内图案 ==============================

def _emblem(kind: str, c: str, gem: str) -> str:
    if kind == "spark":
        return f"""
    <path d="M32 6 L37.5 25 L56 30 L37.5 35 L32 55 L26.5 35 L8 30 L26.5 25 Z" fill="{c}"/>
    <path d="M50 10 l2.2 6 6 2.2 -6 2.2 -2.2 6 -2.2 -6 -6 -2.2 6 -2.2 Z" fill="{c}" opacity=".65"/>
    <circle cx="13" cy="49" r="2.3" fill="{c}" opacity=".55"/>"""
    if kind == "bug":
        return f"""
    <g stroke="{c}" stroke-width="4" stroke-linecap="round" fill="none">
      <path d="M27 15 q-3 -6 -8 -8"/><path d="M37 15 q3 -6 8 -8"/>
      <path d="M20 30 l-9 -4"/><path d="M20 38 l-10 0"/><path d="M21 46 l-9 5"/>
      <path d="M44 30 l9 -4"/><path d="M44 38 l10 0"/><path d="M43 46 l9 5"/>
    </g>
    <circle cx="32" cy="19" r="5.5" fill="{c}"/>
    <path d="M20 40 q0 -16 12 -16 q12 0 12 16 q0 13 -12 13 q-12 0 -12 -13 Z" fill="{c}" fill-opacity=".16" stroke="{c}" stroke-width="3.4"/>
    <path d="M27 40 l4 5 8 -11" stroke="{POS}" stroke-width="4.4" fill="none" stroke-linecap="round" stroke-linejoin="round"/>"""
    if kind == "loop":
        return f"""
    <g stroke="{c}" stroke-width="4.4" fill="none" stroke-linecap="round">
      <path d="M17 27 a16 16 0 0 1 29 -4"/>
      <path d="M47 37 a16 16 0 0 1 -29 4"/>
    </g>
    <path d="M45 11 l3.5 13 -13 -2 Z" fill="{c}"/>
    <path d="M19 53 l-3.5 -13 13 2 Z" fill="{c}"/>"""
    if kind == "fx":
        return f"""
    <circle cx="32" cy="32" r="24" fill="none" stroke="{c}" stroke-width="2" stroke-opacity=".35"/>
    <text x="32" y="43" text-anchor="middle" font-family="Georgia,'Songti SC',serif"
          font-style="italic" font-weight="600" font-size="28" fill="{c}">ƒ(x)</text>"""
    if kind == "text":
        return f"""
    <circle cx="32" cy="32" r="24" fill="none" stroke="{c}" stroke-width="2" stroke-opacity=".35"/>
    <text x="32" y="42" text-anchor="middle" font-family="Georgia,'Songti SC',serif"
          font-weight="700" font-size="26" fill="{c}">Aa</text>"""
    if kind == "cube":
        return f"""
    <path d="M32 9 L53 20 L53 43 L32 54 L11 43 L11 20 Z" fill="{c}" fill-opacity=".14"
          stroke="{c}" stroke-width="3.4" stroke-linejoin="round"/>
    <path d="M32 9 L32 31 M32 31 L53 20 M32 31 L11 20" stroke="{c}" stroke-width="3.4" fill="none"/>
    <circle cx="32" cy="31" r="2.6" fill="{gem}"/>"""
    if kind == "flask":
        return f"""
    <g stroke="{c}" stroke-width="4" fill="none" stroke-linecap="round" stroke-linejoin="round">
      <path d="M26 12 h12"/>
      <path d="M28 12 v15 L16 47 a5 5 0 0 0 4.5 8 h23 a5 5 0 0 0 4.5 -8 L36 27 V12"/>
    </g>
    <path d="M20.5 41 h23 L48 48 a5 5 0 0 1 -4.5 7 h-23 a5 5 0 0 1 -4.5 -7 Z" fill="{gem}" fill-opacity=".85"/>
    <circle cx="26" cy="48" r="2" fill="#0a0f1e"/><circle cx="34" cy="51" r="1.6" fill="#0a0f1e"/><circle cx="30" cy="45" r="1.5" fill="#0a0f1e"/>"""
    if kind == "quant":
        return f"""
    <g stroke="{c}" stroke-width="3" stroke-linecap="round">
      <line x1="16" y1="38" x2="16" y2="54"/><line x1="31" y1="30" x2="31" y2="50"/><line x1="46" y1="22" x2="46" y2="44"/>
    </g>
    <rect x="12" y="42" width="8" height="9" rx="1.5" fill="{c}" fill-opacity=".9"/>
    <rect x="27" y="34" width="8" height="11" rx="1.5" fill="{c}" fill-opacity=".9"/>
    <rect x="42" y="26" width="8" height="12" rx="1.5" fill="{c}" fill-opacity=".9"/>
    <path d="M13 47 L28 39 L40 43 L55 20" stroke="{POS}" stroke-width="4" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
    <path d="M55 20 l-9 0 3 8 Z" fill="{POS}"/>"""
    if kind == "tree":
        return f"""
    <g stroke="{c}" stroke-width="3.4" fill="none">
      <line x1="32" y1="18" x2="20" y2="34"/><line x1="32" y1="18" x2="44" y2="34"/>
      <line x1="44" y1="34" x2="38" y2="50"/><line x1="44" y1="34" x2="52" y2="50"/>
    </g>
    <circle cx="32" cy="16" r="6.5" fill="{c}"/>
    <circle cx="20" cy="36" r="5.5" fill="{c}"/><circle cx="44" cy="36" r="5.5" fill="{c}"/>
    <circle cx="38" cy="52" r="5" fill="{gem}"/><circle cx="52" cy="52" r="5" fill="{gem}"/>"""
    if kind == "laurel":
        side = f"""
      <path d="M22 53 q-9 -14 -3 -31" stroke="{c}" stroke-width="3" fill="none" stroke-linecap="round"/>
      <g fill="{c}">
        <ellipse cx="15" cy="26" rx="2.8" ry="5.4" transform="rotate(-28 15 26)"/>
        <ellipse cx="14.5" cy="34" rx="2.8" ry="5.4" transform="rotate(-8 14.5 34)"/>
        <ellipse cx="16" cy="42" rx="2.8" ry="5.2" transform="rotate(14 16 42)"/>
        <ellipse cx="19.5" cy="49" rx="2.6" ry="4.8" transform="rotate(34 19.5 49)"/>
      </g>"""
        return f"""
    {side}
    <g transform="translate(64,0) scale(-1,1)">{side}</g>
    <path d="M32 20 l9 11 -9 13 -9 -13 Z" fill="{gem}"/>
    <path d="M32 20 l9 11 -9 13 -9 -13 Z" fill="none" stroke="{c}" stroke-width="1.4"/>
    <path d="M32 20 l9 11 -9 13 Z" fill="#000" opacity=".12"/>"""
    if kind == "flame":
        return f"""
    <path d="M32 6 C 41 18, 45 23, 45 35 a13 13 0 1 1 -26 0 c 0 -8 4 -11 5 -17 c 4 4 4 8 4 8 C 30 25, 25 18, 32 6 Z" fill="{gem}"/>
    <path d="M32 6 C 41 18, 45 23, 45 35 a13 13 0 1 1 -26 0 c 0 -8 4 -11 5 -17 c 4 4 4 8 4 8 C 30 25, 25 18, 32 6 Z" fill="#fff" fill-opacity=".12"/>
    <path d="M25 37 l4.5 5.5 9.5 -12" stroke="#0a0f1e" stroke-width="4" fill="none" stroke-linecap="round" stroke-linejoin="round"/>"""
    # —— 新增图案 ——
    if kind == "branch":
        return f"""
    <g stroke="{c}" stroke-width="3.4" fill="none" stroke-linecap="round">
      <path d="M20 46 V22 a7 7 0 0 1 7 -7 h4"/>
      <path d="M31 28 h8 a8 8 0 0 1 8 8 v-14"/>
    </g>
    <circle cx="20" cy="50" r="4.6" fill="{c}"/>
    <circle cx="31" cy="15" r="4.6" fill="{gem}"/>
    <circle cx="47" cy="18" r="4.6" fill="{c}"/>"""
    if kind == "toggle":
        return f"""
    <rect x="9" y="23" width="46" height="18" rx="9" fill="none" stroke="{c}" stroke-width="3.4"/>
    <circle cx="43" cy="32" r="6" fill="{POS}"/>
    <text x="20" y="36" text-anchor="middle" font-size="11" font-weight="700" fill="{c}">1</text>"""
    if kind == "blocks":
        return f"""
    <rect x="27" y="11" width="24" height="24" rx="4" fill="{c}" fill-opacity=".55" stroke="{c}" stroke-width="2.6"/>
    <rect x="11" y="27" width="24" height="24" rx="4" fill="{gem}" fill-opacity=".9" stroke="{c}" stroke-width="2.6"/>
    <path d="M39 11 v6 M39 17 h12" stroke="{c}" stroke-width="2.4" fill="none"/>"""
    if kind == "doc":
        return f"""
    <path d="M18 8 h17 l11 11 v37 H18 Z" fill="{c}" fill-opacity=".14" stroke="{c}" stroke-width="3.4" stroke-linejoin="round"/>
    <path d="M35 8 v11 h11" fill="none" stroke="{c}" stroke-width="3.4" stroke-linejoin="round"/>
    <g stroke="{c}" stroke-width="2.6" stroke-linecap="round">
      <line x1="25" y1="30" x2="42" y2="30"/><line x1="25" y1="37" x2="42" y2="37"/><line x1="25" y1="44" x2="36" y2="44"/>
    </g>"""
    if kind == "warn":
        return f"""
    <path d="M32 9 L55 49 H9 Z" fill="{c}" fill-opacity=".16" stroke="{c}" stroke-width="3.6" stroke-linejoin="round"/>
    <line x1="32" y1="24" x2="32" y2="38" stroke="{c}" stroke-width="5" stroke-linecap="round"/>
    <circle cx="32" cy="44.5" r="2.8" fill="{c}"/>"""
    if kind == "gear":
        teeth = "".join(
            f'<line x1="{32 + 15*math.cos(a):.1f}" y1="{32 + 15*math.sin(a):.1f}" '
            f'x2="{32 + 19*math.cos(a):.1f}" y2="{32 + 19*math.sin(a):.1f}"/>'
            for a in [i * math.tau / 8 for i in range(8)])
        return f"""
    <g stroke="{c}" stroke-width="3.2" stroke-linecap="round">{teeth}
      <circle cx="32" cy="32" r="12" fill="{c}" fill-opacity=".18"/>
      <circle cx="32" cy="32" r="12" fill="none"/>
    </g>
    <circle cx="32" cy="32" r="4.6" fill="{gem}"/>"""
    if kind == "bolt":
        return f"""
    <path d="M36 6 L19 34 h10 l-4 24 19 -31 h-11 Z" fill="{gem}" stroke="{c}" stroke-width="2.4" stroke-linejoin="round"/>"""
    if kind == "shield":
        return f"""
    <path d="M32 8 L50 15 V31 q0 15 -18 21 q-18 -6 -18 -21 V15 Z" fill="{c}" fill-opacity=".15" stroke="{c}" stroke-width="3.4" stroke-linejoin="round"/>
    <path d="M24 31 l6 6 12 -14" stroke="{POS}" stroke-width="4.4" fill="none" stroke-linecap="round" stroke-linejoin="round"/>"""
    if kind == "grid":
        return f"""
    <g stroke="{c}" stroke-width="2.6">
      <rect x="11" y="11" width="13" height="13" rx="2.5" fill="{c}" fill-opacity=".9"/>
      <rect x="28" y="11" width="13" height="13" rx="2.5" fill="{c}" fill-opacity=".55"/>
      <rect x="11" y="28" width="13" height="13" rx="2.5" fill="{c}" fill-opacity=".55"/>
      <rect x="28" y="28" width="13" height="13" rx="2.5" fill="{gem}" fill-opacity=".9"/>
    </g>
    <path d="M44 24 h9 M48.5 19.5 L53 24 l-4.5 4.5" stroke="{c}" stroke-width="2.6" fill="none" stroke-linecap="round" stroke-linejoin="round"/>"""
    if kind == "duck":
        return f"""
    <circle cx="40" cy="19" r="8" fill="{c}"/>
    <path d="M47 18 h10 l-4.5 4.5 Z" fill="{gem}"/>
    <path d="M10 42 q0 -15 17 -15 q8 0 12 4 h7 q6 0 6 6 q0 11 -19 11 q-15 0 -19 -6 Z" fill="{c}" fill-opacity=".85"/>
    <circle cx="42.5" cy="17" r="1.5" fill="#0a0f1e"/>"""
    if kind == "zen":
        return f"""
    <path d="M47 13 A23 23 0 1 0 50 49" fill="none" stroke="{c}" stroke-width="6.5" stroke-linecap="round"/>
    <circle cx="15" cy="17" r="3" fill="{gem}"/>"""
    return ""


def _ticks(cx: float, cy: float, r: float, n: int) -> str:
    parts = []
    for i in range(n):
        a = (i / n) * math.tau
        x1 = cx + math.cos(a) * (r - 3)
        y1 = cy + math.sin(a) * (r - 3)
        x2 = cx + math.cos(a) * (r + 3)
        y2 = cy + math.sin(a) * (r + 3)
        parts.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}"/>')
    return "".join(parts)


def medal_svg(code: str, unlocked: bool = True) -> str:
    """渲染单枚勋章 SVG（200x220 视图，CSS 控制显示尺寸）。"""
    bid, tier_key, em, en, zh, _desc = BADGE_MAP[code]
    t = TIERS[tier_key]
    u = bid
    emblem = _emblem(em, t["mA"], t["gem"])
    svg = f"""<svg class="medal-svg" viewBox="0 0 200 220" role="img" aria-label="{escape(zh)}">
  <defs>
    <radialGradient id="glow-{u}" cx="50%" cy="46%" r="55%">
      <stop offset="0%" stop-color="{t['glow']}"/><stop offset="72%" stop-color="rgba(0,0,0,0)"/>
    </radialGradient>
    <linearGradient id="rim-{u}" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="{t['mA']}"/><stop offset="48%" stop-color="{t['mB']}"/><stop offset="100%" stop-color="{t['mC']}"/>
    </linearGradient>
    <radialGradient id="disc-{u}" cx="40%" cy="34%" r="80%">
      <stop offset="0%" stop-color="#26355a"/><stop offset="100%" stop-color="#0a1020"/>
    </radialGradient>
    <linearGradient id="gem-{u}" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#ffffff"/><stop offset="35%" stop-color="{t['gem']}"/><stop offset="100%" stop-color="{t['mC']}"/>
    </linearGradient>
  </defs>
  <g{' class="medal-locked"' if not unlocked else ''}>
  <circle cx="100" cy="96" r="98" fill="url(#glow-{u})"/>
  <circle cx="100" cy="96" r="86" fill="none" stroke="url(#rim-{u})" stroke-width="15"/>
  <circle cx="100" cy="96" r="93.5" fill="none" stroke="{t['mA']}" stroke-opacity=".3" stroke-width="1.4"/>
  <circle cx="100" cy="96" r="78.5" fill="none" stroke="#000" stroke-opacity=".4" stroke-width="1.6"/>
  <g stroke="{t['mC']}" stroke-opacity=".55" stroke-width="1.8" stroke-linecap="round">{_ticks(100, 96, 86, 36)}</g>
  <circle cx="100" cy="96" r="72" fill="url(#disc-{u})" stroke="{t['mB']}" stroke-opacity=".5" stroke-width="1.4"/>
  <ellipse cx="80" cy="64" rx="42" ry="26" fill="#ffffff" opacity=".05"/>
  <g transform="translate(100,96) scale(1.34) translate(-32,-32)">{emblem}</g>
  <path d="M100 3 l11 13 -11 13 -11 -13 Z" fill="url(#gem-{u})" stroke="{t['mC']}" stroke-width="1"/>
  <path d="M100 3 l11 13 -11 13 Z" fill="#000" opacity=".14"/>
  </g>
</svg>"""
    return svg
