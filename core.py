"""
세종시 복지기관 안내 서비스 - 핵심 로직 (Streamlit 비의존)

이 모듈은 Streamlit UI와 분리된 순수 로직만 담습니다.
- 데이터 로드 / 거리 계산 / 세종시 경계·동 중심
- 지오코딩: 카카오, 브이월드, Nominatim (모두 서버사이드 REST → CORS 무관)
- 키워드 기반 시설유형 매칭 (오프라인)
- 거리 랭킹 + (선택적) OSMnx 도로망 라우팅

외부 API 호출은 모두 방어적으로 처리하여 실패 시 (None, None) 또는 폴백을 반환합니다.
"""

from __future__ import annotations

import json
import math
import os
from typing import Optional

import requests

# ------------------------------------------------------------------
# 세종특별자치시 경계 / 중심 (facilities 데이터 기준으로 산출한 정확한 값)
#   실제 기관 좌표 범위: lat 36.44~36.72, lng 127.16~127.40
#   → 여유를 둔 경계값 사용 (연서면·장군면 서부, 부강면 동부 포함)
# ------------------------------------------------------------------
SEJONG_BOUNDS = {
    "lat_min": 36.40,
    "lat_max": 36.75,
    "lng_min": 127.10,
    "lng_max": 127.45,
}

# 세종시 지리적 중심(정부세종청사 인근)
SEJONG_CENTER = (36.4801, 127.2890)

# facilities.json에서 산출한 읍면동 중심 좌표 (지오코딩 최종 폴백용)
# 원본 코드의 값은 경도가 약 0.25도(~20km) 서쪽으로 잘못돼 있어 데이터 기반으로 교체함.
DONG_CENTERS = {
    "조치원읍": (36.59907, 127.29807),
    "도담동": (36.50982, 127.25464),
    "전동면": (36.63320, 127.23570),
    "연서면": (36.58221, 127.24969),
    "부강면": (36.52628, 127.37512),
    "고운동": (36.51312, 127.23500),
    "아름동": (36.51121, 127.24400),
    "금남면": (36.45369, 127.27984),
    "나성동": (36.48704, 127.26078),
    "전의면": (36.67639, 127.20044),
    "대평동": (36.47350, 127.27701),
    "장군면": (36.51686, 127.21474),
    "보람동": (36.47878, 127.29114),
    "한솔동": (36.49346, 127.25933),
    "반곡동": (36.49857, 127.31284),
    "종촌동": (36.50433, 127.24859),
    "새롬동": (36.48555, 127.25536),
    "소담동": (36.48464, 127.30063),
    "다정동": (36.49427, 127.25022),
    "소정면": (36.71976, 127.15718),
    "어진동": (36.50188, 127.25623),
    "해밀동": (36.53252, 127.26434),
    "연동면": (36.55118, 127.32084),
    "연기면": (36.54502, 127.27815),
}


def is_within_sejong(lat: float, lng: float) -> bool:
    """좌표가 세종시 경계 안인지 판정."""
    return (
        SEJONG_BOUNDS["lat_min"] <= lat <= SEJONG_BOUNDS["lat_max"]
        and SEJONG_BOUNDS["lng_min"] <= lng <= SEJONG_BOUNDS["lng_max"]
    )


# ------------------------------------------------------------------
# 데이터 로드
# ------------------------------------------------------------------
def load_facilities(base_dir: Optional[str] = None) -> list[dict]:
    """복지기관 데이터 로드.

    1순위: facilities.json (정제된 UTF-8)
    2순위: facilities.csv (인코딩 자동 감지)
    반환: [{name, group, type, kind, dong, sigungu, address, lat, lng}, ...]
    """
    base_dir = base_dir or os.path.dirname(os.path.abspath(__file__))

    json_path = os.path.join(base_dir, "facilities.json")
    if os.path.exists(json_path):
        try:
            with open(json_path, encoding="utf-8") as f:
                data = json.load(f)
            return [f for f in data if is_within_sejong(f.get("lat", 0), f.get("lng", 0))]
        except Exception:
            pass

    # CSV 폴백
    csv_path = os.path.join(base_dir, "facilities.csv")
    return _load_facilities_from_csv(csv_path)


_GROUP_MAP = {
    "노인여가복지시설": "노인",
    "재가노인복지시설": "노인",
    "노인의료복지시설": "노인",
    "노인주거복지시설": "노인",
    "다함께돌봄센터": "아동",
    "아동복지시설": "아동",
    "어린이집": "아동",
    "장애인거주시설": "장애인",
    "장애인지역사회재활시설": "장애인",
    "정신재활시설": "장애인",
    "일반사회복지시설": "기타",
    "기타복지시설": "기타",
}


def _load_facilities_from_csv(csv_path: str) -> list[dict]:
    import csv

    if not os.path.exists(csv_path):
        return []

    rows = None
    for enc in ("utf-8-sig", "cp949", "euc-kr", "utf-8", "latin1"):
        try:
            with open(csv_path, encoding=enc) as f:
                rows = list(csv.DictReader(f))
            break
        except Exception:
            continue
    if not rows:
        return []

    def pick(row, *names):
        for n in names:
            for k in row:
                if k and k.strip().replace("﻿", "") == n:
                    return (row[k] or "").strip()
        return ""

    out = []
    for row in rows:
        try:
            lng = float(pick(row, "x축", "경도", "lng", "x").replace(",", ""))
            lat = float(pick(row, "y축", "위도", "lat", "y").replace(",", ""))
        except Exception:
            continue
        if not is_within_sejong(lat, lng):
            continue
        stype = pick(row, "시설유형", "유형")
        out.append(
            {
                "name": pick(row, "시설명", "기관명"),
                "group": _GROUP_MAP.get(stype, "기타"),
                "type": stype,
                "kind": pick(row, "시설종류", "종류"),
                "dong": pick(row, "행정동", "읍면동"),
                "sigungu": pick(row, "시군구"),
                "address": pick(row, "주소", "소재지"),
                "lat": lat,
                "lng": lng,
            }
        )
    return out


# ------------------------------------------------------------------
# 거리 계산
# ------------------------------------------------------------------
def haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """두 좌표 사이 직선거리(m)."""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def rank_by_distance(user_lat, user_lng, facilities, top_n=5) -> list[dict]:
    """직선거리 기준 가까운 기관 top_n. 각 항목에 distance_m/distance_km 부여."""
    scored = []
    for f in facilities:
        d = haversine(user_lat, user_lng, f["lat"], f["lng"])
        item = dict(f)
        item["distance_m"] = round(d)
        item["distance_km"] = round(d / 1000, 1)
        scored.append(item)
    scored.sort(key=lambda x: x["distance_m"])
    return scored[:top_n]


# ------------------------------------------------------------------
# 지오코딩 (모두 서버사이드 REST → 브라우저 CORS 문제 없음)
# ------------------------------------------------------------------
def geocode_kakao(address: str, rest_key: str, timeout: int = 8):
    """카카오 로컬 주소 검색. 실패 시 키워드(장소) 검색으로 재시도.

    REST(카카오 developers) 키 필요. 반환: (lat, lng) | (None, None)
    """
    if not rest_key or not address:
        return None, None
    headers = {"Authorization": f"KakaoAK {rest_key.strip()}"}
    try:
        # 1) 주소 검색
        r = requests.get(
            "https://dapi.kakao.com/v2/local/search/address.json",
            params={"query": address, "size": 1},
            headers=headers,
            timeout=timeout,
        )
        if r.status_code == 200:
            docs = r.json().get("documents", [])
            if docs:
                return float(docs[0]["y"]), float(docs[0]["x"])
        # 2) 키워드(장소) 검색 폴백
        r = requests.get(
            "https://dapi.kakao.com/v2/local/search/keyword.json",
            params={"query": address, "size": 1},
            headers=headers,
            timeout=timeout,
        )
        if r.status_code == 200:
            docs = r.json().get("documents", [])
            if docs:
                return float(docs[0]["y"]), float(docs[0]["x"])
    except Exception:
        pass
    return None, None


def geocode_vworld(address: str, key: str, timeout: int = 8):
    """브이월드(국토교통부) 지오코딩. 도로명(road) 실패 시 지번(parcel)으로 재시도.

    반환: (lat, lng) | (None, None)
    """
    if not key or not address:
        return None, None
    url = "https://api.vworld.kr/req/address"
    for addr_type in ("road", "parcel"):
        params = {
            "service": "address",
            "request": "getcoord",
            "version": "2.0",
            "crs": "epsg:4326",
            "address": address,
            "type": addr_type,
            "format": "json",
            "key": key.strip(),
        }
        try:
            r = requests.get(url, params=params, timeout=timeout)
            data = r.json()
            if data.get("response", {}).get("status") == "OK":
                point = data["response"]["result"]["point"]
                return float(point["y"]), float(point["x"])
        except Exception:
            continue
    return None, None


def geocode_nominatim(address: str, timeout: int = 10):
    """OpenStreetMap Nominatim 무료 지오코딩 (키 불필요). 반환: (lat, lng) | (None, None)"""
    if not address:
        return None, None
    try:
        r = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": address, "format": "json", "countrycodes": "kr", "limit": 1},
            headers={"User-Agent": "SejongWelfareFinder/2.0"},
            timeout=timeout,
        )
        data = r.json()
        if data:
            return float(data[0]["lat"]), float(data[0]["lon"])
    except Exception:
        pass
    return None, None


def _normalize_address(address: str) -> str:
    a = (address or "").strip()
    if a and "세종" in a and "세종특별자치시" not in a:
        a = a.replace("세종특별자치시", "").replace("세종시", "").strip()
        a = f"세종특별자치시 {a}".strip()
    return a


def geocode(address: str, provider: str = "kakao", keys: Optional[dict] = None):
    """통합 지오코딩.

    provider: "kakao" | "vworld" | "nominatim"
    keys: {"kakao": ..., "vworld": ...}
    반환: (lat, lng, status, message)
      status ∈ {"exact", "fallback_dong", "fallback_city", "out_of_area", "failed"}
    """
    keys = keys or {}
    addr = _normalize_address(address)
    log = []

    # 제공자별 시도 순서 구성 (선택 제공자 → 나머지 → Nominatim)
    order = [provider] + [p for p in ("kakao", "vworld", "nominatim") if p != provider]
    for prov in order:
        if prov == "kakao" and keys.get("kakao"):
            lat, lng = geocode_kakao(addr, keys["kakao"])
        elif prov == "vworld" and keys.get("vworld"):
            lat, lng = geocode_vworld(addr, keys["vworld"])
        elif prov == "nominatim":
            lat, lng = geocode_nominatim(addr)
        else:
            continue
        if lat is not None:
            if is_within_sejong(lat, lng):
                return lat, lng, "exact", f"{prov} 지오코딩 성공"
            log.append(f"{prov}: 세종 범위 밖 ({lat:.4f},{lng:.4f})")

    # 읍면동 중심 폴백
    for dong, (dlat, dlng) in DONG_CENTERS.items():
        if dong in address:
            return dlat, dlng, "fallback_dong", f"'{dong}' 중심점 기준(근사)"

    # 세종시 중심 폴백
    if "세종" in address:
        return SEJONG_CENTER[0], SEJONG_CENTER[1], "fallback_city", "세종시 중심 기준(주소 재확인 권장)"

    if log:
        return None, None, "out_of_area", "; ".join(log)
    return None, None, "failed", "주소를 찾을 수 없음"


# ------------------------------------------------------------------
# 시설유형 키워드 매칭 (오프라인, Gemini 불필요)
# ------------------------------------------------------------------
# 값은 (group, type-substring) 조건. type-substring이 ""이면 group만으로 필터.
KEYWORD_MAP = {
    "어린이집": ("아동", "어린이집"),
    "유치원": ("아동", ""),
    "아이": ("아동", ""),
    "아동": ("아동", ""),
    "영유아": ("아동", "어린이집"),
    "돌봄": ("아동", "돌봄"),
    "아이돌봄": ("아동", ""),
    "아동돌봄": ("아동", ""),
    "방과후": ("아동", ""),
    "다함께돌봄": ("아동", "다함께돌봄"),
    "지역아동센터": ("아동", "아동복지"),
    "노인": ("노인", ""),
    "어르신": ("노인", ""),
    "요양": ("노인", "노인의료"),
    "주간보호": ("노인", "재가"),
    "치매": ("노인", ""),
    "재가": ("노인", "재가"),
    "경로": ("노인", "여가"),
    "장애인": ("장애인", ""),
    "장애": ("장애인", ""),
    "정신": ("장애인", "정신"),
    "활동지원": ("장애인", ""),
}


def match_by_keyword(message: str, facilities: list[dict]):
    """사용자 문장에서 키워드로 시설 필터.

    반환: (filtered_list, matched_keywords, status)
      status ∈ {"exact", "no_keyword"}
    """
    matched = []
    conds = []
    for kw, cond in KEYWORD_MAP.items():
        if kw in message:
            matched.append(kw)
            conds.append(cond)

    if not conds:
        return facilities, [], "no_keyword"

    groups = {g for g, _ in conds}
    filtered = [f for f in facilities if f.get("group") in groups]

    # type-substring 조건이 있으면 추가로 좁히되, 결과가 비면 그룹 단위로 유지
    type_subs = [t for _, t in conds if t]
    if type_subs:
        narrowed = [f for f in filtered if any(t in f.get("type", "") or t in f.get("kind", "") for t in type_subs)]
        if narrowed:
            filtered = narrowed

    return filtered, matched, "exact"


# ------------------------------------------------------------------
# 도로망 라우팅 (OSMnx 선택적 - 미설치/실패 시 호출측에서 직선거리로 폴백)
# ------------------------------------------------------------------
def osmnx_available() -> bool:
    try:
        import osmnx  # noqa: F401
        import networkx  # noqa: F401

        return True
    except Exception:
        return False


def _ox_nearest_nodes(graph, lng, lat):
    """osmnx 1.x(ox.nearest_nodes)와 2.x(ox.distance.nearest_nodes) 모두 지원."""
    import osmnx as ox

    try:
        return ox.nearest_nodes(graph, lng, lat)
    except AttributeError:
        from osmnx import distance as oxd

        return oxd.nearest_nodes(graph, lng, lat)


GRAPHML_FILENAME = "sejong_drive.graphml"


def _add_speeds_times(g):
    """속도/소요시간 부여 (osmnx 1.x/2.x 호환). 네트워크 불필요, 빠름."""
    import osmnx as ox

    try:  # osmnx 1.x
        g = ox.add_edge_speeds(g)
        g = ox.add_edge_travel_times(g)
    except AttributeError:  # osmnx 2.x
        from osmnx import routing

        g = routing.add_edge_speeds(g)
        g = routing.add_edge_travel_times(g)
    return g


def build_road_network(place: str = "세종특별자치시, South Korea", base_dir: Optional[str] = None):
    """세종시 도로망 + 속도/소요시간. (osmnx 1.x/2.x 호환)

    콜드스타트(휴면 후 첫 검색) 가속: 저장소에 미리 만들어 둔 sejong_drive.graphml 이
    있으면 OSM에서 재다운로드(1~3분) 대신 파일에서 즉시 로딩합니다(수 초). 없으면
    기존처럼 graph_from_place 로 내려받습니다.

    graphml 생성(로컬에서 1회 실행 후 저장소에 커밋):
        import osmnx as ox
        g = ox.graph_from_place("세종특별자치시, South Korea", network_type="drive")
        ox.save_graphml(g, "sejong_drive.graphml")

    app 쪽에서 @st.cache_resource로 감싸 캐싱합니다.
    """
    import osmnx as ox

    base_dir = base_dir or os.path.dirname(os.path.abspath(__file__))
    graphml_path = os.path.join(base_dir, GRAPHML_FILENAME)

    g = None
    if os.path.exists(graphml_path):
        try:
            g = ox.load_graphml(graphml_path)
        except Exception:
            g = None
    if g is None:
        g = ox.graph_from_place(place, network_type="drive")

    return _add_speeds_times(g)


def road_route(graph, user_lat, user_lng, dest_lat, dest_lng):
    """OSMnx 그래프로 도로 최단경로 계산.

    반환: (distance_m, duration_min, route_coords[list[(lat,lng)]]) | (None, None, None)
    graph는 build_road_network()로 만든(속도/소요시간 포함) 객체.
    """
    try:
        import networkx as nx

        u = _ox_nearest_nodes(graph, user_lng, user_lat)
        v = _ox_nearest_nodes(graph, dest_lng, dest_lat)
        dist = nx.shortest_path_length(graph, u, v, weight="length")
        secs = nx.shortest_path_length(graph, u, v, weight="travel_time")
        path = nx.shortest_path(graph, u, v, weight="travel_time")
        coords = [(graph.nodes[n]["y"], graph.nodes[n]["x"]) for n in path]
        return round(dist), round(secs / 60, 1), coords
    except Exception:
        return None, None, None
