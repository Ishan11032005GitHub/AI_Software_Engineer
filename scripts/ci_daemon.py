import time
import subprocess

INTERVAL = 120   # check every 2 minutes

while True:
    print("🔁 CI Watcher: Checking for failed PRs...")
    subprocess.run(["python","-m","app.main","Ishan11032005GitHub/AutoTriage-PR-Agent"])
    time.sleep(INTERVAL)
