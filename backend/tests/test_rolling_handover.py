"""揉捻成条交班与发酵接料测试：覆盖机器文件归属、冲突复核、重新验收与接料回执。"""
from __future__ import annotations

import unittest

from app.services import service_for
from app.services.rolling_handover import (
    FILE_ACCEPTED,
    FILE_RECEIVED,
    FILE_REJECTED,
    HANDOVER_ISSUED,
    HANDOVER_VOID,
    RECEIPT_FED,
    RECEIPT_HELD,
    REVIEW_DOING,
    REVIEW_SIGNED,
    rolling_handover,
)
from app.store import store


def _make_rolling(batch_no: str, basin: str, machine: str, window: str) -> int:
    svc = service_for("rolling")
    entry, errors = svc.create_entry({
        "压力档位": "中压", "揉捻时长": "45", "成条率": "87", "碎茶比例": "4.8",
        "登记编号": f"ROLL-{batch_no}", "负责人": "制茶师", "生效版本": "v1",
        "备注说明": "测试", "批次号": batch_no,
    })
    assert entry is not None, errors
    entry["实际茶盆"] = basin
    entry["当盆研判时段"] = window
    entry["当次机器身份"] = machine
    return int(entry["id"])


class TestRollingFileAttribution(unittest.TestCase):
    """机器采样文件只按本盆、本时段、本机台重算成条结果。"""

    def setUp(self):
        self.batch_no = f"BATCH-HO-ATTR-{self._testMethodName}"
        self.rolling_id = _make_rolling(self.batch_no, "1号茶盆", "揉捻机-3号", "09:00-10:00")

    def test_same_window_recalculates(self):
        result = rolling_handover.import_file({
            "源文件名称": "attr_main.csv", "批次号": self.batch_no,
            "实际茶盆": "1号茶盆", "机台身份": "揉捻机-3号", "研判时段": "09:00-10:00",
            "实测成条率": 88.5, "实测碎茶比例": 4.6, "揉捻记录ID": self.rolling_id,
        }, "制茶师")
        self.assertTrue(result["ok"])
        self.assertTrue(result["是否重算成条结果"])
        rolling = store.find("rolling", self.rolling_id)
        self.assertEqual(rolling["成条率"], 88.5)
        self.assertEqual(rolling["碎茶比例"], 4.6)

    def test_other_window_not_counted(self):
        result = rolling_handover.import_file({
            "源文件名称": "attr_other.csv", "批次号": self.batch_no,
            "实际茶盆": "1号茶盆", "机台身份": "揉捻机-3号", "研判时段": "11:00-12:00",
            "实测成条率": 96.0, "实测碎茶比例": 1.0, "揉捻记录ID": self.rolling_id,
        })
        self.assertTrue(result["ok"])
        self.assertFalse(result["是否重算成条结果"])
        self.assertFalse(result["file"]["计入本盆"])
        self.assertIn("非本盆研判时段", result["重算说明"])
        rolling = store.find("rolling", self.rolling_id)
        self.assertEqual(rolling["成条率"], "87")
        self.assertEqual(rolling["碎茶比例"], "4.8")

    def test_duplicate_import_does_not_register_twice(self):
        values = {
            "源文件名称": "attr_dup.csv", "批次号": self.batch_no,
            "实际茶盆": "1号茶盆", "机台身份": "揉捻机-3号", "研判时段": "09:00-10:00",
            "实测成条率": 88.0, "实测碎茶比例": 4.5, "揉捻记录ID": self.rolling_id,
        }
        first = rolling_handover.import_file(values)
        second = rolling_handover.import_file(values)
        self.assertTrue(second.get("重复导入"))
        self.assertEqual(first["file"]["id"], second["file"]["id"])
        same_basin_files = [f for f in rolling_handover.list_files(批次号=self.batch_no)]
        self.assertEqual(len([f for f in same_basin_files if f["源文件名称"] == "attr_dup.csv"]), 1)


class TestHandoverConflictReview(unittest.TestCase):
    """人工复称签发后，同机台补报只能“已收到”，不能自动替代正式结果。"""

    def setUp(self):
        self.batch_no = f"BATCH-HO-CONFLICT-{self._testMethodName}"
        self.basin = f"2号茶盆-{self._testMethodName}"
        self.machine = "揉捻机-5号"
        self.window = "13:00-14:00"
        self.rolling_id = _make_rolling(self.batch_no, self.basin, self.machine, self.window)
        self.main_file = rolling_handover.import_file({
            "源文件名称": "conflict_main.csv", "批次号": self.batch_no,
            "实际茶盆": self.basin, "机台身份": self.machine, "研判时段": self.window,
            "实测成条率": 86.4, "实测碎茶比例": 4.7, "揉捻记录ID": self.rolling_id,
        })["file"]
        handover, message = rolling_handover.issue_handover({
            "批次号": self.batch_no, "实际茶盆": self.basin, "机台身份": self.machine,
            "研判时段": self.window, "人工复称值": 4.8, "限值": 5,
            "来源文件ID": self.main_file["id"], "揉捻记录ID": self.rolling_id,
        }, "品控员")
        self.assertIsNotNone(handover, message)
        self.handover = handover

    def _supplement(self, debris: float = 5.2) -> dict:
        return rolling_handover.import_file({
            "源文件名称": "conflict_supplement.csv", "批次号": self.batch_no,
            "实际茶盆": self.basin, "机台身份": self.machine, "研判时段": self.window,
            "实测成条率": 88.2, "实测碎茶比例": debris, "是否补报": True,
            "揉捻记录ID": self.rolling_id,
        })["file"]

    def test_manual_48_signs_handover_under_limit(self):
        self.assertEqual(self.handover["status"], HANDOVER_ISSUED)
        self.assertEqual(self.handover["判定"], "合格")
        self.assertEqual(self.handover["碎茶比例"], 4.8)
        self.assertEqual(self.handover["限值"], 5.0)

    def test_over_limit_manual_cannot_sign(self):
        handover, message = rolling_handover.issue_handover({
            "批次号": "BATCH-HO-LIMIT", "实际茶盆": "3号茶盆", "机台身份": "揉捻机-6号",
            "研判时段": "15:00-16:00", "人工复称值": 5.6, "限值": 5,
        })
        self.assertIsNone(handover)
        self.assertIn("超过限值", message)

    def test_supplement_52_does_not_replace_signed_result(self):
        file_row = self._supplement(5.2)
        self.assertEqual(file_row["status"], FILE_RECEIVED)
        self.assertTrue(file_row["冲突"])
        self.assertIn("冲突", file_row["冲突说明"])
        self.assertEqual(self.handover["碎茶比例"], 4.8)
        self.assertEqual(self.handover["review_status"], "待复核")

        # 未核签前不能核签这份冲突文件
        signed, message = rolling_handover.countersign_file(file_row["id"], "品控员")
        self.assertIsNone(signed)
        self.assertIn("复核签结", message)

    def test_accept_review_then_sign_keeps_manual(self):
        file_row = self._supplement()
        handover, message = rolling_handover.accept_file_for_review(
            self.handover["id"], file_row["id"], "品控员")
        self.assertIsNotNone(handover, message)
        self.assertEqual(handover["review_status"], REVIEW_DOING)
        # 受理核查不等于签结，交班仍按人工复称
        self.assertEqual(handover["碎茶比例"], 4.8)

        handover, message = rolling_handover.sign_review(
            self.handover["id"], "补报有误，维持人工复称 4.8%", "品控员")
        self.assertIsNotNone(handover, message)
        self.assertEqual(handover["review_status"], REVIEW_SIGNED)
        self.assertEqual(handover["碎茶比例"], 4.8)
        # 保留未用于实际发酵的原值
        refreshed = store.ops_find("rolling_files", file_row["id"])
        self.assertEqual(refreshed["status"], FILE_REJECTED)
        self.assertEqual(refreshed["原值"]["实测碎茶比例"], 5.2)
        self.assertTrue(refreshed["未用于实际发酵"])


class TestFermentationReceiving(unittest.TestCase):
    """发酵只接符合要求的有效交班；补报未受理前挂起，受理后可继续。"""

    def setUp(self):
        self.batch_no = f"BATCH-HO-RECEIVE-{self._testMethodName}"
        self.basin = f"4号茶盆-{self._testMethodName}"
        self.machine = "揉捻机-7号"
        self.window = "16:00-17:00"
        self.rolling_id = _make_rolling(self.batch_no, self.basin, self.machine, self.window)
        store.find("rolling", self.rolling_id)["status"] = "已确认"

        fermentation = service_for("fermentation")
        entry, errors = fermentation.create_entry({
            "环境温度": "28", "环境湿度": "85", "堆高尺寸": "25",
            "翻堆间隔": "12", "登记编号": "FERM-HO-1", "负责人": "发酵工",
            "生效版本": "v1", "备注说明": "测试", "批次号": self.batch_no,
            "上游工序": "rolling", "上游记录ID": self.rolling_id,
        })
        self.assertIsNotNone(entry, errors)
        self.fermentation_id = int(entry["id"])
        entry["status"] = "已确认"

        self.main_file = rolling_handover.import_file({
            "源文件名称": "recv_main.csv", "批次号": self.batch_no,
            "实际茶盆": self.basin, "机台身份": self.machine, "研判时段": self.window,
            "实测成条率": 86.4, "实测碎茶比例": 4.7, "揉捻记录ID": self.rolling_id,
        })["file"]
        self.handover, message = rolling_handover.issue_handover({
            "批次号": self.batch_no, "实际茶盆": self.basin, "机台身份": self.machine,
            "研判时段": self.window, "人工复称值": 4.8, "限值": 5,
            "来源文件ID": self.main_file["id"], "揉捻记录ID": self.rolling_id,
        }, "品控员")
        self.assertIsNotNone(self.handover, message)

    def _draft(self) -> dict:
        receipt, message = rolling_handover.create_receiving_draft(
            self.fermentation_id,
            {"交班号": self.handover["交班号"], "实际茶盆": self.basin, "入料重量": "42"},
            "发酵工")
        self.assertIsNotNone(receipt, message)
        return receipt

    def test_draft_rejects_voided_handover(self):
        self.handover["status"] = HANDOVER_VOID
        self.handover["有效入料"] = False
        receipt, message = rolling_handover.create_receiving_draft(
            self.fermentation_id,
            {"交班号": self.handover["交班号"], "实际茶盆": self.basin, "入料重量": "42"})
        self.assertIsNone(receipt)
        self.assertIn("已作废", message)

    def test_supplement_unreviewed_holds_receiving(self):
        supplement = rolling_handover.import_file({
            "源文件名称": "recv_supplement.csv", "批次号": self.batch_no,
            "实际茶盆": self.basin, "机台身份": self.machine, "研判时段": self.window,
            "实测成条率": 88.2, "实测碎茶比例": 5.2, "是否补报": True,
            "揉捻记录ID": self.rolling_id,
        })["file"]
        self.assertEqual(supplement["status"], FILE_RECEIVED)

        receipt = self._draft()
        receipt, fed, message = rolling_handover.submit_receiving(receipt["id"])
        self.assertFalse(fed)
        self.assertEqual(receipt["状态"], RECEIPT_HELD)
        self.assertFalse(receipt["是否入料"])
        self.assertIn("尚未受理核查", message)

        # 品控受理机器文件核查，但尚不签复核结论
        handover, accept_msg = rolling_handover.accept_file_for_review(
            self.handover["id"], supplement["id"], "品控员")
        self.assertIsNotNone(handover, accept_msg)
        self.assertEqual(supplement["status"], FILE_ACCEPTED)

        receipt, fed, message = rolling_handover.submit_receiving(receipt["id"])
        self.assertTrue(fed, message)
        self.assertEqual(receipt["状态"], RECEIPT_FED)
        self.assertTrue(receipt["是否入料"])
        self.assertEqual(receipt["实际交班号"], self.handover["交班号"])
        self.assertEqual(receipt["入料时交班复核状态"], REVIEW_DOING)
        # 实际使用的仍是人工复称结果
        self.assertEqual(self.handover["碎茶比例"], 4.8)
        self.assertEqual(self.handover["入料重量"], 42)

    def test_same_basin_single_effective_feed(self):
        first = self._draft()
        _, fed, _ = rolling_handover.submit_receiving(first["id"])
        self.assertTrue(fed)
        second, message = rolling_handover.create_receiving_draft(
            self.fermentation_id,
            {"交班号": self.handover["交班号"], "实际茶盆": self.basin, "入料重量": "42"})
        self.assertIsNone(second)
        self.assertIn("不能重复接料", message)


class TestReacceptAndExport(unittest.TestCase):
    """重新验产后旧交班可查但不再作为有效入料；导出历史版本可回查当时来源。"""

    def setUp(self):
        self.batch_no = f"BATCH-HO-REACCEPT-{self._testMethodName}"
        self.basin = f"5号茶盆-{self._testMethodName}"
        self.machine = "揉捻机-8号"
        self.window = "17:00-18:00"
        self.rolling_id = _make_rolling(self.batch_no, self.basin, self.machine, self.window)
        self.handover, message = rolling_handover.issue_handover({
            "批次号": self.batch_no, "实际茶盆": self.basin, "机台身份": self.machine,
            "研判时段": self.window, "人工复称值": 4.8, "限值": 5, "成条率": 86.4,
            "揉捻记录ID": self.rolling_id,
        }, "品控员")
        self.assertIsNotNone(self.handover, message)

    def test_export_history_preserves_source(self):
        first = self._export()
        self.assertEqual(first["版本"], 1)
        self.assertEqual(first["当时来源"]["来源"]["碎茶比例"], 4.8)

        # 改人工结果再导出，第一版仍保留原始来源
        self.handover["碎茶比例"] = 4.6
        self.handover["来源"]["碎茶比例"] = 4.6
        second = self._export()
        self.assertEqual(second["版本"], 2)
        self.assertEqual(second["当时来源"]["来源"]["碎茶比例"], 4.6)

        again = rolling_handover.list_exports(self.handover["id"])
        self.assertEqual(again[0]["当时来源"]["来源"]["碎茶比例"], 4.8)
        self.assertEqual(again[0]["当时来源"]["交班"]["碎茶比例"], 4.8)

        content, _ = rolling_handover.build_sheet_csv(self.handover["id"], again[0])
        self.assertIn("4.8", content)
        self.assertIn(self.basin, content)

    def test_reaccept_voids_old_single_effective_feed(self):
        draft = self._draft()
        self.assertIsNotNone(draft)
        receipt, fed, message = rolling_handover.submit_receiving(draft["id"])
        self.assertTrue(fed, message)

        new_handover, message = rolling_handover.reaccept(
            self.handover["id"], {"人工复称值": 4.5, "限值": 5, "原因": "班组重新验收"}, "班长")
        self.assertIsNotNone(new_handover, message)
        self.assertEqual(new_handover["版本"], 2)
        self.assertEqual(self.handover["status"], HANDOVER_VOID)
        self.assertFalse(self.handover["有效入料"])

        # 旧交班仍可查询
        self.assertIsNotNone(rolling_handover.get_handover(self.handover["id"]))
        # 旧接料回执不再作为有效入料身份
        old_receipt = rolling_handover.get_receipt(receipt["id"])
        self.assertFalse(old_receipt["有效入料身份"])
        self.assertEqual(old_receipt["替代说明"].count("重新验收替代"), 1)

        # 旧交班不能再领料
        blocked, msg = rolling_handover.create_receiving_draft(
            self.fermentation_id(),
            {"交班号": self.handover["交班号"], "实际茶盆": self.basin, "入料重量": "10"})
        self.assertIsNone(blocked)
        self.assertIn("已作废", msg)

    def _export(self) -> dict:
        export_row, message = rolling_handover.export_handover(self.handover["id"], "班长")
        self.assertIsNotNone(export_row, message)
        return export_row

    def _draft(self) -> dict:
        fermentation = service_for("fermentation")
        entry, errors = fermentation.create_entry({
            "环境温度": "28", "环境湿度": "85", "堆高尺寸": "25", "翻堆间隔": "12",
            "登记编号": "FERM-HO-REACCEPT", "负责人": "发酵工", "生效版本": "v1",
            "备注说明": "测试", "批次号": self.batch_no,
        })
        self.assertIsNotNone(entry, errors)
        self._ferm_id = int(entry["id"])
        receipt, message = rolling_handover.create_receiving_draft(
            self._ferm_id,
            {"交班号": self.handover["交班号"], "实际茶盆": self.basin, "入料重量": "30"})
        self.assertIsNotNone(receipt, message)
        return receipt

    def fermentation_id(self) -> int:
        return self._ferm_id


if __name__ == "__main__":
    unittest.main()
