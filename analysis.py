"""差评证据、可比体验与试接资格的经营分析。

三类结论互不抵消：

1. 严重承诺违约：有效在住订单承诺了专用服务、当时存在可出售条款与可执行房量，
   且异议核实为「核实未交付」。一条即成立，不能被平均分或其他好评解除。
2. 体验评分：仅统计在可匹配入住条件（旅店/房型/时段/客群）下的已观察回访。
3. 未获观察：未邀评、邀评未回复、未关联订单的匿名评论一律不计为满意。

匿名评论只能通过异议核实关联到订单；没有核实记录时只影响核查优先级，
不得按店型差评数量硬配。
"""
import argparse
import copy
import json
from collections import defaultdict
from pathlib import Path

from source_book import load

# 结论口径（供测试与外部程序引用，避免依赖散落字符串）
BREACH = "严重承诺违约"
CLEARED = "异议不成立"
PENDING_VERIFY = "待核实"
OBSERVED = "已观察"
NOT_INVITED = "未获观察-未邀评"
NO_RESPONSE = "未获观察-邀评未回复"
ANONYMOUS_UNLINKED = "未获观察-匿名未关联订单"
SUPPORTED = "支撑充分"
SUPPORT_WEAK = "支撑不足"
UNKNOWN = "未知"

# 访见选项里的床类简写统一映射到专用服务名
FEATURE_ALIAS = {"床": "儿童床"}


# ---------- 通用工具 ----------

def _latest_appeal(appeals, feedback_id):
    """按 seq 返回某条评论最新一条异议记录；原回访永不被覆盖。"""
    records = [a for a in appeals if a["feedback"] == feedback_id]
    return max(records, key=lambda r: r["seq"]) if records else None


def _is_special_need(need):
    return need not in (None, "普通")


def _room_inventory_key(inv):
    return inv["hotel"], inv["room"], inv["window"], inv["feature"]


# ---------- 评论与订单的证据对应 ----------

def link_feedback(book):
    """把每条评论对应到实际交付服务、当时可出售条款和有效在住订单。

    返回字段：
    - linkage：observe 为观察状态；stay 仅在有订单关联时给出
    - anonymous_priority：未关联匿名评论的核查优先级（专用服务抱怨优先）
    """
    stays = {s["stay"]: s for s in book["stays"]}
    feedback_rows = book["feedback"]
    appeals = book["appeals"]
    special_by_hotel = {p["hotel"]: set(p["special"]) for p in book["properties"]}
    inventory = {_room_inventory_key(i): i["rooms"] for i in book["room_inventory"]}

    linkage = []
    anonymous_priority = []
    for fb in feedback_rows:
        fid = fb["feedback"]
        appeal = _latest_appeal(appeals, fid)
        linked_stay = None
        linked_via = None

        # 异议核实记录的 verified_stay 是匿名评论唯一允许的关联途径
        if appeal is not None and appeal["verified_stay"]:
            linked_stay = stays[appeal["verified_stay"]]
            linked_via = "异议核实"
        elif fb["stay_candidates"]:
            linked_stay = stays[fb["stay_candidates"][0]]
            linked_via = "渠道回访"

        if linked_stay is None:
            observe = ANONYMOUS_UNLINKED
        elif fb["invited"] is False:
            observe = NOT_INVITED
        elif fb["score"] is None:
            observe = NO_RESPONSE
        else:
            observe = OBSERVED

        # 异议是否改变证据可信度：
        # 核实未交付=违约成立；已提供/登记遗漏=不成立；待复核等中间态不进任何终态
        active_finding = appeal["finding"] if appeal is not None else None
        breach_finding = active_finding == "核实未交付"
        cleared = active_finding == "已提供但登记遗漏"

        entry = {
            "feedback": fid,
            "observe": observe,
            "linked_via": linked_via,
            "stay": linked_stay["stay"] if linked_stay else None,
            "hotel": linked_stay["hotel"] if linked_stay else None,
            "room": linked_stay["room"] if linked_stay else None,
            "window": linked_stay["window"] if linked_stay else None,
            "party": linked_stay["party"] if linked_stay else None,
            "promise": list(linked_stay["promise"]) if linked_stay else [],
            "score": fb["score"],
            "concern": fb["concern"],
            "appeal_finding": active_finding,
            "active_breach_finding": breach_finding,
            "cleared": cleared,
            "salable_terms": [],
            "inventory_rooms": [],
        }

        if linked_stay is not None:
            terms = []
            rooms_seen = []
            for feature in linked_stay["promise"]:
                key = (linked_stay["hotel"], linked_stay["room"],
                       linked_stay["window"], feature)
                terms.append(feature)
                rooms_seen.append({"feature": feature,
                                   "rooms": inventory.get(key, 0)})
            entry["salable_terms"] = terms
            entry["inventory_rooms"] = rooms_seen

        linkage.append(entry)

        if observe == ANONYMOUS_UNLINKED and fb["concern"]:
            all_special = set().union(*special_by_hotel.values())
            is_special = fb["concern"] in all_special
            anonymous_priority.append({
                "feedback": fid,
                "concern": fb["concern"],
                "priority": "高" if is_special else "普通",
                "reason": "专用服务承诺类抱怨，未关联订单，仅排核查不硬配",
            })

    anonymous_priority.sort(key=lambda r: (0 if r["priority"] == "高" else 1,
                                           r["feedback"]))
    return linkage, anonymous_priority


def find_breaches(linkage):
    """严重承诺违约：有效订单 + 承诺专用服务 + 可出售条款/房量 + 核实未交付。"""
    breaches = []
    for entry in linkage:
        if not entry["active_breach_finding"]:
            continue
        if not entry["promise"] or entry["stay"] is None:
            continue
        executable = [r for r in entry["inventory_rooms"] if r["rooms"] > 0]
        if not executable:
            continue
        breaches.append({
            "feedback": entry["feedback"],
            "stay": entry["stay"],
            "hotel": entry["hotel"],
            "room": entry["room"],
            "window": entry["window"],
            "party": entry["party"],
            "broken_promises": list(entry["promise"]),
            "salable_terms": entry["salable_terms"],
            "executable_rooms": executable,
            "basis": "有效在住订单承诺专用服务、当时可出售且有可执行房量，异议核实未交付",
        })
    return breaches


def cleared_allegations(linkage):
    """异议成立状态改变客群/可信度：被推翻的抱怨单独留痕，不计违约也不当满意。"""
    return [{
        "feedback": e["feedback"],
        "stay": e["stay"],
        "concern": e["concern"],
        "appeal_finding": e["appeal_finding"],
        "note": "原抱怨保留但可信度被推翻，不计违约；原评分不被改写",
    } for e in linkage if e["cleared"]]


def pending_concerns(linkage):
    """有抱怨但尚无核实结论：挂起，既不判违约也不用平均抵消。"""
    return [{
        "feedback": e["feedback"],
        "stay": e["stay"],
        "hotel": e["hotel"],
        "concern": e["concern"],
        "observe": e["observe"],
        "status": PENDING_VERIFY,
    } for e in linkage
        if e["concern"] and not e["active_breach_finding"] and not e["cleared"]]


# ---------- 可比入住条件下的体验支撑度 ----------

def _stay_condition(stay):
    return stay["hotel"], stay["room"], stay["window"], stay["party"]


def comparable_segments(book, linkage, min_comparable):
    """按可匹配入住条件分段；只统计已观察回访，缺评价不填满意。"""
    stays = {s["stay"]: s for s in book["stays"]}
    observed_by_stay = {e["stay"]: e for e in linkage if e["observe"] == OBSERVED}

    segments = {}
    for stay in book["stays"]:
        key = _stay_condition(stay)
        bucket = segments.setdefault(key, {
            "hotel": key[0], "room": key[1], "window": key[2], "party": key[3],
            "stays": [], "observed": [], "unobserved": [],
        })
        bucket["stays"].append(stay["stay"])
        entry = observed_by_stay.get(stay["stay"])
        if entry is not None:
            # 已核实的承诺违约归入违约桶，不进体验差距；被推翻的抱怨也不计体验问题
            experience_concern = bool(entry["concern"]) \
                and not entry["cleared"] and not entry["active_breach_finding"]
            bucket["observed"].append({
                "feedback": entry["feedback"], "score": entry["score"],
                "concern": entry["concern"],
                "concern_active": experience_concern,
            })
        else:
            linked = next((e for e in linkage if e["stay"] == stay["stay"]), None)
            bucket["unobserved"].append({
                "stay": stay["stay"],
                "reason": linked["observe"] if linked else UNKNOWN,
            })

    result = []
    for key, bucket in sorted(segments.items()):
        n_observed = len(bucket["observed"])
        scores = [o["score"] for o in bucket["observed"] if o["score"] is not None]
        active_concerns = [o for o in bucket["observed"] if o["concern_active"]]
        support = SUPPORTED if n_observed >= min_comparable else SUPPORT_WEAK
        result.append({
            "segment": {"hotel": key[0], "room": key[1],
                        "window": key[2], "party": key[3]},
            "stay_count": len(bucket["stays"]),
            "observed_count": n_observed,
            "unobserved_count": len(bucket["unobserved"]),
            "unobserved": bucket["unobserved"],
            "support": support,
            "avg_score": (sum(scores) / len(scores)) if scores else None,
            "active_concern_rate": (len(active_concerns) / n_observed)
            if n_observed else None,
            "concerns": [o["concern"] for o in active_concerns],
            "unknown_note": None if n_observed else "该可匹配条件下没有任何已观察回访",
        })
    return result


def calibrate_by_party(segments):
    """同类客群校正：大团与散客分开汇总，避免把等房问题平均到全体。"""
    acc = defaultdict(lambda: {"observed": 0, "stays": 0, "concerns": 0,
                               "score_sum": 0, "score_n": 0})
    for seg in segments:
        a = acc[seg["segment"]["party"]]
        a["stays"] += seg["stay_count"]
        a["observed"] += seg["observed_count"]
        a["concerns"] += len(seg["concerns"])
        if seg["avg_score"] is not None:
            a["score_sum"] += seg["avg_score"] * seg["observed_count"]
            a["score_n"] += seg["observed_count"]
    out = []
    for party, a in sorted(acc.items()):
        out.append({
            "party": party,
            "stays": a["stays"],
            "observed": a["observed"],
            "active_concern_rate": (a["concerns"] / a["observed"]) if a["observed"] else None,
            "avg_score": (a["score_sum"] / a["score_n"]) if a["score_n"] else None,
            "note": "仅按同类客群比较；未观察订单不进入分母",
        })
    return out


def coverage_summary(book, linkage):
    """渠道邀评覆盖与未获观察清单。"""
    stays = {s["stay"]: s for s in book["stays"]}
    invited = sum(1 for f in book["feedback"] if f["invited"] is True)
    responded = sum(1 for f in book["feedback"]
                    if f["invited"] is True and f["score"] is not None)
    unobserved = [{
        "stay": e["stay"], "hotel": e["hotel"], "reason": e["observe"],
    } for e in linkage if e["stay"] and e["observe"] != OBSERVED]
    return {
        "invited_feedback": invited,
        "responded_feedback": responded,
        "response_rate_among_invited": (responded / invited) if invited else None,
        "unobserved_stays": unobserved,
        "rule": "未邀评/未回复/匿名未关联一律不计满意",
    }


# ---------- 商户下周去向 ----------

def merchant_outlook(segments, breaches, linkage):
    """信息不足 / 证实履约问题 / 校正后体验差距 三类去向分开列示。"""
    breach_hotels = {b["hotel"] for b in breaches}
    weak_hotels = {s["segment"]["hotel"] for s in segments
                   if s["support"] == SUPPORT_WEAK}
    all_hotels = sorted({s["segment"]["hotel"] for s in segments})

    outlook = []
    for hotel in all_hotels:
        statuses = []
        if hotel in breach_hotels:
            statuses.append({
                "status": "证实履约问题",
                "detail": sorted({p for b in breaches if b["hotel"] == hotel
                                  for p in b["broken_promises"]}),
            })
        hotel_segments = [s for s in segments if s["segment"]["hotel"] == hotel]
        gaps_view = [{"segment": s["segment"], "concern_rate": s["active_concern_rate"]}
                     for s in hotel_segments
                     if s["active_concern_rate"] is not None and s["active_concern_rate"] > 0]
        if gaps_view:
            statuses.append({"status": "同类客群校正后的体验差距", "detail": gaps_view})
        if hotel in weak_hotels:
            statuses.append({
                "status": "信息不足",
                "detail": [{"segment": s["segment"],
                            "observed": s["observed_count"],
                            "missing_conditions": s["unobserved"]}
                           for s in hotel_segments if s["support"] == SUPPORT_WEAK],
            })
        outlook.append({"hotel": hotel, "outlook": statuses})
    return outlook


# ---------- 抽查计划：一次到访、一次代价、多条件见证 ----------

def _parse_witness_option(hotel, option):
    parts = option.split("/")
    room, window = parts[0], parts[1]
    feature = FEATURE_ALIAS.get(parts[2], parts[2]) if len(parts) > 2 else "普通"
    return {"hotel": hotel, "room": room, "window": window, "feature": feature}


def eligible_gap_conditions(book, segments):
    """缺证条件：有可执行房量（可出售条款）但已观察回访不足的房源/时段/服务。"""
    min_comparable = book["limits"][0]["min_comparable"]
    obs = {}
    for seg in segments:
        s = seg["segment"]
        key = (s["hotel"], s["room"], s["window"])
        obs.setdefault(key, 0)
        obs[key] += seg["observed_count"]

    gaps = []
    for inv in book["room_inventory"]:
        key = (inv["hotel"], inv["room"], inv["window"])
        if obs.get(key, 0) < min_comparable:
            gaps.append({"hotel": inv["hotel"], "room": inv["room"],
                         "window": inv["window"], "feature": inv["feature"],
                         "observed": obs.get(key, 0),
                         "inventory_rooms": inv["rooms"]})
    # 同楼不同条件不自动合并：按 (旅店,房型,时段,服务) 去重
    dedup = {tuple(g[k] for k in ("hotel", "room", "window", "feature")): g
             for g in gaps}
    return sorted(dedup.values(),
                  key=lambda g: (g["hotel"], g["room"], g["window"], g["feature"]))


def plan_visits(book, gaps):
    """在抽查经费内选择到访组合；一次到访见证多条件只计一次代价。"""
    budget = book["limits"][0]["visit_budget"]
    gap_set = {(g["hotel"], g["room"], g["window"], g["feature"]) for g in gaps}

    options = []
    for v in book["visits"]:
        witnesses = [_parse_witness_option(v["hotel"], o)
                     for o in v["witness_options"]]
        covered = [w for w in witnesses
                   if (w["hotel"], w["room"], w["window"], w["feature"]) in gap_set]
        options.append({"slot": v["slot"], "hotel": v["hotel"],
                        "cost": v["cost"], "capacity": v["capacity"],
                        "covered": covered})

    options.sort(key=lambda o: (o["cost"], o["slot"]))
    chosen, spent, covered_keys = [], 0, set()
    for opt in options:
        if not opt["covered"]:
            continue
        new_cover = [w for w in opt["covered"]
                     if (w["hotel"], w["room"], w["window"], w["feature"])
                     not in covered_keys]
        # 一次到访最多见证 capacity 个条件；超出的留待后续，不自动算覆盖
        new_cover = new_cover[:opt["capacity"]]
        if not new_cover:
            continue
        if spent + opt["cost"] > budget:
            continue
        spent += opt["cost"]
        covered_keys.update((w["hotel"], w["room"], w["window"], w["feature"])
                            for w in new_cover)
        chosen.append({"slot": opt["slot"], "hotel": opt["hotel"],
                       "cost": opt["cost"],
                       "witnessed_conditions": new_cover,
                       "shared_visit": len(new_cover) > 1})

    uncovered = [g for g in gaps
                 if (g["hotel"], g["room"], g["window"], g["feature"])
                 not in covered_keys]
    return {"budget": budget, "spent": spent, "visits": chosen,
            "uncovered_gaps": uncovered,
            "rule": "同一到访见证多条件只计一次费用；同楼未列入 witness_options 的条件不算覆盖"}


# ---------- 试接批次：必须补缺证，专用客户不填试接名单 ----------

def plan_trials(book, gaps, visit_plan, linkage):
    limits = book["limits"][0]
    breaches = find_breaches(linkage)
    # 未解决违约的专用床位已在接团处置中冻结，不能再算作试接可执行房量
    frozen_keys = {(b["hotel"], f) for b in breaches
                   for f in b["broken_promises"]}
    committed = defaultdict(int)
    for row in book["intake"]:
        if row["state"] == "已接":
            committed[(row["hotel"], row["need"])] += row["rooms"]

    gap_by_hotel_feature = defaultdict(list)
    for g in gaps:
        gap_by_hotel_feature[(g["hotel"], g["feature"])].append(g)

    trials, used = [], 0
    protected_used = 0
    for row in book["intake"]:
        if row["state"] != "预留试接":
            continue
        need = row["need"]
        feature = need if _is_special_need(need) else "普通"
        hotel = row["hotel"]
        matched_gaps = [g for g in gap_by_hotel_feature.get((hotel, feature), [])]
        fills_gap = bool(matched_gaps)

        inv_rows = [i for i in book["room_inventory"]
                    if i["hotel"] == hotel and i["feature"] == feature]
        capacity = 0
        for i in inv_rows:
            available = i["rooms"] - committed.get((hotel, feature), 0)
            if (hotel, feature) in frozen_keys:
                available = 0
            capacity += max(0, available)

        open_breach = (hotel, feature) in frozen_keys
        within_quota = used + row["rooms"] <= limits["trial_rooms"]
        protected_ok = True
        if _is_special_need(need):
            protected_ok = protected_used + row["rooms"] <= limits["protected_rooms"]

        eligible = fills_gap and within_quota and protected_ok and not open_breach \
            and capacity >= row["rooms"]
        if eligible:
            used += row["rooms"]
            if _is_special_need(need):
                protected_used += row["rooms"]
        trials.append({
            "group": row["group"], "hotel": hotel, "need": need,
            "rooms": row["rooms"], "eligible": eligible,
            "evidence_filled": [
                {"hotel": g["hotel"], "room": g["room"], "window": g["window"],
                 "feature": g["feature"], "observed_before": g["observed"]}
                for g in matched_gaps],
            "available_capacity": capacity,
            "reject_reasons": [r for r, ok in [
                ("未指向任何缺证房型或时段", not fills_gap),
                ("超出试接量上限", not within_quota),
                ("超出专用保护保留上限", not protected_ok),
                ("存在未解决的专用房违约，专用客户不得用于试接", open_breach),
                ("可执行房量不足", capacity < row["rooms"]),
            ] if ok],
            "paired_visit_slots": [v["slot"] for v in visit_plan["visits"]
                                   if v["hotel"] == hotel],
            "open_obligations": sorted({
                f for b in breaches if b["hotel"] == hotel
                for f in b["broken_promises"]}),
        })
    return {"trial_rooms_limit": limits["trial_rooms"],
            "protected_rooms_limit": limits["protected_rooms"],
            "trials": trials,
            "rule": "试接必须补到缺证房型或时段；已接专用客户不进入试接名单"}


# ---------- 已接/待接团处置与回访后的重新判断 ----------

def decide_intake(book, linkage):
    breaches = find_breaches(linkage)
    breach_hotel_features = defaultdict(set)
    for b in breaches:
        for f in b["broken_promises"]:
            breach_hotel_features[b["hotel"]].add(f)

    committed = defaultdict(int)
    for row in book["intake"]:
        if row["state"] == "已接":
            committed[(row["hotel"], row["need"])] += row["rooms"]

    decisions, frozen = [], []
    for row in book["intake"]:
        need = row["need"]
        feature = need if _is_special_need(need) else "普通"
        open_breach = feature in breach_hotel_features.get(row["hotel"], set())

        if row["state"] == "已接":
            if open_breach:
                alt_feasible = None
                for alt in row["alternatives"]:
                    alt_inv = [i for i in book["room_inventory"]
                               if i["hotel"] == alt and i["feature"] == feature]
                    free = sum(max(0, i["rooms"]
                                   - committed.get((alt, feature), 0))
                               for i in alt_inv)
                    if free >= row["rooms"]:
                        alt_feasible = alt
                        break
                if alt_feasible is not None:
                    decision = "启用合约认可替代房源"
                    detail = {"alternative": alt_feasible}
                else:
                    decision = "转人工协商"
                    detail = {"alternatives": row["alternatives"],
                              "reason": "无合约认可且房量可行的替代；相应床位冻结不得悄悄重新上架"}
                    frozen.append({"hotel": row["hotel"], "feature": feature,
                                   "rooms": row["rooms"], "group": row["group"]})
            else:
                decision, detail = "履约条件当前成立，维持已接", {}
        elif row["state"] == "待接":
            if open_breach:
                decision, detail = "暂停接团", {"reason": "未解决的专用房违约"}
            else:
                decision, detail = "信息不足，须先补证或安排有保护试接", {}
        else:  # 预留试接
            decision, detail = "随试接批次评估", {}

        decisions.append({"group": row["group"], "hotel": row["hotel"],
                          "need": need, "rooms": row["rooms"],
                          "state": row["state"], "open_breach": open_breach,
                          "decision": decision, "detail": detail})
    return {"decisions": decisions, "frozen_beds": frozen,
            "rule": "回访改变风险判断后，待接试团/普通续接/专用承诺分别重新判断；无法替代的违约床位冻结"}


def build_report(book):
    min_comparable = book["limits"][0]["min_comparable"]
    linkage, anonymous_priority = link_feedback(book)
    breaches = find_breaches(linkage)
    segments = comparable_segments(book, linkage, min_comparable)
    gaps = eligible_gap_conditions(book, segments)
    visit_plan = plan_visits(book, gaps)
    return {
        "口径": {
            "三类结论互不抵消": [BREACH, "体验评分（仅已观察）", "未获观察"],
            "匿名关联规则": "匿名评论仅可凭异议核实关联订单，否则只排核查优先级",
            "最低可比观察": min_comparable,
        },
        "评论证据对应": linkage,
        "匿名核查优先级": anonymous_priority,
        "严重承诺违约": breaches,
        "被推翻的抱怨": cleared_allegations(linkage),
        "待核实抱怨": pending_concerns(linkage),
        "可比体验分段": segments,
        "客群校正汇总": calibrate_by_party(segments),
        "邀评覆盖": coverage_summary(book, linkage),
        "商户下周去向": merchant_outlook(segments, breaches, linkage),
        "缺证条件": gaps,
        "抽查计划": visit_plan,
        "试接批次": plan_trials(book, gaps, visit_plan, linkage),
        "接团处置": decide_intake(book, linkage),
    }


def main():
    parser = argparse.ArgumentParser(description="差评证据与试接资格经营分析")
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    print(json.dumps(build_report(load(args.path)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
