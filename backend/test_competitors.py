"""Tests for competitors from the benchmark."""

import unittest

from backend.benchmark.competitors import find_competitors, overall


def row(domain, psa, seo, geo=0, ai=50, country="NL", category="mkb"):
    return {"root_domain": domain, "country": country, "category": category, "psa_score": psa, "seo_score": seo,
            "geo_score": geo, "ai_visibility_score": ai}


class CompetitorTests(unittest.TestCase):
    def setUp(self):
        # overall = 50% PSA + 50% average(SEO, GEO, AI)
        self.rows = [row(f"site{i}.nl", psa=40 + i * 6, seo=30 + i * 6) for i in range(10)] + [
            row("groot.nl", psa=100, seo=100, geo=60, ai=100),
            row("ander-land.be", psa=100, seo=100, country="BE"),
            row("winkel.nl", psa=100, seo=100, category="ecommerce"),
        ]

    def test_overall_same_formula_and_reweighting(self):
        self.assertEqual(overall({"psa": 80, "seo": 60, "geo": 0, "ai": 60}), 60)    # (80 + 40) / 2
        self.assertEqual(overall({"psa": None, "seo": 70, "geo": None, "ai": None}), 70)
        self.assertIsNone(overall({}))

    def test_sites_just_above_you_closest_first(self):
        you = {"psa": 64, "seo": 54, "geo": 0, "ai": 50}
        mine = overall(you)                                                           # (64 + (54+0+50)/3) / 2 = 49
        self.assertEqual(mine, 49)
        result = find_competitors(self.rows, "mijnsite.nl", you, "mkb")
        self.assertEqual(result["cohort"], "NL:mkb")                                  # other country/category excluded
        self.assertEqual(result["your_overall"], mine)
        overalls = [c["overall"] for c in result["ahead"]]
        self.assertEqual(len(overalls), 3)
        self.assertTrue(all(o > mine for o in overalls) and overalls == sorted(overalls))
        others = sorted(overall({"psa": r["psa_score"], "seo": r["seo_score"], "geo": r["geo_score"],
                                 "ai": r["ai_visibility_score"]}) for r in self.rows[:11])
        self.assertEqual(overalls, [o for o in others if o > mine][:3])               # really the closest above
        self.assertEqual(result["leader"]["domain"], "groot.nl")
        self.assertLessEqual(result["behind"]["overall"], mine)
        self.assertFalse(result["you_lead"])
        self.assertNotIn("mijnsite.nl", [c["domain"] for c in result["ahead"]])

    def test_explains_where_they_are_ahead(self):
        result = find_competitors(self.rows, "mijnsite.nl", {"psa": 40, "seo": 30, "geo": 0, "ai": 50}, "mkb")
        leader = result["leader"]
        metrics = [s["metric"] for s in leader["ahead_on"]]
        # groot.nl vs you: SEO +70, PSA +60, GEO +60, AI +50 -> biggest lead first
        self.assertEqual(metrics, ["seo", "psa", "geo", "ai"])
        self.assertTrue(all(s["difference"] >= 10 for s in leader["ahead_on"]))
        self.assertEqual(leader["ahead_on"][1]["label"], "PSA (privacy en security)")

    def test_you_lead_the_cohort(self):
        result = find_competitors(self.rows, "top.nl", {"psa": 100, "seo": 100, "geo": 100, "ai": 100}, "mkb")
        self.assertTrue(result["you_lead"])
        self.assertEqual(result["ahead"], [])
        self.assertEqual(result["leader"]["domain"], "groot.nl")                     # strongest of the others

    def test_known_domain_uses_its_own_cohort_and_is_excluded(self):
        result = find_competitors(self.rows, "site3.nl", {"psa": 58, "seo": 48, "ai": 50, "geo": 0})
        self.assertEqual(result["cohort"], "NL:mkb")
        self.assertNotIn("site3.nl", [c["domain"] for c in result["ahead"]])

    def test_small_cohort_falls_back_and_empty_data(self):
        small = [row("a.be", 50, 50, country="BE", category="saas")] + self.rows
        result = find_competitors(small, "nieuw.be", {"psa": 50, "seo": 50}, "saas")
        self.assertNotEqual(result["cohort"], "BE:saas")                             # only 1 site: too small
        self.assertEqual(result["cohort"], "*:*")    # *:saas (1) and BE:* (2) are too small as well -> all sites
        self.assertEqual(find_competitors([], "x.nl", {"psa": 50})["cohort"], None)

    def test_distribution_counts_scores_without_names(self):
        self.assertIsNone(find_competitors(self.rows, "nieuw.nl", {"psa": 50, "seo": 50})["distribution"])  # 11 < 30
        many = [row(f"s{i}.nl", psa=i * 2, seo=50, geo=0, ai=50) for i in range(40)]
        dist = find_competitors(many, "nieuw.nl", {"psa": 50, "seo": 50}, "mkb")["distribution"]
        self.assertEqual(set(dist), {"overall", "psa", "seo", "geo", "ai"})
        self.assertTrue(all(len(counts) == 101 for counts in dist.values()))
        self.assertEqual(sum(dist["overall"]), 40)
        self.assertEqual(dist["seo"][50], 40)
        self.assertEqual(dist["psa"][78], 1)
        self.assertEqual(dist["overall"][overall({"psa": 78, "seo": 50, "geo": 0, "ai": 50})], 1)


if __name__ == "__main__":
    unittest.main()
