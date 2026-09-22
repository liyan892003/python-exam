"""SQLAlchemy 数据模型。

关系：
  User (teacher) 1───* Class
  User (student) *───* Class          (via Enrollment)
  Class       1───* ProblemSet 1───* Question 1───* Choice
  User(student)*───* Question        (via Answer，每题只答一次)
"""
import enum
from datetime import datetime

from sqlalchemy import (
    Column, Integer, String, Text, Boolean, DateTime, ForeignKey,
    UniqueConstraint, Enum as SAEnum, func, and_, Table,
)
from sqlalchemy.orm import relationship

from .db import Base


class Role(str, enum.Enum):
    teacher = "teacher"
    student = "student"


class QType(str, enum.Enum):
    mc = "mc"   # 选择题（4 选 1）
    tf = "tf"   # 判断题（2 选 1）


class QuestionSource(str, enum.Enum):
    ai = "ai"
    manual = "manual"


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    username = Column(String(64), nullable=False)
    password_hash = Column(String(255), nullable=False)
    # native_enum=False：用 VARCHAR 存枚举值而非数据库原生 ENUM，
    # 兼容 SQLite/Postgres 且便于跨库迁移
    role = Column(SAEnum(Role, native_enum=False, length=20),
                  nullable=False, default=Role.student)
    created_at = Column(DateTime, default=datetime.utcnow)

    # teacher 拥有的班级
    classes_teaching = relationship("Class", back_populates="teacher", cascade="all, delete-orphan")
    # student 的选课
    enrollments = relationship("Enrollment", back_populates="student", cascade="all, delete-orphan")
    answers = relationship("Answer", back_populates="student", cascade="all, delete-orphan")


class Class(Base):
    __tablename__ = "classes"
    id = Column(Integer, primary_key=True)
    name = Column(String(128), nullable=False)
    description = Column(Text, default="")
    join_code = Column(String(6), unique=True, index=True, nullable=False)
    teacher_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    teacher = relationship("User", back_populates="classes_teaching")
    enrollments = relationship("Enrollment", back_populates="class_", cascade="all, delete-orphan")
    sets = relationship("ProblemSet", back_populates="class_", cascade="all, delete-orphan",
                       order_by="ProblemSet.created_at")
    # 兼容旧代码：通过 set 反向访问所有题目
    questions = relationship("Question", back_populates="class_", viewonly=True)


class ProblemSet(Base):
    """试卷：教师一套一套地出题，如「第2节课」。"""
    __tablename__ = "problem_sets"
    id = Column(Integer, primary_key=True)
    class_id = Column(Integer, ForeignKey("classes.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(128), nullable=False)
    description = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)

    class_ = relationship("Class", back_populates="sets")
    questions = relationship("Question", back_populates="set_", cascade="all, delete-orphan",
                             order_by="Question.created_at")


class Enrollment(Base):
    __tablename__ = "enrollments"
    __table_args__ = (UniqueConstraint("student_id", "class_id", name="uq_student_class"),)
    id = Column(Integer, primary_key=True)
    student_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    class_id = Column(Integer, ForeignKey("classes.id", ondelete="CASCADE"), nullable=False)
    joined_at = Column(DateTime, default=datetime.utcnow)

    student = relationship("User", back_populates="enrollments")
    class_ = relationship("Class", back_populates="enrollments")


# 题目 ↔ 知识点 多对多关联表
question_kps = Table(
    "question_kps", Base.metadata,
    Column("question_id", Integer, ForeignKey("questions.id", ondelete="CASCADE"),
           primary_key=True),
    Column("kp_id", Integer, ForeignKey("knowledge_points.id", ondelete="CASCADE"),
           primary_key=True),
)


class Question(Base):
    __tablename__ = "questions"
    id = Column(Integer, primary_key=True)
    class_id = Column(Integer, ForeignKey("classes.id", ondelete="CASCADE"), nullable=False, index=True)
    # 试卷 ID：新题必填；旧数据迁移后也会回填（列本身允许 NULL 以便迁移）
    set_id = Column(Integer, ForeignKey("problem_sets.id", ondelete="CASCADE"), nullable=True, index=True)
    q_type = Column(SAEnum(QType, native_enum=False, length=20), nullable=False)
    stem = Column(Text, nullable=False)
    explanation = Column(Text, default="")
    source = Column(SAEnum(QuestionSource, native_enum=False, length=20),
                    nullable=False, default=QuestionSource.manual)
    points = Column(Integer, default=10)
    # 难度：basic 基础★ / advanced 进阶★★ / challenge 挑战★★★（徽章进阶门槛用）
    difficulty = Column(String(12), nullable=False, default="basic")
    created_at = Column(DateTime, default=datetime.utcnow)

    class_ = relationship("Class", back_populates="questions")
    set_ = relationship("ProblemSet", back_populates="questions")
    choices = relationship("Choice", back_populates="question", cascade="all, delete-orphan",
                           order_by="Choice.id")
    answers = relationship("Answer", back_populates="question", cascade="all, delete-orphan")
    # 知识点标签（多对多）
    kps = relationship("KnowledgePoint", secondary=question_kps,
                       back_populates="questions")


class Choice(Base):
    __tablename__ = "choices"
    id = Column(Integer, primary_key=True)
    question_id = Column(Integer, ForeignKey("questions.id", ondelete="CASCADE"), nullable=False, index=True)
    text = Column(String(500), nullable=False)
    is_correct = Column(Boolean, nullable=False, default=False)

    question = relationship("Question", back_populates="choices")


class Answer(Base):
    __tablename__ = "answers"
    __table_args__ = (UniqueConstraint("student_id", "question_id", name="uq_student_question"),)
    id = Column(Integer, primary_key=True)
    student_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    question_id = Column(Integer, ForeignKey("questions.id", ondelete="CASCADE"), nullable=False)
    choice_id = Column(Integer, ForeignKey("choices.id", ondelete="SET NULL"))
    is_correct = Column(Boolean, nullable=False, default=False)
    points_awarded = Column(Integer, nullable=False, default=0)
    answered_at = Column(DateTime, default=datetime.utcnow)

    student = relationship("User", back_populates="answers")
    question = relationship("Question", back_populates="answers")


class KnowledgePoint(Base):
    """知识点（树状：parent_id 为空 = 章节，否则为章节下的节点）。

    归属教师：同一教师的所有班级共用一张知识地图；
    预置大纲首次「加载」后可任意增删改。
    """
    __tablename__ = "knowledge_points"
    id = Column(Integer, primary_key=True)
    teacher_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    parent_id = Column(Integer, ForeignKey("knowledge_points.id", ondelete="CASCADE"),
                       nullable=True, index=True)
    name = Column(String(128), nullable=False)
    description = Column(Text, default="")
    emoji = Column(String(16), default="⭐")
    position = Column(Integer, default=0)
    is_preset = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    parent = relationship("KnowledgePoint", remote_side=[id],
                          back_populates="children")
    children = relationship(
        "KnowledgePoint", cascade="all, delete-orphan",
        order_by="KnowledgePoint.position", back_populates="parent")
    questions = relationship("Question", secondary=question_kps,
                             back_populates="kps")


# 关卡 ↔ 试卷 多对多（一关可包含多套试卷，一套卷也可在多关复用）
level_sets = Table(
    "level_sets", Base.metadata,
    Column("level_id", Integer, ForeignKey("levels.id", ondelete="CASCADE"),
           primary_key=True),
    Column("set_id", Integer, ForeignKey("problem_sets.id", ondelete="CASCADE"),
           primary_key=True),
)


class Level(Base):
    """闯关关卡：对应「第 N 周」。顺序解锁，包含若干试卷与知识点拼图。"""
    __tablename__ = "levels"
    id = Column(Integer, primary_key=True)
    class_id = Column(Integer, ForeignKey("classes.id", ondelete="CASCADE"),
                      nullable=False, index=True)
    name = Column(String(128), nullable=False)          # 如「第 1 周 · 变量入门」
    topic = Column(String(32), default="")              # 路径圆圈上的主题短名，如「Python基础」
    description = Column(Text, default="")
    position = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

    class_ = relationship("Class", backref="levels")
    sets = relationship("ProblemSet", secondary=level_sets)
    kp_links = relationship("LevelKp", back_populates="level",
                            cascade="all, delete-orphan",
                            order_by="LevelKp.id")


class LevelKp(Base):
    """关卡内的知识点拼图；requirement 为教学要求：know 了解 / understand 理解 / master 掌握。"""
    __tablename__ = "level_kps"
    __table_args__ = (UniqueConstraint("level_id", "kp_id", name="uq_level_kp"),)
    id = Column(Integer, primary_key=True)
    level_id = Column(Integer, ForeignKey("levels.id", ondelete="CASCADE"),
                      nullable=False, index=True)
    kp_id = Column(Integer, ForeignKey("knowledge_points.id", ondelete="CASCADE"),
                   nullable=False)
    requirement = Column(String(16), nullable=False, default="understand")

    level = relationship("Level", back_populates="kp_links")
    kp = relationship("KnowledgePoint")


class MasteryJudge(Base):
    """AI 对「学生 × 知识点」掌握度的判定结果（带数据指纹缓存，有新作答才重判）。"""
    __tablename__ = "mastery_judges"
    __table_args__ = (UniqueConstraint("student_id", "kp_id", name="uq_student_kp_judge"),)
    id = Column(Integer, primary_key=True)
    student_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    kp_id = Column(Integer, ForeignKey("knowledge_points.id", ondelete="CASCADE"),
                   nullable=False, index=True)
    verdict = Column(String(16), nullable=False, default="none")  # mastered/partial/none
    reason = Column(Text, default="")
    sig = Column(String(64), default="")   # 答题数据指纹，变化才重新调用 AI
    source = Column(String(16), default="ai")  # ai / fallback（API 失败时的规则判定）
    judged_at = Column(DateTime, default=datetime.utcnow)


class AbilityReport(Base):
    """错题本 AI 学情总结（按班级缓存，错题数据指纹变化才重新调用 AI）。"""
    __tablename__ = "ability_reports"
    __table_args__ = (UniqueConstraint("student_id", "class_id",
                                       name="uq_student_class_report"),)
    id = Column(Integer, primary_key=True)
    student_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    class_id = Column(Integer, ForeignKey("classes.id", ondelete="CASCADE"),
                      nullable=False, index=True)
    sig = Column(String(64), default="")
    summary = Column(Text, default="")     # 总体评价
    problems = Column(Text, default="")    # 主要问题
    advice = Column(Text, default="")      # 改进建议
    labels = Column(String(255), default="")  # 问题标签，逗号分隔
    source = Column(String(16), default="ai")
    created_at = Column(DateTime, default=datetime.utcnow)


class PracticeAttempt(Base):
    """练习模式作答（区别于考试 Answer，可反复作答，不影响成绩与排行榜）。

    错题本依据：考试答错的题，只要存在一条正确的 PracticeAttempt 即视为攻克。
    """
    __tablename__ = "practice_attempts"
    id = Column(Integer, primary_key=True)
    student_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    question_id = Column(Integer, ForeignKey("questions.id", ondelete="CASCADE"),
                         nullable=False, index=True)
    choice_id = Column(Integer, ForeignKey("choices.id", ondelete="SET NULL"))
    is_correct = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class StudentAchievement(Base):
    """学生成就徽章解锁记录（code 对应 app/badges.py 的 9 枚固定徽章）。"""
    __tablename__ = "student_achievements"
    __table_args__ = (UniqueConstraint("student_id", "code", name="uq_student_badge"),)
    id = Column(Integer, primary_key=True)
    student_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    code = Column(String(32), nullable=False)
    unlocked_at = Column(DateTime, default=datetime.utcnow)


# —— 排行榜查询辅助 ——
def class_leaderboard(db, class_id: int):
    """返回该班级学生总分排行 [(rank, user_id, username, score, answered_count)]。

    关键：LEFT JOIN Answer 时必须同时匹配 student_id，否则每个学生会被连到
    所有人的答题记录，SUM 就把别人的分也累加进来（典型笛卡尔爆炸 bug）。
    """
    rows = (
        db.query(
            User.id.label("user_id"),
            User.username.label("username"),
            func.coalesce(func.sum(Answer.points_awarded), 0).label("score"),
            func.count(Answer.id).label("answered"),
        )
        .join(Enrollment, Enrollment.student_id == User.id)
        .outerjoin(Question, Question.class_id == class_id)
        .outerjoin(Answer, and_(
            Answer.question_id == Question.id,
            Answer.student_id == User.id,
        ))
        .filter(Enrollment.class_id == class_id, User.role == Role.student)
        .group_by(User.id, User.username)
        .order_by(func.coalesce(func.sum(Answer.points_awarded), 0).desc(),
                  func.count(Answer.id).asc())
        .all()
    )
    return [
        {"rank": i + 1, "user_id": r.user_id, "username": r.username,
         "score": int(r.score), "answered": int(r.answered)}
        for i, r in enumerate(rows)
    ]
