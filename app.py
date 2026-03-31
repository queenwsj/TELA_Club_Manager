"""
TELA CLUB Random Match Generator v2.06
======================================
변경사항:
  - 사이드바 메뉴: 점수판 → 랜덤페어 순서
  - 점수판: shelve 기반 날짜별 영구저장 (새로고침 유지)
  - 점수판: 확정 버튼으로 명시적 저장
  - 점수판: 날짜 수기 입력
  - 점수판: 모바일에서도 좌우 컬럼 강제 유지 (CSS flex)
  - 랜덤페어: 관리자 비밀번호 — 일치해야 대진표 생성 가능
"""

import streamlit as st
import pandas as pd
import random
import io
import shelve
import json
import os
from dataclasses import dataclass, field
from itertools import zip_longest
from typing import Dict, FrozenSet, List, Optional, Set, Tuple
from datetime import date


# ============================================================
# 섹션 0: 저장소 경로
# ============================================================

SAVE_DIR  = os.path.join(os.path.dirname(__file__), ".tela_data")
os.makedirs(SAVE_DIR, exist_ok=True)
SHELF_PATH = os.path.join(SAVE_DIR, "scoreboard")

ADMIN_PASSWORD = "tela1234"   # ← 관리자 비밀번호 (여기서 변경 가능)


def shelf_save(date_key: str, schedule: list, scores: dict):
    """날짜 키로 대진표+점수 영구저장"""
    with shelve.open(SHELF_PATH) as db:
        db[date_key] = {"schedule": schedule, "scores": scores}


def shelf_load(date_key: str) -> Optional[dict]:
    """날짜 키로 데이터 로드. 없으면 None"""
    with shelve.open(SHELF_PATH) as db:
        return db.get(date_key, None)


def shelf_list_dates() -> List[str]:
    """저장된 날짜 목록"""
    with shelve.open(SHELF_PATH) as db:
        return sorted(db.keys(), reverse=True)


# ============================================================
# 섹션 1: 데이터 구조
# ============================================================

@dataclass
class MatchState:
    teammate_used:      Set[FrozenSet] = field(default_factory=set)
    opponent_used:      Set[FrozenSet] = field(default_factory=set)
    mixed_partner_used: Set[FrozenSet] = field(default_factory=set)

@dataclass
class PlayerStats:
    name:         str
    league:       str
    game_count:   int = 0
    mixed_count:  int = 0
    round_records: Dict[str, str] = field(default_factory=dict)
    type_counts:  Dict[str, int]  = field(default_factory=lambda: {
        "남복": 0, "여복": 0, "혼복": 0, "잡복": 0
    })


# ============================================================
# 섹션 2: 기초 유틸리티
# ============================================================

def base_name(p: str) -> str:
    return p.split("(")[0].strip()

def get_gender(code: str) -> str:
    c = base_name(code)
    if len(c) >= 2:
        ch = c[1].upper()
        if ch == "M": return "M"
        if ch == "W": return "W"
    return "U"

def is_custom_code(code: str) -> bool:
    raw = base_name(code)
    return len(raw) > 2 and not raw[2:].isdigit()

def display_name(code: str) -> str:
    dup = "(중복)" in code
    raw = base_name(code)
    if is_custom_code(raw):
        g = "남" if raw[1].upper() == "M" else "여" if raw[1].upper() == "W" else ""
        shown = f"{raw[2:]}({g})" if g else raw[2:]
    else:
        shown = raw
    return shown + ("(중복)" if dup else "")

def pname(code: str) -> str:
    """점수판용 짧은 이름"""
    raw = base_name(code)
    if is_custom_code(raw):
        g_label = "(남)" if raw[1].upper() == "M" else "(여)"
        return raw[2:] + g_label
    return raw

def team_key(t: Tuple) -> FrozenSet:
    return frozenset(base_name(p) for p in t)

def opp_key(a: str, b: str) -> FrozenSet:
    return frozenset([base_name(a), base_name(b)])

def mixed_partner_key(t: Tuple) -> Optional[FrozenSet]:
    g1, g2 = get_gender(t[0]), get_gender(t[1])
    if {g1, g2} == {"M", "W"}:
        return frozenset([base_name(t[0]), base_name(t[1])])
    return None

def classify_match(players4: List[str]) -> str:
    genders = [get_gender(p) for p in players4]
    m, w = genders.count("M"), genders.count("W")
    if m == 4: return "남복"
    if w == 4: return "여복"
    if m == 2 and w == 2: return "혼복"
    return "잡복"

def is_mixed_match(match_type: str) -> bool:
    return match_type in ("혼복", "잡복")

def sort_by_mixed_least(players: List[str], mixed_counts: Dict[str, int]) -> List[str]:
    return sorted(players, key=lambda p: (mixed_counts.get(base_name(p), 0), random.random()))


# ============================================================
# 섹션 3: 페어링
# ============================================================

def score_pairing(t1, t2, gs, rs) -> int:
    pen = 0
    for tk in (team_key(t1), team_key(t2)):
        if tk in rs.teammate_used: pen += 5000
        if tk in gs.teammate_used: pen += 1000
    for t in (t1, t2):
        mk = mixed_partner_key(t)
        if mk:
            if mk in rs.mixed_partner_used: pen += 500
            if mk in gs.mixed_partner_used: pen += 100
    for x in t1:
        for y in t2:
            ok = opp_key(x, y)
            if ok in rs.opponent_used: pen += 50
            if ok in gs.opponent_used: pen += 10
    return pen

def best_pairing(players4, gs, rs):
    a, b, c, d = players4
    all_pairs = [((a,b),(c,d)), ((a,c),(b,d)), ((a,d),(b,c))]
    genders = [get_gender(p) for p in players4]
    if genders.count("M") == 2 and genders.count("W") == 2:
        def mixed_team(t): return {get_gender(x) for x in t} == {"M","W"}
        mp = [(t1,t2) for t1,t2 in all_pairs if mixed_team(t1) and mixed_team(t2)]
        cands = mp if mp else all_pairs
    else:
        cands = all_pairs
    random.shuffle(cands)
    best, best_s = None, float("inf")
    for t1, t2 in cands:
        s = score_pairing(t1, t2, gs, rs)
        if s < best_s: best_s, best = s, (t1, t2)
    return best

def commit_pairing(t1, t2, gs, rs):
    for state in (gs, rs):
        state.teammate_used.add(team_key(t1))
        state.teammate_used.add(team_key(t2))
        for x in t1:
            for y in t2: state.opponent_used.add(opp_key(x, y))
        for t in (t1, t2):
            mk = mixed_partner_key(t)
            if mk: state.mixed_partner_used.add(mk)


# ============================================================
# 섹션 4: 리그 우선순위 & 쿼터
# ============================================================

LEAGUE_PRIORITY = {
    "A리그": ["동성", "혼복", "잡복"],
    "B리그": ["혼복", "동성", "잡복"],
}

def get_priority(league):
    return LEAGUE_PRIORITY.get(league, ["동성", "혼복", "잡복"])

LEAGUE_QUOTA = {
    "A리그": {"mixed_max": None, "dong_min": None},
    "B리그": {"mixed_max": 2,    "dong_min": 1   },
}

def get_quota(league):
    return LEAGUE_QUOTA.get(league, {"mixed_max": None, "dong_min": None})

def mixed_quota_ok(p, mixed_counts, league):
    q = get_quota(league)
    if q["mixed_max"] is None: return True
    return mixed_counts.get(base_name(p), 0) < q["mixed_max"]


# ============================================================
# 섹션 5: 그룹 구성
# ============================================================

def build_one_group(pool, mixed_counts, league="A리그"):
    if len(pool) < 4: return None, pool[:]
    anchor = pool[0]; rest = pool[1:]
    g_a = get_gender(anchor)
    same = [p for p in rest if get_gender(p) == g_a]
    opp  = [p for p in rest if get_gender(p) != g_a and get_gender(p) != "U"]
    priority = get_priority(league)

    def try_dongsong():
        if len(same) >= 3:
            sh = list(same); random.shuffle(sh); return [anchor]+sh[:3]
        return None
    def try_mixed():
        if len(same) >= 1 and len(opp) >= 2:
            sh = list(same); random.shuffle(sh)
            return [anchor]+sh[:1]+sort_by_mixed_least(opp,mixed_counts)[:2]
        return None
    def try_jabbok():
        if len(same) >= 2 and len(opp) >= 1:
            sh = list(same); random.shuffle(sh)
            return [anchor]+sh[:2]+sort_by_mixed_least(opp,mixed_counts)[:1]
        if len(opp) >= 3: return [anchor]+sort_by_mixed_least(opp,mixed_counts)[:3]
        return None

    dispatch = {"동성": try_dongsong, "혼복": try_mixed, "잡복": try_jabbok}
    group = None
    for ptype in priority:
        result = dispatch[ptype]()
        if result is not None: group = result; break
    if group is None:
        group = [anchor] + sort_by_mixed_least(rest, mixed_counts)[:3]
    if group is None or len(group) < 4: return None, pool[:]
    remaining = list(pool)
    for p in group:
        if p in remaining: remaining.remove(p)
    return group, remaining

def build_all_groups(pool, mixed_counts, league="A리그"):
    groups, remaining = [], list(pool)
    while len(remaining) >= 4:
        group, remaining = build_one_group(remaining, mixed_counts, league)
        if group is None: break
        groups.append(group)
    return groups, remaining


# ============================================================
# 섹션 6: 정규 라운드 매치 생성
# ============================================================

def _pick_3_for_anchor(anchor, remaining, mixed_counts, league="A리그"):
    if len(remaining) < 3: return None
    g = get_gender(anchor)
    men   = [p for p in remaining if get_gender(p) == "M"]
    women = [p for p in remaining if get_gender(p) == "W"]
    priority = get_priority(league)
    opp_quota = [p for p in (women if g=="M" else men) if mixed_quota_ok(p,mixed_counts,league)]
    opp_all   = women if g=="M" else men
    anchor_ok = mixed_quota_ok(anchor, mixed_counts, league)

    def try_dongsong():
        if g=="M" and len(men)>=3: return men[:3]
        if g=="W" and len(women)>=3: return women[:3]
        return None
    def try_mixed():
        if not anchor_ok: return None
        opp_use = opp_quota if len(opp_quota)>=2 else []
        if not opp_use: return None
        same_q = [p for p in (men if g=="M" else women) if mixed_quota_ok(p,mixed_counts,league)]
        if len(same_q)>=1:
            return sort_by_mixed_least(same_q,mixed_counts)[:1]+sort_by_mixed_least(opp_use,mixed_counts)[:2]
        return None
    def try_jabbok():
        opp_use = opp_quota if opp_quota else opp_all
        if g=="M":
            if len(men)>=2 and len(opp_use)>=1:
                m2=list(men); random.shuffle(m2); return m2[:2]+sort_by_mixed_least(opp_use,mixed_counts)[:1]
            if len(opp_use)>=3: return sort_by_mixed_least(opp_use,mixed_counts)[:3]
        elif g=="W":
            if len(women)>=2 and len(opp_use)>=1:
                w2=list(women); random.shuffle(w2); return w2[:2]+sort_by_mixed_least(opp_use,mixed_counts)[:1]
            if len(opp_use)>=3: return sort_by_mixed_least(opp_use,mixed_counts)[:3]
        return None

    dispatch = {"동성": try_dongsong, "혼복": try_mixed, "잡복": try_jabbok}
    for ptype in priority:
        result = dispatch[ptype]()
        if result is not None: return result

    def try_mixed_fb():
        if g=="M" and len(men)>=1 and len(women)>=2:
            return sort_by_mixed_least(men,mixed_counts)[:1]+sort_by_mixed_least(women,mixed_counts)[:2]
        if g=="W" and len(women)>=1 and len(men)>=2:
            return sort_by_mixed_least(women,mixed_counts)[:1]+sort_by_mixed_least(men,mixed_counts)[:2]
        return None
    def try_jabbok_fb():
        if g=="M":
            if len(men)>=2 and len(women)>=1:
                m2=list(men); random.shuffle(m2); return m2[:2]+sort_by_mixed_least(women,mixed_counts)[:1]
            if len(women)>=3: return sort_by_mixed_least(women,mixed_counts)[:3]
        elif g=="W":
            if len(women)>=2 and len(men)>=1:
                w2=list(women); random.shuffle(w2); return w2[:2]+sort_by_mixed_least(men,mixed_counts)[:1]
            if len(men)>=3: return sort_by_mixed_least(men,mixed_counts)[:3]
        return None

    for fn in [try_dongsong, try_mixed_fb, try_jabbok_fb]:
        result = fn()
        if result is not None: return result

    rest = sort_by_mixed_least(remaining, mixed_counts)
    return rest[:3] if len(rest)>=3 else None


def make_round_matches(players, game_counts, mixed_counts, gs, rs, league="A리그", dong_forced=False):
    n_groups = len(players)//4
    if n_groups == 0: return []

    gender_count = {}
    for p in players:
        g = get_gender(p); gender_count[g] = gender_count.get(g,0)+1

    priority = get_priority(league)
    def sort_key(p):
        return (game_counts.get(base_name(p),0), gender_count.get(get_gender(p),99), random.random())

    working   = sorted(players, key=sort_key)
    men_all   = [p for p in working if get_gender(p)=="M"]
    women_all = [p for p in working if get_gender(p)=="W"]
    groups_of_4 = []
    top_ptype = priority[0]

    if top_ptype == "동성":
        preprocess_slots = min(max(0,n_groups-1), len(men_all)//4+len(women_all)//4)
        men_s   = sorted(men_all,   key=lambda p:(game_counts.get(base_name(p),0),random.random()))
        women_s = sorted(women_all, key=lambda p:(game_counts.get(base_name(p),0),random.random()))
        while len(men_s)>=4 and len(groups_of_4)<preprocess_slots:
            grp=men_s[:4]; men_s=men_s[4:]; groups_of_4.append(grp)
            for p in grp: working.remove(p)
        while len(women_s)>=4 and len(groups_of_4)<preprocess_slots:
            grp=women_s[:4]; women_s=women_s[4:]; groups_of_4.append(grp)
            for p in grp: working.remove(p)

    elif top_ptype == "혼복":
        import math
        quota_ok_m = [p for p in men_all   if mixed_quota_ok(p,mixed_counts,league)]
        quota_ok_w = [p for p in women_all if mixed_quota_ok(p,mixed_counts,league)]
        max_by_quota = min(len(quota_ok_m)//2, len(quota_ok_w)//2)
        minority_cnt = min(len(men_all), len(women_all))
        dong_possible = len(men_all)//4+len(women_all)//4
        mixed_possible = (max_by_quota>0 and minority_cnt>=2)

        if not mixed_possible or dong_forced:
            minority_groups_needed = math.ceil(minority_cnt/4) if minority_cnt>0 else 0
            dong_slots = min(dong_possible,n_groups) if dong_forced else min(dong_possible,max(0,n_groups-minority_groups_needed))
            men_s   = sorted(men_all,   key=lambda p:(game_counts.get(base_name(p),0),random.random()))
            women_s = sorted(women_all, key=lambda p:(game_counts.get(base_name(p),0),random.random()))
            while len(men_s)>=4 and len(groups_of_4)<dong_slots:
                grp=men_s[:4]; men_s=men_s[4:]; groups_of_4.append(grp)
                for p in grp: working.remove(p)
            while len(women_s)>=4 and len(groups_of_4)<dong_slots:
                grp=women_s[:4]; women_s=women_s[4:]; groups_of_4.append(grp)
                for p in grp: working.remove(p)
        else:
            preprocess_slots = min(max(0,n_groups-1), minority_cnt//2, max_by_quota)
            while len(groups_of_4)<preprocess_slots:
                men_avail   = [p for p in working if get_gender(p)=="M" and mixed_quota_ok(p,mixed_counts,league)]
                women_avail = [p for p in working if get_gender(p)=="W" and mixed_quota_ok(p,mixed_counts,league)]
                if len(men_avail)<2 or len(women_avail)<2: break
                m2=sort_by_mixed_least(men_avail,mixed_counts)[:2]
                w2=sort_by_mixed_least(women_avail,mixed_counts)[:2]
                grp=m2+w2; groups_of_4.append(grp)
                for p in grp: working.remove(p)

    remaining_need = n_groups-len(groups_of_4)
    if remaining_need>0 and len(working)>=4:
        wpool = sorted(working, key=sort_key)
        men_w   = [p for p in wpool if get_gender(p)=="M"]
        women_w = [p for p in wpool if get_gender(p)=="W"]
        dong_still_needed = dong_forced and len(groups_of_4)==0
        if dong_still_needed and len(women_w)>=4: first_g,second_g=women_w,men_w
        elif dong_still_needed and len(men_w)>=4: first_g,second_g=men_w,women_w
        elif len(men_w)<=len(women_w): first_g,second_g=men_w,women_w
        else: first_g,second_g=women_w,men_w

        interleaved = []
        for a,b in zip_longest(first_g,second_g):
            if a is not None: interleaved.append(a)
            if b is not None: interleaved.append(b)
        anchors = interleaved[:remaining_need]
        remaining_pool = [p for p in wpool if p not in anchors]
        anchor_league = "A리그" if dong_still_needed else league

        for anchor in anchors:
            three = _pick_3_for_anchor(anchor,remaining_pool,mixed_counts,anchor_league)
            if three is None or len(three)<3:
                three = _pick_3_for_anchor(anchor,remaining_pool,mixed_counts,league)
            if three is None or len(three)<3:
                remaining_pool.insert(0,anchor); continue
            grp = [anchor]+three; groups_of_4.append(grp)
            for p in grp:
                if p in remaining_pool: remaining_pool.remove(p)

        if len(remaining_pool)>=4:
            extra,_ = build_all_groups(remaining_pool,mixed_counts,league)
            groups_of_4.extend(extra)

    if not groups_of_4:
        groups_of_4,_ = build_all_groups(working,mixed_counts,league)

    matches = []
    for g in groups_of_4:
        if len(g)<4: continue
        random.shuffle(g)
        t1,t2 = best_pairing(g,gs,rs)
        commit_pairing(t1,t2,gs,rs)
        mt = classify_match([base_name(p) for p in list(t1)+list(t2)])
        matches.append({"team1":t1,"team2":t2,"type":mt})
    return matches


# ============================================================
# 섹션 7: 이벤트 라운드
# ============================================================

def build_event_round(players, game_counts, mixed_counts, league="A리그", min_games=3, max_games=4):
    all_groups = []
    local_counts = dict(game_counts)
    gender_count = {}
    for p in players:
        g=get_gender(p); gender_count[g]=gender_count.get(g,0)+1

    for _ in range(20):
        need = [p for p in players if local_counts.get(base_name(p),0)<min_games]
        if not need: break
        avail = [p for p in players if p not in need and local_counts.get(base_name(p),0)<max_games]
        pool = sorted(need, key=lambda p:(local_counts.get(base_name(p),0),gender_count.get(get_gender(p),99),random.random()))

        while len(pool)%4!=0:
            cands = sort_by_mixed_least([p for p in avail if p not in pool],mixed_counts)
            if not cands: pool=pool[:(len(pool)//4)*4]; break
            pool.append(cands.pop(0))

        if len(pool)<4: break
        groups, leftovers = build_all_groups(pool,mixed_counts,league)

        if leftovers:
            cands = sort_by_mixed_least([p for p in avail if p not in leftovers and p not in pool],mixed_counts)
            while len(leftovers)<4 and cands: leftovers.append(cands.pop(0))
            if len(leftovers)>=4:
                eg,_ = build_all_groups(leftovers,mixed_counts,league); groups.extend(eg)

        if not groups: break
        for g in groups:
            tagged = []
            for p in g:
                pn=base_name(p)
                tagged.append(pn+"(중복)" if local_counts.get(pn,0)>=min_games else pn)
                local_counts[pn]=local_counts.get(pn,0)+1
            all_groups.append((g,tagged))
    return all_groups


# ============================================================
# 섹션 8: 통계 업데이트
# ============================================================

def update_stats(stats, team1, team2, match_type, round_name, league):
    for p_raw in list(team1)+list(team2):
        p = base_name(p_raw)
        if p not in stats: stats[p] = PlayerStats(name=p,league=league)
        s = stats[p]
        s.game_count += 1
        s.type_counts[match_type] = s.type_counts.get(match_type,0)+1
        if is_mixed_match(match_type): s.mixed_count += 1
        dup = "(중복)" in p_raw
        s.round_records[round_name] = match_type+("★" if dup else "")


# ============================================================
# 섹션 9: 리그 스케줄 생성
# ============================================================

def generate_schedule_from_leagues(league_players, num_rounds=3):
    all_results = []
    all_stats   = {}

    for league_name, players in league_players.items():
        if len(players)<4: continue
        game_counts  = {p:0 for p in players}
        mixed_counts = {p:0 for p in players}
        gs = MatchState()

        for r in range(1, num_rounds+1):
            rname = f"{r}R"
            rs = MatchState()
            matches = make_round_matches(players,game_counts,mixed_counts,gs,rs,league_name,dong_forced=(r==num_rounds))
            for m in matches:
                t1,t2,mt = m["team1"],m["team2"],m["type"]
                for p_raw in list(t1)+list(t2):
                    p=base_name(p_raw); game_counts[p]+=1
                    if is_mixed_match(mt): mixed_counts[p]+=1
                update_stats(all_stats,t1,t2,mt,rname,league_name)
                all_results.append({"round":rname,"league":league_name,"team1":t1,"team2":t2,"type":mt})

        rs = MatchState()
        for raw_g, tagged_g in build_event_round(players,game_counts,mixed_counts,league_name):
            random.shuffle(tagged_g)
            t1,t2 = best_pairing(tagged_g,gs,rs)
            commit_pairing(t1,t2,gs,rs)
            mt = classify_match([base_name(p) for p in list(t1)+list(t2)])
            has_dup = any("(중복)" in p for p in list(t1)+list(t2))
            note = mt+("(중복)" if has_dup else "")
            for p_raw in list(t1)+list(t2):
                p=base_name(p_raw); game_counts[p]+=1
                if is_mixed_match(mt): mixed_counts[p]+=1
            update_stats(all_stats,t1,t2,mt,"4R(이벤트)",league_name)
            all_results.append({"round":"4R(이벤트)","league":league_name,"team1":t1,"team2":t2,"type":note})

    return all_results, all_stats


# ============================================================
# 섹션 10: 입력 파싱
# ============================================================

def parse_custom_players(text, league_prefix):
    players = []
    for line in text.strip().splitlines():
        parts = line.strip().split()
        if not parts: continue
        name = parts[0]; gender = "M"
        if len(parts)>=2 and parts[1].upper() in ("여","W","F"): gender="W"
        players.append(f"{league_prefix}{gender}{name}")
    return players


# ============================================================
# 섹션 11: DataFrame 변환
# ============================================================

def stats_to_df(all_stats):
    rows = []
    for code, s in all_stats.items():
        rows.append({
            "_코드": code, "리그": s.league, "이름": display_name(code),
            "1R": s.round_records.get("1R","-"), "2R": s.round_records.get("2R","-"),
            "3R": s.round_records.get("3R","-"), "4R(이벤트)": s.round_records.get("4R(이벤트)","-"),
            "남복": s.type_counts.get("남복",0), "여복": s.type_counts.get("여복",0),
            "혼복": s.type_counts.get("혼복",0), "잡복": s.type_counts.get("잡복",0),
            "혼성합계": s.mixed_count, "총경기": s.game_count,
        })
    df = pd.DataFrame(rows)
    if df.empty: return df
    return df.sort_values(["리그","_코드"]).reset_index(drop=True)


# ============================================================
# 섹션 12: 점수판 통계
# ============================================================

def compute_scoreboard_stats(schedule, scores):
    player_stats = {}

    def ensure_player(code, league):
        key = base_name(code)
        if key not in player_stats:
            player_stats[key] = {
                "이름": pname(code), "리그": league,
                "출전":0,"승":0,"패":0,"승점":0,"실점":0,
                "1R출전":0,"2R출전":0,"3R출전":0,"4R출전":0,
            }

    for idx, match in enumerate(schedule):
        key = str(idx)
        sc  = scores.get(key, {})
        s1  = sc.get("score1", None)
        s2  = sc.get("score2", None)
        league = match["league"]
        t1  = [base_name(p) for p in match["team1"]]
        t2  = [base_name(p) for p in match["team2"]]
        rnd = match["round"]
        rnd_num = None
        if rnd=="1R": rnd_num=1
        elif rnd=="2R": rnd_num=2
        elif rnd=="3R": rnd_num=3
        elif "4R" in rnd or "이벤트" in rnd: rnd_num=4

        for code in match["team1"]: ensure_player(code, league)
        for code in match["team2"]: ensure_player(code, league)

        for p in t1+t2:
            player_stats[p]["출전"] += 1
            if rnd_num==1: player_stats[p]["1R출전"]+=1
            elif rnd_num==2: player_stats[p]["2R출전"]+=1
            elif rnd_num==3: player_stats[p]["3R출전"]+=1
            elif rnd_num==4: player_stats[p]["4R출전"]+=1

        if s1 is not None and s2 is not None and (s1+s2)>0:
            if s1>s2: winners,losers,ws,ls=t1,t2,s1,s2
            elif s2>s1: winners,losers,ws,ls=t2,t1,s2,s1
            else:
                for p in t1+t2:
                    player_stats[p]["승점"]+=s1; player_stats[p]["실점"]+=s2
                continue
            for p in winners:
                player_stats[p]["승"]+=1; player_stats[p]["승점"]+=ws; player_stats[p]["실점"]+=ls
            for p in losers:
                player_stats[p]["패"]+=1; player_stats[p]["승점"]+=ls; player_stats[p]["실점"]+=ws

    if not player_stats: return pd.DataFrame()
    df = pd.DataFrame(list(player_stats.values()))
    df = df[["리그","이름","출전","승","패","승점","실점","1R출전","2R출전","3R출전","4R출전"]]
    return df.sort_values(["리그","승","승점"],ascending=[True,False,False]).reset_index(drop=True)


# ============================================================
# 섹션 13: schedule 직렬화 헬퍼 (shelve는 tuple을 list로 저장)
# ============================================================

def serialize_schedule(schedule):
    """tuple → list 변환 (shelve 저장용)"""
    result = []
    for m in schedule:
        result.append({
            "round":  m["round"],
            "league": m["league"],
            "team1":  list(m["team1"]),
            "team2":  list(m["team2"]),
            "type":   m["type"],
        })
    return result

def deserialize_schedule(schedule):
    """list → tuple 변환 (로드 후 복원)"""
    result = []
    for m in schedule:
        result.append({
            "round":  m["round"],
            "league": m["league"],
            "team1":  tuple(m["team1"]),
            "team2":  tuple(m["team2"]),
            "type":   m["type"],
        })
    return result


# ============================================================
# 섹션 14: Streamlit 앱
# ============================================================

st.set_page_config(page_title="TELA Tennis Match", page_icon="🎾", layout="wide")

# ── 전역 CSS ────────────────────────────────────────────────
st.markdown("""
<style>
/* 사이드바 폭 */
[data-testid="stSidebar"] { min-width:220px; max-width:260px; }

/* ── 점수판 카드 레이아웃 ────────────────────────────────── */
/* 모바일/데스크탑 모두 flex row 강제 */
.sb-grid {
    display: flex;
    flex-direction: row;
    gap: 10px;
    overflow-x: auto;
    padding-bottom: 8px;
}
.sb-col {
    flex: 1 1 220px;
    min-width: 200px;
}
.rnd-header {
    background: #1a1a2e;
    color: white;
    font-weight: 700;
    font-size: 0.95rem;
    text-align: center;
    padding: 7px 0;
    border-radius: 6px 6px 0 0;
    letter-spacing: 1px;
    margin-bottom: 4px;
}
.lg-label {
    font-size: 0.78rem;
    font-weight: 700;
    padding: 2px 0 4px 4px;
    margin: 8px 0 3px 0;
}
.match-card {
    border: 1px solid #ddd;
    border-radius: 6px;
    margin-bottom: 4px;
    overflow: hidden;
    background: #fff;
}
.match-body {
    display: flex;
    align-items: stretch;
}
.team-left {
    flex: 3;
    padding: 5px 5px 3px 8px;
}
.team-right {
    flex: 3;
    padding: 5px 8px 3px 5px;
    text-align: right;
}
.score-box {
    flex: 0 0 30px;
    background: #f0f0f0;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 1rem;
    font-weight: 800;
    color: #222;
}
.vs-box {
    flex: 0 0 20px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 0.65rem;
    color: #aaa;
}
.player-name {
    font-size: 0.8rem;
    font-weight: 600;
    color: #222;
    line-height: 1.3;
}
.player-win { color: #b71c1c !important; }
.match-footer {
    background: #fafafa;
    font-size: 0.68rem;
    color: #999;
    text-align: right;
    padding: 2px 8px;
}
</style>
""", unsafe_allow_html=True)

# ── 사이드바 네비게이션 ──────────────────────────────────────
st.sidebar.markdown("## 🎾 TELA CLUB")
st.sidebar.markdown("---")

page = st.sidebar.radio(
    "메뉴",
    ["📊 점수판", "🎲 랜덤페어"],
    index=0,
    label_visibility="collapsed",
)
st.sidebar.markdown("---")


# ============================================================
# 페이지 A: 점수판
# ============================================================

if page == "📊 점수판":

    st.markdown("## 🎾 TELA 테니스 클럽 랜덤페어 점수판")

    # ── 날짜 선택 / 수기 입력 ──────────────────────────────
    saved_dates = shelf_list_dates()
    today_str   = date.today().strftime("%Y-%m-%d")

    col_date1, col_date2 = st.columns([2, 3])
    with col_date1:
        date_mode = st.radio("날짜 방식", ["오늘 날짜", "직접 입력", "저장된 날짜 불러오기"],
                              index=0, horizontal=False, label_visibility="collapsed")
    with col_date2:
        if date_mode == "오늘 날짜":
            selected_date = today_str
            st.markdown(f"**📅 {selected_date}**")
        elif date_mode == "직접 입력":
            selected_date = st.text_input("날짜 입력 (YYYY-MM-DD)", value=today_str)
        else:
            if saved_dates:
                selected_date = st.selectbox("저장된 날짜 선택", saved_dates)
            else:
                st.info("저장된 데이터가 없습니다.")
                selected_date = today_str

    st.caption(f"현재 날짜 키: **{selected_date}**")

    # ── 해당 날짜 데이터 로드 ──────────────────────────────
    loaded = shelf_load(selected_date)

    # session_state 동기화
    if "sb_date" not in st.session_state or st.session_state["sb_date"] != selected_date:
        st.session_state["sb_date"] = selected_date
        if loaded:
            st.session_state["sb_schedule"] = deserialize_schedule(loaded["schedule"])
            st.session_state["sb_scores"]   = loaded["scores"]
        else:
            # 랜덤페어에서 생성된 것 있으면 가져오기
            if "schedule" in st.session_state:
                st.session_state["sb_schedule"] = st.session_state["schedule"]
                st.session_state["sb_scores"]   = {}
            else:
                st.session_state["sb_schedule"] = None
                st.session_state["sb_scores"]   = {}

    schedule = st.session_state.get("sb_schedule", None)

    if not schedule:
        st.warning("⚠️ 이 날짜에 저장된 대진표가 없습니다.")
        st.info("👈 **🎲 랜덤페어**에서 대진표를 생성하거나, 저장된 날짜를 선택해주세요.")
        st.stop()

    scores = st.session_state.get("sb_scores", {})

    # ── 라운드 목록 ────────────────────────────────────────
    rounds = []
    seen_r = set()
    for m in schedule:
        r = m["round"]
        if r not in seen_r: rounds.append(r); seen_r.add(r)

    # ── 날짜 표시 ──────────────────────────────────────────
    display_date = selected_date.replace("-","년 ",1).replace("-","월 ")+"일"
    st.markdown(f'<div style="text-align:right;font-size:0.85rem;color:#666;margin-bottom:12px;">{display_date}</div>', unsafe_allow_html=True)

    # ── session_state key 초기화 ──────────────────────────────
    # sb_scores에 저장된 값이 있으면 항상 그걸로 덮어씀 (수정 저장 후 반영 보장)
    for i, match in enumerate(schedule):
        saved_sc = st.session_state["sb_scores"].get(str(i), {})
        if saved_sc:
            # 저장된 값이 있으면 항상 최신 저장값으로 강제 동기화
            st.session_state[f"sc1_{i}"] = saved_sc.get("score1", 0)
            st.session_state[f"sc2_{i}"] = saved_sc.get("score2", 0)
        else:
            if f"sc1_{i}" not in st.session_state:
                st.session_state[f"sc1_{i}"] = 0
            if f"sc2_{i}" not in st.session_state:
                st.session_state[f"sc2_{i}"] = 0

    # ── 저장 트리거 수신 ────────────────────────────────────
    qp = st.query_params
    save_trigger = qp.get("save_idx", None)
    if save_trigger is not None:
        try:
            sidx = int(save_trigger)
            # URL 파라미터의 s1/s2를 최우선 적용 (수정 후 저장 포함)
            s1v = int(qp.get("s1", 0))
            s2v = int(qp.get("s2", 0))
            # session_state 키를 항상 최신값으로 강제 갱신 (기존 키 삭제 후 재설정)
            st.session_state.pop(f"sc1_{sidx}", None)
            st.session_state.pop(f"sc2_{sidx}", None)
            st.session_state[f"sc1_{sidx}"] = s1v
            st.session_state[f"sc2_{sidx}"] = s2v
            st.session_state["sb_scores"][str(sidx)] = {"score1": s1v, "score2": s2v}
            shelf_save(
                selected_date,
                serialize_schedule(st.session_state["sb_schedule"]),
                st.session_state["sb_scores"],
            )
            st.query_params.clear()
            st.rerun()
        except Exception as e:
            st.query_params.clear()

    # ── 전체 점수판 HTML 빌드 ───────────────────────────────
    # 모든 라운드를 순수 HTML+JS로 렌더링 (Streamlit 컬럼 미사용)
    # → 모바일 포함 항상 가로 배치 보장

    def build_scoreboard_html(schedule, rounds, session_state, selected_date):
        import json as _json
        matches_data = []
        for idx, match in enumerate(schedule):
            s1 = session_state.get(f"sc1_{idx}", 0)
            s2 = session_state.get(f"sc2_{idx}", 0)
            # 저장된 점수가 있으면(score1 키 존재) locked 상태
            saved = str(idx) in session_state.get("sb_scores", {})
            matches_data.append({
                "idx":    idx,
                "round":  match["round"],
                "league": match["league"],
                "t1a":    pname(match["team1"][0]),
                "t1b":    pname(match["team1"][1]),
                "t2a":    pname(match["team2"][0]),
                "t2b":    pname(match["team2"][1]),
                "type":   match["type"],
                "s1":     s1,
                "s2":     s2,
                "saved":  saved,
            })

        matches_json = _json.dumps(matches_data, ensure_ascii=False)
        rounds_json  = _json.dumps(rounds, ensure_ascii=False)

        html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<style>
*{{box-sizing:border-box;margin:0;padding:0;}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
      background:#f5f5f5;padding:6px;}}

/* 라운드 블록 */
.rnd-block{{margin-bottom:10px;background:#fff;border-radius:8px;
            overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,.08);}}
.rnd-hdr{{background:#1a1a2e;color:#fff;font-weight:700;font-size:0.92rem;
           text-align:center;padding:8px 4px;letter-spacing:1px;}}

/* 리그 섹션 */
.lg-section{{padding:6px 8px 4px;}}
.lg-lbl{{font-size:0.75rem;font-weight:700;padding:2px 0 4px 6px;margin-bottom:4px;}}

/* 경기 1열 */
.match-list{{display:flex;flex-direction:column;gap:6px;}}

/* 경기 카드 */
.mc{{border:1px solid #e0e0e0;border-radius:6px;overflow:hidden;background:#fff;}}
.mc-body{{display:flex;align-items:stretch;}}
.mc-team-l{{flex:3;padding:5px 2px 3px 7px;min-width:0;}}
.mc-team-r{{flex:3;padding:5px 7px 3px 2px;text-align:right;min-width:0;}}
.mc-score{{flex:0 0 26px;background:#f0f0f0;display:flex;align-items:center;
            justify-content:center;font-size:0.95rem;font-weight:800;color:#222;}}
.mc-vs{{flex:0 0 16px;display:flex;align-items:center;justify-content:center;
         font-size:0.6rem;color:#bbb;}}
.mc-ft{{background:#fafafa;font-size:0.62rem;color:#aaa;text-align:right;padding:2px 7px;}}
.pn{{font-size:0.78rem;font-weight:600;color:#222;line-height:1.4;
      overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}}
.pw{{color:#b71c1c!important;font-weight:800!important;}}

/* 저장된 카드 배경 */
.mc.saved-card{{background:#f9fff9;border-color:#a5d6a7;}}

/* 입력행 */
.inp-row{{display:flex;align-items:center;gap:3px;padding:4px 4px 5px;}}
.inp-box{{flex:1;display:flex;align-items:center;border:1px solid #ccc;
           border-radius:5px;overflow:hidden;background:#f8f8f8;height:36px;min-width:0;}}
.inp-box.locked{{background:#eee;border-color:#ddd;}}

/* - + 버튼 */
.inp-btn{{width:30px;height:36px;border:none;background:#e8e8e8;font-size:1.05rem;
           font-weight:700;cursor:pointer;color:#333;flex-shrink:0;
           display:flex;align-items:center;justify-content:center;
           -webkit-tap-highlight-color:transparent;touch-action:manipulation;}}
.inp-btn:active{{background:#d0d0d0;}}
.inp-btn:disabled{{color:#bbb;cursor:default;background:#eee;}}
.inp-btn.hidden{{visibility:hidden;}}

/* 점수 숫자 표시 */
.inp-num{{flex:1;text-align:center;font-size:1rem;font-weight:700;
           border:none;background:transparent;width:100%;
           -moz-appearance:textfield;}}
.inp-num::-webkit-inner-spin-button,
.inp-num::-webkit-outer-spin-button{{-webkit-appearance:none;}}
.inp-num:disabled{{color:#333;}}

/* 저장 버튼 */
.save-btn{{flex:0 0 54px;height:36px;background:#e53935;color:#fff;border:none;
            border-radius:5px;font-size:0.82rem;font-weight:700;cursor:pointer;
            -webkit-tap-highlight-color:transparent;touch-action:manipulation;}}
.save-btn:active{{background:#b71c1c;}}

/* 수정 버튼 */
.edit-btn{{flex:0 0 54px;height:36px;background:#1565c0;color:#fff;border:none;
            border-radius:5px;font-size:0.82rem;font-weight:700;cursor:pointer;
            -webkit-tap-highlight-color:transparent;touch-action:manipulation;}}
.edit-btn:active{{background:#0d47a1;}}

/* 저장완료 배지 */
.saved-badge{{
    font-size:0.68rem;color:#2e7d32;font-weight:700;
    text-align:center;padding:2px 0 3px;
    display:flex;align-items:center;justify-content:center;gap:3px;
}}
</style>
</head><body>
<div id="root"></div>
<script>
(function(){{
  const matches = {matches_json};
  const rounds  = {rounds_json};
  const MAX = 6, MIN = 0;

  // 로컬 점수 상태
  const scores = {{}};
  // 잠금 상태 (저장된 경기)
  const locked = {{}};

  matches.forEach(m => {{
    scores[m.idx] = {{s1: m.s1, s2: m.s2}};
    locked[m.idx] = m.saved;
  }});

  function pWin(a,b){{ return (a+b)>0 && a>b; }}

  function render(){{
    const root = document.getElementById('root');
    root.innerHTML = '';
    rounds.forEach(rnd => {{
      const rndMs = matches.filter(m => m.round === rnd);
      if(!rndMs.length) return;
      const block = document.createElement('div');
      block.className = 'rnd-block';
      const lbl = rnd.replace('(이벤트)','') + (rnd.includes('이벤트')?' ⭐':'');
      block.innerHTML = `<div class="rnd-hdr">${{lbl}}</div>`;
      const leagues = [...new Set(rndMs.map(m=>m.league))];
      leagues.forEach(lg => {{
        const lgColor = lg.includes('A') ? '#2e7d32' : '#1565c0';
        const sec = document.createElement('div');
        sec.className = 'lg-section';
        const lblDiv = document.createElement('div');
        lblDiv.className = 'lg-lbl';
        lblDiv.style.cssText = `color:${{lgColor}};border-left:3px solid ${{lgColor}};padding-left:6px;`;
        lblDiv.textContent = lg;
        sec.appendChild(lblDiv);
        const list = document.createElement('div');
        list.className = 'match-list';
        rndMs.filter(m=>m.league===lg).forEach(m => {{
          list.appendChild(buildMatch(m, lgColor));
        }});
        sec.appendChild(list);
        block.appendChild(sec);
      }});
      root.appendChild(block);
    }});
    notifyHeight();
  }}

  function buildMatch(m, lgColor){{
    const wrap = document.createElement('div');
    wrap.id = 'wrap_' + m.idx;
    refreshMatch(m, lgColor, wrap);
    return wrap;
  }}

  function refreshMatch(m, lgColor, wrap){{
    const sc  = scores[m.idx];
    const lk  = locked[m.idx];
    const t1w = pWin(sc.s1, sc.s2);
    const t2w = pWin(sc.s2, sc.s1);
    const savedCls = lk ? ' saved-card' : '';
    const btnHide  = lk ? ' hidden' : '';

    wrap.innerHTML = `
<div class="mc${{savedCls}}" style="border-left:4px solid ${{lgColor}}">
  <div class="mc-body">
    <div class="mc-team-l">
      <div class="pn${{t1w?' pw':''}}">${{m.t1a}}</div>
      <div class="pn${{t1w?' pw':''}}">${{m.t1b}}</div>
    </div>
    <div class="mc-score" id="d1_${{m.idx}}">${{sc.s1}}</div>
    <div class="mc-vs">vs</div>
    <div class="mc-score" id="d2_${{m.idx}}">${{sc.s2}}</div>
    <div class="mc-team-r">
      <div class="pn${{t2w?' pw':''}}">${{m.t2a}}</div>
      <div class="pn${{t2w?' pw':''}}">${{m.t2b}}</div>
    </div>
  </div>
  <div class="mc-ft">${{m.type}}</div>
</div>
<div class="inp-row">
  <div class="inp-box${{lk?' locked':''}}">
    <button class="inp-btn${{btnHide}}" ${{lk?'disabled':''}}
            ontouchend="ev(event,()=>adj(${{m.idx}},1,-1))"
            onclick="adj(${{m.idx}},1,-1)">−</button>
    <input  class="inp-num" type="number" id="i1_${{m.idx}}"
            value="${{sc.s1}}" min="${{MIN}}" max="${{MAX}}"
            ${{lk?'disabled':''}}
            oninput="onInp(${{m.idx}},1,this.value)">
    <button class="inp-btn${{btnHide}}" ${{lk?'disabled':''}}
            ontouchend="ev(event,()=>adj(${{m.idx}},1,1))"
            onclick="adj(${{m.idx}},1,1)">+</button>
  </div>
  ${{lk
    ? `<button class="edit-btn" onclick="doEdit(${{m.idx}})">수정</button>`
    : `<button class="save-btn" onclick="doSave(${{m.idx}})">저장</button>`
  }}
  <div class="inp-box${{lk?' locked':''}}">
    <button class="inp-btn${{btnHide}}" ${{lk?'disabled':''}}
            ontouchend="ev(event,()=>adj(${{m.idx}},2,-1))"
            onclick="adj(${{m.idx}},2,-1)">−</button>
    <input  class="inp-num" type="number" id="i2_${{m.idx}}"
            value="${{sc.s2}}" min="${{MIN}}" max="${{MAX}}"
            ${{lk?'disabled':''}}
            oninput="onInp(${{m.idx}},2,this.value)">
    <button class="inp-btn${{btnHide}}" ${{lk?'disabled':''}}
            ontouchend="ev(event,()=>adj(${{m.idx}},2,1))"
            onclick="adj(${{m.idx}},2,1)">+</button>
  </div>
</div>
${{lk ? '<div class="saved-badge">✅ 저장완료</div>' : ''}}`;
  }}

  // touchend 중복 방지 (click도 발생하므로)
  function ev(e, fn){{ e.preventDefault(); fn(); }}

  window.adj = function(idx,team,delta){{
    if(locked[idx]) return;
    const el = document.getElementById((team===1?'i1_':'i2_')+idx);
    let v = parseInt(el.value||'0') + delta;
    if(v<MIN) v=MIN; if(v>MAX) v=MAX;
    el.value = v;
    scores[idx][team===1?'s1':'s2'] = v;
    document.getElementById((team===1?'d1_':'d2_')+idx).textContent = v;
  }};

  window.onInp = function(idx,team,val){{
    if(locked[idx]) return;
    let v = parseInt(val)||0;
    if(v<MIN) v=MIN; if(v>MAX) v=MAX;
    scores[idx][team===1?'s1':'s2'] = v;
    document.getElementById((team===1?'d1_':'d2_')+idx).textContent = v;
  }};

  window.doSave = function(idx){{
    const s1 = scores[idx].s1;
    const s2 = scores[idx].s2;
    const url = new URL(window.location.href);
    url.searchParams.set('save_idx', idx);
    url.searchParams.set('s1', s1);
    url.searchParams.set('s2', s2);
    window.location.href = url.toString();
  }};

  window.doEdit = function(idx){{
    locked[idx] = false;
    // 해당 wrap만 다시 그리기
    const wrap = document.getElementById('wrap_'+idx);
    const m = matches.find(x=>x.idx===idx);
    // lgColor 재계산
    const lgColor = m.league.includes('A') ? '#2e7d32' : '#1565c0';
    refreshMatch(m, lgColor, wrap);
    notifyHeight();
  }};

  function notifyHeight(){{
    setTimeout(()=>{{
      const h = document.documentElement.scrollHeight;
      window.parent.postMessage({{type:'streamlit:setFrameHeight',height:h+10}},'*');
    }}, 100);
  }}

  render();
}})();
</script>
</body></html>"""
        return html

    sb_html = build_scoreboard_html(schedule, rounds, st.session_state, selected_date)
    n_matches = len(schedule)
    # 경기당 약 160px (카드+입력행+배지+여백) + 라운드헤더/리그헤더 여유분
    # scrolling=True로 내부 스크롤 대신 충분한 높이 확보
    est_height = 300 + n_matches * 160
    st.components.v1.html(sb_html, height=est_height, scrolling=True)

    # ── 전체 초기화 버튼 ─────────────────────────────────────
    st.markdown("---")
    if st.button("🔄 점수 전체 초기화", type="secondary"):
        # session_state의 sc1_, sc2_ 키도 초기화
        for idx in range(len(schedule)):
            st.session_state.pop(f"sc1_{idx}", None)
            st.session_state.pop(f"sc2_{idx}", None)
        st.session_state["sb_scores"] = {}
        st.rerun()

    # ── 통계 ───────────────────────────────────────────────
    st.markdown("### 📈 선수별 통계")
    df_sb = compute_scoreboard_stats(schedule, st.session_state.get("sb_scores",{}))

    if df_sb.empty:
        st.info("점수를 입력하면 통계가 표시됩니다.")
    else:
        for league in ["A리그", "B리그"]:
            df_lg = df_sb[df_sb["리그"]==league].drop(columns=["리그"]).reset_index(drop=True)
            if df_lg.empty: continue
            lg_color = "#2e7d32" if "A" in league else "#1565c0"
            st.markdown(
                f'<div style="color:{lg_color};font-weight:700;border-bottom:2px solid {lg_color};'
                f'padding-bottom:4px;margin:16px 0 8px 0;">🎾 {league} 통계</div>',
                unsafe_allow_html=True
            )
            max_win = int(df_lg["승"].max()) if not df_lg.empty else 0
            def hl_sb(row, mw=max_win):
                styles = [""]*len(row)
                if "승" in row.index:
                    wi = row.index.get_loc("승")
                    if row["승"]==mw and mw>0: styles[wi]="background-color:#FFF176;font-weight:bold"
                return styles
            st.dataframe(df_lg.style.apply(hl_sb,axis=1), use_container_width=True, hide_index=True)


# ============================================================
# 페이지 B: 랜덤페어
# ============================================================

elif page == "🎲 랜덤페어":
    st.title("🎾 TELA CLUB Random Match Generator v2.06")
    st.markdown(
        "**A리그:** 동성복 → 혼복 → 잡복 &nbsp;|&nbsp; "
        "**B리그:** 혼복(≤2회) → 동성복(≥1회) → 잡복 &nbsp;|&nbsp; "
        "**최소 3경기 보장 / 최대 4경기 제한**"
    )

    input_mode = st.sidebar.radio("입력 방식", ["코드 자동 생성 (AM01/AW01...)", "직접 이름 입력"], index=0)
    st.sidebar.markdown("---")

    if input_mode == "코드 자동 생성 (AM01/AW01...)":
        c1, c2 = st.sidebar.columns(2)
        with c1:
            am = st.number_input("A리그 남자", min_value=0, max_value=30, value=8, step=1)
            aw = st.number_input("A리그 여자", min_value=0, max_value=30, value=2, step=1)
        with c2:
            bm = st.number_input("B리그 남자", min_value=0, max_value=30, value=3, step=1)
            bw = st.number_input("B리그 여자", min_value=0, max_value=30, value=2, step=1)
        custom_input = None
    else:
        st.sidebar.markdown("각 줄에 `이름 성별` 입력  \n성별: `남` 또는 `여` (생략 시 남자)")
        a_input = st.sidebar.text_area("A리그 선수 목록", placeholder="홍길동 남\n김영희 여", height=150)
        b_input = st.sidebar.text_area("B리그 선수 목록", placeholder="박보검 남\n아이유 여", height=120)
        custom_input = {"A": a_input, "B": b_input}
        am = aw = bm = bw = 0

    st.sidebar.markdown("---")
    use_seed = st.sidebar.checkbox("🔒 결과 고정 (시드)", value=False)
    seed_val = None
    if use_seed:
        seed_val = st.sidebar.number_input("시드 번호", min_value=0, max_value=9999, value=42, step=1)

    # ── 관리자 비밀번호 ────────────────────────────────────
    st.sidebar.markdown("---")
    st.sidebar.markdown("🔐 **관리자 확인**")
    admin_pw = st.sidebar.text_input("비밀번호", type="password", placeholder="비밀번호 입력")

    pw_ok = (admin_pw == ADMIN_PASSWORD)

    generate_btn = st.sidebar.button(
        "🎾 대진표 생성",
        type="primary",
        use_container_width=True,
        disabled=not pw_ok,
    )

    if admin_pw and not pw_ok:
        st.sidebar.error("❌ 비밀번호가 틀렸습니다.")
    elif not admin_pw:
        st.sidebar.caption("비밀번호를 입력해야 생성할 수 있습니다.")

    if generate_btn and pw_ok:
        if custom_input is not None:
            league_players = {
                "A리그": parse_custom_players(custom_input["A"],"A") if custom_input["A"].strip() else [],
                "B리그": parse_custom_players(custom_input["B"],"B") if custom_input["B"].strip() else [],
            }
        else:
            league_players = {
                "A리그": [f"AM{i+1:02d}" for i in range(am)]+[f"AW{i+1:02d}" for i in range(aw)],
                "B리그": [f"BM{i+1:02d}" for i in range(bm)]+[f"BW{i+1:02d}" for i in range(bw)],
            }

        errors = []
        for lg, pl in league_players.items():
            if 0<len(pl)<4: errors.append(f"{lg} 인원이 4명 미만입니다.")
        if not any(len(pl)>=4 for pl in league_players.values()):
            errors.append("최소 한 리그에 4명 이상 입력해주세요.")

        if errors:
            for e in errors: st.error(e)
            st.stop()

        if use_seed and seed_val is not None:
            random.seed(int(seed_val))

        with st.spinner("대진표 생성 중..."):
            schedule, stats = generate_schedule_from_leagues(league_players)

        if not schedule:
            st.warning("경기를 생성할 수 없습니다."); st.stop()

        # session_state 저장 (점수판에서 활용)
        st.session_state["schedule"]    = schedule
        st.session_state["stats"]       = stats
        st.session_state["scores"]      = {}
        st.session_state["sb_schedule"] = schedule
        st.session_state["sb_scores"]   = {}
        st.session_state["sb_date"]     = ""   # 날짜 동기화 리셋

        seed_label = f"시드 #{int(seed_val)}" if (use_seed and seed_val is not None) else "시드 없음(랜덤)"

        def dn(code): return display_name(code)

        df_matches = pd.DataFrame([{
            "라운드": d["round"], "리그": d["league"],
            "팀1-A": dn(d["team1"][0]), "팀1-B": dn(d["team1"][1]),
            "팀2-A": dn(d["team2"][0]), "팀2-B": dn(d["team2"][1]),
            "매치종류": d["type"],
        } for d in schedule])

        df_full    = stats_to_df(stats)
        df_display = df_full.drop(columns=["_코드"])

        tab1, tab2, tab3 = st.tabs(["📋 대진표", "📊 출전 현황", "🔍 검증 리포트"])

        with tab1:
            st.subheader(f"경기 대진표 · {seed_label}")
            def hl_match(row):
                bg = "#E8F5E9" if "A리그" in str(row.get("리그","")) else "#E3F2FD"
                return [f"background-color:{bg};color:black"]*len(row)
            st.dataframe(df_matches.style.apply(hl_match,axis=1), use_container_width=True, height=600)
            summary = df_matches["매치종류"].value_counts()
            st.caption(f"총 {len(df_matches)}경기 | "+" | ".join(f"{k}: {v}경기" for k,v in summary.items()))
            st.info("💡 대진표 생성 후 사이드바에서 **📊 점수판**을 선택하면 점수를 입력할 수 있습니다.")

        with tab2:
            st.subheader("선수별 출전 현황")
            palette = {"AM":"#E8F5E9","AW":"#FCE4EC","BM":"#E3F2FD","BW":"#FFF3E0"}
            def hl_stats(row):
                code = df_full.loc[row.name,"_코드"] if "_코드" in df_full.columns else ""
                bg   = palette.get(code[:2],"")
                base = f"background-color:{bg};color:black" if bg else ""
                styles = [base]*len(row)
                if "총경기" in row.index:
                    idx2  = row.index.get_loc("총경기"); total = row["총경기"]
                    if total>=4: styles[idx2]="background-color:#FFF176;color:black;font-weight:bold"
                    elif total<3: styles[idx2]="background-color:#FFCDD2;color:black;font-weight:bold"
                    else: styles[idx2]=base+";font-weight:bold"
                return styles
            st.dataframe(df_display.style.apply(hl_stats,axis=1), use_container_width=True, height=700)

        with tab3:
            st.subheader("🔍 자동 검증 리포트")
            issues, warns = [], []
            if not df_full.empty:
                under3 = df_full[df_full["총경기"]<3]
                if not under3.empty: issues.append(f"❌ 3경기 미달 {len(under3)}명: {', '.join(under3['이름'].tolist())}")
                else: st.success("✅ 모든 선수 3경기 이상")
                over4 = df_full[df_full["총경기"]>4]
                if not over4.empty: issues.append(f"❌ 4경기 초과 {len(over4)}명: {', '.join(over4['이름'].tolist())}")
                else: st.success("✅ 4경기 초과 없음")
                st.markdown("**매치 종류 분포**")
                td = df_matches["매치종류"].value_counts().reset_index()
                td.columns=["매치종류","경기수"]; st.dataframe(td, use_container_width=False)
                if len(df_full)>1:
                    std_m=df_full["혼성합계"].std(); mean_m=df_full["혼성합계"].mean()
                    if std_m>1.5: warns.append(f"⚠️ 혼성 편차 큼 (평균 {mean_m:.1f}회, σ={std_m:.2f})")
                    else: st.success(f"✅ 혼성 균등 분배 (평균 {mean_m:.1f}회, σ={std_m:.2f})")
                b_rows = df_full[df_full["리그"]=="B리그"]
                if not b_rows.empty:
                    st.markdown("**B리그 쿼터 현황** (혼성≤2회, 동성≥1회)")
                    quota_rows = []
                    for _, row in b_rows.iterrows():
                        dong=row["남복"]+row["여복"]; mc=row["혼성합계"]
                        quota_rows.append({"이름":row["이름"],"혼성":mc,"동성":dong,
                                           "혼성쿼터":"✅" if mc<=2 else "❌","동성쿼터":"✅" if dong>=1 else "⚠️"})
                    st.dataframe(pd.DataFrame(quota_rows), use_container_width=False)
            for i in issues: st.error(i)
            for w in warns:  st.warning(w)
            if not issues and not warns: st.info("🎾 모든 검증 통과!")

        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
            df_matches.to_excel(writer, sheet_name="대진표", index=False)
            df_display.to_excel(writer, sheet_name="출전현황", index=False)
            for sn in ["대진표","출전현황"]: writer.sheets[sn].set_column("A:Z",14)
        excel_tag = f"_시드{int(seed_val)}" if (use_seed and seed_val is not None) else "_랜덤"
        st.sidebar.markdown("---")
        st.sidebar.download_button(
            label="📥 엑셀 다운로드", data=buf.getvalue(),
            file_name=f"TELA_대진표{excel_tag}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )

    else:
        if not generate_btn:
            st.info("👈 사이드바에서 인원을 설정하고 비밀번호 입력 후 **대진표 생성** 버튼을 눌러주세요.")
            with st.expander("📖 사용 방법 및 규칙 안내"):
                st.markdown("""
                ### 입력 방식
                | 방식 | 설명 |
                |------|------|
                | 코드 자동 생성 | 인원 수만 입력 (AM01, AW01, BM01 등) |
                | 직접 이름 입력 | `이름 성별` 형식 (예: `홍길동 남`, `김영희 여`) |

                ### 매치 우선순위
                | 리그 | 1순위 | 2순위 | 3순위 |
                |------|-------|-------|-------|
                | A리그 | 동성복 (남복/여복) | 혼복 | 잡복 |
                | B리그 | 혼복 | 동성복 | 잡복 |

                ### 출전 규칙
                - **최소 3경기** 보장 → 이벤트 라운드(4R)로 보충
                - **최대 4경기** 제한
                - **관리자 비밀번호** 필요 → 사이드바 하단 입력

                ### 점수판
                1. 대진표 생성 후 사이드바에서 **📊 점수판** 선택
                2. 날짜 선택 또는 직접 입력
                3. 각 경기 점수 입력 후 경기 카드 하단 **💾 저장** 버튼 클릭
                4. 새로고침 후에도 날짜별로 저장 유지
                """)
