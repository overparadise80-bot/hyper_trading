# -*- coding: utf-8 -*-
from collections import deque
from PyQt5.QtCore import QTimer


class KiwoomQueue:
    """글로벌 TR 큐 — 키움 OCX 호출 직렬화 (모듈 간 동시 호출 충돌 방지)

    사용 패턴:
        queue.push(lambda: kiwoom.dynamicCall(...))  # 요청 등록
        queue.done()                                  # 응답 수신 완료 (핸들러 안에서 호출)
    """
    MIN_INTERVAL = 200  # ms: 요청 사이 최소 대기 시간

    def __init__(self):
        self._q     = deque()
        self._busy  = False
        self._timer = QTimer()
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._fire_next)

    def push(self, fn):
        """OCX 호출 fn을 큐에 추가. 큐가 비어있으면 즉시 실행."""
        self._q.append(fn)
        if not self._busy:
            self._fire_next()

    def done(self):
        """TR 응답 수신 완료 알림 → MIN_INTERVAL 후 다음 요청 실행."""
        self._timer.start(self.MIN_INTERVAL)

    def _fire_next(self):
        if not self._q:
            self._busy = False
            return
        self._busy = True
        fn = self._q.popleft()
        try:
            fn()
        except Exception as e:
            print(f"[KiwoomQueue] 오류: {e}")
            self._timer.start(self.MIN_INTERVAL)
