"""Fast stdlib-only job: fake gradient descent on a quadratic. Used for smoke and e2e tests."""
import json
import math
import os
import time

params = json.loads(os.environ.get("PARAMS", "{}"))
lr = float(params.get("lr", 0.1))
epochs = int(params.get("epochs", 5))

w, target = 5.0, 1.0
for step in range(1, epochs + 1):
    w -= lr * 2 * (w - target)
    loss = (w - target) ** 2
    print("METRIC " + json.dumps({"step": step, "loss": round(loss, 6), "w": round(w, 6)}), flush=True)
    time.sleep(float(params.get("sleep", 0.05)))

with open(os.path.join(os.environ.get("OUT_DIR", "."), "result.json"), "w") as f:
    json.dump({"final_loss": loss, "diverged": math.isnan(loss) or loss > 1e6}, f)
