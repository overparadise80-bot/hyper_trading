import sys
from PyQt5.QtWidgets import QApplication
from PyQt5.QAxContainer import QAxWidget
from PyQt5.QtCore import QTimer

app = QApplication(sys.argv)
kiwoom = QAxWidget("KHOPENAPI.KHOpenAPICtrl.1")

condition_list = {}  # {조건식명: 인덱스}

def on_login(err_code):
    if err_code == 0:
        print("✅ 로그인 성공!")
        QTimer.singleShot(1000, load_conditions)

def load_conditions():
    # 조건검색식 목록 로드
    ret = kiwoom.dynamicCall("GetConditionLoad()")
    print(f"조건식 로드 요청: {ret}")

def on_condition_load():
    # 조건식 목록 가져오기
    result = kiwoom.dynamicCall("GetConditionNameList()")
    print(f"\n조건식 목록: {result}")

    conditions = [c for c in result.split(';') if c]
    for c in conditions:
        parts = c.split('^')
        if len(parts) >= 2:
            idx, name = parts[0], parts[1]
            condition_list[name] = idx
            print(f"  [{idx}] {name}")

    # 52주신고가 조건식 실행
    if "52주신고가" in condition_list:
        idx = condition_list["52주신고가"]
        print(f"\n✅ '52주신고가' 조건식 발견! (인덱스: {idx})")
        print("조건검색 실행 중...")
        ret = kiwoom.dynamicCall(
            "SendCondition(QString, QString, int, int)",
            "0201", "52주신고가", int(idx), 0  # 0=일반조회
        )
        print(f"조건검색 요청 결과: {ret}")
    else:
        print("❌ '52주신고가' 조건식을 찾을 수 없습니다.")
        print(f"등록된 조건식: {list(condition_list.keys())}")

def on_receive_condition(screen, code_list, condition_name, idx, prev_next):
    print(f"\n✅ 조건검색 결과 수신!")
    print(f"  조건식: {condition_name}")
    codes = [c for c in code_list.split(';') if c]
    print(f"  종목수: {len(codes)}개")
    for code in codes[:10]:
        name = kiwoom.dynamicCall("GetMasterCodeName(QString)", code)
        print(f"  {code} → {name}")

kiwoom.OnEventConnect.connect(on_login)
kiwoom.OnReceiveConditionVer.connect(on_condition_load)
kiwoom.OnReceiveTrCondition.connect(on_receive_condition)
kiwoom.dynamicCall("CommConnect()")

app.exec_()