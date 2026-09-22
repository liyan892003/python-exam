"""徽章自动评定（依据 badge-system-spec.md v1.0）。

在考试提交、练习提交、进入闯关路径时调用 evaluate_achievements：
  A 进阶线：按「答对题的知识点标签」归到 8 条线，依据累计答对数/难度题数/正确率
            评定青铜→白银→紫金→黄金（题数为累计值，拿到高级自动包含低级）。
  D 单枚点：15 题 + 正确率 70%。
  B 思维：暂以知识点关键词近似，答对 8 道相关题。
  C 成就：first_light / bug_whisperer / flawless_streak / quant_rookie / pythonic_master。
计数：同一题只在首次答对时计入（distinct question_id）。
"""
import re

from sqlalchemy import select

from .models import (StudentAchievement, Answer, PracticeAttempt, Question,
                     ProblemSet, Enrollment, KnowledgePoint, question_kps)
from .badges import (BADGES, BADGE_MAP, A_TRACKS, D_SINGLE,
                     TIER_ORDER, TIER_GATES, A_BRONZE_CODES)

ALL_CODES = [b[0] for b in BADGES]

# A 线 / B / D 的知识点关键词（匹配知识点名或其所属章节名）
TRACK_KEYWORDS = {
    "loop": r"循环|for|while|range|迭代|遍历|推导式",
    "function": r"函数|参数|lambda|装饰器|闭包|作用域|返回值|def\b",
    "data_structure": r"列表|字典|元组|集合|数据结构|序列|list|dict|tuple",
    "string": r"字符串|文本|正则|格式化|string",
    "oop": r"面向对象|对象|继承|多态|封装|实例化|魔术方法|dataclass",
    "debug": r"调试|bug|traceback|断言|排查|报错|logging",
    "pandas": r"pandas|dataframe|数据分析|csv|excel|groupby|透视|清洗|merge|时间序列",
    "computational_thinking": r"计算思维|算法|分解|抽象|复杂度|递归|分治|模式识别",
}
D_KEYWORDS = {
    "conditionals": r"条件|分支|if|elif|三元",
    "truthiness": r"真值|逻辑|布尔|短路|bool",
    "modules": r"模块|标准库|import|math|random|datetime|collections",
    "file_io": r"文件|路径|pathlib|编码|输入输出|i/o|csv|json",
    "exceptions": r"异常|try|except|raise|finally|错误处理",
    "environment": r"环境|pip|虚拟环境|包管理|__main__",
    "advanced_features": r"生成器|yield|迭代器|上下文管理|typing|进阶特性",
}
# B 家族多数需要专项任务系统，暂以关键词近似，无对应题目的自然永不解锁
B_KEYWORDS = {
    "rubber_duck": r"调试|讲解|排错",
    "test_believer": r"测试|断言|assert|验证",
    "dry_disciple": r"复用|重构|函数式",
    "edge_hunter": r"边界|空值|极端|异常|输入校验",
    "iteration_sage": r"迭代|循环|小步",
    "code_archaeologist": r"读代码|旧代码|代码阅读",
    "divide_conquer": r"分解|递归|分治",
    "pattern_seer": r"模式|规律|模式识别",
    "automation_alchemist": r"自动化|脚本|批量",
    "abstraction_architect": r"抽象|建模|本质",
    "blueprint_thinker": r"算法设计|复杂度|算法|方案比较",
    "model_maker": r"建模|数据结构|模型",
}


def _correct_qids(db, uid: int) -> set[int]:
    """答对过的题（考试答对 或 练习答对），同一题只计一次。"""
    exam = {q for (q,) in db.query(Answer.question_id)
            .filter(Answer.student_id == uid, Answer.is_correct.is_(True)).all()}
    prac = {q for (q,) in db.query(PracticeAttempt.question_id)
            .filter(PracticeAttempt.student_id == uid,
                    PracticeAttempt.is_correct.is_(True)).distinct().all()}
    return exam | prac


def _attempted_qids(db, uid: int) -> set[int]:
    exam = {q for (q,) in db.query(Answer.question_id)
            .filter(Answer.student_id == uid).all()}
    prac = {q for (q,) in db.query(PracticeAttempt.question_id)
            .filter(PracticeAttempt.student_id == uid).distinct().all()}
    return exam | prac


def _build_pools(db, class_ids: list[int]):
    """构建 题目→难度、题目→标签知识点名(含章节名) 映射。"""
    if not class_ids:
        return {}, {}, set()
    questions = db.query(Question.id, Question.difficulty).filter(
        Question.class_id.in_(class_ids)).all()
    q_diff = {qid: (diff or "basic") for qid, diff in questions}
    all_qids = set(q_diff)
    kps = {k.id: (k.name, k.parent_id)
           for k in db.query(KnowledgePoint).all()}
    parent_name = {pid: name for pid, name in
                   db.query(KnowledgePoint.id, KnowledgePoint.name)
                   .filter(KnowledgePoint.parent_id.is_(None)).all()}
    tag_rows = db.execute(
        select(question_kps.c.question_id, question_kps.c.kp_id)
        .where(question_kps.c.question_id.in_(all_qids))).all() if all_qids else []
    q_texts: dict[int, str] = {}
    for qid, kid in tag_rows:
        name, pid = kps.get(kid, ("", None))
        q_texts.setdefault(qid, "")
        q_texts[qid] += " " + name + " " + parent_name.get(pid, "")
    return q_diff, q_texts, all_qids


def _pool(q_texts: dict[int, str], pattern: str) -> set[int]:
    rx = re.compile(pattern, re.IGNORECASE)
    return {qid for qid, txt in q_texts.items() if rx.search(txt)}


def _pool_stats(pool: set[int], correct: set[int], attempted: set[int],
                q_diff: dict[int, str]) -> dict:
    c = pool & correct
    a = pool & attempted
    return {
        "total": len(c),
        "basic": sum(1 for q in c if q_diff.get(q, "basic") == "basic"),
        "advanced": sum(1 for q in c if q_diff.get(q) == "advanced"),
        "challenge": sum(1 for q in c if q_diff.get(q) == "challenge"),
        "acc": (len(c) / len(a)) if a else 0.0,
        "attempted": len(a),
    }


def _highest_tier(stats: dict) -> int:
    """返回达到的最高级序号 0..4（0=未达青铜）。从高往低判。"""
    for i in range(3, -1, -1):
        tier = TIER_ORDER[i]
        g = TIER_GATES[tier]
        # 注：spec 黄金级还要求 1 个综合任务，任务系统上线前暂以 10 道挑战题替代
        if (stats["total"] >= g["total"]
                and stats["advanced"] >= g["advanced"]
                and stats["challenge"] >= g["challenge"]
                and stats["acc"] >= g["acc"]):
            return i + 1
    return 0


def evaluate_achievements(db, user) -> list[str]:
    uid = user.id
    existing = {c for (c,) in db.query(StudentAchievement.code)
                .filter(StudentAchievement.student_id == uid).all()}
    earned = set(existing) & set(ALL_CODES)  # 丢弃历史上已废弃的 code

    correct = _correct_qids(db, uid)
    attempted = _attempted_qids(db, uid)
    class_ids = [c for (c,) in db.query(Enrollment.class_id)
                 .filter(Enrollment.student_id == uid).all()]
    q_diff, q_texts, _all = _build_pools(db, class_ids)

    # —— C: first_light 初次点亮 ——
    if db.query(Answer.id).filter(Answer.student_id == uid).first():
        earned.add("first_light")

    # —— C: bug_whisperer 除虫者 ——
    wrong_exam = {q for (q,) in db.query(Answer.question_id)
                  .filter(Answer.student_id == uid,
                          Answer.is_correct.is_(False)).all()}
    if wrong_exam & correct:
        earned.add("bug_whisperer")

    # —— C: flawless_streak / quant_rookie ——
    if class_ids:
        sets = db.query(ProblemSet).filter(ProblemSet.class_id.in_(class_ids)).all()
        answered_correct = {q for (q,) in db.query(Answer.question_id)
                            .filter(Answer.student_id == uid,
                                    Answer.is_correct.is_(True)).all()}
        answered_any = {q for (q,) in db.query(Answer.question_id)
                        .filter(Answer.student_id == uid).all()}
        for s in sets:
            qids = {q.id for q in s.questions}
            if len(qids) >= 5 and qids and qids <= answered_correct:
                earned.add("flawless_streak")
        for cid in class_ids:
            qids = {q for (q,) in db.query(Question.id).filter(Question.class_id == cid).all()}
            if len(qids) >= 10 and qids <= answered_any:
                if len(answered_correct & qids) / len(qids) >= 0.8:
                    earned.add("quant_rookie")

    # —— A: 8 条知识点进阶线 ——
    for tid in A_TRACKS:
        pool = _pool(q_texts, TRACK_KEYWORDS[tid])
        st = _pool_stats(pool, correct, attempted, q_diff)
        lvl = _highest_tier(st)
        for i in range(lvl):
            earned.add(A_TRACKS[tid][2][i][1])

    # —— D: 单枚知识点（15 题 + 70%）——
    for did, pattern in D_KEYWORDS.items():
        pool = _pool(q_texts, pattern)
        st = _pool_stats(pool, correct, attempted, q_diff)
        if st["total"] >= 15 and st["acc"] >= 0.70:
            earned.add(did)

    # —— B: 计算思维（8 道相关题）——
    for bid, pattern in B_KEYWORDS.items():
        pool = _pool(q_texts, pattern)
        if len(pool & correct) >= 8:
            earned.add(bid)

    # —— C: pythonic_master 优雅之道：8 线全青铜 ——
    if set(A_BRONZE_CODES) <= earned:
        earned.add("pythonic_master")

    # 落库新解锁（含补齐低级），清理已废弃 code
    fresh = [c for c in ALL_CODES if c in earned and c not in existing]
    stale = [c for c in existing if c not in ALL_CODES]
    for code in fresh:
        db.add(StudentAchievement(student_id=uid, code=code))
    if stale:
        db.query(StudentAchievement).filter(
            StudentAchievement.student_id == uid,
            StudentAchievement.code.in_(stale)).delete(synchronize_session=False)
    if fresh or stale:
        db.commit()
    return fresh


def unlocked_codes(db, user) -> set[str]:
    return {c for (c,) in db.query(StudentAchievement.code)
            .filter(StudentAchievement.student_id == user.id).all()
            if c in BADGE_MAP}


def next_goal(db, user) -> dict | None:
    """下一目标提示：找距离下一门槛题数最近的 A 线级别或 D 知识点。"""
    correct = _correct_qids(db, user.id)
    attempted = _attempted_qids(db, user.id)
    class_ids = [c for (c,) in db.query(Enrollment.class_id)
                 .filter(Enrollment.student_id == user.id).all()]
    q_diff, q_texts, _ = _build_pools(db, class_ids)
    candidates = []

    for tid, (tname, _em, levels) in A_TRACKS.items():
        st = _pool_stats(_pool(q_texts, TRACK_KEYWORDS[tid]),
                         correct, attempted, q_diff)
        lvl = _highest_tier(st)
        if lvl >= 4:
            continue
        g = TIER_GATES[TIER_ORDER[lvl]]
        need_total = max(0, g["total"] - st["total"])
        need_adv = max(0, g["advanced"] - st["advanced"])
        need_chl = max(0, g["challenge"] - st["challenge"])
        code = levels[lvl][1]
        zh = levels[lvl][3]
        parts = [f"还差 {need_total} 题"]
        if need_adv:
            parts.append(f"含 {need_adv} 进阶")
        if need_chl:
            parts.append(f"含 {need_chl} 挑战")
        candidates.append((need_total, code,
                           f"{tname}·{zh}：{'，'.join(parts)}，正确率 ≥{int(g['acc']*100)}%"))

    for did, pattern in D_KEYWORDS.items():
        st = _pool_stats(_pool(q_texts, pattern), correct, attempted, q_diff)
        if st["total"] >= 15 and st["acc"] >= 0.70:
            continue
        need = max(0, 15 - st["total"])
        zh = D_SINGLE[did][2]
        candidates.append((need, did, f"{zh}：还差 {need} 题，正确率 ≥70%"))

    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0])
    _, code, text = candidates[0]
    return {"code": code, "text": text}
