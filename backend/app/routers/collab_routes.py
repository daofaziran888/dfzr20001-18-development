"""揉捻成条交班与发酵接料路由。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.services.rolling_handover import (
    FILE_RECEIVED,
    rolling_handover,
)

router = APIRouter(prefix="/api/collab", tags=["工序协同"])


def _file_or_404(file_id: int) -> dict[str, Any]:
    from app.store import store
    row = store.ops_find("rolling_files", file_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"采样文件 {file_id} 不存在")
    return row


def _handover_or_404(handover_id: int) -> dict[str, Any]:
    row = rolling_handover.get_handover(handover_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"交班 {handover_id} 不存在")
    return row


# ── 采样文件 ──

@router.get("/rolling/files")
def list_rolling_files(
    batch_no: str | None = Query(default=None),
    basin: str | None = Query(default=None),
    status: str | None = Query(default=None),
) -> dict[str, Any]:
    files = rolling_handover.list_files(批次号=batch_no, 实际茶盆=basin, status=status)
    return {"ok": True, "files": files, "items": files, "total": len(files)}


@router.post("/rolling/files/import")
def import_rolling_file(payload: dict[str, Any]) -> dict[str, Any]:
    """导入机器采样文件。按实际茶盆、研判时段与当次机器身份记账。"""
    values = payload.get("values", payload) if isinstance(payload, dict) else {}
    operator = str(payload.get("operator") or payload.get("导入人") or "") if isinstance(payload, dict) else ""
    result = rolling_handover.import_file(values, operator)
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result["message"])
    return {"ok": True, **result}


@router.get("/rolling/files/{file_id}")
def get_rolling_file(file_id: int) -> dict[str, Any]:
    return _file_or_404(file_id)


@router.post("/rolling/files/{file_id}/countersign")
def countersign_rolling_file(file_id: int, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    signer = str((payload or {}).get("signer") or (payload or {}).get("核签人") or "")
    file_row, message = rolling_handover.countersign_file(file_id, signer)
    if file_row is None:
        raise HTTPException(status_code=400, detail=message)
    return {"ok": True, "message": message, "file": file_row}


# ── 交班 ──

@router.get("/rolling/handovers")
def list_handovers(
    batch_no: str | None = Query(default=None),
    basin: str | None = Query(default=None),
) -> dict[str, Any]:
    rows = rolling_handover.list_handovers(批次号=batch_no, 实际茶盆=basin)
    return {"ok": True, "handovers": rows, "items": rows, "total": len(rows)}


@router.post("/rolling/handovers")
def issue_handover(payload: dict[str, Any]) -> dict[str, Any]:
    """品控员凭人工复称签发成条交班。"""
    values = payload.get("values", payload) if isinstance(payload, dict) else {}
    operator = str(payload.get("operator") or payload.get("签发人") or "") if isinstance(payload, dict) else ""
    handover, message = rolling_handover.issue_handover(values, operator)
    if handover is None:
        raise HTTPException(status_code=400, detail=message)
    return {"ok": True, "message": message, "handover": handover, "entry": handover}


@router.get("/rolling/handovers/{handover_id}")
def get_handover(handover_id: int) -> dict[str, Any]:
    return _handover_or_404(handover_id)


@router.get("/rolling/handovers/{handover_id}/ops")
def handover_operator_sheet(handover_id: int) -> dict[str, Any]:
    """当盆交班办理页所需的复核信息。"""
    handover = _handover_or_404(handover_id)
    from app.store import store
    review_file = store.ops_find("rolling_files", handover.get("review_file_id") or -1) \
        if handover.get("review_file_id") else None
    return {
        "ok": True,
        "handover": handover,
        "review_file": review_file,
        "pending_files": [
            f for f in rolling_handover.list_files(
                批次号=handover.get("批次号"), 实际茶盆=handover.get("实际茶盆"))
            if f.get("status") == FILE_RECEIVED
            and not f.get("用于签发")
            and (f.get("冲突") or f.get("是否补报"))
        ],
        "sheet": rolling_handover._sheet_rows(handover),
    }


@router.post("/rolling/handovers/{handover_id}/accept-review")
def accept_review(handover_id: int, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    file_id = (payload or {}).get("file_id")
    reviewer = str((payload or {}).get("reviewer") or (payload or {}).get("受理人") or "")
    handover, message = rolling_handover.accept_file_for_review(handover_id, file_id, reviewer)
    if handover is None:
        raise HTTPException(status_code=400, detail=message)
    return {"ok": True, "message": message, "handover": handover}


@router.post("/rolling/handovers/{handover_id}/sign-review")
def sign_review(handover_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    conclusion = str(payload.get("conclusion") or payload.get("复核结论") or "")
    signer = str(payload.get("signer") or payload.get("签结人") or "")
    disposition = str(payload.get("disposition") or payload.get("处置") or "")
    handover, message = rolling_handover.sign_review(handover_id, conclusion, signer, disposition)
    if handover is None:
        raise HTTPException(status_code=400, detail=message)
    return {"ok": True, "message": message, "handover": handover}


@router.post("/rolling/handovers/{handover_id}/reaccept")
def reaccept_handover(handover_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    values = payload.get("values", payload) if isinstance(payload, dict) else {}
    operator = str(payload.get("operator") or payload.get("签发人") or "") if isinstance(payload, dict) else ""
    handover, message = rolling_handover.reaccept(handover_id, values, operator)
    if handover is None:
        raise HTTPException(status_code=400, detail=message)
    return {"ok": True, "message": message, "handover": handover}


@router.post("/rolling/handovers/{handover_id}/export")
def export_handover(handover_id: int, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    operator = str((payload or {}).get("operator") or (payload or {}).get("导出人") or "")
    export_row, message = rolling_handover.export_handover(handover_id, operator)
    if export_row is None:
        raise HTTPException(status_code=404, detail=message)
    return {"ok": True, "message": message, "export": export_row}


@router.get("/rolling/handovers/{handover_id}/exports")
def list_handover_exports(handover_id: int) -> dict[str, Any]:
    rows = rolling_handover.list_exports(handover_id)
    return {"ok": True, "exports": rows, "items": rows, "total": len(rows)}


@router.get("/rolling/handovers/{handover_id}/sheet.csv")
def handover_sheet_csv(handover_id: int, version: int | None = Query(default=None)) -> StreamingResponse:
    """下载交接单；指定 version 时取历史导出版本，回查当时来源。"""
    export_row = None
    if version is not None:
        export_row = next(
            (e for e in rolling_handover.list_exports(handover_id) if int(e.get("版本", 0)) == version),
            None,
        )
        if export_row is None:
            raise HTTPException(status_code=404, detail=f"交班 {handover_id} 没有导出版本 {version}")
    content, message = rolling_handover.build_sheet_csv(handover_id, export_row)
    if content is None:
        raise HTTPException(status_code=404, detail=message)
    handover = export_row.get("当时来源", {}).get("交班", {}) if export_row else _handover_or_404(handover_id)
    filename = f"{handover.get('交班号')}_交接单" + (f"_v{version}" if version else "") + ".csv"
    from urllib.parse import quote
    return StreamingResponse(
        iter([content]), media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition":
                f"attachment; filename=\"handover{'_v' + str(version) if version else ''}.csv\"; "
                f"filename*=UTF-8''{quote(filename)}",
        },
    )


# ── 发酵接料 ──

@router.get("/fermentation/receipts")
def list_receipts(
    fermentation_id: int | None = Query(default=None),
    handover_id: int | None = Query(default=None),
) -> dict[str, Any]:
    rows = rolling_handover.list_receipts(发酵记录ID=fermentation_id, handover_id=handover_id)
    return {"ok": True, "receipts": rows, "items": rows, "total": len(rows)}


@router.post("/fermentation/{fermentation_id}/receiving-draft")
def create_receiving_draft(fermentation_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    """从接料页建立发酵接料草稿，关联成条交接凭据。"""
    values = payload.get("values", payload) if isinstance(payload, dict) else {}
    operator = str(payload.get("operator") or "") if isinstance(payload, dict) else ""
    receipt, message = rolling_handover.create_receiving_draft(fermentation_id, values, operator)
    if receipt is None:
        raise HTTPException(status_code=400, detail=message)
    return {"ok": True, "message": message, "receipt": receipt}


@router.get("/fermentation/receipts/{receipt_id}")
def get_receipt(receipt_id: int) -> dict[str, Any]:
    from app.store import store
    receipt = store.ops_find("fermentation_receipts", receipt_id)
    if receipt is None:
        raise HTTPException(status_code=404, detail=f"接料回执 {receipt_id} 不存在")
    return receipt


@router.post("/fermentation/receipts/{receipt_id}/submit")
def submit_receiving(receipt_id: int) -> dict[str, Any]:
    """提交接料请求；回执直接显示是否入料与实际使用的交班号。"""
    receipt, fed, message = rolling_handover.submit_receiving(receipt_id)
    if receipt is None:
        raise HTTPException(status_code=404, detail=message)
    return {
        "ok": True,
        "status": receipt.get("状态"),
        "是否入料": bool(receipt.get("是否入料")),
        "实际交班号": receipt.get("实际交班号") or "",
        "receipt": receipt,
        "message": message,
    }
