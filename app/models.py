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
    UniqueConstraint, Enum as SAEnum, func, and_,
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
    created_at = Column(DateTime, default=datetime.utcnow)

    class_ = relationship("Class", back_populates="questions")
    set_ = relationship("ProblemSet", back_populates="questions")
    choices = relationship("Choice", back_populates="question", cascade="all, delete-orphan",
                           order_by="Choice.id")
    answers = relationship("Answer", back_populates="question", cascade="all, delete-orphan")


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
