"""
TELA CLUB Random Match Generator v5.3
버전 이력: CHANGELOG.md 참고
"""


import streamlit as st
import pandas as pd
import random
import io
import shelve
import os
import json
try:
    import extra_streamlit_components as stx
    COOKIES_AVAILABLE = True
except ImportError:
    COOKIES_AVAILABLE = False
from dataclasses import dataclass, field
from itertools import zip_longest
from typing import Dict, FrozenSet, List, Optional, Set, Tuple
from datetime import date

# ── 날짜 → 요일 포함 문자열 헬퍼 ────────────────────────────
_WEEKDAY_KO = ["월", "화", "수", "목", "금", "토", "일"]

def _date_with_weekday(date_str: str) -> str:
    """'2026-05-23' → '2026-05-23(토)'. 파싱 실패 시 원본 반환."""
    try:
        from datetime import datetime as _dt
        d = _dt.strptime(date_str.strip()[:10], "%Y-%m-%d")
        wd = _WEEKDAY_KO[d.weekday()]
        return f"{date_str.strip()[:10]}({wd})"
    except Exception:
        return date_str
import secrets as _secrets


# ============================================================
# 섹션 0: 저장소 경로 & 상수
# ============================================================

SAVE_DIR   = os.path.join(os.path.dirname(__file__), ".tela_data")
os.makedirs(SAVE_DIR, exist_ok=True)
SHELF_PATH   = os.path.join(SAVE_DIR, "scoreboard")
MEMBER_PATH  = os.path.join(SAVE_DIR, "members")
USER_PATH    = os.path.join(SAVE_DIR, "users")
GUEST_PATH   = os.path.join(SAVE_DIR, "guests")   # 게스트 영구 저장 (회원명부 미반영)
SESSION_PATH = os.path.join(SAVE_DIR, "sessions") # 세션 토큰 저장 (로그인 유지)
RECORDS_PATH = os.path.join(SAVE_DIR, "records")  # 누적 기록실 (월간/연간)
EXCLUDE_PATH = os.path.join(SAVE_DIR, "exclude")  # 기록실 제외 선수 목록 (코치 등)

# ── 세션 토큰 헬퍼 (query_params 기반 로그인 유지) ─────────────
SESSION_EXPIRE_DAYS = 30

def _session_save(user: dict) -> str:
    """토큰 생성 후 shelve에 저장, 토큰 반환"""
    from datetime import datetime, timedelta
    token = _secrets.token_urlsafe(32)
    expire = (datetime.now() + timedelta(days=SESSION_EXPIRE_DAYS)).isoformat()
    with shelve.open(SESSION_PATH) as db:
        db[token] = {"user": user, "expire": expire}
    return token

def _session_load(token: str) -> Optional[dict]:
    """토큰으로 사용자 정보 복원. 만료/미존재 시 None"""
    if not token:
        return None
    from datetime import datetime
    with shelve.open(SESSION_PATH) as db:
        rec = db.get(token)
    if not rec:
        return None
    try:
        if datetime.fromisoformat(rec["expire"]) < datetime.now():
            return None
    except Exception:
        return None
    return rec.get("user")

def _session_delete(token: str):
    with shelve.open(SESSION_PATH) as db:
        if token in db:
            del db[token]

def _session_cleanup():
    """만료 토큰 정리 (10% 확률로 실행)"""
    import random as _r
    if _r.random() > 0.1:
        return
    from datetime import datetime
    with shelve.open(SESSION_PATH) as db:
        expired = [k for k, v in list(db.items())
                   if datetime.fromisoformat(v.get("expire","2000-01-01")) < datetime.now()]
        for k in expired:
            del db[k]

def guest_load() -> list:
    """게스트 목록 로드. [{name, gender, league, code}, ...]"""
    with shelve.open(GUEST_PATH) as db:
        return list(db.get("guests", []))

def guest_save(guests: list):
    with shelve.open(GUEST_PATH) as db:
        db["guests"] = guests

def guest_add(name: str, gender: str, league: str, code: str):
    guests = guest_load()
    if not any(g["name"] == name and g["league"] == league for g in guests):
        guests.append({"name": name, "gender": gender, "league": league, "code": code})
        guest_save(guests)

def guest_remove(name: str, league: str):
    guests = guest_load()
    guests = [g for g in guests if not (g["name"] == name and g["league"] == league)]
    guest_save(guests)


# ── 앱 로그인 계정 관리 ───────────────────────────────────────
import hashlib

def _hash_pw(pw: str) -> str:
    return hashlib.sha256(pw.strip().encode()).hexdigest()

def user_load_all() -> dict:
    """전체 계정 로드. 구조: {user_id: {pw_hash, role, name}}"""
    with shelve.open(USER_PATH) as db:
        return dict(db.get("users", {}))

def user_save_all(data: dict):
    with shelve.open(USER_PATH) as db:
        db["users"] = data

def user_ensure_admin():
    """secrets의 ADMIN_ID/ADMIN_PASSWORD로 최초 관리자 계정 보장"""
    admin_id = st.secrets.get("ADMIN_ID", "admin")
    admin_pw = st.secrets.get("ADMIN_PASSWORD", "1223")
    data = user_load_all()
    if admin_id not in data:
        data[admin_id] = {
            "pw_hash": _hash_pw(admin_pw),
            "role":    "admin",
            "name":    "관리자",
        }
        user_save_all(data)

def user_authenticate(user_id: str, password: str) -> Optional[dict]:
    """로그인 시도. 성공 시 {id, role, name} 반환, 실패 시 None"""
    data = user_load_all()
    u = data.get(user_id.strip())
    if u and u["pw_hash"] == _hash_pw(password):
        return {"id": user_id.strip(), "role": u["role"], "name": u["name"]}
    return None

def user_add(user_id: str, password: str, role: str, name: str) -> bool:
    data = user_load_all()
    if user_id in data:
        return False  # 중복
    data[user_id] = {"pw_hash": _hash_pw(password), "role": role, "name": name}
    user_save_all(data)
    return True

def user_delete(user_id: str):
    data = user_load_all()
    data.pop(user_id, None)
    user_save_all(data)

def user_change_pw(user_id: str, new_pw: str):
    data = user_load_all()
    if user_id in data:
        data[user_id]["pw_hash"] = _hash_pw(new_pw)
        user_save_all(data)

# ── 쿠키 기반 로그인 유지 헬퍼 ────────────────────────────────
COOKIE_NAME = "telaclub_session"
COOKIE_EXPIRE_DAYS = 30

def _get_cookie_manager():
    """CookieManager 싱글톤. @st.cache_resource를 쓰면 위젯 경고가 발생하므로
    session_state로 직접 관리."""
    if not COOKIES_AVAILABLE:
        return None
    if "_cookie_mgr" not in st.session_state:
        try:
            st.session_state["_cookie_mgr"] = stx.CookieManager(key="telaclub_cookie_mgr")
        except Exception:
            st.session_state["_cookie_mgr"] = None
    return st.session_state.get("_cookie_mgr")

def _cookie_save_user(user: dict):
    """로그인 성공 시 쿠키에 사용자 정보 저장."""
    if not COOKIES_AVAILABLE:
        return
    cm = _get_cookie_manager()
    if cm is None:
        return
    try:
        from datetime import datetime, timedelta
        cm.set(COOKIE_NAME, json.dumps(user),
               expires_at=datetime.now() + timedelta(days=COOKIE_EXPIRE_DAYS),
               key=f"cookie_set_{user.get('id','')}")
    except Exception:
        pass

def _cookie_clear_user():
    """로그아웃 시 쿠키 삭제."""
    if not COOKIES_AVAILABLE:
        return
    cm = _get_cookie_manager()
    if cm is None:
        return
    try:
        cm.delete(COOKIE_NAME, key="cookie_del")
    except Exception:
        pass

def _cookie_restore_user():
    """앱 시작 시 쿠키에서 사용자 정보 복원."""
    if not COOKIES_AVAILABLE:
        return None
    cm = _get_cookie_manager()
    if cm is None:
        return None
    try:
        raw = cm.get(COOKIE_NAME)
        if raw:
            return json.loads(raw)
    except Exception:
        return None
    return None

# ── 현재 로그인 사용자 헬퍼 ──────────────────────────────────
def get_app_user() -> Optional[dict]:
    # session_state에 있으면 우선 반환
    u = st.session_state.get("app_user")
    if u:
        return u
    # 1순위: query_params 토큰 복원 (새로고침 후에도 유지)
    try:
        token = st.query_params.get("t", "")
        if token:
            restored = _session_load(token)
            if restored:
                st.session_state["app_user"] = restored
                _session_cleanup()
                return restored
    except Exception:
        pass
    # 2순위: 쿠키에서 복원 시도 (extra_streamlit_components 있을 때)
    restored = _cookie_restore_user()
    if restored:
        st.session_state["app_user"] = restored
        return restored
    return None

def is_logged_in() -> bool:
    return bool(get_app_user())

def is_admin() -> bool:
    u = get_app_user()
    return bool(u and u.get("role") == "admin")


def shelf_save(date_key: str, schedule: list, scores: dict, is_fully_random: bool = False):
    with shelve.open(SHELF_PATH) as db:
        db[date_key] = {"schedule": schedule, "scores": scores, "is_fully_random": is_fully_random}

def shelf_load(date_key: str) -> Optional[dict]:
    with shelve.open(SHELF_PATH) as db:
        return db.get(date_key, None)

def shelf_list_dates() -> List[str]:
    with shelve.open(SHELF_PATH) as db:
        return sorted(db.keys(), reverse=True)

def shelf_delete(date_key: str):
    with shelve.open(SHELF_PATH) as db:
        if date_key in db:
            del db[date_key]

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


# [제거] ADMIN_PASSWORD: 어디서도 사용되지 않음.
# 관리자 비밀번호는 RS_ADMIN_PASSWORD(섹션 R)로 별도 관리.

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
        name_part = raw[2:]
        # 게스트 태그 처리
        is_guest = name_part.startswith("★")
        if is_guest:
            name_part = name_part[1:]  # ★ 제거
            shown = f"{name_part}(게스트/{g})" if g else f"{name_part}(게스트)"
        else:
            shown = f"{name_part}({g})" if g else name_part
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
    # 위에서 fallback으로 group을 채웠으므로 len(group) < 4만 체크
    if len(group) < 4: return None, pool[:]
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
        # 동성 강제 라운드(예: 마지막 라운드 dong_forced=True에서 동성 그룹이 아직 없는 경우)에서는
        # A리그 설정(동성우선)으로 픽 시도. 다른 리그의 혼복우선 설정에 영향받지 않기 위함.
        # ⚠️ 리그별 설정이 크게 다르면 의도와 다를 수 있으므로 검토 시 주의.
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
    """
    선수별 현황 계산.
    - (중복) 태그 선수: 출전수 포함하되 승/패/득점/실점 제외
    - 제외 목록 선수(코치 등): 완전 제외
    - 게스트(★ prefix): 완전 제외
    """
    _excluded = set(exclude_list_load())

    def _skip_player(code: str) -> bool:
        """True이면 집계에서 완전 제외"""
        raw = base_name(code)
        # 게스트: 코드 내 ★ 포함
        if "★" in raw:
            return True
        # 제외 목록
        pkey = _clean_player_key(code)
        if pkey in _excluded:
            return True
        return False

    player_stats = {}

    def ensure_player(code, league):
        if _skip_player(code):
            return
        key = base_name(code)
        if key not in player_stats:
            player_stats[key] = {
                "이름": display_name(code), "리그": league,
                "출전":0,"승":0,"패":0,"득점":0,"실점":0,
                "1R출전":0,"2R출전":0,"3R출전":0,"4R출전":0,
            }

    for idx, match in enumerate(schedule):
        sc  = scores.get(str(idx), {})
        s1  = sc.get("score1", None)
        s2  = sc.get("score2", None)
        league = match["league"]

        # (중복) 여부 분리
        t1_all = list(match["team1"])
        t2_all = list(match["team2"])

        # 정상 선수만 등록
        for code in t1_all + t2_all:
            ensure_player(code, league)

        rnd = match["round"]
        rnd_num = (1 if rnd=="1R" else 2 if rnd=="2R" else 3 if rnd=="3R"
                   else 4 if ("4R" in rnd or "이벤트" in rnd) else None)

        # 출전 카운트: 중복·제외·게스트 제외한 선수만
        for code in t1_all + t2_all:
            if _skip_player(code) or _is_duplicate_player(code):
                continue
            key = base_name(code)
            if key in player_stats:
                player_stats[key]["출전"] += 1
                if rnd_num==1: player_stats[key]["1R출전"]+=1
                elif rnd_num==2: player_stats[key]["2R출전"]+=1
                elif rnd_num==3: player_stats[key]["3R출전"]+=1
                elif rnd_num==4: player_stats[key]["4R출전"]+=1

        # 승/패/득점/실점: 중복·제외·게스트 제외한 선수만
        t1_valid = [base_name(c) for c in t1_all
                    if not _skip_player(c) and not _is_duplicate_player(c)]
        t2_valid = [base_name(c) for c in t2_all
                    if not _skip_player(c) and not _is_duplicate_player(c)]

        if s1 is not None and s2 is not None and (s1+s2) > 0:
            if s1 > s2:
                winners, losers, ws, ls = t1_valid, t2_valid, s1, s2
            elif s2 > s1:
                winners, losers, ws, ls = t2_valid, t1_valid, s2, s1
            else:
                for p in t1_valid + t2_valid:
                    if p in player_stats:
                        player_stats[p]["득점"] += s1
                        player_stats[p]["실점"] += s2
                continue
            for p in winners:
                if p in player_stats:
                    player_stats[p]["승"]+=1
                    player_stats[p]["득점"]+=ws
                    player_stats[p]["실점"]+=ls
            for p in losers:
                if p in player_stats:
                    player_stats[p]["패"]+=1
                    player_stats[p]["득점"]+=ls
                    player_stats[p]["실점"]+=ws

    if not player_stats: return pd.DataFrame()
    df = pd.DataFrame(list(player_stats.values()))
    df = df[["리그","이름","출전","승","패","득점","실점","1R출전","2R출전","3R출전","4R출전"]]
    return df.sort_values(["리그","승","득점"],ascending=[True,False,False]).reset_index(drop=True)


# ============================================================
# 섹션 13: 직렬화 헬퍼
# ============================================================

def serialize_schedule(schedule):
    return [{
        "round": m["round"], "league": m["league"],
        "team1": list(m["team1"]), "team2": list(m["team2"]), "type": m["type"],
        "exclude_players": m.get("exclude_players", []),
    } for m in schedule]

def deserialize_schedule(schedule):
    return [{
        "round": m["round"], "league": m["league"],
        "team1": tuple(m["team1"]), "team2": tuple(m["team2"]), "type": m["type"],
        "exclude_players": m.get("exclude_players", []),
    } for m in schedule]


# ============================================================
# 섹션 12-B: 누적 기록실 (구글시트 저장)
# ============================================================
# 구글시트 "records" 워크시트 구조:
# date_key | year_month | year | player_key | display_name | league | wins | losses | pf | pa

RECORDS_SHEET_NAME = "records"
RECORDS_COLUMNS = ["date_key","year_month","year","player_key","display_name","league",
                   "wins","losses","pf","pa"]

@st.cache_resource
def _get_records_sheet():
    """records 워크시트. 없으면 자동 생성."""
    try:
        wb = _get_gsheet_connection()
        try:
            ws = wb.worksheet(RECORDS_SHEET_NAME)
        except Exception:
            ws = wb.add_worksheet(title=RECORDS_SHEET_NAME,
                                  rows=5000, cols=len(RECORDS_COLUMNS))
            ws.append_row(RECORDS_COLUMNS)
        # 헤더 보정
        headers = ws.row_values(1)
        if not headers or headers[0] != "date_key":
            ws.insert_row(RECORDS_COLUMNS, 1)
        return ws
    except Exception:
        return None


def _records_sheet_load_all() -> list:
    """records 시트 전체 행 로드. [{date_key, year_month, ...}, ...]"""
    try:
        ws = _get_records_sheet()
        if ws is None:
            return []
        rows = ws.get_all_records()
        return rows
    except Exception:
        return []


def _clean_player_key(raw_code: str) -> str:
    """
    player_key 정제: 리그+성별 접두사(AM/AW/BM/BW 등) 제거 → 순수 이름만 반환.
    예) 'AM윤지수' → '윤지수', 'AW최선화' → '최선화', 'AM★조원찬' → '★조원찬'
    """
    b = base_name(raw_code)
    if is_custom_code(b):
        return b[2:]  # 접두사 2글자 제거
    return b


def _is_duplicate_player(code: str) -> bool:
    """(중복) 태그가 있는 이벤트 라운드 중복 선수 여부"""
    return "(중복)" in code


def _records_build_session_stats(date_key: str, schedule: list, scores: dict) -> dict:
    """
    date_key 세션의 선수별 통계 계산.
    - (중복) 태그 선수: 기록 집계 제외
    - 제외 목록 선수(코치 등) / 게스트(★): 완전 제외
    - player_key: 리그+성별 접두사 제거, 순수 이름만 사용
    """
    from datetime import datetime as _dt
    try:
        year_month = _dt.strptime(date_key[:7], "%Y-%m").strftime("%Y-%m")
        year_str   = date_key[:4]
    except Exception:
        year_month = "unknown"
        year_str   = "unknown"

    # 제외 목록 1회만 로드 (경기 수만큼 반복 호출 방지)
    _excluded_set = set(exclude_list_load())

    def _should_skip(code: str, _me: set = None) -> bool:
        raw = base_name(code)
        if "★" in raw: return True
        if _is_duplicate_player(code): return True
        if _clean_player_key(code) in _excluded_set: return True
        if _me and base_name(code) in _me: return True
        return False

    session_stats = {}
    for idx, match in enumerate(schedule):
        sc = scores.get(str(idx), {})
        s1 = sc.get("score1", None)
        s2 = sc.get("score2", None)
        if s1 is None or s2 is None:
            continue
        # 경기 전체 중복 처리(스코어보드 is_dup) → 기록실 제외
        if sc.get("is_dup", False):
            continue
        # 대진표 수동조정의 개인별 제외 목록
        _match_excl = set(match.get("exclude_players", []))

        t1_codes = list(match["team1"])
        t2_codes = list(match["team2"])
        t1_valid = [c for c in t1_codes if not _should_skip(c, _match_excl)]
        t2_valid = [c for c in t2_codes if not _should_skip(c, _match_excl)]
        t1_keys  = [_clean_player_key(c) for c in t1_valid]
        t2_keys  = [_clean_player_key(c) for c in t2_valid]

        # 정상 선수만 session_stats 등록
        for code, pkey in zip(t1_valid + t2_valid, t1_keys + t2_keys):
            if pkey not in session_stats:
                session_stats[pkey] = {
                    "date_key":    date_key,
                    "year_month":  year_month,
                    "year":        year_str,
                    "player_key":  pkey,
                    "display_name": pname(code),
                    "league":      match["league"],
                    "wins": 0, "losses": 0, "pf": 0, "pa": 0,
                }
            session_stats[pkey]["display_name"] = pname(code)
            session_stats[pkey]["league"] = match["league"]

        if s1 > s2:
            for k in t1_keys:
                session_stats[k]["wins"]+=1; session_stats[k]["pf"]+=s1; session_stats[k]["pa"]+=s2
            for k in t2_keys:
                session_stats[k]["losses"]+=1; session_stats[k]["pf"]+=s2; session_stats[k]["pa"]+=s1
        elif s2 > s1:
            for k in t2_keys:
                session_stats[k]["wins"]+=1; session_stats[k]["pf"]+=s2; session_stats[k]["pa"]+=s1
            for k in t1_keys:
                session_stats[k]["losses"]+=1; session_stats[k]["pf"]+=s1; session_stats[k]["pa"]+=s2
        else:
            for k in t1_keys + t2_keys:
                session_stats[k]["pf"] += s1
                session_stats[k]["pa"] += s2
    return session_stats


def records_commit(date_key: str, schedule: list, scores: dict):
    """
    구글시트 records 탭에 세션 점수 반영.
    동일 date_key 행이 있으면 삭제 후 재삽입 (중복 방지).
    """
    try:
        ws = _get_records_sheet()
        if ws is None:
            return
        session_stats = _records_build_session_stats(date_key, schedule, scores)
        if not session_stats:
            return

        # 기존 동일 date_key 행 삭제 (역순으로 삭제해야 인덱스 밀림 없음)
        all_rows = ws.get_all_values()
        del_rows = [i+1 for i, row in enumerate(all_rows)
                    if i > 0 and len(row) > 0 and row[0] == date_key]
        for ri in sorted(del_rows, reverse=True):
            ws.delete_rows(ri)

        # 새 행 일괄 삽입
        new_rows = []
        for pkey, pdata in session_stats.items():
            new_rows.append([
                str(pdata.get("date_key","")),
                str(pdata.get("year_month","")),
                str(pdata.get("year","")),
                str(pdata.get("player_key","")),
                str(pdata.get("display_name","")),
                str(pdata.get("league","")),
                int(pdata.get("wins",0)),
                int(pdata.get("losses",0)),
                int(pdata.get("pf",0)),
                int(pdata.get("pa",0)),
            ])
        if new_rows:
            ws.append_rows(new_rows, value_input_option="USER_ENTERED")
    except Exception:
        pass  # 기록실 오류는 점수 저장을 막지 않음


# ── 기록실 제외 선수 관리 (shelve 저장) ──────────────────────
def exclude_list_load() -> list:
    """제외 선수 이름 목록 로드. ['윤지수', '홍길동', ...]"""
    with shelve.open(EXCLUDE_PATH) as db:
        return list(db.get("excluded", []))

def exclude_list_save(names: list):
    with shelve.open(EXCLUDE_PATH) as db:
        db["excluded"] = sorted(list(set(names)))

def exclude_list_add(name: str):
    names = exclude_list_load()
    name = name.strip()
    if name and name not in names:
        names.append(name)
        exclude_list_save(names)

def exclude_list_remove(name: str):
    names = exclude_list_load()
    names = [n for n in names if n != name.strip()]
    exclude_list_save(names)

def _is_excluded_player(player_key: str) -> bool:
    """player_key(순수 이름)가 제외 목록에 있는지 확인."""
    excluded = exclude_list_load()
    return player_key.strip() in excluded


@st.cache_data(ttl=120)
def records_load_cached() -> list:
    """records 시트 캐시 로드 (120초 TTL)."""
    return _records_sheet_load_all()


def records_get_df(filter_type: str, filter_value: str) -> "pd.DataFrame":
    """
    filter_type: 'monthly' 또는 'yearly'
    filter_value: 'YYYY-MM' 또는 'YYYY'
    제외 선수 목록에 있는 player_key는 조회에서도 제외.
    """
    all_rows = records_load_cached()
    col = "year_month" if filter_type == "monthly" else "year"
    excluded = set(exclude_list_load())  # 제외 선수 이름 세트
    filtered = [r for r in all_rows
                if str(r.get(col,"")).strip() == filter_value
                and str(r.get("player_key","")).strip() not in excluded]
    if not filtered:
        return pd.DataFrame()

    # 선수별 집계
    agg = {}
    for r in filtered:
        pkey = str(r.get("player_key","")).strip()
        if not pkey:
            continue
        if pkey not in agg:
            agg[pkey] = {
                "리그":    str(r.get("league","")),
                "이름":    str(r.get("display_name", pkey)),
                "승":      0, "패": 0, "득점": 0, "실점": 0, "출전경기": 0,
            }
        _w = int(r.get("wins",0)  or 0)
        _l = int(r.get("losses",0) or 0)
        agg[pkey]["승"]       += _w
        agg[pkey]["패"]       += _l
        agg[pkey]["출전경기"] += _w + _l
        agg[pkey]["득점"]     += int(r.get("pf",0) or 0)
        agg[pkey]["실점"]     += int(r.get("pa",0) or 0)
        agg[pkey]["이름"]  = str(r.get("display_name", pkey))
        agg[pkey]["리그"]  = str(r.get("league",""))

    rows = []
    for pkey, rec in agg.items():
        total = rec["승"] + rec["패"]
        rate  = f"{rec['승']/total*100:.1f}%" if total > 0 else "-"
        rows.append({
            "리그":     rec["리그"],
            "이름":     rec["이름"],
            "출전경기": rec["출전경기"],
            "승":       rec["승"],
            "패":       rec["패"],
            "득점":     rec["득점"],
            "실점":     rec["실점"],
            "득실차":   rec["득점"] - rec["실점"],
            "승률":     rate,
        })
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df = df.sort_values(["리그","득점","득실차","승"], ascending=[True,False,False,False]).reset_index(drop=True)
    df["순위"] = df.groupby("리그").cumcount() + 1
    df["순위"] = df["순위"].apply(lambda x: f"{x}위")
    cols = ["리그","순위","이름","출전경기","승","패","득점","실점","득실차","승률"]
    return df[cols]



# ── [다이어트] 랜덤페어 렌더링 공통 헬퍼 ─────────────────────
# 첫 생성 직후 / 페이지 복귀 후 복원 시 양쪽에서 공통 사용하여 중복 제거.

def _build_matches_df(schedule):
    """대진표 DataFrame 변환."""
    import pandas as _pd
    return _pd.DataFrame([{
        "라운드": d["round"], "리그": d["league"],
        "팀1-A": display_name(d["team1"][0]), "팀1-B": display_name(d["team1"][1]),
        "팀2-A": display_name(d["team2"][0]), "팀2-B": display_name(d["team2"][1]),
        "매치종류": d["type"],
    } for d in schedule])

def _render_match_table(df_matches, active_lgs, seed_label, mode_label, league_players_dict):
    """대진표 탭 공통 렌더러."""
    import streamlit as _st
    _st.subheader(f"경기 대진표 · {seed_label}  [{mode_label}]")
    lg_color_map = {lg: get_league_color(lg) for lg in active_lgs}
    def _hl(row):
        bg = ""
        for lg, color in lg_color_map.items():
            if str(row.get("리그","")) == lg:
                bg = f"{color}18"; break
        if not bg: bg = "#f5f5f5"
        return [f"background-color:{bg};color:black"]*len(row)
    _st.dataframe(df_matches.style.apply(_hl, axis=1), use_container_width=True, height=600)
    summary = df_matches["매치종류"].value_counts()
    _st.caption(f"총 {len(df_matches)}경기 | "
                + " | ".join(f"{k}: {v}경기" for k,v in summary.items()))
    total_players = sum(len(pl) for pl in league_players_dict.values())
    per_league = " · ".join(f"{lg} {len(pl)}명" for lg,pl in league_players_dict.items() if pl)
    _st.caption(f"👥 총 {total_players}명  ({per_league})")

def _render_basic_validation(df_full):
    """검증 리포트 공통 부분 (3경기 미달, 4경기 초과)."""
    import streamlit as _st
    if df_full.empty: return
    under3 = df_full[df_full["총경기"]<3]
    if not under3.empty:
        _st.error(f"❌ 3경기 미달 {len(under3)}명: {', '.join(under3['이름'].tolist())}")
    else:
        _st.success("✅ 모든 선수 3경기 이상")
    over4 = df_full[df_full["총경기"]>4]
    if not over4.empty:
        _st.error(f"❌ 4경기 초과 {len(over4)}명: {', '.join(over4['이름'].tolist())}")
    else:
        _st.success("✅ 4경기 초과 없음")


# ============================================================
# 섹션 14: Streamlit 앱
# ============================================================

import re
import gspread
from gspread.utils import rowcol_to_a1
from google.oauth2.service_account import Credentials
from datetime import datetime, date, timedelta

st.set_page_config(page_title="TELA CLUB v5.3", page_icon="🎾", layout="wide")


# ============================================================
# 섹션 R: 회원명부 함수 (rostor_app.py 통합)
# ============================================================

# ─────────────────────────────────────────────────────────
# 비밀번호: 우선 st.secrets에서 읽고, 없으면 기본값(개발용)
# 운영 시 반드시 .streamlit/secrets.toml 또는 Streamlit Cloud Secrets에 등록:
#   ADMIN_PASSWORD = "원하는비번"  (Secrets에서 ADMIN_PASSWORD 키 사용)
RS_ADMIN_PASSWORD = st.secrets.get("ADMIN_PASSWORD", "1223")
RS_SCOPES = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]
RS_COLUMNS = [
    "id", "category", "name", "cafe_id", "birth_year", "gender",
    "phone", "region", "join_date", "dormant_period", "leave_date",
    "email", "application", "memo", "updated_at",
    "deleted_at",   # 소프트 삭제: 삭제 시각. 비어있으면 정상 회원.
    "league",
]
AUDIT_COLUMNS = ["timestamp", "action", "member_id", "member_name", "detail"]
TRASH_DAYS    = 90   # 휴지통 보관 기간 (일)
CATEGORIES   = ["마스터","고문","회장","총무","경기이사","홍보이사","정회원","휴면","탈퇴"]
CAT_ORDER    = {c: i for i, c in enumerate(CATEGORIES)}
OFFICER_CATS = ["마스터","고문","회장","총무","경기이사","홍보이사"]
RS_FS = "font-size:12px"

# ── CSS ───────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@300;400;500;700;900&display=swap');
html, body, [class*="css"] { font-family:'Noto Sans KR',sans-serif !important; }
.app-header {
    background:linear-gradient(135deg,#1a2e4a 0%,#2563eb 100%);
    border-radius:16px; padding:22px 28px; margin-bottom:20px;
    display:flex; align-items:center; gap:16px;
    box-shadow:0 8px 32px rgba(37,99,235,.25);
}
.app-header h1 { color:#fff; margin:0; font-size:22px; font-weight:800; letter-spacing:-0.5px; }
.app-header p  { color:rgba(255,255,255,.65); margin:2px 0 0; font-size:13px; }
.stat-card { background:#fff; border-radius:12px; padding:14px 16px;
    box-shadow:0 2px 12px rgba(0,0,0,.08); border-left:4px solid #2563eb; }
.stat-card.officer { border-color:#f59e0b; }
.stat-card.regular { border-color:#2563eb; }
.stat-card.dormant { border-color:#ca8a04; }
.stat-card.left    { border-color:#dc2626; }
.stat-card.total   { border-color:#1a2e4a; background:#1a2e4a; }
.stat-label       { font-size:11px; font-weight:700; color:#6b7280; text-transform:uppercase; letter-spacing:.5px; }
.stat-label.white { color:rgba(255,255,255,.7); }
.stat-num         { font-size:26px; font-weight:900; color:#1a2e4a; line-height:1.1; }
.stat-num.white   { color:#fff; }
.stat-sub         { font-size:11px; color:#9ca3af; margin-top:1px; }
.stat-sub.white   { color:rgba(255,255,255,.55); }
.badge { display:inline-block; padding:2px 9px; border-radius:20px; font-size:11px; font-weight:700; white-space:nowrap; }
.b-master    { background:#fef3c7; color:#92400e; }
.b-advisor   { background:#fde68a; color:#78350f; }
.b-president { background:#d1fae5; color:#065f46; }
.b-secretary { background:#a7f3d0; color:#064e3b; }
.b-sports    { background:#bfdbfe; color:#1e40af; }
.b-pr        { background:#c7d2fe; color:#3730a3; }
.b-regular   { background:#e0f2fe; color:#0369a1; }
.b-dormant   { background:#fef9c3; color:#854d0e; }
.b-left      { background:#fee2e2; color:#991b1b; }
.stButton > button {
    border-radius:7px !important;
    font-family:'Noto Sans KR',sans-serif !important;
    font-weight:700 !important;
    font-size:12px !important;
}
/* [다이어트] 미사용 div.edit-col / save-col / cancel-col / delete-col CSS 제거
   (.st-key-form_save 등 방식으로 전환되어 더 이상 사용되지 않음) */

/* 반응형 — 모바일(아이폰) 최적화 */
section[data-testid="stMain"] .stMainBlockContainer,
.block-container {
    max-width: 100% !important;
    width: 100% !important;
    padding-left: 1rem !important;
    padding-right: 1rem !important;
}
@media (max-width: 430px) {
    .app-header { padding: 14px 16px !important; border-radius: 10px !important; }
    .app-header h1 { font-size: 15px !important; }
    .app-header p  { font-size: 10px !important; }
    .stat-card { padding: 8px 10px !important; min-width: 70px !important; }
    .stat-num  { font-size: 18px !important; }
    .stat-label, .stat-sub { font-size: 10px !important; }
    section[data-testid="stMain"] .stMainBlockContainer,
    .block-container { padding-left: 0.3rem !important; padding-right: 0.3rem !important; }
}
/* 다이얼로그 */
div[data-testid="stDialog"] > div { max-width: 95vw !important; width: 95vw !important; }

/* ── 다이얼로그 공통 버튼 스타일 (전역 1회 선언, dialog 내부 중복 제거) ── */
/* 저장 (파랑) */
.st-key-form_save button { background:#2563eb !important; color:#fff !important; border:none !important; font-weight:700 !important; }
.st-key-form_save button:hover { background:#1d4ed8 !important; color:#fff !important; }
.st-key-form_save button p { color:#fff !important; }
/* 취소 (회색) */
.st-key-form_cancel button, .st-key-confirm_del_no button { background:#6b7280 !important; color:#fff !important; border:none !important; font-weight:700 !important; height:42px !important; }
.st-key-form_cancel button:hover, .st-key-confirm_del_no button:hover { background:#4b5563 !important; color:#fff !important; }
.st-key-form_cancel button p, .st-key-confirm_del_no button p { color:#fff !important; }
/* 삭제 (빨강) */
.st-key-form_delete button, .st-key-confirm_del_yes button { background:#ef4444 !important; color:#fff !important; border:none !important; font-weight:700 !important; height:42px !important; }
.st-key-form_delete button:hover, .st-key-confirm_del_yes button:hover { background:#dc2626 !important; color:#fff !important; }
.st-key-form_delete button p, .st-key-confirm_del_yes button p { color:#fff !important; }
/* 휴면 기간 추가 (베이지) */
.st-key-add_dormant_btn button { background:#fef3c7 !important; color:#854d0e !important; border:1px dashed #ca8a04 !important; font-weight:700 !important; }
.st-key-add_dormant_btn button:hover { background:#fde68a !important; }
/* 휴면 기간 행 래퍼 */
div.dormant-row-wrap { background:#fef9c3; border-radius:8px; padding:8px 12px; margin-bottom:6px; border-left:3px solid #ca8a04; }

/* ── 회원 목록 행: 열람/수정 버튼 (와일드카드로 전역 1회 선언) ── */
/* 행마다 .st-key-detail_{id} / .st-key-edit_{id} 형태로 키가 부여되므로 attr selector 사용 */
[class*="st-key-detail_"] button { background:#f0f9ff !important; color:#0369a1 !important; border:1px solid #bae6fd !important; font-size:11px !important; font-weight:700 !important; padding:2px 4px !important; height:28px !important; }
[class*="st-key-detail_"] button:hover { background:#dbeafe !important; }
[class*="st-key-edit_"] button { background:#f0fdf4 !important; color:#15803d !important; border:1px solid #bbf7d0 !important; font-size:11px !important; font-weight:700 !important; padding:2px 4px !important; height:28px !important; }
[class*="st-key-edit_"] button:hover { background:#dcfce7 !important; }

/* ── 사이드바 컴팩트 + 매치카드 ── */
[data-testid="stSidebar"] { min-width:230px; max-width:270px; }
[data-testid="stSidebar"] .stMarkdown p { margin-bottom: 2px !important; }
[data-testid="stSidebar"] .stMarkdown { margin-bottom: 0px !important; }
[data-testid="stSidebar"] hr { margin: 6px 0 !important; }
[data-testid="stSidebar"] .stRadio,
[data-testid="stSidebar"] .stNumberInput,
[data-testid="stSidebar"] .stTextInput,
[data-testid="stSidebar"] .stButton { margin-bottom: 2px !important; }
[data-testid="stSidebar"] .stCheckbox { margin-bottom: 0px !important; }
[data-testid="stSidebar"] [data-testid="stVerticalBlock"] > div { gap: 4px !important; }
.match-card { border:1px solid #ddd; border-radius:6px; margin-bottom:4px; overflow:hidden; background:#fff; }
</style>
""", unsafe_allow_html=True)

# ── 세션 상태 ─────────────────────────────────────────────
for k, v in {
    "filter_cat":    "전체",
    "search_q":      "",
    "search_active": "",
    "open_dialog":   None,
    "edit_target":   None,
    "admin_authed":  False,
    "auth_time":     None,   # 관리자 인증 시각 (타임아웃용)
    "show_trash":    False,  # 휴지통 보기 토글
    "bulk_selected": set(),  # 선택된 회원 ID set
    "bulk_all_flag":  False,  # 헤더 체크박스 전체선택 플래그
}.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ── 세션 타임아웃 체크 (1시간) ────────────────────────────
SESSION_TIMEOUT_MIN = 60
if st.session_state.admin_authed and st.session_state.auth_time:
    elapsed = (datetime.now() - st.session_state.auth_time).total_seconds() / 60
    if elapsed >= SESSION_TIMEOUT_MIN:
        st.session_state.admin_authed = False
        st.session_state.auth_time    = None
        st.toast("⏰ 관리자 세션이 만료되었습니다. 다시 인증해 주세요.", icon="🔒")

# ── Google Sheets ─────────────────────────────────────────
@st.cache_resource
def _get_gsheet_connection():
    """구글 시트 연결 객체만 캐싱 (API 호출 없음)."""
    creds  = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=RS_SCOPES)
    client = gspread.authorize(creds)
    wb     = client.open_by_key(st.secrets["SHEET_ID"])
    return wb

def get_sheet():
    """sheet1 반환. 컬럼 마이그레이션은 최초 1회만 실행."""
    wb    = _get_gsheet_connection()
    sheet = wb.sheet1
    # 마이그레이션은 세션당 1회만 (session_state 플래그)
    if not st.session_state.get("_sheet_migrated"):
        try:
            existing_headers = sheet.row_values(1)
            if not existing_headers or existing_headers[0] != "id":
                sheet.insert_row(RS_COLUMNS, 1)
            else:
                missing = [c for c in RS_COLUMNS if c not in existing_headers]
                for col_name in missing:
                    next_col = len(existing_headers) + 1
                    sheet.update_cell(1, next_col, col_name)
                    existing_headers.append(col_name)
        except Exception:
            pass
        st.session_state["_sheet_migrated"] = True
    return sheet

@st.cache_resource
def get_audit_sheet():
    """변경 이력 시트 (audit_log 탭). 없으면 자동 생성."""
    wb = _get_gsheet_connection()
    try:
        asheet = wb.worksheet("audit_log")
    except gspread.exceptions.WorksheetNotFound:
        asheet = wb.add_worksheet(title="audit_log", rows=2000, cols=len(AUDIT_COLUMNS))
        asheet.insert_row(AUDIT_COLUMNS, 1)
    return asheet

def log_audit(action: str, member_id, member_name: str, detail: str = ""):
    """변경 이력을 audit_log 시트에 기록. 실패해도 메인 기능에 영향 없도록 try/except."""
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        get_audit_sheet().append_row(
            [ts, action, str(member_id), member_name, detail],
            value_input_option="USER_ENTERED"
        )
    except Exception:
        pass

@st.cache_data(ttl=120, show_spinner=False)
def _load_records_cached() -> list:
    """
    구글 시트 전체 레코드를 120초 TTL로 캐싱.
    API 429 방지용. 저장/수정 후 st.cache_data.clear()로 즉시 무효화.
    """
    wb    = _get_gsheet_connection()
    sheet = wb.sheet1
    return sheet.get_all_records()

def load_df(include_deleted=False):
    records = _load_records_cached()
    if not records:
        df = pd.DataFrame(columns=RS_COLUMNS)
    else:
        df = pd.DataFrame(records)
        for col in RS_COLUMNS:
            if col not in df.columns:
                df[col] = ""
        df = df[RS_COLUMNS]
    df["id"]         = pd.to_numeric(df["id"],         errors="coerce").fillna(0).astype(int)
    df["birth_year"] = pd.to_numeric(df["birth_year"], errors="coerce")
    df["deleted_at"] = df["deleted_at"].astype(str).str.strip()
    if not include_deleted:
        df = df[df["deleted_at"] == ""]
    return df

def load_df_for_match() -> pd.DataFrame:
    """
    랜덤페어용 회원 데이터 로더.
    - 탈퇴(deleted_at 있음, category=='탈퇴', leave_date 있음) 제외
    - 반환: id, name, gender, category, league, dormant_period 포함 df
    """
    df = load_df(include_deleted=False)
    if df.empty:
        return df
    # 탈퇴 카테고리 제외
    df = df[df["category"] != "탈퇴"].copy()
    # leave_date 있으면 제외
    df = df[df["leave_date"].astype(str).str.strip() == ""].copy()
    return df.reset_index(drop=True)

def save_league_to_sheet(member_id: int, league_value: str):
    """구글 시트의 특정 회원(id 기준) league 컬럼 업데이트."""
    sheet   = _get_gsheet_connection().sheet1
    all_ids = sheet.col_values(1)
    headers = sheet.row_values(1)
    if "league" not in headers:
        st.error("구글 시트에 league 컬럼이 없습니다. 앱을 새로고침해주세요.")
        return False
    league_col = headers.index("league") + 1
    try:
        idx = all_ids.index(str(member_id))
    except ValueError:
        st.error(f"시트에서 id={member_id}를 찾을 수 없습니다.")
        return False
    sheet.update_cell(idx + 1, league_col, league_value)
    st.cache_data.clear()
    return True

def save_league_by_name(member_name: str, league_value: str) -> bool:
    """구글 시트에서 이름으로 회원을 찾아 league 컬럼 업데이트."""
    sheet   = _get_gsheet_connection().sheet1
    headers = sheet.row_values(1)
    if "league" not in headers:
        st.error("구글 시트에 league 컬럼이 없습니다. 앱을 새로고침해주세요.")
        return False
    if "name" not in headers:
        st.error("구글 시트에 name 컬럼이 없습니다.")
        return False
    name_col   = headers.index("name") + 1
    league_col = headers.index("league") + 1
    # 이름 열 전체 읽기
    all_names  = sheet.col_values(name_col)
    # 헤더(1행) 제외하고 이름 검색
    found_rows = [i+1 for i, n in enumerate(all_names) if i > 0 and n.strip() == member_name.strip()]
    if not found_rows:
        st.error(f"구글 시트에서 '{member_name}'을 찾을 수 없습니다.")
        return False
    # 첫 번째 매칭 행 업데이트
    sheet.update_cell(found_rows[0], league_col, league_value)
    st.cache_data.clear()
    return True

def save_row(df, row, is_new, action_detail=""):
    sheet = _get_gsheet_connection().sheet1
    row["updated_at"] = datetime.today().strftime("%Y-%m-%d %H:%M")
    if "deleted_at" not in row:
        row["deleted_at"] = ""
    values = [str(row.get(c,"") or "") for c in RS_COLUMNS]
    action = "등록" if is_new else "수정"
    if is_new:
        sheet.append_row(values, value_input_option="USER_ENTERED")
    else:
        all_ids = sheet.col_values(1)
        try:
            ri         = all_ids.index(str(row["id"])) + 1
            start_cell = rowcol_to_a1(ri, 1)
            end_cell   = rowcol_to_a1(ri, len(RS_COLUMNS))
            sheet.update(f"{start_cell}:{end_cell}", [values], value_input_option="USER_ENTERED")
        except ValueError:
            sheet.append_row(values, value_input_option="USER_ENTERED")
    log_audit(action, row.get("id",""), row.get("name",""), action_detail or f"카테고리:{row.get('category','')}")

def soft_delete_row(mid, member_name):
    """소프트 삭제: deleted_at 컬럼에 현재 시각을 기록. 행은 보존됨."""
    sheet   = _get_gsheet_connection().sheet1
    all_ids = sheet.col_values(1)
    if not all_ids or all_ids[0] != "id":
        raise RuntimeError("시트 헤더가 손상되었습니다.")
    try:
        idx = all_ids.index(str(mid))
        if idx == 0:
            raise RuntimeError("헤더 행은 삭제할 수 없습니다.")
        ri         = idx + 1
        del_col    = RS_COLUMNS.index("deleted_at") + 1
        del_cell   = rowcol_to_a1(ri, del_col)
        sheet.update(del_cell, [[datetime.now().strftime("%Y-%m-%d %H:%M:%S")]],
                     value_input_option="USER_ENTERED")
        log_audit("삭제(소프트)", mid, member_name, f"휴지통 이동. {TRASH_DAYS}일 후 영구 삭제.")
    except ValueError:
        pass

def hard_delete_row(mid, member_name):
    """영구 삭제: 시트에서 행 자체를 제거."""
    sheet   = _get_gsheet_connection().sheet1
    all_ids = sheet.col_values(1)
    if not all_ids or all_ids[0] != "id":
        raise RuntimeError("시트 헤더가 손상되었습니다.")
    try:
        idx = all_ids.index(str(mid))
        if idx == 0:
            raise RuntimeError("헤더 행은 삭제할 수 없습니다.")
        sheet.delete_rows(idx + 1)
        log_audit("삭제(영구)", mid, member_name, "영구 삭제 완료.")
    except ValueError:
        pass

def restore_row(mid, member_name):
    """소프트 삭제 취소: deleted_at을 비워서 복구."""
    sheet   = _get_gsheet_connection().sheet1
    all_ids = sheet.col_values(1)
    try:
        idx = all_ids.index(str(mid))
        ri  = idx + 1
        del_col  = RS_COLUMNS.index("deleted_at") + 1
        del_cell = rowcol_to_a1(ri, del_col)
        sheet.update(del_cell, [[""]], value_input_option="USER_ENTERED")
        log_audit("복구", mid, member_name, "휴지통에서 복구.")
    except ValueError:
        pass

def next_id(df):
    return int(df["id"].max()) + 1 if not df.empty else 1

# ── 헬퍼 ──────────────────────────────────────────────────
BADGE_CLS = {
    "마스터":"b-master","고문":"b-advisor","회장":"b-president","총무":"b-secretary",
    "경기이사":"b-sports","홍보이사":"b-pr","정회원":"b-regular","휴면":"b-dormant","탈퇴":"b-left",
}
def badge(cat):
    return f'<span class="badge {BADGE_CLS.get(cat,"b-regular")}">{cat}</span>'

def gender_html(g):
    c = {"남":"#2563eb","여":"#db2777"}.get(g,"#374151")
    return f'<span style="color:{c};font-weight:700;{RS_FS}">{g}</span>'

def cell(txt, color="#374151", extra=""):
    return f"<div style='padding:7px 0;{RS_FS};color:{color};{extra}'>{txt}</div>"

# ─────────────────────────────────────────────────────────
# 검증 함수
# ─────────────────────────────────────────────────────────
PHONE_RE = re.compile(r"^\d{2,4}-?\d{3,4}-?\d{4}$")
EMAIL_RE = re.compile(r"^[\w\.\-+]+@[\w\.\-]+\.\w{2,}$")
DATE_RE  = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# [제거] DORMANT_RANGE_RE: 정의만 되고 어디서도 사용되지 않음.

def validate_phone(s):
    if not s: return True
    return bool(PHONE_RE.match(s.strip()))

def validate_email(s):
    if not s: return True
    return bool(EMAIL_RE.match(s.strip()))

def validate_date(s):
    if not s: return True
    if not DATE_RE.match(str(s).strip()): return False
    try:
        datetime.strptime(str(s).strip(), "%Y-%m-%d")
        return True
    except ValueError:
        return False

def normalize_date(s):
    """다양한 입력 형식을 YYYY-MM-DD로 자동 변환.
    - 8자리: 20260101 → 2026-01-01
    - 6자리: 260101  → 2026-01-01
    - 구분자 혼용: 2026/01/01, 2026.01.01 → 2026-01-01
    """
    if not s: return ""
    s = str(s).strip()
    if not s: return ""
    cleaned = re.sub(r"[/.]", "-", s)
    if DATE_RE.match(cleaned):
        try:
            return datetime.strptime(cleaned, "%Y-%m-%d").strftime("%Y-%m-%d")
        except ValueError:
            return s
    digits = re.sub(r"\D", "", s)
    if len(digits) == 8:
        try: return datetime.strptime(digits, "%Y%m%d").strftime("%Y-%m-%d")
        except ValueError: return s
    elif len(digits) == 6:
        try: return datetime.strptime("20" + digits, "%Y%m%d").strftime("%Y-%m-%d")
        except ValueError: return s
    return s

def normalize_phone(s):
    """연락처 자동 포맷팅: 01012345678 → 010-1234-5678"""
    if not s: return ""
    digits = re.sub(r"\D", "", str(s).strip())
    if len(digits) == 11 and digits.startswith("010"):
        return f"{digits[:3]}-{digits[3:7]}-{digits[7:]}"
    elif len(digits) == 11:
        return f"{digits[:3]}-{digits[3:7]}-{digits[7:]}"
    elif len(digits) == 10:
        return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"
    return s  # 변환 불가 시 원본

# ─────────────────────────────────────────────────────────
# 휴면 기간 관리 (누적)
# ─────────────────────────────────────────────────────────
def parse_dormant_periods(s):
    if not s or not str(s).strip(): return []
    periods = []
    for chunk in str(s).split(";"):
        chunk = chunk.strip()
        if not chunk: continue
        if "~" in chunk:
            start, _, end = chunk.partition("~")
            periods.append({"start": start.strip(), "end": end.strip()})
        else:
            periods.append({"start": chunk, "end": ""})
    return periods

def format_dormant_periods(periods):
    parts = []
    for p in periods:
        start = (p.get("start") or "").strip()
        end   = (p.get("end") or "").strip()
        if not start: continue
        parts.append(f"{start}~{end}")
    return "; ".join(parts)

def has_ongoing_dormant(s):
    return any(not p["end"] for p in parse_dormant_periods(s))

def check_dormant_overlap(periods):
    """휴면 기간 겹침 및 진행중 중복 검사. 문제 있으면 에러 문자열 반환, 없으면 None."""
    ongoing_count = 0
    date_ranges = []
    for i, p in enumerate(periods):
        s = p.get("start","")
        e = p.get("end","")
        if not e:
            ongoing_count += 1
            if ongoing_count > 1:
                return f"진행중 휴면 기간이 2개 이상입니다. 1개만 허용됩니다."
        else:
            try:
                sd = datetime.strptime(s, "%Y-%m-%d").date()
                ed = datetime.strptime(e, "%Y-%m-%d").date()
                for j, (psd, ped) in enumerate(date_ranges):
                    if sd <= ped and ed >= psd:
                        return f"#{i+1}번 기간이 #{j+1}번 기간과 겹칩니다."
                date_ranges.append((sd, ed))
            except ValueError:
                pass
    return None

def check_duplicate(df, name, phone, cafe_id, exclude_id=None):
    if df.empty: return None
    target = df[df["id"] != exclude_id] if exclude_id is not None else df
    name_n  = (name or "").strip()
    phone_n = (phone or "").strip()
    cafe_n  = (cafe_id or "").strip()
    if name_n and phone_n:
        dup = target[(target["name"].astype(str).str.strip() == name_n) &
                     (target["phone"].astype(str).str.strip() == phone_n)]
        if not dup.empty:
            return f"이름+연락처가 동일한 회원이 이미 있습니다 (No.{int(dup.iloc[0]['id'])} {dup.iloc[0]['name']})"
    if cafe_n:
        dup = target[target["cafe_id"].astype(str).str.strip() == cafe_n]
        if not dup.empty:
            return f"카페ID가 동일한 회원이 이미 있습니다 (No.{int(dup.iloc[0]['id'])} {dup.iloc[0]['name']})"
    return None

# ─────────────────────────────────────────────────────────
# 생일자 / 휴면 알림 헬퍼
# ─────────────────────────────────────────────────────────
# [제거] get_birthday_members: 데이터에 birth_month_day 컬럼이 존재하지 않아
# 항상 빈 리스트만 반환하던 죽은 코드. 호출처도 없음.

def get_this_month_birthdays(df):
    """이번 달 입회 기념일 회원 (입회월 기준)"""
    today = date.today()
    result = []
    for _, row in df.iterrows():
        jd = str(row.get("join_date","") or "").strip()
        if not jd: continue
        try:
            jdate = datetime.strptime(jd[:10], "%Y-%m-%d").date()
            if jdate.month == today.month:
                years = today.year - jdate.year
                result.append({"name": row["name"], "join_date": jd, "years": years, "category": row["category"]})
        except ValueError:
            pass
    return result

def get_long_dormant_members(df, months=3):
    """진행중 휴면이 N개월 이상인 회원 목록 반환"""
    today   = date.today()
    cutoff  = today - timedelta(days=months * 30)
    result  = []
    for _, row in df.iterrows():
        if row.get("category") != "휴면": continue
        for p in parse_dormant_periods(str(row.get("dormant_period","") or "")):
            if not p["end"] and p["start"]:
                try:
                    sd = datetime.strptime(p["start"], "%Y-%m-%d").date()
                    if sd <= cutoff:
                        result.append({"name": row["name"], "start": p["start"],
                                       "days": (today - sd).days})
                except ValueError:
                    pass
    return result

# ─────────────────────────────────────────────────────────
#  팝업 다이얼로그: 관리자 비밀번호
# ─────────────────────────────────────────────────────────
@st.dialog("🔐 관리자 인증")
def dialog_pw(target):
    action_label = "수정" if target["type"] == "edit" else "삭제"
    st.markdown(f"**[{target['name']}]** 회원 {action_label}을 위해 비밀번호를 입력하세요.")
    st.caption("💡 한 번 인증하면 브라우저를 닫기 전까지 다시 묻지 않습니다.")
    pw = st.text_input("비밀번호", type="password", placeholder="비밀번호 입력")
    col_ok, col_cancel = st.columns(2)
    if col_ok.button("✅ 확인", type="primary", use_container_width=True):
        if pw == RS_ADMIN_PASSWORD:
            # 인증 성공 → 세션 전체 인증 플래그 설정
            st.session_state.admin_authed = True
            st.session_state.auth_time    = datetime.now()   # 타임아웃 기산점
            if target["type"] == "edit":
                st.session_state.open_dialog = "edit"
            else:
                st.session_state.open_dialog = "delete_confirm"
            st.session_state.edit_target = target
            st.rerun()
        else:
            st.error("❌ 비밀번호가 틀렸습니다.")
    if col_cancel.button("취소", use_container_width=True):
        st.session_state.open_dialog  = None
        st.session_state.edit_target  = None
        st.rerun()

# ─────────────────────────────────────────────────────────
#  팝업 다이얼로그: 삭제 확인
# ─────────────────────────────────────────────────────────
@st.dialog("🗑️ 삭제 확인")
def dialog_delete(target):
    st.warning(
        f"**[{target['name']}]** 회원을 휴지통으로 이동합니다.\n\n"
        f"휴지통에서 **{TRASH_DAYS}일 후 자동 영구 삭제**됩니다. 그 전에는 복구 가능합니다."
    )
    cy, cn = st.columns(2)
    if cy.button("🗑️ 휴지통으로 이동", type="primary", use_container_width=True):
        with st.spinner("삭제 중…"):
            soft_delete_row(target["id"], target["name"])
        st.session_state.open_dialog   = None
        st.session_state.edit_target   = None
        st.cache_data.clear()
        st.rerun()
    if cn.button("취소", use_container_width=True):
        st.session_state.open_dialog   = None
        st.session_state.edit_target   = None
        st.rerun()

# ─────────────────────────────────────────────────────────
#  팝업 다이얼로그: 회원 상세 보기 (읽기 전용 — 비밀번호 불필요)
# ─────────────────────────────────────────────────────────
@st.dialog("👤 회원 상세 정보", width="large")
def dialog_detail(row):
    cat   = str(row.get("category",""))
    name  = str(row.get("name",""))
    gender = str(row.get("gender",""))
    by    = row.get("birth_year","")
    age   = (date.today().year - int(by)) if by and str(by).isdigit() else None

    # ── 상단 카드 ──
    gender_color = {"남":"#2563eb","여":"#db2777"}.get(gender,"#374151")
    st.markdown(f"""
    <div style='background:linear-gradient(135deg,#1a2e4a,#2563eb);
         border-radius:14px;padding:20px 24px;margin-bottom:16px;color:#fff;
         display:flex;align-items:center;gap:16px;'>
      <div style='font-size:48px;line-height:1'>{"🎾"}</div>
      <div>
        <div style='font-size:22px;font-weight:900;margin-bottom:4px'>{name}</div>
        <div style='font-size:13px;opacity:.85;display:flex;gap:12px;flex-wrap:wrap'>
          <span>{badge(cat)}</span>
          <span style='color:{gender_color};font-weight:700'>{gender}</span>
          {"<span>생년 " + str(int(by)) + "년" + (f" ({age}세)" if age else "") + "</span>" if by else ""}
        </div>
      </div>
    </div>
    """, unsafe_allow_html=True)

    # ── 기본 정보 ──
    def info_row(label, value, color="#1a2e4a"):
        if not value or str(value).strip() in ("", "—", "nan"): value = "—"
        return (f"<div style='display:flex;padding:8px 0;border-bottom:1px solid #f1f5f9;{RS_FS}'>"
                f"<div style='width:100px;color:#6b7280;font-weight:600;flex-shrink:0'>{label}</div>"
                f"<div style='color:{color};font-weight:500'>{value}</div></div>")

    st.markdown("**📋 기본 정보**")
    st.markdown(
        info_row("카페ID",    row.get("cafe_id","")) +
        info_row("연락처",    row.get("phone",""),    "#2563eb") +
        info_row("이메일",    row.get("email",""),    "#2563eb") +
        info_row("거주지",    row.get("region","")) +
        info_row("입회일",    row.get("join_date","")) +
        info_row("입회신청서", row.get("application","")),
        unsafe_allow_html=True)

    # ── 휴면 기간 타임라인 ──
    dorm_raw = str(row.get("dormant_period","") or "").strip()
    if dorm_raw:
        st.markdown("**💤 휴면 기간 이력**")
        periods = parse_dormant_periods(dorm_raw)
        for i, p in enumerate(periods, 1):
            is_ongoing = not p["end"]
            status_badge = ("<span style='background:#fef9c3;color:#854d0e;padding:1px 8px;"
                            "border-radius:20px;font-size:11px;font-weight:700'>🟡 진행중</span>"
                            if is_ongoing else
                            "<span style='background:#dcfce7;color:#166534;padding:1px 8px;"
                            "border-radius:20px;font-size:11px;font-weight:700'>✅ 종료</span>")
            end_disp = p["end"] if p["end"] else "현재"
            # 기간(일수) 계산
            try:
                sd = datetime.strptime(p["start"], "%Y-%m-%d").date()
                ed = date.today() if is_ongoing else datetime.strptime(p["end"], "%Y-%m-%d").date()
                days = (ed - sd).days
                duration = f"({days}일)"
            except Exception:
                duration = ""
            st.markdown(
                f"<div style='display:flex;align-items:center;gap:10px;padding:7px 12px;"
                f"background:#fef9c3;border-radius:8px;margin-bottom:4px;{RS_FS}'>"
                f"<span style='color:#854d0e;font-weight:700'>#{i}</span>"
                f"<span>{p['start']} ~ {end_disp}</span>"
                f"<span style='color:#9ca3af'>{duration}</span>"
                f"{status_badge}</div>",
                unsafe_allow_html=True)

    # ── 탈퇴일 ──
    leave = str(row.get("leave_date","") or "").strip()
    if leave:
        st.markdown(
            f"<div style='background:#fee2e2;border-left:4px solid #ef4444;"
            f"padding:8px 14px;border-radius:6px;{RS_FS};color:#7f1d1d;margin-top:8px'>"
            f"🚪 탈퇴일: <b>{leave}</b></div>", unsafe_allow_html=True)

    # ── 메모 ──
    memo = str(row.get("memo","") or "").strip()
    if memo:
        st.markdown("**📝 메모**")
        st.markdown(
            f"<div style='background:#f8fafc;border-left:3px solid #94a3b8;"
            f"padding:10px 14px;border-radius:6px;{RS_FS};color:#374151;white-space:pre-wrap'>"
            f"{memo}</div>", unsafe_allow_html=True)

    # ── 업데이트 시각 ──
    upd = str(row.get("updated_at","") or "").strip()
    if upd:
        st.markdown(f"<div style='{RS_FS};color:#9ca3af;text-align:right;margin-top:12px'>최근 수정: {upd}</div>",
                    unsafe_allow_html=True)

    st.divider()
    if st.button("✕ 닫기", use_container_width=True):
        st.session_state.open_dialog = None
        st.session_state.edit_target = None
        st.rerun()

# ─────────────────────────────────────────────────────────
#  팝업 다이얼로그: 삭제 1차 확인 (비번 전 경고)
# ─────────────────────────────────────────────────────────
@st.dialog("⚠️ 회원 삭제 확인")
def dialog_confirm_delete(target):
    st.markdown(f"""
    <div style="text-align:center; padding: 8px 0 16px;">
        <div style="font-size:48px; margin-bottom:12px;">🚨</div>
        <div style="font-size:17px; font-weight:700; color:#1a2e4a; margin-bottom:8px;">
            정말로 삭제하시겠습니까?
        </div>
        <div style="font-size:14px; color:#6b7280; line-height:1.6;">
            <b style="color:#dc2626;">[{target['name']}]</b> 회원의 모든 정보가<br>
            영구적으로 삭제되며 복구할 수 없습니다.
        </div>
    </div>
    """, unsafe_allow_html=True)
    st.divider()

    # [다이어트] 버튼 스타일은 전역 CSS에 통합됨 (.st-key-confirm_del_yes/no)

    cy, cn = st.columns([1, 1], gap="small")
    with cy:
        if st.button("🗑️ 삭제 진행", use_container_width=True, key="confirm_del_yes"):
            st.session_state.edit_target = target
            # 이미 세션 인증된 경우 비번 건너뛰고 바로 최종 삭제 확인으로
            if st.session_state.admin_authed:
                st.session_state.open_dialog = "delete_confirm"
            else:
                st.session_state.open_dialog = "pw_delete"
            st.rerun()
    with cn:
        if st.button("✕ 취소", use_container_width=True, key="confirm_del_no"):
            st.session_state.open_dialog   = None
            st.session_state.edit_target   = None
            st.rerun()



@st.dialog("회원 정보", width="large")
def dialog_form(df, existing=None):
    title = "✏️ 회원 정보 수정" if existing else "➕ 새 회원 등록"
    st.markdown(f"#### {title}")

    # 행1: 구분 / 성명 / 성별
    c1,c2,c3 = st.columns([1,1,1])
    with c1:
        cat = st.selectbox("구분 *", CATEGORIES,
            index=CATEGORIES.index(existing["category"]) if existing else 6)
    with c2:
        name = st.text_input("성명 *",
            value=existing["name"] if existing else "", placeholder="홍길동")
    with c3:
        gender = st.selectbox("성별 *", ["남","여"],
            index=0 if not existing else (0 if existing["gender"]=="남" else 1))

    # 행2: 카페ID / 생년 / 연락처 / 거주지
    c4,c5,c6,c6b = st.columns([1,1,1,1])
    with c4:
        cafe_id = st.text_input("카페ID",
            value=existing["cafe_id"] if existing else "", placeholder="cafe_id")
    with c5:
        by_v = ""
        if existing and existing.get("birth_year"):
            try: by_v = str(int(existing["birth_year"]))
            except (ValueError, TypeError): pass
        birth_year = st.text_input("생년 (YYYY)", value=by_v, placeholder="1990", max_chars=4)
    with c6:
        phone = st.text_input("연락처",
            value=existing["phone"] if existing else "", placeholder="010-0000-0000")
    with c6b:
        region = st.text_input("거주지",
            value=existing["region"] if existing else "", placeholder="서울 강남구")

    # 행3: 입회일 / 이메일
    c7,c8 = st.columns([1,2])
    with c7:
        jd_val = None
        if existing and existing.get("join_date"):
            try: jd_val = datetime.strptime(str(existing["join_date"]),"%Y-%m-%d").date()
            except ValueError: pass
        join_date = st.date_input("입회일", value=jd_val or date.today())
    with c8:
        email = st.text_input("이메일",
            value=existing["email"] if existing else "", placeholder="example@email.com")

    # 행4: 휴면기간 (누적 관리) — 폭 전체 사용 (행 단위 입력이라 넓게)
    # ─────────────────────────────────────────────────────────
    # 휴면 기간 세션 초기화: 이 다이얼로그가 처음 열릴 때만 기존 값 로드
    # (다른 위젯 조작으로 인한 rerun에서는 기존 편집 상태 유지)
    target_id = existing["id"] if existing else "new"
    dorm_session_key = f"dormant_edit_list_{target_id}"
    if dorm_session_key not in st.session_state:
        if existing and existing.get("dormant_period"):
            st.session_state[dorm_session_key] = parse_dormant_periods(existing["dormant_period"])
        else:
            st.session_state[dorm_session_key] = []

    # ── 콜백 함수: 다이얼로그 안에서는 st.rerun()을 호출하면 다이얼로그가 닫혀버림
    # 콜백은 다이얼로그를 닫지 않고 세션 상태만 변경한 뒤 자연스럽게 리렌더됨
    def _add_dormant_row(key=dorm_session_key):
        st.session_state[key].append({"start": "", "end": ""})

    def _delete_dormant_row(key, idx):
        if 0 <= idx < len(st.session_state[key]):
            st.session_state[key].pop(idx)

    def _normalize_date_input(widget_key):
        """텍스트 입력의 값을 정규화된 날짜로 자동 변환"""
        v = st.session_state.get(widget_key, "")
        if v:
            st.session_state[widget_key] = normalize_date(v)

    st.markdown("**휴면 기간** <span style='font-size:11px;color:#6b7280;'>(진행중이면 자동→휴면, 모두 종료 시 자동→정회원)</span>", unsafe_allow_html=True)

    # [다이어트] 휴면기간 행 스타일은 전역 CSS에 통합됨 (dormant-row-wrap, .st-key-add_dormant_btn)

    dorm_list = st.session_state[dorm_session_key]

    if not dorm_list:
        st.caption("📭 등록된 휴면 기간이 없습니다. 아래 '+ 기간 추가' 버튼으로 추가하세요.")
    else:
        for i, p in enumerate(dorm_list):
            st.markdown('<div class="dormant-row-wrap">', unsafe_allow_html=True)
            rc_lbl, rc_start, rc_end, rc_status, rc_del = st.columns([0.4, 1.5, 1.5, 1, 0.5])
            with rc_lbl:
                st.markdown(f"<div style='padding-top:8px;font-weight:700;color:#854d0e;{RS_FS}'>#{i+1}</div>", unsafe_allow_html=True)
            with rc_start:
                start_key = f"dorm_start_{target_id}_{i}"
                # session에 위젯 값이 없으면 초기값 세팅
                if start_key not in st.session_state:
                    st.session_state[start_key] = p["start"]
                st.text_input(
                    "시작일", key=start_key,
                    placeholder="YYYY-MM-DD 또는 20260101", label_visibility="collapsed",
                    on_change=_normalize_date_input, args=(start_key,)
                )
                dorm_list[i]["start"] = st.session_state[start_key].strip()
            with rc_end:
                end_key = f"dorm_end_{target_id}_{i}"
                if end_key not in st.session_state:
                    st.session_state[end_key] = p["end"]
                st.text_input(
                    "종료일", key=end_key,
                    placeholder="YYYY-MM-DD (비우면 진행중)", label_visibility="collapsed",
                    on_change=_normalize_date_input, args=(end_key,)
                )
                dorm_list[i]["end"] = st.session_state[end_key].strip()
            with rc_status:
                is_ongoing = not dorm_list[i]["end"]
                status_html = ("<span style='color:#ca8a04;font-weight:700;'>🟡 진행중</span>"
                               if is_ongoing else
                               "<span style='color:#16a34a;font-weight:700;'>✅ 종료</span>")
                st.markdown(f"<div style='padding-top:8px;{RS_FS}'>{status_html}</div>", unsafe_allow_html=True)
            with rc_del:
                # ⚠️ 콜백 사용 — 다이얼로그 안에서 st.rerun() 호출 금지 (다이얼로그 튕김 원인)
                st.button("🗑️", key=f"dorm_del_{target_id}_{i}",
                          use_container_width=True, help="이 기간 삭제",
                          on_click=_delete_dormant_row, args=(dorm_session_key, i))
            st.markdown('</div>', unsafe_allow_html=True)

    # + 기간 추가 버튼 — 콜백 방식 (rerun 금지)
    st.button("➕ 휴면 기간 추가", use_container_width=True, key="add_dormant_btn",
              on_click=_add_dormant_row)

    # ─── 탈퇴일 (휴면 아래) ───
    ld_str_existing = ""
    if existing and existing.get("leave_date"):
        ld_str_existing = str(existing["leave_date"]).strip()
    ld_key = f"leave_date_input_{target_id}"
    if ld_key not in st.session_state:
        st.session_state[ld_key] = ld_str_existing
    st.text_input(
        "탈퇴일 (입력 시 구분 자동→탈퇴)",
        key=ld_key,
        placeholder="YYYY-MM-DD 또는 20260101 (비우면 탈퇴 해제)",
        on_change=_normalize_date_input, args=(ld_key,)
    )
    leave_date_str = st.session_state[ld_key]


    # 행5: 입회신청서 / 메모
    c11,c12 = st.columns([1,2])
    with c11:
        app_opts = ["—","Yes","No"]
        app_idx  = 0
        if existing:
            av = existing.get("application","")
            if av in app_opts: app_idx = app_opts.index(av)
        application = st.selectbox("입회신청서", app_opts, index=app_idx)
    with c12:
        memo = st.text_area("메모",
            value=existing["memo"] if existing else "",
            placeholder="특이사항, 역할 등 자유 기재", height=80)

    st.markdown("<br>", unsafe_allow_html=True)

    # [다이어트] 폼 버튼 스타일은 전역 CSS에 통합됨
    # (.st-key-form_save / .st-key-form_cancel / .st-key-form_delete)

    if existing:
        bs, bc, bd = st.columns([1,1,1])
    else:
        bs, bc = st.columns([1,1])
        bd = None

    with bs:
        save_clicked = st.button("💾 저장", use_container_width=True, key="form_save")
    with bc:
        cancel_clicked = st.button("✕ 취소", use_container_width=True, key="form_cancel")
    delete_clicked = False
    if bd:
        with bd:
            delete_clicked = st.button("🗑️ 삭제", use_container_width=True, key="form_delete")

    # ── 다이얼로그 종료 시 휴면 관련 위젯 세션 전부 정리하는 헬퍼 ──
    def _cleanup_dormant_session():
        # 리스트
        if dorm_session_key in st.session_state:
            del st.session_state[dorm_session_key]
        # 각 행의 위젯 키들 (dorm_start_*, dorm_end_*)
        for k in list(st.session_state.keys()):
            if k.startswith(f"dorm_start_{target_id}_") or k.startswith(f"dorm_end_{target_id}_"):
                del st.session_state[k]
        # 탈퇴일 위젯
        if f"leave_date_input_{target_id}" in st.session_state:
            del st.session_state[f"leave_date_input_{target_id}"]

    if cancel_clicked:
        _cleanup_dormant_session()
        st.session_state.open_dialog    = None
        st.session_state.edit_target    = None
        st.rerun()

    if delete_clicked and existing:
        _cleanup_dormant_session()
        st.session_state.open_dialog    = "confirm_delete"
        st.session_state.edit_target    = {"type":"delete","id":existing["id"],"name":existing["name"]}
        st.rerun()

    if save_clicked:
        # ── 검증 단계 (순차적으로 모든 에러를 수집) ──
        errors = []

        # 1. 필수 필드
        if not name.strip():
            errors.append("성명은 필수입니다.")

        # 2. 생년 범위
        by = None
        if birth_year.strip():
            try:
                by = int(birth_year.strip())
                if not (1900 <= by <= date.today().year):
                    errors.append(f"생년은 1900~{date.today().year} 사이여야 합니다.")
            except ValueError:
                errors.append("생년은 4자리 숫자여야 합니다.")

        # 3. 연락처 — 자동 포맷팅 후 형식 검증
        phone_normalized = normalize_phone(phone.strip())
        if phone_normalized and not validate_phone(phone_normalized):
            errors.append("연락처 형식이 올바르지 않습니다. (예: 010-1234-5678 또는 01012345678)")

        # 4. 이메일 형식
        if email.strip() and not validate_email(email.strip()):
            errors.append("이메일 형식이 올바르지 않습니다.")

        # 5. 탈퇴일 — 정규화 후 형식 검증
        ld_str = normalize_date(leave_date_str.strip())
        if ld_str and not validate_date(ld_str):
            errors.append("탈퇴일 형식이 올바르지 않습니다. (YYYY-MM-DD)")

        # 6. 휴면 기간 검증 + 정규화 + 겹침 검사
        clean_dorm_list = []
        for i, p in enumerate(dorm_list):
            s = normalize_date((p.get("start") or "").strip())
            e = normalize_date((p.get("end") or "").strip())
            if not s and not e: continue
            if not s:
                errors.append(f"휴면 기간 #{i+1}: 시작일이 비어있습니다."); continue
            if not validate_date(s):
                errors.append(f"휴면 기간 #{i+1}: 시작일 형식 오류 (예: 20260101)"); continue
            if e and not validate_date(e):
                errors.append(f"휴면 기간 #{i+1}: 종료일 형식 오류 (예: 20260101)"); continue
            if e and s > e:
                errors.append(f"휴면 기간 #{i+1}: 종료일이 시작일보다 빠를 수 없습니다."); continue
            clean_dorm_list.append({"start": s, "end": e})

        # 12번: 시작일 오름차순 자동 정렬
        clean_dorm_list.sort(key=lambda p: p["start"])

        # 11번: 겹침 검사
        if not errors and clean_dorm_list:
            overlap_err = check_dormant_overlap(clean_dorm_list)
            if overlap_err:
                errors.append(f"휴면 기간 겹침 오류: {overlap_err}")

        dorm_str = format_dormant_periods(clean_dorm_list)

        # 7. 중복 검사
        if not errors:
            exclude_id = existing["id"] if existing else None
            dup_msg = check_duplicate(df, name, phone_normalized, cafe_id, exclude_id=exclude_id)
            if dup_msg:
                errors.append(f"⚠️ {dup_msg}")

        if errors:
            for e in errors:
                st.error(f"❗ {e}")
        else:
            # ── 카테고리 자동 결정 ──
            had_dormant = bool(dorm_str)
            has_ongoing = had_dormant and any(not p["end"] for p in clean_dorm_list)
            if ld_str:
                final_cat = "탈퇴"
            elif has_ongoing:
                final_cat = "휴면"
            elif had_dormant and cat == "휴면":
                final_cat = "정회원"
            else:
                final_cat = cat

            action_detail = (f"{'신규등록' if not existing else '수정'} → "
                             f"카테고리:{final_cat}, 연락처:{phone_normalized}")
            row_data = {
                "id":             existing["id"] if existing else next_id(df),
                "category":       final_cat,
                "name":           name.strip(),
                "cafe_id":        cafe_id.strip(),
                "birth_year":     by or "",
                "gender":         gender,
                "phone":          phone_normalized,
                "join_date":      join_date.strftime("%Y-%m-%d") if join_date else "",
                "dormant_period": dorm_str,
                "leave_date":     ld_str,
                "email":          email.strip(),
                "application":    "" if application=="—" else application,
                "region":         region.strip(),
                "memo":           memo.strip(),
                "deleted_at":     "",
            }
            with st.spinner("구글 시트에 저장 중…"):
                save_row(df, row_data, is_new=(existing is None), action_detail=action_detail)

            st.success(f"✅ {'수정' if existing else '등록'} 완료! — {final_cat} {name.strip()}")
            _cleanup_dormant_session()
            st.session_state.open_dialog    = None
            st.session_state.edit_target    = None
            st.cache_data.clear()
            st.rerun()

# ─────────────────────────────────────────────────────────
#  헤더
# ─────────────────────────────────────────────────────────


def render_roster_page():
    """회원명부 페이지 — 로그인/비로그인 분기"""
    _logged_in  = is_logged_in()
    _is_admin   = is_admin()
    _app_user   = get_app_user()

    st.markdown("""
    <div class="app-header">
      <span style="font-size:36px">🎾</span>
      <div><h1>테라클럽 회원 명부</h1>
      <p>TELA CLUB Member Roster · Google Sheets 연동</p></div>
    </div>""", unsafe_allow_html=True)

    # ── 비로그인: 본인 인증 후 제한 열람 모드 ───────────────────
    if not _logged_in:
        # 본인 인증 상태 확인
        _authed_guest = st.session_state.get("guest_auth_ok", False)

        if not _authed_guest:
            st.warning("🔒 회원명부는 등록된 본인 이름과 연락처를 입력해야 열람할 수 있습니다.\n\n운영진이 아닌 일반 회원은 제한된 열람만 가능합니다.")
            st.markdown("**본인 확인**")
            _gc1, _gc2 = st.columns(2)
            _auth_name  = _gc1.text_input("이름", placeholder="홍길동", key="guest_auth_name")
            _auth_phone = _gc2.text_input("연락처", placeholder="010-1234-5678", key="guest_auth_phone")
            if st.button("확인", type="primary", key="guest_auth_btn"):
                try:
                    _df_auth = load_df(include_deleted=False)
                    # 연락처 정규화 (숫자만 비교)
                    import re as _re
                    _phone_clean = _re.sub(r'\D', '', _auth_phone.strip())
                    _match = _df_auth[
                        (_df_auth["name"].str.strip() == _auth_name.strip()) &
                        (_df_auth["phone"].astype(str).apply(lambda x: _re.sub(r'\D','',x)) == _phone_clean) &
                        (_df_auth["category"] != "탈퇴")
                    ]
                    if not _match.empty:
                        st.session_state["guest_auth_ok"] = True
                        st.rerun()
                    else:
                        st.error("❌ 일치하는 회원 정보가 없습니다.")
                except Exception as _e:
                    st.error(f"오류: {_e}")
            return

        # 인증 성공 → 제한 열람
        _auth_col, _logout_col = st.columns([5, 1])
        _auth_col.info(f"🔍 제한 열람 모드 — 구분 · 성명 · 연락처만 표시됩니다.")
        if _logout_col.button("🔒 나가기", key="guest_auth_logout"):
            st.session_state["guest_auth_ok"] = False
            st.rerun()

        with st.spinner("📡 구글 시트에서 데이터 불러오는 중…"):
            try:
                df_guest = load_df(include_deleted=False)
            except Exception as e:
                st.error(f"⚠️ Google Sheets 연결 오류: {e}")
                st.stop()

        # 탈퇴 제외
        OFFICER_CATS_G = ["마스터","고문","회장","총무","경기이사","홍보이사"]
        CATEGORIES_SHOW = OFFICER_CATS_G + ["정회원","휴면"]
        df_guest = df_guest[df_guest["category"].isin(CATEGORIES_SHOW)].copy()
        if df_guest.empty:
            st.info("표시할 회원이 없습니다.")
            return

        # ── 정렬: 운영진 상단 고정 → 정회원 이름순 → 휴면 하단 ──
        # [수정] 기존엔 _sort_key 함수를 정의해 sort_values를 먼저 호출한 뒤
        # _sort 컬럼으로 다시 정렬했음. 첫 호출은 결과가 즉시 덮어써지므로
        # 무의미했고 _sort_key 함수도 사용되지 않았음. _sort 컬럼 정렬만 남김.
        df_guest["_sort"] = df_guest.apply(lambda r: (
            0 if r["category"] in OFFICER_CATS_G else (1 if r["category"] == "정회원" else 2),
            OFFICER_CATS_G.index(r["category"]) if r["category"] in OFFICER_CATS_G else 0,
            str(r.get("name",""))
        ), axis=1)
        df_guest = df_guest.sort_values("_sort").drop(columns=["_sort"]).reset_index(drop=True)

        st.caption(f"총 **{len(df_guest)}명**")
        gq = st.text_input("🔍 이름 검색", placeholder="이름 입력", key="guest_search",
                           label_visibility="collapsed")
        if gq.strip():
            df_guest = df_guest[df_guest["name"].str.contains(gq.strip(), na=False)]

        _g_fs = "font-size:12px"
        hc = st.columns([1, 2, 2])
        hc[0].markdown(f"<div style='{_g_fs};font-weight:700;color:#6b7280;border-bottom:2px solid #e2e8f0;padding:4px 0'>구분</div>", unsafe_allow_html=True)
        hc[1].markdown(f"<div style='{_g_fs};font-weight:700;color:#6b7280;border-bottom:2px solid #e2e8f0;padding:4px 0'>성명</div>", unsafe_allow_html=True)
        hc[2].markdown(f"<div style='{_g_fs};font-weight:700;color:#6b7280;border-bottom:2px solid #e2e8f0;padding:4px 0'>연락처</div>", unsafe_allow_html=True)

        _prev_group = None
        for _, row in df_guest.iterrows():
            cat = row.get("category","")
            # 그룹 구분선
            cur_group = 0 if cat in OFFICER_CATS_G else (1 if cat == "정회원" else 2)
            if _prev_group is not None and cur_group != _prev_group:
                st.markdown("<div style='border-bottom:2px solid #e2e8f0;margin:4px 0'></div>",
                            unsafe_allow_html=True)
            _prev_group = cur_group

            rc = st.columns([1, 2, 2])
            rc[0].markdown(f"<div style='padding:5px 0'>{badge(cat)}</div>", unsafe_allow_html=True)
            rc[1].markdown(f"<div style='{_g_fs};padding:7px 0;font-weight:600;color:#1a2e4a'>{row.get('name','')}</div>", unsafe_allow_html=True)
            phone_val = str(row.get('phone','') or '—')
            rc[2].markdown(f"<div style='{_g_fs};padding:7px 0;color:#374151'>{phone_val}</div>", unsafe_allow_html=True)
            st.markdown("<div style='border-bottom:1px solid #f1f5f9'></div>", unsafe_allow_html=True)
        return

    # ── 로그인 상태: 기존 roster_app 기능 전체 ────────────────
    # 관리자 인증 상태 표시 (roster 내부 admin_authed와 별개)
    if st.session_state.get("admin_authed") and st.session_state.get("auth_time"):
        elapsed_min = int((datetime.now() - st.session_state.auth_time).total_seconds() / 60)
        remain_min  = SESSION_TIMEOUT_MIN - elapsed_min
        auth_col1, auth_col2 = st.columns([6, 1])
        with auth_col1:
            st.markdown(
                f"<div style='background:#d1fae5;border-left:4px solid #10b981;"
                f"padding:6px 12px;border-radius:6px;font-size:12px;color:#065f46;font-weight:600;'>"
                f"🔓 관리자 인증됨 — 잔여 {remain_min}분"
                f"</div>", unsafe_allow_html=True)
        with auth_col2:
            if st.button("🔒 잠금", use_container_width=True, key="admin_logout_roster"):
                st.session_state.admin_authed = False
                st.session_state.auth_time    = None
                st.rerun()

    # ── 계정 관리 탭 (관리자 전용) ──────────────────────────
    if _is_admin:
        with st.expander("🔑 계정 관리 (관리자 전용)", expanded=False):
            all_users = user_load_all()
            st.markdown(f"**등록 계정 ({len(all_users)}개)**")

            # 계정 목록
            _admin_id = st.secrets.get("ADMIN_ID", "admin")
            for uid, uinfo in list(all_users.items()):
                ucols = st.columns([2, 2, 1, 1, 1])
                ucols[0].write(uid)
                ucols[1].write(f"{uinfo.get('name','')} ({'관리자' if uinfo.get('role')=='admin' else '회원'})")
                # 비밀번호 변경
                new_pw_key = f"chpw_{uid}"
                new_pw = ucols[2].text_input("새PW", key=new_pw_key,
                                              label_visibility="collapsed",
                                              placeholder="새 PW")
                if ucols[3].button("변경", key=f"chpwbtn_{uid}"):
                    if new_pw.strip():
                        user_change_pw(uid, new_pw.strip())
                        st.success(f"'{uid}' 비밀번호 변경 완료")
                        st.rerun()
                    else:
                        st.warning("새 비밀번호를 입력하세요.")
                # 삭제 (관리자 본인 제외)
                if uid != _admin_id:
                    if ucols[4].button("🗑", key=f"delusr_{uid}"):
                        user_delete(uid)
                        st.rerun()
                else:
                    ucols[4].caption("주계정")

            st.markdown("---")
            st.markdown("**신규 계정 추가**")
            nc1, nc2, nc3, nc4, nc5 = st.columns([2, 2, 2, 1, 1])
            new_uid   = nc1.text_input("아이디", key="new_uid", label_visibility="collapsed", placeholder="아이디")
            new_upw   = nc2.text_input("비밀번호", key="new_upw", label_visibility="collapsed", placeholder="비밀번호")
            new_uname = nc3.text_input("이름", key="new_uname", label_visibility="collapsed", placeholder="이름")
            new_urole = nc4.selectbox("권한", ["회원", "관리자"], key="new_urole", label_visibility="collapsed")
            if nc5.button("➕ 추가", key="add_user_btn"):
                if new_uid.strip() and new_upw.strip() and new_uname.strip():
                    role_val = "admin" if new_urole == "관리자" else "member"
                    ok = user_add(new_uid.strip(), new_upw.strip(), role_val, new_uname.strip())
                    if ok:
                        st.success(f"계정 '{new_uid}' 추가 완료")
                        st.rerun()
                    else:
                        st.error(f"이미 존재하는 아이디입니다: {new_uid}")
                else:
                    st.warning("아이디, 비밀번호, 이름을 모두 입력해주세요.")

    # ─────────────────────────────────────────────────────────
    #  데이터 로드
    # ─────────────────────────────────────────────────────────
    with st.spinner("📡 구글 시트에서 데이터 불러오는 중…"):
        try:
            df = load_df(include_deleted=False)
        except Exception as e:
            st.error(f"⚠️ Google Sheets 연결 오류: {e}")
            st.stop()

    # ── 알림 배지 계산 (로그인 시에만 표시) ──────────────────
    anniversary_members  = get_this_month_birthdays(df)
    long_dormant_members = get_long_dormant_members(df, months=3)

    # 알림 배지 표시
    notif_parts = []
    if anniversary_members:
        notif_parts.append(f"🎾 이번 달 입회기념 **{len(anniversary_members)}명**")
    if long_dormant_members:
        notif_parts.append(f"⚠️ 장기 휴면(3개월↑) **{len(long_dormant_members)}명** — 탈퇴 검토 필요")
    if notif_parts:
        st.markdown(
            "<div style='background:#fef3c7;border-left:4px solid #f59e0b;"
            "padding:8px 14px;border-radius:8px;font-size:13px;color:#92400e;margin-bottom:8px;'>"
            + " &nbsp;|&nbsp; ".join(notif_parts) +
            "</div>", unsafe_allow_html=True)
        if anniversary_members or long_dormant_members:
            with st.expander("📋 알림 상세 보기", expanded=False):
                if anniversary_members:
                    st.markdown("**🎾 이번 달 입회 기념일**")
                    for m in anniversary_members:
                        yr = f"{m['years']}주년" if m['years'] > 0 else "첫해"
                        st.markdown(f"- {m['name']} ({m['category']}) — 입회일 {m['join_date'][:10]} ({yr})")
                if long_dormant_members:
                    st.markdown("**⚠️ 장기 휴면 탈퇴 검토 대상**")
                    for m in long_dormant_members:
                        st.markdown(f"- {m['name']} — 휴면 시작 {m['start']} ({m['days']}일 경과)")
    
    # ─────────────────────────────────────────────────────────
    #  다이얼로그 라우터 — 렌더링 최상단에서 처리
    #  ⚠️ 중요: 다이얼로그를 띄운 직후 open_dialog 상태를 비워서
    #         다음 rerun(다른 위젯 조작 등)에서 다이얼로그가 재팝업되지 않도록 함
    # ─────────────────────────────────────────────────────────
    od = st.session_state.open_dialog
    et = st.session_state.edit_target
    
    # 다이얼로그 호출 전에 상태를 "소비"(consume) — 한 번만 표시되도록
    if od is not None:
        st.session_state.open_dialog = None
    
    if od == "add":
        dialog_form(df, existing=None)
    
    elif od == "detail" and et:
        # 읽기 전용 상세 보기 — 비밀번호 불필요
        detail_row = None
        if not df.empty:
            rows = df[df["id"] == et["id"]]
            if not rows.empty:
                detail_row = rows.iloc[0].to_dict()
        if detail_row:
            dialog_detail(detail_row)
    
    elif od == "edit" and et and st.session_state.admin_authed:
        existing_row = None
        if not df.empty:
            rows = df[df["id"] == et["id"]]
            if not rows.empty:
                existing_row = rows.iloc[0].to_dict()
        dialog_form(df, existing=existing_row)
    
    elif od == "confirm_delete" and et:
        dialog_confirm_delete(et)
    
    elif od == "delete_confirm" and et and st.session_state.admin_authed:
        dialog_delete(et)
    
    elif od in ("pw_edit", "pw_delete") and et:
        dialog_pw(et)
    
    # ─────────────────────────────────────────────────────────
    #  통계 카드
    # ─────────────────────────────────────────────────────────
    def stat_counts(cats):
        sub = df[df["category"].isin(cats)] if not df.empty else pd.DataFrame()
        m   = len(sub[sub["gender"]=="남"]) if not sub.empty else 0
        f   = len(sub[sub["gender"]=="여"]) if not sub.empty else 0
        return m, f
    
    groups = [
        ("운영진", OFFICER_CATS, "officer"),
        ("정회원", ["정회원"],   "regular"),
        ("휴면",   ["휴면"],     "dormant"),
        ("탈퇴",   ["탈퇴"],     "left"),
    ]
    sc = st.columns(len(groups)+1)
    for col,(label,cats,cls) in zip(sc[:-1],groups):
        m,f = stat_counts(cats)
        col.markdown(f'<div class="stat-card {cls}"><div class="stat-label">{label}</div>'
                     f'<div class="stat-num">{m+f}</div><div class="stat-sub">남 {m} · 여 {f}</div></div>',
                     unsafe_allow_html=True)
    # 총 회원수 = 탈퇴 제외
    active_df = df[df["category"] != "탈퇴"] if not df.empty else df
    tm = len(active_df[active_df["gender"]=="남"]) if not active_df.empty else 0
    tf = len(active_df[active_df["gender"]=="여"]) if not active_df.empty else 0
    sc[-1].markdown(f'<div class="stat-card total"><div class="stat-label white">총 회원수</div>'
                    f'<div class="stat-num white">{tm+tf}</div><div class="stat-sub white">남 {tm} · 여 {tf}</div></div>',
                    unsafe_allow_html=True)
    st.markdown("<br>", unsafe_allow_html=True)
    
    # ─────────────────────────────────────────────────────────
    #  툴바
    # ─────────────────────────────────────────────────────────
    c_s, c_sb, c_dl, c_add = st.columns([4, 0.8, 1.0, 1.2])
    with c_s:
        search_q = st.text_input("검색", value=st.session_state.search_q,
            placeholder="이름 / 카페ID / 연락처 입력 후 검색 버튼 클릭",
            label_visibility="collapsed")
        st.session_state.search_q = search_q
    with c_sb:
        if st.button("🔍 검색", use_container_width=True):
            st.session_state.search_active = search_q.strip()
            st.rerun()
    with c_dl:
        # CSV 백업 다운로드 (BOM 추가로 엑셀 한글 깨짐 방지)
        csv_data = df.to_csv(index=False).encode("utf-8-sig") if not df.empty else "".encode("utf-8-sig")
        today_str = date.today().strftime("%Y%m%d")
        st.download_button(
            "📥 백업",
            data=csv_data,
            file_name=f"tela_club_backup_{today_str}.csv",
            mime="text/csv",
            use_container_width=True,
            help="현재 명부 전체를 CSV로 다운로드 (엑셀 호환)"
        )
    with c_add:
        if _is_admin:
            if st.button("＋ 회원 등록", type="primary", use_container_width=True):
                st.session_state.open_dialog  = "add"
                st.session_state.edit_target  = None
                st.rerun()
        else:
            st.caption("등록: 관리자만 가능")

    if not search_q.strip():
        st.session_state.search_active = ""

    # ── 카테고리 필터 + 리그 필터 ────────────────────────────
    FILTER_OPTIONS = ["전체","운영진","정회원","휴면"] + (["탈퇴"] if _is_admin else [])
    if st.session_state.filter_cat not in FILTER_OPTIONS:
        st.session_state.filter_cat = "전체"

    # 카테고리 + 리그 필터를 한 줄에
    f_col1, f_sep, f_col2 = st.columns([3, 0.1, 2])
    with f_col1:
        filter_cat = st.radio("필터", FILTER_OPTIONS,
            index=FILTER_OPTIONS.index(st.session_state.filter_cat),
            horizontal=True, label_visibility="collapsed",
            key="filter_radio")
    with f_sep:
        st.markdown("<div style='border-left:2px solid #e2e8f0;height:36px;margin-top:4px'></div>",
                    unsafe_allow_html=True)
    with f_col2:
        LEAGUE_FILTER_OPTIONS = ["전체 리그"] + LEAGUE_NAMES[:3]
        if "filter_league" not in st.session_state:
            st.session_state["filter_league"] = "전체 리그"
        filter_league = st.radio("리그 필터", LEAGUE_FILTER_OPTIONS,
            index=LEAGUE_FILTER_OPTIONS.index(
                st.session_state["filter_league"]
                if st.session_state["filter_league"] in LEAGUE_FILTER_OPTIONS
                else "전체 리그"
            ),
            horizontal=True, label_visibility="collapsed",
            key="filter_league_radio"
        )
        st.session_state["filter_league"] = filter_league
    
    # ── 카테고리 변경 감지: 필터가 바뀌면 정렬 위젯도 자동 초기화 ──
    # 사용자 의도에 따라:
    #  - 정회원: 입회일순 (오래된 회원이 위)
    #  - 휴면: 최근 휴면일순 (최근 휴면 시작이 위)
    #  - 탈퇴: 최근 탈퇴일순 (최근 탈퇴가 위)
    #  - 전체/운영진: 구분순 (기존 기본값)
    SORT_DEFAULT_BY_FILTER = {
        "전체":   "구분순",
        "운영진": "구분순",
        "정회원": "입회일순(빠른)",
        "휴면":   "휴면 시작일순(최근)",
        "탈퇴":   "탈퇴일순(최근)",
    }
    # 필터가 바뀌면 sort_select의 세션값을 해당 기본값으로 교체
    if st.session_state.filter_cat != filter_cat:
        st.session_state["sort_select"] = SORT_DEFAULT_BY_FILTER[filter_cat]
        st.session_state.filter_cat = filter_cat
    
    SORT_OPTIONS = [
        "No.순", "구분순", "이름순",
        "입회일순(빠른)", "입회일순(최근)",
        "휴면 시작일순(최근)",
        "탈퇴일순(최근)",
        "생년순", "성별순"
    ]
    # 세션에 sort_select가 없거나 옵션에 없으면 현재 필터의 기본값으로
    if "sort_select" not in st.session_state or st.session_state.get("sort_select") not in SORT_OPTIONS:
        st.session_state["sort_select"] = SORT_DEFAULT_BY_FILTER.get(filter_cat, "구분순")
    
    sc2,_ = st.columns([1,5])
    with sc2:
        sort_by = st.selectbox("정렬", SORT_OPTIONS,
            key="sort_select",
            label_visibility="collapsed")
    
    # 휴지통 토글 (관리자 인증 시에만 표시)
    if st.session_state.admin_authed:
        trash_col, _ = st.columns([2, 8])
        with trash_col:
            trash_label = "📦 휴지통 닫기" if st.session_state.show_trash else "🗑️ 휴지통 보기"
            if st.button(trash_label, use_container_width=True, key="toggle_trash"):
                st.session_state.show_trash = not st.session_state.show_trash
                st.rerun()
    
    # ── 휴지통 뷰 ─────────────────────────────────────────────
    if st.session_state.show_trash and st.session_state.admin_authed:
        st.markdown("---")
        st.markdown("### 🗑️ 휴지통")
        st.caption(f"삭제 후 {TRASH_DAYS}일이 지난 항목은 자동으로 영구 삭제됩니다.")
        try:
            df_all     = load_df(include_deleted=True)
            df_trash   = df_all[df_all["deleted_at"].astype(str).str.strip() != ""].copy()
            today_dt   = datetime.now()
            # 90일 초과 자동 영구 삭제
            for _, trow in df_trash.iterrows():
                try:
                    del_dt = datetime.strptime(str(trow["deleted_at"])[:19], "%Y-%m-%d %H:%M:%S")
                    if (today_dt - del_dt).days >= TRASH_DAYS:
                        hard_delete_row(trow["id"], trow["name"])
                        st.cache_data.clear()
                except Exception:
                    pass
            # 재로드 후 표시
            df_all   = load_df(include_deleted=True)
            df_trash = df_all[df_all["deleted_at"].astype(str).str.strip() != ""].copy()
        except Exception as e:
            df_trash = pd.DataFrame()
            st.warning(f"휴지통 로드 실패: {e}")
    
        if df_trash.empty:
            st.info("휴지통이 비어있습니다.")
        else:
            for _, trow in df_trash.iterrows():
                del_dt_str = str(trow.get("deleted_at",""))[:16]
                try:
                    del_dt   = datetime.strptime(del_dt_str[:19], "%Y-%m-%d %H:%M:%S")
                    days_ago = (today_dt - del_dt).days
                    remain   = TRASH_DAYS - days_ago
                except Exception:
                    remain = TRASH_DAYS
                tc1, tc2, tc3, tc4 = st.columns([3, 2, 2, 2])
                tc1.markdown(f"**{trow['name']}** ({trow['category']})")
                tc2.caption(f"삭제일: {del_dt_str}")
                tc3.caption(f"영구삭제까지 {remain}일")
                with tc4:
                    rcol1, rcol2 = st.columns(2)
                    if rcol1.button("↩️ 복구", key=f"restore_{trow['id']}", use_container_width=True):
                        restore_row(trow["id"], trow["name"])
                        st.cache_data.clear()
                        st.rerun()
                    if rcol2.button("💀 영구삭제", key=f"hardel_{trow['id']}", use_container_width=True):
                        hard_delete_row(trow["id"], trow["name"])
                        st.cache_data.clear()
                        st.rerun()
        st.markdown("---")
    
    # ─────────────────────────────────────────────────────────
    #  필터링 & 정렬
    # ─────────────────────────────────────────────────────────
    def _latest_dormant_start(s):
        """휴면 기간 문자열에서 가장 최근의 시작일을 반환 (정렬용)"""
        periods = parse_dormant_periods(s) if s else []
        if not periods: return ""
        # 시작일 기준 최대값 반환
        return max((p["start"] for p in periods if p.get("start")), default="")
    
    def apply_filters(data):
        if data.empty: return data
        # 비관리자: 탈퇴 항목 원천 제외
        if not _is_admin:
            data = data[data["category"] != "탈퇴"]
        if filter_cat == "운영진":
            data = data[data["category"].isin(OFFICER_CATS)]
        elif filter_cat == "탈퇴":
            data = data[data["category"] == "탈퇴"]
        elif filter_cat == "전체":
            data = data[data["category"] != "탈퇴"]
        else:
            data = data[data["category"] == filter_cat]
        # 리그 필터
        _fl = st.session_state.get("filter_league", "전체 리그")
        if _fl and _fl != "전체 리그":
            data = data[data["league"].astype(str).str.strip() == _fl]
        q = st.session_state.search_active.lower()
        if q:
            mask = (data["name"].str.lower().str.contains(q,na=False) |
                    data["cafe_id"].astype(str).str.lower().str.contains(q,na=False) |
                    data["phone"].astype(str).str.contains(q,na=False))
            data = data[mask]
    
        if sort_by == "구분순":
            data = data.copy()
            data["_o"] = data["category"].map(CAT_ORDER).fillna(99)
            data = data.sort_values("_o").drop(columns="_o")
        elif sort_by == "이름순":
            data = data.sort_values("name")
        elif sort_by == "입회일순(빠른)":
            # 오래된 입회일이 위 (오름차순). 빈 값은 맨 뒤로.
            data = data.sort_values("join_date", ascending=True, na_position="last")
        elif sort_by == "입회일순(최근)":
            data = data.sort_values("join_date", ascending=False, na_position="last")
        elif sort_by == "휴면 시작일순(최근)":
            data = data.copy()
            data["_dorm_latest"] = data["dormant_period"].apply(_latest_dormant_start)
            data = data.sort_values("_dorm_latest", ascending=False, na_position="last").drop(columns="_dorm_latest")
        elif sort_by == "탈퇴일순(최근)":
            data = data.sort_values("leave_date", ascending=False, na_position="last")
        elif sort_by == "생년순":
            data = data.sort_values("birth_year")
        elif sort_by == "성별순":
            data = data.sort_values("gender")
        else:  # No.순
            data = data.sort_values("id")
        return data.reset_index(drop=True)
    
    view_df = apply_filters(df.copy())
    st.caption(f"검색 결과 **{len(view_df)}명** / 전체 {len(df)}명")
    
    # ─────────────────────────────────────────────────────────
    #  선택 액션 툴바 (선택된 회원이 있을 때만 표시)
    # ─────────────────────────────────────────────────────────
    sel_ids   = st.session_state.bulk_selected
    sel_count = len(sel_ids)
    
    if sel_count > 0:
        sel_names = [str(r["name"]) for _, r in df[df["id"].isin(sel_ids)].iterrows()]
        st.markdown(
            f"<div style='background:#eff6ff;border:1px solid #bfdbfe;border-radius:8px;"
            f"padding:10px 16px;margin-bottom:8px;font-size:13px;color:#1e40af;font-weight:600'>"
            f"☑️ {sel_count}명 선택됨: {', '.join(sel_names[:7])}"
            f"{'…' if len(sel_names)>7 else ''}</div>",
            unsafe_allow_html=True)
    
        ba1, ba2, ba3 = st.columns([2, 1.5, 1.5])
    
        # 일괄 카테고리 변경
        with ba1:
            bac1, bac2 = st.columns([2, 1])
            with bac1:
                new_cat = st.selectbox("카테고리 변경", ["—"] + CATEGORIES,
                                       key="bulk_cat_sel", label_visibility="collapsed")
            with bac2:
                if st.button("✅ 적용", key="bulk_cat_apply", use_container_width=True):
                    if new_cat != "—" and st.session_state.admin_authed:
                        with st.spinner(f"{sel_count}명 카테고리 변경 중…"):
                            for _, r in df[df["id"].isin(sel_ids)].iterrows():
                                row_d = r.to_dict()
                                row_d["category"] = new_cat
                                save_row(df, row_d, is_new=False,
                                         action_detail=f"벌크 카테고리 변경 → {new_cat}")
                        # 체크박스 세션 초기화
                        for sid in list(sel_ids):
                            k = f"chk_{sid}"
                            if k in st.session_state:
                                del st.session_state[k]
                        st.session_state.bulk_selected = set()
                        st.cache_data.clear()
                        st.rerun()
                    elif not st.session_state.admin_authed:
                        st.warning("관리자 인증이 필요합니다.")
    
        # 연락처 추출
        with ba2:
            sel_rows   = df[df["id"].isin(sel_ids)].copy()
            lines      = ["구분\t성명\t연락처"]
            for _, r in sel_rows.iterrows():
                lines.append(f"{str(r.get('category','') or '').strip()}\t"
                             f"{str(r.get('name','') or '').strip()}\t"
                             f"{str(r.get('phone','') or '').strip()}")
            phone_text = "\n".join(lines)
            today_str  = date.today().strftime("%Y%m%d")
            st.download_button(
                "📋 연락처 추출",
                data=phone_text.encode("utf-8-sig"),
                file_name=f"contacts_{today_str}.txt",
                mime="text/plain",
                use_container_width=True,
                key="bulk_phone_dl"
            )
    
        # 선택 해제
        with ba3:
            if st.button("✕ 선택 해제", key="bulk_none", use_container_width=True):
                for sid in list(sel_ids):
                    k = f"chk_{sid}"
                    if k in st.session_state:
                        del st.session_state[k]
                st.session_state.bulk_selected  = set()
                st.session_state.bulk_all_flag  = False
                if "hdr_chk_all" in st.session_state:
                    del st.session_state["hdr_chk_all"]
                st.rerun()
    
    # ─────────────────────────────────────────────────────────
    #  회원 목록 테이블 (체크박스 항상 표시)
    # ─────────────────────────────────────────────────────────
    CW  = [0.22, 0.28, 0.55, 0.65, 0.82, 0.85, 0.46, 0.38, 0.95, 0.72, 0.75, 1.0, 0.72, 0.68, 1.1, 0.85]
    HDR = ["☑","No.","구분","리그","성명","카페ID","생년","성별","연락처","거주지","입회일","휴면기간","탈퇴일","입회신청서","메모","관리"]
    
    if view_df.empty:
        st.info("🎾 해당 조건의 회원이 없습니다.")
    else:
        hcols = st.columns(CW)
        # ── 헤더 첫 번째 열: 전체 선택/해제 체크박스 ──
        all_ids_in_view = set(view_df["id"].tolist())
        all_selected    = bool(all_ids_in_view) and all_ids_in_view.issubset(st.session_state.bulk_selected)
    
        def _toggle_all():
            if st.session_state.get("hdr_chk_all", False):
                # 전체 선택: 현재 뷰의 모든 ID 추가
                st.session_state.bulk_selected.update(all_ids_in_view)
                for rid in all_ids_in_view:
                    st.session_state[f"chk_{rid}"] = True
            else:
                # 전체 해제: 현재 뷰의 모든 ID 제거
                st.session_state.bulk_selected -= all_ids_in_view
                for rid in all_ids_in_view:
                    st.session_state[f"chk_{rid}"] = False
    
        if "hdr_chk_all" not in st.session_state:
            st.session_state["hdr_chk_all"] = all_selected
    
        with hcols[0]:
            st.checkbox("", key="hdr_chk_all",
                        label_visibility="collapsed",
                        on_change=_toggle_all,
                        help="전체 선택 / 해제")
    
        for hc, txt in zip(hcols[1:], HDR[1:]):
            hc.markdown(f"<div style='{RS_FS};font-weight:700;color:#6b7280;"
                        f"padding:6px 0 4px;border-bottom:2px solid #e2e8f0'>{txt}</div>",
                        unsafe_allow_html=True)
    
        for idx, row in view_df.iterrows():
            rc = st.columns(CW)
            col_offset = 0
    
            # ── 행 체크박스 (항상 표시, 콜백 방식) ──
            row_id  = int(row["id"])
            chk_key = f"chk_{row_id}"
    
            def _toggle_chk(rid=row_id, k=chk_key):
                if st.session_state.get(k, False):
                    st.session_state.bulk_selected.add(rid)
                else:
                    st.session_state.bulk_selected.discard(rid)
    
            if chk_key not in st.session_state:
                st.session_state[chk_key] = row_id in st.session_state.bulk_selected
    
            with rc[0]:
                st.checkbox("", key=chk_key,
                            label_visibility="collapsed",
                            on_change=_toggle_chk)
            col_offset = 1
    
            memo_txt  = str(row.get("memo","") or "").strip()
            memo_disp = (memo_txt[:20]+"…") if len(memo_txt)>20 else (memo_txt or "—")
            by_val    = int(row["birth_year"]) if pd.notna(row.get("birth_year")) and row.get("birth_year") else "—"
            app_val   = str(row.get("application","") or "—")
            app_color = {"Yes":"#16a34a","No":"#dc2626"}.get(app_val,"#9ca3af")
    
            rc[col_offset+0].markdown(cell(idx+1,"#9ca3af"), unsafe_allow_html=True)
            rc[col_offset+1].markdown(f"<div style='padding:5px 0'>{badge(row.get('category',''))}</div>", unsafe_allow_html=True)
            # ── 리그 셀 (표시만, 랜덤매치에서 수정) ──
            lg_val = str(row.get('league','') or '').strip()
            _lg_color = LEAGUE_COLORS[LEAGUE_NAMES.index(lg_val)] if lg_val in LEAGUE_NAMES else "#9ca3af"
            rc[col_offset+2].markdown(
                f"<div style='padding:5px 0;{RS_FS};color:{_lg_color};font-weight:700'>{lg_val or '—'}</div>",
                unsafe_allow_html=True
            )
            rc[col_offset+3].markdown(cell(row.get('name',''),"#1a2e4a","font-weight:600"), unsafe_allow_html=True)
            rc[col_offset+4].markdown(cell(row.get('cafe_id','') or '—',"#6b7280"), unsafe_allow_html=True)
            rc[col_offset+5].markdown(cell(by_val), unsafe_allow_html=True)
            rc[col_offset+6].markdown(f"<div style='padding:5px 0'>{gender_html(str(row.get('gender','')))}</div>", unsafe_allow_html=True)
            rc[col_offset+7].markdown(cell(row.get('phone','') or '—'), unsafe_allow_html=True)
            rc[col_offset+8].markdown(cell(row.get('region','') or '—',"#374151"), unsafe_allow_html=True)
            rc[col_offset+9].markdown(cell(row.get('join_date','') or '—',"#6b7280"), unsafe_allow_html=True)
    
            # 휴면 기간 요약
            dorm_raw = str(row.get('dormant_period','') or '').strip()
            if dorm_raw:
                dorm_list_disp  = parse_dormant_periods(dorm_raw)
                dorm_cnt        = len(dorm_list_disp)
                ongoing_periods = [p for p in dorm_list_disp if not p["end"]]
                if ongoing_periods:
                    dorm_disp = f"{ongoing_periods[-1]['start']}~"
                elif dorm_cnt == 1:
                    dorm_disp = f"{dorm_list_disp[0]['start']}~{dorm_list_disp[0]['end']}"
                else:
                    last = dorm_list_disp[-1]
                    dorm_disp = f"{last['start']}~{last['end']} 외 {dorm_cnt-1}건"
            else:
                dorm_disp = "—"
            rc[col_offset+10].markdown(
                f"<div style='padding:7px 0;{RS_FS};color:#ca8a04' title='{dorm_raw}'>{dorm_disp}</div>",
                unsafe_allow_html=True)
    
            # [버그수정] 기존 코드는 col_offset+10에 휴면기간을 그린 직후 같은 인덱스에
            # 탈퇴일을 다시 그려서 휴면기간이 덮어써졌고, 이후 컬럼이 1칸씩 밀려
            # 관리 버튼이 표시되지 않았음. 인덱스를 +1씩 보정.
            rc[col_offset+11].markdown(cell(row.get('leave_date','') or '—',"#dc2626"), unsafe_allow_html=True)
            rc[col_offset+12].markdown(
                f"<div style='padding:5px 0'><span style='{RS_FS};font-weight:700;color:{app_color}'>{app_val}</span></div>",
                unsafe_allow_html=True)
            rc[col_offset+13].markdown(
                f"<div style='padding:7px 0;{RS_FS};color:#4b5563' title='{memo_txt}'>{memo_disp}</div>",
                unsafe_allow_html=True)
    
            # [다이어트] 행별 inline CSS 제거 - 전역 와일드카드 사용
            with rc[col_offset+14]:
                btn_c1, btn_c2 = st.columns([1, 1])
                with btn_c1:
                    if st.button("열람", key=f"detail_{row['id']}", use_container_width=True,
                                 help="상세 보기 (비밀번호 불필요)"):
                        st.session_state.open_dialog = "detail"
                        st.session_state.edit_target = {"id": int(row["id"]), "name": row["name"], "type": "detail"}
                        st.rerun()
                with btn_c2:
                    if _is_admin:
                        if st.button("수정", key=f"edit_{row['id']}", use_container_width=True,
                                     help="수정 (관리자 인증 필요)"):
                            target = {"type":"edit","id":int(row["id"]),"name":row["name"]}
                            st.session_state.edit_target = target
                            if st.session_state.admin_authed:
                                st.session_state.open_dialog = "edit"
                            else:
                                st.session_state.open_dialog = "pw_edit"
                            st.rerun()
    
            st.markdown("<div style='border-bottom:1px solid #f1f5f9'></div>", unsafe_allow_html=True)



# [다이어트] 사이드바 + 매치카드 CSS는 전역 CSS 블록에 통합됨

# ── 네비게이션 ───────────────────────────────────────────────
st.sidebar.markdown("## 🎾 TELA TENNIS CLUB")
st.sidebar.caption("v5.3")
st.sidebar.markdown("---")

# ── 최초 관리자 계정 보장 ────────────────────────────────────
user_ensure_admin()

# ── 앱 세션 초기화 ───────────────────────────────────────────
if "app_user" not in st.session_state:
    st.session_state["app_user"] = None

# ── 사이드바 로그인/로그아웃 UI ──────────────────────────────
_u = get_app_user()
if _u:
    role_label = "🔑 관리자" if _u["role"] == "admin" else "👤 회원"
    st.sidebar.markdown(
        f'<div style="background:#d1fae5;border-radius:8px;padding:8px 10px;'
        f'font-size:0.8rem;color:#065f46;font-weight:700;margin-bottom:6px;">'
        f'{role_label} · {_u["name"]} ({_u["id"]})</div>',
        unsafe_allow_html=True
    )
    if st.sidebar.button("🔒 로그아웃", key="app_logout", use_container_width=True):
        try:
            _old_tok = st.query_params.get("t", "")
            if _old_tok:
                _session_delete(_old_tok)
            st.query_params.clear()
        except Exception:
            pass
        st.session_state["app_user"] = None
        _cookie_clear_user()
        st.rerun()
else:
    with st.sidebar.expander("🔐 로그인", expanded=True):
        def _on_login_enter():
            _id = st.session_state.get("login_id", "")
            _pw = st.session_state.get("login_pw", "")
            if _id and _pw:
                _r = user_authenticate(_id, _pw)
                if _r:
                    st.session_state["app_user"] = _r
                    _cookie_save_user(_r)
                    _tok = _session_save(_r)
                    try:
                        st.query_params["t"] = _tok
                    except Exception:
                        pass
                else:
                    st.session_state["_login_fail"] = True

        _lid = st.text_input("아이디", key="login_id", placeholder="ID 입력")
        _lpw = st.text_input("비밀번호", type="password", key="login_pw",
                              placeholder="입력 후 엔터", on_change=_on_login_enter)

        # on_change로 로그인 성공 시 rerun
        if st.session_state.get("app_user"):
            st.rerun()

        if st.button("로그인", key="login_btn", type="primary", use_container_width=True):
            _result = user_authenticate(_lid, _lpw)
            if _result:
                st.session_state["app_user"] = _result
                _cookie_save_user(_result)
                _tok = _session_save(_result)
                try:
                    st.query_params["t"] = _tok
                except Exception:
                    pass
                st.rerun()
            else:
                st.session_state["_login_fail"] = True

        if st.session_state.pop("_login_fail", False):
            st.error("아이디 또는 비밀번호가 틀렸습니다.")
        st.caption("비회원은 회원명부 열람(제한)만 가능합니다.")

st.sidebar.markdown("---")
page = st.sidebar.radio("메뉴", ["🏆 기록실", "📊 스코어보드", "📋 대진표생성", "👥 회원명부"],
                         index=0, label_visibility="collapsed")
st.sidebar.markdown("---")


# ============================================================
# 페이지 A: 점수판
# ============================================================

if page == "📊 스코어보드":

    st.markdown("## 🎾 TELA 클럽 랭킹리그 스코어보드")

    today_str  = date.today().strftime("%Y-%m-%d")
    saved_keys = shelf_list_dates()

    sb_mode = st.radio("모드", ["저장된 스코어보드 불러오기", "새 스코어보드 (날짜+번호 입력)"],
                       index=0, horizontal=True, label_visibility="collapsed")
    if sb_mode == "새 스코어보드 (날짜+번호 입력)":
        sb_date = st.text_input("날짜 (YYYY-MM-DD)", value=today_str, key="sb_date_inp")
        sb_num  = st.text_input("일련번호 (예: 001)", value="001", key="sb_num_inp")
        selected_key = f"{_date_with_weekday(sb_date)}_{sb_num}"
    else:
        if saved_keys:
            selected_key = st.selectbox("저장된 스코어보드 선택", saved_keys)
        else:
            st.info("저장된 데이터가 없습니다.")
            selected_key = f"{_date_with_weekday(today_str)}_001"

    st.caption(f"현재 키: **{selected_key}**")

    if st.session_state.get("sb_key") != selected_key:
        st.session_state["sb_key"] = selected_key
        loaded = shelf_load(selected_key)
        if loaded:
            st.session_state["sb_schedule"] = deserialize_schedule(loaded["schedule"])
            # [수정1] shelf에서 점수 정확히 복원 (새로고침 후 데이터 유지)
            st.session_state["sb_scores"]   = loaded.get("scores", {})
            # locked 상태도 복원
            for k, v in loaded.get("scores", {}).items():
                if v:
                    st.session_state[f"locked_{k}"] = True
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
        st.info("👈 **📋 대진표생성**에서 같은 날짜+일련번호로 대진표를 생성하거나, 저장된 키를 선택해주세요.")
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

    # ── 점수 입력 UI ─────────────────────────────────────────
    _can_edit = is_admin()   # 수정1: 저장·수정은 관리자만

    def _save_score(idx, s1, s2):
        """점수 저장: shelf 즉시 저장 → 구글시트 기록은 백그라운드 처리"""
        scores[str(idx)] = {"score1": int(s1), "score2": int(s2)}
        st.session_state["sb_scores"] = scores
        st.session_state[f"locked_{idx}"] = True
        st.session_state.pop(f"editing_{idx}", None)
        _cur_schedule = st.session_state.get("sb_schedule")
        if _cur_schedule:
            _prev = shelf_load(selected_key) or {}
            _ifr  = _prev.get("is_fully_random", False)
            shelf_save(selected_key, serialize_schedule(_cur_schedule), scores, _ifr)
            import threading as _threading
            def _bg_commit(_dk, _sched, _sc):
                try:
                    records_commit(_dk, _sched, dict(_sc))
                except Exception:
                    pass
            _threading.Thread(
                target=_bg_commit,
                args=(selected_key, list(_cur_schedule), dict(scores)),
                daemon=True
            ).start()

    def _unlock_score(idx):
        st.session_state[f"locked_{idx}"] = False
        st.session_state[f"editing_{idx}"] = True

    def _cancel_edit(idx, s1_orig, s2_orig):
        st.session_state[f"locked_{idx}"] = True
        st.session_state.pop(f"editing_{idx}", None)

    # 모바일 레이아웃 CSS — columns 줄바꿈 완전 방지
    st.markdown("""
<style>
/* 모든 horizontal block: 줄바꿈 없이 한 줄 고정 */
[data-testid="stHorizontalBlock"] {
    flex-wrap: nowrap !important;
    gap: 4px !important;
    align-items: center !important;
}
[data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {
    min-width: 0 !important;
    flex-shrink: 1 !important;
    padding-left: 2px !important;
    padding-right: 2px !important;
}
/* number_input 최소 너비 제거 */
[data-testid="stNumberInput"] { min-width: 0 !important; }
[data-testid="stNumberInput"] > div { min-width: 0 !important; }
[data-testid="stNumberInput"] input {
    min-width: 0 !important;
    font-size: 0.9rem !important;
    padding: 4px 2px !important;
    text-align: center !important;
}
/* +/- 버튼 */
[data-testid="stNumberInput"] button {
    min-width: 0 !important;
    padding: 2px !important;
    width: 24px !important;
}
/* 저장/취소/수정 버튼 텍스트 줄바꿈 방지 */
[data-testid="stHorizontalBlock"] [data-testid="stBaseButton-primary"] p,
[data-testid="stHorizontalBlock"] [data-testid="stBaseButton-secondary"] p {
    white-space: nowrap !important;
    font-size: 0.78rem !important;
}
/* 체크박스 여백 최소화 */
[data-testid="stCheckbox"] { margin: 0 0 4px 0 !important; }
[data-testid="stCheckbox"] label p { font-size: 0.72rem !important; }
/* 경기카드 하단 Streamlit 여백 제거 */
.stMarkdown { margin-bottom: 0 !important; }
/* +/- 버튼 배경색 강조 (수정3) */
[data-testid="stNumberInput"] button[aria-label="increment"],
[data-testid="stNumberInput"] button[aria-label="decrement"] {
    background-color: #e8f5e9 !important;
    color: #2e7d32 !important;
    border: 1px solid #a5d6a7 !important;
    border-radius: 4px !important;
    font-weight: 700 !important;
}
[data-testid="stNumberInput"] button[aria-label="increment"]:hover,
[data-testid="stNumberInput"] button[aria-label="decrement"]:hover {
    background-color: #c8e6c9 !important;
}
[data-testid="stNumberInput"] input {
    background-color: #f9fbe7 !important;
    border: 1px solid #c5e1a5 !important;
    border-radius: 4px !important;
}
</style>""", unsafe_allow_html=True)

    league_list  = list(dict.fromkeys(m["league"] for m in schedule))
    lg_color_map = {lg: get_league_color(lg) for lg in league_list}

    for rnd in rounds:
        rnd_label = rnd.replace("(이벤트)", "") + (" ⭐" if "이벤트" in rnd else "")
        st.markdown(
            f'<div style="background:#1a1a2e;color:#fff;font-weight:700;font-size:0.9rem;'
            f'text-align:center;padding:8px 4px;border-radius:6px;margin:10px 0 6px;'
            f'letter-spacing:1px;">{rnd_label}</div>', unsafe_allow_html=True)

        rnd_matches = [m for m in schedule if m["round"] == rnd]
        rnd_leagues = list(dict.fromkeys(m["league"] for m in rnd_matches))

        for lg in rnd_leagues:
            lc = lg_color_map.get(lg, "#555")
            st.markdown(
                f'<div style="color:{lc};font-weight:700;font-size:0.75rem;'
                f'border-left:3px solid {lc};padding-left:6px;margin:4px 0 3px;">{lg}</div>',
                unsafe_allow_html=True)

            for idx, match in [(i, m) for i, m in enumerate(schedule)
                               if m["round"] == rnd and m["league"] == lg]:
                sc        = scores.get(str(idx), {})
                is_locked = st.session_state.get(f"locked_{idx}", bool(sc))
                s1_saved  = sc.get("score1", 0)
                s2_saved  = sc.get("score2", 0)
                is_dup_saved = sc.get("is_dup", False)

                t1a = display_name(match["team1"][0]); t1b = display_name(match["team1"][1])
                t2a = display_name(match["team2"][0]); t2b = display_name(match["team2"][1])
                match_type = match["type"]

                t1_win    = is_locked and s1_saved > s2_saved
                t2_win    = is_locked and s2_saved > s1_saved
                win_style = "color:#b71c1c;font-weight:900;"
                nrm_style = "color:#333;font-weight:600;"

                border_color = "#a5d6a7" if is_locked else lc
                bg_color     = "#f0fff0" if is_locked else "#fff"
                dup_badge    = ' <span style="font-size:0.65rem;color:#e65100;background:#fff3e0;padding:1px 5px;border-radius:8px;">중복</span>' if is_dup_saved else ""

                _p1 = win_style if t1_win else nrm_style
                _p2 = win_style if t2_win else nrm_style
                st.markdown(
                    f'<div style="border:1px solid {border_color};border-left:4px solid {lc};'
                    f'border-radius:6px;background:{bg_color};padding:6px 8px;margin-bottom:2px;">'
                    f'<div style="display:flex;align-items:center;gap:2px;">'
                    f'<div style="flex:1;min-width:0;overflow:hidden;">'
                    f'<div style="{_p1}font-size:0.78rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{t1a}</div>'
                    f'<div style="{_p1}font-size:0.78rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{t1b}</div>'
                    f'</div>'
                    f'<div style="flex:0 0 56px;text-align:center;font-size:0.92rem;font-weight:800;color:#333;white-space:nowrap;">'
                    f'{s1_saved if is_locked else "·"}&nbsp;vs&nbsp;{s2_saved if is_locked else "·"}'
                    f'</div>'
                    f'<div style="flex:1;min-width:0;overflow:hidden;text-align:right;">'
                    f'<div style="{_p2}font-size:0.78rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{t2a}</div>'
                    f'<div style="{_p2}font-size:0.78rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{t2b}</div>'
                    f'</div>'
                    f'</div>'
                    f'<div style="font-size:0.58rem;color:#aaa;text-align:right;margin-top:1px;">'
                    f'{match_type}{dup_badge}{" ✅저장완료" if is_locked else ""}'
                    f'</div>'
                    f'</div>', unsafe_allow_html=True)

                if is_locked:
                    if _can_edit:
                        # 저장 완료 + 관리자: [점수1] [수정] [점수2]
                        _lk1, _lk2, _lk3 = st.columns([4, 3, 4])
                        _lk1.markdown(
                            f'<div style="text-align:center;padding:5px 0;background:#ebebeb;'
                            f'border-radius:5px;font-size:0.95rem;font-weight:700;">{s1_saved}</div>',
                            unsafe_allow_html=True)
                        _lk3.markdown(
                            f'<div style="text-align:center;padding:5px 0;background:#ebebeb;'
                            f'border-radius:5px;font-size:0.95rem;font-weight:700;">{s2_saved}</div>',
                            unsafe_allow_html=True)
                        if _lk2.button("✏️수정", key=f"edit_{idx}", use_container_width=True):
                            _unlock_score(idx)
                            st.rerun()
                    # 비관리자: 아무것도 표시 안 함

                else:
                    if _can_edit:
                        # 입력 모드: [점수1] [💾] [✖] [점수2] — 한 줄 4컬럼
                        _ic1, _ic2, _ic3, _ic4 = st.columns([4, 2, 2, 4])
                        s1_new = _ic1.number_input(
                            f"팀1_{idx}", min_value=0, max_value=9,
                            value=s1_saved, step=1, key=f"s1_{idx}",
                            label_visibility="collapsed"
                        )
                        s2_new = _ic4.number_input(
                            f"팀2_{idx}", min_value=0, max_value=9,
                            value=s2_saved, step=1, key=f"s2_{idx}",
                            label_visibility="collapsed"
                        )
                        if _ic2.button("💾", key=f"save_{idx}", type="primary",
                                       use_container_width=True, help="저장"):
                            _save_score(idx,
                                        st.session_state.get(f"s1_{idx}", s1_new),
                                        st.session_state.get(f"s2_{idx}", s2_new))
                            st.rerun()
                        if _ic3.button("✖", key=f"cancel_{idx}",
                                       use_container_width=True, help="취소"):
                            if bool(sc):
                                _cancel_edit(idx, s1_saved, s2_saved)
                            else:
                                st.session_state.pop(f"editing_{idx}", None)
                            st.rerun()

    st.markdown("---")
    # 점수 전체 초기화: 관리자만
    if is_admin():
        _rc1, _rc2 = st.columns([2, 8])
        if _rc1.button("🔄 점수 전체 초기화", type="secondary"):
            for i in range(len(schedule)):
                st.session_state.pop(f"locked_{i}", None)
            st.session_state["sb_scores"] = {}
            st.rerun()
        _rc2.caption("⚠️ 초기화 시 저장된 점수가 모두 삭제됩니다.")

    # ── 관리자 전용: 기록실 재집계 ────────────────────────────
    # 기존 시트 데이터(잘못된 집계)를 현재 코드 기준으로 덮어써서 정정
    if is_admin():
        _ra1, _ra2 = st.columns([3, 7])
        if _ra1.button("🔁 기록실 재집계 (관리자)", type="secondary",
                       help="이 점수판의 기록실 데이터를 현재 점수 기준으로 다시 계산해 구글시트에 덮어씁니다."):
            _reagg_scores = st.session_state.get("sb_scores", {})
            if not _reagg_scores:
                _sd = shelf_load(selected_key)
                if _sd: _reagg_scores = _sd.get("scores", {})
            if _reagg_scores:
                with st.spinner("기록실 재집계 중…"):
                    try:
                        records_commit(selected_key, schedule, _reagg_scores)
                        st.cache_data.clear()
                        st.success(f"✅ '{selected_key}' 기록실 재집계 완료! 중복 선수 데이터가 제거되었습니다.")
                    except Exception as _e:
                        st.error(f"재집계 오류: {_e}")
            else:
                st.warning("저장된 점수가 없습니다. 먼저 점수를 저장해주세요.")

    # 선수별 현황
    st.markdown("### 📈 선수별 현황")
    _current_scores = st.session_state.get("sb_scores", {})
    if not _current_scores:
        _shelf_data = shelf_load(selected_key)
        if _shelf_data:
            _current_scores = _shelf_data.get("scores", {})
            st.session_state["sb_scores"] = _current_scores
    df_sb = compute_scoreboard_stats(schedule, _current_scores)
    if df_sb.empty:
        st.info("점수를 저장하면 통계가 표시됩니다.")
    else:
        for league in df_sb["리그"].unique():
            df_lg = df_sb[df_sb["리그"]==league].drop(columns=["리그"]).reset_index(drop=True)
            if df_lg.empty: continue
            lg_color = get_league_color(league)
            st.markdown(
                f'<div style="color:{lg_color};font-weight:700;border-bottom:2px solid {lg_color};'
                f'padding-bottom:4px;margin:16px 0 8px 0;">🎾 {league} 선수별 현황</div>',
                unsafe_allow_html=True)
            st.dataframe(df_lg, use_container_width=True, hide_index=True)


# ============================================================
# 페이지 B: 랜덤페어
# ============================================================

elif page == "📋 대진표생성":

    if not is_logged_in():
        st.warning("🔐 대진표생성은 로그인 후 이용할 수 있습니다. 사이드바에서 로그인해주세요.")
        st.stop()

    # ── [1] 페어링 방식 ──────────────────────────────────────
    st.sidebar.markdown("### 🎯 페어링 방식")
    pairing_mode = st.sidebar.radio(
        "페어링 방식 선택",
        ["🔴 완전 랜덤페어", "🔵 조건부 랜덤페어"],
        index=0, label_visibility="collapsed",
    )
    IS_FULLY_RANDOM = (pairing_mode == "🔴 완전 랜덤페어")
    if IS_FULLY_RANDOM:
        st.sidebar.info("**완전 랜덤페어**\n\n완전 무작위\n\n✅ 남성 vs 여성 대결 제한")
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

    # ── [3] 참가자 선택 ──────────────────────────────────────
    # 사이드바: 팝업 버튼 + 선택 현황만 표시
    if st.sidebar.button("👥 참가자 선택", type="primary",
                         use_container_width=True, key="open_member_popup"):
        st.session_state["member_popup_open"] = True
        st.session_state["member_popup_just_opened"] = True

    # 현재 선택 현황 요약
    _match_df_sidebar = st.session_state.get("match_df_cache", pd.DataFrame())
    _total_sel = 0
    for i, lg in enumerate(active_leagues):
        lc = LEAGUE_COLORS[i]
        if not _match_df_sidebar.empty:
            lg_rows = _match_df_sidebar[_match_df_sidebar["league"] == lg]
            sel_cnt = sum(1 for _, r in lg_rows.iterrows()
                          if st.session_state.get(f"mchk_{lg}_{r['id']}", False))
        else:
            sel_cnt = 0
        # 게스트 수 포함
        guest_cnt = sum(1 for g in guest_load() if g["league"] == lg)
        total_lg  = sel_cnt + guest_cnt
        _total_sel += total_lg
        disp = f"{lg}: {sel_cnt}명"
        if guest_cnt: disp += f" + 게스트{guest_cnt}"
        st.sidebar.markdown(
            f'<span style="color:{lc};font-size:0.78rem;">{disp}</span>',
            unsafe_allow_html=True
        )
    if _total_sel == 0:
        st.sidebar.caption("⚠️ 참가자를 선택해주세요")

    # member_selected 수집 — 영구 저장소(selected_members/selected_guests) 기반
    member_selected = {}
    _match_df_cache = st.session_state.get("match_df_cache", pd.DataFrame())

    # 캐시가 비어있으면 자동 로드 (불러오기/rerun 후에도 선택 유지)
    if _match_df_cache.empty and st.session_state.get("selected_members"):
        try:
            _match_df_cache = load_df_for_match()
            st.session_state["match_df_cache"] = _match_df_cache
        except Exception:
            pass

    if not _match_df_cache.empty and "league" in _match_df_cache.columns:
        _match_df_cache = _match_df_cache.copy()
        _match_df_cache["league"] = _match_df_cache["league"].astype(str).str.strip()
        st.session_state["match_df_cache"] = _match_df_cache

    _sel_store  = st.session_state.get("selected_members", {})
    _gsel_store = st.session_state.get("selected_guests",  {})

    # 디버그 정보
    _dbg = {
        "cache_empty": _match_df_cache.empty,
        "cache_size":  len(_match_df_cache) if not _match_df_cache.empty else 0,
        "unique_leagues_in_cache": [],
        "active_leagues": active_leagues,
        "store_size":  len(_sel_store),
        "store_true":  [k for k, v in _sel_store.items() if v][:5],
    }
    if not _match_df_cache.empty:
        _dbg["unique_leagues_in_cache"] = sorted(set(_match_df_cache["league"].astype(str)))

    for i, lg in enumerate(active_leagues):
        pfx = active_prefixes[i]
        selected = []
        if not _match_df_cache.empty:
            lg_rows = _match_df_cache[_match_df_cache["league"] == lg]
            for _, r in lg_rows.iterrows():
                rid = int(r['id'])
                if _sel_store.get(f"{lg}_{rid}", False):
                    g = "M" if str(r.get("gender","")).strip() in ("남","M") else "W"
                    selected.append(f"{pfx}{g}{r['name']}")
        for gm in guest_load():
            if gm["league"] == lg:
                if _gsel_store.get(f"{lg}_{gm['name']}", False):
                    selected.append(gm["code"])
        member_selected[lg] = selected

    st.session_state["_debug_member_select"] = _dbg

    # input_mode 고정 (회원 선택 모드)
    input_mode    = "👥 회원 선택 (명부 연동)"
    custom_input  = None
    league_counts = None

    # ══════════════════════════════════════════════════════════
    # 참가자 선택 팝업 — 구글 시트 회원명부 직접 연동
    # ══════════════════════════════════════════════════════════
    @st.dialog("👥 참가자 선택 (회원명부 연동)", width="large")
    def _member_select_popup():
        # 호출 즉시 플래그 해제 — rerun 시 팝업 재오픈 방지
        st.session_state["member_popup_open"] = False
        if "match_df_cache" not in st.session_state or st.session_state.get("member_popup_just_opened"):
            with st.spinner("📡 회원명부 불러오는 중…"):
                try:
                    match_df = load_df_for_match()
                    st.session_state["match_df_cache"] = match_df
                    st.session_state["member_popup_just_opened"] = False
                except Exception as e:
                    st.error(f"회원명부 로드 실패: {e}")
                    return
        else:
            match_df = st.session_state["match_df_cache"]

        if match_df is None or (hasattr(match_df, 'empty') and match_df.empty):
            st.info("회원명부에 데이터가 없습니다.")
            if st.button("닫기"): st.session_state["member_popup_open"] = False; st.rerun()
            return

        unassigned_df = match_df[match_df["league"].astype(str).str.strip() == ""]
        if not unassigned_df.empty:
            st.warning(f"⚠️ 리그 미배정 회원 {len(unassigned_df)}명이 있습니다.")

        tabs_p = st.tabs([f"{lg}" for lg in active_leagues])
        for i, (tab_p, lg) in enumerate(zip(tabs_p, active_leagues)):
            with tab_p:
                lc    = LEAGUE_COLORS[i]
                # strip 처리로 league 비교 정확하게
                lg_df = match_df[match_df["league"].astype(str).str.strip() == lg].copy()
                lg_df = lg_df.sort_values("name").reset_index(drop=True)

                _guests_lg = [g for g in guest_load() if g["league"] == lg]

                if lg_df.empty and not _guests_lg:
                    st.info(f"{lg}에 배정된 회원이 없습니다.")
                    continue

                def _is_dorm(r):
                    if r.get("category") == "휴면": return True
                    _dp = str(r.get("dormant_period","")).strip()
                    if not _dp: return False
                    if "~" in _dp:
                        _e = _dp.split("~")[-1].strip()
                        if not _e: return True
                        try: return date.fromisoformat(_e[:10]) >= date.today()
                        except ValueError: pass
                    return True

                normal_df  = lg_df[~lg_df.apply(_is_dorm, axis=1)]
                dormant_df = lg_df[lg_df.apply(_is_dorm, axis=1)]

                # 선택 현황 카운트 (영구 저장소 기준)
                _sel_store_view = st.session_state.get("selected_members", {})
                _gsel_store_view = st.session_state.get("selected_guests", {})
                sel_cnt   = sum(1 for _, r in lg_df.iterrows()
                                if _sel_store_view.get(f"{lg}_{int(r['id'])}", False))
                g_sel_cnt = sum(1 for gm in _guests_lg
                                if _gsel_store_view.get(f"{lg}_{gm['name']}", False))
                total_sel = sel_cnt + g_sel_cnt

                col_sa, col_sd, col_cnt = st.columns([1, 1, 3])
                if col_sa.button("✅ 전체선택", key=f"popup_sa_{lg}"):
                    _sel_store = st.session_state.setdefault("selected_members", {})
                    for _, r in normal_df.iterrows():
                        _sel_store[f"{lg}_{int(r['id'])}"] = True
                    _gsel_store = st.session_state.setdefault("selected_guests", {})
                    for gm in _guests_lg:
                        _gsel_store[f"{lg}_{gm['name']}"] = True
                    # 위젯 키도 동기화
                    for _, r in normal_df.iterrows():
                        st.session_state[f"mchk_{lg}_{int(r['id'])}"] = True
                    for gm in _guests_lg:
                        st.session_state[f"gchk_{lg}_{gm['name']}"] = True
                    st.rerun()
                if col_sd.button("⬜ 전체해제", key=f"popup_sd_{lg}"):
                    _sel_store = st.session_state.setdefault("selected_members", {})
                    for _, r in lg_df.iterrows():
                        _sel_store[f"{lg}_{int(r['id'])}"] = False
                    _gsel_store = st.session_state.setdefault("selected_guests", {})
                    for gm in _guests_lg:
                        _gsel_store[f"{lg}_{gm['name']}"] = False
                    for _, r in lg_df.iterrows():
                        st.session_state[f"mchk_{lg}_{int(r['id'])}"] = False
                    for gm in _guests_lg:
                        st.session_state[f"gchk_{lg}_{gm['name']}"] = False
                    st.rerun()

                col_cnt.markdown(
                    f'<div style="padding-top:6px;color:{lc};font-weight:700;">'
                    f'{total_sel}명 선택</div>', unsafe_allow_html=True
                )

                # ── 정상 회원 체크박스 ────────────────────────
                if not normal_df.empty:
                    cols_per_row = 3
                    for row_chunk in [normal_df.iloc[j:j+cols_per_row]
                                      for j in range(0, len(normal_df), cols_per_row)]:
                        rcols = st.columns(cols_per_row)
                        for k, (_, r) in enumerate(row_chunk.iterrows()):
                            g_label = "남" if str(r.get("gender","")).strip() in ("남","M") else "여"
                            rid     = int(r['id'])
                            wkey    = f"mchk_{lg}_{rid}"
                            store_k = f"{lg}_{rid}"
                            # 영구 저장소에서 기본값 가져오기
                            _sel_store = st.session_state.setdefault("selected_members", {})
                            if wkey not in st.session_state:
                                st.session_state[wkey] = _sel_store.get(store_k, False)
                            checked = rcols[k].checkbox(f"{r['name']} ({g_label})", key=wkey)
                            # 위젯 값을 영구 저장소에 반영
                            _sel_store[store_k] = checked

                # ── 게스트 체크박스 ──────────────────────────
                if _guests_lg:
                    st.markdown(
                        "<div style='margin-top:10px;font-size:0.8rem;color:#1565C0;"
                        "font-weight:700;'>👤 게스트</div>",
                        unsafe_allow_html=True
                    )
                    gcols_per_row = 3
                    for gi in range(0, len(_guests_lg), gcols_per_row):
                        gcols = st.columns(gcols_per_row)
                        for gk, gm in enumerate(_guests_lg[gi:gi+gcols_per_row]):
                            g_gender   = "남" if gm["gender"] == "M" else "여"
                            gkey       = f"gchk_{lg}_{gm['name']}"
                            g_store_k  = f"{lg}_{gm['name']}"
                            _gsel_store = st.session_state.setdefault("selected_guests", {})
                            if gkey not in st.session_state:
                                st.session_state[gkey] = _gsel_store.get(g_store_k, False)
                            g_checked = gcols[gk].checkbox(
                                f"⭐ {gm['name']} ({g_gender})", key=gkey
                            )
                            _gsel_store[g_store_k] = g_checked

                # ── 휴면 회원 (하단, 체크박스 없음) ──────────
                if not dormant_df.empty:
                    st.markdown(
                        f"<div style='margin-top:12px;padding:6px 10px;background:#fff8e1;"
                        f"border-radius:6px;border-left:3px solid #FF8F00;font-size:0.8rem;"
                        f"color:#6d4c41;font-weight:700;'>💤 휴면 ({len(dormant_df)}명) — 참가 제외</div>",
                        unsafe_allow_html=True
                    )
                    for row_chunk in [dormant_df.iloc[j:j+3] for j in range(0, len(dormant_df), 3)]:
                        rcols = st.columns(3)
                        for k, (_, r) in enumerate(row_chunk.iterrows()):
                            g_label = "남" if str(r.get("gender","")).strip() in ("남","M") else "여"
                            rcols[k].markdown(
                                f'<div style="color:#aaa;font-size:0.85rem;padding:4px 0;">'
                                f'💤 {r["name"]} ({g_label})</div>',
                                unsafe_allow_html=True
                            )

        if st.button("✔️ 확인", type="primary", use_container_width=True):
            st.session_state["member_popup_open"] = False
            st.rerun()

    if st.session_state.get("member_popup_open", False):
        _member_select_popup()

    # ── [4] 날짜·번호 (가로 배치) + 비밀번호 + 생성 버튼 ────
    st.sidebar.markdown("---")
    _d1, _d2 = st.sidebar.columns([3, 2])
    rp_date = _d1.text_input("📅 날짜", value=date.today().strftime("%Y-%m-%d"), key="rp_date")
    rp_num  = _d2.text_input("번호", value="001", key="rp_num", placeholder="001")
    rp_key  = f"{_date_with_weekday(rp_date)}_{rp_num}"
    st.sidebar.caption(f"저장키: {rp_key}")

    # ── 저장된 대진표 불러오기 / 삭제 (사이드바) ──────────────
    _saved_keys = shelf_list_dates()
    if _saved_keys:
        st.sidebar.markdown("---")
        _sel_key = st.sidebar.selectbox(
            "📂 저장된 대진표", _saved_keys, key="sb_load_key_sidebar"
        )
        _lb1, _lb2 = st.sidebar.columns([1, 1])

        # 불러오기
        if _lb1.button("📥 Load", key="sb_load_btn", type="primary", use_container_width=True):
            _loaded = shelf_load(_sel_key)
            if _loaded:
                _loaded_sched = deserialize_schedule(_loaded["schedule"])
                _loaded_is_fully_random = _loaded.get("is_fully_random", False)
                _loaded_stats: Dict[str, PlayerStats] = {}
                for _m in _loaded_sched:
                    update_stats(_loaded_stats, _m["team1"], _m["team2"],
                                 _m["type"].replace("(중복)",""), _m["round"], _m["league"])
                st.session_state.update({
                    "rp_schedule":     _loaded_sched,
                    "stats":           _loaded_stats,
                    "last_gen_params": {
                        "league_players":  {},
                        "is_fully_random": _loaded_is_fully_random,
                        "league_configs":  {},
                        "use_seed":        False,
                        "seed_val":        None,
                        "rp_key":          _sel_key,
                    },
                })
                st.sidebar.success(f"✅ '{_sel_key}' 불러옴")
                st.rerun()
            else:
                st.sidebar.error("불러오기 실패")

        # 삭제 (2단계 확인)
        if _lb2.button("🗑️ Del", key="sb_delete_btn", use_container_width=True):
            st.session_state["_sb_confirm_del"] = _sel_key

        if st.session_state.get("_sb_confirm_del") == _sel_key:
            st.sidebar.warning(f"'{_sel_key}' 삭제할까요?")
            _dc1, _dc2 = st.sidebar.columns([1, 1])
            if _dc1.button("✅ 확인", key="sb_del_confirm", use_container_width=True):
                shelf_delete(_sel_key)
                st.session_state.pop("_sb_confirm_del", None)
                if st.session_state.get("last_gen_params", {}).get("rp_key") == _sel_key:
                    st.session_state.pop("rp_schedule", None)
                    st.session_state.pop("stats", None)
                    st.session_state.pop("last_gen_params", None)
                st.sidebar.success(f"🗑️ '{_sel_key}' 삭제됨")
                st.rerun()
            if _dc2.button("✕ 취소", key="sb_del_cancel", use_container_width=True):
                st.session_state.pop("_sb_confirm_del", None)
                st.rerun()

    # ── [5] 대진표 생성 ────────────────────────────────────────
    st.sidebar.markdown("---")
    _admin_ok = is_admin()
    pw_ok = _admin_ok   # 호환성 유지
    if not _admin_ok:
        st.sidebar.warning("🔒 대진표 생성은 관리자만 가능합니다.")

    generate_btn = st.sidebar.button(
        "🎾 대진표 생성", type="primary", use_container_width=True,
        disabled=not _admin_ok
    )

    # ── 메인 타이틀 ─────────────────────────────────────────
    mode_badge = "🔴 완전 랜덤" if IS_FULLY_RANDOM else "🔵 조건부"
    league_badge = " · ".join(active_leagues)
    st.title("🎾 TELA CLUB 대진표 생성")
    st.caption(f"{mode_badge} &nbsp;|&nbsp; {league_badge} &nbsp;|&nbsp; 최소 3경기 / 최대 4경기")

    # ── 결과 고정 (시드) — 본문 배치 ──────────────────────────
    _sc1, _sc2 = st.columns([1, 4])
    use_seed = _sc1.checkbox("🔒 결과 고정 (시드)", value=False, key="use_seed_main")
    seed_val = None
    if use_seed:
        seed_val = _sc2.number_input("시드 번호", min_value=0, max_value=9999,
                                      value=42, step=1, key="seed_val_main",
                                      label_visibility="collapsed")

    # ── 🔍 디버그 패널 ───────────────────────────────────────
    with st.expander("🔍 진단 정보 (4명 미만 오류 디버깅)", expanded=False):
        _dbg = st.session_state.get("_debug_member_select", {})
        st.write(f"- 캐시 비어있음: `{_dbg.get('cache_empty', '?')}`")
        st.write(f"- 캐시 총 회원수: `{_dbg.get('cache_size', 0)}`")
        st.write(f"- 캐시 내 리그값: `{_dbg.get('unique_leagues_in_cache', [])}`")
        st.write(f"- 활성 리그: `{_dbg.get('active_leagues', [])}`")
        st.write(f"- 영구 저장소 크기: `{_dbg.get('store_size', 0)}`")
        st.write(f"- 영구 저장소 True 키 (처음 5개): `{_dbg.get('store_true', [])}`")
        for lg in active_leagues:
            cnt = len(member_selected.get(lg, []))
            st.write(f"- {lg}: **{cnt}명** → {member_selected.get(lg, [])[:3]}")

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
            if input_mode == "👥 회원 선택 (명부 연동)":
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

            # ── 디버그: 실제 선택 현황 확인용 ──────────────────
            with st.expander("🔍 선택 현황 (디버그)", expanded=False):
                for lg, pl in league_players.items():
                    st.write(f"**{lg}**: {len(pl)}명 → {pl[:5]}")

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
        shelf_save(rp_key_run, serialize_schedule(schedule), {}, IS_FULLY_RANDOM_run)
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

        # [다이어트] dn(code) 제거 + 매치 DataFrame/렌더링 공통 헬퍼화

        df_matches = _build_matches_df(schedule)
        df_full    = stats_to_df(stats)
        df_display = df_full.drop(columns=["_코드"])

        tab1, tab2, tab3 = st.tabs(["📋 대진표", "📊 출전 현황", "🔍 검증 리포트"])

        with tab1:
            _render_match_table(df_matches, active_lgs, seed_label, mode_label, league_players)

            # ── [수정3] 관리자 전용: 페어 수동 조정 ──────────────
            if is_admin():
                with st.expander("🔧 관리자: 페어 수동 조정", expanded=False):
                    st.caption("경기 번호를 선택하고 선수를 재배정할 수 있습니다.")
                    _adj_matches = st.session_state.get("schedule", schedule)
                    _match_labels = []
                    for _mi, _m in enumerate(_adj_matches):
                        t1a = display_name(_m["team1"][0]); t1b = display_name(_m["team1"][1])
                        t2a = display_name(_m["team2"][0]); t2b = display_name(_m["team2"][1])
                        _match_labels.append(f"#{_mi+1} [{_m['round']}·{_m['league']}] {t1a}/{t1b} vs {t2a}/{t2b}")

                    _sel_mi = st.selectbox("조정할 경기 선택", range(len(_adj_matches)),
                                           format_func=lambda i: _match_labels[i],
                                           key="adj_match_sel")
                    _sel_match = _adj_matches[_sel_mi]
                    st.markdown(f"**현재:** {_match_labels[_sel_mi]}")

                    # 해당 리그 전체 선수 목록 수집
                    _lg_name   = _sel_match["league"]
                    _lg_players_all = []
                    for _m2 in _adj_matches:
                        if _m2["league"] == _lg_name:
                            for _p in list(_m2["team1"]) + list(_m2["team2"]):
                                _pb = base_name(_p)
                                if _pb not in [base_name(x) for x in _lg_players_all]:
                                    _lg_players_all.append(_p)

                    _player_labels = {base_name(p): display_name(p) for p in _lg_players_all}

                    st.markdown("**팀 재구성**")
                    _pkeys = list(_player_labels.keys())
                    def _pidx(code, _pk=_pkeys):
                        k = base_name(code)
                        return _pk.index(k) if k in _pk else 0
                    _cc1, _cc2 = st.columns(2)
                    with _cc1:
                        st.markdown("**팀1**")
                        _t1_new_a = st.selectbox("팀1 선수A", _pkeys,
                                                  format_func=lambda k: _player_labels.get(k,k),
                                                  index=_pidx(_sel_match["team1"][0]),
                                                  key=f"adj_t1a_{_sel_mi}")
                        _t1_new_b = st.selectbox("팀1 선수B", _pkeys,
                                                  format_func=lambda k: _player_labels.get(k,k),
                                                  index=_pidx(_sel_match["team1"][1]),
                                                  key=f"adj_t1b_{_sel_mi}")
                    with _cc2:
                        st.markdown("**팀2**")
                        _t2_new_a = st.selectbox("팀2 선수A", _pkeys,
                                                  format_func=lambda k: _player_labels.get(k,k),
                                                  index=_pidx(_sel_match["team2"][0]),
                                                  key=f"adj_t2a_{_sel_mi}")
                        _t2_new_b = st.selectbox("팀2 선수B", _pkeys,
                                                  format_func=lambda k: _player_labels.get(k,k),
                                                  index=_pidx(_sel_match["team2"][1]),
                                                  key=f"adj_t2b_{_sel_mi}")

                    _all_4 = [_t1_new_a, _t1_new_b, _t2_new_a, _t2_new_b]
                    _dup_warn = len(set(_all_4)) < 4
                    if _dup_warn:
                        st.warning("⚠️ 4명 모두 달라야 합니다. 중복 선수가 있습니다.")

                    # 수정2: 개인별 기록 제외 체크박스
                    st.markdown("**기록실 제외 설정** (중복 참여 등 개인별 선택)")
                    _code_map_pre = {base_name(p): p for p in _lg_players_all}
                    _preview_players = [
                        (_t1_new_a, _player_labels.get(_t1_new_a, _t1_new_a), "팀1A"),
                        (_t1_new_b, _player_labels.get(_t1_new_b, _t1_new_b), "팀1B"),
                        (_t2_new_a, _player_labels.get(_t2_new_a, _t2_new_a), "팀2A"),
                        (_t2_new_b, _player_labels.get(_t2_new_b, _t2_new_b), "팀2B"),
                    ]
                    # 기존 저장된 exclude_players 불러오기
                    _prev_excl = set(_sel_match.get("exclude_players", []))
                    _excl_cc = st.columns(4)
                    _new_excl = []
                    for _ei, (_ek, _en, _elbl) in enumerate(_preview_players):
                        _full_code = _code_map_pre.get(_ek, _ek)
                        _is_excl = _excl_cc[_ei].checkbox(
                            f"제외 {_en}", value=(base_name(_full_code) in _prev_excl),
                            key=f"adj_excl_{_sel_mi}_{_ei}",
                            help=f"{_elbl}: {_en} 이 경기 기록 제외"
                        )
                        if _is_excl:
                            _new_excl.append(base_name(_full_code))

                    if st.button("✅ 페어 적용", type="primary", key=f"adj_apply_btn_{_sel_mi}",
                                 disabled=_dup_warn):
                        _code_map = {base_name(p): p for p in _lg_players_all}
                        _new_t1 = tuple([_code_map.get(_t1_new_a, _t1_new_a),
                                         _code_map.get(_t1_new_b, _t1_new_b)])
                        _new_t2 = tuple([_code_map.get(_t2_new_a, _t2_new_a),
                                         _code_map.get(_t2_new_b, _t2_new_b)])
                        _new_type = classify_match([base_name(p) for p in list(_new_t1)+list(_new_t2)])
                        _adj_matches[_sel_mi] = {
                            **_sel_match,
                            "team1": _new_t1,
                            "team2": _new_t2,
                            "type":  _new_type,
                            "exclude_players": _new_excl,  # 개인별 기록 제외
                        }
                        st.session_state["schedule"]    = _adj_matches
                        st.session_state["rp_schedule"] = _adj_matches
                        st.session_state["sb_schedule"] = _adj_matches
                        _ifr = st.session_state.get("last_gen_params",{}).get("is_fully_random", False)
                        shelf_save(rp_key_run, serialize_schedule(_adj_matches), {}, _ifr)
                        _excl_msg = f" (제외: {', '.join(_new_excl)})" if _new_excl else ""
                        st.success(f"✅ #{_sel_mi+1} 경기 페어 조정 완료{_excl_msg}!")
                        st.rerun()

            # ── 카카오톡 복사 버튼 (5번 기능) ─────────────────
            # [다이어트] _json2 별칭 제거 - 상단의 json 모듈 직접 사용

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
            kakao_json = json.dumps(kakao_text, ensure_ascii=False)

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
            st.info("💡 대진표 생성 후 사이드바에서 **📊 스코어보드**를 선택하면 점수를 입력할 수 있습니다.")

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
            # [다이어트] DataFrame 생성 및 매치 테이블/검증 렌더링 모두 공통 헬퍼 사용
            df_matches = _build_matches_df(schedule)
            df_full    = stats_to_df(stats)
            df_display = df_full.drop(columns=["_코드"])

            tab1, tab2, tab3 = st.tabs(["📋 대진표", "📊 출전 현황", "🔍 검증 리포트"])
            with tab1:
                _render_match_table(df_matches, active_lgs, seed_label, mode_label, league_players_r)
                # [수정3] 복원된 대진표에도 관리자 페어 조정 UI 표시
                if is_admin():
                    with st.expander("🔧 관리자: 페어 수동 조정", expanded=False):
                        st.caption("경기 번호를 선택하고 선수를 재배정할 수 있습니다.")
                        _adj2_matches = list(schedule)
                        _adj2_labels = []
                        for _mi2, _m2 in enumerate(_adj2_matches):
                            t1a2 = display_name(_m2["team1"][0]); t1b2 = display_name(_m2["team1"][1])
                            t2a2 = display_name(_m2["team2"][0]); t2b2 = display_name(_m2["team2"][1])
                            _adj2_labels.append(f"#{_mi2+1} [{_m2['round']}·{_m2['league']}] {t1a2}/{t1b2} vs {t2a2}/{t2b2}")
                        _sel_mi2 = st.selectbox("조정할 경기", range(len(_adj2_matches)),
                                                format_func=lambda i: _adj2_labels[i],
                                                key="adj2_match_sel")
                        _sm2 = _adj2_matches[_sel_mi2]
                        _lg2 = _sm2["league"]
                        _lp2_all = []
                        for _m3 in _adj2_matches:
                            if _m3["league"] == _lg2:
                                for _p3 in list(_m3["team1"]) + list(_m3["team2"]):
                                    if base_name(_p3) not in [base_name(x) for x in _lp2_all]:
                                        _lp2_all.append(_p3)
                        _pl2 = {base_name(p): display_name(p) for p in _lp2_all}
                        _keys2 = list(_pl2.keys())
                        _c1, _c2 = st.columns(2)
                        def _idx2(code):
                            k = base_name(code)
                            return _keys2.index(k) if k in _keys2 else 0
                        with _c1:
                            st.markdown("**팀1**")
                            _t1a2 = st.selectbox("팀1A", _keys2, format_func=lambda k:_pl2.get(k,k),
                                                  index=_idx2(_sm2["team1"][0]), key=f"adj2_t1a_{_sel_mi2}")
                            _t1b2 = st.selectbox("팀1B", _keys2, format_func=lambda k:_pl2.get(k,k),
                                                  index=_idx2(_sm2["team1"][1]), key=f"adj2_t1b_{_sel_mi2}")
                        with _c2:
                            st.markdown("**팀2**")
                            _t2a2 = st.selectbox("팀2A", _keys2, format_func=lambda k:_pl2.get(k,k),
                                                  index=_idx2(_sm2["team2"][0]), key=f"adj2_t2a_{_sel_mi2}")
                            _t2b2 = st.selectbox("팀2B", _keys2, format_func=lambda k:_pl2.get(k,k),
                                                  index=_idx2(_sm2["team2"][1]), key=f"adj2_t2b_{_sel_mi2}")
                        _d2 = len({_t1a2,_t1b2,_t2a2,_t2b2}) < 4
                        if _d2: st.warning("⚠️ 4명 모두 달라야 합니다.")
                        if st.button("✅ 페어 적용", type="primary", key=f"adj2_apply_{_sel_mi2}", disabled=_d2):
                            _cm2 = {base_name(p): p for p in _lp2_all}
                            _nt1 = tuple([_cm2.get(_t1a2,_t1a2), _cm2.get(_t1b2,_t1b2)])
                            _nt2 = tuple([_cm2.get(_t2a2,_t2a2), _cm2.get(_t2b2,_t2b2)])
                            _ntype = classify_match([base_name(p) for p in list(_nt1)+list(_nt2)])
                            _adj2_matches[_sel_mi2] = {**_sm2, "team1":_nt1, "team2":_nt2, "type":_ntype}
                            st.session_state["rp_schedule"] = _adj2_matches
                            st.session_state["sb_schedule"] = _adj2_matches
                            _ifr2 = restored_params.get("is_fully_random", False)
                            shelf_save(rp_key_run, serialize_schedule(_adj2_matches), {}, _ifr2)
                            st.success(f"✅ #{_sel_mi2+1} 경기 페어 조정 완료!")
                            st.rerun()
            with tab2:
                st.subheader("선수별 출전 현황")
                st.dataframe(df_display, use_container_width=True, height=700)
            with tab3:
                st.subheader("🔍 검증 리포트")
                _render_basic_validation(df_full)

        else:
            # ── 최초 진입 안내 ───────────────────────────────
            st.info("👈 사이드바에서 리그·페어링 방식·인원을 설정하고 비밀번호 입력 후 **대진표 생성** 버튼을 눌러주세요.")

        # ═══════════════════════════════════════════════════════
        # 리그 설정 (구글 시트 직접 연동)
        # ═══════════════════════════════════════════════════════
        st.markdown("---")
        with st.expander("🏷️ 회원 리그 설정 (구글 시트 직접 연동)", expanded=False):
            st.caption("체크박스로 회원을 선택하고 이동 대상 리그를 선택 후 일괄 저장하세요.")

            _ref_col, _ = st.columns([1, 4])
            if _ref_col.button("🔄 최신 데이터 로드", key="lg_refresh_btn"):
                st.cache_data.clear()
                st.rerun()

            try:
                with st.spinner("회원 명부 불러오는 중…"):
                    _lg_df = load_df_for_match()   # TTL 캐시 사용 (429 방지)
            except Exception as _e:
                st.error(f"구글 시트 연결 오류: {_e}")
                _lg_df = pd.DataFrame()

            if not _lg_df.empty:
                # ── 리그별 탭 ────────────────────────────────
                _lg_tabs = st.tabs([f"📋 {lg}" for lg in active_leagues] + ["📋 미배정"])
                _lg_list = active_leagues + [""]

                for _ti, (_tab, _lg_val) in enumerate(zip(_lg_tabs, _lg_list)):
                    with _tab:
                        _tab_label = _lg_val if _lg_val else "미배정"
                        if _lg_val == "":
                            _tab_df = _lg_df[_lg_df["league"].astype(str).str.strip() == ""].copy()
                        else:
                            _tab_df = _lg_df[_lg_df["league"].astype(str).str.strip() == _lg_val].copy()

                        lc_t = LEAGUE_COLORS[_ti % len(LEAGUE_COLORS)]
                        st.markdown(
                            f'<div style="color:{lc_t};font-weight:700;margin-bottom:8px;">'
                            f'{_tab_label} 회원 ({len(_tab_df)}명)</div>',
                            unsafe_allow_html=True
                        )

                        if _tab_df.empty:
                            st.info("이 리그에 배정된 회원이 없습니다.")
                            continue

                        # 이름순 정렬
                        _tab_df = _tab_df.sort_values("name").reset_index(drop=True)

                        # ── 이동 대상 리그 + 일괄 저장 (상시 표시) ──
                        _all_lgs   = active_leagues + (["미배정"] if _lg_val != "" else [])
                        _other_lgs = [lg for lg in active_leagues if lg != _lg_val] + (["미배정"] if _lg_val != "" else [])
                        _target_options = _other_lgs if _other_lgs else active_leagues

                        _ctrl1, _ctrl2, _ctrl3, _ctrl4 = st.columns([1, 1, 2, 1])
                        _sa_key = f"selall_{_tab_label}"
                        _sd_key = f"seldeall_{_tab_label}"

                        # 전체선택/해제
                        if _ctrl1.button("✅ 전체선택", key=f"sa_{_tab_label}"):
                            for _, _r in _tab_df.iterrows():
                                st.session_state[f"lgchk_{_r['id']}"] = True
                            st.rerun()
                        if _ctrl2.button("⬜ 전체해제", key=f"sd_{_tab_label}"):
                            for _, _r in _tab_df.iterrows():
                                st.session_state[f"lgchk_{_r['id']}"] = False
                            st.rerun()

                        # 이동 대상 리그 selectbox (상시)
                        _target_sel = _ctrl3.selectbox(
                            "이동 대상 리그",
                            _target_options,
                            key=f"bulk_target_{_tab_label}",
                            label_visibility="collapsed"
                        )
                        _target_val = "" if _target_sel == "미배정" else _target_sel

                        # 선택된 회원 수
                        _checked_ids = [
                            int(_r["id"]) for _, _r in _tab_df.iterrows()
                            if st.session_state.get(f"lgchk_{_r['id']}", False)
                        ]
                        _checked_names = [
                            str(_r["name"]) for _, _r in _tab_df.iterrows()
                            if st.session_state.get(f"lgchk_{_r['id']}", False)
                        ]

                        # 일괄 저장 버튼 (선택 수 표시)
                        _btn_label = f"💾 저장 ({len(_checked_ids)}명 선택)" if _checked_ids else "💾 저장 (선택 없음)"
                        if _ctrl4.button(_btn_label, key=f"bulk_save_{_tab_label}",
                                         type="primary" if _checked_ids else "secondary",
                                         disabled=len(_checked_ids) == 0):
                            _ok_cnt = 0
                            with st.spinner(f"{len(_checked_ids)}명 저장 중…"):
                                for _mid in _checked_ids:
                                    if save_league_to_sheet(_mid, _target_val):
                                        _ok_cnt += 1
                            st.success(f"✅ {_ok_cnt}명 → {_target_sel} 저장 완료")
                            # 체크박스 초기화
                            for _mid in _checked_ids:
                                st.session_state[f"lgchk_{_mid}"] = False
                            st.rerun()

                        st.markdown("<div style='border-bottom:1px solid #e2e8f0;margin:4px 0 8px'></div>",
                                    unsafe_allow_html=True)

                        # ── 회원 목록 (체크박스) ─────────────────
                        _hc = st.columns([0.5, 3, 1, 2, 1])
                        _hc[0].markdown("**☑**")
                        _hc[1].markdown("**이름**"); _hc[2].markdown("**성별**")
                        _hc[3].markdown("**개별 이동 대상**"); _hc[4].markdown("**저장**")

                        for _, _row in _tab_df.iterrows():
                            _g   = "남" if str(_row.get("gender","")).strip() in ("남","M") else "여"
                            _rid = int(_row["id"])
                            _chk_key = f"lgchk_{_rid}"
                            if _chk_key not in st.session_state:
                                st.session_state[_chk_key] = False

                            # 휴면 판별
                            _is_dorm = (_row.get("category") == "휴면")
                            if not _is_dorm and str(_row.get("dormant_period","")).strip():
                                _dp = str(_row.get("dormant_period","")).strip()
                                if "~" in _dp:
                                    _dp_end = _dp.split("~")[-1].strip()
                                    if not _dp_end:
                                        _is_dorm = True
                                    else:
                                        try: _is_dorm = date.fromisoformat(_dp_end[:10]) >= date.today()
                                        except ValueError: pass
                                else:
                                    _is_dorm = True
                            _status_badge = (
                                '<span style="background:#FF8F00;color:#fff;border-radius:4px;'
                                'padding:1px 5px;font-size:0.68rem;font-weight:700;margin-left:4px;">💤휴면</span>'
                                if _is_dorm else
                                '<span style="background:#2e7d32;color:#fff;border-radius:4px;'
                                'padding:1px 5px;font-size:0.68rem;font-weight:700;margin-left:4px;">✅정상</span>'
                            )
                            _name_html = f'{_row["name"]}{_status_badge}'

                            _rc = st.columns([0.5, 3, 1, 2, 1])
                            _rc[0].checkbox("", key=_chk_key, label_visibility="collapsed")
                            _rc[1].markdown(_name_html, unsafe_allow_html=True)
                            _rc[2].write(_g)

                            # 개별 이동 대상 selectbox
                            _ind_opts   = _target_options
                            _ind_sel    = _rc[3].selectbox(
                                "개별리그", _ind_opts,
                                key=f"lgind_{_rid}",
                                label_visibility="collapsed"
                            )
                            _ind_val = "" if _ind_sel == "미배정" else _ind_sel

                            if _rc[4].button("저장", key=f"lgsave_{_rid}"):
                                with st.spinner("저장 중…"):
                                    _ok = save_league_to_sheet(_rid, _ind_val)
                                if _ok:
                                    st.success(f"✅ '{_row['name']}' → {_ind_sel} 저장")
                                    st.rerun()
            else:
                st.info("구글 시트에 회원 데이터가 없습니다.")

            # ── 게스트 관리 (회원명부 미반영) ────────────────────
            st.markdown("---")
            st.markdown("#### 👤 게스트 관리")
            st.caption("회원명부·구글 시트 미반영 · 직접 삭제 전까지 유지됩니다.")

            # 추가 폼
            _gc1, _gc2, _gc3, _gc4 = st.columns([2, 2, 1, 1])
            _g_lg   = _gc1.selectbox("리그", active_leagues, key="guest_lg",
                                     label_visibility="collapsed")
            _g_name = _gc2.text_input("이름", key="guest_name", placeholder="이름 입력",
                                      label_visibility="collapsed")
            _g_sex  = _gc3.selectbox("성별", ["남", "여"], key="guest_sex",
                                     label_visibility="collapsed")
            if _gc4.button("➕ 추가", key="add_guest_btn", use_container_width=True):
                if _g_name.strip():
                    _g_code = "M" if _g_sex == "남" else "W"
                    _pfx_g  = active_prefixes[active_leagues.index(_g_lg)]
                    _gcode  = f"{_pfx_g}{_g_code}★{_g_name.strip()}"
                    guest_add(_g_name.strip(), _g_code, _g_lg, _gcode)
                    st.success(f"✅ '{_g_name.strip()}' 게스트 추가 완료")
                    st.rerun()
                else:
                    st.warning("이름을 입력해주세요.")

            # 현재 게스트 목록
            _all_guests = guest_load()
            if _all_guests:
                st.markdown(f"**등록된 게스트 ({len(_all_guests)}명)**")
                _gh = st.columns([2, 2, 1, 1])
                _gh[0].markdown("**리그**"); _gh[1].markdown("**이름**")
                _gh[2].markdown("**성별**"); _gh[3].markdown("**삭제**")
                for _gm in list(_all_guests):
                    _lc_g = LEAGUE_COLORS[active_leagues.index(_gm["league"])] \
                            if _gm["league"] in active_leagues else "#555"
                    _gr = st.columns([2, 2, 1, 1])
                    _gr[0].markdown(
                        f'<span style="color:{_lc_g};font-weight:700;">{_gm["league"]}</span>',
                        unsafe_allow_html=True
                    )
                    _gr[1].write(_gm["name"])
                    _gr[2].write("남" if _gm["gender"] == "M" else "여")
                    if _gr[3].button("🗑", key=f"del_guest_{_gm['league']}_{_gm['name']}"):
                        guest_remove(_gm["name"], _gm["league"])
                        st.rerun()
            else:
                st.info("등록된 게스트가 없습니다.")

        if not restored_schedule:
            with st.expander("📖 사용 방법 및 규칙 안내"):
                st.markdown("""
                ### v5.3 기능 안내

                | 항목 | 내용 |
                |------|------|
                | **회원 사전 등록** | 👥 회원 관리에서 리그별 회원 등록 후 체크박스로 선택 |
                | **대진표 불러오기** | 📂 저장된 대진표 불러오기에서 날짜 선택 후 로드 |
                | **페이지 복귀 유지** | 스코어보드↔대진표생성 이동해도 마지막 대진표 유지 |
                | **리그 수 설정** | 1~5개 자유 설정 (A→B→C→D→E 순) |
                | **페어링 방식** | 🔵 조건부 / 🔴 완전 랜덤 선택 |
                | **재생성 버튼** | 동일 설정으로 새 대진표 즉시 생성 |
                | **카카오톡 복사** | 대진표를 카카오톡용 텍스트로 한 번에 복사 |
                | **QR코드** | 앱 URL QR코드로 회원 공유 |

                ### 공통 출전 규칙
                - 최소 3경기 보장 → 이벤트 라운드(4R)로 보충
                - 최대 4경기 제한

                ### 점수판
                1. 대진표 생성 후 사이드바 **📊 스코어보드** 선택
                2. 날짜+일련번호 입력 (대진표생성과 동일하게)
                3. 각 경기 **💾 저장** 버튼 클릭 → 새로고침 후에도 유지
                """)

elif page == "🏆 기록실":
    st.markdown("## 🏆 기록실 (누적 통계)")
    st.caption("점수 저장 시 구글시트에 자동 누적됩니다. 중복 선수 및 제외 지정 선수는 기록에서 제외됩니다.")

    # ── 관리자 전용: 기록 제외 선수 관리 ────────────────────────
    if is_admin():
        with st.expander("⚙️ 관리자: 기록 제외 선수 설정 (코치 등)", expanded=False):
            st.caption("여기 등록된 선수는 기록실 집계·조회에서 완전히 제외됩니다. 이름은 구글시트 player_key와 동일하게 입력하세요.")
            _ex_list = exclude_list_load()

            # 현재 제외 목록
            if _ex_list:
                st.markdown(f"**현재 제외 선수 ({len(_ex_list)}명)**")
                for _ex_name in _ex_list:
                    _exc1, _exc2 = st.columns([5, 1])
                    _exc1.markdown(f'<div style="padding:4px 0;font-size:0.9rem;">🚫 {_ex_name}</div>',
                                   unsafe_allow_html=True)
                    if _exc2.button("삭제", key=f"del_ex_{_ex_name}", use_container_width=True):
                        exclude_list_remove(_ex_name)
                        st.success(f"'{_ex_name}' 제외 목록에서 제거됨")
                        st.rerun()
            else:
                st.info("제외 선수가 없습니다.")

            st.markdown("---")
            _add_c1, _add_c2 = st.columns([5, 1])
            _new_ex = _add_c1.text_input("제외할 선수 이름 입력",
                                          placeholder="예: 윤지수  (구글시트 player_key와 동일하게)",
                                          label_visibility="collapsed", key="new_exclude_inp")
            if _add_c2.button("➕ 추가", key="add_exclude_btn", use_container_width=True):
                if _new_ex.strip():
                    if _new_ex.strip() in _ex_list:
                        st.warning(f"'{_new_ex.strip()}'는 이미 제외 목록에 있습니다.")
                    else:
                        exclude_list_add(_new_ex.strip())
                        st.success(f"✅ '{_new_ex.strip()}' 제외 등록 완료. 재집계 버튼으로 기존 데이터도 정정하세요.")
                        st.rerun()
                else:
                    st.warning("이름을 입력해주세요.")

            # ── 전체 날짜 일괄 재집계 ──
            st.markdown("---")
            st.caption("⚠️ 제외 선수를 새로 추가한 경우, 기존에 저장된 모든 날짜의 기록을 아래 버튼으로 한 번에 재집계해야 합니다.")
            if st.button("🔁 전체 날짜 일괄 재집계 (관리자)", type="primary",
                         key="bulk_reagg_btn",
                         help="저장된 모든 점수판 날짜의 기록실 데이터를 현재 제외 목록 기준으로 다시 계산합니다."):
                _all_keys = shelf_list_dates()
                if not _all_keys:
                    st.warning("저장된 스코어보드가 없습니다.")
                else:
                    _ok, _fail = 0, 0
                    with st.spinner(f"{len(_all_keys)}개 날짜 재집계 중…"):
                        for _dk in _all_keys:
                            try:
                                _sd = shelf_load(_dk)
                                if not _sd:
                                    continue
                                _sched = deserialize_schedule(_sd["schedule"])
                                _sc    = _sd.get("scores", {})
                                if _sc:
                                    records_commit(_dk, _sched, _sc)
                                    _ok += 1
                            except Exception:
                                _fail += 1
                    st.cache_data.clear()
                    if _fail:
                        st.warning(f"✅ {_ok}개 완료, ⚠️ {_fail}개 오류")
                    else:
                        st.success(f"✅ {_ok}개 날짜 재집계 완료! 제외 선수({', '.join(exclude_list_load())})가 모든 기록에서 제거되었습니다.")
                    st.rerun()


    _now = date.today()
    _c1, _c2, _c3 = st.columns([3, 3, 2])
    with _c1:
        _rec_mode = st.radio("기간", ["월간", "연간"], horizontal=True,
                              key="rec_page_mode", label_visibility="collapsed")
    with _c2:
        if _rec_mode == "월간":
            _months = []
            for i in range(12):
                _m = _now.month - i
                _y = _now.year
                while _m <= 0: _m += 12; _y -= 1
                _months.append(f"{_y}-{_m:02d}")
            _months = sorted(list(dict.fromkeys(_months)), reverse=True)
            _sel_val = st.selectbox("월", _months, key="rec_pg_month",
                                     label_visibility="collapsed")
            _ft = "monthly"; _fv = _sel_val; _lbl = f"{_sel_val} 월간"
        else:
            _years = [str(_now.year - i) for i in range(4)]
            _sel_val = st.selectbox("연도", _years, key="rec_pg_year",
                                     label_visibility="collapsed")
            _ft = "yearly"; _fv = _sel_val; _lbl = f"{_sel_val} 연간"
    with _c3:
        if st.button("🔄 새로고침", key="rec_refresh", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

    with st.spinner("기록 불러오는 중…"):
        try:
            _df_rec = records_get_df(_ft, _fv)
        except Exception as _re:
            st.error(f"기록실 로드 오류: {_re}")
            _df_rec = pd.DataFrame()

    if _df_rec.empty:
        st.info(f"📭 {_lbl} 기록이 없습니다. 스코어보드에서 점수를 저장하면 자동으로 집계됩니다.")
    else:
        _all_leagues = list(_df_rec["리그"].unique())

        # ── 왕 카드 렌더 헬퍼 ─────────────────────────────────
        def _award_card(emoji, title, name, value, color, subtitle=""):
            return f"""
<div style="background:linear-gradient(135deg,{color}22,{color}08);
     border:2px solid {color}55;border-radius:14px;padding:14px 16px;
     text-align:center;box-shadow:0 2px 12px {color}22;">
  <div style="font-size:2rem;line-height:1.1">{emoji}</div>
  <div style="font-size:0.68rem;font-weight:700;color:{color};
       letter-spacing:0.5px;margin:4px 0 2px;line-height:1.3">{title}</div>
  <div style="font-size:1.1rem;font-weight:900;color:#1a2e4a;margin:2px 0">{name}</div>
  <div style="font-size:0.85rem;font-weight:700;color:{color}">{value}</div>
  {"<div style='font-size:0.65rem;color:#9ca3af;margin-top:2px'>"+subtitle+"</div>" if subtitle else ""}
</div>"""

        # ── 연간 모드: 전 리그 통합 수상 ─────────────────────
        if _ft == "yearly":
            _yr = _fv
            st.markdown(
                f'<div style="background:linear-gradient(135deg,#1a1a2e22,transparent);'
                f'border-left:5px solid #1a1a2e;border-radius:0 10px 10px 0;'
                f'padding:10px 16px;margin:16px 0 12px;">'
                f'<span style="color:#1a1a2e;font-weight:900;font-size:1.05rem;">🏆 {_yr} TELA 통합 랭킹</span>'
                f'<span style="color:#6b7280;font-size:0.8rem;margin-left:8px;">— 전 리그 통합</span>'
                f'</div>', unsafe_allow_html=True)

            _df_all = _df_rec.copy()
            _df_all_act = _df_all[(_df_all["승"] + _df_all["패"]) > 0]
            _ac = st.columns(3)
            _ch = ["", "", ""]
            if not _df_all_act.empty:
                # 득점왕 (1순위)
                _mp = _df_all_act["득점"].max()
                _wp = _df_all_act[_df_all_act["득점"] == _mp].iloc[0]
                _yr_lbl = f"{_yr[2:]}년 통합"
                _ch[0] = _award_card("🎯", f"{_yr_lbl} 득점왕", _wp["이름"],
                                     f"{int(_mp)}점", "#2563eb",
                                     f"득실차 {int(_wp['득실차']):+d}")
                # 다승왕
                _mw = _df_all_act["승"].max()
                _ww = _df_all_act[_df_all_act["승"] == _mw].iloc[0]
                _ch[1] = _award_card("🥇", f"{_yr_lbl} 다승왕", _ww["이름"],
                                     f"{int(_mw)}승", "#f59e0b",
                                     f"승률 {_ww['승률']}")
                # 승률왕
                _df_r2 = _df_all_act[(_df_all_act["승"]+_df_all_act["패"])>=2].copy()
                if not _df_r2.empty:
                    _df_r2["_rn"] = _df_r2["승"] / (_df_r2["승"]+_df_r2["패"])
                    _mr = _df_r2["_rn"].max()
                    _wr = _df_r2[_df_r2["_rn"]==_mr].iloc[0]
                    _ch[2] = _award_card("👑", f"{_yr_lbl} 승률왕", _wr["이름"],
                                         _wr["승률"], "#7c3aed",
                                         f"{int(_wr['승'])}승 {int(_wr['패'])}패")
                else:
                    _ch[2] = _award_card("👑", f"{_yr_lbl} 승률왕", "—", "2경기↑ 필요", "#9ca3af")
            else:
                _yr_lbl = f"{_yr[2:]}년 통합"
                _ch[0] = _award_card("🎯", f"{_yr_lbl} 득점왕", "—", "기록 없음", "#9ca3af")
                _ch[1] = _award_card("🥇", f"{_yr_lbl} 다승왕", "—", "기록 없음", "#9ca3af")
                _ch[2] = _award_card("👑", f"{_yr_lbl} 승률왕", "—", "기록 없음", "#9ca3af")
            for _ci, _h in enumerate(_ch):
                _ac[_ci].markdown(_h, unsafe_allow_html=True)
            st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)

        # ── 리그별 섹션 ──────────────────────────────────────
        for _rec_lg in _all_leagues:
            _df_lg_full = _df_rec[_df_rec["리그"] == _rec_lg].copy()
            if _df_lg_full.empty:
                continue

            _lc = get_league_color(_rec_lg)

            # 리그 헤더
            st.markdown(
                f'<div style="background:linear-gradient(135deg,{_lc}22,transparent);'
                f'border-left:5px solid {_lc};border-radius:0 10px 10px 0;'
                f'padding:10px 16px;margin:20px 0 12px 0;">'
                f'<span style="color:{_lc};font-weight:900;font-size:1.05rem;">🎾 {_rec_lg}</span>'
                f'<span style="color:#6b7280;font-size:0.8rem;margin-left:8px;">— {_lbl}</span>'
                f'</div>', unsafe_allow_html=True)

            _df_active = _df_lg_full[(_df_lg_full["승"] + _df_lg_full["패"]) > 0]
            _award_cols = st.columns(3)
            _cards_html = ["", "", ""]

            # 수정6: 월간 카드 제목 = "{리그} 월간 최다득점" 등
            # 수정5: 카드 순서 = 득점왕(0) → 다승왕(1) → 승률왕(2)
            if _ft == "monthly":
                _ym = _fv  # "2026-05"
                # "2026-05" → "26년 05월"
                try:
                    _ym_parts = _ym.split("-")
                    _ym_label = f"{_ym_parts[0][2:]}년 {_ym_parts[1]}월"
                except Exception:
                    _ym_label = _ym
                _t_score = f"{_rec_lg} {_ym_label} 최다득점"
                _t_wins  = f"{_rec_lg} {_ym_label} 최다승"
                _t_rate  = f"{_rec_lg} {_ym_label} 최고승률"
            else:
                _t_score = f"{_rec_lg} 득점왕"
                _t_wins  = f"{_rec_lg} 다승왕"
                _t_rate  = f"{_rec_lg} 승률왕"

            if not _df_active.empty:
                # 득점왕 (카드 0번)
                _max_p = _df_active["득점"].max()
                _winner_p = _df_active[_df_active["득점"] == _max_p].iloc[0]
                _cards_html[0] = _award_card("🎯", _t_score, _winner_p["이름"],
                                              f"{int(_max_p)}점", "#2563eb",
                                              f"득실차 {int(_winner_p['득실차']):+d}")
                # 다승왕 (카드 1번)
                _max_w = _df_active["승"].max()
                _winner_w = _df_active[_df_active["승"] == _max_w].iloc[0]
                _cards_html[1] = _award_card("🥇", _t_wins, _winner_w["이름"],
                                              f"{int(_max_w)}승", "#f59e0b",
                                              f"승률 {_winner_w['승률']}")
                # 승률왕 (카드 2번)
                _df_rate = _df_active[(_df_active["승"] + _df_active["패"]) >= 2].copy()
                if not _df_rate.empty:
                    _df_rate["_rate_num"] = _df_rate["승"] / (_df_rate["승"] + _df_rate["패"])
                    _max_r = _df_rate["_rate_num"].max()
                    _winner_r = _df_rate[_df_rate["_rate_num"] == _max_r].iloc[0]
                    _cards_html[2] = _award_card("👑", _t_rate, _winner_r["이름"],
                                                  _winner_r["승률"], "#7c3aed",
                                                  f"{int(_winner_r['승'])}승 {int(_winner_r['패'])}패")
                else:
                    _cards_html[2] = _award_card("👑", _t_rate, "—", "2경기↑ 필요", "#9ca3af")
            else:
                _cards_html[0] = _award_card("🎯", _t_score, "—", "기록 없음", "#9ca3af")
                _cards_html[1] = _award_card("🥇", _t_wins,  "—", "기록 없음", "#9ca3af")
                _cards_html[2] = _award_card("👑", _t_rate,  "—", "기록 없음", "#9ca3af")

            for _ci, _html in enumerate(_cards_html):
                _award_cols[_ci].markdown(_html, unsafe_allow_html=True)

            st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)

            _df_lg_disp = _df_lg_full.drop(columns=["리그"]).reset_index(drop=True)
            # 수정1: Streamlit 자동 배경색(숫자 gradient) 제거 → column_config으로 모든 컬럼 text화
            import streamlit as _st_cc
            _cc_cfg = {c: _st_cc.column_config.NumberColumn(c, format="%d")
                       for c in ["출전경기","승","패","득점","실점","득실차"]
                       if c in _df_lg_disp.columns}
            st.dataframe(_df_lg_disp, use_container_width=True, hide_index=True,
                         column_config=_cc_cfg)

elif page == "👥 회원명부":
    render_roster_page()
