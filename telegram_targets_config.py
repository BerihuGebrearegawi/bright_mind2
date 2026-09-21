"""Canonical BMT Telegram target registry.

B-owned configuration for the 6 real BMT channels and 6 real BMT groups.
Chat IDs are routing identifiers, not secrets. Bot tokens remain server-side
environment secrets and are intentionally absent from this file.
"""

BMT_TELEGRAM_TARGETS = [
    {"key": "general", "name": "𝗕𝗠𝗧 𝐆𝐄𝐍𝐄𝐑𝐀𝐋", "kind": "channel", "chatId": "-1002244140012", "purpose": "general", "grades": []},
    {"key": "grade_9_10_channel", "name": "𝗕𝗠𝗧 For 𝗚𝗿𝗮𝗱𝗲 𝟵-𝟭𝟬", "kind": "channel", "chatId": "-1003534125070", "purpose": "grade", "grades": ["9", "10"]},
    {"key": "grade_5_6_channel", "name": "𝗕𝗠𝗧 For 𝗚𝗿𝗮𝗱𝗲 𝟱-𝟲", "kind": "channel", "chatId": "-1004412366163", "purpose": "grade", "grades": ["5", "6"]},
    {"key": "grade_11_12_channel", "name": "𝗕𝗠𝗧 For 𝗚𝗿𝗮𝗱𝗲 𝟭𝟭-𝟭𝟮", "kind": "channel", "chatId": "-1003727728689", "purpose": "grade", "grades": ["11", "12"]},
    {"key": "academic_challenge", "name": "📖 𝗕𝗠𝗧 𝐀𝐜𝐚𝐝𝐞𝐦𝐢𝐜 𝐂𝐡𝐚𝐥𝐥𝐞𝐧𝐠𝐞 📓", "kind": "channel", "chatId": "-1004398819000", "purpose": "academic", "grades": []},
    {"key": "grade_7_8_channel", "name": "𝗕𝗠𝗧 For 𝗚𝗿𝗮𝗱𝗲 𝟳-𝟴", "kind": "channel", "chatId": "-1004440374107", "purpose": "grade", "grades": ["7", "8"]},
    {"key": "grade_7_8_group", "name": "𝗕𝗠𝗧 (𝗚𝗿𝗮𝗱𝗲 𝟳-𝟴)", "kind": "group", "chatId": "-1003863484698", "purpose": "grade", "grades": ["7", "8"]},
    {"key": "winners_awards", "name": "𝗪𝗶𝗻𝗻𝗲𝗿𝘀/𝗔𝘄𝗮𝗿𝗱𝘀", "kind": "group", "chatId": "-1003946288450", "purpose": "winners", "grades": []},
    {"key": "grade_5_6_group", "name": "𝗕𝗠𝗧 (𝗚𝗿𝗮𝗱𝗲 𝟱-𝟲)", "kind": "group", "chatId": "-1004302467840", "purpose": "grade", "grades": ["5", "6"]},
    {"key": "grade_9_10_group", "name": "𝗕𝗠𝗧 (𝗚𝗿𝗮𝗱𝗲 𝟵-𝟭𝟬)", "kind": "group", "chatId": "-1004405873434", "purpose": "grade", "grades": ["9", "10"]},
    {"key": "teacher_student_support", "name": "𝐓𝐞𝐚𝐜𝐡𝐞𝐫/𝐒𝐭𝐮𝐝𝐞𝐧𝐭 𝐒𝐮𝐩𝐩𝐨𝐫𝐭", "kind": "group", "chatId": "-1004463953278", "purpose": "support", "grades": []},
    {"key": "grade_11_12_group", "name": "𝗕𝗠𝗧 (𝗚𝗿𝗮𝗱𝗲 𝟭𝟭-𝟭𝟮)", "kind": "group", "chatId": "-1003953743474", "purpose": "grade", "grades": ["11", "12"]},
]

BMT_TELEGRAM_TARGETS_BY_KEY = {item["key"]: item for item in BMT_TELEGRAM_TARGETS}


def smart_targets_for(grade=None, purpose=None, extra_targets=None):
    """Smart List Builder: given a grade and/or an announcement purpose,
    returns the subset of BMT_TELEGRAM_TARGETS (plus any admin-added custom
    targets passed in extra_targets) that should receive it, instead of the
    admin having to remember which of the 12 channels/groups apply.

    purpose: "general" | "academic" | "grade" | "winners" | "support" | None
    grade: e.g. "9" - matches any target whose grades list includes it.
    """
    grade = str(grade or "").strip()
    pool = list(BMT_TELEGRAM_TARGETS) + list(extra_targets or [])
    picked = []
    for t in pool:
        t_purpose = t.get("purpose", "")
        t_grades = t.get("grades") or []
        if purpose == "winners":
            if t_purpose in {"winners", "academic"} or (grade and grade in t_grades):
                picked.append(t)
        elif purpose == "academic":
            if t_purpose == "academic" or (grade and grade in t_grades):
                picked.append(t)
        elif purpose == "support":
            if t_purpose == "support":
                picked.append(t)
        elif purpose == "general" or not purpose:
            if t_purpose == "general" or (grade and grade in t_grades):
                picked.append(t)
        else:
            if grade and grade in t_grades:
                picked.append(t)
    return picked
