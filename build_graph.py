"""
세종시 도로망을 미리 내려받아 sejong_drive.graphml 로 저장하는 1회용 스크립트.

이 파일을 저장소에 커밋해 두면, 배포 앱이 휴면에서 깨어난 뒤 첫 검색에서
OSM 재다운로드(1~3분) 대신 파일 로딩(수 초)으로 훨씬 빠르게 동작합니다.

사용법(로컬, osmnx 설치 필요):
    pip install osmnx
    python build_graph.py
    git add sejong_drive.graphml && git commit -m "Add prebuilt road network" && git push
"""

import osmnx as ox

PLACE = "세종특별자치시, South Korea"
OUT = "sejong_drive.graphml"

if __name__ == "__main__":
    print(f"[1/2] '{PLACE}' 도로망 다운로드 중... (1~3분)")
    g = ox.graph_from_place(PLACE, network_type="drive")
    print(f"      노드 {len(g.nodes):,} / 엣지 {len(g.edges):,}")
    print(f"[2/2] 저장: {OUT}")
    ox.save_graphml(g, OUT)
    print("완료. 저장소에 커밋하세요: git add", OUT)
