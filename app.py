"""
TELA CLUB Random Match Generator v2.05
======================================
핵심 규칙:
  1. 1인 최소 3경기 보장 / 최대 4경기 제한
  2. A리그 매치 우선순위: 동성복(남복/여복) > 혼복 > 잡복
     B리그 매치 우선순위: 혼복 > 동성복 > 잡복
  3. 혼성 자리 → 혼성 경기 최소 참여자 우선 선발
  4. B리그 개인 쿼터: 혼복(혼성) 최대 2회 / 동성복 최소 1회
  5. [v2.05] 사이드바 네비게이션: 랜덤페어 / 점수판
             점수판: 랜덤페어 대진 자동 불러오기 + 점수 입력 + 통계
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

LEAGUE_QUOTA: Dict[str, Dict] = {
    "A리그": {"mixed_max": None, "dong_min": None},
    "B리그": {"mixed_max": 2,    "dong_min": 1   },
}

def get_quota(league: str) -> Dict:
    return LEAGUE_QUOTA.get(league, {"mixed_max": None, "dong_min": None})

def mixed_quota_ok(p: str, mixed_counts: Dict[str, int], league: str) -> bool:
    q = get_quota(league)
    if q["mixed_max"] is None: return True
    return mixed_counts.get(base_name(p), 0) < q["mixed_max"]


# ============================================================
# 섹션 5: 그룹 구성
# ============================================================

def build_one_group(
    pool: List[str],
    mixed_counts: Dict[str, int],
    league: str = "A리그",
) -> Tuple[Optional[List[str]], List[str]]:
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
    if len(remaining) < 3: return None

    g        = get_gender(anchor)
    men      = [p for p in remaining if get_gender(p) == "M"]
    women    = [p for p in remaining if get_gender(p) == "W"]
    priority = get_priority(league)

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
        if not anchor_ok: return None
        opp_use = opp_quota if len(opp_quota) >= 2 else []
        if not opp_use: return None
        if g == "M":
            m_use = [p for p in men if mixed_quota_ok(p, mixed_counts, league)]
            if len(m_use) >= 1:
                return sort_by_mixed_least(m_use, mixed_counts)[:1] + sort_by_mixed_least(opp_use, mixed_counts)[:2]
            return None
        else:
            w_use = [p for p in women if mixed_quota_ok(p, mixed_counts, league)]
            if len(w_use) >= 1:
                return sort_by_mixed_least(w_use, mixed_counts)[:1] + sort_by_mixed_least(opp_use, mixed_counts)[:2]
            return None

    def try_jabbok():
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

    if top_ptype == "동성":
        preprocess_slots = min(max(0, n_groups - 1), len(men_all)//4 + len(women_all)//4)
        men_s   = sorted(men_all,   key=lambda p: (game_counts.get(base_name(p),0), random.random()))
        women_s = sorted(women_all, key=lambda p: (game_counts.get(base_name(p),0), random.random()))
        while len(men_s) >= 4 and len(groups_of_4) < preprocess_slots:
            grp = men_s[:4]; men_s = men_s[4:]
            groups_of_4.append(grp)
            for p in grp: working.remove(p)
        while len(women_s) >= 4 and len(groups_of_4) < preprocess_slots:
            grp = women_s[:4]; women_s = women_s[4:]
            groups_of_4.append(grp)
            for p in grp: working.remove(p)

    elif top_ptype == "혼복":
        quota_ok_m = [p for p in men_all   if mixed_quota_ok(p, mixed_counts, league)]
        quota_ok_w = [p for p in women_all if mixed_quota_ok(p, mixed_counts, league)]
        max_by_quota = min(len(quota_ok_m)//2, len(quota_ok_w)//2)
        minority_cnt = min(len(men_all), len(women_all))

        dong_possible = len(men_all)//4 + len(women_all)//4
        mixed_possible = (max_by_quota > 0 and minority_cnt >= 2)

        if not mixed_possible or dong_forced:
            import math
            minority_cnt2 = min(len(men_all), len(women_all))
            minority_groups_needed = math.ceil(minority_cnt2 / 4) if minority_cnt2 > 0 else 0
            if dong_forced:
                dong_slots = min(dong_possible, n_groups)
            else:
                dong_slots = min(dong_possible, max(0, n_groups - minority_groups_needed))
            men_s   = sorted(men_all,   key=lambda p: (game_counts.get(base_name(p),0), random.random()))
            women_s = sorted(women_all, key=lambda p: (game_counts.get(base_name(p),0), random.random()))
            while len(men_s) >= 4 and len(groups_of_4) < dong_slots:
                grp = men_s[:4]; men_s = men_s[4:]
                groups_of_4.append(grp)
                for p in grp: working.remove(p)
            while len(women_s) >= 4 and len(groups_of_4) < dong_slots:
                grp = women_s[:4]; women_s = women_s[4:]
                groups_of_4.append(grp)
                for p in grp: working.remove(p)
        else:
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
                groups_of_4.append(grp)
                for p in grp: working.remove(p)

    remaining_need = n_groups - len(groups_of_4)
    if remaining_need > 0 and len(working) >= 4:
        wpool = sorted(working, key=sort_key)

        men_w   = [p for p in wpool if get_gender(p) == "M"]
        women_w = [p for p in wpool if get_gender(p) == "W"]

        dong_still_needed = dong_forced and len(groups_of_4) == 0
        if dong_still_needed and len(women_w) >= 4:
            first_g, second_g = women_w, men_w
        elif dong_still_needed and len(men_w) >= 4:
            first_g, second_g = men_w, women_w
        elif len(men_w) <= len(women_w):
            first_g, second_g = men_w, women_w
        else:
            first_g, second_g = women_w, men_w

        interleaved: List[str] = []
        for a, b in zip_longest(first_g, second_g):
            if a is not None: interleaved.append(a)
            if b is not None: interleaved.append(b)
        anchors        = interleaved[:remaining_need]
        remaining_pool = [p for p in wpool if p not in anchors]

        anchor_league = "A리그" if dong_still_needed else league

        for anchor in anchors:
            three = _pick_3_for_anchor(anchor, remaining_pool, mixed_counts, anchor_league)
            if three is None or len(three) < 3:
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
# 섹션 7: 이벤트 라운드
# ============================================================

def build_event_round(
    players:      List[str],
    game_counts:  Dict[str, int],
    mixed_counts: Dict[str, int],
    league:       str = "A리그",
    min_games:    int = 3,
    max_games:    int = 4,
) -> List[Tuple[List[str], List[str]]]:
    all_groups: List[Tuple[List[str], List[str]]] = []
    local_counts = dict(game_counts)

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
# 섹션 12: 점수판 유틸리티
# ============================================================

def get_player_display_name(code: str) -> str:
    """점수판용 짧은 표시 이름"""
    raw = base_name(code)
    if is_custom_code(raw):
        return raw[2:]  # 이름만 (성별 괄호 제거)
    return raw

def compute_scoreboard_stats(schedule: List[dict], scores: Dict[str, Dict]) -> pd.DataFrame:
    """
    점수판 통계 계산.
    scores: { match_key: {"score1": int, "score2": int} }
    match_key: f"{idx}"
    """
    # 선수별 집계
    player_stats: Dict[str, Dict] = {}

    def ensure_player(code: str, league: str):
        key = base_name(code)
        if key not in player_stats:
            player_stats[key] = {
                "이름": get_player_display_name(code),
                "리그": league,
                "출전": 0,
                "승": 0,
                "패": 0,
                "승점": 0,
                "실점": 0,
                "1R출전": 0,
                "2R출전": 0,
                "3R출전": 0,
                "4R출전": 0,
            }

    for idx, match in enumerate(schedule):
        key = str(idx)
        sc = scores.get(key, {})
        s1 = sc.get("score1", None)
        s2 = sc.get("score2", None)

        league = match["league"]
        t1 = [base_name(p) for p in match["team1"]]
        t2 = [base_name(p) for p in match["team2"]]
        rnd = match["round"]

        # 라운드 번호 파싱
        rnd_num = None
        if rnd == "1R": rnd_num = 1
        elif rnd == "2R": rnd_num = 2
        elif rnd == "3R": rnd_num = 3
        elif "이벤트" in rnd or "4R" in rnd: rnd_num = 4

        for code in match["team1"]:
            ensure_player(code, league)
        for code in match["team2"]:
            ensure_player(code, league)

        # 출전 카운트
        for p in t1 + t2:
            player_stats[p]["출전"] += 1
            if rnd_num == 1: player_stats[p]["1R출전"] += 1
            elif rnd_num == 2: player_stats[p]["2R출전"] += 1
            elif rnd_num == 3: player_stats[p]["3R출전"] += 1
            elif rnd_num == 4: player_stats[p]["4R출전"] += 1

        # 점수가 모두 입력된 경우만 승패 반영
        if s1 is not None and s2 is not None:
            if s1 > s2:
                winners, losers = t1, t2
                w_score, l_score = s1, s2
            elif s2 > s1:
                winners, losers = t2, t1
                w_score, l_score = s2, s1
            else:
                # 무승부
                for p in t1 + t2:
                    player_stats[p]["승점"] += s1
                    player_stats[p]["실점"] += s2
                continue

            for p in winners:
                player_stats[p]["승"] += 1
                player_stats[p]["승점"] += w_score
                player_stats[p]["실점"] += l_score
            for p in losers:
                player_stats[p]["패"] += 1
                player_stats[p]["승점"] += l_score
                player_stats[p]["실점"] += w_score

    if not player_stats:
        return pd.DataFrame()

    rows = list(player_stats.values())
    df = pd.DataFrame(rows)
    df = df[["리그","이름","출전","승","패","승점","실점","1R출전","2R출전","3R출전","4R출전"]]
    df = df.sort_values(["리그","승","승점"], ascending=[True, False, False]).reset_index(drop=True)
    return df


# ============================================================
# 섹션 13: Streamlit UI
# ============================================================

st.set_page_config(page_title="TELA Tennis Match", page_icon="🎾", layout="wide")

# ── CSS 커스텀 ───────────────────────────────────────────────
st.markdown("""
<style>
[data-testid="stSidebar"] { min-width: 220px; max-width: 260px; }
.nav-btn { font-size: 1.1rem; font-weight: 700; }
.score-input input { text-align: center; font-weight: bold; font-size: 1.1rem; }
.match-card {
    background: #f8f9fa;
    border-radius: 10px;
    padding: 8px 12px;
    margin-bottom: 6px;
    border-left: 4px solid #2e7d32;
}
.match-card-b { border-left-color: #1565c0; }
</style>
""", unsafe_allow_html=True)

# ── 사이드바 네비게이션 ─────────────────────────────────────
st.sidebar.markdown("## 🎾 TELA CLUB")
st.sidebar.markdown("---")

page = st.sidebar.radio(
    "메뉴",
    ["🎲 랜덤페어", "📊 점수판"],
    index=0,
    label_visibility="collapsed",
)
st.sidebar.markdown("---")


# ============================================================
# 페이지 1: 랜덤페어
# ============================================================

if page == "🎲 랜덤페어":
    st.title("🎾 TELA CLUB Random Match Generator v2.05")
    st.markdown(
        "**A리그:** 동성복 → 혼복 → 잡복 &nbsp;|&nbsp; "
        "**B리그:** 혼복(≤2회) → 동성복(≥1회) → 잡복 &nbsp;|&nbsp; "
        "**최소 3경기 보장 / 최대 4경기 제한**"
    )

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

        # session_state에 저장 (점수판에서 사용)
        st.session_state["schedule"] = schedule
        st.session_state["stats"]    = stats
        st.session_state["scores"]   = {}  # 점수 초기화

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
            st.info("💡 대진표 생성 후 사이드바에서 **📊 점수판**을 선택하면 점수를 입력할 수 있습니다.")

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

            ### 표시 기호
            - `★` / `(중복)` : 이벤트 라운드(4R) 중복 출전
            - 총경기 **노란색**: 4경기, **빨간색**: 3경기 미만

            ### 점수판 사용법
            1. 대진표 생성 후 사이드바에서 **📊 점수판** 선택
            2. 각 경기 점수 입력 (예: 6, 4)
            3. 하단 통계에서 선수별 승·패·승점 확인
            """)


# ============================================================
# 페이지 2: 점수판
# ============================================================

elif page == "📊 점수판":

    # ── 점수판 전용 CSS ────────────────────────────────────────
    st.markdown("""
    <style>
    .sb-title {
        font-family: 'Malgun Gothic', sans-serif;
        font-size: 1.5rem; font-weight: 800;
        text-align: center; margin-bottom: 4px;
        letter-spacing: -0.5px;
    }
    .sb-date {
        text-align: right; font-size: 0.85rem;
        color: #666; margin-bottom: 16px;
    }
    .rnd-header {
        background: #1a1a2e;
        color: white;
        font-weight: 700;
        font-size: 1rem;
        text-align: center;
        padding: 6px 0;
        border-radius: 6px 6px 0 0;
        margin-bottom: 2px;
        letter-spacing: 1px;
    }
    .match-row {
        display: flex;
        align-items: center;
        border: 1px solid #ddd;
        border-radius: 6px;
        margin-bottom: 4px;
        background: #fff;
        overflow: hidden;
    }
    .match-row:hover { background: #f5f5f5; }
    .team-cell {
        flex: 3;
        padding: 6px 8px;
        font-size: 0.88rem;
        font-weight: 600;
        color: #222;
    }
    .score-cell {
        flex: 1;
        text-align: center;
        padding: 4px;
        background: #f0f0f0;
        font-size: 1.1rem;
        font-weight: 700;
    }
    .vs-cell {
        flex: 0 0 24px;
        text-align: center;
        font-size: 0.75rem;
        color: #999;
        font-weight: 700;
    }
    .win-team { color: #b71c1c; }
    .type-badge {
        font-size: 0.72rem;
        color: #888;
        padding: 0 6px;
    }
    .stat-table { width: 100%; border-collapse: collapse; font-size: 0.88rem; }
    .stat-table th {
        background: #1a1a2e; color: white;
        padding: 6px 8px; text-align: center;
        font-weight: 600;
    }
    .stat-table td {
        padding: 5px 8px; text-align: center;
        border-bottom: 1px solid #eee;
    }
    .stat-table tr:hover td { background: #f9f9f9; }
    .stat-name { text-align: left !important; font-weight: 600; }
    .top-win { background: #fff9c4 !important; font-weight: 700; }
    .lg-a { border-left: 4px solid #2e7d32; }
    .lg-b { border-left: 4px solid #1565c0; }
    </style>
    """, unsafe_allow_html=True)

    schedule = st.session_state.get("schedule", None)

    if not schedule:
        st.warning("⚠️ 아직 대진표가 생성되지 않았습니다.")
        st.info("👈 사이드바에서 **🎲 랜덤페어**를 선택하고 대진표를 먼저 생성해주세요.")
        st.stop()

    if "scores" not in st.session_state:
        st.session_state["scores"] = {}

    # 날짜
    from datetime import date
    today_str = date.today().strftime("%Y년 %m월 %d일")

    # 타이틀
    st.markdown(
        '<div class="sb-title">🎾 TELA 테니스 클럽 랜덤페어 점수판</div>'
        f'<div class="sb-date">{today_str}</div>',
        unsafe_allow_html=True
    )

    # 라운드 목록 추출
    rounds = []
    seen_r = set()
    for m in schedule:
        r = m["round"]
        if r not in seen_r:
            rounds.append(r)
            seen_r.add(r)

    def pname(code: str) -> str:
        raw = base_name(code)
        if is_custom_code(raw):
            g_label = "(남)" if raw[1].upper() == "M" else "(여)"
            return raw[2:] + g_label
        return raw

    # ── 라운드별 컬럼 레이아웃 ────────────────────────────────
    # 이미지2처럼: 라운드 수만큼 좌우 컬럼, 각 컬럼 안에 A/B리그 경기 카드
    # ─────────────────────────────────────────────────────────

    # 라운드 컬럼 분할 (최대 4개: 1R, 2R, 3R, 4R이벤트)
    n_rounds = len(rounds)
    rnd_cols = st.columns(n_rounds, gap="small")

    for col_i, (rnd, rcol) in enumerate(zip(rounds, rnd_cols)):
        rnd_matches = [(idx, m) for idx, m in enumerate(schedule) if m["round"] == rnd]
        if not rnd_matches:
            continue

        with rcol:
            # 라운드 헤더
            rnd_label = rnd.replace("(이벤트)", "") + (" ⭐" if "이벤트" in rnd else "")
            st.markdown(f'<div class="rnd-header">{rnd_label}</div>', unsafe_allow_html=True)

            # 리그별로 분리
            leagues_in_rnd = []
            seen_lg2 = set()
            for _, m in rnd_matches:
                if m["league"] not in seen_lg2:
                    leagues_in_rnd.append(m["league"])
                    seen_lg2.add(m["league"])

            for league in leagues_in_rnd:
                lg_matches = [(idx, m) for idx, m in rnd_matches if m["league"] == league]
                if not lg_matches: continue

                lg_color  = "#2e7d32" if "A" in league else "#1565c0"
                lg_class  = "lg-a"   if "A" in league else "lg-b"

                # 리그 소헤더
                st.markdown(
                    f'<div style="color:{lg_color};font-size:0.82rem;font-weight:700;'
                    f'margin:8px 0 4px 0;padding-left:4px;border-left:3px solid {lg_color};">'
                    f'{league}</div>',
                    unsafe_allow_html=True
                )

                for idx, match in lg_matches:
                    sc  = st.session_state["scores"].get(str(idx), {})
                    s1v = sc.get("score1", 0)
                    s2v = sc.get("score2", 0)

                    t1a = pname(match["team1"][0])
                    t1b = pname(match["team1"][1])
                    t2a = pname(match["team2"][0])
                    t2b = pname(match["team2"][1])
                    mtype = match["type"]

                    # 승팀 하이라이트 판정
                    t1_win = s1v > s2v and (s1v + s2v) > 0
                    t2_win = s2v > s1v and (s1v + s2v) > 0

                    t1_style = "color:#b71c1c;font-weight:800;" if t1_win else ""
                    t2_style = "color:#b71c1c;font-weight:800;" if t2_win else ""

                    # 경기 카드 (HTML)
                    card_html = f"""
                    <div style="border:1px solid #ddd;border-radius:6px;
                                margin-bottom:6px;overflow:hidden;
                                border-left:4px solid {lg_color};">
                      <div style="display:flex;align-items:stretch;">
                        <div style="flex:3;padding:6px 6px 2px 8px;">
                          <div style="font-size:0.82rem;font-weight:700;{t1_style}">{t1a}</div>
                          <div style="font-size:0.82rem;font-weight:700;{t1_style}">{t1b}</div>
                        </div>
                        <div style="flex:0 0 28px;background:#f0f0f0;
                                    display:flex;align-items:center;justify-content:center;
                                    font-size:0.95rem;font-weight:800;color:#333;">{s1v}</div>
                        <div style="flex:0 0 18px;display:flex;align-items:center;
                                    justify-content:center;font-size:0.65rem;color:#aaa;">vs</div>
                        <div style="flex:0 0 28px;background:#f0f0f0;
                                    display:flex;align-items:center;justify-content:center;
                                    font-size:0.95rem;font-weight:800;color:#333;">{s2v}</div>
                        <div style="flex:3;padding:6px 8px 2px 6px;text-align:right;">
                          <div style="font-size:0.82rem;font-weight:700;{t2_style}">{t2a}</div>
                          <div style="font-size:0.82rem;font-weight:700;{t2_style}">{t2b}</div>
                        </div>
                      </div>
                      <div style="background:#fafafa;padding:2px 8px;
                                  font-size:0.68rem;color:#999;text-align:right;">{mtype}</div>
                    </div>
                    """
                    st.markdown(card_html, unsafe_allow_html=True)

                    # 점수 입력 (카드 아래 나란히)
                    ic1, ic2, ic3 = st.columns([1, 0.3, 1])
                    s1_new = ic1.number_input(
                        "팀1",
                        min_value=0, max_value=99,
                        value=s1v,
                        key=f"score1_{idx}",
                        label_visibility="collapsed",
                    )
                    ic2.markdown(
                        '<div style="text-align:center;font-size:0.9rem;'
                        'font-weight:700;padding-top:6px;color:#888;">-</div>',
                        unsafe_allow_html=True
                    )
                    s2_new = ic3.number_input(
                        "팀2",
                        min_value=0, max_value=99,
                        value=s2v,
                        key=f"score2_{idx}",
                        label_visibility="collapsed",
                    )
                    st.session_state["scores"][str(idx)] = {
                        "score1": s1_new, "score2": s2_new
                    }

    # ── 구분선 ────────────────────────────────────────────────
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown("---")

    # ── 통계 테이블 ──────────────────────────────────────────
    st.markdown("### 📈 선수별 통계")
    st.caption("점수 입력 즉시 실시간 반영됩니다.")

    df_sb = compute_scoreboard_stats(schedule, st.session_state.get("scores", {}))

    if df_sb.empty:
        st.info("점수를 입력하면 통계가 표시됩니다.")
    else:
        for league in ["A리그", "B리그"]:
            df_lg = df_sb[df_sb["리그"] == league].drop(columns=["리그"]).reset_index(drop=True)
            if df_lg.empty: continue

            lg_color = "#2e7d32" if "A" in league else "#1565c0"
            st.markdown(
                f'<div style="color:{lg_color};font-weight:700;font-size:1rem;'
                f'border-bottom:2px solid {lg_color};padding-bottom:4px;margin:16px 0 8px 0;">'
                f'🎾 {league} 통계</div>',
                unsafe_allow_html=True
            )

            # 최다승 값
            max_win = df_lg["승"].max() if not df_lg.empty else 0

            def hl_sb(row, _max_win=max_win):
                styles = [""] * len(row)
                if "승" in row.index:
                    wi = row.index.get_loc("승")
                    if row["승"] == _max_win and _max_win > 0:
                        styles[wi] = "background-color:#FFF176;font-weight:bold"
                return styles

            st.dataframe(
                df_lg.style.apply(hl_sb, axis=1),
                use_container_width=True,
                hide_index=True,
            )

    # ── 점수 초기화 ───────────────────────────────────────────
    st.markdown("---")
    if st.button("🔄 점수 초기화", type="secondary"):
        st.session_state["scores"] = {}
        st.rerun()
