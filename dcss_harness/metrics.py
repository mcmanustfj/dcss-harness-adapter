"""Compact action logs and offline statistics."""

import json
import time


def progress(player):
    return {key: player.get(key) for key in ("turn", "place", "depth", "xl")}


class ActionLog:
    """Small, append-only metrics; no observations or protocol payloads."""

    def __init__(self, path):
        self.stream = path.open("a", buffering=1, encoding="utf-8")
        self.count = 0
        self.last_finished = None

    def write(self, record):
        self.stream.write(json.dumps(record, ensure_ascii=False,
                                     separators=(",", ":")) + "\n")

    def start(self, player):
        self.write({"event": "start", "schema": 1, "ts": round(time.time(), 3),
                    "name": player.get("name"), **progress(player)})
        self.last_finished = time.monotonic()

    def record(self, action, requested, sent, before, player, started, timestamp,
               settled, running, error=None):
        finished = time.monotonic()
        self.count += 1
        record = {"event": "act", "n": self.count, "ts": timestamp,
                  "action": action, "requested": requested, "keys_sent": sent,
                  "turn_before": before, **progress(player),
                  "latency_ms": round((finished - started) * 1000, 3),
                  "idle_ms": round((started - self.last_finished) * 1000, 3),
                  "settled": settled, "running": running}
        if error is not None:
            record["error"] = str(error)[:200]
        self.write(record)
        self.last_finished = finished

    def close(self):
        self.stream.close()


def action_stats(path):
    """Summarize metrics without retaining individual action records."""
    result = {"log": str(path), "starts": 0, "actions": 0, "keys_sent": 0,
              "more_acknowledgments": 0, "combat_steps": 0, "recover_steps": 0, "wait_for_steps": 0, "ranged_steps": 0,
              "observed_turns": 0, "unsettled_actions": 0, "failed_actions": 0,
              "action_counts": {}, "latest": {}}
    timings = {key: {"min": None, "max": None, "total": 0}
               for key in ("latency_ms", "idle_ms")}
    previous_turn = None
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            # A reader may catch the writer halfway through its last record.
            if not line.endswith("\n"):
                break
            record = json.loads(line)
            if record["event"] == "combat_step":
                result["combat_steps"] += 1
                continue
            if record["event"] in ("recover_step", "wait_for_step", "ranged_step"):
                result[record["event"] + "s"] += 1
                continue
            if record["event"] == "auto_more":
                result["more_acknowledgments"] += record["count"]
                continue
            if record["event"] == "start":
                result["starts"] += 1
                previous_turn = record.get("turn")
                result["latest"] = progress(record)
                continue
            if record["event"] != "act":
                continue
            result["actions"] += 1
            result["keys_sent"] += record["keys_sent"]
            result["unsettled_actions"] += not record["settled"]
            result["failed_actions"] += "error" in record
            counts = result["action_counts"]
            action = record["action"]
            counts[action] = counts.get(action, 0) + 1
            turn = record.get("turn")
            if turn is not None:
                if previous_turn is not None:
                    result["observed_turns"] += max(0, turn - previous_turn)
                previous_turn = turn
            result["latest"] = progress(record)
            for key, values in timings.items():
                value = record[key]
                values["total"] += value
                values["min"] = value if values["min"] is None else min(values["min"], value)
                values["max"] = value if values["max"] is None else max(values["max"], value)
    for key, values in timings.items():
        result[key] = {"min": values["min"], "max": values["max"],
                       "mean": (round(values["total"] / result["actions"], 3)
                                if result["actions"] else None)}
    result["bytes"] = path.stat().st_size
    return result
