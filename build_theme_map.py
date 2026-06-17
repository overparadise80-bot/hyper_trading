# -*- coding: utf-8 -*-
"""
build_theme_map.py - 네이버 테마 기반 섹터맵 빌더

실행: python build_theme_map.py
출력:
  - theme_map.json       : {테마명: {"no": "7", "codes": ["005930", ...]}}
  - code_to_themes.json  : {종목코드: ["테마명1", "테마명2", ...]}
  - theme_stats.txt      : 통계 리포트
"""

import requests
from bs4 import BeautifulSoup
import json
import time
from collections import defaultdict

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://finance.naver.com/",
    "Accept-Language": "ko-KR,ko;q=0.9",
}

session = requests.Session()
session.headers.update(headers)

# ─────────────────────────────────────────
# 1단계: 테마 목록 전체 수집
# ─────────────────────────────────────────
def get_theme_list():
    url = "https://finance.naver.com/sise/sise_group.naver?type=theme"
    res = session.get(url, timeout=10)
    res.encoding = "euc-kr"
    soup = BeautifulSoup(res.text, "lxml")

    themes = {}
    links = soup.find_all("a", href=True)
    for a in links:
        href = a.get("href", "")
        name = a.text.strip()
        if "type=theme" in href and "no=" in href and name:
            no = href.split("no=")[-1].split("&")[0]
            if no.isdigit() and name not in themes.values():
                themes[no] = name

    print(f"[1단계] 테마 목록 수집 완료: {len(themes)}개")
    return themes  # {no: 테마명}

# ─────────────────────────────────────────
# 2단계: 테마별 종목코드 수집
# ─────────────────────────────────────────
def get_theme_codes(no, name):
    url = f"https://finance.naver.com/sise/sise_group_detail.naver?type=theme&no={no}"
    try:
        res = session.get(url, timeout=8)
        res.encoding = "euc-kr"
        soup = BeautifulSoup(res.text, "lxml")

        codes = []
        for a in soup.select("a[href*='code=']"):
            href = a.get("href", "")
            if "code=" in href:
                code = href.split("code=")[-1][:6]
                if code.isdigit() and len(code) == 6:
                    codes.append(code)

        codes = list(dict.fromkeys(codes))  # 중복 제거, 순서 유지
        return codes

    except Exception as e:
        print(f"  ❌ {name}({no}) 오류: {e}")
        return []

# ─────────────────────────────────────────
# 3단계: 실행
# ─────────────────────────────────────────
def main():
    print("=" * 50)
    print(" 네이버 테마맵 빌더 시작")
    print("=" * 50)

    # 테마 목록
    themes = get_theme_list()
    if not themes:
        print("❌ 테마 목록 수집 실패. 네트워크 확인 필요")
        return

    # 테마별 종목 수집
    theme_map = {}      # {테마명: {"no": "7", "codes": [...]}}
    code_to_themes = defaultdict(list)  # {종목코드: [테마명...]}

    total = len(themes)
    for i, (no, name) in enumerate(themes.items(), 1):
        codes = get_theme_codes(no, name)
        theme_map[name] = {"no": no, "codes": codes}

        for code in codes:
            code_to_themes[code].append(name)

        print(f"  [{i:3d}/{total}] {name[:15]:15s} (no={no:4s}): {len(codes):3d}종목")
        time.sleep(0.3)  # 서버 부하 방지

    # ─────────────────────────────────────────
    # 4단계: 저장
    # ─────────────────────────────────────────
    with open("theme_map_v2.json", "w", encoding="utf-8") as f:
        json.dump(theme_map, f, ensure_ascii=False, indent=2)

    with open("code_to_themes_v2.json", "w", encoding="utf-8") as f:
        json.dump(dict(code_to_themes), f, ensure_ascii=False, indent=2)

    # ─────────────────────────────────────────
    # 5단계: 통계
    # ─────────────────────────────────────────
    all_codes = set(code_to_themes.keys())
    theme_sizes = sorted([(name, len(d["codes"])) for name, d in theme_map.items()],
                         key=lambda x: -x[1])
    multi_theme = {c: ts for c, ts in code_to_themes.items() if len(ts) > 1}
    avg_themes = sum(len(v) for v in code_to_themes.values()) / len(code_to_themes) if code_to_themes else 0

    stats = f"""
==============================================
 네이버 테마맵 빌드 결과
==============================================
 총 테마 수       : {len(theme_map)}개
 유니크 종목 수   : {len(all_codes)}개
 복수 테마 종목   : {len(multi_theme)}개
 평균 테마 편입 수: {avg_themes:.2f}개/종목

[테마별 종목 수 TOP 30]
"""
    for name, cnt in theme_sizes[:30]:
        stats += f"  {name[:20]:20s}: {cnt:3d}종목\n"

    stats += f"\n[종목당 테마 편입 수 분포]\n"
    from collections import Counter
    dist = Counter(len(v) for v in code_to_themes.values())
    for k in sorted(dist.keys()):
        stats += f"  {k}개 테마: {dist[k]}종목\n"

    print(stats)
    with open("theme_stats.txt", "w", encoding="utf-8") as f:
        f.write(stats)

    print("✅ 저장 완료: theme_map_v2.json / code_to_themes_v2.json / theme_stats.txt")

if __name__ == "__main__":
    main()