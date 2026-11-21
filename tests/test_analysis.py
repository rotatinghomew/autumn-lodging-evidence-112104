"""差评证据、试接资格与重新判断的规则测试。"""
import copy
from pathlib import Path
import unittest

from source_book import load
import analysis


def append_row(book, table, values):
    """深拷贝样册追加行时，按 fields 转成与 load() 一致的字典行。"""
    fields = book[table][0].keys() if book[table] else []
    book[table].append(dict(zip(fields, values)))


class AnalysisTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.book = load(Path(__file__).resolve().parents[1] / "data" / "samples.json")
        cls.linkage, cls.priority = analysis.link_feedback(cls.book)
        cls.breaches = analysis.find_breaches(cls.linkage)
        cls.segments = analysis.comparable_segments(
            cls.book, cls.linkage, cls.book["limits"][0]["min_comparable"])
        cls.gaps = analysis.eligible_gap_conditions(cls.book, cls.segments)
        cls.visits = analysis.plan_visits(cls.book, cls.gaps)
        cls.trials = analysis.plan_trials(
            cls.book, cls.gaps, cls.visits, cls.linkage)
        cls.intake = analysis.decide_intake(cls.book, cls.linkage)

    def _entry(self, fid):
        return next(e for e in self.linkage if e["feedback"] == fid)

    # ---- 证据对应与异议 ----

    def test_anonymous_only_links_through_appeal(self):
        r7 = self._entry("r7")
        self.assertEqual(r7["observe"], analysis.OBSERVED)
        self.assertEqual(r7["linked_via"], "异议核实")
        self.assertEqual(r7["stay"], "s1")
        # 原始 stay_candidates 为空，证明没有按店型差评硬配
        raw = next(f for f in self.book["feedback"] if f["feedback"] == "r7")
        self.assertEqual(raw["stay_candidates"], [])

    def test_unlinked_anonymous_never_force_matched(self):
        book = copy.deepcopy(self.book)
        book["appeals"] = [a for a in book["appeals"] if a["feedback"] != "r7"]
        linkage, priority = analysis.link_feedback(book)
        r7 = next(e for e in linkage if e["feedback"] == "r7")
        self.assertIsNone(r7["stay"])
        self.assertEqual(r7["observe"], analysis.ANONYMOUS_UNLINKED)
        self.assertEqual(priority[0]["feedback"], "r7")
        self.assertEqual(priority[0]["priority"], "高")
        # 匿名未关联时不产生违约
        self.assertEqual(analysis.find_breaches(linkage), [])

    def test_appeal_timeline_latest_wins_but_original_kept(self):
        # r3 最新 seq=3 为「已提供但登记遗漏」，推翻 seq=1 的待复核
        r3 = self._entry("r3")
        self.assertTrue(r3["cleared"])
        self.assertEqual(r3["appeal_finding"], "已提供但登记遗漏")
        self.assertEqual(r3["score"], 1)  # 原评分不被改写
        self.assertNotIn("r3", [b["feedback"] for b in self.breaches])
        cleared = analysis.cleared_allegations(self.linkage)
        self.assertEqual([c["feedback"] for c in cleared], ["r3"])

    def test_breach_requires_order_term_and_inventory(self):
        self.assertEqual(len(self.breaches), 1)
        b = self.breaches[0]
        self.assertEqual(b["feedback"], "r7")
        self.assertEqual(b["hotel"], "山庭")
        self.assertEqual(b["broken_promises"], ["儿童床"])
        self.assertTrue(all(r["rooms"] > 0 for r in b["executable_rooms"]))

    def test_pending_appeal_is_neither_breach_nor_cleared(self):
        book = copy.deepcopy(self.book)
        # 去掉 r3 的终态裁定，只剩 seq=1 的「待复核」
        book["appeals"] = [a for a in book["appeals"]
                           if not (a["feedback"] == "r3" and a["seq"] == 3)]
        linkage, _ = analysis.link_feedback(book)
        r3 = next(e for e in linkage if e["feedback"] == "r3")
        self.assertFalse(r3["active_breach_finding"])
        self.assertFalse(r3["cleared"])
        # r3 自身不构成违约（r7 的违约是另一条独立证据，不受影响）
        self.assertNotIn("r3", [b["feedback"] for b in
                                analysis.find_breaches(linkage)])
        # 抱怨仍挂在待核实，不能被平均分解除
        pending = {p["feedback"] for p in analysis.pending_concerns(linkage)}
        self.assertIn("r3", pending)

    def test_no_inventory_no_breach_even_if_finding_says_undelivered(self):
        book = copy.deepcopy(self.book)
        for inv in book["room_inventory"]:
            if inv["hotel"] == "山庭" and inv["feature"] == "儿童床":
                inv["rooms"] = 0
        linkage, _ = analysis.link_feedback(book)
        self.assertEqual(analysis.find_breaches(linkage), [])

    # ---- 未获观察不得当满意 ----

    def test_uninvited_and_no_response_are_unobserved(self):
        self.assertEqual(self._entry("r4")["observe"], analysis.NOT_INVITED)
        self.assertEqual(self._entry("r5")["observe"], analysis.NO_RESPONSE)
        coverage = analysis.coverage_summary(self.book, self.linkage)
        unobs = {u["stay"]: u["reason"] for u in coverage["unobserved_stays"]}
        self.assertEqual(unobs, {"s4": analysis.NOT_INVITED,
                                 "s5": analysis.NO_RESPONSE})
        # 未观察不进入任何分段的评分均值
        for seg in self.segments:
            self.assertEqual(seg["observed_count"] + seg["unobserved_count"],
                             seg["stay_count"])

    def test_support_levels_and_unknown_segments(self):
        by_key = {(s["segment"]["hotel"], s["segment"]["room"],
                   s["segment"]["party"]): s for s in self.segments}
        self.assertEqual(by_key[("山庭", "套房", "大团")]["support"],
                         analysis.SUPPORTED)
        weak = by_key[("青里", "双床", "散客")]
        self.assertEqual(weak["support"], analysis.SUPPORT_WEAK)
        self.assertEqual(weak["observed_count"], 0)
        self.assertIsNone(weak["avg_score"])
        self.assertIn("没有任何已观察回访", weak["unknown_note"])

    # ---- 客群校正与三类分开 ----

    def test_party_calibration_separates_group_and_individual(self):
        cal = {c["party"]: c for c in
               analysis.calibrate_by_party(self.segments)}
        self.assertGreater(cal["大团"]["active_concern_rate"], 0)
        self.assertEqual(cal["散客"]["active_concern_rate"], 0.0)
        # 未观察的 s4/s5 不进分母
        self.assertEqual(cal["大团"]["observed"], 3)
        self.assertEqual(cal["散客"]["observed"], 2)

    def test_breach_concern_excluded_from_experience_gap_buckets(self):
        seg = next(s for s in self.segments
                   if s["segment"]["hotel"] == "山庭"
                   and s["segment"]["room"] == "套房")
        # 两个已观察：r7（违约）与 r1（等房）；体验问题率只算等房 = 0.5
        self.assertEqual(seg["active_concern_rate"], 0.5)
        self.assertEqual(seg["concerns"], ["等房"])
        # 违约仍独立成立，不能被 2.5 的均分解除
        self.assertTrue(any(b["hotel"] == "山庭" for b in self.breaches))

    # ---- 抽查成本与覆盖口径 ----

    def test_shared_visit_counted_once(self):
        spent = sum(v["cost"] for v in self.visits["visits"])
        self.assertEqual(spent, self.visits["spent"])
        self.assertLessEqual(spent, self.visits["budget"])
        v1 = next(v for v in self.visits["visits"] if v["slot"] == "v1")
        self.assertTrue(v1["shared_visit"])
        self.assertEqual(len(v1["witnessed_conditions"]), 2)
        self.assertEqual(v1["cost"], 80)  # 不是 80×2

    def test_same_building_unlisted_conditions_not_covered(self):
        # v2 在柳居但只见证 双床/夜间，柳居套房缺证不能算被它覆盖
        covered = {(c["hotel"], c["room"], c["window"])
                   for v in self.visits["visits"] for c in v["witnessed_conditions"]}
        self.assertNotIn(("柳居", "双床", "夜间"), covered)
        self.assertEqual(self.visits["uncovered_gaps"], [])

    def test_visit_plan_respects_budget(self):
        book = copy.deepcopy(self.book)
        book["limits"][0]["visit_budget"] = 50
        plan = analysis.plan_visits(book, self.gaps)
        self.assertLessEqual(plan["spent"], 50)
        self.assertTrue(plan["uncovered_gaps"])  # 经费不足时如实留未知

    # ---- 试接资格 ----

    def test_visit_capacity_caps_witnessed_conditions(self):
        book = copy.deepcopy(self.book)
        book["visits"][0]["capacity"] = 1  # v1 有两个缺证选项但只能见证一个
        plan = analysis.plan_visits(book, self.gaps)
        v1 = next(v for v in plan["visits"] if v["slot"] == "v1")
        self.assertEqual(len(v1["witnessed_conditions"]), 1)
        self.assertTrue(plan["uncovered_gaps"])  # 未见证条件如实留未知

    def test_trial_must_fill_evidence_gap_and_traceable(self):
        self.assertEqual(len(self.trials["trials"]), 1)
        t = self.trials["trials"][0]
        self.assertEqual(t["group"], "G4")
        self.assertTrue(t["eligible"])
        self.assertEqual(t["evidence_filled"][0]["observed_before"], 0)
        self.assertEqual(t["paired_visit_slots"], ["v4"])
        self.assertEqual(t["open_obligations"], [])

    def test_trial_rejected_when_no_gap_or_open_special_breach(self):
        book = copy.deepcopy(self.book)
        # 山庭新增预留试接：专用儿童床，但山庭存在未解决儿童床违约
        append_row(book, "intake", ["G9", "山庭", "儿童床", 1, "预留试接", []])
        # 青里再加一个不补缺证的试接（虚构服务，无 inventory / 无 gap）
        append_row(book, "intake", ["G8", "青里", "儿童床", 1, "预留试接", []])
        trials = analysis.plan_trials(book, self.gaps, self.visits, self.linkage)
        by_group = {t["group"]: t for t in trials["trials"]}
        self.assertFalse(by_group["G9"]["eligible"])
        self.assertIn("专用房违约", "".join(by_group["G9"]["reject_reasons"]))
        self.assertFalse(by_group["G8"]["eligible"])
        self.assertIn("缺证", "".join(by_group["G8"]["reject_reasons"]))

    def test_special_customers_never_fill_trial_roster(self):
        # 已接状态的 G1/G3 是专用客户，绝不进入试接批次
        groups = {t["group"] for t in self.trials["trials"]}
        self.assertTrue(groups.isdisjoint({"G1", "G3"}))

    def test_trial_quota_enforced(self):
        book = copy.deepcopy(self.book)
        book["limits"][0]["trial_rooms"] = 1  # G4 要 2 间，超限
        trials = analysis.plan_trials(book, self.gaps, self.visits, self.linkage)
        g4 = next(t for t in trials["trials"] if t["group"] == "G4")
        self.assertFalse(g4["eligible"])
        self.assertIn("试接量上限", "".join(g4["reject_reasons"]))

    # ---- 接团处置与重新判断 ----

    def test_accepted_breach_prefers_contract_alternative_else_manual(self):
        d = {x["group"]: x for x in self.intake["decisions"]}
        self.assertEqual(d["G1"]["decision"], "转人工协商")
        # 柳居儿童床房量已被 G3 承诺占满，替代不可行 → 冻结山庭床位
        self.assertEqual(self.intake["frozen_beds"],
                         [{"hotel": "山庭", "feature": "儿童床",
                           "rooms": 2, "group": "G1"}])
        self.assertEqual(d["G3"]["decision"], "履约条件当前成立，维持已接")

    def test_feasible_alternative_is_used(self):
        book = copy.deepcopy(self.book)
        # 放大柳居儿童床房量，使 G1 的合约替代可行
        for inv in book["room_inventory"]:
            if inv["hotel"] == "柳居" and inv["feature"] == "儿童床":
                inv["rooms"] = 10
        linkage, _ = analysis.link_feedback(book)
        intake = analysis.decide_intake(book, linkage)
        g1 = next(x for x in intake["decisions"] if x["group"] == "G1")
        self.assertEqual(g1["decision"], "启用合约认可替代房源")
        self.assertEqual(g1["detail"]["alternative"], "柳居")
        self.assertEqual(intake["frozen_beds"], [])

    def test_rejudgment_after_appeal_clears_breach(self):
        # 追加一条推翻 r7 核实结论的新回访 → 风险判断改变，三类义务重判
        book = copy.deepcopy(self.book)
        append_row(book, "appeals",
                   ["r7", "s1", "儿童床", "已提供但登记遗漏", 3])
        linkage, _ = analysis.link_feedback(book)
        self.assertEqual(analysis.find_breaches(linkage), [])
        intake = analysis.decide_intake(book, linkage)
        d = {x["group"]: x for x in intake["decisions"]}
        self.assertEqual(d["G1"]["decision"], "履约条件当前成立，维持已接")
        self.assertEqual(intake["frozen_beds"], [])
        # 待接普通续接仍按信息不足处理，不因为违约解除就自动批准
        self.assertIn("信息不足", d["G2"]["decision"])

    def test_outlook_three_way_split(self):
        outlook = {o["hotel"]: o for o in
                   analysis.merchant_outlook(self.segments, self.breaches,
                                             self.linkage)}
        statuses = lambda h: {x["status"] for x in outlook[h]["outlook"]}
        self.assertIn("证实履约问题", statuses("山庭"))
        self.assertIn("同类客群校正后的体验差距", statuses("山庭"))
        self.assertIn("信息不足", statuses("山庭"))
        self.assertEqual(statuses("柳居"), {"信息不足"})
        self.assertEqual(statuses("青里"), {"信息不足"})


if __name__ == "__main__":
    unittest.main()
