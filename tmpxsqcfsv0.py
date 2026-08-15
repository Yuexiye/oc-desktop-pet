
import sys, os
code = int(os.environ.get("FAKE_CODE", "0"))
msg = os.environ.get("FAKE_MSG", "")
if msg: print(msg)
sys.exit(code)
