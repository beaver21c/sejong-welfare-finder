"""
🏥 세종시 복지기관 안내 서비스 (BYOK · 퍼블릭)

- 방문자가 본인의 API 키(Gemini + 카카오/브이월드)를 직접 입력해 사용합니다.
- 키는 브라우저 세션에만 보관되며 서버/저장소에 저장되지 않습니다.
- Streamlit Community Cloud 등에 배포하면 하나의 URL로 바로 사용할 수 있습니다.

핵심 로직은 core.py(Streamlit 비의존)에 분리돼 있습니다.
"""

import json

import folium
import streamlit as st
import streamlit.components.v1 as components

import core

# ============================================================
# 페이지 설정
# ============================================================
st.set_page_config(
    page_title="세종시 복지기관 안내",
    page_icon="🏥",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ============================================================
# 세션 기본값 (BYOK: 키는 세션에만 보관)
# ============================================================
def _secret(name: str, default: str = "") -> str:
    """배포자가 st.secrets/환경변수로 기본값을 미리 채워둘 수 있음(선택)."""
    try:
        if name in st.secrets:
            return str(st.secrets[name])
    except Exception:
        pass
    import os

    return os.environ.get(name, default)


DEFAULTS = {
    "gemini_key": _secret("GEMINI_API_KEY"),
    "kakao_key": _secret("KAKAO_REST_API_KEY", _secret("KAKAO_API_KEY")),
    "vworld_key": _secret("VWORLD_API_KEY"),
    "provider": "kakao",
    "use_road": False,  # 기본 꺼짐: 앱을 즉시 띄우고, 사용자가 켤 때만 도로망 로딩
    "last_answer": None,
    "last_map_html": None,
    "last_results": None,
    "last_routing_mode": None,
}
for k, v in DEFAULTS.items():
    st.session_state.setdefault(k, v)


# ============================================================
# 데이터 로드 (캐시)
# ============================================================
@st.cache_data(show_spinner="📋 기관 데이터 로딩 중...")
def get_facilities():
    return core.load_facilities()


@st.cache_resource(show_spinner=False)
def get_road_network():
    """OSMnx 도로망(속도/소요시간 포함). 실패하면 None → 직선거리로 폴백.

    진행 상태는 호출부의 st.status 로 명시적으로 표시하므로 기본 스피너는 끔.
    """
    try:
        return core.build_road_network()
    except Exception as e:  # noqa: BLE001
        st.session_state["road_error"] = str(e)
        return None


facilities = get_facilities()


# ============================================================
# Gemini 헬퍼 (키 없으면 규칙 기반으로 자동 폴백)
# ============================================================
def gemini_call(prompt: str, model: str = "gemini-2.5-flash"):
    key = st.session_state.get("gemini_key", "").strip()
    if not key:
        return None
    try:
        from google import genai

        client = genai.Client(api_key=key)
        resp = client.models.generate_content(model=model, contents=prompt)
        return resp.text
    except Exception:
        return None


def _parse_json(text):
    if not text:
        return None
    try:
        t = text.strip().replace("```json", "").replace("```", "").strip()
        return json.loads(t)
    except Exception:
        return None


# 세종시가 아닌 것이 명확한 주요 지역 키워드 (규칙 기반 판정용)
_OTHER_REGIONS = (
    "서울", "부산", "대구", "인천", "광주", "대전", "울산", "경기", "강원",
    "충북", "충남", "전북", "전남", "경북", "경남", "제주", "천안", "청주", "공주",
)


def extract_address(user_message: str) -> dict:
    """질문에서 주소/동 추출. Gemini 있으면 사용, 없으면 규칙 기반."""
    data = _parse_json(
        gemini_call(
            "다음 문장에서 사용자의 거주지 주소를 추출해 JSON만 출력.\n"
            f"문장: {user_message}\n"
            '출력: {"address":"세종시 포함 주소","dong":"읍면동명","is_sejong":true/false}'
        )
    )
    if isinstance(data, dict) and (data.get("address") or data.get("dong")):
        return data

    # 규칙 기반 폴백: 위치가 불명확하면 세종시로 간주(세종 전용 서비스).
    # 명확한 '타 지역' 키워드가 있고 '세종'이 없을 때만 지역 밖으로 판정.
    dong = next((d for d in core.DONG_CENTERS if d in user_message), "")
    is_other = ("세종" not in user_message) and any(r in user_message for r in _OTHER_REGIONS)
    is_sejong = not is_other
    if "세종" in user_message:
        address = user_message
    elif dong:
        address = f"세종특별자치시 {dong}"
    else:
        address = user_message  # 위치 불명확 → 이후 세종시 중심 폴백
    return {"address": address, "dong": dong, "is_sejong": is_sejong}


def refine_match_with_gemini(user_message, facilities_subset, base_message):
    """키워드 매칭이 없을 때 Gemini로 시설유형 추정(선택)."""
    groups = sorted({f["group"] for f in facilities_subset})
    types = sorted({f["type"] for f in facilities_subset})
    data = _parse_json(
        gemini_call(
            "사용자가 원하는 복지서비스 분류를 판단해 JSON만 출력.\n"
            f"질문: {user_message}\n"
            f"가능한 group: {groups}\n가능한 시설유형: {types}\n"
            '출력: {"group":"노인|아동|장애인|기타|","type_keyword":"시설유형 일부 또는 빈문자열","confidence":"high|medium|low"}'
        )
    )
    if not isinstance(data, dict) or data.get("confidence") == "low":
        return facilities_subset, base_message
    g = data.get("group", "")
    tk = data.get("type_keyword", "")
    filtered = facilities_subset
    if g:
        cand = [f for f in filtered if f["group"] == g]
        if cand:
            filtered = cand
    if tk:
        cand = [f for f in filtered if tk in f["type"] or tk in f["kind"]]
        if cand:
            filtered = cand
    return filtered, f"AI 분석으로 '{g or '전체'}' 분류 추정"


def generate_answer(user_message, results, address_info, geo_status, match_note):
    lines = []
    for i, r in enumerate(results, 1):
        t = f"차량 약 {r['travel_time_min']}분 / " if r.get("travel_time_min") is not None else ""
        lines.append(
            f"{i}. {r['name']} ({r['type']})\n   주소: {r['address']}\n   {t}거리 {r['distance_km']}km"
        )
    results_text = "\n".join(lines) if results else "(검색 결과 없음)"

    ai = gemini_call(
        "당신은 세종시 복지기관 안내 AI입니다. 아래 정보로 친절한 한국어 안내문을 작성하세요.\n"
        f"[질문] {user_message}\n"
        f"[사용자 거주지] {address_info.get('address','')}\n"
        f"[주소 처리] {geo_status}\n[기관 매칭] {match_note}\n[결과]\n{results_text}\n"
        "[규칙]\n- 가까운 순으로 기관명·분류·주소·소요시간(있으면) 안내\n"
        "- fallback_dong/fallback_city면 '근사 위치 기준'임을 언급\n"
        "- 소요시간은 추정치임을 언급\n- 마지막 줄: '※ 방문 전 운영시간을 꼭 확인하세요.'"
    )
    if ai:
        return ai

    # Gemini 없을 때 템플릿 응답
    if not results:
        return "검색 결과가 없습니다. 세종시 내 주소와 원하는 서비스를 다시 확인해주세요."
    head = "📍 가까운 복지기관 안내입니다.\n\n"
    if geo_status in ("fallback_dong", "fallback_city"):
        head = "📍 (근사 위치 기준) 가까운 복지기관 안내입니다.\n\n"
    body = "\n".join(
        f"**{i}. {r['name']}** ({r['type']})\n"
        f"- 주소: {r['address']}\n"
        + (f"- 차량 약 {r['travel_time_min']}분 / " if r.get("travel_time_min") is not None else "- ")
        + f"거리 {r['distance_km']}km"
        for i, r in enumerate(results, 1)
    )
    return head + body + "\n\n※ 방문 전 운영시간을 꼭 확인하세요."


# ============================================================
# 검색 파이프라인
# ============================================================
def search(user_message: str, status=None):
    provider = st.session_state["provider"]
    keys = {"kakao": st.session_state["kakao_key"], "vworld": st.session_state["vworld_key"]}

    # 1) 주소 추출
    if status is not None:
        status.write("📍 주소 확인 중…")
    addr_info = extract_address(user_message)
    if addr_info.get("is_sejong") is False and "세종" not in addr_info.get("address", ""):
        return {
            "answer": f"⚠️ 본 서비스는 **세종특별자치시**만 지원합니다.\n\n입력 주소 '{addr_info.get('address','')}'는 서비스 지역 밖입니다.",
            "map_html": None,
            "results": None,
        }

    address_str = addr_info.get("address") or (f"세종특별자치시 {addr_info.get('dong','')}")

    # 2) 지오코딩
    lat, lng, geo_status, geo_msg = core.geocode(address_str, provider=provider, keys=keys)
    if lat is None:
        if geo_status == "out_of_area":
            return {
                "answer": "⚠️ 입력하신 주소가 **세종시 범위 밖**입니다. 본 서비스는 세종특별자치시만 지원합니다.",
                "map_html": None,
                "results": None,
            }
        # 위치가 불명확한 경우: 세종시 중심 기준으로 안내(정확도 위해 동/주소 입력 권장)
        lat, lng = core.SEJONG_CENTER
        geo_status = "fallback_city"

    # 3) 시설유형 매칭 (키워드 → 없으면 Gemini 보정)
    filtered, matched_kw, mstatus = core.match_by_keyword(user_message, facilities)
    if mstatus == "no_keyword":
        filtered, match_note = refine_match_with_gemini(user_message, facilities, "전체 대상 검색")
        if filtered is facilities:
            match_note = "특정 유형이 감지되지 않아 전체 기관 대상"
    else:
        match_note = f"'{', '.join(matched_kw)}' 관련 {len(filtered)}개"

    if not filtered:
        return {
            "answer": "해당 유형의 기관이 세종시 데이터에 없습니다. 다른 키워드로 검색해보세요.",
            "map_html": None,
            "results": None,
        }

    # 4) 거리 랭킹 (직선거리로 후보 압축 → 도로망 켜졌으면 정밀 재계산)
    candidates = core.rank_by_distance(lat, lng, filtered, top_n=8)

    # 도로망 준비(사용자가 켰을 때만). 진행/성공/실패를 status로 명시.
    graph, routing_mode = _prepare_graph(status)
    results = _apply_road_routing(lat, lng, candidates, graph)[:5]

    # 5) 안내문 + 지도
    answer = generate_answer(user_message, results, addr_info, geo_status, match_note)
    if routing_mode == "road_failed":
        answer = "⚠️ 도로망을 불러오지 못해 **직선거리 기준**으로 안내합니다.\n\n" + answer
    map_html = build_map(lat, lng, results)
    return {"answer": answer, "map_html": map_html, "results": results, "routing_mode": routing_mode}


def _prepare_graph(status=None):
    """도로망 토글이 켜져 있으면 그래프를 로딩하고 상태를 표시.

    반환: (graph|None, routing_mode)
      routing_mode ∈ {"straight", "road", "road_failed"}
    """
    use_road = st.session_state.get("use_road", False) and core.osmnx_available()
    if not use_road:
        return None, "straight"

    if status is not None:
        status.write("🗺️ 도로망 불러오는 중… (최초 1~3분, **그대로 기다려 주세요**)")
    graph = get_road_network()
    if graph is None:
        if status is not None:
            err = st.session_state.get("road_error", "")
            status.write(f"⚠️ 도로망 로딩 실패 → 직선거리로 안내합니다. {('('+err[:80]+')') if err else ''}")
        return None, "road_failed"

    if status is not None:
        status.write(f"✅ 도로망 준비 완료 (노드 {len(graph.nodes):,}개). 경로 계산 중…")
    return graph, "road"


def _apply_road_routing(lat, lng, candidates, graph):
    """graph가 있으면 도로 소요시간/경로로 재정렬, 없으면 직선거리 유지."""
    for r in candidates:
        if graph is not None:
            dist, mins, route = core.road_route(graph, lat, lng, r["lat"], r["lng"])
            if dist is not None:
                r["distance_m"] = dist
                r["distance_km"] = round(dist / 1000, 1)
                r["travel_time_min"] = mins
                r["route"] = route
                continue
        r.setdefault("travel_time_min", None)
        r.setdefault("route", None)

    # 도로 소요시간이 있으면 그 기준, 아니면 직선거리 기준 정렬
    candidates.sort(key=lambda x: (x["travel_time_min"] is None, x["travel_time_min"] or x["distance_m"]))
    return candidates


def build_map(user_lat, user_lng, results):
    m = folium.Map(location=[user_lat, user_lng], zoom_start=13, tiles="OpenStreetMap")

    # V-World 위성/일반 타일(키 있을 때, 선택). 실패해도 OSM 기본 타일로 표시됨.
    vkey = st.session_state.get("vworld_key", "").strip()
    if vkey:
        try:
            folium.TileLayer(
                tiles=f"https://api.vworld.kr/req/wmts/1.0.0/{vkey}/Base/{{z}}/{{y}}/{{x}}.png",
                attr="V-World",
                name="브이월드 일반",
                overlay=False,
            ).add_to(m)
        except Exception:
            pass

    folium.Marker(
        [user_lat, user_lng],
        popup="📍 내 위치",
        icon=folium.Icon(color="red", icon="home", prefix="fa"),
    ).add_to(m)

    colors = ["blue", "green", "purple", "orange", "darkred"]
    for i, r in enumerate(results):
        folium.Marker(
            [r["lat"], r["lng"]],
            popup=folium.Popup(
                f"<b>{r['name']}</b><br>{r['type']}<br>{r['address']}<br>거리 {r['distance_km']}km"
                + (f"<br>차량 약 {r['travel_time_min']}분" if r.get("travel_time_min") is not None else ""),
                max_width=260,
            ),
            icon=folium.Icon(color=colors[i % len(colors)], icon="building", prefix="fa"),
        ).add_to(m)
        if r.get("route"):
            try:
                folium.PolyLine(r["route"], weight=4, color=colors[i % len(colors)], opacity=0.7).add_to(m)
            except Exception:
                pass

    folium.LayerControl(collapsed=True).add_to(m)
    return m._repr_html_()


# ============================================================
# 사이드바 — API 키 설정(BYOK) + 현황
# ============================================================
with st.sidebar:
    st.header("🔑 API 키 설정")
    st.caption("입력한 키는 **브라우저 세션에만** 저장되며 서버/저장소에 남지 않습니다.")

    st.session_state["gemini_key"] = st.text_input(
        "Gemini API 키",
        value=st.session_state["gemini_key"],
        type="password",
        help="Google AI Studio에서 무료 발급: https://aistudio.google.com/apikey",
        placeholder="AIza...",
    )

    st.session_state["provider"] = st.radio(
        "지오코딩(주소→좌표) 제공자",
        options=["kakao", "vworld", "nominatim"],
        format_func=lambda x: {"kakao": "카카오맵", "vworld": "브이월드(V-World)", "nominatim": "무료(Nominatim·키 불필요)"}[x],
        index=["kakao", "vworld", "nominatim"].index(st.session_state["provider"]),
    )

    if st.session_state["provider"] == "kakao":
        st.session_state["kakao_key"] = st.text_input(
            "카카오 REST API 키",
            value=st.session_state["kakao_key"],
            type="password",
            help="Kakao Developers > 내 애플리케이션 > REST API 키. https://developers.kakao.com",
        )
    elif st.session_state["provider"] == "vworld":
        st.session_state["vworld_key"] = st.text_input(
            "브이월드 인증키",
            value=st.session_state["vworld_key"],
            type="password",
            help="V-World > 인증키 발급(오픈API). https://www.vworld.kr/dev/v4api.do",
        )

    road_ok = core.osmnx_available()
    st.session_state["use_road"] = st.toggle(
        "🚗 도로 소요시간 사용 (선택)",
        value=st.session_state["use_road"] and road_ok,
        disabled=not road_ok,
        help=(
            "끄면(기본) 직선거리(km)로 즉시 안내합니다. 켜면 도로망으로 '차량 N분'을 계산하는데, "
            "켠 뒤 첫 검색에서 도로망을 불러오느라 최초 1~3분 걸립니다"
            "(sejong_drive.graphml 을 커밋해두면 수 초). 실패 시 직선거리로 자동 전환됩니다."
        ),
    )
    if not road_ok:
        st.caption("ℹ️ 현재 환경엔 OSMnx가 없어 **직선거리** 기준으로 안내합니다.")
    elif st.session_state["use_road"]:
        st.caption("⏳ 켜짐: 첫 검색에서 도로망 로딩(최초 1~3분)이 있습니다.")
    else:
        st.caption("⚡ 기본(직선거리) — 즉시 안내. 도로 소요시간이 필요할 때만 위 스위치를 켜세요.")

    st.divider()
    st.header("📊 데이터 현황")
    st.metric("등록 기관", f"{len(facilities)}개")
    groups = {}
    for f in facilities:
        groups[f["group"]] = groups.get(f["group"], 0) + 1
    st.write(" · ".join(f"{g} {c}" for g, c in sorted(groups.items(), key=lambda x: -x[1])))
    st.caption("ℹ️ 세종특별자치시 지역만 지원합니다. 소요시간·거리는 추정치입니다.")


# ============================================================
# 메인 — 소개 + 온보딩
# ============================================================
st.title("🏥 세종시 복지기관 안내 서비스")
st.caption("주소와 필요한 서비스를 입력하면 가장 가까운 복지기관을 안내합니다. 본인의 API 키로 바로 사용하세요.")

need_keys = not st.session_state["gemini_key"].strip()
provider = st.session_state["provider"]
if provider == "kakao" and not st.session_state["kakao_key"].strip():
    need_keys = True
if provider == "vworld" and not st.session_state["vworld_key"].strip():
    need_keys = True

if need_keys:
    with st.container(border=True):
        st.subheader("👋 시작하기 (키 입력 후 바로 사용)")
        st.markdown(
            "1. **Gemini API 키** — [Google AI Studio](https://aistudio.google.com/apikey)에서 무료 발급 "
            "*(키가 없어도 규칙 기반으로 동작하지만, 자연어 안내는 제한됩니다.)*\n"
            "2. **지도/주소 검색 키** — 왼쪽에서 제공자를 고르고 키를 입력하세요.\n"
            "   - 카카오: [Kakao Developers](https://developers.kakao.com) REST API 키\n"
            "   - 브이월드: [V-World 오픈API](https://www.vworld.kr/dev/v4api.do) 인증키\n"
            "   - 또는 **무료(Nominatim)** 선택 시 키 없이 사용 가능(정확도는 다소 낮음)\n\n"
            "왼쪽 사이드바(◀)에서 키를 입력하면 아래 검색창이 활성화됩니다."
        )

# 입력 폼
with st.form("query_form", clear_on_submit=False):
    user_input = st.text_input(
        "💬 질문을 입력하세요",
        placeholder="예) 세종시 한솔동에 사는데 아동돌봄 기관 알려줘",
    )
    submitted = st.form_submit_button("🔍 검색", use_container_width=True, type="primary")

if submitted and user_input.strip():
    road_on = st.session_state.get("use_road", False) and core.osmnx_available()
    label = "🔍 검색 중… (도로망 사용: 최초 1~3분 소요될 수 있음)" if road_on else "🔍 검색 중…"
    with st.status(label, expanded=True) as status:
        try:
            out = search(user_input.strip(), status=status)
            st.session_state["last_answer"] = out["answer"]
            st.session_state["last_map_html"] = out["map_html"]
            st.session_state["last_results"] = out["results"]
            st.session_state["last_routing_mode"] = out.get("routing_mode", "straight")
            mode = st.session_state["last_routing_mode"]
            done = {
                "road": "✅ 완료 — 도로 경로 기준",
                "road_failed": "✅ 완료 — 직선거리 기준(도로망 로딩 실패)",
                "straight": "✅ 완료 — 직선거리 기준",
            }.get(mode, "✅ 완료")
            status.update(label=done, state="complete", expanded=False)
        except Exception as e:  # noqa: BLE001
            st.session_state["last_answer"] = f"⚠️ 처리 중 오류가 발생했습니다: {e}"
            st.session_state["last_map_html"] = None
            st.session_state["last_results"] = None
            st.session_state["last_routing_mode"] = None
            status.update(label=f"⚠️ 오류: {e}", state="error")

# 결과 표시
if st.session_state.get("last_answer"):
    st.divider()
    st.subheader("🤖 안내 결과")

    mode = st.session_state.get("last_routing_mode")
    if mode == "road":
        st.success("🚗 **도로 경로 기준** — 지도에 실제 이동 경로선이 표시됩니다.")
    elif mode == "road_failed":
        st.warning("📏 **직선거리 기준** — 도로망을 불러오지 못했습니다. 사이드바에서 다시 시도하거나 잠시 후 재검색하세요.")
    elif mode == "straight":
        st.info("📏 **직선거리 기준** — 경로선이 필요하면 사이드바 '🚗 도로 소요시간 사용'을 켜고 검색하세요.")

    st.markdown(st.session_state["last_answer"])

    if st.session_state.get("last_results"):
        with st.expander("📋 표로 보기"):
            st.dataframe(
                [
                    {
                        "기관명": r["name"],
                        "분류": r["type"],
                        "거리(km)": r["distance_km"],
                        "차량(분)": r.get("travel_time_min", "—"),
                        "주소": r["address"],
                    }
                    for r in st.session_state["last_results"]
                ],
                use_container_width=True,
                hide_index=True,
            )

    if st.session_state.get("last_map_html"):
        st.subheader("🗺️ 지도")
        components.html(st.session_state["last_map_html"], height=520)

st.divider()
st.caption(
    "본 서비스는 오픈소스입니다. 각자 발급한 API 키로 동작하며 키는 저장되지 않습니다. "
    "데이터: 세종특별자치시 복지기관 목록."
)
