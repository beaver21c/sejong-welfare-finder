# 🏥 세종시 복지기관 안내 서비스

세종시 거주자가 주소와 필요한 서비스를 입력하면, OSMnx 도로 네트워크 기반으로 가장 가까운 복지기관을 안내하는 AI 서비스입니다.

## 기능
- 자연어 질의 (예: "세종시 한솔동에 사는데 아동돌봄 기관 알려줘")
- VWORLD 지오코딩 → 정확한 좌표 변환
- OSMnx 도로 네트워크 기반 최단경로 계산
- Gemini AI 기반 자연어 안내문 생성
- Folium 지도 시각화 (경로 표시)

## 기술 스택
- Streamlit, OSMnx, NetworkX, Folium
- Google Gemini API, VWORLD Geocoding API
