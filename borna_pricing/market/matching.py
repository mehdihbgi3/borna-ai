from __future__ import annotations

from dataclasses import dataclass, field

ENGINE_VERSION = "1.0"

REASONS = {
    "pn_exact": "پارت‌نامبر یکسان",
    "ecu_match": "ECU یکسان",
    "socket_match": "رنگ سوکت یکسان",
    "length_match": "طول سیم یکسان",
    "length_class_match": "رده طول سیم یکسان",
    "wire_count_match": "تعداد سیم یکسان",
    "wire_color_match": "رنگ سیم یکسان",
    "position_match": "موقعیت نصب یکسان",
    "design_match": "هم‌طرح",
    "vehicle_overlap": "خودروی مشترک",
    "not_o2": "آگهی سنسور اکسیژن نیست",
    "pn_other": "پارت‌نامبر آگهی مال کالای دیگری از برناست",
    "ecu_mismatch": "ECU متفاوت",
    "wire_count_mismatch": "تعداد سیم متفاوت",
    "socket_mismatch": "رنگ سوکت متفاوت",
    "length_mismatch": "طول سیم متفاوت",
    "pn_shared": "این پارت‌نامبر در کاتالوگ برنا به چند کالا وصل است",
    "ecu_multi": "آگهی چند ECU را با هم نام برده",
    "ecu_unknown": "ECU آگهی معلوم نیست",
    "wire_count_unknown": "تعداد سیم آگهی معلوم نیست",
    "socket_unknown": "رنگ سوکت آگهی معلوم نیست",
    "length_unknown": "طول سیم آگهی معلوم نیست",
    "length_class_mismatch": "رده طول سیم (کوتاه/متوسط/بلند) متفاوت",
    "length_unverified": "طول سیم یک طرف فقط به‌صورت کوتاه/متوسط/بلند معلوم است",
    "wire_color_mismatch": "رنگ سیم متفاوت",
    "wire_color_unknown": "رنگ سیم آگهی معلوم نیست",
    "position_mismatch": "موقعیت نصب (بالایی/پایینی) متفاوت",
    "generation_mismatch": "نسل سنسور متفاوت",
    "design_differs": "طرح متفاوت؛ جایگزین است، نه هم‌طرح",
    "vehicle_conflict": "خودروی آگهی با خودروی کالا جور نیست",
    "weak_signal": "نشانه فنی کافی برای تطبیق نیست",
    "manual": "انتخاب دستی کارشناس",
}
HARD = frozenset({"not_o2", "pn_other", "ecu_mismatch", "wire_count_mismatch", "socket_mismatch", "length_mismatch"})
POSITIVE = frozenset({"pn_exact", "ecu_match", "socket_match", "length_match", "length_class_match",
                      "wire_count_match", "wire_color_match", "position_match", "design_match", "vehicle_overlap",
                      "manual"})
SOFT = frozenset(REASONS) - HARD - POSITIVE
VERDICT_LABELS = {"strong": "تطبیق قوی", "candidate": "کاندید", "review": "نیاز به بررسی", "reject": "رد قاعده",
                  "manual": "دستی"}
VERDICT_RANK = {"strong": 0, "candidate": 1, "review": 2, "reject": 3, "manual": 0}
RELATION_LABELS = {"equivalent": "هم‌طرح", "substitute": "جایگزین"}
_DISCRIMINATING = (("ecu", "ecu_unknown"), ("socket_color", "socket_unknown"), ("wire_count", "wire_count_unknown"),
                   ("wire_color", "wire_color_unknown"))
_SCORES = {"pn_exact": 50, "ecu_match": 15, "socket_match": 10, "length_match": 10, "length_class_match": 5,
           "wire_count_match": 10, "wire_color_match": 5, "position_match": 3, "design_match": 5,
           "vehicle_overlap": 8}


@dataclass
class Verdict:
    product_key: str
    verdict: str = "review"
    reasons: list[str] = field(default_factory=list)
    score: int = 0
    relation: str = "equivalent"

    @property
    def hard(self) -> bool:
        return any(r in HARD for r in self.reasons)

    def add(self, reason: str) -> None:
        if reason not in self.reasons:
            self.reasons.append(reason)


def pn_owners(profiles: list[dict]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for p in profiles:
        for pn in p.get("part_nos") or []:
            out.setdefault(pn, [])
            if p["key"] not in out[pn]:
                out[pn].append(p["key"])
    return out


def _ext_ecus(e: dict) -> list[str]:
    return list(e.get("ecus") or ([e["ecu"]] if e.get("ecu") else []))


def evaluate(p: dict, e: dict, owners: dict[str, list[str]], tolerance_mm: int = 30) -> Verdict:
    v = Verdict(p["key"])
    if e.get("family", "o2") != "o2":
        v.add("not_o2")
    known_pns = [pn for pn in (e.get("part_nos") or []) if pn in owners]
    if known_pns:
        v.add("pn_exact" if set(known_pns) & set(p.get("part_nos") or []) else "pn_other")
    ecus = _ext_ecus(e)
    if p.get("ecu") and ecus:
        if p["ecu"] not in ecus:
            v.add("ecu_mismatch")
        elif len(ecus) > 1:
            v.add("ecu_multi")
        else:
            v.add("ecu_match")
    if p.get("wire_count") and e.get("wire_count"):
        v.add("wire_count_match" if int(p["wire_count"]) == int(e["wire_count"]) else "wire_count_mismatch")
    if p.get("socket_color") and e.get("socket_color"):
        v.add("socket_match" if p["socket_color"] == e["socket_color"] else "socket_mismatch")
    pm, em = p.get("length_mm"), e.get("length_mm")
    pc, ec = p.get("length_class"), e.get("length_class")
    if pm and em:
        v.add("length_match" if abs(int(pm) - int(em)) <= tolerance_mm else "length_mismatch")
    elif pc and ec:
        v.add("length_class_match" if pc == ec else "length_class_mismatch")
    elif (pm or pc) and (em or ec):
        v.add("length_unverified")
    if p.get("wire_color") and e.get("wire_color"):
        v.add("wire_color_match" if p["wire_color"] == e["wire_color"] else "wire_color_mismatch")
    if p.get("position") and e.get("position"):
        v.add("position_match" if p["position"] == e["position"] else "position_mismatch")
    if e.get("generation") and e.get("generation") != p.get("generation"):
        v.add("generation_mismatch")
    if p.get("design") and e.get("design"):
        if p["design"] == e["design"]:
            v.add("design_match")
        else:
            v.add("design_differs")
            v.relation = "substitute"
    pv, ev = set(p.get("vehicles") or []), set(e.get("vehicles") or [])
    if pv and ev:
        v.add("vehicle_overlap" if pv & ev else "vehicle_conflict")
    return v


def candidate_keys(e: dict, profiles: list[dict], owners: dict[str, list[str]]) -> list[str]:
    if e.get("family", "o2") != "o2":
        return []
    keys: list[str] = []

    def add(k):
        if k not in keys:
            keys.append(k)

    known = [pn for pn in (e.get("part_nos") or []) if pn in owners]
    if known:
        for pn in known:
            for k in owners[pn]:
                add(k)
        return keys
    ecus = set(_ext_ecus(e))
    ev = set(e.get("vehicles") or [])
    for p in profiles:
        if ecus and p.get("ecu") in ecus:
            add(p["key"])
        elif ev and set(p.get("vehicles") or []) & ev and (not p.get("ecu") or not ecus):
            add(p["key"])
        elif (not ecus and not ev and e.get("wire_count") and p.get("wire_count")
              and int(e["wire_count"]) == int(p["wire_count"])):
            add(p["key"])
    return keys


def _finish(v: Verdict) -> Verdict:
    v.score = sum(_SCORES.get(r, 0) for r in v.reasons) - 10 * sum(1 for r in v.reasons if r in SOFT)
    if v.hard:
        v.verdict = "reject"
    elif any(r in SOFT for r in v.reasons):
        v.verdict = "review"
    elif "pn_exact" in v.reasons:
        v.verdict = "strong"
    elif v.score >= 15:
        v.verdict = "candidate"
    else:
        v.add("weak_signal")
        v.verdict = "review"
        v.score -= 10
    return v


def match_listing(e: dict, profiles: list[dict], tolerance_mm: int = 30,
                  owners: dict[str, list[str]] | None = None) -> list[Verdict]:
    owners = owners if owners is not None else pn_owners(profiles)
    by_key = {p["key"]: p for p in profiles}
    verdicts = [evaluate(by_key[k], e, owners, tolerance_mm) for k in candidate_keys(e, profiles, owners)]
    alive = [v for v in verdicts if not v.hard]
    for attr, reason in _DISCRIMINATING:
        if e.get(attr) or (attr == "ecu" and _ext_ecus(e)):
            continue
        values = {by_key[v.product_key].get(attr) for v in alive} - {None}
        if len(values) >= 2:
            for v in alive:
                v.add(reason)
    if not e.get("length_mm") and not e.get("length_class"):
        mms = [int(by_key[v.product_key]["length_mm"]) for v in alive if by_key[v.product_key].get("length_mm")]
        classes = {by_key[v.product_key].get("length_class") for v in alive} - {None}
        if (mms and max(mms) - min(mms) > tolerance_mm) or len(classes) >= 2:
            for v in alive:
                v.add("length_unknown")
    for pn in [pn for pn in (e.get("part_nos") or []) if pn in owners]:
        sharing = [v for v in alive if pn in (by_key[v.product_key].get("part_nos") or [])]
        if len(sharing) > 1:
            for v in sharing:
                v.add("pn_shared")
    out = [_finish(v) for v in verdicts]
    out.sort(key=lambda v: (VERDICT_RANK[v.verdict], -v.score, v.product_key))
    return out


def reason_labels(reasons: list[str]) -> list[tuple[str, str]]:
    kind = {r: ("good" if r in POSITIVE else "crit" if r in HARD else "warn") for r in reasons}
    return [(kind[r], REASONS.get(r, r)) for r in reasons]
