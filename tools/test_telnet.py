"""兼容入口：等价于同目录下的 check_telnet.py（本文件只是别名，参数与它完全一致）。

用法：
    python test_telnet.py
    python test_telnet.py 192.168.31.1
    python test_telnet.py 192.168.31.1 root 你的密码
"""

from __future__ import annotations

import os
import runpy
import sys

TARGET = os.path.join(os.path.dirname(os.path.abspath(__file__)), "check_telnet.py")
sys.argv[0] = "check_telnet.py"
runpy.run_path(TARGET, run_name="__main__")
