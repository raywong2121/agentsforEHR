"""跑管线的通用入口：python3 run_cases.py cases.json predictions.json"""
import json
import sys
import threading
import time

import pipeline


def run_with_timeout(fn, args, seconds=300):
    """在独立线程里跑，超时即放弃该用例。"""
    box = {"res": None, "err": None}

    def target():
        try:
            box["res"] = fn(*args)
        except Exception as e:
            box["err"] = e

    th = threading.Thread(target=target, daemon=True)
    th.start()
    th.join(seconds)
    if th.is_alive():
        return None, TimeoutError(f"case exceeded {seconds}s")
    return box["res"], box["err"]


def main():
    cases_path = sys.argv[1] if len(sys.argv) > 1 else "cases.json"
    out_path = sys.argv[2] if len(sys.argv) > 2 else "predictions_flash.json"
    cases = json.load(open(cases_path, encoding="utf-8"))["cases"]
    preds, errors = [], []
    t0 = time.time()
    for i, c in enumerate(cases, 1):
        res, err = run_with_timeout(pipeline.run_case, (c["text"],), seconds=300)
        if res is not None:
            res["id"] = c["id"]
            preds.append(res)
            mapped = sum(1 for r in res["rows"] if r["status"] == "mapped")
            print(f"[{i:02d}/{len(cases)}] {c['id']} mapped={mapped} mentions={len(res['rows'])}", flush=True)
        else:
            errors.append({"id": c["id"], "error": str(err)[:200]})
            preds.append({"id": c["id"], "text": c["text"], "rows": [], "error": str(err)[:200]})
            print(f"[{i:02d}/{len(cases)}] {c['id']} TIMEOUT/ERROR {str(err)[:120]}", flush=True)
        json.dump({"preds": preds, "errors": errors}, open(out_path, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
    print(f"\ndone in {time.time()-t0:.1f}s; cases={len(preds)} errors={len(errors)}", flush=True)


if __name__ == "__main__":
    main()
