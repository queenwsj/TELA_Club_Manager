"""
TELA CLUB Random Match Generator v2.02
======================================
핵심 규칙:
  1. 1인 최소 3경기 보장 / 최대 4경기 제한 (중복 출전 최대 1회)
  2. 매치 우선순위: 동성복(남복/여복) > 혼복(2M+2W) > 잡복(3:1)
  3. 혼성 자리 발생 시 → 혼성 경기 최소 참여자 우선 선발
  4. 같은 팀/상대 중복 최소화 (페널티 점수제)
  5. 입력 방식: 코드 자동 생성(AM01...) 또는 자유 이름 입력 모두 지원
"""

import streamlit as st
import pandas as pd
import random
import io
from dataclasses import dataclass, field
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
    """'AM01(중복)' → 'AM01'"""
    return p.split("(")[0].strip()

def get_gender(code: str) -> str:
    """AM01→M, AW01→W, AM홍길동→M, AW김영희→W"""
    c = base_name(code)
    if len(c) >= 2:
        ch = c[1].upper()
        if ch == "M": return "M"
        if ch == "W": return "W"
    return "U"

def is_custom_code(code: str) -> bool:
    """코드 3번째 자리부터 숫자가 아니면 커스텀 이름 코드"""
    raw = base_name(code)
    return len(raw) > 2 and not raw[2:].isdigit()

def display_name(code: str) -> str:
    """
    표시용 이름 변환.
    'AM01'     → 'AM01'
    'AM홍길동' → '홍길동(남)'
    'AM01(중복)' → 'AM01(중복)'
    """
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
    """혼성 경기 횟수 적은 순 정렬 (동점 → 랜덤)"""
    return sorted(players, key=lambda p: (mixed_counts.get(base_name(p), 0), random.random()))


# ============================================================
# 섹션 3: 페어링 (4명 → 최적 2팀)
# ============================================================

def score_pairing(
    t1: Tuple, t2: Tuple,
    gs: MatchState, rs: MatchState
) -> int:
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

def best_pairing(
    players4: List[str],
    gs: MatchState, rs: MatchState
) -> Tuple[Tuple, Tuple]:
    """페널티 최소 2팀 분할. 혼복(2M2W)은 반드시 혼성 팀끼리."""
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
    for t1,t2 in cands:
        s = score_pairing(t1, t2, gs, rs)
        if s < best_s:
            best_s, best = s, (t1, t2)
    return best

def commit_pairing(t1: Tuple, t2: Tuple, gs: MatchState, rs: MatchState):
    for state in (gs, rs):
        state.teammate_used.add(team_key(t1))
        state.teammate_used.add(team_key(t2))
        for x in t1:
            for y in t2:
                state.opponent_used.add(opp_key(x, y))
        for t in (t1, t2):
            mk = mixed_partner_key(t)
            if mk: state.mixed_partner_used.add(mk)


# ============================================================
# 섹션 4: 그룹 구성 (우선순위 + 혼성 최소 선발)
# ============================================================

def build_one_group(
    pool: List[str],
    mixed_counts: Dict[str, int],
) -> Tuple[Optional[List[str]], List[str]]:
    """
    pool[0] = anchor (game_count 최소 선수) 강제 포함.
    anchor 성별 기준으로 우선순위에 따라 나머지 3명 선발.
    우선순위: 동성복 > 혼복 > 잡복
    혼성 자리는 mixed_count 최소 선수 우선.
    """
    if len(pool) < 4:
        return None, pool[:]

    anchor = pool[0]
    rest   = pool[1:]
    g_a    = get_gender(anchor)
    same   = [p for p in rest if get_gender(p) == g_a]
    opp    = [p for p in rest if get_gender(p) != g_a and get_gender(p) != "U"]

    group = None

    # 1순위: 동성복 (anchor + 같은 성별 3명)
    if len(same) >= 3:
        sh = list(same); random.shuffle(sh)
        group = [anchor] + sh[:3]

    # 2순위: 혼복 (anchor + 같은 성별 1명 + 반대 성별 2명)
    elif len(same) >= 1 and len(opp) >= 2:
        sh = list(same); random.shuffle(sh)
        group = [anchor] + sh[:1] + sort_by_mixed_least(opp, mixed_counts)[:2]

    # 3순위: 잡복 (anchor + 같은 성별 2명 + 반대 성별 1명)
    elif len(same) >= 2 and len(opp) >= 1:
        sh = list(same); random.shuffle(sh)
        group = [anchor] + sh[:2] + sort_by_mixed_least(opp, mixed_counts)[:1]

    # 3순위: 잡복 반대 (anchor + 반대 성별 3명)
    elif len(opp) >= 3:
        group = [anchor] + sort_by_mixed_least(opp, mixed_counts)[:3]

    # 최후수단
    else:
        others = sort_by_mixed_least(rest, mixed_counts)
        group = [anchor] + others[:3]

    if group is None or len(group) < 4:
        return None, pool[:]

    remaining = list(pool)
    for p in group:
        if p in remaining:
            remaining.remove(p)
    return group, remaining


def build_all_groups(
    pool: List[str],
    mixed_counts: Dict[str, int],
) -> Tuple[List[List[str]], List[str]]:
    """pool 전체를 4명 그룹으로 분할. 나머지는 leftovers 반환."""
    groups, remaining = [], list(pool)
    while len(remaining) >= 4:
        group, remaining = build_one_group(remaining, mixed_counts)
        if group is None:
            break
        groups.append(group)
    return groups, remaining


# ============================================================
# 섹션 5: 정규 라운드 매치 생성 (강제 포함 로직)
# ============================================================

def _pick_3_for_anchor(
    anchor: str,
    remaining: List[str],
    mixed_counts: Dict[str, int],
) -> Optional[List[str]]:
    """
    anchor 1명에 맞춰 나머지 3명을 우선순위대로 선발.
    동성복 > 혼복 > 잡복, 혼성 자리 = mixed_count 최소 우선.
    """
    if len(remaining) < 3:
        return None
    g     = get_gender(anchor)
    men   = [p for p in remaining if get_gender(p) == "M"]
    women = [p for p in remaining if get_gender(p) == "W"]

    if g == "M":
        if len(men) >= 3:
            return men[:3]
        if len(men) >= 1 and len(women) >= 2:
            return (sort_by_mixed_least(men, mixed_counts)[:1]
                  + sort_by_mixed_least(women, mixed_counts)[:2])
        if len(men) >= 2 and len(women) >= 1:
            m2 = list(men); random.shuffle(m2)
            return m2[:2] + sort_by_mixed_least(women, mixed_counts)[:1]
        if len(women) >= 3:
            return sort_by_mixed_least(women, mixed_counts)[:3]
    elif g == "W":
        if len(women) >= 3:
            return women[:3]
        if len(women) >= 1 and len(men) >= 2:
            return (sort_by_mixed_least(women, mixed_counts)[:1]
                  + sort_by_mixed_least(men, mixed_counts)[:2])
        if len(women) >= 2 and len(men) >= 1:
            w2 = list(women); random.shuffle(w2)
            return w2[:2] + sort_by_mixed_least(men, mixed_counts)[:1]
        if len(men) >= 3:
            return sort_by_mixed_least(men, mixed_counts)[:3]
    # 최후수단
    rest = sort_by_mixed_least(remaining, mixed_counts)
    return rest[:3] if len(rest) >= 3 else None


def make_round_matches(
    players: List[str],
    game_counts:  Dict[str, int],
    mixed_counts: Dict[str, int],
    global_state: MatchState,
    round_state:  MatchState,
) -> List[dict]:
    """
    정규 라운드 매치 생성.
    경기 횟수 최소 선수(소수 성별 포함)를 각 그룹의 앵커로 강제 배정.
    leftover는 이벤트 라운드(4R)에서 처리.
    """
    n_groups = len(players) // 4
    if n_groups == 0:
        return []

    # 경기 횟수 오름차순 정렬
    pool = sorted(players, key=lambda p: (game_counts.get(base_name(p), 0), random.random()))

    # 경기수 최소 선수 n_groups명 = 각 그룹의 앵커
    forced_anchors = pool[:n_groups]
    remaining_pool = [p for p in pool if p not in forced_anchors]

    groups_of_4: List[List[str]] = []

    for anchor in forced_anchors:
        three = _pick_3_for_anchor(anchor, remaining_pool, mixed_counts)
        if three is None or len(three) < 3:
            remaining_pool.insert(0, anchor)
            continue
        group = [anchor] + three
        groups_of_4.append(group)
        for p in group:
            if p in remaining_pool:
                remaining_pool.remove(p)

    # 남은 선수로 추가 그룹 구성
    if len(remaining_pool) >= 4:
        extra_groups, _ = build_all_groups(remaining_pool, mixed_counts)
        groups_of_4.extend(extra_groups)

    # fallback
    if not groups_of_4:
        groups_of_4, _ = build_all_groups(pool, mixed_counts)

    matches = []
    for g in groups_of_4:
        if len(g) < 4: continue
        random.shuffle(g)
        t1, t2 = best_pairing(g, global_state, round_state)
        commit_pairing(t1, t2, global_state, round_state)
        mt = classify_match([base_name(p) for p in list(t1) + list(t2)])
        matches.append({"team1": t1, "team2": t2, "type": mt})
    return matches


# ============================================================
# 섹션 6: 이벤트 라운드 (3회 미달자 보장 + 최대 4회 절대 제한)
# ============================================================

def build_event_round(
    players:      List[str],
    game_counts:  Dict[str, int],
    mixed_counts: Dict[str, int],
    min_games:    int = 3,
    max_games:    int = 4,
) -> List[Tuple[List[str], List[str]]]:
    """
    이벤트 라운드 그룹 구성.
    - min_games 미달자가 없어질 때까지 반복
    - 4명 단위 맞추기: max_games 미만 선수 중 혼성 최소 우선 보충
    - max_games 이상 선수 절대 선발 불가
    - 반환: [(원본그룹, 중복태그그룹)] 리스트
    """
    all_groups: List[Tuple[List[str], List[str]]] = []
    local_counts = dict(game_counts)  # 이벤트 내 누적 추적

    for _ in range(20):  # 안전 상한
        need = [p for p in players if local_counts.get(base_name(p), 0) < min_games]
        if not need:
            break

        avail = [
            p for p in players
            if p not in need and local_counts.get(base_name(p), 0) < max_games
        ]

        # pool: 미달자를 game_count 낮은 순(anchor 강제 포함 보장)
        pool = sorted(need, key=lambda p: (local_counts.get(base_name(p), 0), random.random()))

        # 4의 배수로 맞추기
        while len(pool) % 4 != 0:
            cands = [p for p in avail if p not in pool]
            cands = sort_by_mixed_least(cands, mixed_counts)
            if not cands:
                pool = pool[: (len(pool) // 4) * 4]
                break
            pool.append(cands.pop(0))

        if len(pool) < 4:
            break

        groups, leftovers = build_all_groups(pool, mixed_counts)

        if leftovers:
            cands = [p for p in avail if p not in leftovers and p not in pool]
            cands = sort_by_mixed_least(cands, mixed_counts)
            while len(leftovers) < 4 and cands:
                leftovers.append(cands.pop(0))
            if len(leftovers) >= 4:
                eg, _ = build_all_groups(leftovers, mixed_counts)
                groups.extend(eg)

        if not groups:
            break

        for g in groups:
            tagged = []
            for p in g:
                pn = base_name(p)
                tagged.append(pn + "(중복)" if local_counts.get(pn, 0) >= min_games else pn)
                local_counts[pn] = local_counts.get(pn, 0) + 1
            all_groups.append((g, tagged))

    return all_groups


# ============================================================
# 섹션 7: 통계 업데이트
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
        if is_mixed_match(match_type):
            s.mixed_count += 1
        dup    = "(중복)" in p_raw
        record = match_type + ("★" if dup else "")
        s.round_records[round_name] = record


# ============================================================
# 섹션 8: 리그 스케줄 생성 (league_players dict 입력)
# ============================================================

def generate_schedule_from_leagues(
    league_players: Dict[str, List[str]],
    num_rounds: int = 3,
) -> Tuple[List[dict], Dict[str, PlayerStats]]:
    """
    league_players = {"A리그": [...], "B리그": [...]}
    각 리그별로 정규 라운드(num_rounds) + 이벤트 라운드(4R) 진행.
    """
    all_results: List[dict] = []
    all_stats:   Dict[str, PlayerStats] = {}

    for league_name, players in league_players.items():
        if len(players) < 4:
            continue

        game_counts:  Dict[str, int] = {p: 0 for p in players}
        mixed_counts: Dict[str, int] = {p: 0 for p in players}
        gs = MatchState()

        # 정규 라운드
        for r in range(1, num_rounds + 1):
            rname = f"{r}R"
            rs    = MatchState()
            matches = make_round_matches(players, game_counts, mixed_counts, gs, rs)
            for m in matches:
                t1, t2, mt = m["team1"], m["team2"], m["type"]
                for p_raw in list(t1) + list(t2):
                    p = base_name(p_raw)
                    game_counts[p] += 1
                    if is_mixed_match(mt): mixed_counts[p] += 1
                update_stats(all_stats, t1, t2, mt, rname, league_name)
                all_results.append({
                    "round": rname, "league": league_name,
                    "team1": t1,    "team2": t2,    "type": mt,
                })

        # 이벤트 라운드 (4R)
        rs = MatchState()
        for raw_g, tagged_g in build_event_round(players, game_counts, mixed_counts):
            random.shuffle(tagged_g)
            t1, t2 = best_pairing(tagged_g, gs, rs)
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
# 섹션 9: 입력 파싱 유틸리티 (자유 이름 입력)
# ============================================================

def parse_custom_players(text: str, league_prefix: str) -> List[str]:
    """
    자유 입력 파싱.
    각 줄: "이름 성별"  (성별: 남/M 또는 여/W/F, 생략 시 남자 처리)
    내부 코드: "{리그prefix}{M|W}{이름}"  → get_gender이 c[1]을 읽으므로 정상 동작
    예) "홍길동 남" → "AM홍길동"
    """
    players = []
    for line in text.strip().splitlines():
        parts = line.strip().split()
        if not parts:
            continue
        name   = parts[0]
        gender = "M"
        if len(parts) >= 2:
            g = parts[1].upper()
            if g in ("여", "W", "F"):
                gender = "W"
        players.append(f"{league_prefix}{gender}{name}")
    return players


# ============================================================
# 섹션 10: Streamlit UI
# ============================================================

st.set_page_config(page_title="TELA Tennis Match", page_icon="🎾", layout="wide")

st.title("🎾 TELA CLUB Random Match Generator v2.02")
st.markdown(
    "**매치 우선순위:** 동성복(남복/여복) → 혼복(2M+2W) → 잡복(3:1) &nbsp;|&nbsp; "
    "**혼성 자리 → 혼성 최소 참여자 우선** &nbsp;|&nbsp; "
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

# ── 모드 A: 코드 자동 생성 ──────────────────────────────────
if input_mode == "코드 자동 생성 (AM01/AW01...)":
    c1, c2 = st.sidebar.columns(2)
    with c1:
        am = st.number_input("A리그 남자", min_value=0, max_value=30, value=8,  step=1)
        aw = st.number_input("A리그 여자", min_value=0, max_value=30, value=2,  step=1)
    with c2:
        bm = st.number_input("B리그 남자", min_value=0, max_value=30, value=3,  step=1)
        bw = st.number_input("B리그 여자", min_value=0, max_value=30, value=2,  step=1)
    custom_input = None

# ── 모드 B: 직접 이름 입력 ──────────────────────────────────
else:
    st.sidebar.markdown(
        "각 줄에 `이름 성별` 입력  \n"
        "성별: `남` 또는 `여`  \n"
        "성별 생략 시 남자로 처리  \n"
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

    # ── 선수 목록 확정 ──────────────────────────────────────
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
        if 0 < len(pl) < 4:
            errors.append(f"{lg} 인원이 4명 미만입니다.")
    if not any(len(pl) >= 4 for pl in league_players.values()):
        errors.append("최소 한 리그에 4명 이상 입력해주세요.")

    if errors:
        for e in errors: st.error(e)
        st.stop()

    if use_seed and seed_val is not None:
        random.seed(int(seed_val))

    with st.spinner("대진표 생성 중..."):
        schedule, stats = generate_schedule_from_leagues(league_players)

    if not schedule:
        st.warning("경기를 생성할 수 없습니다. 인원을 확인해주세요.")
        st.stop()

    # ── 시드 표시 레이블 ────────────────────────────────────
    seed_label = f"시드 #{int(seed_val)}" if (use_seed and seed_val is not None) else "시드 없음(랜덤)"

    # ── DataFrame 구성 ──────────────────────────────────────
    def dn(code: str) -> str:
        return display_name(code)

    df_matches = pd.DataFrame([{
        "라운드":   d["round"],
        "리그":     d["league"],
        "팀1-A":    dn(d["team1"][0]),
        "팀1-B":    dn(d["team1"][1]),
        "팀2-A":    dn(d["team2"][0]),
        "팀2-B":    dn(d["team2"][1]),
        "매치종류": d["type"],
    } for d in schedule])

    # stats DataFrame: 코드 기반 정렬 후 표시용 이름으로 변환
    stats_rows = []
    for code, s in stats.items():
        stats_rows.append({
            "_코드":       code,
            "리그":        s.league,
            "이름":        dn(code),
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
    df_stats_full    = pd.DataFrame(stats_rows).sort_values(["리그", "_코드"]).reset_index(drop=True)
    df_stats_display = df_stats_full.drop(columns=["_코드"])

    # ── 탭 ──────────────────────────────────────────────────
    tab1, tab2, tab3 = st.tabs(["📋 대진표", "📊 출전 현황", "🔍 검증 리포트"])

    # 탭1: 대진표
    with tab1:
        st.subheader(f"경기 대진표 · {seed_label}")

        def hl_match(row):
            bg = ("#E8F5E9" if "A리그" in str(row.get("리그","")) else "#E3F2FD")
            return [f"background-color:{bg};color:black"] * len(row)

        st.dataframe(df_matches.style.apply(hl_match, axis=1),
                     use_container_width=True, height=600)
        summary = df_matches["매치종류"].value_counts()
        st.caption(f"총 {len(df_matches)}경기 | " +
                   " | ".join(f"{k}: {v}경기" for k,v in summary.items()))

    # 탭2: 출전 현황
    with tab2:
        st.subheader("선수별 출전 현황")

        palette = {
            "AM": "#E8F5E9",  # A남: 연두
            "AW": "#FCE4EC",  # A여: 연분홍
            "BM": "#E3F2FD",  # B남: 연파랑
            "BW": "#FFF3E0",  # B여: 연주황
        }

        def hl_stats(row):
            code  = df_stats_full.loc[row.name, "_코드"] if "_코드" in df_stats_full.columns else ""
            bg    = palette.get(code[:2], "")
            base  = f"background-color:{bg};color:black" if bg else ""
            styles = [base] * len(row)
            if "총경기" in row.index:
                idx   = row.index.get_loc("총경기")
                total = row["총경기"]
                if total >= 4:
                    styles[idx] = "background-color:#FFF176;color:black;font-weight:bold"
                elif total < 3:
                    styles[idx] = "background-color:#FFCDD2;color:black;font-weight:bold"
                else:
                    styles[idx] = base + ";font-weight:bold"
            return styles

        st.dataframe(df_stats_display.style.apply(hl_stats, axis=1),
                     use_container_width=True, height=700)

    # 탭3: 검증 리포트
    with tab3:
        st.subheader("🔍 자동 검증 리포트")
        issues, warns = [], []

        if not df_stats_full.empty:
            under3 = df_stats_full[df_stats_full["총경기"] < 3]
            if not under3.empty:
                issues.append(f"❌ 3경기 미달 선수 {len(under3)}명: {', '.join(under3['이름'].tolist())}")
            else:
                st.success("✅ 모든 선수 3경기 이상 출전")

            over4 = df_stats_full[df_stats_full["총경기"] > 4]
            if not over4.empty:
                issues.append(f"❌ 4경기 초과 선수 {len(over4)}명: {', '.join(over4['이름'].tolist())}")
            else:
                st.success("✅ 4경기 초과 선수 없음")

            st.markdown("**매치 종류 분포**")
            td = df_matches["매치종류"].value_counts().reset_index()
            td.columns = ["매치종류", "경기수"]
            st.dataframe(td, use_container_width=False)

            if len(df_stats_full) > 1:
                std_m  = df_stats_full["혼성합계"].std()
                mean_m = df_stats_full["혼성합계"].mean()
                if std_m > 1.5:
                    warns.append(f"⚠️ 혼성 경기 편차 큼 (평균 {mean_m:.1f}회, 표준편차 {std_m:.2f})")
                else:
                    st.success(f"✅ 혼성 균등 분배 (평균 {mean_m:.1f}회, σ={std_m:.2f})")

        for i in issues: st.error(i)
        for w in warns:  st.warning(w)
        if not issues and not warns:
            st.info("🎾 모든 검증 통과!")

    # ── 엑셀 다운로드 ───────────────────────────────────────
    excel_seed_tag = f"_시드{int(seed_val)}" if (use_seed and seed_val is not None) else "_랜덤"
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
        df_matches.to_excel(      writer, sheet_name="대진표",   index=False)
        df_stats_display.to_excel(writer, sheet_name="출전현황", index=False)
        for sn in ["대진표", "출전현황"]:
            writer.sheets[sn].set_column("A:Z", 14)

    st.sidebar.markdown("---")
    st.sidebar.download_button(
        label="📥 엑셀 다운로드",
        data=buf.getvalue(),
        file_name=f"TELA_대진표{excel_seed_tag}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )

else:
    st.info("👈 사이드바에서 인원을 설정하고 **대진표 생성** 버튼을 눌러주세요.")

    with st.expander("📖 사용 방법 및 규칙 안내"):
        st.markdown("""
        ### 입력 방식
        | 방식 | 설명 | 예시 |
        |------|------|------|
        | 코드 자동 생성 | 인원 수만 입력 | A리그 남 8명 → AM01~AM08 |
        | 직접 이름 입력 | `이름 성별` 형식으로 줄마다 입력 | `홍길동 남` / `김영희 여` |

        ### 매치 구성 우선순위
        | 순위 | 종류 | 조건 |
        |------|------|------|
        | 1 | 남복 / 여복 | 동성 4명 |
        | 2 | 혼복 | 남2 + 여2 |
        | 3 | 잡복 | 남3 + 여1 또는 남1 + 여3 |

        ### 출전 규칙
        - **최소 3경기** 보장 → 이벤트 라운드(4R)에서 보충
        - **최대 4경기** 제한 → 중복 출전 1회 허용
        - 혼성 자리 발생 시 → **혼성 경기 최소 참여자 우선** 선발

        ### 표시 기호
        - `★` / `(중복)` : 이벤트 라운드(4R) 중복 출전 선수
        - 총경기 **노란색** : 4경기 (이벤트 포함)
        - 총경기 **빨간색** : 3경기 미만 (이상 감지)
        """)
