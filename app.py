import streamlit as st
import osmnx as ox
import networkx as nx
import folium
import requests
import json
import csv
import io
import os
import pandas as pd
from google import genai
from streamlit_folium import st_folium

# ============================================
# 페이지 설정
# ============================================
st.set_page_config(
    page_title="세종시 복지기관 안내",
    page_icon="🏥",
    layout="wide"
)

st.title("🏥 세종시 복지기관 안내 서비스")
st.caption("주소와 필요한 서비스를 입력하면, 가장 가까운 기관을 안내해드립니다.")

# ============================================
# 비밀번호 인증
# ============================================
try:
    APP_PASSWORD = st.secrets["APP_PASSWORD"]
except:
    APP_PASSWORD = "7216"

if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    st.info("🔒 본 서비스는 시범 운영 중입니다. 비밀번호를 입력해주세요.")
    pw_input = st.text_input("비밀번호", type="password", key="pw_input")
    if st.button("로그인"):
        if str(pw_input).strip() == str(APP_PASSWORD).strip():
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("비밀번호가 올바르지 않습니다.")
    st.stop()

# ============================================
# API 키 로드 (Streamlit Cloud secrets 또는 환경변수)
# ============================================
try:
    VWORLD_API_KEY = st.secrets["VWORLD_API_KEY"]
    GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
except:
    VWORLD_API_KEY = os.environ.get("VWORLD_API_KEY", "")
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

if not VWORLD_API_KEY or not GEMINI_API_KEY:
    st.error("⚠️ API 키가 설정되지 않았습니다. Streamlit Cloud의 Secrets 설정을 확인하세요.")
    st.stop()

# Gemini 클라이언트
gemini_client = genai.Client(api_key=GEMINI_API_KEY)

def call_gemini(prompt, model="gemini-2.5-flash"):
    response = gemini_client.models.generate_content(model=model, contents=prompt)
    return response.text


# ============================================
# 데이터 로드 (캐싱)
# ============================================
@st.cache_resource(show_spinner="🗺️ 세종시 도로 네트워크 로딩 중... (최초 1회, 약 1~3분)")
def load_network():
    """세종시 도로 네트워크 다운로드 + 속도/시간 정보 추가"""
    G = ox.graph_from_place("세종특별자치시, South Korea", network_type="drive")
    G = ox.add_edge_speeds(G)
    G = ox.add_edge_travel_times(G)
    return G

@st.cache_data(show_spinner="📋 기관 데이터 로딩 중...")
def load_facilities():
    """CSV에서 기관 데이터 로드 (방어적 처리)"""
    filepath = os.path.join(os.path.dirname(__file__), "data", "facilities.csv")
    facilities = []

    # 1) 파일 존재 확인
    if not os.path.exists(filepath):
        st.error(f"⚠️ 파일이 존재하지 않습니다: {filepath}")
        return []

    # 2) 인코딩 자동 감지하여 읽기
    df = None
    for encoding in ['utf-8-sig', 'utf-8', 'cp949', 'euc-kr', 'latin1']:
        try:
            df = pd.read_csv(filepath, encoding=encoding, dtype=str)  # 모든 컬럼을 문자열로 읽기
            break
        except Exception as e:
            continue

    if df is None:
        st.error("⚠️ facilities.csv를 읽을 수 없습니다.")
        return []

    # 3) 컬럼명 정리 (공백, BOM, 특수문자 제거)
    df.columns = [c.strip().replace('\ufeff', '').replace('\xa0', '').replace('\t', '') for c in df.columns]

    # 디버그: 실제 컬럼명 표시 (문제 파악용, 정상 작동 후 삭제 가능)
    st.sidebar.text(f"CSV 컬럼: {list(df.columns)}")
    st.sidebar.text(f"CSV 행 수: {len(df)}")

    # 4) 컬럼명 유연 매핑 (다양한 표기 대응)
    col_map = {}
    for col in df.columns:
        col_lower = col.lower().strip()
        if col_lower in ['시설유형', '유형', '대분류']:
            col_map['시설유형'] = col
        elif col_lower in ['시설종류', '종류', '중분류', '소분류']:
            col_map['시설종류'] = col
        elif col_lower in ['시설명', '기관명', '기관', '시설']:
            col_map['시설명'] = col
        elif col_lower in ['주소', '기관주소', '소재지', '도로명주소']:
            col_map['주소'] = col
        elif col_lower in ['행정동', '동', '읍면동']:
            col_map['행정동'] = col
        elif col_lower in ['시군구', '시도', '지역']:
            col_map['시군구'] = col
        elif col_lower in ['x축', 'x좌표', 'x', 'lng', 'lon', 'longitude', '경도']:
            col_map['x축'] = col
        elif col_lower in ['y축', 'y좌표', 'y', 'lat', 'latitude', '위도']:
            col_map['y축'] = col

    st.sidebar.text(f"컬럼 매핑: {col_map}")

    # 5) 행별 파싱 (오류 건너뛰기)
    error_count = 0
    for idx, row in df.iterrows():
        try:
            # 좌표값 정리: 쉼표 제거, 공백 제거
            x_raw = str(row.get(col_map.get('x축', 'x축'), '0')).strip().replace(',', '').replace(' ', '')
            y_raw = str(row.get(col_map.get('y축', 'y축'), '0')).strip().replace(',', '').replace(' ', '')

            # 빈 값 처리
            if not x_raw or x_raw == 'nan' or x_raw == 'None':
                x_raw = '0'
            if not y_raw or y_raw == 'nan' or y_raw == 'None':
                y_raw = '0'

            lng = float(x_raw)
            lat = float(y_raw)

            facility = {
                'category_l': str(row.get(col_map.get('시설유형', '시설유형'), '')).strip(),
                'category_m': str(row.get(col_map.get('시설종류', '시설종류'), '')).strip(),
                'category_s': str(row.get(col_map.get('시설종류', '시설종류'), '')).strip(),
                'name': str(row.get(col_map.get('시설명', '시설명'), '')).strip(),
                'address': str(row.get(col_map.get('주소', '주소'), '')).strip(),
                'dong': str(row.get(col_map.get('행정동', '행정동'), '')).strip(),
                'sigungu': str(row.get(col_map.get('시군구', '시군구'), '')).strip(),
                'lng': lng,
                'lat': lat,
            }

            # nan 문자열 정리
            for key in ['category_l', 'category_m', 'category_s', 'name', 'address', 'dong', 'sigungu']:
                if facility[key] == 'nan' or facility[key] == 'None':
                    facility[key] = ''

            # 좌표 유효성 검사
            if 36.3 < lat < 36.8 and 126.7 < lng < 127.3:
                facilities.append(facility)
            elif lat != 0 and lng != 0:
                error_count += 1
        except Exception as e:
            error_count += 1
            continue

    if error_count > 0:
        st.sidebar.warning(f"⚠️ {error_count}건 파싱 오류/범위 밖 제외")

    return facilities

@st.cache_data(show_spinner="📍 기관-도로 매핑 중...")
def map_facility_nodes(_G, facilities):
    """기관별 최근접 도로 노드 매핑"""
    for f in facilities:
        try:
            f['node'] = ox.nearest_nodes(_G, f['lng'], f['lat'])
        except:
            f['node'] = None
    return facilities


# ============================================
# 세종시 읍면동 중심 좌표
# ============================================
SEJONG_DONG_CENTERS = {
    "한솔동": (36.6040, 127.0015), "새롬동": (36.5100, 127.0080),
    "도담동": (36.5920, 127.0040), "어진동": (36.5050, 127.0020),
    "종촌동": (36.5150, 126.9950), "고운동": (36.5000, 127.0100),
    "아름동": (36.4950, 127.0050), "보람동": (36.5200, 127.0100),
    "대평동": (36.5250, 127.0000), "소담동": (36.4900, 127.0080),
    "반곡동": (36.5300, 126.9900), "나성동": (36.5350, 126.9950),
    "조치원읍": (36.6030, 127.0290), "연기면": (36.5500, 127.0000),
    "연동면": (36.5700, 127.0500), "부강면": (36.6200, 127.1200),
    "금남면": (36.5400, 127.0800), "장군면": (36.5100, 127.0600),
    "연서면": (36.5600, 126.9500), "전의면": (36.6500, 127.0500),
    "전동면": (36.6300, 127.0800), "소정면": (36.6700, 127.0300),
}

SEJONG_BOUNDS = {'lat_min': 36.42, 'lat_max': 36.72, 'lng_min': 126.82, 'lng_max': 127.25}

def is_within_sejong(lat, lng):
    return (SEJONG_BOUNDS['lat_min'] <= lat <= SEJONG_BOUNDS['lat_max'] and
            SEJONG_BOUNDS['lng_min'] <= lng <= SEJONG_BOUNDS['lng_max'])


# ============================================
# 핵심 함수
# ============================================

def geocode_vworld(address):
    url = "https://api.vworld.kr/req/address"
    for addr_type in ['ROAD', 'PARCEL']:
        params = {
            "service": "address", "request": "getCoord", "version": "2.0",
            "crs": "epsg:4326", "address": address, "refine": "true",
            "simple": "false", "format": "json", "type": addr_type,
            "key": VWORLD_API_KEY
        }
        try:
            res = requests.get(url, params=params, timeout=10)
            data = res.json()
            if data['response']['status'] == 'OK':
                point = data['response']['result']['point']
                lat, lng = float(point['y']), float(point['x'])
                if is_within_sejong(lat, lng):
                    return lat, lng, 'exact', '정확한 주소 매칭'
                else:
                    return lat, lng, 'out_of_area', '세종시 범위 밖'
        except:
            continue

    for dong_name, (lat, lng) in SEJONG_DONG_CENTERS.items():
        if dong_name in address:
            return lat, lng, 'fallback_dong', f"'{dong_name}' 중심점 기준"

    if '세종' in address:
        return 36.4800, 126.9270, 'fallback_city', '세종시 중심점 기준'

    return None, None, 'failed', '주소를 찾을 수 없음'


KEYWORD_MAP = {
    '어린이집': ['시설유형:아동', '시설종류:어린이집'],
    '유치원': ['시설유형:아동', '시설종류:유치원'],
    '아이': ['시설유형:아동'], '아동': ['시설유형:아동'],
    '돌봄': ['시설종류:돌봄'],
    '아이돌봄': ['시설유형:아동'], '아동돌봄': ['시설유형:아동'],
    '방과후': ['시설유형:아동'],
    '지역아동센터': ['시설종류:지역아동센터'],
    '다함께돌봄': ['시설종류:다함께돌봄'],
    '초등돌봄': ['시설유형:아동'],
    '영유아': ['시설유형:아동', '시설종류:어린이집'],
    '노인': ['시설유형:노인'], '어르신': ['시설유형:노인'],
    '요양': ['시설유형:노인', '시설종류:요양'],
    '주간보호': ['시설종류:주간보호'], '치매': ['시설유형:노인'],
    '경로당': ['시설종류:경로당'], '재가': ['시설종류:재가'],
    '장애인': ['시설유형:장애인'], '장애': ['시설유형:장애인'],
    '활동지원': ['시설유형:장애인'],
    '보건소': ['시설유형:보건', '시설종류:보건소'],
    '보건': ['시설유형:보건'], '건강검진': ['시설유형:보건'],
    '병원': ['시설유형:보건'], '의료': ['시설유형:보건'],
    '복지관': ['시설종류:복지관'], '상담': ['시설종류:상담'],
}


def match_facility_type(user_message, facilities):
    msg = user_message
    matched_conditions = []
    matched_keywords = []
    for keyword, conditions in KEYWORD_MAP.items():
        if keyword in msg:
            matched_conditions.extend(conditions)
            matched_keywords.append(keyword)

    if matched_conditions:
        filtered = facilities.copy()
        for condition in set(matched_conditions):
            field, value = condition.split(':')
            field_map = {'시설유형': 'category_l', '시설종류': 'category_m'}
            if field in field_map:
                new_filtered = [f for f in filtered if value in f[field_map[field]]]
                if new_filtered:
                    filtered = new_filtered
        if filtered:
            return filtered, 'exact', f"'{', '.join(matched_keywords)}' 매칭"
        else:
            return facilities, 'no_match', f"'{', '.join(matched_keywords)}' 관련 기관이 DB에 없음"

    try:
        existing_categories = sorted(set(
            f"{f['category_l']}/{f['category_m']}" for f in facilities
        ))
        prompt = f"""사용자가 어떤 복지서비스를 원하는지 분석하세요.
사용자 질문: {user_message}
DB 기관 분류: {', '.join(existing_categories)}
JSON만 반환:
{{"confidence":"high|medium|low","matched_category_l":"시설유형","matched_category_m":"시설종류","reason":"판단근거","alternatives":["유사1","유사2"]}}"""

        text = call_gemini(prompt)
        text = text.strip().replace("```json", "").replace("```", "").strip()
        result = json.loads(text)

        if result['confidence'] == 'high':
            filtered = facilities
            if result['matched_category_l']:
                new_f = [f for f in filtered if result['matched_category_l'] in f['category_l']]
                if new_f: filtered = new_f
            if result['matched_category_m']:
                new_f = [f for f in filtered if result['matched_category_m'] in f['category_m']]
                if new_f: filtered = new_f
            return filtered, 'partial', f"AI 분석: {result['reason']}"
        else:
            alt_text = ', '.join(result.get('alternatives', []))
            msg_out = f"정확한 기관유형 특정이 어렵습니다. AI 판단: {result['reason']}"
            if alt_text:
                msg_out += f" | 혹시 이런 서비스? → {alt_text}"
            return facilities, 'uncertain', msg_out
    except:
        return facilities, 'all', "기관유형 분석 실패 (전체 대상 검색)"


def extract_address_with_gemini(user_message):
    prompt = f"""다음에서 사용자 위치/주소를 추출. JSON만 반환.
질문: {user_message}
출력: {{"address":"세종시 포함 주소","dong":"읍면동명","is_sejong":true/false,"confidence":"high|medium|low"}}"""
    try:
        text = call_gemini(prompt)
        text = text.strip().replace("```json", "").replace("```", "").strip()
        return json.loads(text)
    except:
        return {"address": "", "dong": "", "is_sejong": True, "confidence": "low"}


def find_nearest_facilities(G, user_lat, user_lng, target_facilities, top_n=5):
    user_node = ox.nearest_nodes(G, user_lng, user_lat)
    results = []
    for f in target_facilities:
        if f.get('node') is None:
            continue
        try:
            travel_time = nx.shortest_path_length(G, user_node, f['node'], weight='travel_time')
            distance = nx.shortest_path_length(G, user_node, f['node'], weight='length')
            route = nx.shortest_path(G, user_node, f['node'], weight='travel_time')
            results.append({
                'name': f['name'], 'category_l': f['category_l'],
                'category_m': f['category_m'], 'address': f['address'],
                'dong': f.get('dong', ''), 'lat': f['lat'], 'lng': f['lng'],
                'distance_m': round(distance), 'distance_km': round(distance/1000, 1),
                'travel_time_min': round(travel_time/60, 1), 'route': route
            })
        except:
            continue
    results.sort(key=lambda x: x['travel_time_min'])
    return results[:top_n]


def generate_answer(user_message, results, address_info, geo_status, match_status, match_message):
    results_text = ""
    if results:
        for i, r in enumerate(results, 1):
            results_text += f"{i}. {r['name']} (시설유형: {r['category_l']} / 시설종류: {r['category_m']})\n"
            results_text += f"   주소: {r['address']}\n"
            results_text += f"   거리: {r['distance_km']}km / 차량 약 {r['travel_time_min']}분\n\n"
    else:
        results_text = "(검색 결과 없음)"

    prompt = f"""세종시 복지기관 안내 AI입니다.
[질문] {user_message}
[주소 상태] {geo_status} / {address_info.get('address','')}
[기관매칭] {match_status} / {match_message}
[결과]{results_text}
[규칙]
- fallback_dong → "OO동 중심점 기준 안내" 언급
- fallback_city → "세종시 중심 기준, 정확한 주소 재입력 요청"
- out_of_area → "세종시 범위 밖, 서비스 지역 밖"
- failed → "주소 찾을 수 없음, 재입력 요청"
- uncertain → 유사 기관 함께 안내
- no_match → "해당 유형 미등록" + 대안 제시
- 정상 → 가까운 순 안내 (기관명, 분류, 주소, 차량시간)
- 소요시간은 도로 네트워크 추정치 언급
- 마지막: "※ 방문 전 운영시간 확인 권장"
"""
    try:
        return call_gemini(prompt)
    except:
        if not results:
            return "검색 결과가 없습니다. 주소와 요청 내용을 확인해주세요."
        text = "📍 검색 결과:\n\n"
        for i, r in enumerate(results, 1):
            text += f"{i}. {r['name']} ({r['category_m']})\n   {r['address']} / 차량 약 {r['travel_time_min']}분\n\n"
        text += "※ 방문 전 운영시간 확인을 권장합니다."
        return text


def create_map(user_lat, user_lng, results, G):
    m = folium.Map(location=[user_lat, user_lng], zoom_start=14)
    folium.Marker(
        [user_lat, user_lng], popup="📍 내 위치",
        icon=folium.Icon(color='red', icon='home', prefix='fa')
    ).add_to(m)
    colors = ['blue', 'green', 'purple', 'orange', 'darkred']
    for i, r in enumerate(results):
        folium.Marker(
            [r['lat'], r['lng']],
            popup=f"<b>{r['name']}</b><br>{r['address']}<br>차량 {r['travel_time_min']}분",
            icon=folium.Icon(color=colors[i % len(colors)], icon='building', prefix='fa')
        ).add_to(m)
        if r.get('route'):
            try:
                route_coords = [(G.nodes[n]['y'], G.nodes[n]['x']) for n in r['route']]
                folium.PolyLine(route_coords, weight=4, color=colors[i % len(colors)], opacity=0.7).add_to(m)
            except:
                pass
    return m


# ============================================
# 데이터 로드 실행
# ============================================
G = load_network()
facilities_raw = load_facilities()
facilities = map_facility_nodes(G, facilities_raw)

# 사이드바 정보
with st.sidebar:
    st.header("📊 데이터 현황")
    st.metric("도로 노드", f"{len(G.nodes):,}개")
    st.metric("도로 엣지", f"{len(G.edges):,}개")
    st.metric("등록 기관", f"{len(facilities)}개")

    st.divider()
    st.subheader("📋 기관 분류")
    cat_counts = {}
    for f in facilities:
        key = f"{f['category_l']} > {f['category_m']}"
        cat_counts[key] = cat_counts.get(key, 0) + 1
    for cat, cnt in sorted(cat_counts.items()):
        st.text(f"  {cat}: {cnt}개")

    st.divider()
    st.caption("ℹ️ 본 서비스는 세종특별자치시 지역만 지원합니다.")
    st.caption("소요시간은 도로 네트워크 기반 추정치입니다.")


# ============================================
# 채팅 UI
# ============================================

# 채팅 히스토리 초기화
if "messages" not in st.session_state:
    st.session_state.messages = [
        {"role": "assistant", "content": "안녕하세요! 세종시 복지기관 안내 서비스입니다.\n\n**사용 예시:**\n- \"세종시 한솔동에 사는데 아동돌봄 기관 알려줘\"\n- \"도담동인데 어르신 주간보호센터 찾아줘\"\n- \"세종시 보건소 어디 있어?\"\n\n주소와 필요한 서비스를 함께 입력해주세요."}
    ]

# 채팅 히스토리 출력
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if "map" in msg and msg["map"]:
            st_folium(msg["map"], width=700, height=400)

# 사용자 입력
if user_input := st.chat_input("세종시 주소와 필요한 서비스를 입력하세요..."):
    # 사용자 메시지 표시
    st.session_state.messages.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)

    # AI 처리
    with st.chat_message("assistant"):
        with st.spinner("🔍 검색 중..."):
            # 1) 주소 추출
            address_info = extract_address_with_gemini(user_input)

            # 세종시 밖 체크
            if address_info.get('is_sejong') == False:
                answer = f"⚠️ 본 서비스는 **세종특별자치시** 지역만 지원합니다.\n\n입력하신 주소 '{address_info.get('address', '')}'는 서비스 지역 밖입니다."
                st.markdown(answer)
                st.session_state.messages.append({"role": "assistant", "content": answer})
                st.stop()

            # 2) 지오코딩
            address_str = address_info.get('address', '')
            if not address_str and address_info.get('dong'):
                address_str = f"세종시 {address_info['dong']}"

            lat, lng, geo_status, geo_message = geocode_vworld(address_str)

            if lat is None:
                dong = address_info.get('dong', '')
                if dong and dong in SEJONG_DONG_CENTERS:
                    lat, lng = SEJONG_DONG_CENTERS[dong]
                    geo_status = 'fallback_dong'
                else:
                    answer = "⚠️ 주소를 찾을 수 없습니다.\n\n세종시 내 구체적 주소를 입력해주세요.\n\n예) \"세종시 한솔동 123\", \"세종시 도담동\""
                    st.markdown(answer)
                    st.session_state.messages.append({"role": "assistant", "content": answer})
                    st.stop()

            if geo_status == 'out_of_area' or (lat and not is_within_sejong(lat, lng)):
                answer = "⚠️ 입력하신 주소가 **세종시 범위 밖**입니다.\n\n본 서비스는 세종특별자치시만 지원합니다."
                st.markdown(answer)
                st.session_state.messages.append({"role": "assistant", "content": answer})
                st.stop()

            # 3) 기관유형 매칭
            filtered, match_status, match_message = match_facility_type(user_input, facilities)

            # 4) 최단경로 계산
            results = find_nearest_facilities(G, lat, lng, filtered, top_n=5)

            # 5) AI 안내문 생성
            answer = generate_answer(user_input, results, address_info, geo_status, match_status, match_message)
            st.markdown(answer)

            # 6) 지도 표시
            result_map = None
            if results:
                result_map = create_map(lat, lng, results, G)
                st_folium(result_map, width=700, height=400)

            st.session_state.messages.append({
                "role": "assistant", "content": answer, "map": result_map
            })
