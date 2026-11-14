"""Self-learning human gate: review strategy drafts; only approved drafts are
written to experience.jsonl.

Usage:
  python3 approve.py list             # 查看待审草案
  python3 approve.py approve 0 [1 2]  # 批准指定序号（写入经验库）
  python3 approve.py reject 0         # 驳回
"""
import json
import os
import sys

DRAFT_PATH = os.path.join(os.path.dirname(__file__), "experience_drafts.jsonl")
EXP_PATH = os.path.join(os.path.dirname(__file__), "experience_strategies.jsonl")


def load_drafts():
    if not os.path.exists(DRAFT_PATH):
        return []
    return [json.loads(line) for line in open(DRAFT_PATH, encoding="utf-8") if line.strip()]


def save_drafts(drafts):
    with open(DRAFT_PATH, "w", encoding="utf-8") as f:
        for d in drafts:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")


def append_experience(d):
    entry = {
        "name": d.get("name") or d.get("concept", ""),
        "trigger": d.get("trigger", ""),
        "action": d.get("action") or d.get("fix", ""),
        "examples": d.get("examples") or [],
        "tags": d.get("tags") or [],
    }
    with open(EXP_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    drafts = load_drafts()
    if cmd == "list":
        pending = [(i, d) for i, d in enumerate(drafts) if d.get("status") == "pending"]
        if not pending:
            print("no pending drafts")
        for i, d in pending:
            print(f"[{i}] ({d.get('count', 1)}x) {d['concept']}: {d['error']} -> {d['fix']}")
        return
    idxs = [int(x) for x in sys.argv[2:]]
    for i in idxs:
        if 0 <= i < len(drafts):
            d = drafts[i]
            if cmd == "approve":
                append_experience(d)
                d["status"] = "approved"
                print(f"approved and written to the library: {d['concept']}")
            else:
                d["status"] = "rejected"
                print(f"rejected: {d['concept']}")
    save_drafts(drafts)


if __name__ == "__main__":
    main()
