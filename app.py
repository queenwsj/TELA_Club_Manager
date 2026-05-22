"""
TELA CLUB Random Match Generator v4.02
======================================
변경사항 (v4.01):
  [버그수정] 완전 랜덤페어 여복 미출현 버그 수정
      · 문제: 남자 10명 여자 5명 등 특정 비율에서 여복이 나오지 않음
      · 원인: _build_jabbok_minimized_groups의 동점 후보 수집 방식이
              dong_w=0인 조합만 동점으로 묶어 여복 선택 기회 원천 차단
      · 수정: best_score + threshold(15점) 이내 모든 유효 조합을 candidates로
              확장하여 여복/남복 그룹이 골고루 등장하도록 개선

변경사항 (v4.00):
  [1] 페어링 방식 선택 섹션 추가 (v3.01 내용 통합)
      · 조건부 랜덤페어: 리그별 우선순위·쿼터 적용
      · 완전 랜덤페어: 완전 무작위 (남자팀 vs 여자팀 대결만 제한)
  [2] 리그 수 변동 설정 기능 추가
      · 1~5개 리그 자유 설정
      · 리그명: A리그, B리그, C리그, D리그, E리그 순 자동 부여
      · 리그별 독립적으로 우선순위(동성우선/혼복우선) 및 쿼터 설정
      · 인원 입력 UI, 색상, 검증 리포트 모두 리그 수에 따라 동적 생성
"""

import streamlit as st
import pandas as pd
import random
import io
import shelve
import os
from dataclasses import dataclass, field
from itertools import zip_longest
from typing import Dict, FrozenSet, List, Optional, Set, Tuple
from datetime import date


# ============================================================
# 섹션 0: 저장소 경로 & 상수
# ============================================================

SAVE_DIR   = os.path.join(os.path.dirname(__file__), ".tela_data")
os.makedirs(SAVE_DIR, exist_ok=True)
SHELF_PATH  = os.path.join(SAVE_DIR, "scoreboard")
MEMBER_PATH = os.path.join(SAVE_DIR, "members")


def shelf_save(date_key: str, schedule: list, scores: dict):
    with shelve.open(SHELF_PATH) as db:
        db[date_key] = {"schedule": schedule, "scores": scores}

def shelf_load(date_key: str) -> Optional[dict]:
    with shelve.open(SHELF_PATH) as db:
        return db.get(date_key, None)

def shelf_list_dates() -> List[str]:
    with shelve.open(SHELF_PATH) as db:
        return sorted(db.keys(), reverse=True)

# ── 회원 관리 shelve 헬퍼 ─────────────────────────────────────
def member_load_all() -> dict:
    """전체 회원 데이터 로드. 구조: {league_name: [{name, gender}, ...]}"""
    with shelve.open(MEMBER_PATH) as db:
        return dict(db.get("members", {}))

def member_save_all(data: dict):
    with shelve.open(MEMBER_PATH) as db:
        db["members"] = data

def member_add(league: str, name: str, gender: str):
    data = member_load_all()
    if league not in data:
        data[league] = []
    # 중복 방지
    if not any(m["name"] == name for m in data[league]):
        data[league].append({"name": name, "gender": gender})
    member_save_all(data)

def member_remove(league: str, name: str):
    data = member_load_all()
    if league in data:
        data[league] = [m for m in data[league] if m["name"] != name]
    member_save_all(data)


# ── 구글 시트 회원 명부 연동 ──────────────────────────────────
SHEET_ID = "1QjzPLZuXiE2BKt9lC-6Gzbi1mssYkosR12Q2tO4rVJk"
UNASSIGNED_KEY = "미배정"   # 리그 미지정 회원 임시 버킷

def _get_gspread_client():
    """Streamlit secrets의 gcp_service_account로 gspread 인증"""
    import gspread
    from google.oauth2.service_account import Credentials
    scopes = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds_dict = dict(st.secrets["gcp_service_account"])
    # private_key 개행 처리 (TOML에서 \\n → \n)
    if "private_key" in creds_dict:
        creds_dict["private_key"] = creds_dict["private_key"].replace("\\n", "\n")
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    return gspread.authorize(creds)

def sync_from_sheet(target_league: str) -> dict:
    """
    구글 시트에서 활성 회원 읽기.
    컬럼 구조: id(A) category(B) name(C) cafe_id(D) birth_year(E)
               gender(F) phone(G) region(H) join_date(I) dormant_period(J)
               leave_date(K) email(L) application(M) memo(N)
               updated_at(O) deleted_at(P)

    상태 판별:
      - deleted_at 값 있음         → 탈퇴 (완전 제외)
      - leave_date 값 있음         → 탈퇴 (완전 제외)
      - dormant_period 비어있음    → 정상
      - dormant_period 종료일이 오늘 이전 → 정상 (휴면 종료)
      - dormant_period 종료일이 오늘 이후 → 휴면
      - dormant_period 종료일 없음  → 휴면 (진행 중)

    반환: {"imported": int, "skipped": int, "added": int}
    """
    from datetime import date as _date
    today = _date.today()

    def _parse_dormant(dormant_str: str) -> str:
        """dormant_period 문자열 → '정상' 또는 '휴면'"""
        s = dormant_str.strip()
        if not s:
            return "정상"
        # '2024-01-01~2024-06-30' 또는 '2024-01-01~' 형태
        if "~" in s:
            end_part = s.split("~", 1)[1].strip()
            if end_part:
                try:
                    end_date = _date.fromisoformat(end_part[:10])
                    return "정상" if end_date < today else "휴면"
                except ValueError:
                    pass
            return "휴면"   # 종료일 없으면 진행 중
        # 날짜 하나만 있을 때 (시작일로 간주 → 휴면 진행 중)
        return "휴면"

    gc   = _get_gspread_client()
    sh   = gc.open_by_key(SHEET_ID)
    ws   = sh.sheet1
    rows = ws.get_all_records()

    imported, skipped = 0, 0
    new_members = []
    for row in rows:
        name         = str(row.get("name",           "")).strip()
        gender_raw   = str(row.get("gender",          "")).strip().upper()
        deleted_at   = str(row.get("deleted_at",      "")).strip()
        leave_date   = str(row.get("leave_date",      "")).strip()
        dormant      = str(row.get("dormant_period",  "")).strip()

        if not name:
            skipped += 1; continue
        if deleted_at or leave_date:
            skipped += 1; continue

        if gender_raw in ("남", "M", "MALE", "1"):
            gender = "M"
        elif gender_raw in ("여", "F", "W", "FEMALE", "2"):
            gender = "W"
        else:
            gender = "M"

        status = _parse_dormant(dormant)
        new_members.append({"name": name, "gender": gender, "status": status})
        imported += 1

    # 기존 shelve 머지 — status는 시트 기준으로 갱신, 수동 override는 유지 안 함
    # (재가져오기 시 항상 시트가 최신 source of truth)
    data = member_load_all()
    existing_by_name: dict = {}
    for lg, members in data.items():
        for idx, m in enumerate(members):
            existing_by_name[m["name"]] = (lg, idx)

    added = 0
    for m in new_members:
        if m["name"] in existing_by_name:
            lg, idx = existing_by_name[m["name"]]
            data[lg][idx]["status"] = m["status"]
        else:
            bucket = target_league if target_league else UNASSIGNED_KEY
            if bucket not in data:
                data[bucket] = []
            data[bucket].append(m)
            added += 1

    member_save_all(data)
    return {"imported": imported, "skipped": skipped, "added": added}


ADMIN_PASSWORD = "1223"

# 리그 이름 풀 (최대 5개)
LEAGUE_NAMES = ["A리그", "B리그", "C리그", "D리그", "E리그"]

# 리그별 색상 (순서대로)
LEAGUE_COLORS = ["#2e7d32", "#1565c0", "#6a1b9a", "#e65100", "#00695c"]

# 코드 접두사: A/B/C/D/E
LEAGUE_PREFIXES = ["A", "B", "C", "D", "E"]




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
    name:          str
    league:        str
    game_count:    int = 0
    mixed_count:   int = 0
    round_records: Dict[str, str] = field(default_factory=dict)
    type_counts:   Dict[str, int] = field(default_factory=lambda: {
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

def get_league_color(league_name: str) -> str:
    """리그 이름으로 색상 반환"""
    try:
        idx = LEAGUE_NAMES.index(league_name)
        return LEAGUE_COLORS[idx]
    except ValueError:
        return "#555555"


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
# 섹션 4: 리그 우선순위 & 쿼터 (동적)
# ============================================================

def get_priority(league_name: str, league_configs: dict) -> List[str]:
    """
    league_configs: {league_name: {"priority": "동성우선"/"혼복우선", "mixed_max": int|None, "dong_min": int|None}}
    """
    cfg = league_configs.get(league_name, {})
    ptype = cfg.get("priority", "동성우선")
    if ptype == "혼복우선":
        return ["혼복", "동성", "잡복"]
    else:
        return ["동성", "혼복", "잡복"]

def get_quota(league_name: str, league_configs: dict) -> dict:
    cfg = league_configs.get(league_name, {})
    return {
        "mixed_max": cfg.get("mixed_max", None),
        "dong_min":  cfg.get("dong_min",  None),
    }

def mixed_quota_ok(p, mixed_counts, league_name, league_configs):
    q = get_quota(league_name, league_configs)
    if q["mixed_max"] is None: return True
    return mixed_counts.get(base_name(p), 0) < q["mixed_max"]


# ============================================================
# 섹션 5: 그룹 구성
# ============================================================

def build_one_group(pool, mixed_counts, league_name, league_configs):
    if len(pool) < 4: return None, pool[:]
    anchor = pool[0]; rest = pool[1:]
    g_a = get_gender(anchor)
    same = [p for p in rest if get_gender(p) == g_a]
    opp  = [p for p in rest if get_gender(p) != g_a and get_gender(p) != "U"]
    priority = get_priority(league_name, league_configs)

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

def build_all_groups(pool, mixed_counts, league_name, league_configs):
    groups, remaining = [], list(pool)
    while len(remaining) >= 4:
        group, remaining = build_one_group(remaining, mixed_counts, league_name, league_configs)
        if group is None: break
        groups.append(group)
    return groups, remaining


# ============================================================
# 섹션 6: 정규 라운드 매치 생성
# ============================================================

def _pick_3_for_anchor(anchor, remaining, mixed_counts, league_name, league_configs):
    if len(remaining) < 3: return None
    g = get_gender(anchor)
    men   = [p for p in remaining if get_gender(p) == "M"]
    women = [p for p in remaining if get_gender(p) == "W"]
    priority = get_priority(league_name, league_configs)
    opp_quota = [p for p in (women if g=="M" else men)
                 if mixed_quota_ok(p, mixed_counts, league_name, league_configs)]
    opp_all   = women if g=="M" else men
    anchor_ok = mixed_quota_ok(anchor, mixed_counts, league_name, league_configs)

    def try_dongsong():
        if g=="M" and len(men)>=3: return men[:3]
        if g=="W" and len(women)>=3: return women[:3]
        return None
    def try_mixed():
        if not anchor_ok: return None
        opp_use = opp_quota if len(opp_quota)>=2 else []
        if not opp_use: return None
        same_q = [p for p in (men if g=="M" else women)
                  if mixed_quota_ok(p, mixed_counts, league_name, league_configs)]
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


def make_round_matches(players, game_counts, mixed_counts, gs, rs,
                       league_name, league_configs, dong_forced=False):
    n_groups = len(players)//4
    if n_groups == 0: return []

    gender_count = {}
    for p in players:
        g = get_gender(p); gender_count[g] = gender_count.get(g,0)+1

    priority = get_priority(league_name, league_configs)
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
        quota_ok_m = [p for p in men_all   if mixed_quota_ok(p,mixed_counts,league_name,league_configs)]
        quota_ok_w = [p for p in women_all if mixed_quota_ok(p,mixed_counts,league_name,league_configs)]
        max_by_quota = min(len(quota_ok_m)//2, len(quota_ok_w)//2)
        minority_cnt = min(len(men_all), len(women_all))
        dong_possible = len(men_all)//4+len(women_all)//4
        mixed_possible = (max_by_quota>0 and minority_cnt>=2)

        if not mixed_possible or dong_forced:
            minority_groups_needed = math.ceil(minority_cnt/4) if minority_cnt>0 else 0
            dong_slots = (min(dong_possible,n_groups) if dong_forced
                          else min(dong_possible,max(0,n_groups-minority_groups_needed)))
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
                men_avail   = [p for p in working if get_gender(p)=="M"
                               and mixed_quota_ok(p,mixed_counts,league_name,league_configs)]
                women_avail = [p for p in working if get_gender(p)=="W"
                               and mixed_quota_ok(p,mixed_counts,league_name,league_configs)]
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
        anchor_lname = LEAGUE_NAMES[0] if dong_still_needed else league_name

        for anchor in anchors:
            three = _pick_3_for_anchor(anchor,remaining_pool,mixed_counts,anchor_lname,league_configs)
            if three is None or len(three)<3:
                three = _pick_3_for_anchor(anchor,remaining_pool,mixed_counts,league_name,league_configs)
            if three is None or len(three)<3:
                remaining_pool.insert(0,anchor); continue
            grp = [anchor]+three; groups_of_4.append(grp)
            for p in grp:
                if p in remaining_pool: remaining_pool.remove(p)

        if len(remaining_pool)>=4:
            extra,_ = build_all_groups(remaining_pool,mixed_counts,league_name,league_configs)
            groups_of_4.extend(extra)

    if not groups_of_4:
        groups_of_4,_ = build_all_groups(working,mixed_counts,league_name,league_configs)

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

def build_event_round(players, game_counts, mixed_counts,
                      league_name, league_configs, min_games=3, max_games=4):
    all_groups = []
    local_counts = dict(game_counts)
    gender_count = {}
    for p in players:
        g=get_gender(p); gender_count[g]=gender_count.get(g,0)+1

    for _ in range(20):
        need = [p for p in players if local_counts.get(base_name(p),0)<min_games]
        if not need: break
        avail = [p for p in players if p not in need
                 and local_counts.get(base_name(p),0)<max_games]
        pool = sorted(need, key=lambda p:(
            local_counts.get(base_name(p),0),
            gender_count.get(get_gender(p),99),
            random.random()
        ))

        while len(pool)%4!=0:
            cands = sort_by_mixed_least([p for p in avail if p not in pool],mixed_counts)
            if not cands: pool=pool[:(len(pool)//4)*4]; break
            pool.append(cands.pop(0))

        if len(pool)<4: break
        groups, leftovers = build_all_groups(pool,mixed_counts,league_name,league_configs)

        if leftovers:
            cands = sort_by_mixed_least(
                [p for p in avail if p not in leftovers and p not in pool],mixed_counts)
            while len(leftovers)<4 and cands: leftovers.append(cands.pop(0))
            if len(leftovers)>=4:
                eg,_ = build_all_groups(leftovers,mixed_counts,league_name,league_configs)
                groups.extend(eg)

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

def update_stats(stats, team1, team2, match_type, round_name, league_name):
    for p_raw in list(team1)+list(team2):
        p = base_name(p_raw)
        if p not in stats: stats[p] = PlayerStats(name=p, league=league_name)
        s = stats[p]
        s.game_count += 1
        s.type_counts[match_type] = s.type_counts.get(match_type,0)+1
        if is_mixed_match(match_type): s.mixed_count += 1
        dup = "(중복)" in p_raw
        s.round_records[round_name] = match_type+("★" if dup else "")


# ============================================================
# 섹션 9-A: 조건부 랜덤 스케줄 생성
# ============================================================

def generate_schedule_from_leagues(league_players, league_configs, num_rounds=3):
    """
    league_players: {league_name: [player_code, ...]}
    league_configs: {league_name: {"priority": str, "mixed_max": int|None, "dong_min": int|None}}
    """
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
            matches = make_round_matches(
                players, game_counts, mixed_counts, gs, rs,
                league_name, league_configs, dong_forced=(r==num_rounds)
            )
            for m in matches:
                t1,t2,mt = m["team1"],m["team2"],m["type"]
                for p_raw in list(t1)+list(t2):
                    p=base_name(p_raw); game_counts[p]+=1
                    if is_mixed_match(mt): mixed_counts[p]+=1
                update_stats(all_stats,t1,t2,mt,rname,league_name)
                all_results.append({"round":rname,"league":league_name,
                                     "team1":t1,"team2":t2,"type":mt})

        rs = MatchState()
        for raw_g, tagged_g in build_event_round(
            players, game_counts, mixed_counts, league_name, league_configs
        ):
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
            all_results.append({"round":"4R(이벤트)","league":league_name,
                                  "team1":t1,"team2":t2,"type":note})

    return all_results, all_stats


# ============================================================
# 섹션 9-B: 완전 랜덤 스케줄 생성
# ============================================================

def _is_gender_vs_gender(t1, t2) -> bool:
    """팀1 전원 남자 & 팀2 전원 여자, 또는 그 반대 → True (금지)"""
    g1 = {get_gender(p) for p in t1}
    g2 = {get_gender(p) for p in t2}
    if g1 == {"M"} and g2 == {"W"}: return True
    if g1 == {"W"} and g2 == {"M"}: return True
    return False

def best_pairing_fully_random(players4, gs, rs):
    a, b, c, d = players4
    all_pairs = [((a,b),(c,d)), ((a,c),(b,d)), ((a,d),(b,c))]
    random.shuffle(all_pairs)
    valid_pairs = [(t1,t2) for t1,t2 in all_pairs if not _is_gender_vs_gender(t1,t2)]
    cands = valid_pairs if valid_pairs else all_pairs
    best, best_s = None, float("inf")
    for t1, t2 in cands:
        s = score_pairing(t1, t2, gs, rs)
        if s < best_s: best_s, best = s, (t1, t2)
    return best

def _build_jabbok_minimized_groups(pool):
    """
    잡복(남3+여1, 남1+여3) 최소화 그룹 구성.
    혼복(남2+여2)과 동성(남복+여복)은 동등한 우선순위로,
    잡복 최소화 후 남은 조합에서 균등 랜덤 선택.

    [버그수정 v4.01]
    기존: 스코어(잡복+균형 가중합) 동점 조합만 candidates 수집
      → 남10:여5 등 특정 비율에서 dong_w=0인 조합만 동점으로 묶여
         여복(dong_w≥1)이 수학적으로 선택 불가능한 상태 발생.
    수정: "잡복 수가 최소인" 조합 전체를 candidates로 사용
      → 잡복 수만 최소 조건으로 1차 필터 후 나머지는 랜덤 선택
      → 여복/남복/혼복이 인원 비율에 맞게 골고루 등장.
    반환: groups (list of list), leftover (list)
    """
    men   = [p for p in pool if get_gender(p) == "M"]
    women = [p for p in pool if get_gender(p) == "W"]
    other = [p for p in pool if get_gender(p) == "U"]
    random.shuffle(men); random.shuffle(women); random.shuffle(other)

    M, W = len(men), len(women)
    N = (M + W + len(other)) // 4

    # 모든 유효 조합 수집 (mixed, dong_m, dong_w, jab_grps)
    all_valid = []
    for mixed in range(min(M // 2, W // 2) + 1):
        rem_m = M - mixed * 2
        rem_w = W - mixed * 2
        if rem_m < 0 or rem_w < 0: continue
        dong_m   = rem_m // 4
        dong_w   = rem_w // 4
        jab_grps = (rem_m % 4 + rem_w % 4) // 4
        total    = mixed + dong_m + dong_w + jab_grps
        if total != N: continue
        all_valid.append((mixed, dong_m, dong_w, jab_grps))

    best_mixed, best_dong_m, best_dong_w = 0, 0, 0
    if all_valid:
        # [핵심 수정] 잡복 수 최소인 조합 전체를 candidates로 사용
        # 잡복 수가 같은 조합끼리는 균등 랜덤 선택 → 여복 출현 보장
        min_jab = min(j for _, _, _, j in all_valid)
        best_candidates = [
            (m, dm, dw)
            for m, dm, dw, j in all_valid
            if j == min_jab
        ]
        best_mixed, best_dong_m, best_dong_w = random.choice(best_candidates)

    groups = []
    m_pool = list(men)
    w_pool = list(women)

    # 혼복 그룹 (남2+여2)
    for _ in range(best_mixed):
        if len(m_pool) >= 2 and len(w_pool) >= 2:
            groups.append(m_pool[:2] + w_pool[:2])
            m_pool = m_pool[2:]; w_pool = w_pool[2:]

    # 남복 그룹 (남4)
    for _ in range(best_dong_m):
        if len(m_pool) >= 4:
            groups.append(m_pool[:4]); m_pool = m_pool[4:]

    # 여복 그룹 (여4)
    for _ in range(best_dong_w):
        if len(w_pool) >= 4:
            groups.append(w_pool[:4]); w_pool = w_pool[4:]

    # 나머지(잡복 불가피) → leftover
    leftover = m_pool + w_pool + other
    return groups, leftover


def _make_fully_random_round(players, game_counts, gs, rs):
    """
    완전 랜덤 1라운드 생성 (잡복 최소화 버전).
    그룹 구성: 남2+여2 우선 → 남4/여4 → 불가피 잡복
    페어링: 팀 내에서 무작위 (남팀 vs 여팀 대결만 제한)
    """
    if len(players) < 4: return []

    # 경기 수 적은 순 정렬 후 사용할 인원만 추출
    pool = sorted(players, key=lambda p: (game_counts.get(base_name(p), 0), random.random()))
    n_groups = len(pool) // 4
    if n_groups == 0: return []
    working = pool[:n_groups * 4]

    # 잡복 최소화 그룹 구성
    groups, leftover = _build_jabbok_minimized_groups(working)

    # leftover가 4명 이상이면 그냥 순서대로 묶음 (완전 랜덤 fallback)
    random.shuffle(leftover)
    while len(leftover) >= 4:
        groups.append(leftover[:4])
        leftover = leftover[4:]

    # 그룹 수가 n_groups보다 적으면 부족분 보충 (엣지 케이스)
    # → 발생 시 leftover를 기존 그룹에 합쳐 재구성
    if len(groups) < n_groups and leftover:
        for p in leftover:
            if groups:
                groups[-1].append(p)

    matches = []
    for grp in groups:
        if len(grp) < 4: continue
        # 4명 초과 시 앞 4명만 사용 (엣지케이스 방어)
        grp4 = grp[:4]
        random.shuffle(grp4)
        t1, t2 = best_pairing_fully_random(grp4, gs, rs)
        commit_pairing(t1, t2, gs, rs)
        mt = classify_match([base_name(p) for p in list(t1)+list(t2)])
        matches.append({"team1": t1, "team2": t2, "type": mt})
    return matches


def _build_event_round_fully_random(players, game_counts, min_games=3, max_games=4):
    all_groups = []
    local_counts = dict(game_counts)
    for _ in range(20):
        need = [p for p in players if local_counts.get(base_name(p), 0) < min_games]
        if not need: break
        avail = [p for p in players if p not in need
                 and local_counts.get(base_name(p), 0) < max_games]

        # 잡복 최소화 구성 시도
        pool_need = list(need)
        avail_s   = list(avail); random.shuffle(avail_s)

        # 4의 배수 맞추기
        while len(pool_need) % 4 != 0:
            if not avail_s: pool_need = pool_need[:(len(pool_need)//4)*4]; break
            pool_need.append(avail_s.pop(0))

        if len(pool_need) < 4: break

        # 잡복 최소화 그룹 구성
        groups, leftover = _build_jabbok_minimized_groups(pool_need)
        random.shuffle(leftover)
        while len(leftover) >= 4:
            groups.append(leftover[:4])
            leftover = leftover[4:]

        if not groups: break
        for g in groups:
            tagged = []
            for p in g:
                pn = base_name(p)
                tagged.append(pn+"(중복)" if local_counts.get(pn,0) >= min_games else pn)
                local_counts[pn] = local_counts.get(pn, 0) + 1
            all_groups.append((g, tagged))
    return all_groups

def generate_schedule_fully_random(league_players, num_rounds=3):
    all_results = []
    all_stats   = {}
    for league_name, players in league_players.items():
        if len(players) < 4: continue
        game_counts = {p: 0 for p in players}
        gs = MatchState()
        for r in range(1, num_rounds+1):
            rname = f"{r}R"
            rs = MatchState()
            matches = _make_fully_random_round(players, game_counts, gs, rs)
            for m in matches:
                t1, t2, mt = m["team1"], m["team2"], m["type"]
                for p_raw in list(t1)+list(t2):
                    p = base_name(p_raw); game_counts[p] = game_counts.get(p,0)+1
                update_stats(all_stats, t1, t2, mt, rname, league_name)
                all_results.append({"round":rname,"league":league_name,
                                     "team1":t1,"team2":t2,"type":mt})
        event_gs = MatchState()
        for raw_g, tagged_g in _build_event_round_fully_random(players, game_counts):
            random.shuffle(tagged_g)
            t1, t2 = best_pairing_fully_random(tagged_g, gs, event_gs)
            commit_pairing(t1, t2, gs, event_gs)
            mt = classify_match([base_name(p) for p in list(t1)+list(t2)])
            has_dup = any("(중복)" in p for p in list(t1)+list(t2))
            note = mt+("(중복)" if has_dup else "")
            for p_raw in list(t1)+list(t2):
                p = base_name(p_raw); game_counts[p] = game_counts.get(p,0)+1
            update_stats(all_stats, t1, t2, mt, "4R(이벤트)", league_name)
            all_results.append({"round":"4R(이벤트)","league":league_name,
                                  "team1":t1,"team2":t2,"type":note})
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
            "1R": s.round_records.get("1R","-"),
            "2R": s.round_records.get("2R","-"),
            "3R": s.round_records.get("3R","-"),
            "4R(이벤트)": s.round_records.get("4R(이벤트)","-"),
            "남복": s.type_counts.get("남복",0),
            "여복": s.type_counts.get("여복",0),
            "혼복": s.type_counts.get("혼복",0),
            "잡복": s.type_counts.get("잡복",0),
            "혼성합계": s.mixed_count,
            "총경기": s.game_count,
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
        sc  = scores.get(str(idx), {})
        s1  = sc.get("score1", None)
        s2  = sc.get("score2", None)
        league = match["league"]
        t1  = [base_name(p) for p in match["team1"]]
        t2  = [base_name(p) for p in match["team2"]]
        rnd = match["round"]
        rnd_num = (1 if rnd=="1R" else 2 if rnd=="2R" else 3 if rnd=="3R"
                   else 4 if ("4R" in rnd or "이벤트" in rnd) else None)

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
# 섹션 13: 직렬화 헬퍼
# ============================================================

def serialize_schedule(schedule):
    return [{
        "round": m["round"], "league": m["league"],
        "team1": list(m["team1"]), "team2": list(m["team2"]), "type": m["type"],
    } for m in schedule]

def deserialize_schedule(schedule):
    return [{
        "round": m["round"], "league": m["league"],
        "team1": tuple(m["team1"]), "team2": tuple(m["team2"]), "type": m["type"],
    } for m in schedule]


# ============================================================
# 섹션 14: Streamlit 앱
# ============================================================

st.set_page_config(page_title="TELA Tennis Match", page_icon="🎾", layout="wide")

st.markdown("""
<style>
[data-testid="stSidebar"] { min-width:230px; max-width:270px; }
.match-card { border:1px solid #ddd; border-radius:6px; margin-bottom:4px; overflow:hidden; background:#fff; }
</style>
""", unsafe_allow_html=True)

# ── 네비게이션 ───────────────────────────────────────────────
st.sidebar.markdown("## 🎾 TELA TENNIS CLUB")
st.sidebar.markdown("---")
page = st.sidebar.radio("메뉴", ["📊 점수판", "🎲 랜덤페어"],
                         index=0, label_visibility="collapsed")
st.sidebar.markdown("---")


# ============================================================
# 페이지 A: 점수판
# ============================================================

if page == "📊 점수판":

    st.markdown("## 🎾 TELA 테니스 클럽 랜덤페어 점수판")

    today_str  = date.today().strftime("%Y-%m-%d")
    saved_keys = shelf_list_dates()

    sb_mode = st.radio("모드", ["새 점수판 (날짜+번호 입력)", "저장된 점수판 불러오기"],
                       index=0, horizontal=True, label_visibility="collapsed")
    if sb_mode == "새 점수판 (날짜+번호 입력)":
        sb_date = st.text_input("날짜 (YYYY-MM-DD)", value=today_str, key="sb_date_inp")
        sb_num  = st.text_input("일련번호 (예: 001)", value="001", key="sb_num_inp")
        selected_key = f"{sb_date}_{sb_num}"
    else:
        if saved_keys:
            selected_key = st.selectbox("저장된 점수판 선택", saved_keys)
        else:
            st.info("저장된 데이터가 없습니다.")
            selected_key = f"{today_str}_001"

    st.caption(f"현재 키: **{selected_key}**")

    if st.session_state.get("sb_key") != selected_key:
        st.session_state["sb_key"] = selected_key
        loaded = shelf_load(selected_key)
        if loaded:
            st.session_state["sb_schedule"] = deserialize_schedule(loaded["schedule"])
            st.session_state["sb_scores"]   = loaded["scores"]
        else:
            rp_sched = st.session_state.get("rp_schedule")
            rp_key   = st.session_state.get("rp_key", "")
            if rp_sched and rp_key == selected_key:
                st.session_state["sb_schedule"] = rp_sched
                st.session_state["sb_scores"]   = {}
            else:
                st.session_state["sb_schedule"] = None
                st.session_state["sb_scores"]   = {}

    schedule = st.session_state.get("sb_schedule")
    if not schedule:
        st.warning("⚠️ 이 키에 저장된 대진표가 없습니다.")
        st.info("👈 **🎲 랜덤페어**에서 같은 날짜+일련번호로 대진표를 생성하거나, 저장된 키를 선택해주세요.")
        st.stop()

    scores = st.session_state.setdefault("sb_scores", {})
    rounds = []
    seen_r = set()
    for m in schedule:
        if m["round"] not in seen_r:
            rounds.append(m["round"]); seen_r.add(m["round"])

    parts = selected_key.split("_")
    disp_date = parts[0] if parts else selected_key
    disp_num  = parts[1] if len(parts) > 1 else ""
    st.markdown(
        f'<div style="text-align:right;font-size:0.85rem;color:#666;margin-bottom:8px;">'
        f'{disp_date} · {disp_num}</div>', unsafe_allow_html=True)

    qp  = st.query_params
    act = qp.get("act", None)
    if act is not None:
        try:
            pidx = int(qp.get("idx", -1))
            if act == "save" and pidx >= 0:
                s1v = int(qp.get("s1", 0))
                s2v = int(qp.get("s2", 0))
                scores[str(pidx)] = {"score1": s1v, "score2": s2v}
                st.session_state["sb_scores"] = scores
                st.session_state[f"locked_{pidx}"] = True
                shelf_save(selected_key, serialize_schedule(st.session_state["sb_schedule"]), scores)
            elif act == "edit" and pidx >= 0:
                st.session_state[f"locked_{pidx}"] = False
        except Exception:
            pass
        st.query_params.clear()
        st.rerun()

    import json as _json

    def pname_short(code):
        raw = base_name(code)
        if is_custom_code(raw):
            g = "(남)" if raw[1].upper()=="M" else "(여)"
            return raw[2:] + g
        return raw

    def build_full_html(schedule, rounds, scores, session_state):
        # 리그별 색상 동적 생성
        league_list = list(dict.fromkeys(m["league"] for m in schedule))
        lg_color_map = {lg: get_league_color(lg) for lg in league_list}

        matches_data = []
        for idx, match in enumerate(schedule):
            sc        = scores.get(str(idx), {})
            is_locked = session_state.get(f"locked_{idx}", bool(sc))
            matches_data.append({
                "idx":    idx,
                "round":  match["round"],
                "league": match["league"],
                "lc":     lg_color_map.get(match["league"], "#555"),
                "t1a":    pname_short(match["team1"][0]),
                "t1b":    pname_short(match["team1"][1]),
                "t2a":    pname_short(match["team2"][0]),
                "t2b":    pname_short(match["team2"][1]),
                "type":   match["type"],
                "s1":     sc.get("score1", 0),
                "s2":     sc.get("score2", 0),
                "locked": is_locked,
            })

        mj = _json.dumps(matches_data, ensure_ascii=False)
        rj = _json.dumps(rounds, ensure_ascii=False)

        return f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<style>
*{{box-sizing:border-box;margin:0;padding:0;}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Malgun Gothic',sans-serif;
      background:#f5f5f5;padding:4px;font-size:14px;}}
.rnd-hdr{{background:#1a1a2e;color:#fff;font-weight:700;font-size:0.88rem;
           text-align:center;padding:7px 4px;border-radius:6px;margin:8px 0 5px;letter-spacing:1px;}}
.lg-lbl{{font-size:0.72rem;font-weight:700;padding:2px 0 3px 6px;margin:3px 0 2px;}}
.mc{{border:1px solid #e0e0e0;border-radius:6px;overflow:hidden;background:#fff;margin-bottom:4px;}}
.mc.lk{{background:#f0fff0;border-color:#a5d6a7;}}
.mc-body{{display:flex;align-items:stretch;}}
.mc-tl{{flex:3;padding:5px 2px 3px 6px;min-width:0;}}
.mc-tr{{flex:3;padding:5px 6px 3px 2px;text-align:right;min-width:0;}}
.mc-sc{{flex:0 0 26px;background:#f0f0f0;display:flex;align-items:center;
         justify-content:center;font-size:0.9rem;font-weight:800;color:#222;}}
.mc-vs{{flex:0 0 14px;display:flex;align-items:center;justify-content:center;
         font-size:0.55rem;color:#bbb;}}
.mc-ft{{background:#fafafa;font-size:0.6rem;color:#aaa;text-align:right;padding:1px 6px;}}
.mc-bj{{font-size:0.62rem;color:#2e7d32;font-weight:700;text-align:right;padding:1px 6px;}}
.pn{{font-size:0.75rem;font-weight:600;line-height:1.35;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}}
.pw{{color:#b71c1c!important;font-weight:800!important;}}
.inp-row{{display:flex;flex-direction:row;align-items:center;gap:3px;padding:3px 3px 4px;width:100%;}}
.inp-box{{flex:1;display:flex;flex-direction:row;align-items:center;border:1px solid #ccc;
           border-radius:5px;overflow:hidden;background:#f8f8f8;height:34px;min-width:0;}}
.ibtn{{width:28px;height:34px;border:none;background:#e5e5e5;font-size:1rem;font-weight:700;
        color:#333;cursor:pointer;flex-shrink:0;-webkit-tap-highlight-color:transparent;
        touch-action:manipulation;display:flex;align-items:center;justify-content:center;}}
.ibtn:active{{background:#ccc;}}
.inum{{flex:1;min-width:0;text-align:center;font-size:0.95rem;font-weight:700;
        border:none;background:transparent;-moz-appearance:textfield;}}
.inum::-webkit-inner-spin-button,.inum::-webkit-outer-spin-button{{-webkit-appearance:none;}}
.sbtn{{flex:0 0 52px;height:34px;background:#e53935;color:#fff;border:none;
        border-radius:5px;font-size:0.78rem;font-weight:700;cursor:pointer;
        white-space:nowrap;-webkit-tap-highlight-color:transparent;touch-action:manipulation;}}
.sbtn:active{{background:#b71c1c;}}
.ebtn{{flex:0 0 52px;height:34px;background:#1565c0;color:#fff;border:none;
        border-radius:5px;font-size:0.78rem;font-weight:700;cursor:pointer;
        white-space:nowrap;-webkit-tap-highlight-color:transparent;touch-action:manipulation;}}
.ebtn:active{{background:#0d47a1;}}
.scr-disp{{flex:1;height:34px;background:#ebebeb;display:flex;align-items:center;
            justify-content:center;font-size:0.95rem;font-weight:700;color:#444;
            border-radius:5px;border:1px solid #ddd;}}
</style></head><body>
<div id="root"></div>
<script>
(function(){{
  const matches={mj};
  const rounds={rj};
  const MAX=6,MIN=0;
  const scores={{}};const locked={{}};
  matches.forEach(m=>{{scores[m.idx]={{s1:m.s1,s2:m.s2}};locked[m.idx]=m.locked;}});

  function pWin(a,b){{return (a+b)>0&&a>b;}}
  function render(){{
    const root=document.getElementById('root');root.innerHTML='';
    rounds.forEach(rnd=>{{
      const ms=matches.filter(m=>m.round===rnd);if(!ms.length)return;
      const lbl=rnd.replace('(이벤트)','')+(rnd.includes('이벤트')?' ⭐':'');
      const h=document.createElement('div');h.className='rnd-hdr';h.textContent=lbl;root.appendChild(h);
      const lgs=[...new Set(ms.map(m=>m.league))];
      lgs.forEach(lg=>{{
        const lc=ms.find(m=>m.league===lg).lc;
        const ld=document.createElement('div');ld.className='lg-lbl';
        ld.style.cssText=`color:${{lc}};border-left:3px solid ${{lc}};padding-left:6px;`;
        ld.textContent=lg;root.appendChild(ld);
        ms.filter(m=>m.league===lg).forEach(m=>{{root.appendChild(buildMatch(m));}});
      }});
    }});
  }}
  function buildMatch(m){{const w=document.createElement('div');w.id='w'+m.idx;redraw(m,w);return w;}}
  function redraw(m,w){{
    if(!w)w=document.getElementById('w'+m.idx);
    const sc=scores[m.idx];const lk=locked[m.idx];const lc=m.lc;
    const t1w=pWin(sc.s1,sc.s2),t2w=pWin(sc.s2,sc.s1);
    const p1=t1w?'pw':'',p2=t2w?'pw':'';
    w.innerHTML=`
<div class="mc${{lk?' lk':''}}" style="border-left:4px solid ${{lc}}">
  <div class="mc-body">
    <div class="mc-tl"><div class="pn ${{p1}}">${{m.t1a}}</div><div class="pn ${{p1}}">${{m.t1b}}</div></div>
    <div class="mc-sc">${{sc.s1}}</div><div class="mc-vs">vs</div><div class="mc-sc">${{sc.s2}}</div>
    <div class="mc-tr"><div class="pn ${{p2}}">${{m.t2a}}</div><div class="pn ${{p2}}">${{m.t2b}}</div></div>
  </div>
  <div class="mc-ft">${{m.type}}</div>
  ${{lk?'<div class="mc-bj">✅ 저장완료</div>':''}}
</div>
<div class="inp-row">
  ${{lk
    ?`<div class="scr-disp">${{sc.s1}}</div>
      <button class="ebtn" onclick="doEdit(${{m.idx}})">✏️ 수정</button>
      <div class="scr-disp">${{sc.s2}}</div>`
    :`<div class="inp-box">
        <button class="ibtn" onclick="adj(${{m.idx}},1,-1)">−</button>
        <input class="inum" id="i1_${{m.idx}}" type="number" value="${{sc.s1}}" min="${{MIN}}" max="${{MAX}}"
               oninput="onInp(${{m.idx}},1,this.value)">
        <button class="ibtn" onclick="adj(${{m.idx}},1,1)">+</button>
      </div>
      <button class="sbtn" onclick="doSave(${{m.idx}})">💾 저장</button>
      <div class="inp-box">
        <button class="ibtn" onclick="adj(${{m.idx}},2,-1)">−</button>
        <input class="inum" id="i2_${{m.idx}}" type="number" value="${{sc.s2}}" min="${{MIN}}" max="${{MAX}}"
               oninput="onInp(${{m.idx}},2,this.value)">
        <button class="ibtn" onclick="adj(${{m.idx}},2,1)">+</button>
      </div>`
  }}
</div>`;
  }}
  window.adj=function(idx,t,d){{
    const el=document.getElementById((t===1?'i1_':'i2_')+idx);
    let v=parseInt(el.value||'0')+d;if(v<MIN)v=MIN;if(v>MAX)v=MAX;
    el.value=v;scores[idx][t===1?'s1':'s2']=v;
  }};
  window.onInp=function(idx,t,val){{
    let v=parseInt(val)||0;if(v<MIN)v=MIN;if(v>MAX)v=MAX;scores[idx][t===1?'s1':'s2']=v;
  }};
  window.doSave=function(idx){{
    const s1=scores[idx].s1,s2=scores[idx].s2;locked[idx]=true;
    const m=matches.find(x=>x.idx===idx);redraw(m);
    const url=new URL(window.top.location.href);
    url.searchParams.set('act','save');url.searchParams.set('idx',idx);
    url.searchParams.set('s1',s1);url.searchParams.set('s2',s2);
    window.top.location.href=url.toString();
  }};
  window.doEdit=function(idx){{
    locked[idx]=false;const m=matches.find(x=>x.idx===idx);redraw(m);
    const url=new URL(window.top.location.href);
    url.searchParams.set('act','edit');url.searchParams.set('idx',idx);
    window.top.location.href=url.toString();
  }};
  render();
}})();
</script></body></html>"""

    sb_html = build_full_html(schedule, rounds, scores, st.session_state)
    n = len(schedule); n_rounds = len(rounds)
    n_leagues = len(set(m["league"] for m in schedule))
    est = (n * 112) + (n_rounds * 40) + (n_rounds * n_leagues * 24) + 60
    st.components.v1.html(sb_html, height=est, scrolling=False)

    st.markdown("---")
    if st.button("🔄 점수 전체 초기화", type="secondary"):
        for i in range(len(schedule)):
            st.session_state.pop(f"locked_{i}", None)
        st.session_state["sb_scores"] = {}
        st.rerun()

    st.markdown("### 📈 선수별 통계")
    df_sb = compute_scoreboard_stats(schedule, st.session_state.get("sb_scores", {}))
    if df_sb.empty:
        st.info("점수를 저장하면 통계가 표시됩니다.")
    else:
        all_leagues = df_sb["리그"].unique()
        for league in all_leagues:
            df_lg = df_sb[df_sb["리그"]==league].drop(columns=["리그"]).reset_index(drop=True)
            if df_lg.empty: continue
            lg_color = get_league_color(league)
            st.markdown(
                f'<div style="color:{lg_color};font-weight:700;border-bottom:2px solid {lg_color};'
                f'padding-bottom:4px;margin:16px 0 8px 0;">🎾 {league} 통계</div>',
                unsafe_allow_html=True)
            max_win = int(df_lg["승"].max()) if not df_lg.empty else 0
            def hl_sb(row, mw=max_win):
                styles = [""]*len(row)
                if "승" in row.index:
                    wi = row.index.get_loc("승")
                    if row["승"]==mw and mw>0:
                        styles[wi] = "background-color:#FFF176;font-weight:bold"
                return styles
            st.dataframe(df_lg.style.apply(hl_sb, axis=1),
                         use_container_width=True, hide_index=True)


# ============================================================
# 페이지 B: 랜덤페어
# ============================================================

elif page == "🎲 랜덤페어":

    # ── [1] 페어링 방식 ──────────────────────────────────────
    st.sidebar.markdown("### 🎯 페어링 방식")
    pairing_mode = st.sidebar.radio(
        "페어링 방식 선택",
        ["🔴 완전 랜덤페어", "🔵 조건부 랜덤페어"],
        index=0, label_visibility="collapsed",
    )
    IS_FULLY_RANDOM = (pairing_mode == "🔴 완전 랜덤페어")
    if IS_FULLY_RANDOM:
        st.sidebar.info("**완전 랜덤페어**\n\n완전 무작위\n\n✅ 남자팀 vs 여자팀 대결만 제한")
    else:
        st.sidebar.info("**조건부 랜덤페어**\n\n리그별 우선순위·쿼터 적용")
    st.sidebar.markdown("---")

    # ── [2] 리그 수 설정 (NEW) ───────────────────────────────
    st.sidebar.markdown("### 🏆 리그 설정")
    num_leagues = st.sidebar.number_input(
        "리그 수", min_value=1, max_value=5, value=2, step=1,
        help="1~5개 리그 설정 가능. A리그부터 순서대로 자동 부여됩니다."
    )
    active_leagues = LEAGUE_NAMES[:num_leagues]      # ["A리그"] ~ ["A리그","B리그","C리그","D리그","E리그"]
    active_prefixes = LEAGUE_PREFIXES[:num_leagues]

    # 리그별 우선순위 & 쿼터 설정 (조건부 랜덤일 때만 표시)
    league_configs = {}
    if not IS_FULLY_RANDOM:
        with st.sidebar.expander("⚙️ 리그별 상세 설정", expanded=(num_leagues > 0)):
            for i, lg in enumerate(active_leagues):
                lc = LEAGUE_COLORS[i]
                st.markdown(
                    f'<div style="color:{lc};font-weight:700;margin:6px 0 3px;">▶ {lg}</div>',
                    unsafe_allow_html=True
                )
                prio = st.radio(
                    f"{lg} 우선순위",
                    ["동성우선", "혼복우선"],
                    index=1 if i > 0 else 0,   # A리그=동성우선, 나머지=혼복우선 기본값
                    key=f"prio_{lg}",
                    horizontal=True,
                    label_visibility="collapsed",
                )
                use_quota = st.checkbox(f"쿼터 제한 적용 ({lg})", value=(i > 0), key=f"quota_{lg}")
                mixed_max_val = None
                dong_min_val  = None
                if use_quota:
                    col1, col2 = st.columns(2)
                    with col1:
                        mixed_max_val = st.number_input(
                            "혼성 최대", min_value=1, max_value=10, value=2,
                            step=1, key=f"mmax_{lg}"
                        )
                    with col2:
                        dong_min_val = st.number_input(
                            "동성 최소", min_value=0, max_value=5, value=1,
                            step=1, key=f"dmin_{lg}"
                        )
                league_configs[lg] = {
                    "priority":  prio,
                    "mixed_max": mixed_max_val,
                    "dong_min":  dong_min_val,
                }
    else:
        # 완전 랜덤은 config 불필요 (빈 dict)
        for lg in active_leagues:
            league_configs[lg] = {"priority": "동성우선", "mixed_max": None, "dong_min": None}

    st.sidebar.markdown("---")

    # ── [3] 입력 방식 ────────────────────────────────────────
    input_mode = st.sidebar.radio(
        "입력 방식",
        ["👥 회원 선택", "코드 자동 생성 (AM01/AW01...)", "직접 이름 입력"],
        index=0
    )
    st.sidebar.markdown("---")

    # 인원 입력 — 리그 수에 따라 동적 생성
    custom_input   = {}
    league_counts  = {}   # {league_name: {"m": int, "w": int}}
    member_selected = {}  # {league_name: [player_code, ...]}

    # ── 회원 선택 모드 ───────────────────────────────────────
    if input_mode == "👥 회원 선택":
        all_members = member_load_all()
        custom_input  = None
        league_counts = None

        # 사이드바에는 버튼만 표시 → 팝업에서 선택
        st.sidebar.markdown("---")
        if st.sidebar.button("👥 참가자 선택 열기", type="primary",
                             use_container_width=True, key="open_member_popup"):
            st.session_state["member_popup_open"] = True

        # 현재 선택 현황 요약 표시
        for i, lg in enumerate(active_leagues):
            pfx      = active_prefixes[i]
            lc       = LEAGUE_COLORS[i]
            lg_mem_i = all_members.get(lg, [])
            sel_names = {
                m["name"] for m in lg_mem_i
                if st.session_state.get(f"chk_{lg}_{m['name']}", True)
            }
            dormant_sel = sum(
                1 for m in lg_mem_i
                if m["name"] in sel_names and m.get("status") == "휴면"
            )
            summary_str = f"{lg}: {len(sel_names)}명 선택"
            if dormant_sel:
                summary_str += f" (휴면 {dormant_sel}명 포함)"
            st.sidebar.markdown(
                f'<span style="color:{lc};font-size:0.8rem;">{summary_str}</span>',
                unsafe_allow_html=True
            )

        # 선택 결과 수집 (session_state의 chk_ 키 기반)
        for i, lg in enumerate(active_leagues):
            pfx = active_prefixes[i]
            lg_members = all_members.get(lg, [])
            selected = []
            for m in lg_members:
                key = f"chk_{lg}_{m['name']}"
                if st.session_state.get(key, True):
                    selected.append(f"{pfx}{m['gender']}{m['name']}")
            member_selected[lg] = selected

    # ── 코드 자동 생성 모드 ──────────────────────────────────
    elif input_mode == "코드 자동 생성 (AM01/AW01...)":
        custom_input = None
        for i, lg in enumerate(active_leagues):
            lc = LEAGUE_COLORS[i]
            st.sidebar.markdown(
                f'<span style="color:{lc};font-weight:700;">{lg}</span>',
                unsafe_allow_html=True
            )
            col1, col2 = st.sidebar.columns(2)
            with col1:
                m_cnt = st.number_input(f"남자 ({lg})", min_value=0, max_value=30,
                                         value=8 if i==0 else 3, step=1, key=f"m_{lg}")
            with col2:
                w_cnt = st.number_input(f"여자 ({lg})", min_value=0, max_value=30,
                                         value=2, step=1, key=f"w_{lg}")
            league_counts[lg] = {"m": m_cnt, "w": w_cnt}

    # ── 직접 이름 입력 모드 ──────────────────────────────────
    else:
        league_counts = None
        for i, lg in enumerate(active_leagues):
            lc = LEAGUE_COLORS[i]
            st.sidebar.markdown(
                f'<span style="color:{lc};font-weight:700;">{lg} 선수 목록</span>',
                unsafe_allow_html=True
            )
            txt = st.sidebar.text_area(
                f"{lg} 입력", placeholder="홍길동 남\n김영희 여",
                height=100, key=f"txt_{lg}", label_visibility="collapsed"
            )
            custom_input[lg] = txt

    # ══════════════════════════════════════════════════════════
    # 참가자 선택 팝업 (dialog)
    # ══════════════════════════════════════════════════════════
    @st.dialog("👥 참가자 선택", width="large")
    def _member_select_popup():
        all_members_p = member_load_all()
        tabs_p = st.tabs([f"{lg}" for lg in active_leagues])
        for i, (tab_p, lg) in enumerate(zip(tabs_p, active_leagues)):
            with tab_p:
                pfx       = active_prefixes[i]
                lc        = LEAGUE_COLORS[i]
                lg_mem    = all_members_p.get(lg, [])
                if not lg_mem:
                    st.info(f"{lg}에 등록된 회원이 없습니다. 아래 '회원 관리'에서 추가하세요.")
                    continue

                # ── 전체선택/해제 버튼 ──────────────────────
                col_sa, col_sd, col_cnt = st.columns([1, 1, 3])
                if col_sa.button(f"✅ 전체선택", key=f"popup_sa_{lg}"):
                    for m in lg_mem:
                        st.session_state[f"chk_{lg}_{m['name']}"] = True
                    st.rerun()
                if col_sd.button(f"⬜ 전체해제", key=f"popup_sd_{lg}"):
                    for m in lg_mem:
                        st.session_state[f"chk_{lg}_{m['name']}"] = False
                    st.rerun()
                sel_cnt = sum(1 for m in lg_mem
                              if st.session_state.get(f"chk_{lg}_{m['name']}", True))
                col_cnt.markdown(
                    f'<div style="padding-top:6px;color:{lc};font-weight:700;">'
                    f'{sel_cnt} / {len(lg_mem)}명 선택</div>',
                    unsafe_allow_html=True
                )

                # ── 회원 체크박스 그리드 ────────────────────
                cols_per_row = 4
                rows = [lg_mem[j:j+cols_per_row] for j in range(0, len(lg_mem), cols_per_row)]
                for row in rows:
                    rcols = st.columns(cols_per_row)
                    for k, m in enumerate(row):
                        g_label    = "남" if m["gender"]=="M" else "여"
                        status     = m.get("status", "정상")
                        status_tag = " 💤" if status == "휴면" else ""
                        key = f"chk_{lg}_{m['name']}"
                        if key not in st.session_state:
                            st.session_state[key] = True
                        rcols[k].checkbox(
                            f"{m['name']}{status_tag} ({g_label})",
                            key=key
                        )

        if st.button("✔️ 확인", type="primary", use_container_width=True):
            st.session_state["member_popup_open"] = False
            st.rerun()

    if st.session_state.get("member_popup_open", False):
        _member_select_popup()

    st.sidebar.markdown("---")

    # ── [4] 시드 ─────────────────────────────────────────────
    use_seed = st.sidebar.checkbox("🔒 결과 고정 (시드)", value=False)
    seed_val = None
    if use_seed:
        seed_val = st.sidebar.number_input("시드 번호", min_value=0, max_value=9999,
                                            value=42, step=1)

    # ── [5] 날짜 & 일련번호 ──────────────────────────────────
    st.sidebar.markdown("---")
    st.sidebar.markdown("📅 **날짜 & 일련번호**")
    rp_date = st.sidebar.text_input("날짜 (YYYY-MM-DD)",
                                     value=date.today().strftime("%Y-%m-%d"), key="rp_date")
    rp_num  = st.sidebar.text_input("일련번호 (예: 001)", value="001", key="rp_num")
    rp_key  = f"{rp_date}_{rp_num}"
    st.sidebar.caption(f"저장 키: {rp_key}")

    # ── [6] 관리자 비밀번호 ──────────────────────────────────
    st.sidebar.markdown("---")
    st.sidebar.markdown("🔐 **관리자 확인**")
    admin_pw = st.sidebar.text_input("비밀번호", type="password", placeholder="비밀번호 입력")
    pw_ok = (admin_pw == ADMIN_PASSWORD)

    generate_btn = st.sidebar.button(
        "🎾 대진표 생성", type="primary", use_container_width=True, disabled=not pw_ok
    )
    if admin_pw and not pw_ok:
        st.sidebar.error("❌ 비밀번호가 틀렸습니다.")
    elif not admin_pw:
        st.sidebar.caption("비밀번호를 입력해야 생성할 수 있습니다.")

    # ── 메인 타이틀 ─────────────────────────────────────────
    mode_badge = "🔴 완전 랜덤" if IS_FULLY_RANDOM else "🔵 조건부"
    league_badge = " · ".join(active_leagues)
    st.title(f"🎾 TELA CLUB Random Match Generator v4.02")
    st.caption(f"{mode_badge} &nbsp;|&nbsp; {league_badge} &nbsp;|&nbsp; 최소 3경기 / 최대 4경기")

    # ── 대진표 생성 ──────────────────────────────────────────

    # ── 대진표 생성 ──────────────────────────────────────────
    # do_regen: pop 대신 get으로 읽고, 실제 실행 후에만 삭제
    do_regen = st.session_state.get("do_regen", False)
    has_saved_params = bool(st.session_state.get("last_gen_params"))

    if (generate_btn and pw_ok) or (do_regen and has_saved_params):

        # ── 재생성: 저장된 파라미터 그대로 사용 ─────────────
        if do_regen and has_saved_params:
            st.session_state["do_regen"] = False   # 소비 처리
            p = st.session_state["last_gen_params"]
            league_players      = p["league_players"]
            IS_FULLY_RANDOM_run = p["is_fully_random"]
            league_configs_run  = p["league_configs"]
            use_seed_run        = p["use_seed"]
            seed_val_run        = p["seed_val"]
            rp_key_run          = p["rp_key"]

        # ── 최초 생성: 사이드바 값으로 파라미터 구성 ────────
        else:
            st.session_state["do_regen"] = False

            # league_players 구성
            league_players = {}
            if input_mode == "👥 회원 선택":
                for lg in active_leagues:
                    league_players[lg] = member_selected.get(lg, [])
            elif custom_input is None:
                for i, lg in enumerate(active_leagues):
                    pfx = active_prefixes[i]
                    cnt = league_counts[lg]
                    players = (
                        [f"{pfx}M{j+1:02d}" for j in range(cnt["m"])] +
                        [f"{pfx}W{j+1:02d}" for j in range(cnt["w"])]
                    )
                    league_players[lg] = players
            else:
                for i, lg in enumerate(active_leagues):
                    pfx = active_prefixes[i]
                    txt = custom_input.get(lg, "")
                    league_players[lg] = parse_custom_players(txt, pfx) if txt.strip() else []

            IS_FULLY_RANDOM_run = IS_FULLY_RANDOM
            league_configs_run  = league_configs
            use_seed_run        = use_seed
            seed_val_run        = seed_val
            rp_key_run          = rp_key

            # 유효성 검사
            errors = []
            for lg, pl in league_players.items():
                if 0 < len(pl) < 4:
                    errors.append(f"{lg} 인원이 4명 미만입니다 ({len(pl)}명).")
            if not any(len(pl) >= 4 for pl in league_players.values()):
                errors.append("최소 한 리그에 4명 이상 입력해주세요.")
            if errors:
                for e in errors: st.error(e)
                st.stop()

            # 파라미터 저장 (재생성 시 재사용)
            st.session_state["last_gen_params"] = {
                "league_players":  league_players,
                "is_fully_random": IS_FULLY_RANDOM,
                "league_configs":  league_configs,
                "use_seed":        use_seed,
                "seed_val":        seed_val,
                "rp_key":          rp_key,
            }

        # 시드 고정 (시드 사용 시 재생성해도 동일 결과)
        if use_seed_run and seed_val_run is not None:
            random.seed(int(seed_val_run))

        spinner_msg = "완전 랜덤 대진표 생성 중..." if IS_FULLY_RANDOM_run else "조건부 대진표 생성 중..."
        with st.spinner(spinner_msg):
            if IS_FULLY_RANDOM_run:
                schedule, stats = generate_schedule_fully_random(league_players)
            else:
                schedule, stats = generate_schedule_from_leagues(league_players, league_configs_run)

        if not schedule:
            st.warning("경기를 생성할 수 없습니다."); st.stop()

        st.session_state.update({
            "schedule": schedule, "stats": stats, "scores": {},
            "rp_schedule": schedule, "rp_key": rp_key_run,
            "sb_schedule": schedule, "sb_scores": {}, "sb_key": "",
        })
        shelf_save(rp_key_run, serialize_schedule(schedule), {})
        mode_label   = "완전 랜덤" if IS_FULLY_RANDOM_run else "조건부 랜덤"
        active_lgs   = list(league_players.keys())
        league_badge_run = " · ".join(active_lgs)
        st.success(f"✅ [{mode_label} / {league_badge_run}] 대진표가 **{rp_key_run}** 키로 저장되었습니다.")

        # ── 재생성 버튼 ──────────────────────────────────────
        def _set_regen():
            st.session_state["do_regen"] = True

        col_regen, col_space = st.columns([1, 4])
        with col_regen:
            st.button("🔄 다시 생성", type="secondary", use_container_width=True,
                      on_click=_set_regen,
                      help="동일 설정으로 새로운 랜덤 대진표를 생성합니다 (시드 고정 시 동일 결과)")

        seed_label = f"시드 #{int(seed_val_run)}" if (use_seed_run and seed_val_run is not None) else "랜덤"

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
            st.subheader(f"경기 대진표 · {seed_label}  [{mode_label}]")
            lg_color_map = {lg: get_league_color(lg) for lg in active_lgs}
            def hl_match(row):
                bg = ""
                for lg, color in lg_color_map.items():
                    if str(row.get("리그","")) == lg:
                        bg = f"{color}18"
                        break
                if not bg: bg = "#f5f5f5"
                return [f"background-color:{bg};color:black"]*len(row)
            st.dataframe(df_matches.style.apply(hl_match, axis=1),
                         use_container_width=True, height=600)
            summary = df_matches["매치종류"].value_counts()
            st.caption(f"총 {len(df_matches)}경기 | "
                       +" | ".join(f"{k}: {v}경기" for k,v in summary.items()))
            # 총 참가 인원수
            total_players = sum(len(pl) for pl in league_players.values())
            per_league = " · ".join(
                f"{lg} {len(pl)}명" for lg, pl in league_players.items() if pl
            )
            st.caption(f"👥 총 {total_players}명  ({per_league})")

            # ── 카카오톡 복사 버튼 (5번 기능) ─────────────────
            import json as _json2

            def build_kakao_text(schedule, mode_label, rp_key_run):
                """라운드별 경기 목록을 카카오톡 붙여넣기용 텍스트로 변환"""
                lines = [f"🎾 TELA 대진표 [{mode_label}]", f"📅 {rp_key_run}", ""]
                rounds_seen = []
                seen_r = set()
                for m in schedule:
                    if m["round"] not in seen_r:
                        rounds_seen.append(m["round"]); seen_r.add(m["round"])

                for rnd in rounds_seen:
                    rnd_label = rnd.replace("(이벤트)", "") + (" ⭐" if "이벤트" in rnd else "")
                    lines.append(f"▣ {rnd_label}")
                    cur_league = None
                    for m in schedule:
                        if m["round"] != rnd: continue
                        if m["league"] != cur_league:
                            cur_league = m["league"]
                            lines.append(f"  [{cur_league}]")
                        t1a = display_name(m["team1"][0])
                        t1b = display_name(m["team1"][1])
                        t2a = display_name(m["team2"][0])
                        t2b = display_name(m["team2"][1])
                        mt  = m["type"]
                        lines.append(f"  {t1a}/{t1b} vs {t2a}/{t2b}  ({mt})")
                    lines.append("")
                return "\n".join(lines)

            kakao_text = build_kakao_text(schedule, mode_label, rp_key_run)
            kakao_json = _json2.dumps(kakao_text, ensure_ascii=False)

            kakao_html = f"""
<div style="margin:10px 0;">
  <button id="kakao-copy-btn" onclick="copyKakao()" style="
    background:#FEE500;color:#3C1E1E;border:none;border-radius:8px;
    padding:8px 18px;font-size:0.88rem;font-weight:700;cursor:pointer;
    display:inline-flex;align-items:center;gap:6px;
    -webkit-tap-highlight-color:transparent;">
    💬 카카오톡 복사
  </button>
  <span id="kakao-msg" style="margin-left:10px;font-size:0.8rem;color:#2e7d32;display:none;">✅ 복사됨!</span>
</div>
<script>
function copyKakao() {{
  const text = {kakao_json};
  if (navigator.clipboard && window.isSecureContext) {{
    navigator.clipboard.writeText(text).then(function() {{
      showMsg();
    }}).catch(function() {{ fallbackCopy(text); }});
  }} else {{
    fallbackCopy(text);
  }}
}}
function fallbackCopy(text) {{
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.style.cssText = 'position:fixed;left:-9999px;top:-9999px;';
  document.body.appendChild(ta);
  ta.focus(); ta.select();
  try {{ document.execCommand('copy'); showMsg(); }} catch(e) {{}}
  document.body.removeChild(ta);
}}
function showMsg() {{
  const m = document.getElementById('kakao-msg');
  m.style.display = 'inline';
  setTimeout(function() {{ m.style.display = 'none'; }}, 2500);
}}
</script>
"""
            st.components.v1.html(kakao_html, height=55)

            # ── QR코드 (8번 기능) ──────────────────────────────
            st.markdown("---")
            st.markdown("**📱 앱 QR코드**")
            st.caption("아래 QR코드를 스캔하면 이 앱에 접속할 수 있습니다.")

            qr_html = """
<div id="qrcode-wrap" style="display:inline-block;padding:8px;background:#fff;border-radius:8px;border:1px solid #ddd;">
  <div id="qrcode"></div>
</div>
<p id="qr-url" style="font-size:0.7rem;color:#888;margin-top:4px;word-break:break-all;"></p>
<script src="https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js"></script>
<script>
(function() {
  // top 페이지 URL에서 쿼리스트링 제거한 앱 기본 URL 사용
  var appUrl = window.top ? window.top.location.href.split('?')[0] : window.location.href.split('?')[0];
  document.getElementById('qr-url').textContent = appUrl;
  new QRCode(document.getElementById('qrcode'), {
    text: appUrl,
    width: 140,
    height: 140,
    colorDark: '#1a1a2e',
    colorLight: '#ffffff',
    correctLevel: QRCode.CorrectLevel.M
  });
})();
</script>
"""
            st.components.v1.html(qr_html, height=200)
            st.info("💡 대진표 생성 후 사이드바에서 **📊 점수판**을 선택하면 점수를 입력할 수 있습니다.")

        with tab2:
            st.subheader("선수별 출전 현황")
            def hl_stats(row):
                code = df_full.loc[row.name,"_코드"] if "_코드" in df_full.columns else ""
                pfx  = code[:2] if len(code)>=2 else ""
                bg   = ""
                for i, pr in enumerate(active_prefixes):
                    if pfx.startswith(pr):
                        base_color = LEAGUE_COLORS[i]
                        bg = f"{base_color}22" if pfx[1:]=="W" else f"{base_color}15"
                        break
                base_style = f"background-color:{bg};color:black" if bg else ""
                styles = [base_style]*len(row)
                if "총경기" in row.index:
                    ti = row.index.get_loc("총경기"); total = row["총경기"]
                    if total>=4:   styles[ti]="background-color:#FFF176;color:black;font-weight:bold"
                    elif total<3:  styles[ti]="background-color:#FFCDD2;color:black;font-weight:bold"
                    else:          styles[ti]=base_style+";font-weight:bold"
                return styles
            st.dataframe(df_display.style.apply(hl_stats, axis=1),
                         use_container_width=True, height=700)

        with tab3:
            st.subheader("🔍 자동 검증 리포트")
            issues, warns = [], []
            if not df_full.empty:
                under3 = df_full[df_full["총경기"]<3]
                if not under3.empty:
                    issues.append(f"❌ 3경기 미달 {len(under3)}명: {', '.join(under3['이름'].tolist())}")
                else:
                    st.success("✅ 모든 선수 3경기 이상")
                over4 = df_full[df_full["총경기"]>4]
                if not over4.empty:
                    issues.append(f"❌ 4경기 초과 {len(over4)}명: {', '.join(over4['이름'].tolist())}")
                else:
                    st.success("✅ 4경기 초과 없음")

                st.markdown("**매치 종류 분포**")
                td = df_matches["매치종류"].value_counts().reset_index()
                td.columns=["매치종류","경기수"]
                st.dataframe(td, use_container_width=False)

                if len(df_full)>1:
                    std_m=df_full["혼성합계"].std(); mean_m=df_full["혼성합계"].mean()
                    if std_m>1.5:
                        warns.append(f"⚠️ 혼성 편차 큼 (평균 {mean_m:.1f}회, σ={std_m:.2f})")
                    else:
                        st.success(f"✅ 혼성 균등 분배 (평균 {mean_m:.1f}회, σ={std_m:.2f})")

                if not IS_FULLY_RANDOM_run:
                    for lg in active_lgs:
                        cfg = league_configs_run.get(lg, {})
                        if cfg.get("mixed_max") is None and cfg.get("dong_min") is None:
                            continue
                        lg_rows = df_full[df_full["리그"]==lg]
                        if lg_rows.empty: continue
                        mmax = cfg.get("mixed_max"); dmin = cfg.get("dong_min")
                        label_parts = []
                        if mmax: label_parts.append(f"혼성≤{mmax}회")
                        if dmin: label_parts.append(f"동성≥{dmin}회")
                        st.markdown(f"**{lg} 쿼터 현황** ({', '.join(label_parts)})")
                        quota_rows = []
                        for _, row in lg_rows.iterrows():
                            dong=row["남복"]+row["여복"]; mc=row["혼성합계"]
                            quota_rows.append({
                                "이름": row["이름"], "혼성": mc, "동성": dong,
                                "혼성쿼터": "✅" if (mmax is None or mc<=mmax) else "❌",
                                "동성쿼터": "✅" if (dmin is None or dong>=dmin) else "⚠️",
                            })
                        st.dataframe(pd.DataFrame(quota_rows), use_container_width=False)

                if IS_FULLY_RANDOM_run:
                    gvg_count = sum(
                        1 for d in schedule
                        if _is_gender_vs_gender(d["team1"], d["team2"])
                    )
                    if gvg_count > 0:
                        issues.append(f"❌ 남자팀 vs 여자팀 대결 {gvg_count}건 발생 (재생성 권장)")
                    else:
                        st.success("✅ 남자팀 vs 여자팀 대결 없음")

            for i in issues: st.error(i)
            for w in warns:  st.warning(w)
            if not issues and not warns: st.info("🎾 모든 검증 통과!")

        # ── 엑셀 다운로드 ────────────────────────────────────
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
            df_matches.to_excel(writer, sheet_name="대진표", index=False)
            df_display.to_excel(writer, sheet_name="출전현황", index=False)
            for sn in ["대진표","출전현황"]:
                writer.sheets[sn].set_column("A:Z", 14)
        excel_tag  = f"_시드{int(seed_val_run)}" if (use_seed_run and seed_val_run is not None) else "_랜덤"
        mode_tag   = "_완전랜덤" if IS_FULLY_RANDOM_run else "_조건부"
        league_tag = f"_{len(active_lgs)}리그"
        st.sidebar.markdown("---")
        st.sidebar.download_button(
            label="📥 엑셀 다운로드", data=buf.getvalue(),
            file_name=f"TELA_대진표{mode_tag}{league_tag}{excel_tag}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )

    else:
        # ── 버그수정: 페이지 전환 후 복귀해도 대진표 유지 ────
        # session_state에 이미 생성된 schedule이 있으면 그대로 표시
        restored_schedule = st.session_state.get("rp_schedule")
        restored_stats    = st.session_state.get("stats")
        restored_params   = st.session_state.get("last_gen_params")

        if restored_schedule and restored_stats and restored_params:
            # ── 복원된 대진표 표시 ───────────────────────────
            rp_key_run          = restored_params.get("rp_key", "")
            IS_FULLY_RANDOM_run = restored_params.get("is_fully_random", False)
            league_players_r    = restored_params.get("league_players", {})
            use_seed_run        = restored_params.get("use_seed", False)
            seed_val_run        = restored_params.get("seed_val", None)
            active_lgs          = list(league_players_r.keys())
            mode_label          = "완전 랜덤" if IS_FULLY_RANDOM_run else "조건부 랜덤"
            schedule            = restored_schedule
            stats               = restored_stats

            st.info(f"📋 마지막 생성 대진표: **{rp_key_run}** [{mode_label}]")

            def _set_regen2():
                st.session_state["do_regen"] = True
            col_regen2, col_space2 = st.columns([1, 4])
            with col_regen2:
                st.button("🔄 다시 생성", type="secondary", use_container_width=True,
                          on_click=_set_regen2,
                          help="동일 설정으로 새로운 랜덤 대진표를 생성합니다",
                          key="regen2")

            seed_label = f"시드 #{int(seed_val_run)}" if (use_seed_run and seed_val_run is not None) else "랜덤"
            def dn2(code): return display_name(code)
            df_matches = pd.DataFrame([{
                "라운드": d["round"], "리그": d["league"],
                "팀1-A": dn2(d["team1"][0]), "팀1-B": dn2(d["team1"][1]),
                "팀2-A": dn2(d["team2"][0]), "팀2-B": dn2(d["team2"][1]),
                "매치종류": d["type"],
            } for d in schedule])
            df_full    = stats_to_df(stats)
            df_display = df_full.drop(columns=["_코드"])

            tab1, tab2, tab3 = st.tabs(["📋 대진표", "📊 출전 현황", "🔍 검증 리포트"])
            with tab1:
                st.subheader(f"경기 대진표 · {seed_label}  [{mode_label}]")
                lg_color_map2 = {lg: get_league_color(lg) for lg in active_lgs}
                def hl_match2(row):
                    bg = ""
                    for lg, color in lg_color_map2.items():
                        if str(row.get("리그","")) == lg:
                            bg = f"{color}18"; break
                    if not bg: bg = "#f5f5f5"
                    return [f"background-color:{bg};color:black"]*len(row)
                st.dataframe(df_matches.style.apply(hl_match2, axis=1),
                             use_container_width=True, height=600)
                summary2 = df_matches["매치종류"].value_counts()
                st.caption(f"총 {len(df_matches)}경기 | "+" | ".join(f"{k}: {v}경기" for k,v in summary2.items()))
                total_players2 = sum(len(pl) for pl in league_players_r.values())
                per_league2 = " · ".join(f"{lg} {len(pl)}명" for lg,pl in league_players_r.items() if pl)
                st.caption(f"👥 총 {total_players2}명  ({per_league2})")
            with tab2:
                st.subheader("선수별 출전 현황")
                st.dataframe(df_display, use_container_width=True, height=700)
            with tab3:
                st.subheader("🔍 검증 리포트")
                if not df_full.empty:
                    under3 = df_full[df_full["총경기"]<3]
                    if not under3.empty:
                        st.error(f"❌ 3경기 미달 {len(under3)}명: {', '.join(under3['이름'].tolist())}")
                    else:
                        st.success("✅ 모든 선수 3경기 이상")
                    over4 = df_full[df_full["총경기"]>4]
                    if not over4.empty:
                        st.error(f"❌ 4경기 초과 {len(over4)}명: {', '.join(over4['이름'].tolist())}")
                    else:
                        st.success("✅ 4경기 초과 없음")

        else:
            # ── 최초 진입 안내 ───────────────────────────────
            st.info("👈 사이드바에서 리그·페어링 방식·인원을 설정하고 비밀번호 입력 후 **대진표 생성** 버튼을 눌러주세요.")

        # ═══════════════════════════════════════════════════════
        # 저장된 대진표 불러오기
        # ═══════════════════════════════════════════════════════
        st.markdown("---")
        with st.expander("📂 저장된 대진표 불러오기", expanded=False):
            saved_keys = shelf_list_dates()
            if not saved_keys:
                st.info("저장된 대진표가 없습니다.")
            else:
                load_key = st.selectbox("날짜+일련번호 선택", saved_keys, key="load_key_rp")
                if st.button("📥 불러오기", key="load_btn_rp", type="primary"):
                    loaded = shelf_load(load_key)
                    if loaded:
                        loaded_sched = deserialize_schedule(loaded["schedule"])
                        # stats 재계산
                        loaded_stats: Dict[str, PlayerStats] = {}
                        for m in loaded_sched:
                            update_stats(loaded_stats, m["team1"], m["team2"],
                                         m["type"].replace("(중복)",""), m["round"], m["league"])
                        st.session_state.update({
                            "rp_schedule":     loaded_sched,
                            "stats":           loaded_stats,
                            "last_gen_params": {
                                "league_players":  {},
                                "is_fully_random": False,
                                "league_configs":  {},
                                "use_seed":        False,
                                "seed_val":        None,
                                "rp_key":          load_key,
                            },
                        })
                        st.success(f"✅ '{load_key}' 대진표를 불러왔습니다.")
                        st.rerun()
                    else:
                        st.error("불러오기 실패: 데이터를 찾을 수 없습니다.")

        # ═══════════════════════════════════════════════════════
        # 회원 관리 (사전 등록 + 리그 이동)
        # ═══════════════════════════════════════════════════════
        st.markdown("---")
        with st.expander("👥 회원 관리 (사전 등록)", expanded=False):
            all_members = member_load_all()

            # ── 구글 시트 가져오기 ───────────────────────────
            st.markdown("#### 📥 구글 시트 회원 명부 가져오기")
            gs_col1, gs_col2 = st.columns([2, 3])
            gs_target = gs_col1.selectbox(
                "가져올 리그",
                [UNASSIGNED_KEY] + active_leagues,
                key="gs_target_league",
                help="시트에서 읽어온 신규 회원을 어느 리그에 넣을지 선택. '미배정' 선택 시 나중에 리그 이동 가능."
            )
            if gs_col2.button("🔄 구글 시트에서 가져오기", key="gs_import_btn",
                               use_container_width=True):
                try:
                    with st.spinner("구글 시트 연결 중..."):
                        result = sync_from_sheet(gs_target)
                    st.success(
                        f"✅ 완료! "
                        f"시트 총 {result['imported']}명 확인 · "
                        f"신규 추가 {result['added']}명 · "
                        f"기존/탈퇴 제외 {result['skipped']}명"
                    )
                    st.rerun()
                except Exception as e:
                    err = str(e)
                    if "gspread" in err.lower() or "module" in err.lower():
                        st.error("❌ gspread 패키지 미설치. requirements.txt에 `gspread` 및 `google-auth` 추가 필요.")
                    elif "CREDENTIALS" in err.upper() or "secret" in err.lower():
                        st.error("❌ Streamlit Secrets에 gcp_service_account 설정이 없습니다. Settings → Secrets 확인.")
                    else:
                        st.error(f"❌ 오류: {err}")

            # 미배정 버킷 회원 수 안내
            unassigned = member_load_all().get(UNASSIGNED_KEY, [])
            if unassigned:
                st.warning(
                    f"⚠️ **미배정 회원 {len(unassigned)}명** — "
                    f"아래 '📋 {UNASSIGNED_KEY}' 탭에서 리그로 이동시켜 주세요."
                )

            st.markdown("---")

            mgmt_tabs = st.tabs(
                [f"📋 {UNASSIGNED_KEY}"] + [f"📋 {lg}" for lg in active_leagues]
                if unassigned
                else [f"📋 {lg}" for lg in active_leagues]
            )
            mgmt_league_list = (
                [UNASSIGNED_KEY] + active_leagues if unassigned else active_leagues
            )

            for ti, (mgmt_tab, mgmt_lg) in enumerate(zip(mgmt_tabs, mgmt_league_list)):
                with mgmt_tab:
                    lg_members = all_members.get(mgmt_lg, [])
                    lc_mg = LEAGUE_COLORS[ti % len(LEAGUE_COLORS)]

                    # ── 등록 회원 목록 + 삭제 + 리그 이동 ──────
                    if lg_members:
                        st.markdown(
                            f'<div style="color:{lc_mg};font-weight:700;margin-bottom:6px;">'
                            f'{mgmt_lg} 등록 회원 ({len(lg_members)}명)</div>',
                            unsafe_allow_html=True
                        )
                        other_leagues = (
                            active_leagues if mgmt_lg == UNASSIGNED_KEY
                            else [lg for lg in active_leagues if lg != mgmt_lg]
                        )
                        has_move = bool(other_leagues)

                        # 헤더
                        if has_move:
                            h_cols = st.columns([3, 1, 1, 2, 1, 1])
                            h_cols[0].markdown("**이름**"); h_cols[1].markdown("**성별**")
                            h_cols[2].markdown("**상태**")
                            h_cols[3].markdown("**이동 대상**"); h_cols[4].markdown("**이동**")
                            h_cols[5].markdown("**삭제**")
                        else:
                            h_cols = st.columns([3, 1, 1, 1])
                            h_cols[0].markdown("**이름**"); h_cols[1].markdown("**성별**")
                            h_cols[2].markdown("**상태**"); h_cols[3].markdown("**삭제**")

                        for m in list(lg_members):
                            g_label = "남" if m["gender"]=="M" else "여"
                            status  = m.get("status", "정상")
                            if status == "휴면":
                                badge = '<span style="background:#FF8F00;color:#fff;border-radius:4px;padding:1px 6px;font-size:0.72rem;font-weight:700;margin-right:4px;">💤휴면</span>'
                            else:
                                badge = '<span style="background:#2e7d32;color:#fff;border-radius:4px;padding:1px 6px;font-size:0.72rem;font-weight:700;margin-right:4px;">✅정상</span>'
                            name_html = f'{badge}{m["name"]}'

                            if has_move:
                                row_cols = st.columns([3, 1, 1, 2, 1, 1])
                                row_cols[0].markdown(name_html, unsafe_allow_html=True)
                                row_cols[1].write(g_label)
                                # 상태 토글 버튼
                                toggle_label = "→정상" if status == "휴면" else "→휴면"
                                if row_cols[2].button(
                                    toggle_label,
                                    key=f"tog_{mgmt_lg}_{m['name']}",
                                    help="상태 변경"
                                ):
                                    data_edit = member_load_all()
                                    for idx2, mm in enumerate(data_edit.get(mgmt_lg, [])):
                                        if mm["name"] == m["name"]:
                                            data_edit[mgmt_lg][idx2]["status"] = (
                                                "정상" if status == "휴면" else "휴면"
                                            )
                                            break
                                    member_save_all(data_edit)
                                    st.rerun()
                                move_to = row_cols[3].selectbox(
                                    "이동대상", other_leagues,
                                    key=f"moveto_{mgmt_lg}_{m['name']}",
                                    label_visibility="collapsed"
                                )
                                if row_cols[4].button(
                                    "→", key=f"movebtn_{mgmt_lg}_{m['name']}",
                                    help=f"{move_to}으로 이동"
                                ):
                                    member_remove(mgmt_lg, m["name"])
                                    member_add(move_to, m["name"], m["gender"])
                                    # status 유지
                                    data_mv = member_load_all()
                                    for mm in data_mv.get(move_to, []):
                                        if mm["name"] == m["name"]:
                                            mm["status"] = status; break
                                    member_save_all(data_mv)
                                    st.success(f"'{m['name']}' → {move_to} 이동 완료")
                                    st.rerun()
                                if row_cols[5].button("🗑", key=f"del_{mgmt_lg}_{m['name']}"):
                                    member_remove(mgmt_lg, m["name"])
                                    st.rerun()
                            else:
                                row_cols = st.columns([3, 1, 1, 1])
                                row_cols[0].markdown(name_html, unsafe_allow_html=True)
                                row_cols[1].write(g_label)
                                toggle_label = "→정상" if status == "휴면" else "→휴면"
                                if row_cols[2].button(
                                    toggle_label,
                                    key=f"tog_{mgmt_lg}_{m['name']}",
                                    help="상태 변경"
                                ):
                                    data_edit = member_load_all()
                                    for idx2, mm in enumerate(data_edit.get(mgmt_lg, [])):
                                        if mm["name"] == m["name"]:
                                            data_edit[mgmt_lg][idx2]["status"] = (
                                                "정상" if status == "휴면" else "휴면"
                                            )
                                            break
                                    member_save_all(data_edit)
                                    st.rerun()
                                if row_cols[3].button("🗑", key=f"del_{mgmt_lg}_{m['name']}"):
                                    member_remove(mgmt_lg, m["name"])
                                    st.rerun()
                    else:
                        st.info(f"{mgmt_lg}에 등록된 회원이 없습니다.")

                    st.markdown("---")

                    # ── 신규 회원 추가 ───────────────────────────
                    st.markdown("**신규 회원 추가**")
                    col_n, col_g, col_s, col_add = st.columns([3, 1, 1, 1])
                    new_name = col_n.text_input(
                        "이름", key=f"new_name_{mgmt_lg}",
                        label_visibility="collapsed", placeholder="이름 입력"
                    )
                    new_gender = col_g.selectbox(
                        "성별", ["남", "여"],
                        key=f"new_gender_{mgmt_lg}",
                        label_visibility="collapsed"
                    )
                    new_status = col_s.selectbox(
                        "상태", ["정상", "휴면"],
                        key=f"new_status_{mgmt_lg}",
                        label_visibility="collapsed"
                    )
                    if col_add.button("➕", key=f"add_btn_{mgmt_lg}", help="회원 추가"):
                        if new_name.strip():
                            g_code = "M" if new_gender == "남" else "W"
                            data_add = member_load_all()
                            if mgmt_lg not in data_add:
                                data_add[mgmt_lg] = []
                            if not any(mm["name"] == new_name.strip()
                                       for members in data_add.values() for mm in members):
                                data_add[mgmt_lg].append({
                                    "name": new_name.strip(),
                                    "gender": g_code,
                                    "status": new_status,
                                })
                                member_save_all(data_add)
                                st.success(f"'{new_name}' ({new_status}) 추가 완료")
                                st.rerun()
                            else:
                                st.warning(f"'{new_name}'은 이미 등록된 이름입니다.")
                        else:
                            st.warning("이름을 입력해주세요.")

                    # ── 일괄 추가 ────────────────────────────────
                    st.markdown("**일괄 추가** (한 줄에 `이름 성별`, 성별: 남/여, 상태 기본=정상)")
                    bulk_text = st.text_area(
                        "일괄 입력", placeholder="홍길동 남\n김영희 여\n이철수 남",
                        height=100, key=f"bulk_{mgmt_lg}",
                        label_visibility="collapsed"
                    )
                    if st.button("📋 일괄 등록", key=f"bulk_btn_{mgmt_lg}"):
                        added = 0
                        data_bulk = member_load_all()
                        existing_names_bulk = {
                            mm["name"] for members in data_bulk.values() for mm in members
                        }
                        if mgmt_lg not in data_bulk:
                            data_bulk[mgmt_lg] = []
                        for line in bulk_text.strip().splitlines():
                            parts = line.strip().split()
                            if not parts: continue
                            bname   = parts[0]
                            bgender = "W" if (len(parts)>=2 and parts[1] in ("여","W","F")) else "M"
                            if bname not in existing_names_bulk:
                                data_bulk[mgmt_lg].append({
                                    "name": bname, "gender": bgender, "status": "정상"
                                })
                                existing_names_bulk.add(bname)
                                added += 1
                        if added:
                            member_save_all(data_bulk)
                            st.success(f"{added}명 등록 완료")
                            st.rerun()

        if not restored_schedule:
            with st.expander("📖 사용 방법 및 규칙 안내"):
                st.markdown("""
                ### v4.01 기능 안내

                | 항목 | 내용 |
                |------|------|
                | **회원 사전 등록** | 👥 회원 관리에서 리그별 회원 등록 후 체크박스로 선택 |
                | **대진표 불러오기** | 📂 저장된 대진표 불러오기에서 날짜 선택 후 로드 |
                | **페이지 복귀 유지** | 점수판↔랜덤페어 이동해도 마지막 대진표 유지 |
                | **리그 수 설정** | 1~5개 자유 설정 (A→B→C→D→E 순) |
                | **페어링 방식** | 🔵 조건부 / 🔴 완전 랜덤 선택 |
                | **재생성 버튼** | 동일 설정으로 새 대진표 즉시 생성 |
                | **카카오톡 복사** | 대진표를 카카오톡용 텍스트로 한 번에 복사 |
                | **QR코드** | 앱 URL QR코드로 회원 공유 |

                ### 공통 출전 규칙
                - 최소 3경기 보장 → 이벤트 라운드(4R)로 보충
                - 최대 4경기 제한

                ### 점수판
                1. 대진표 생성 후 사이드바 **📊 점수판** 선택
                2. 날짜+일련번호 입력 (랜덤페어와 동일하게)
                3. 각 경기 **💾 저장** 버튼 클릭 → 새로고침 후에도 유지
                """)
