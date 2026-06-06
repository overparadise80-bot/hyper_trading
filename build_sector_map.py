import requests
from bs4 import BeautifulSoup
import json
import time

headers = {"User-Agent": "Mozilla/5.0"}

# 키움 업종코드 → 네이버 업종번호 직접 매핑
KIWOOM_TO_NAVER = {
    "005": "261",  # 음식료품
    "006": "262",  # 섬유의복
    "007": "263",  # 종이목재
    "008": "272",  # 화학
    "009": "273",  # 의약품
    "010": "274",  # 비금속광물
    "011": "304",  # 철강금속
    "012": "299",  # 기계
    "013": "276",  # 전기전자
    "014": "277",  # 의료정밀
    "015": "278",  # 운수장비
    "016": "283",  # 유통업
    "017": "331",  # 전기가스업
    "018": "279",  # 건설업
    "019": "282",  # 운수창고
    "020": "284",  # 통신업
    "021": "319",  # 금융업
    "022": "301",  # 은행
    "024": "321",  # 증권
    "025": "315",  # 보험
    "026": "285",  # 서비스업
}

KIWOOM_NAME = {
    "005": "음식료품", "006": "섬유의복", "007": "종이목재",
    "008": "화학",     "009": "의약품",   "010": "비금속광물",
    "011": "철강금속", "012": "기계",     "013": "전기전자",
    "014": "의료정밀", "015": "운수장비", "016": "유통업",
    "017": "전기가스업","018": "건설업",  "019": "운수창고",
    "020": "통신업",   "021": "금융업",   "022": "은행",
    "024": "증권",     "025": "보험",     "026": "서비스업",
}

sector_map = {}

for kiwoom_code, naver_no in KIWOOM_TO_NAVER.items():
    name = KIWOOM_NAME[kiwoom_code]
    url = f"https://finance.naver.com/sise/sise_group_detail.naver?type=upjong&no={naver_no}"
    try:
        res = requests.get(url, headers=headers, timeout=5)
        soup = BeautifulSoup(res.text, "html.parser")
        links = soup.select("a[href*='code=']")
        codes = []
        for a in links:
            href = a.get("href", "")
            if "code=" in href:
                code = href.split("code=")[-1][:6]
                if code.isdigit() and len(code) == 6:
                    codes.append(code)
        codes = list(dict.fromkeys(codes))
        sector_map[kiwoom_code] = codes
        print(f"✅ {name}({kiwoom_code}) no={naver_no}: {len(codes)}개")
    except Exception as e:
        print(f"❌ {name}: {e}")
        sector_map[kiwoom_code] = []
    time.sleep(0.5)

with open("sector_map.json", "w", encoding="utf-8") as f:
    json.dump(sector_map, f, ensure_ascii=False, indent=2)

print("\n✅ sector_map.json 저장 완료!")
print("\n[검증] 각 업종 종목수:")
for k, v in sector_map.items():
    print(f"  {KIWOOM_NAME[k]:10s}: {len(v)}개")