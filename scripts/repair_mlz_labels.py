"""Repair only legacy pin-wrapped material titles. Dry-run unless --apply is used."""
import argparse
import datetime
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.loader import load_bot_symbols

load_bot_symbols()

from bot import data_access as data
from bot.mlz_labels import legacy_mlz_label_plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--emoji-alias", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    emoji = data.get_emoji_alias(args.emoji_alias)
    if not emoji or not emoji.get("fallback") or not emoji.get("emoji_id"):
        raise ValueError("The selected emoji alias is missing or invalid.")
    style = {emoji["fallback"]: str(emoji["emoji_id"])}
    buttons = list(data._col("buttons").find(
        {"deleted": {"$ne": 1}},
        {"_id": 0, "id": 1, "parent_id": 1, "label": 1, "type": 1, "label_emojis": 1},
    ))
    plan = legacy_mlz_label_plan(buttons, style)
    print(json.dumps({"count": len(plan), "changes": plan}, ensure_ascii=False, indent=2))
    if not args.apply:
        return

    by_id = {button["id"]: button for button in buttons}
    affected_ids = {change["id"] for change in plan}
    for button_id in list(affected_ids):
        twin = data.get_twin(button_id)
        if twin is not None:
            affected_ids.add(twin)
    snapshots = []
    for button_id in sorted(affected_ids):
        button = data.get_btn_any(button_id)
        if not button:
            raise ValueError("A target button disappeared; no changes applied.")
        snapshots.append({
            "id": button_id, "label": button["label"],
            "had_label_emojis": "label_emojis" in button,
            "label_emojis": button.get("label_emojis"),
        })
    for change in plan:
        current = data.get_btn_any(change["id"])
        if current["label"] != by_id[change["id"]]["label"]:
            raise ValueError("A target title changed during planning; no changes applied.")

    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = Path(".local/backups") / f"mlz-labels-{timestamp}.json"
    backup.parent.mkdir(parents=True, exist_ok=True)
    backup.write_text(json.dumps({
        "previous_emoji_alias": data.get_setting("mlz_button_emoji_alias"),
        "buttons": snapshots,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    processed = set()
    for change in plan:
        if change["id"] in processed:
            continue
        current = data.get_btn_any(change["id"])
        if current["label"] != change["before"]:
            raise ValueError(f"Concurrent change detected; backup saved at {backup}.")
        data.upd_btn_label(change["id"], change["after"], label_emojis=style)
        processed.add(change["id"])
        twin = data.get_twin(change["id"])
        if twin is not None:
            processed.add(twin)
    data.set_setting("mlz_button_emoji_alias", args.emoji_alias)
    for change in plan:
        current = data.get_btn_any(change["id"])
        if current["label"] != change["after"] or current.get("label_emojis") != style:
            raise ValueError(f"Verification failed; backup saved at {backup}.")
    print(f"Verified {len(plan)} corrected titles. Backup: {backup}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Never print a database/network exception that may contain credentials.
        print(f"Repair stopped: {type(exc).__name__}", file=sys.stderr)
        raise SystemExit(1)
