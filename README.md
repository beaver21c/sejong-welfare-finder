# 🏥 세종시 복지기관 안내 서비스 (BYOK)

주소와 필요한 서비스를 자연어로 입력하면 **가장 가까운 세종시 복지기관**을 찾아 지도와 함께 안내합니다.
방문자가 **본인이 발급한 API 키**(Gemini + 카카오맵 또는 브이월드)를 입력해 사용하는 **오픈소스 · 퍼블릭** 서비스입니다.

> 🔑 **BYOK(Bring Your Own Key)** — 입력한 키는 **브라우저 세션에만** 보관되며 서버나 저장소에 저장되지 않습니다.
> 🔗 **원클릭 URL** — Streamlit Community Cloud 등에 한 번 배포하면, 방문자는 URL 접속 후 키만 넣으면 바로 사용합니다. (별도 웹/코드스페이스에서 열 필요 없음)

---

## ✨ 주요 기능

- 자연어 질의 — 예) *"세종시 한솔동에 사는데 아동돌봄 기관 알려줘"*
- **선택형 지오코딩** — 카카오맵 / 브이월드(V-World) / 무료(Nominatim) 중 선택
- **도로망 기반 최단시간** — OSMnx 도로 네트워크로 실제 소요시간 추정 *(미설치 시 직선거리 자동 폴백)*
- Gemini 기반 자연어 안내문 생성 *(키 없으면 규칙 기반으로 동작)*
- Folium 지도 — 내 위치 · 기관 · 경로 표시 (브이월드 타일 선택 지원)
- 세종시 454개 복지기관 데이터 내장 (노인 · 아동 · 장애인 · 기타)

---

## 🚀 바로 배포하기 (원클릭 공개 URL 만들기)

### 방법 A. Streamlit Community Cloud (권장, 무료)

1. 이 저장소를 **본인 GitHub 계정으로 Fork** 하거나 그대로 사용합니다.
2. <https://share.streamlit.io> 접속 → **New app** 클릭.
3. 저장소 `beaver21c/sejong-welfare-finder`, 브랜치, **Main file path = `app.py`** 선택 → **Deploy**.
4. 몇 분 뒤 `https://<앱이름>.streamlit.app` 형태의 **공개 URL**이 생성됩니다.
5. 그 URL을 공유하세요. 방문자는 접속 후 **사이드바에 본인 키만 입력**하면 바로 사용합니다.

> 배포자가 키를 미리 넣어두고 싶다면(선택) `Settings → Secrets`에 아래를 추가할 수 있습니다.
> 이 경우 방문자 화면에 기본값으로 채워지므로 **개인/공용 용도에 맞게** 사용하세요.
> ```toml
> GEMINI_API_KEY = "..."
> KAKAO_REST_API_KEY = "..."
> VWORLD_API_KEY = "..."
> ```

### 방법 B. 내 컴퓨터에서 실행

```bash
pip install -r requirements.txt
streamlit run app.py
# 브라우저에서 http://localhost:8501 접속 후 키 입력
```

가벼운 실행(도로망 없이 직선거리)을 원하면 `requirements.txt`의 `osmnx`, `networkx` 두 줄을 지우고 설치하세요.

---

## 🔑 API 키 발급 방법

| 키 | 발급처 | 비고 |
|---|---|---|
| **Gemini API 키** | [Google AI Studio](https://aistudio.google.com/apikey) | 무료. 자연어 주소 추출·안내문 생성에 사용 |
| **카카오 REST API 키** | [Kakao Developers](https://developers.kakao.com) → 내 애플리케이션 → **REST API 키** | 지오코딩(주소→좌표) |
| **브이월드 인증키** | [V-World 오픈API](https://www.vworld.kr/dev/v4api.do) | 지오코딩 + (선택)지도 타일. 발급 시 사용 도메인 등록 |

- 세 가지를 모두 넣을 필요는 없습니다. **Gemini + (카카오 또는 브이월드)** 조합이면 충분합니다.
- 키가 하나도 없어도 **무료(Nominatim)** 지오코딩 + 규칙 기반 안내로 동작합니다(정확도는 낮아집니다).

> ⚠️ **키 보안** — BYOK 특성상 키가 브라우저에서 사용됩니다. 각 콘솔에서 **사용량 제한·허용 도메인·호출 한도**를 설정해 두는 것을 권장합니다.

---

## 🌐 저장소를 퍼블릭으로 전환하기

이 서비스를 여러 사람이 URL로 쓰게 하려면 저장소를 공개로 바꾸세요.

`GitHub 저장소 → Settings → General → 맨 아래 Danger Zone → "Change repository visibility" → Public`

- 공개 전, 커밋 히스토리에 **실제 API 키가 남아있지 않은지** 확인하세요. (본 코드는 키를 저장소에 넣지 않습니다.)
- 배포된 `*.streamlit.app` URL 자체는 저장소 공개 여부와 무관하게 접속 가능합니다.

---

## 🧩 구조

```
app.py            # Streamlit UI (BYOK 키 입력, 검색, 지도)
core.py           # 순수 로직 (데이터 로드·거리·지오코딩·매칭·도로 라우팅) — Streamlit 비의존
facilities.json   # 세종시 복지기관 454개 (좌표 포함, UTF-8)
facilities.csv    # 원본 데이터 (CP949)
requirements.txt  # 의존성 (osmnx는 선택)
packages.txt      # Streamlit Cloud용 시스템 패키지
.streamlit/config.toml
```

`core.py`는 Streamlit 없이도 임포트·테스트할 수 있습니다.

```python
import core
fac = core.load_facilities()
lat, lng, status, msg = core.geocode("세종시 한솔동", provider="kakao", keys={"kakao": "<REST키>"})
nearest = core.rank_by_distance(lat, lng, fac, top_n=5)
```

---

## 🛠 기술 스택

Streamlit · Folium · OSMnx/NetworkX(선택) · Google Gemini API · 카카오맵 / 브이월드 / Nominatim 지오코딩

## ℹ️ 참고

- 서비스 지역은 **세종특별자치시**로 한정됩니다.
- 거리·소요시간은 추정치이며, **방문 전 기관 운영시간을 반드시 확인**하세요.
- 지오코딩·라우팅 API 호출은 각자의 키로 이루어지며, 호출 비용/한도는 키 소유자에게 귀속됩니다.
