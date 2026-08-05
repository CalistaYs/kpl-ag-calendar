#!/usr/bin/env python3
"""针对 fetch.py 里赛事代号模板的回归测试，不发真实网络请求。

主要防止以后有人为了"清理"或"简化"候选列表，不小心把 KWC{year}（2025 年起
Esports World Cup 王者荣耀项目改用的代号）删掉——这个代号是系统性试探才找到的
（EWC{year} 只对 2024 年有效，2025/2026 年官方换成了 KWC{year}，季前排查详见
README"EWC / KWC 王者荣耀赛事"一节），删掉不会报错，只会静默漏掉当年整届 EWC，
很难在日常使用中被发现。
"""
import unittest

import fetch


class SeasonIdPatternsTest(unittest.TestCase):
    def test_default_patterns_include_both_ewc_and_kwc(self):
        patterns = fetch.DEFAULT_SEASON_ID_PATTERNS
        self.assertIn(
            "EWC{year}", patterns,
            "EWC{year} 不见了：2024 年的 Esports World Cup 数据（EWC2024，38 场比赛）"
            "用的就是这个前缀，删掉会漏掉这一年的历史比赛。",
        )
        self.assertIn(
            "KWC{year}", patterns,
            "KWC{year} 不见了：2025 年起 Esports World Cup 的王者荣耀项目改用这个前缀"
            "（KWC2025/KWC2026 都验证过真实存在 AG 的比赛），删掉会导致以后每年的 "
            "EWC 赛程都抓不到。",
        )

    def test_generate_season_candidates_includes_ewc_and_kwc_for_target_year(self):
        candidates = fetch.generate_season_candidates(years=[2026])
        self.assertIn("EWC2026", candidates)
        self.assertIn("KWC2026", candidates)

    def test_generate_season_candidates_scales_with_years(self):
        # 多年份场景下，EWC/KWC 都应该按年份分别生成候选，不能只出现一次。
        candidates = fetch.generate_season_candidates(years=[2025, 2026])
        for year in (2025, 2026):
            self.assertIn(f"EWC{year}", candidates)
            self.assertIn(f"KWC{year}", candidates)

    def test_default_patterns_still_cover_kpl_and_challenger_cup(self):
        # 顺带确认这次改动没有影响其它已确认赛事的候选模板。
        patterns = fetch.DEFAULT_SEASON_ID_PATTERNS
        for expected in ("KPL{year}S1", "KPL{year}S2", "KPL{year}S3", "KCC{year}"):
            self.assertIn(expected, patterns)


if __name__ == "__main__":
    unittest.main()
