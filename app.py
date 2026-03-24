"""
TELA CLUB Random Match Generator v2.0
======================================
핵심 규칙:
  1. 1인 최소 3경기 보장 / 최대 4경기 제한
  2. A리그 매치 우선순위: 동성복(남복/여복) > 혼복 > 잡복
     B리그 매치 우선순위: 혼복 > 동성복 > 잡복
  3. 혼성 자리 → 혼성 경기 최소 참여자 우선 선발
  4. B리그 개인 쿼터: 혼복(혼성) 최대 2회 / 동성복 최소 1회
     (쿼터는 선처리 단계에서 적용; 구조상 불가능한 인원 구성은 최대한 근접)
"""

import streamlit as st
import pandas as pd
import random
import io
from dataclasses import dataclass, field
from itertools import zip_longest
from typing import Dict, FrozenSet, List, Optional, Set, Tuple


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

def score_pairing(t1: Tuple, t2: Tuple, gs: MatchState, rs: MatchState) -> int:
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

def best_pairing(players4: List[str], gs: MatchState, rs: MatchState) -> Tuple[Tuple, Tuple]:
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

def commit_pairing(t1: Tuple, t2: Tuple, gs: MatchState, rs: MatchState):
    for state in (gs, rs):
        state.teammate_used.add(team_key(t1))
        state.teammate_used.add(team_key(t2))
        for x in t1:
            for y in t2: state.opponent_used.add(opp_key(x, y))
        for t in (t1, t2):
            mk = mixed_partner_key(t)
            if mk: state.mixed_partner_used.add(mk)


# ============================================================
# 섹션 4: 리그별 우선순위 & 쿼터 설정
# ============================================================

LEAGUE_PRIORITY: Dict[str, List[str]] = {
    "A리그": ["동성", "혼복", "잡복"],
    "B리그": ["혼복", "동성", "잡복"],
}
DEFAULT_PRIORITY = ["동성", "혼복", "잡복"]

def get_priority(league: str) -> List[str]:
    return LEAGUE_PRIORITY.get(league, DEFAULT_PRIORITY)

# B리그 개인 쿼터: 혼성 최대 2회, 동성 최소 1회
LEAGUE_QUOTA: Dict[str, Dict] = {
    "A리그": {"mixed_max": None, "dong_min": None},
    "B리그": {"mixed_max": 2,    "dong_min": 1   },
}

def get_quota(league: str) -> Dict:
    return LEAGUE_QUOTA.get(league, {"mixed_max": None, "dong_min": None})

def mixed_quota_ok(p: str, mixed_counts: Dict[str, int], league: str) -> bool:
    """혼성 쿼터 여유 있으면 True"""
    q = get_quota(league)
    if q["mixed_max"] is None: return True
    return mixed_counts.get(base_name(p), 0) < q["mixed_max"]


# ============================================================
# 섹션 5: 그룹 구성 (기본 - 쿼터 없음)
# ============================================================

def build_one_group(
    pool: List[str],
    mixed_counts: Dict[str, int],
    league: str = "A리그",
) -> Tuple[Optional[List[str]], List[str]]:
    """
    pool[0] = anchor (game_count 최소, 소수성별 우선).
    리그 우선순위에 따라 나머지 3명 선발.
    혼성 자리는 mixed_count 최소 우선.
    """
    if len(pool) < 4:
        return None, pool[:]

    anchor   = pool[0]
    rest     = pool[1:]
    g_a      = get_gender(anchor)
    same     = [p for p in rest if get_gender(p) == g_a]
    opp      = [p for p in rest if get_gender(p) != g_a and get_gender(p) != "U"]
    priority = get_priority(league)

    def try_dongsong():
        if len(same) >= 3:
            sh = list(same); random.shuffle(sh)
            return [anchor] + sh[:3]
        return None

    def try_mixed():
        if len(same) >= 1 and len(opp) >= 2:
            sh = list(same); random.shuffle(sh)
            return [anchor] + sh[:1] + sort_by_mixed_least(opp, mixed_counts)[:2]
        return None

    def try_jabbok():
        if len(same) >= 2 and len(opp) >= 1:
            sh = list(same); random.shuffle(sh)
            return [anchor] + sh[:2] + sort_by_mixed_least(opp, mixed_counts)[:1]
        if len(opp) >= 3:
            return [anchor] + sort_by_mixed_least(opp, mixed_counts)[:3]
        return None

    dispatch = {"동성": try_dongsong, "혼복": try_mixed, "잡복": try_jabbok}

    group = None
    for ptype in priority:
        result = dispatch[ptype]()
        if result is not None:
            group = result; break

    if group is None:
        others = sort_by_mixed_least(rest, mixed_counts)
        group  = [anchor] + others[:3]

    if group is None or len(group) < 4:
        return None, pool[:]

    remaining = list(pool)
    for p in group:
        if p in remaining: remaining.remove(p)
    return group, remaining


def build_all_groups(
    pool: List[str],
    mixed_counts: Dict[str, int],
    league: str = "A리그",
) -> Tuple[List[List[str]], List[str]]:
    """pool 전체를 4명 그룹으로 분할."""
    groups, remaining = [], list(pool)
    while len(remaining) >= 4:
        group, remaining = build_one_group(remaining, mixed_counts, league)
        if group is None: break
        groups.append(group)
    return groups, remaining


# ============================================================
# 섹션 6: 정규 라운드 매치 생성
# ============================================================

def _pick_3_for_anchor(
    anchor: str,
    remaining: List[str],
    mixed_counts: Dict[str, int],
    league: str = "A리그",
) -> Optional[List[str]]:
    """anchor에 맞춰 나머지 3명 선발 (리그 우선순위 + 쿼터 적용)."""
    if len(remaining) < 3: return None

    g        = get_gender(anchor)
    men      = [p for p in remaining if get_gender(p) == "M"]
    women    = [p for p in remaining if get_gender(p) == "W"]
    priority = get_priority(league)

    # 혼성 쿼터 여유 있는 이성 선수 필터 (쿼터 없는 리그는 전체 사용)
    if g == "M":
        opp_quota = [p for p in women if mixed_quota_ok(p, mixed_counts, league)]
        opp_all   = women
    else:
        opp_quota = [p for p in men   if mixed_quota_ok(p, mixed_counts, league)]
        opp_all   = men
    anchor_ok = mixed_quota_ok(anchor, mixed_counts, league)

    def try_dongsong():
        if g == "M" and len(men) >= 3:   return men[:3]
        if g == "W" and len(women) >= 3: return women[:3]
        return None

    def try_mixed():
        # anchor 쿼터 초과면 혼복 불가
        if not anchor_ok: return None
        # 이성 파트너도 쿼터 여유 있는 선수만 사용
        opp_use = opp_quota if len(opp_quota) >= 2 else []
        if not opp_use: return None  # 쿼터 여유 이성 < 2명이면 혼복 불가
        if g == "M":
            m_use = [p for p in men if mixed_quota_ok(p, mixed_counts, league)]
            if len(m_use) >= 1:
                return sort_by_mixed_least(m_use, mixed_counts)[:1] + sort_by_mixed_least(opp_use, mixed_counts)[:2]
            # same 쿼터 여유 없어도 이성이 있으면 잡복 방향으로
            return None
        else:
            w_use = [p for p in women if mixed_quota_ok(p, mixed_counts, league)]
            if len(w_use) >= 1:
                return sort_by_mixed_least(w_use, mixed_counts)[:1] + sort_by_mixed_least(opp_use, mixed_counts)[:2]
            return None

    def try_jabbok():
        # 잡복도 혼성 → 쿼터 여유 있는 이성만 사용 (없으면 전체)
        opp_use = opp_quota if opp_quota else opp_all
        if g == "M":
            if len(men) >= 2 and len(opp_use) >= 1:
                m2 = list(men); random.shuffle(m2)
                return m2[:2] + sort_by_mixed_least(opp_use, mixed_counts)[:1]
            if len(opp_use) >= 3:
                return sort_by_mixed_least(opp_use, mixed_counts)[:3]
        elif g == "W":
            if len(women) >= 2 and len(opp_use) >= 1:
                w2 = list(women); random.shuffle(w2)
                return w2[:2] + sort_by_mixed_least(opp_use, mixed_counts)[:1]
            if len(opp_use) >= 3:
                return sort_by_mixed_least(opp_use, mixed_counts)[:3]
        return None

    dispatch = {"동성": try_dongsong, "혼복": try_mixed, "잡복": try_jabbok}
    for ptype in priority:
        result = dispatch[ptype]()
        if result is not None: return result

    # fallback: 쿼터 무시하고 재시도 (3~4회 보장 우선)
    def try_mixed_fallback():
        if g == "M" and len(men) >= 1 and len(women) >= 2:
            return sort_by_mixed_least(men, mixed_counts)[:1] + sort_by_mixed_least(women, mixed_counts)[:2]
        if g == "W" and len(women) >= 1 and len(men) >= 2:
            return sort_by_mixed_least(women, mixed_counts)[:1] + sort_by_mixed_least(men, mixed_counts)[:2]
        return None
    def try_jabbok_fallback():
        if g == "M":
            if len(men) >= 2 and len(women) >= 1:
                m2 = list(men); random.shuffle(m2)
                return m2[:2] + sort_by_mixed_least(women, mixed_counts)[:1]
            if len(women) >= 3:
                return sort_by_mixed_least(women, mixed_counts)[:3]
        elif g == "W":
            if len(women) >= 2 and len(men) >= 1:
                w2 = list(women); random.shuffle(w2)
                return w2[:2] + sort_by_mixed_least(men, mixed_counts)[:1]
            if len(men) >= 3:
                return sort_by_mixed_least(men, mixed_counts)[:3]
        return None

    for fn in [try_dongsong, try_mixed_fallback, try_jabbok_fallback]:
        result = fn()
        if result is not None: return result

    rest = sort_by_mixed_least(remaining, mixed_counts)
    return rest[:3] if len(rest) >= 3 else None


def make_round_matches(
    players:      List[str],
    game_counts:  Dict[str, int],
    mixed_counts: Dict[str, int],
    gs: MatchState,
    rs: MatchState,
    league: str = "A리그",
    dong_forced: bool = False,
) -> List[dict]:
    """
    리그 우선순위 + 쿼터를 고려해 그룹 구성 후 매치 생성.

    dong_forced=True: 동성복 가능하면 동성 선처리 우선 (마지막 라운드 등)
    """
    n_groups = len(players) // 4
    if n_groups == 0: return []

    gender_count: Dict[str, int] = {}
    for p in players:
        g = get_gender(p)
        gender_count[g] = gender_count.get(g, 0) + 1

    priority = get_priority(league)

    def sort_key(p):
        return (
            game_counts.get(base_name(p), 0),
            gender_count.get(get_gender(p), 99),
            random.random()
        )

    working   = sorted(players, key=sort_key)
    men_all   = [p for p in working if get_gender(p) == "M"]
    women_all = [p for p in working if get_gender(p) == "W"]
    groups_of_4: List[List[str]] = []

    top_ptype = priority[0]

    # ── 1단계: 1순위 타입 선처리 ─────────────────────────────
    if top_ptype == "동성":
        preprocess_slots = min(max(0, n_groups - 1), len(men_all)//4 + len(women_all)//4)
        men_s   = sorted(men_all,   key=lambda p: (game_counts.get(base_name(p),0), random.random()))
        women_s = sorted(women_all, key=lambda p: (game_counts.get(base_name(p),0), random.random()))
        while len(men_s) >= 4 and len(groups_of_4) < preprocess_slots:
            grp = men_s[:4]; men_s = men_s[4:]
            groups_of_4.append(grp); [working.remove(p) for p in grp]
        while len(women_s) >= 4 and len(groups_of_4) < preprocess_slots:
            grp = women_s[:4]; women_s = women_s[4:]
            groups_of_4.append(grp); [working.remove(p) for p in grp]

    elif top_ptype == "혼복":
        quota_ok_m = [p for p in men_all   if mixed_quota_ok(p, mixed_counts, league)]
        quota_ok_w = [p for p in women_all if mixed_quota_ok(p, mixed_counts, league)]
        max_by_quota = min(len(quota_ok_m)//2, len(quota_ok_w)//2)
        minority_cnt = min(len(men_all), len(women_all))

        # 동성 선처리 조건:
        #   1) 혼복 쿼터 소진 (mixed_possible=False) 또는
        #   2) dong_forced=True (마지막 라운드 동성 보장)
        dong_possible = len(men_all)//4 + len(women_all)//4
        mixed_possible = (max_by_quota > 0 and minority_cnt >= 2)

        if not mixed_possible or dong_forced:
            # 소수성별이 들어갈 그룹 최소 1개 확보 (3~4회 보장 위반 방지)
            import math
            minority_cnt2 = min(len(men_all), len(women_all))
            minority_groups_needed = math.ceil(minority_cnt2 / 4) if minority_cnt2 > 0 else 0
            # dong_forced이면 최대한 동성 선처리 (소수성별은 anchor에서 처리)
            if dong_forced:
                dong_slots = min(dong_possible, n_groups)
            else:
                dong_slots = min(dong_possible, max(0, n_groups - minority_groups_needed))
            men_s   = sorted(men_all,   key=lambda p: (game_counts.get(base_name(p),0), random.random()))
            women_s = sorted(women_all, key=lambda p: (game_counts.get(base_name(p),0), random.random()))
            while len(men_s) >= 4 and len(groups_of_4) < dong_slots:
                grp = men_s[:4]; men_s = men_s[4:]
                groups_of_4.append(grp); [working.remove(p) for p in grp]
            while len(women_s) >= 4 and len(groups_of_4) < dong_slots:
                grp = women_s[:4]; women_s = women_s[4:]
                groups_of_4.append(grp); [working.remove(p) for p in grp]
            # dong_forced이고 선처리가 안 됐으면 anchor 방식에서 동성 우선 적용
            # (이후 anchor 방식에서 dong_forced 플래그를 활용)
        else:
            # 혼복 선처리 (anchor용 슬롯 1개 확보)
            preprocess_slots = min(max(0, n_groups - 1), minority_cnt // 2, max_by_quota)
            while len(groups_of_4) < preprocess_slots:
                men_avail   = [p for p in working if get_gender(p)=="M"
                               and mixed_quota_ok(p, mixed_counts, league)]
                women_avail = [p for p in working if get_gender(p)=="W"
                               and mixed_quota_ok(p, mixed_counts, league)]
                if len(men_avail) < 2 or len(women_avail) < 2: break
                m2 = sort_by_mixed_least(men_avail,   mixed_counts)[:2]
                w2 = sort_by_mixed_least(women_avail, mixed_counts)[:2]
                grp = m2 + w2
                groups_of_4.append(grp); [working.remove(p) for p in grp]

    # ── 2단계: 나머지 → anchor 기반 greedy ───────────────────
    remaining_need = n_groups - len(groups_of_4)
    if remaining_need > 0 and len(working) >= 4:
        wpool = sorted(working, key=sort_key)

        men_w   = [p for p in wpool if get_gender(p) == "M"]
        women_w = [p for p in wpool if get_gender(p) == "W"]

        # dong_forced이고 아직 동성 선처리가 안된 경우:
        # 다수성별(동성 구성 가능한 쪽)을 anchor 앞에 배치
        dong_still_needed = dong_forced and len(groups_of_4) == 0
        if dong_still_needed and len(women_w) >= 4:
            # 여자4명 이상 → 여자 anchor 우선 (여복 유도)
            first_g, second_g = women_w, men_w
        elif dong_still_needed and len(men_w) >= 4:
            # 남자4명 이상 → 남자 anchor 우선 (남복 유도)
            first_g, second_g = men_w, women_w
        elif len(men_w) <= len(women_w):
            first_g, second_g = men_w, women_w   # 소수성별 우선 (기본)
        else:
            first_g, second_g = women_w, men_w

        interleaved: List[str] = []
        for a, b in zip_longest(first_g, second_g):
            if a is not None: interleaved.append(a)
            if b is not None: interleaved.append(b)
        anchors        = interleaved[:remaining_need]
        remaining_pool = [p for p in wpool if p not in anchors]

        # dong_forced 시 anchor → _pick_3_for_anchor에 "동성 우선" 강제
        anchor_league = "A리그" if dong_still_needed else league  # A리그 우선순위=동성>혼복

        for anchor in anchors:
            three = _pick_3_for_anchor(anchor, remaining_pool, mixed_counts, anchor_league)
            if three is None or len(three) < 3:
                # 동성 강제 실패 → 원래 리그 우선순위로 재시도
                three = _pick_3_for_anchor(anchor, remaining_pool, mixed_counts, league)
            if three is None or len(three) < 3:
                remaining_pool.insert(0, anchor); continue
            grp = [anchor] + three
            groups_of_4.append(grp)
            for p in grp:
                if p in remaining_pool: remaining_pool.remove(p)

        if len(remaining_pool) >= 4:
            extra, _ = build_all_groups(remaining_pool, mixed_counts, league)
            groups_of_4.extend(extra)

    if not groups_of_4:
        groups_of_4, _ = build_all_groups(working, mixed_counts, league)

    matches = []
    for g in groups_of_4:
        if len(g) < 4: continue
        random.shuffle(g)
        t1, t2 = best_pairing(g, gs, rs)
        commit_pairing(t1, t2, gs, rs)
        mt = classify_match([base_name(p) for p in list(t1) + list(t2)])
        matches.append({"team1": t1, "team2": t2, "type": mt})
    return matches


    n_groups = len(players) // 4
    if n_groups == 0: return []

    gender_count: Dict[str, int] = {}
    for p in players:
        g = get_gender(p)
        gender_count[g] = gender_count.get(g, 0) + 1

    priority = get_priority(league)

    def sort_key(p):
        return (
            game_counts.get(base_name(p), 0),
            gender_count.get(get_gender(p), 99),
            random.random()
        )

    working   = sorted(players, key=sort_key)
    men_all   = [p for p in working if get_gender(p) == "M"]
    women_all = [p for p in working if get_gender(p) == "W"]
    groups_of_4: List[List[str]] = []

    top_ptype = priority[0]

    # ── 1단계: 1순위 타입 선처리 ─────────────────────────────
    if top_ptype == "동성":
        # 순수 동성 그룹 최대 구성 (anchor용 슬롯 최소 1개 확보)
        preprocess_slots = min(max(0, n_groups - 1), len(men_all)//4 + len(women_all)//4)
        men_s   = sorted(men_all,   key=lambda p: (game_counts.get(base_name(p),0), random.random()))
        women_s = sorted(women_all, key=lambda p: (game_counts.get(base_name(p),0), random.random()))
        while len(men_s) >= 4 and len(groups_of_4) < preprocess_slots:
            grp = men_s[:4]; men_s = men_s[4:]
            groups_of_4.append(grp); [working.remove(p) for p in grp]
        while len(women_s) >= 4 and len(groups_of_4) < preprocess_slots:
            grp = women_s[:4]; women_s = women_s[4:]
            groups_of_4.append(grp); [working.remove(p) for p in grp]

    elif top_ptype == "혼복":
        # 쿼터 여유 있는 선수 수로 이번 라운드 최대 혼복 경기 수 계산
        quota_ok_m = [p for p in men_all   if mixed_quota_ok(p, mixed_counts, league)]
        quota_ok_w = [p for p in women_all if mixed_quota_ok(p, mixed_counts, league)]
        max_by_quota = min(len(quota_ok_m)//2, len(quota_ok_w)//2)
        minority_cnt = min(len(men_all), len(women_all))

        # 혼복 가능 여부: 쿼터 여유 있는 남녀가 각 2명 이상
        mixed_possible = (max_by_quota > 0 and minority_cnt >= 2)

        if mixed_possible:
            # 혼복 선처리 (anchor용 슬롯 1개 확보)
            preprocess_slots = min(max(0, n_groups - 1), minority_cnt // 2, max_by_quota)
            while len(groups_of_4) < preprocess_slots:
                men_avail   = [p for p in working if get_gender(p)=="M"
                               and mixed_quota_ok(p, mixed_counts, league)]
                women_avail = [p for p in working if get_gender(p)=="W"
                               and mixed_quota_ok(p, mixed_counts, league)]
                if len(men_avail) < 2 or len(women_avail) < 2: break
                m2 = sort_by_mixed_least(men_avail,   mixed_counts)[:2]
                w2 = sort_by_mixed_least(women_avail, mixed_counts)[:2]
                grp = m2 + w2
                groups_of_4.append(grp); [working.remove(p) for p in grp]
        else:
            # 혼복 불가 → 동성 선처리로 전환 (여자4명이면 여복 보장)
            dong_slots = min(n_groups, len(men_all)//4 + len(women_all)//4)
            men_s   = sorted(men_all,   key=lambda p: (game_counts.get(base_name(p),0), random.random()))
            women_s = sorted(women_all, key=lambda p: (game_counts.get(base_name(p),0), random.random()))
            while len(men_s) >= 4 and len(groups_of_4) < dong_slots:
                grp = men_s[:4]; men_s = men_s[4:]
                groups_of_4.append(grp); [working.remove(p) for p in grp]
            while len(women_s) >= 4 and len(groups_of_4) < dong_slots:
                grp = women_s[:4]; women_s = women_s[4:]
                groups_of_4.append(grp); [working.remove(p) for p in grp]

    # ── 2단계: 나머지 → anchor 기반 greedy ───────────────────
    remaining_need = n_groups - len(groups_of_4)
    if remaining_need > 0 and len(working) >= 4:
        wpool = sorted(working, key=sort_key)

        # 소수 성별이 anchor 앞자리를 차지하도록 교차 배치
        men_w   = [p for p in wpool if get_gender(p) == "M"]
        women_w = [p for p in wpool if get_gender(p) == "W"]
        if len(men_w) <= len(women_w):
            first_g, second_g = men_w, women_w
        else:
            first_g, second_g = women_w, men_w
        interleaved: List[str] = []
        for a, b in zip_longest(first_g, second_g):
            if a is not None: interleaved.append(a)
            if b is not None: interleaved.append(b)
        anchors        = interleaved[:remaining_need]
        remaining_pool = [p for p in wpool if p not in anchors]

        for anchor in anchors:
            three = _pick_3_for_anchor(anchor, remaining_pool, mixed_counts, league)
            if three is None or len(three) < 3:
                remaining_pool.insert(0, anchor); continue
            grp = [anchor] + three
            groups_of_4.append(grp)
            for p in grp:
                if p in remaining_pool: remaining_pool.remove(p)

        if len(remaining_pool) >= 4:
            extra, _ = build_all_groups(remaining_pool, mixed_counts, league)
            groups_of_4.extend(extra)

    if not groups_of_4:
        groups_of_4, _ = build_all_groups(working, mixed_counts, league)

    matches = []
    for g in groups_of_4:
        if len(g) < 4: continue
        random.shuffle(g)
        t1, t2 = best_pairing(g, gs, rs)
        commit_pairing(t1, t2, gs, rs)
        mt = classify_match([base_name(p) for p in list(t1) + list(t2)])
        matches.append({"team1": t1, "team2": t2, "type": mt})
    return matches


# ============================================================
# 섹션 7: 이벤트 라운드 (3회 미달자 보장, 기존 로직 유지)
# ============================================================

def build_event_round(
    players:      List[str],
    game_counts:  Dict[str, int],
    mixed_counts: Dict[str, int],
    league:       str = "A리그",
    min_games:    int = 3,
    max_games:    int = 4,
) -> List[Tuple[List[str], List[str]]]:
    """
    이벤트 라운드 그룹 구성 (기존 로직 유지 - 3~4회 보장 최우선).
    쿼터는 best-effort로만 반영 (3~4회 보장을 깨지 않음).
    """
    all_groups: List[Tuple[List[str], List[str]]] = []
    local_counts = dict(game_counts)

    # 성별 인원 수
    gender_count: Dict[str, int] = {}
    for p in players:
        g = get_gender(p)
        gender_count[g] = gender_count.get(g, 0) + 1

    for _ in range(20):
        need = [p for p in players if local_counts.get(base_name(p), 0) < min_games]
        if not need: break

        avail = [p for p in players
                 if p not in need and local_counts.get(base_name(p), 0) < max_games]

        pool = sorted(need, key=lambda p: (
            local_counts.get(base_name(p), 0),
            gender_count.get(get_gender(p), 99),
            random.random()
        ))

        while len(pool) % 4 != 0:
            cands = [p for p in avail if p not in pool]
            cands = sort_by_mixed_least(cands, mixed_counts)
            if not cands:
                pool = pool[: (len(pool) // 4) * 4]; break
            pool.append(cands.pop(0))

        if len(pool) < 4: break

        groups, leftovers = build_all_groups(pool, mixed_counts, league)

        if leftovers:
            cands = [p for p in avail if p not in leftovers and p not in pool]
            cands = sort_by_mixed_least(cands, mixed_counts)
            while len(leftovers) < 4 and cands:
                leftovers.append(cands.pop(0))
            if len(leftovers) >= 4:
                eg, _ = build_all_groups(leftovers, mixed_counts, league)
                groups.extend(eg)

        if not groups: break

        for g in groups:
            tagged = []
            for p in g:
                pn = base_name(p)
                tagged.append(pn + "(중복)" if local_counts.get(pn, 0) >= min_games else pn)
                local_counts[pn] = local_counts.get(pn, 0) + 1
            all_groups.append((g, tagged))

    return all_groups


# ============================================================
# 섹션 8: 통계 업데이트
# ============================================================

def update_stats(
    stats:      Dict[str, PlayerStats],
    team1:      Tuple, team2: Tuple,
    match_type: str,
    round_name: str,
    league:     str,
):
    for p_raw in list(team1) + list(team2):
        p = base_name(p_raw)
        if p not in stats:
            stats[p] = PlayerStats(name=p, league=league)
        s = stats[p]
        s.game_count += 1
        s.type_counts[match_type] = s.type_counts.get(match_type, 0) + 1
        if is_mixed_match(match_type): s.mixed_count += 1
        dup    = "(중복)" in p_raw
        record = match_type + ("★" if dup else "")
        s.round_records[round_name] = record


# ============================================================
# 섹션 9: 리그 스케줄 생성
# ============================================================

def generate_schedule_from_leagues(
    league_players: Dict[str, List[str]],
    num_rounds: int = 3,
) -> Tuple[List[dict], Dict[str, PlayerStats]]:
    all_results: List[dict] = []
    all_stats:   Dict[str, PlayerStats] = {}

    for league_name, players in league_players.items():
        if len(players) < 4: continue

        game_counts:  Dict[str, int] = {p: 0 for p in players}
        mixed_counts: Dict[str, int] = {p: 0 for p in players}
        gs = MatchState()

        for r in range(1, num_rounds + 1):
            rname = f"{r}R"
            rs    = MatchState()
            # 마지막 라운드에 dong_forced=True → B리그에서 동성복 보장
            is_last = (r == num_rounds)
            matches = make_round_matches(
                players, game_counts, mixed_counts, gs, rs, league_name,
                dong_forced=is_last
            )
            for m in matches:
                t1, t2, mt = m["team1"], m["team2"], m["type"]
                for p_raw in list(t1) + list(t2):
                    p = base_name(p_raw)
                    game_counts[p]  += 1
                    if is_mixed_match(mt): mixed_counts[p] += 1
                update_stats(all_stats, t1, t2, mt, rname, league_name)
                all_results.append({
                    "round": rname, "league": league_name,
                    "team1": t1,    "team2": t2,    "type": mt,
                })

        rs = MatchState()
        for raw_g, tagged_g in build_event_round(players, game_counts, mixed_counts, league_name):
            random.shuffle(tagged_g)
            t1, t2  = best_pairing(tagged_g, gs, rs)
            commit_pairing(t1, t2, gs, rs)
            mt      = classify_match([base_name(p) for p in list(t1) + list(t2)])
            has_dup = any("(중복)" in p for p in list(t1) + list(t2))
            note    = mt + ("(중복)" if has_dup else "")
            for p_raw in list(t1) + list(t2):
                p = base_name(p_raw)
                game_counts[p] += 1
                if is_mixed_match(mt): mixed_counts[p] += 1
            update_stats(all_stats, t1, t2, mt, "4R(이벤트)", league_name)
            all_results.append({
                "round": "4R(이벤트)", "league": league_name,
                "team1": t1,           "team2": t2,  "type": note,
            })

    return all_results, all_stats


# ============================================================
# 섹션 10: 입력 파싱
# ============================================================

def parse_custom_players(text: str, league_prefix: str) -> List[str]:
    players = []
    for line in text.strip().splitlines():
        parts = line.strip().split()
        if not parts: continue
        name   = parts[0]
        gender = "M"
        if len(parts) >= 2:
            g = parts[1].upper()
            if g in ("여", "W", "F"): gender = "W"
        players.append(f"{league_prefix}{gender}{name}")
    return players


# ============================================================
# 섹션 11: DataFrame 변환
# ============================================================

def stats_to_df(all_stats: Dict[str, PlayerStats]) -> pd.DataFrame:
    rows = []
    for code, s in all_stats.items():
        rows.append({
            "_코드":       code,
            "리그":        s.league,
            "이름":        display_name(code),
            "1R":          s.round_records.get("1R",         "-"),
            "2R":          s.round_records.get("2R",         "-"),
            "3R":          s.round_records.get("3R",         "-"),
            "4R(이벤트)":  s.round_records.get("4R(이벤트)", "-"),
            "남복":        s.type_counts.get("남복", 0),
            "여복":        s.type_counts.get("여복", 0),
            "혼복":        s.type_counts.get("혼복", 0),
            "잡복":        s.type_counts.get("잡복", 0),
            "혼성합계":    s.mixed_count,
            "총경기":      s.game_count,
        })
    df = pd.DataFrame(rows)
    if df.empty: return df
    return df.sort_values(["리그","_코드"]).reset_index(drop=True)


# ============================================================
# 섹션 12: Streamlit UI
# ============================================================

st.set_page_config(page_title="TELA Tennis Match", page_icon="🎾", layout="wide")

st.title("🎾 TELA CLUB Random Match Generator v2.0")
st.markdown(
    "**A리그:** 동성복 → 혼복 → 잡복 &nbsp;|&nbsp; "
    "**B리그:** 혼복(≤2회) → 동성복(≥1회) → 잡복 &nbsp;|&nbsp; "
    "**최소 3경기 보장 / 최대 4경기 제한**"
)

# ── 사이드바 ────────────────────────────────────────────────
st.sidebar.header("⚙️ 설정")

input_mode = st.sidebar.radio(
    "입력 방식",
    ["코드 자동 생성 (AM01/AW01...)", "직접 이름 입력"],
    index=0,
)
st.sidebar.markdown("---")

if input_mode == "코드 자동 생성 (AM01/AW01...)":
    c1, c2 = st.sidebar.columns(2)
    with c1:
        am = st.number_input("A리그 남자", min_value=0, max_value=30, value=8,  step=1)
        aw = st.number_input("A리그 여자", min_value=0, max_value=30, value=2,  step=1)
    with c2:
        bm = st.number_input("B리그 남자", min_value=0, max_value=30, value=3,  step=1)
        bw = st.number_input("B리그 여자", min_value=0, max_value=30, value=2,  step=1)
    custom_input = None
else:
    st.sidebar.markdown(
        "각 줄에 `이름 성별` 입력  \n"
        "성별: `남` 또는 `여` (생략 시 남자)  \n"
        "예) `홍길동 남` / `김영희 여`"
    )
    a_input = st.sidebar.text_area("A리그 선수 목록", placeholder="홍길동 남\n김영희 여", height=150)
    b_input = st.sidebar.text_area("B리그 선수 목록", placeholder="박보검 남\n아이유 여", height=120)
    custom_input = {"A": a_input, "B": b_input}
    am = aw = bm = bw = 0

st.sidebar.markdown("---")
use_seed = st.sidebar.checkbox("🔒 결과 고정 (시드)", value=False)
seed_val = None
if use_seed:
    seed_val = st.sidebar.number_input("시드 번호", min_value=0, max_value=9999, value=42, step=1)

generate_btn = st.sidebar.button("🎾 대진표 생성", type="primary", use_container_width=True)


# ── 메인 ────────────────────────────────────────────────────
if generate_btn:
    if custom_input is not None:
        league_players = {
            "A리그": parse_custom_players(custom_input["A"], "A") if custom_input["A"].strip() else [],
            "B리그": parse_custom_players(custom_input["B"], "B") if custom_input["B"].strip() else [],
        }
    else:
        league_players = {
            "A리그": [f"AM{i+1:02d}" for i in range(am)] + [f"AW{i+1:02d}" for i in range(aw)],
            "B리그": [f"BM{i+1:02d}" for i in range(bm)] + [f"BW{i+1:02d}" for i in range(bw)],
        }

    errors = []
    for lg, pl in league_players.items():
        if 0 < len(pl) < 4: errors.append(f"{lg} 인원이 4명 미만입니다.")
    if not any(len(pl) >= 4 for pl in league_players.values()):
        errors.append("최소 한 리그에 4명 이상 입력해주세요.")

    if errors:
        for e in errors: st.error(e)
        st.stop()

    if use_seed and seed_val is not None:
        random.seed(int(seed_val))

    seed_label = f"시드 #{int(seed_val)}" if (use_seed and seed_val is not None) else "시드 없음(랜덤)"

    with st.spinner("대진표 생성 중..."):
        schedule, stats = generate_schedule_from_leagues(league_players)

    if not schedule:
        st.warning("경기를 생성할 수 없습니다. 인원을 확인해주세요.")
        st.stop()

    def dn(code: str) -> str: return display_name(code)

    df_matches = pd.DataFrame([{
        "라운드":   d["round"],   "리그":     d["league"],
        "팀1-A":    dn(d["team1"][0]), "팀1-B": dn(d["team1"][1]),
        "팀2-A":    dn(d["team2"][0]), "팀2-B": dn(d["team2"][1]),
        "매치종류": d["type"],
    } for d in schedule])

    df_full    = stats_to_df(stats)
    df_display = df_full.drop(columns=["_코드"])

    tab1, tab2, tab3 = st.tabs(["📋 대진표", "📊 출전 현황", "🔍 검증 리포트"])

    with tab1:
        st.subheader(f"경기 대진표 · {seed_label}")
        def hl_match(row):
            bg = "#E8F5E9" if "A리그" in str(row.get("리그","")) else "#E3F2FD"
            return [f"background-color:{bg};color:black"] * len(row)
        st.dataframe(df_matches.style.apply(hl_match, axis=1),
                     use_container_width=True, height=600)
        summary = df_matches["매치종류"].value_counts()
        st.caption(f"총 {len(df_matches)}경기 | " +
                   " | ".join(f"{k}: {v}경기" for k,v in summary.items()))

    with tab2:
        st.subheader("선수별 출전 현황")
        palette = {"AM":"#E8F5E9","AW":"#FCE4EC","BM":"#E3F2FD","BW":"#FFF3E0"}
        def hl_stats(row):
            code  = df_full.loc[row.name, "_코드"] if "_코드" in df_full.columns else ""
            bg    = palette.get(code[:2], "")
            base  = f"background-color:{bg};color:black" if bg else ""
            styles = [base] * len(row)
            if "총경기" in row.index:
                idx   = row.index.get_loc("총경기")
                total = row["총경기"]
                if total >= 4:   styles[idx] = "background-color:#FFF176;color:black;font-weight:bold"
                elif total < 3:  styles[idx] = "background-color:#FFCDD2;color:black;font-weight:bold"
                else:            styles[idx] = base + ";font-weight:bold"
            return styles
        st.dataframe(df_display.style.apply(hl_stats, axis=1),
                     use_container_width=True, height=700)

    with tab3:
        st.subheader("🔍 자동 검증 리포트")
        issues, warns = [], []

        if not df_full.empty:
            under3 = df_full[df_full["총경기"] < 3]
            if not under3.empty:
                issues.append(f"❌ 3경기 미달 {len(under3)}명: {', '.join(under3['이름'].tolist())}")
            else: st.success("✅ 모든 선수 3경기 이상")

            over4 = df_full[df_full["총경기"] > 4]
            if not over4.empty:
                issues.append(f"❌ 4경기 초과 {len(over4)}명: {', '.join(over4['이름'].tolist())}")
            else: st.success("✅ 4경기 초과 없음")

            st.markdown("**매치 종류 분포**")
            td = df_matches["매치종류"].value_counts().reset_index()
            td.columns = ["매치종류","경기수"]
            st.dataframe(td, use_container_width=False)

            if len(df_full) > 1:
                std_m  = df_full["혼성합계"].std()
                mean_m = df_full["혼성합계"].mean()
                if std_m > 1.5: warns.append(f"⚠️ 혼성 편차 큼 (평균 {mean_m:.1f}회, σ={std_m:.2f})")
                else:           st.success(f"✅ 혼성 균등 분배 (평균 {mean_m:.1f}회, σ={std_m:.2f})")

            # B리그 쿼터 현황
            b_rows = df_full[df_full["리그"]=="B리그"]
            if not b_rows.empty:
                st.markdown("**B리그 쿼터 현황** (혼성≤2회, 동성≥1회)")
                quota_rows = []
                for _, row in b_rows.iterrows():
                    dong = row["남복"] + row["여복"]
                    mc   = row["혼성합계"]
                    quota_rows.append({
                        "이름": row["이름"],
                        "혼성": mc,
                        "동성": dong,
                        "혼성쿼터": "✅" if mc <= 2 else "❌",
                        "동성쿼터": "✅" if dong >= 1 else "⚠️",
                    })
                st.dataframe(pd.DataFrame(quota_rows), use_container_width=False)

        for i in issues: st.error(i)
        for w in warns:  st.warning(w)
        if not issues and not warns: st.info("🎾 모든 검증 통과!")

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
        df_matches.to_excel(writer, sheet_name="대진표",   index=False)
        df_display.to_excel(writer, sheet_name="출전현황", index=False)
        for sn in ["대진표","출전현황"]: writer.sheets[sn].set_column("A:Z", 14)

    excel_tag = f"_시드{int(seed_val)}" if (use_seed and seed_val is not None) else "_랜덤"
    st.sidebar.markdown("---")
    st.sidebar.download_button(
        label="📥 엑셀 다운로드",
        data=buf.getvalue(),
        file_name=f"TELA_대진표{excel_tag}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )

else:
    st.info("👈 사이드바에서 인원을 설정하고 **대진표 생성** 버튼을 눌러주세요.")
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
        - **최대 4경기** 제한 → 중복 출전 최대 1회
        - 혼성 자리 → **혼성 최소 참여자 우선** 선발

        ### B리그 쿼터
        - 1인당 혼성 경기(혼복+잡복) **최대 2회**
        - 1인당 동성 경기(남복/여복) **최소 1회**
        - ⚠️ 남≥4명 AND 여≥4명일 때 완전 준수 가능

        ### 표시 기호
        - `★` / `(중복)` : 이벤트 라운드(4R) 중복 출전
        - 총경기 **노란색**: 4경기, **빨간색**: 3경기 미만
        """)
