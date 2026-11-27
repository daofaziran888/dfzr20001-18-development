"""揉捻成条交班与发酵接料领域服务。

这条链路解决三个真实业务问题：

1. 揉捻工序原先没有可导入机器采样结果、再导出交班的入口。这里按
   **实际茶盆 + 研判时段 + 当次机器身份** 记录每一次机器采样文件；
   只有与本盆同一研判时段、同一机台的文件才允许重算这盆的成条结果，
   其他时段的数据只留在台账里备查。
2. 品控员以人工复称值（碎茶比例）签发成条交班，限值默认 5%。
   之后同机台的补报文件若给出不同结果，文件只是“已收到”，
   在核签/复核签结之前绝不自动替代正式签发用的人工结果。
3. 发酵工位从接料页受理这盆茶。它只认已签发、判定合格且仍有效的
   交班凭据；同一茶盆只保留一种“实际入料身份”。补报文件已收到但
   尚未受理核查时，接料请求会被挂起不写入；受理后（哪怕复核结论
   还没签）请求可继续，回执直接写明是否入料和实际使用的交班号。

内部台账存放在 ``store.ops_rows``，不参与 18 个业务模块的概览计数。
"""
from __future__ import annotations

import copy
import csv
import hashlib
import io
from datetime import datetime
from typing import Any

from app.store import store

# 机器采样文件状态
FILE_RECEIVED = "已收到"
FILE_ACCEPTED = "已受理核查"
FILE_SIGNED = "已核签"
FILE_REJECTED = "已驳回"
FILE_REVIEWED_UNUSED = "已复核未采用"
FILE_VOID = "已作废"

# 交班凭据状态
HANDOVER_ISSUED = "已签发"
HANDOVER_VOID = "已作废"

# 交班复核状态
REVIEW_NONE = "无复核"
REVIEW_PENDING = "待复核"
REVIEW_DOING = "复核中"
REVIEW_SIGNED = "已签结"

# 接料回执状态
RECEIPT_DRAFT = "草稿"
RECEIPT_FED = "已接料"
RECEIPT_HELD = "已挂起"
RECEIPT_SUPERSEDED = "已替代"

DEFAULT_LIMIT = 5.0


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().casefold() in {"1", "true", "yes", "y", "是", "补报"}


class RollingHandoverService:
    """揉捻采样文件导入、成条交班、复核与接料。"""

    def __init__(self) -> None:
        self.limit = DEFAULT_LIMIT

    # ── 台账访问 ──

    def _files(self) -> list[dict[str, Any]]:
        return store.ops_rows("rolling_files")

    def _handovers(self) -> list[dict[str, Any]]:
        return store.ops_rows("rolling_handovers")

    def _exports(self) -> list[dict[str, Any]]:
        return store.ops_rows("handover_exports")

    def _receipts(self) -> list[dict[str, Any]]:
        return store.ops_rows("fermentation_receipts")

    def list_files(self, *, 批次号: str | None = None, 实际茶盆: str | None = None,
                   status: str | None = None) -> list[dict[str, Any]]:
        rows = self._files()
        if 批次号:
            rows = [r for r in rows if r.get("批次号") == 批次号]
        if 实际茶盆:
            rows = [r for r in rows if r.get("实际茶盆") == 实际茶盆]
        if status:
            rows = [r for r in rows if r.get("status") == status]
        return rows

    def list_handovers(self, *, 批次号: str | None = None,
                       实际茶盆: str | None = None) -> list[dict[str, Any]]:
        rows = self._handovers()
        if 批次号:
            rows = [r for r in rows if r.get("批次号") == 批次号]
        if 实际茶盆:
            rows = [r for r in rows if r.get("实际茶盆") == 实际茶盆]
        return rows

    def get_handover(self, handover_id: int) -> dict[str, Any] | None:
        return store.ops_find("rolling_handovers", handover_id)

    def find_handover(self, handover_no: str | None = None,
                      handover_id: int | None = None) -> dict[str, Any] | None:
        if handover_id is not None:
            found = self.get_handover(int(handover_id))
            if found is not None:
                return found
        if handover_no:
            for row in self._handovers():
                if row.get("交班号") == handover_no:
                    return row
        return None

    # ── 有效交班查询 ──

    def find_valid_handover(self, 批次号: str, 实际茶盆: str) -> dict[str, Any] | None:
        for row in self._handovers():
            if (row.get("批次号") == 批次号
                    and row.get("实际茶盆") == 实际茶盆
                    and row.get("status") == HANDOVER_ISSUED
                    and row.get("有效入料")):
                return row
        return None

    # ── 机器采样文件导入 ──

    def import_file(self, values: dict[str, Any], operator: str = "") -> dict[str, Any]:
        """导入一份机器采样文件，返回 {ok, message, file, 是否重算成条结果, ...}。"""
        source_name = str(values.get("源文件名称") or values.get("文件名") or "").strip()
        batch_no = str(values.get("批次号") or "").strip()
        basin = str(values.get("实际茶盆") or values.get("茶盆") or "").strip()
        machine = str(values.get("机台身份") or values.get("机器身份") or values.get("机台") or "").strip()
        window = str(values.get("研判时段") or values.get("采样时段") or "").strip()
        twig = store.parse_weight(values.get("实测成条率", values.get("成条率", 0)))
        debris = store.parse_weight(values.get("实测碎茶比例", values.get("碎茶比例", 0)))
        rolling_id = values.get("揉捻记录ID")

        missing = []
        if not source_name:
            missing.append("源文件名称")
        if not batch_no:
            missing.append("批次号")
        if not basin:
            missing.append("实际茶盆")
        if not machine:
            missing.append("机台身份")
        if not window:
            missing.append("研判时段")
        if debris <= 0 and twig <= 0:
            missing.append("实测成条率/实测碎茶比例")
        if missing:
            return {"ok": False, "message": f"缺少必填字段：{'、'.join(missing)}", "file": None}

        rolling = store.find("rolling", int(rolling_id)) if rolling_id not in (None, "") else None
        if rolling_id not in (None, "") and rolling is None:
            return {"ok": False, "message": f"揉捻记录 {rolling_id} 不存在", "file": None}
        if rolling is not None and rolling.get("批次号") and rolling["批次号"] != batch_no:
            return {"ok": False, "message": "采样文件批次号与揉捻记录不一致", "file": None}

        fingerprint = hashlib.sha1(
            "|".join([source_name, batch_no, basin, machine, window,
                      f"{twig:.3f}", f"{debris:.3f}"]).encode("utf-8")
        ).hexdigest()

        for existing in self._files():
            if existing.get("文件指纹") == fingerprint:
                existing["重复导入次数"] = int(existing.get("重复导入次数", 0)) + 1
                return {
                    "ok": True,
                    "message": "该采样文件已导入过；未重复建立台账，也不会重复领取茶料",
                    "file": existing,
                    "重复导入": True,
                    "是否重算成条结果": False,
                }

        # 本盆的基准：实际茶盆 / 研判时段 / 当次机器身份。
        # 首次导入时把本盆身份落到揉捻记录上，后续文件据此判定归属。
        canonical_basin = rolling.get("实际茶盆") if rolling else None
        canonical_window = rolling.get("当盆研判时段") if rolling else None
        canonical_machine = rolling.get("当次机器身份") if rolling else None

        reasons: list[str] = []
        if canonical_basin and canonical_basin != basin:
            reasons.append("与本盆实际茶盆不一致")
        if canonical_window and canonical_window != window:
            reasons.append("非本盆研判时段，不能重算本盆成条结果")
        if canonical_machine and canonical_machine != machine:
            reasons.append("非当次机器身份，不能重算本盆成条结果")
        if rolling is not None and rolling.get("批次号") and rolling["批次号"] != batch_no:
            reasons.append("与本盆批次号不一致")

        counted = not reasons

        if rolling is not None and counted:
            rolling.setdefault("实际茶盆", basin)
            rolling.setdefault("当盆研判时段", window)
            rolling.setdefault("当次机器身份", machine)
            if not rolling.get("批次号"):
                rolling["批次号"] = batch_no

        file_row: dict[str, Any] = {
            "id": store.ops_next_id("rolling_files"),
            "源文件名称": source_name,
            "文件指纹": fingerprint,
            "批次号": batch_no,
            "揉捻记录ID": int(rolling_id) if rolling_id not in (None, "") else (rolling or {}).get("id"),
            "实际茶盆": basin,
            "机台身份": machine,
            "研判时段": window,
            "实测成条率": twig,
            "实测碎茶比例": debris,
            "压力档位": values.get("压力档位", ""),
            "揉捻时长": values.get("揉捻时长", ""),
            "是否补报": _as_bool(values.get("是否补报", False)),
            "导入时间": _now(),
            "导入人": operator or str(values.get("导入人") or ""),
            "status": FILE_RECEIVED,
            "计入本盆": counted,
            "不计入原因": "；".join(reasons),
            "关联交班号": "",
            "冲突": False,
            "冲突说明": "",
            # 原值在导入时冻结：即使后续被视为错误，也保留未经实际发酵使用的原始读数。
            "原值": {"实测成条率": twig, "实测碎茶比例": debris,
                     "机台身份": machine, "研判时段": window},
            "处置": "",
            "处置说明": "",
            "重复导入次数": 0,
        }

        recalculated = False
        recalc_note = ""
        valid_handover = self.find_valid_handover(batch_no, basin)
        if counted and valid_handover is not None:
            # 已经签发的交班结果不能被后到的机器文件自动改写。
            file_row["关联交班号"] = valid_handover.get("交班号", "")
            diff = abs(debris - store.parse_weight(valid_handover.get("碎茶比例", 0)))
            twig_diff = abs(twig - store.parse_weight(valid_handover.get("成条率", 0)))
            if file_row["是否补报"] and (diff > 0.05 or twig_diff > 0.5):
                file_row["冲突"] = True
                file_row["冲突说明"] = (
                    f"补报文件与已签发交班结果冲突：补报碎茶比例 {debris:.1f}%"
                    f"（成条率 {twig:.1f}%），交班采用人工复称 "
                    f"{store.parse_weight(valid_handover.get('碎茶比例', 0)):.1f}%"
                    f"（限值 {store.parse_weight(valid_handover.get('限值', DEFAULT_LIMIT)):.1f}%）"
                )
                if valid_handover.get("review_status") in (REVIEW_NONE, REVIEW_SIGNED):
                    valid_handover["review_status"] = REVIEW_PENDING
                valid_handover["待复核文件ID"] = file_row["id"]
                valid_handover["待复核文件说明"] = file_row["冲突说明"]
                recalc_note = "新文件已收到但未核签，未替代正式签发的人工结果"
            else:
                recalc_note = "与本盆已签发交班结果一致，不改变交班结果"
        elif counted:
            if rolling is not None:
                rolling["成条率"] = twig
                rolling["碎茶比例"] = debris
                rolling["成条结果来源文件ID"] = file_row["id"]
                rolling["成条结果更新时间"] = _now()
                recalculated = True
                recalc_note = f"按本盆研判时段 {window} 与机台 {machine} 重算成条结果"
            else:
                recalc_note = "文件已登记，但未关联揉捻记录，未重算"
        else:
            recalc_note = "；".join(reasons)

        file_row["是否重算成条结果"] = recalculated
        file_row["重算说明"] = recalc_note
        self._files().append(file_row)
        return {
            "ok": True,
            "message": "采样文件已导入" if not reasons else "采样文件已登记（未计入本盆）",
            "file": file_row,
            "是否重算成条结果": recalculated,
            "重算说明": recalc_note,
        }

    def countersign_file(self, file_id: int, signer: str = "") -> tuple[dict[str, Any] | None, str]:
        """机器文件核签。有冲突且复核未签结时不得自动替代人工结果。"""
        file_row = store.ops_find("rolling_files", file_id)
        if file_row is None:
            return None, f"采样文件 {file_id} 不存在"
        handover = self.find_handover(handover_no=file_row.get("关联交班号"))
        if file_row.get("冲突") and handover is not None and handover.get("review_status") != REVIEW_SIGNED:
            return None, "补报文件与已签发交班冲突，须先完成复核签结后才能核签"
        file_row["status"] = FILE_SIGNED
        file_row["核签人"] = signer
        file_row["核签时间"] = _now()
        return file_row, f"采样文件 {file_id} 已核签"

    # ── 成条交班签发 ──

    def issue_handover(self, values: dict[str, Any], operator: str = "") -> tuple[dict[str, Any] | None, str]:
        batch_no = str(values.get("批次号") or "").strip()
        basin = str(values.get("实际茶盆") or values.get("茶盆") or "").strip()
        machine = str(values.get("机台身份") or values.get("机器身份") or values.get("机台") or "").strip()
        window = str(values.get("研判时段") or "").strip()
        rolling_id = values.get("揉捻记录ID")
        manual = store.parse_weight(
            values.get("人工复称值", values.get("人工复称碎茶比例", values.get("复称碎茶比例", 0)))
        )
        limit = store.parse_weight(values.get("限值", DEFAULT_LIMIT)) or DEFAULT_LIMIT

        missing = [label for label, val in (
            ("批次号", batch_no), ("实际茶盆", basin), ("机台身份", machine),
            ("研判时段", window), ("人工复称值", values.get("人工复称值", values.get("人工复称碎茶比例", ""))),
        ) if not str(val or "").strip()]
        if missing:
            return None, f"缺少必填字段：{'、'.join(missing)}"

        rolling = store.find("rolling", int(rolling_id)) if rolling_id not in (None, "") else None
        if rolling_id not in (None, "") and rolling is None:
            return None, f"揉捻记录 {rolling_id} 不存在"

        # 同一茶盆同一时间只能有一种有效入料身份。
        existing_valid = self.find_valid_handover(batch_no, basin)
        if existing_valid is not None:
            return None, (
                f"茶盆 {basin} 已有有效交班 {existing_valid.get('交班号')}；"
                "如班组必须重新验收，请走交班重新验收，旧交班只保留查询"
            )

        if manual < 0 or manual > 100:
            return None, "人工复称值应在0-100%之间"
        if manual > limit:
            return None, f"人工复称碎茶比例 {manual:.1f}% 超过限值 {limit:.1f}%，不得签发交班"
        judgment = "合格" if manual <= limit else "超限"

        source_file_id = values.get("来源文件ID")
        source = {"类型": "人工复称", "文件ID": int(source_file_id) if source_file_id not in (None, "") else None,
                  "说明": "品控员人工复称"}
        twig = store.parse_weight(values.get("成条率", 0))
        if source_file_id not in (None, ""):
            source_file = store.ops_find("rolling_files", int(source_file_id))
            if source_file is None:
                return None, f"来源文件 {source_file_id} 不存在"
            if not source_file.get("计入本盆"):
                return None, "来源文件不属于本盆研判时段/机台，不能作为交班来源"
            source = {"类型": "机器采样", "文件ID": int(source_file_id),
                      "文件名称": source_file.get("源文件名称"),
                      "说明": "取本盆同时段同机台采样，经人工复称核对"}
            if twig <= 0:
                twig = store.parse_weight(source_file.get("实测成条率", 0))
            source_file["关联交班号"] = ""
        if twig <= 0 and rolling is not None:
            twig = store.parse_weight(rolling.get("成条率", 0))
        source["成条率"] = twig
        source["碎茶比例"] = manual

        handover_id = store.ops_next_id("rolling_handovers")
        handover_no = f"HO-{batch_no}-{handover_id:02d}" if batch_no else f"HO-{handover_id:04d}"
        handover: dict[str, Any] = {
            "id": handover_id,
            "交班号": handover_no,
            "版本": 1,
            "批次号": batch_no,
            "揉捻记录ID": int(rolling_id) if rolling_id not in (None, "") else (rolling or {}).get("id"),
            "实际茶盆": basin,
            "机台身份": machine,
            "研判时段": window,
            "成条率": twig,
            "碎茶比例": manual,
            "人工复称值": manual,
            "限值": limit,
            "判定": judgment,
            "来源": source,
            "来源文件ID": source.get("文件ID"),
            "签发人": operator or str(values.get("签发人") or "品控员"),
            "签发时间": _now(),
            "status": HANDOVER_ISSUED,
            "有效入料": True,
            "review_status": REVIEW_NONE,
            "待复核文件ID": None,
            "待复核文件说明": "",
            "review_conclusion": "",
            "review_signer": "",
            "review_signed_at": "",
            "入料状态": "未入料",
            "入料回执ID": None,
            "入料重量": 0,
            "入料时间": "",
            "替代交班号": "",
            "被替代": False,
            "版本历史": [],
        }
        self._handovers().append(handover)

        if source.get("文件ID") is not None:
            for file_row in self._files():
                if file_row["id"] == source["文件ID"]:
                    file_row["关联交班号"] = handover_no
                    file_row["用于签发"] = True

        if rolling is not None:
            rolling.setdefault("实际茶盆", basin)
            rolling.setdefault("当盆研判时段", window)
            rolling.setdefault("当次机器身份", machine)
            rolling["成条交班号"] = handover_no
            rolling["成条率"] = twig
            rolling["碎茶比例"] = manual
            rolling["成条结果更新时间"] = _now()

        store.log_event("rolling", handover.get("揉捻记录ID") or 0, "issue_handover",
                        {"交班号": handover_no, "茶盆": basin, "人工复称": manual})
        return handover, f"成条交班 {handover_no} 已签发（人工复称 {manual:.1f}%，限值 {limit:.1f}%）"

    # ── 补报复核 ──

    def accept_file_for_review(self, handover_id: int, file_id: int | None = None,
                               reviewer: str = "") -> tuple[dict[str, Any] | None, str]:
        """受理新的机器文件核查：文件从“已收到”进入“已受理核查”，复核结论仍未签。"""
        handover = self.get_handover(handover_id)
        if handover is None:
            return None, f"交班 {handover_id} 不存在"
        if handover.get("status") != HANDOVER_ISSUED:
            return None, "该交班已作废，不能再受理复核"
        if handover.get("review_status") == REVIEW_SIGNED:
            return None, "该交班复核已签结；如新文件有误，请走重新验收流程"

        target_id = file_id if file_id not in (None, "") else handover.get("待复核文件ID")
        file_row = store.ops_find("rolling_files", int(target_id)) if target_id not in (None, "") else None
        if file_row is None:
            return None, "没有待受理的补报采样文件"

        file_row["status"] = FILE_ACCEPTED
        file_row["受理人"] = reviewer
        file_row["受理时间"] = _now()
        file_row["关联交班号"] = handover.get("交班号")
        handover["review_status"] = REVIEW_DOING
        handover["待复核文件ID"] = file_row["id"]
        handover["review_file_id"] = file_row["id"]
        handover["待复核文件说明"] = file_row.get("冲突说明", "")
        # 注意：交班的成条率/碎茶比例保持人工复称结果，不因受理而变。
        return handover, (
            f"已受理补报文件 {file_row.get('源文件名称')} 的机器核查；"
            "复核结论尚未签结，交班结果仍按人工复称"
        )

    def sign_review(self, handover_id: int, conclusion: str, signer: str = "",
                    disposition: str = "") -> tuple[dict[str, Any] | None, str]:
        handover = self.get_handover(handover_id)
        if handover is None:
            return None, f"交班 {handover_id} 不存在"
        if handover.get("status") != HANDOVER_ISSUED:
            return None, "该交班已作废，不能签复核结论"
        if not str(conclusion).strip():
            return None, "必须填写复核结论"
        if handover.get("review_status") == REVIEW_SIGNED:
            return handover, "该交班复核结论已签结"

        file_row = store.ops_find("rolling_files", handover.get("review_file_id") or handover.get("待复核文件ID") or -1)
        conclusion_text = str(conclusion).strip()

        if "采信补报" in conclusion_text:
            if file_row is None:
                return None, "没有可采信的补报文件"
            file_debris = store.parse_weight(file_row.get("实测碎茶比例", 0))
            if file_debris > store.parse_weight(handover.get("限值", DEFAULT_LIMIT)):
                return None, "补报值超过限值，不能直接采信，须走重新验收"
            handover["碎茶比例"] = file_debris
            handover["成条率"] = store.parse_weight(file_row.get("实测成条率", handover.get("成条率", 0)))
            file_row["status"] = FILE_SIGNED
            file_row["处置"] = "采信补报"
            file_row["处置说明"] = "复核采信补报，交班按补报值更新"
        elif ("补报有误" in conclusion_text or "有误" in conclusion_text
              or "驳回" in conclusion_text or "不采信" in conclusion_text):
            if file_row is not None:
                file_row["status"] = FILE_REJECTED
                file_row["处置"] = "驳回补报，维持人工结果"
                file_row["处置说明"] = (
                    "补报经复核认定有误；保留其未用于实际发酵的原值 "
                    f"{file_row.get('原值', {}).get('实测碎茶比例')}%"
                )
                file_row["未用于实际发酵"] = True
        elif "重新验收" in conclusion_text:
            if file_row is not None:
                file_row["处置"] = "需重新验收"
                file_row["处置说明"] = "复核认为需班组重新验收，旧交班只保留查询"
            handover["需重新验收"] = True
        else:
            if file_row is not None:
                file_row["status"] = FILE_REVIEWED_UNUSED
                file_row["处置"] = "复核维持人工结果"
                file_row["处置说明"] = "补报未用于实际发酵，保留原始读数备查"
                file_row["未用于实际发酵"] = True

        handover["review_status"] = REVIEW_SIGNED
        handover["review_conclusion"] = conclusion_text
        handover["review_signer"] = signer or "品控员"
        handover["review_signed_at"] = _now()
        if disposition:
            handover["复核处置"] = disposition

        store.log_event("rolling", handover.get("揉捻记录ID") or 0, "sign_review",
                        {"交班号": handover.get("交班号"), "结论": conclusion_text})
        return handover, f"交班 {handover.get('交班号')} 复核结论已签结：{conclusion_text}"

    def reaccept(self, handover_id: int, values: dict[str, Any],
                 operator: str = "") -> tuple[dict[str, Any] | None, str]:
        """班组重新验收：旧交班仍可查询，但不再作为有效入料；生成新版本。"""
        old = self.get_handover(handover_id)
        if old is None:
            return None, f"交班 {handover_id} 不存在"
        if old.get("status") != HANDOVER_ISSUED:
            return None, "该交班已作废，不能重复重新验收"

        manual = store.parse_weight(values.get("人工复称值", values.get("人工复称碎茶比例", 0)))
        limit = store.parse_weight(values.get("限值", old.get("限值", DEFAULT_LIMIT))) or DEFAULT_LIMIT
        if str(values.get("人工复称值", values.get("人工复称碎茶比例", ""))).strip() == "":
            return None, "重新验收必须填写人工复称值"
        if manual > limit:
            return None, f"重新验收人工复称 {manual:.1f}% 超过限值 {limit:.1f}%，不得签发"

        new_version = int(old.get("版本", 1)) + 1
        new_id = store.ops_next_id("rolling_handovers")
        new_no = f"{old.get('交班号')}-V{new_version}"
        new_handover = copy.deepcopy(old)
        new_handover.update({
            "id": new_id,
            "交班号": new_no,
            "版本": new_version,
            "碎茶比例": manual,
            "人工复称值": manual,
            "限值": limit,
            "判定": "合格",
            "status": HANDOVER_ISSUED,
            "有效入料": True,
            "review_status": REVIEW_NONE,
            "待复核文件ID": None,
            "待复核文件说明": "",
            "review_conclusion": "",
            "review_signer": "",
            "review_signed_at": "",
            "入料状态": "未入料",
            "入料回执ID": None,
            "入料重量": 0,
            "入料时间": "",
            "替代交班号": "",
            "被替代": False,
            "版本历史": (old.get("版本历史") or []) + [old.get("交班号")],
            "来源": {"类型": "重新验收人工复称", "成条率": old.get("成条率", 0), "碎茶比例": manual},
            "来源文件ID": None,
            "签发时间": _now(),
            "签发人": operator or str(values.get("签发人") or "班长"),
            "重新验收原因": str(values.get("原因") or ""),
        })
        self._handovers().append(new_handover)

        old["status"] = HANDOVER_VOID
        old["有效入料"] = False
        old["被替代"] = True
        old["替代交班号"] = new_no
        old["作废原因"] = str(values.get("原因") or "班组重新验收")
        old["作废时间"] = _now()

        # 旧交班产生的历史接料记录保留，但入料身份失效，避免出现两份有效入料。
        for receipt in self._receipts():
            if receipt.get("交班ID") == handover_id and receipt.get("有效入料身份"):
                receipt["有效入料身份"] = False
                receipt["状态"] = RECEIPT_SUPERSEDED
                receipt["替代说明"] = f"旧交班 {old.get('交班号')} 已由 {new_no} 重新验收替代"

        store.log_event("rolling", old.get("揉捻记录ID") or 0, "reaccept",
                        {"旧交班号": old.get("交班号"), "新交班号": new_no})
        return new_handover, (
            f"交班 {old.get('交班号')} 已被 {new_no} 替代；旧交班仍可查询，但不再作为有效入料"
        )

    # ── 导出与交接单 ──

    def export_handover(self, handover_id: int, operator: str = "") -> tuple[dict[str, Any] | None, str]:
        """把当前交班连同其来源冻结成一份可回查的导出版本。"""
        handover = self.get_handover(handover_id)
        if handover is None:
            return None, f"交班 {handover_id} 不存在"

        prior = [e for e in self._exports() if e.get("交班ID") == handover_id]
        export_version = len(prior) + 1
        related_files = [
            copy.deepcopy(f) for f in self._files()
            if f.get("批次号") == handover.get("批次号")
            and f.get("实际茶盆") == handover.get("实际茶盆")
        ]
        snapshot = {
            "交班": copy.deepcopy(handover),
            "来源": copy.deepcopy(handover.get("来源", {})),
            "采样文件": related_files,
            "导出时点": _now(),
        }
        export_row: dict[str, Any] = {
            "id": store.ops_next_id("handover_exports"),
            "交班ID": handover_id,
            "交班号": handover.get("交班号"),
            "版本": export_version,
            "导出时间": _now(),
            "导出人": operator or "班长",
            "文件名": f"{handover.get('交班号')}_{handover.get('实际茶盆')}_导出v{export_version}.csv",
            "当时来源": snapshot,
            "内容": self._sheet_rows(handover, related_files),
        }
        self._exports().append(export_row)
        return export_row, f"交班 {handover.get('交班号')} 已导出 v{export_version}，历史版本可回查当时来源"

    def list_exports(self, handover_id: int | None = None) -> list[dict[str, Any]]:
        rows = self._exports()
        if handover_id is not None:
            rows = [r for r in rows if r.get("交班ID") == handover_id]
        return rows

    def get_export(self, export_id: int) -> dict[str, Any] | None:
        return store.ops_find("handover_exports", export_id)

    def _sheet_rows(self, handover: dict[str, Any],
                    related_files: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        files = related_files if related_files is not None else [
            f for f in self._files()
            if f.get("批次号") == handover.get("批次号")
            and f.get("实际茶盆") == handover.get("实际茶盆")
        ]
        supplement = next((f for f in files if f.get("冲突")), None)
        if supplement is None:
            supplement = next((f for f in files if f.get("是否补报")), None)

        rows = [{
            "项目": "成条率",
            "现场原值": handover.get("成条率"),
            "现场来源": (handover.get("来源") or {}).get("说明", "人工复称"),
            "补报值": supplement.get("实测成条率") if supplement else "",
            "补报来源文件": supplement.get("源文件名称") if supplement else "",
            "差值": (round(store.parse_weight(supplement.get("实测成条率", 0))
                           - store.parse_weight(handover.get("成条率", 0)), 2)
                     if supplement else ""),
            "限值": handover.get("限值"),
            "结论/处置": handover.get("review_conclusion") or "—",
        }, {
            "项目": "碎茶比例",
            "现场原值": handover.get("碎茶比例"),
            "现场来源": (handover.get("来源") or {}).get("说明", "人工复称"),
            "补报值": supplement.get("实测碎茶比例") if supplement else "",
            "补报来源文件": supplement.get("源文件名称") if supplement else "",
            "差值": (round(store.parse_weight(supplement.get("实测碎茶比例", 0))
                           - store.parse_weight(handover.get("碎茶比例", 0)), 2)
                     if supplement else ""),
            "限值": handover.get("限值"),
            "结论/处置": handover.get("review_conclusion") or "—",
        }, {
            "项目": "机台/茶盆",
            "现场原值": f"{handover.get('机台身份')} / {handover.get('实际茶盆')}",
            "现场来源": "交班凭据",
            "补报值": (f"{supplement.get('机台身份')} / {supplement.get('实际茶盆')}"
                       if supplement else ""),
            "补报来源文件": supplement.get("源文件名称") if supplement else "",
            "差值": "",
            "限值": "",
            "结论/处置": handover.get("review_conclusion") or "—",
        }, {
            "项目": "研判时段",
            "现场原值": handover.get("研判时段"),
            "现场来源": "交班凭据",
            "补报值": supplement.get("研判时段") if supplement else "",
            "补报来源文件": supplement.get("源文件名称") if supplement else "",
            "差值": "",
            "限值": "",
            "结论/处置": handover.get("review_conclusion") or "—",
        }]
        return rows

    def build_sheet_csv(self, handover_id: int, export_row: dict[str, Any] | None = None) -> tuple[str | None, str]:
        if export_row is not None:
            handover = export_row.get("当时来源", {}).get("交班", {})
            rows = export_row.get("内容", [])
        else:
            handover = self.get_handover(handover_id)
            if handover is None:
                return None, f"交班 {handover_id} 不存在"
            rows = self._sheet_rows(handover)
        output = io.StringIO()
        output.write("﻿")
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(["交班号", handover.get("交班号"), "版本", handover.get("版本")])
        writer.writerow(["批次号", handover.get("批次号"), "实际茶盆", handover.get("实际茶盆")])
        writer.writerow(["机台身份", handover.get("机台身份"), "研判时段", handover.get("研判时段")])
        writer.writerow(["限值(%)", handover.get("限值"), "签发人", handover.get("签发人")])
        writer.writerow(["状态", handover.get("status"), "复核状态", handover.get("review_status")])
        writer.writerow([])
        writer.writerow(["项目", "现场原值", "现场来源", "补报值", "补报来源文件", "差值", "限值", "结论/处置"])
        for row in rows:
            values = [row.get(key, "") for key in
                      ["项目", "现场原值", "现场来源", "补报值", "补报来源文件", "差值", "限值", "结论/处置"]]
            writer.writerow([
                "'" + str(v) if str(v or "").startswith(("=", "+", "-", "@")) else v
                for v in values
            ])
        return output.getvalue(), ""

    # ── 发酵接料 ──

    def _fermentation(self, entry_id: int) -> dict[str, Any] | None:
        return store.find("fermentation", int(entry_id))

    def list_receipts(self, *, 发酵记录ID: int | None = None,
                      handover_id: int | None = None) -> list[dict[str, Any]]:
        rows = self._receipts()
        if 发酵记录ID is not None:
            rows = [r for r in rows if r.get("发酵记录ID") == 发酵记录ID]
        if handover_id is not None:
            rows = [r for r in rows if r.get("交班ID") == handover_id]
        return rows

    def get_receipt(self, receipt_id: int) -> dict[str, Any] | None:
        return store.ops_find("fermentation_receipts", receipt_id)

    def latest_receipt(self, fermentation_id: int) -> dict[str, Any] | None:
        rows = self.list_receipts(发酵记录ID=fermentation_id)
        return rows[-1] if rows else None

    def create_receiving_draft(self, fermentation_id: int, values: dict[str, Any],
                               operator: str = "") -> tuple[dict[str, Any] | None, str]:
        fermentation = self._fermentation(fermentation_id)
        if fermentation is None:
            return None, f"发酵记录 {fermentation_id} 不存在"

        handover = self.find_handover(
            handover_no=str(values.get("交班号") or "").strip() or None,
            handover_id=values.get("交班ID"),
        )
        if handover is None:
            return None, "找不到对应的成条交班凭据"
        if handover.get("status") != HANDOVER_ISSUED or not handover.get("有效入料"):
            return None, f"交班 {handover.get('交班号')} 已作废或被替代，不能作为实际入料凭据"
        if handover.get("判定") != "合格":
            return None, f"交班 {handover.get('交班号')} 判定为{handover.get('判定')}，不符合发酵接料要求"

        basin = str(values.get("实际茶盆") or handover.get("实际茶盆") or "").strip()
        if basin != handover.get("实际茶盆"):
            return None, f"接料茶盆 {basin} 与交班记录的茶盆 {handover.get('实际茶盆')} 不一致"

        batch_no = fermentation.get("批次号") or handover.get("批次号")
        if fermentation.get("批次号") and handover.get("批次号") \
                and fermentation["批次号"] != handover["批次号"]:
            return None, "发酵记录与交班的批次号不一致"
        if fermentation.get("上游工序") == "rolling" and fermentation.get("上游记录ID") is not None \
                and handover.get("揉捻记录ID") is not None \
                and int(fermentation["上游记录ID"]) != int(handover["揉捻记录ID"]):
            return None, "该发酵记录的上游揉捻记录与交班凭据不一致"

        # 同一茶盆只保留一种实际入料身份。
        for other in self._handovers():
            if (other.get("id") != handover.get("id")
                    and other.get("批次号") == handover.get("批次号")
                    and other.get("实际茶盆") == basin
                    and other.get("status") == HANDOVER_ISSUED
                    and other.get("有效入料")):
                return None, f"茶盆 {basin} 已有另一份有效交班 {other.get('交班号')}"

        for receipt in self._receipts():
            if receipt.get("实际茶盆") == basin and receipt.get("有效入料身份"):
                return None, (
                    f"茶盆 {basin} 已实际入料（回执 {receipt.get('id')}，交班 {receipt.get('交班号')}），"
                    "不能重复接料；如已重新验收，请使用新版本交班"
                )
        for receipt in self._receipts():
            if receipt.get("发酵记录ID") == fermentation_id and receipt.get("是否入料"):
                return None, f"发酵记录 {fermentation_id} 已接料（回执 {receipt.get('id')}），不能重复入料"

        existing = next((r for r in self._receipts()
                         if r.get("发酵记录ID") == fermentation_id
                         and r.get("交班号") == handover.get("交班号")
                         and r.get("状态") in (RECEIPT_DRAFT, RECEIPT_HELD)), None)
        if existing is not None:
            return existing, "该发酵记录已有同一交班的待提交接料草稿"

        weight = store.parse_weight(values.get("入料重量", 0))
        if weight <= 0:
            return None, "入料重量必须大于0"

        receipt: dict[str, Any] = {
            "id": store.ops_next_id("fermentation_receipts"),
            "发酵记录ID": fermentation_id,
            "批次号": batch_no,
            "实际茶盆": basin,
            "交班ID": handover.get("id"),
            "交班号": handover.get("交班号"),
            "机台身份": handover.get("机台身份"),
            "研判时段": handover.get("研判时段"),
            "入料重量": weight,
            "状态": RECEIPT_DRAFT,
            "是否入料": False,
            "实际交班号": "",
            "有效入料身份": False,
            "提交人": operator or str(values.get("提交人") or ""),
            "提交时间": _now(),
            "入料时间": "",
            "挂起原因": "",
            "关联复核文件ID": None,
            "替代说明": "",
            "使用来源": copy.deepcopy(handover.get("来源", {})),
        }
        self._receipts().append(receipt)
        return receipt, f"接料草稿已建立，关联交班 {handover.get('交班号')}"

    def submit_receiving(self, receipt_id: int) -> tuple[dict[str, Any] | None, bool, str]:
        """提交接料。

        返回 (回执, 是否写入入料, 说明)。补报文件已收到但未受理核查时，
        回执会被挂起且不写入入料；受理后（复核未签结亦可）请求可继续。
        """
        receipt = self.get_receipt(receipt_id)
        if receipt is None:
            return None, False, f"接料回执 {receipt_id} 不存在"
        if receipt.get("状态") == RECEIPT_FED:
            return receipt, True, "该接料请求已写入，回执可直接查看入料与交班号"
        if receipt.get("有效入料身份") is False and receipt.get("状态") == RECEIPT_SUPERSEDED:
            return receipt, False, "该接料回执已被重新验收替代，不再是有效入料"

        handover = self.get_handover(int(receipt.get("交班ID", 0)))
        if handover is None or handover.get("status") != HANDOVER_ISSUED or not handover.get("有效入料"):
            receipt["状态"] = RECEIPT_HELD
            receipt["挂起原因"] = "交班凭据已作废或被替代"
            return receipt, False, receipt["挂起原因"]

        # 补报文件“已收到未核签/未受理”时，不允许把入料写进去。
        pending = [
            f for f in self._files()
            if f.get("批次号") == handover.get("批次号")
            and f.get("实际茶盆") == handover.get("实际茶盆")
            and f.get("冲突")
            and f.get("status") == FILE_RECEIVED
        ]
        if pending:
            receipt["状态"] = RECEIPT_HELD
            receipt["挂起原因"] = (
                "同机台补报文件已收到但尚未受理核查，接料暂不写入；"
                "请品控先受理该文件核查（复核结论可稍后签结）"
            )
            receipt["关联复核文件ID"] = pending[0].get("id")
            return receipt, False, receipt["挂起原因"]

        # 继续：仍使用正式签发的交班（人工复称结果），补报仅在复核中，不替代。
        receipt["状态"] = RECEIPT_FED
        receipt["是否入料"] = True
        receipt["实际交班号"] = handover.get("交班号")
        receipt["有效入料身份"] = True
        receipt["入料时间"] = _now()
        receipt["挂起原因"] = ""
        receipt["入料时交班复核状态"] = handover.get("review_status")
        receipt["使用来源"] = copy.deepcopy(handover.get("来源", {}))

        handover["入料状态"] = "已入料"
        handover["入料回执ID"] = receipt.get("id")
        handover["入料重量"] = receipt.get("入料重量")
        handover["入料时间"] = receipt.get("入料时间")

        fermentation = self._fermentation(int(receipt.get("发酵记录ID", 0)))
        if fermentation is not None:
            fermentation["实际茶盆"] = receipt.get("实际茶盆")
            fermentation["接料交班号"] = handover.get("交班号")
            fermentation["入料回执ID"] = receipt.get("id")
            fermentation["是否入料"] = True
            fermentation["入料重量"] = receipt.get("入料重量")

        store.log_event("fermentation", int(receipt.get("发酵记录ID", 0)), "receiving",
                        {"回执": receipt.get("id"), "交班号": handover.get("交班号"),
                         "茶盆": receipt.get("实际茶盆")})
        return receipt, True, (
            f"已接料：茶盆 {receipt.get('实际茶盆')} 实际使用交班 {receipt.get('实际交班号')}"
        )


rolling_handover = RollingHandoverService()
