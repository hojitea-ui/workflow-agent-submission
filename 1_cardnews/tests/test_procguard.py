"""서버가 강제 종료돼도 하위 프로세스가 함께 끝나는지(Windows Job Object) 시험한다.
서버 대역(부모) 프로세스가 자식과 손자를 띄우고 오래 자는 동안, 부모만 강제 종료(자식은 건드리지 않음)한 뒤 자식·손자가 남는지 본다.
같은 시험을 보호 없이 하면 남아야 한다(대조군) — 그래야 시험이 보호의 효과를 구별한다."""
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

REPO = str(Path(__file__).resolve().parent.parent)

PARENT = r"""
import subprocess, sys, time
sys.path.insert(0, sys.argv[2])
from app import procguard
if sys.argv[1] == "guard":
    print("STATUS", procguard.protect_current_process(), flush=True)
else:
    print("STATUS none", flush=True)
child_code = ("import subprocess, sys, time\n"
              "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
              "print(g.pid, flush=True)\n"
              "time.sleep(120)\n")
child = subprocess.Popen([sys.executable, "-c", child_code], stdout=subprocess.PIPE, text=True)
grand = int(child.stdout.readline())
print("PIDS", child.pid, grand, flush=True)
time.sleep(120)
"""


def alive(pid: int) -> bool:
    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
    return str(pid) in out


def kill_pid(pid: int) -> None:
    subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)  # /T 없이: 이 프로세스 하나만 강제 종료


@unittest.skipUnless(os.name == "nt", "Job Object는 Windows 전용")
class ProcGuardTests(unittest.TestCase):
    def run_parent(self, mode: str):
        parent = subprocess.Popen([sys.executable, "-c", PARENT, mode, REPO], stdout=subprocess.PIPE, text=True)
        status = parent.stdout.readline().split(maxsplit=1)[1].strip()
        _, child, grand = parent.stdout.readline().split()
        pids = (parent.pid, int(child), int(grand))
        self.assertTrue(all(alive(p) for p in pids), "시작 직후에는 셋 다 살아 있어야 한다")
        kill_pid(parent.pid)  # 서버 강제 종료 흉내
        parent.wait(timeout=10)
        return status, pids

    def wait_dead(self, pids, seconds=8):
        end = time.time() + seconds
        while time.time() < end and any(alive(p) for p in pids):
            time.sleep(0.2)
        return [p for p in pids if alive(p)]

    def test_children_die_with_forced_kill_when_guarded(self):
        status, (parent, child, grand) = self.run_parent("guard")
        self.assertEqual(status, "job-object")
        left = self.wait_dead([child, grand])
        for p in left:
            kill_pid(p)
        self.assertEqual(left, [], "보호가 켜져 있으면 자식·손자가 부모와 함께 사라져야 한다")

    def test_control_without_guard_children_survive(self):
        status, (parent, child, grand) = self.run_parent("none")
        self.assertEqual(status, "none")
        time.sleep(2)
        survivors = [p for p in (child, grand) if alive(p)]
        for p in survivors:
            kill_pid(p)
        self.assertEqual(sorted(survivors), sorted([child, grand]), "대조군: 보호가 없으면 자식·손자가 남는다(문제 재현)")

    def test_protect_is_idempotent_and_reports_status(self):
        from app import procguard
        first = procguard.protect_current_process()
        self.assertEqual(procguard.protect_current_process(), first)
        self.assertTrue(first == "job-object" or first.startswith("failed"), first)


if __name__ == "__main__":
    unittest.main()
