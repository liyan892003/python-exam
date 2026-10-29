"""猜数字课堂游戏：服务端轮次引擎。

规则（教师端单屏版升级为多人版）：
  1. 教师创建一局 → 班级学生进入游戏页即加入，按加入先后排队。
  2. 教师点「开始」→ 随机生成 1~100 的答案，范围 [1,100]，从第一位同学开始。
  3. 轮流猜：猜大 / 猜小 → 收窄范围，轮到下一位；猜中 → 本轮结束。
  4. 本轮有人猜中后，若此时还有同学「从没轮到过」，
     则换一个新的随机数，并从猜中者的下一位继续（不回到第一位）。
  5. 若一轮里所有同学都猜完仍没猜中 → 保持当前答案与范围，从下一位继续。
  6. 当「每位同学都至少轮到过一次」且本轮有人猜中时，游戏结束，
     公布所有猜中过的同学。
"""
import random
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .models import GameSession, GamePlayer, GameGuess, GameStatus

LOW, HIGH = 1, 100


def _new_target() -> int:
    return random.randint(LOW, HIGH)


def add_player(db: Session, session: GameSession, student_id: int) -> bool:
    """学生加入本局（仅等待阶段可加入）。返回是否新加入。"""
    if session.status != GameStatus.lobby:
        return False
    exists = (db.query(GamePlayer)
              .filter(GamePlayer.session_id == session.id,
                      GamePlayer.student_id == student_id).first())
    if exists:
        return False
    db.add(GamePlayer(session_id=session.id, student_id=student_id))
    db.commit()
    return True


def reset_to_lobby(db: Session, session: GameSession) -> None:
    """「再玩一局」：保留已加入的同学，清空作答与进度，回到等待开始。"""
    session.status = GameStatus.lobby
    session.target = None
    session.lo, session.hi = LOW, HIGH
    session.turn_pos = 0
    session.round_no = 0
    session.started_at = None
    session.finished_at = None
    for p in session.players:
        p.played = False
        p.wins = 0
    db.query(GameGuess).filter(GameGuess.session_id == session.id).delete()
    db.commit()


def start_game(db: Session, session: GameSession) -> None:
    """开始（或重新开始）一局：至少要有 1 位同学。"""
    if not session.players:
        raise HTTPException(400, "还没有同学加入，先把游戏链接发给学生吧")
    session.status = GameStatus.playing
    session.round_no = 1
    session.target = _new_target()
    session.lo, session.hi = LOW, HIGH
    session.turn_pos = 0
    session.started_at = datetime.utcnow()
    session.finished_at = None
    for p in session.players:
        p.played = False
        p.wins = 0
    db.query(GameGuess).filter(GameGuess.session_id == session.id).delete()
    db.commit()


def _finish_round(db: Session, session: GameSession) -> None:
    """本轮有人猜中后的收尾：全员都轮到过 → 结束；否则换数字，从下一位继续。

    注意：这里不重置 turn_pos —— 调用方（submit_guess）已把轮次指针推进到下一位，
    所以猜中后是新数字 + 接着下一位同学，而不是回到第一位。
    """
    if all(p.played for p in session.players):
        session.status = GameStatus.finished
        session.finished_at = datetime.utcnow()
        return
    session.round_no += 1
    session.target = _new_target()
    session.lo, session.hi = LOW, HIGH


def submit_guess(db: Session, session: GameSession, student_id: int, guess: int) -> str:
    """处理一次猜测，返回 'high' / 'low' / 'win'。"""
    if session.status != GameStatus.playing:
        raise HTTPException(400, "游戏还没开始或已经结束了")
    players = session.players
    if not players:
        raise HTTPException(400, "本局还没有同学加入")
    pos = session.turn_pos % len(players)
    cur = players[pos]
    if cur.student_id != student_id:
        cur_name = cur.student.username if cur.student else "其他同学"
        raise HTTPException(403, f"还没轮到你哦，现在是「{cur_name}」在猜")
    if not (session.lo <= guess <= session.hi):
        raise HTTPException(400, f"请输入 {session.lo} ~ {session.hi} 之间的数字")

    cur.played = True
    # 不管猜中与否，都轮到下一位（走到队尾则回到第一位）
    session.turn_pos = (pos + 1) % len(players)
    if guess == session.target:
        cur.wins = (cur.wins or 0) + 1
        db.add(GameGuess(session_id=session.id, student_id=student_id, guess=guess,
                         result="win", lo=session.lo, hi=session.hi,
                         round_no=session.round_no))
        _finish_round(db, session)
        db.commit()
        return "win"

    if guess > session.target:
        session.hi = guess - 1
        result = "high"
    else:
        session.lo = guess + 1
        result = "low"
    db.add(GameGuess(session_id=session.id, student_id=student_id, guess=guess,
                     result=result, lo=session.lo, hi=session.hi,
                     round_no=session.round_no))
    db.commit()
    return result


def session_state(session: GameSession, viewer_id: int | None = None) -> dict:
    """构造给前端渲染/轮询的状态（不含答案）。"""
    players = session.players
    n = len(players)
    turn_pos = (session.turn_pos % n) if (n and session.status == GameStatus.playing) else None

    players_json = [{
        "sid": p.student_id,
        "no": p.student.email,
        "name": p.student.username,
        "played": bool(p.played),
        "wins": int(p.wins or 0),
        "is_turn": (turn_pos == i),
    } for i, p in enumerate(players)]

    turn = None
    if turn_pos is not None:
        tp = players[turn_pos]
        turn = {"sid": tp.student_id, "no": tp.student.email, "name": tp.student.username}

    history = [{
        "round": g.round_no, "sid": g.student_id,
        "no": g.student.email, "name": g.student.username,
        "guess": g.guess, "result": g.result, "lo": g.lo, "hi": g.hi,
    } for g in session.guesses]

    winners = [{
        "no": g.student.email, "name": g.student.username,
        "round": g.round_no, "guess": g.guess,
    } for g in session.guesses if g.result == "win"]

    return {
        "status": session.status.value,
        "round": session.round_no,
        "lo": session.lo,
        "hi": session.hi,
        "n_players": n,
        "n_played": sum(1 for p in players if p.played),
        "players": players_json,
        "turn": turn,
        "history": history,
        "winners": winners,
        "your_turn": (turn is not None and viewer_id is not None and turn["sid"] == viewer_id),
        "viewer_id": viewer_id,
        "joined": (viewer_id is not None and any(p.student_id == viewer_id for p in players)),
    }
