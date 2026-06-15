# -*- coding: utf-8 -*-
"""monitor.html 미리보기 생성"""
import sys, os, webbrowser
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modules.module1_sector import Module1Sector

def s(name, price, rate, amount, prog):
    return {"code":"000000","name":name,"price":price,"rate":rate,"amount":amount,"prog":prog}

SAMPLE = [
    {"theme":"반도체/부품","avg_rate":2.45,"total_amount":3820,"up_ratio":0.72,"stocks":[
        s("SK하이닉스",    198500, 3.21, 1240, 85200),
        s("삼성전자",       84200, 2.18, 2100,-32000),
        s("DB하이텍",       52300, 4.87,  320, 12400),
        s("원익IPS",        38100, 1.92,  180,     0),
        s("피에스케이홀딩스",28600, 0.84,   95,  4500),
    ]},
    {"theme":"2차전지","avg_rate":1.18,"total_amount":2140,"up_ratio":0.58,"stocks":[
        s("에코프로비엠",  142000, 2.34,  890, 24100),
        s("포스코퓨처엠",  185000, 1.05,  670, -8900),
        s("엘앤에프",       74800, 0.81,  410,  3200),
        s("코스모신소재",   38200,-0.52,  120,     0),
        s("나노신소재",     63100, 3.11,   85,  1800),
    ]},
    {"theme":"시멘트/레미콘","avg_rate":2.68,"total_amount":42,"up_ratio":0.67,"stocks":[
        s("서산",     1272, 29.93, 0,    0),
        s("모헨즈",   4605, 26.34, 1, 3400),
        s("강동씨앤엘",1018,  3.04, 0,    0),
        s("동양",      560,  2.00, 0,    0),
        s("HC홀센타", 1928,  0.68, 0,    0),
    ]},
    {"theme":"백화점","avg_rate":1.66,"total_amount":14,"up_ratio":0.60,"stocks":[
        s("신세계",    658000,  5.45, 8,  1200),
        s("롯데쇼핑",  189900,  4.40, 3,  -500),
        s("광주신세계", 34600,  1.47, 0,     0),
        s("대구백화점",  5050,  1.30, 0,     0),
        s("현대백화점",166100, -0.42, 3, -1100),
    ]},
    {"theme":"리츠(REITs)","avg_rate":0.73,"total_amount":0,"up_ratio":0.50,"stocks":[
        s("신한서부티엔디", 4300, 15.13, 0, 5600),
        s("SK리츠",         5190,  7.23, 0,    0),
        s("한화리츠",       4410,  6.91, 0, 2100),
        s("롯데리츠",       3525,  2.03, 0,    0),
        s("이지스밸류플러스",3150,  1.29, 0,    0),
    ]},
    {"theme":"주류업","avg_rate":0.10,"total_amount":0,"up_ratio":0.46,"stocks":[
        s("한국알뤌",   12220, 8.72, 0, 0),
        s("한올앤제주", 12710, 1.68, 0, 0),
        s("보해양조",    1294, 1.57, 0, 0),
        s("나라셀라",    2050, 1.23, 0, 0),
        s("무학",        7620, 0.66, 0, 0),
    ]},
    {"theme":"은행","avg_rate":-0.09,"total_amount":82,"up_ratio":0.35,"stocks":[
        s("제주은행",   13660, 22.73, 16,  8900),
        s("케이뱅크",    5970,  3.29, 19, -4200),
        s("기업은행",   20450, -0.49,  2, -1500),
        s("카카오뱅크", 24000, -1.64,  4,     0),
        s("iM금융지주", 16920, -2.08,  0,     0),
    ]},
]

m = Module1Sector.__new__(Module1Sector)
m.theme_ranking = SAMPLE
m._update_html("18:50:00 (미리보기)")

out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "monitor_preview.html")
import shutil
shutil.copy(os.path.join(os.path.dirname(os.path.abspath(__file__)), "monitor.html"), out)

print(f"미리보기: {out}")
webbrowser.open(f"file:///{out.replace(chr(92), '/')}")
