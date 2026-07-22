# -*- coding: utf-8 -*-
"""이 노트북에 하이브리드 감시자를 작업 스케줄러로 등록한다 (PC당 1회).

hybrid_poller 를 5분마다 돌려, 서버가 낸 하이브리드 검색 요청이 있으면 앞장 몫을
집어 돈다. 노트북이 꺼져 있으면 안 돌고, 그때는 서버가 전국을 전담한다.

실행:  py setup_hybrid_laptop.py         (등록)
       py setup_hybrid_laptop.py --off   (해제)
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TASK = "Daangn-Hybrid-Laptop"
POLLER = os.path.join(HERE, "hybrid_poller.py")
WRAP = os.path.join(HERE, "run_hybrid_poller.cmd")


def sh(cmd):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


if "--off" in sys.argv:
    code, out = sh('schtasks /delete /tn "%s" /f' % TASK)
    print("해제:", "ok" if code == 0 else out[:120])
    sys.exit(0)

# pythonw 로 돌려 콘솔 창이 안 뜨게 한다.
pyw = sys.executable.replace("python.exe", "pythonw.exe")
if not os.path.exists(pyw):
    pyw = sys.executable
log = os.path.join(HERE, "hybrid_poller.log")
with open(WRAP, "w", encoding="ascii") as f:
    f.write("@echo off\r\nchcp 65001 >nul\r\n"
            'cd /d "%s"\r\n"%s" "%s" >> "%s" 2>&1\r\n' % (HERE, pyw, POLLER, log))

# 5분마다, 로그인 세션에서(집 IP 필요). /ru 로 현재 사용자.
code, out = sh('schtasks /create /tn "%s" /tr "\\"%s\\"" /sc minute /mo 5 /f' % (TASK, WRAP))
if code == 0:
    print("OK: '%s' 등록 — 5분마다 하이브리드 요청 확인" % TASK)
    print("   노트북 켜져 있을 때만 앞장 몫(1-2/3)을 돌립니다.")
else:
    print("FAIL:", out[:200])
    sys.exit(1)
