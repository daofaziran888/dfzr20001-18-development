/** 揉捻成条交班 / 发酵接料 API 客户端。 */
import { fetchJson, request } from './client'

const API = '/api/collab'

export interface RollingFile {
  id: number
  status: string
  源文件名称: string
  批次号: string
  实际茶盆: string
  机台身份: string
  研判时段: string
  实测成条率: number
  实测碎茶比例: number
  是否补报: boolean
  计入本盆: boolean
  不计入原因: string
  冲突: boolean
  冲突说明: string
  关联交班号: string
  是否重算成条结果?: boolean
  重算说明?: string
  处置?: string
  处置说明?: string
  原值?: Record<string, string | number>
  未用于实际发酵?: boolean
  [key: string]: unknown
}

export interface HandoverSource {
  类型: string
  文件ID?: number | null
  说明: string
  成条率?: number
  碎茶比例?: number
  [key: string]: unknown
}

export interface Handover {
  id: number
  交班号: string
  版本: number
  批次号: string
  揉捻记录ID?: number
  实际茶盆: string
  机台身份: string
  研判时段: string
  成条率: number
  碎茶比例: number
  人工复称值: number
  限值: number
  判定: string
  来源: HandoverSource
  status: string
  有效入料: boolean
  review_status: string
  待复核文件ID?: number | null
  待复核文件说明?: string
  review_conclusion?: string
  入料状态?: string
  入料重量?: number
  入料时间?: string
  替代交班号?: string
  被替代?: boolean
  [key: string]: unknown
}

export interface Receipt {
  id: number
  发酵记录ID: number
  批次号: string
  实际茶盆: string
  交班ID: number
  交班号: string
  入料重量: number
  状态: string
  是否入料: boolean
  实际交班号: string
  挂起原因: string
  有效入料身份: boolean
  替代说明?: string
  [key: string]: unknown
}

export interface ReviewSheetRow {
  项目: string
  现场原值: string | number
  现场来源: string
  补报值: string | number
  补报来源文件: string
  差值: string | number
  限值: string | number
  "结论/处置": string
}

export interface HandoverExport {
  id: number
  交班ID: number
  交班号: string
  版本: number
  导出时间: string
  导出人: string
  文件名: string
  当时来源: {
    交班: Handover
    来源: HandoverSource
    采样文件: RollingFile[]
  }
}

export const collabApi = {
  files: (batchNo?: string, basin?: string) => {
    const params = new URLSearchParams()
    if (batchNo) params.set('batch_no', batchNo)
    if (basin) params.set('basin', basin)
    return fetchJson<{ files: RollingFile[]; total: number }>(`${API}/rolling/files?${params}`)
  },
  importFile: (values: Record<string, unknown>, operator: string) =>
    fetchJson<{ ok: boolean; message: string; file: RollingFile; 是否重算成条结果: boolean; 重算说明: string; 重复导入?: boolean }>(
      `${API}/rolling/files/import`,
      { method: 'POST', body: JSON.stringify({ values, operator }) },
    ),
  handovers: (batchNo?: string, basin?: string) => {
    const params = new URLSearchParams()
    if (batchNo) params.set('batch_no', batchNo)
    if (basin) params.set('basin', basin)
    return fetchJson<{ handovers: Handover[]; total: number }>(`${API}/rolling/handovers?${params}`)
  },
  issueHandover: (values: Record<string, unknown>, operator: string) =>
    fetchJson<{ ok: boolean; message: string; handover: Handover }>(
      `${API}/rolling/handovers`,
      { method: 'POST', body: JSON.stringify({ values, operator }) },
    ),
  handoverOps: (id: number) =>
    fetchJson<{ handover: Handover; review_file: RollingFile | null; pending_files: RollingFile[]; sheet: ReviewSheetRow[] }>(
      `${API}/rolling/handovers/${id}/ops`,
    ),
  acceptReview: (id: number, fileId?: number) =>
    fetchJson<{ ok: boolean; message: string; handover: Handover }>(
      `${API}/rolling/handovers/${id}/accept-review`,
      { method: 'POST', body: JSON.stringify({ file_id: fileId }) },
    ),
  signReview: (id: number, conclusion: string, signer: string, disposition = '') =>
    fetchJson<{ ok: boolean; message: string; handover: Handover }>(
      `${API}/rolling/handovers/${id}/sign-review`,
      { method: 'POST', body: JSON.stringify({ conclusion, signer, disposition }) },
    ),
  reaccept: (id: number, values: Record<string, unknown>, operator: string) =>
    fetchJson<{ ok: boolean; message: string; handover: Handover }>(
      `${API}/rolling/handovers/${id}/reaccept`,
      { method: 'POST', body: JSON.stringify({ values, operator }) },
    ),
  exportHandover: (id: number, operator: string) =>
    fetchJson<{ ok: boolean; message: string; export: HandoverExport }>(
      `${API}/rolling/handovers/${id}/export`,
      { method: 'POST', body: JSON.stringify({ operator }) },
    ),
  exports: (id: number) =>
    fetchJson<{ exports: HandoverExport[]; total: number }>(`${API}/rolling/handovers/${id}/exports`),
  downloadSheet: async (id: number, version?: number) => {
    const query = version ? `?version=${version}` : ''
    const response = await request(`${API}/rolling/handovers/${id}/sheet.csv${query}`)
    if (!response.ok) throw new Error('交接单下载失败')
    return await response.blob()
  },
  receipts: (fermentationId?: number) => {
    const params = new URLSearchParams()
    if (fermentationId !== undefined) params.set('fermentation_id', String(fermentationId))
    return fetchJson<{ receipts: Receipt[]; total: number }>(`${API}/fermentation/receipts?${params}`)
  },
  createReceivingDraft: (fermentationId: number, values: Record<string, unknown>, operator: string) =>
    fetchJson<{ ok: boolean; message: string; receipt: Receipt }>(
      `${API}/fermentation/${fermentationId}/receiving-draft`,
      { method: 'POST', body: JSON.stringify({ values, operator }) },
    ),
  submitReceiving: (receiptId: number) =>
    fetchJson<{ ok: boolean; status: string; 是否入料: boolean; 实际交班号: string; receipt: Receipt; message: string }>(
      `${API}/fermentation/receipts/${receiptId}/submit`,
      { method: 'POST' },
    ),
}
