<template>
  <section class="collab-panel" data-testid="rolling-handover-panel">
    <header class="collab-head">
      <div>
        <h3>揉捻成条交班</h3>
        <p class="collab-desc">
          导入机器采样文件，按实际茶盆、研判时段与当次机器身份记账；品控凭人工复称签发成条交班。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" data-testid="open-import-file" type="button" @click="toggleImport">
          {{ showImport ? '收起导入' : '导入揉捻记录文件' }}
        </button>
      </div>
    </header>

    <div v-if="showImport" class="collab-form" data-testid="import-file-form">
      <label v-for="field in importFields" :key="field.key" class="collab-field">
        <span>{{ field.label }}</span>
        <input
          v-model="importValues[field.key]"
          :data-testid="`import-${field.key}`"
          :placeholder="field.placeholder"
        />
      </label>
      <label class="collab-field collab-check">
        <span>是否补报</span>
        <input v-model="importSupplement" data-testid="import-supplement" type="checkbox" />
      </label>
      <button class="btn primary" data-testid="submit-import" type="button" :disabled="busy" @click="submitImport">
        导入采样文件
      </button>
    </div>
    <p v-if="importResult" class="collab-notice" data-testid="import-result">{{ importResult }}</p>

    <h4 class="collab-sub">成条交班凭据</h4>
    <table class="data-table">
      <thead>
        <tr>
          <th>交班号</th>
          <th>版本</th>
          <th>实际茶盆</th>
          <th>机台 / 研判时段</th>
          <th>成条率</th>
          <th>人工复称碎茶比例</th>
          <th>限值</th>
          <th>判定</th>
          <th>状态</th>
          <th>复核</th>
          <th>入料</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="h in handovers"
          :key="h.id"
          :data-testid="`handover-${h.id}`"
          :data-status="h.status"
        >
          <td>{{ h.交班号 }}</td>
          <td>{{ h.版本 }}</td>
          <td>{{ h.实际茶盆 }}</td>
          <td>{{ h.机台身份 }} / {{ h.研判时段 }}</td>
          <td>{{ h.成条率 }}</td>
          <td>{{ h.人工复称值 }}</td>
          <td>{{ h.限值 }}</td>
          <td>{{ h.判定 }}</td>
          <td>{{ h.status }}<span v-if="!h.有效入料">（不用于入料）</span></td>
          <td>{{ h.review_status }}</td>
          <td>{{ h.入料状态 || '未入料' }}</td>
          <td class="row-actions">
            <button class="link" :data-testid="`open-ops-${h.id}`" type="button" @click="openOps(h)">
              当盆交班办理
            </button>
            <button class="link" :data-testid="`export-${h.id}`" type="button" @click="exportHandover(h)">
              交班导出
            </button>
            <button class="link" :data-testid="`sheet-${h.id}`" type="button" @click="downloadSheet(h)">
              下载交接单
            </button>
          </td>
        </tr>
        <tr v-if="!handovers.length">
          <td colspan="12" class="empty-state">暂无成条交班，可先在下方录入人工复称并签发</td>
        </tr>
      </tbody>
    </table>

    <h4 class="collab-sub">签发成条交班（品控）</h4>
    <div class="collab-form" data-testid="issue-handover-form">
      <label v-for="field in issueFields" :key="field.key" class="collab-field">
        <span>{{ field.label }}</span>
        <input
          v-model="issueValues[field.key]"
          :data-testid="`issue-${field.key}`"
          :placeholder="field.placeholder"
        />
      </label>
      <button class="btn primary" data-testid="submit-issue-handover" type="button" :disabled="busy" @click="submitIssue">
        签发交班
      </button>
    </div>
    <p v-if="errorMessage" class="error-text" data-testid="rolling-error">{{ errorMessage }}</p>

    <!-- 当盆交班办理：复核面板 -->
    <div v-if="opsHandover" class="ops-panel" data-testid="handover-ops">
      <header class="collab-head">
        <div>
          <h3>当盆交班办理 · {{ opsHandover.交班号 }}</h3>
          <p class="collab-desc">
            茶盆 {{ opsHandover.实际茶盆 }} · {{ opsHandover.机台身份 }} · {{ opsHandover.研判时段 }} ·
            人工复称 {{ opsHandover.人工复称值 }}% · 限值 {{ opsHandover.限值 }}% ·
            复核状态：{{ opsHandover.review_status }}
          </p>
        </div>
        <button class="btn" type="button" @click="closeOps">关闭</button>
      </header>

      <table class="data-table" data-testid="review-sheet">
        <thead>
          <tr>
            <th>项目</th>
            <th>现场原值</th>
            <th>现场来源</th>
            <th>补报值</th>
            <th>补报来源文件</th>
            <th>差值</th>
            <th>限值</th>
            <th>结论 / 处置</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in opsSheet" :key="row.项目">
            <td>{{ row.项目 }}</td>
            <td>{{ row.现场原值 }}</td>
            <td>{{ row.现场来源 }}</td>
            <td>{{ row.补报值 || '—' }}</td>
            <td>{{ row.补报来源文件 || '—' }}</td>
            <td>{{ row.差值 || '—' }}</td>
            <td>{{ row.限值 || '—' }}</td>
            <td>{{ row["结论/处置"] }}</td>
          </tr>
        </tbody>
      </table>

      <p v-if="opsHandover.待复核文件说明" class="collab-notice" data-testid="pending-review-note">
        {{ opsHandover.待复核文件说明 }}（复核结论尚未签结，交班结果仍按人工复称）
      </p>

      <div v-if="opsPendingFiles.length" class="collab-form">
        <span class="collab-label">待受理补报文件：</span>
        <button
          v-for="file in opsPendingFiles"
          :key="file.id"
          class="btn"
          :data-testid="`accept-file-${file.id}`"
          type="button"
          :disabled="busy"
          @click="acceptFile(file)"
        >
          受理 {{ file.源文件名称 }} 核查
        </button>
      </div>

      <div class="collab-form" data-testid="sign-review-form">
        <label class="collab-field collab-wide">
          <span>复核结论</span>
          <input
            v-model="reviewConclusion"
            data-testid="review-conclusion"
            placeholder="如：补报有误，维持人工复称 或 采信补报 或 需重新验收"
          />
        </label>
        <label class="collab-field">
          <span>签结人</span>
          <input v-model="reviewSigner" data-testid="review-signer" />
        </label>
        <button class="btn primary" data-testid="submit-sign-review" type="button" :disabled="busy" @click="signReview">
          签结复核结论
        </button>
      </div>

      <div class="collab-form" data-testid="reaccept-form">
        <label class="collab-field">
          <span>重新验收人工复称值（%）</span>
          <input v-model="reacceptValues.人工复称值" data-testid="reaccept-value" />
        </label>
        <label class="collab-field">
          <span>重新验收原因</span>
          <input v-model="reacceptValues.原因" data-testid="reaccept-reason" />
        </label>
        <button class="btn" data-testid="submit-reaccept" type="button" :disabled="busy" @click="submitReaccept">
          班组重新验收
        </button>
      </div>

      <h4 class="collab-sub">交班导出历史版本</h4>
      <table class="data-table" data-testid="export-history">
        <thead>
          <tr><th>版本</th><th>导出时间</th><th>导出人</th><th>文件名</th><th>当时来源</th><th>操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="ex in opsExports" :key="ex.id">
            <td>v{{ ex.版本 }}</td>
            <td>{{ ex.导出时间 }}</td>
            <td>{{ ex.导出人 }}</td>
            <td>{{ ex.文件名 }}</td>
            <td>
              人工复称 {{ ex.当时来源?.交班?.人工复称值 }}% ·
              交班状态 {{ ex.当时来源?.交班?.status }} ·
              来源 {{ ex.当时来源?.来源?.说明 }}
            </td>
            <td>
              <button class="link" :data-testid="`download-version-${ex.版本}`" type="button" @click="downloadSheet(opsHandover!, ex.版本)">
                下载该版本
              </button>
            </td>
          </tr>
          <tr v-if="!opsExports.length">
            <td colspan="6" class="empty-state">尚未导出，点“交班导出”生成第一份历史版本</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'

import { collabApi } from '@/api/collab-client'
import type { Handover, HandoverExport, ReviewSheetRow, RollingFile } from '@/api/collab-client'

const route = useRoute()
const busy = ref(false)
const errorMessage = ref('')
const showImport = ref(false)
const importResult = ref('')
const handovers = ref<Handover[]>([])
const opsHandover = ref<Handover | null>(null)
const opsSheet = ref<ReviewSheetRow[]>([])
const opsPendingFiles = ref<RollingFile[]>([])
const opsExports = ref<HandoverExport[]>([])
const reviewConclusion = ref('')
const reviewSigner = ref('品控员')

const importFields = [
  { key: '源文件名称', label: '源文件名称', placeholder: 'roll_YYYYMMDD.csv' },
  { key: '批次号', label: '批次号', placeholder: 'BATCH-000001' },
  { key: '揉捻记录ID', label: '揉捻记录ID', placeholder: '仅本盆记录' },
  { key: '实际茶盆', label: '实际茶盆', placeholder: '如 1号茶盆' },
  { key: '机台身份', label: '机台身份', placeholder: '如 揉捻机-3号' },
  { key: '研判时段', label: '研判时段', placeholder: '如 09:00-10:00' },
  { key: '实测成条率', label: '实测成条率（%）', placeholder: '86.4' },
  { key: '实测碎茶比例', label: '实测碎茶比例（%）', placeholder: '4.7' },
]
const importValues = reactive<Record<string, string>>(
  Object.fromEntries(importFields.map((field) => [field.key, ''])),
)
const importSupplement = ref(false)

const issueFields = [
  { key: '批次号', label: '批次号', placeholder: 'BATCH-000001' },
  { key: '实际茶盆', label: '实际茶盆', placeholder: '如 1号茶盆' },
  { key: '机台身份', label: '机台身份', placeholder: '如 揉捻机-3号' },
  { key: '研判时段', label: '研判时段', placeholder: '如 09:00-10:00' },
  { key: '人工复称值', label: '人工复称碎茶比例（%）', placeholder: '4.8' },
  { key: '限值', label: '限值（%）', placeholder: '5' },
  { key: '成条率', label: '成条率（%）', placeholder: '86.4' },
  { key: '来源文件ID', label: '来源文件ID（可选）', placeholder: '' },
]
const issueValues = reactive<Record<string, string>>(
  Object.fromEntries(issueFields.map((field) => [field.key, ''])),
)
const reacceptValues = reactive<Record<string, string>>({ 人工复称值: '', 原因: '' })

async function reload() {
  try {
    const payload = await collabApi.handovers()
    handovers.value = payload.handovers
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '交班列表读取失败'
  }
}

function toggleImport() {
  showImport.value = !showImport.value
}

async function submitImport() {
  busy.value = true
  errorMessage.value = ''
  importResult.value = ''
  try {
    const values: Record<string, unknown> = { ...importValues }
    values.是否补报 = importSupplement.value
    if (values.揉捻记录ID === '') delete values.揉捻记录ID
    const result = await collabApi.importFile(values, '制茶师')
    importResult.value = result.重复导入
      ? result.message
      : `已导入 ${result.file.源文件名称}：${result.重算说明}`
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '采样文件导入失败'
  } finally {
    busy.value = false
  }
}

async function submitIssue() {
  busy.value = true
  errorMessage.value = ''
  try {
    const values: Record<string, unknown> = { ...issueValues }
    if (values.来源文件ID === '') delete values.来源文件ID
    const result = await collabApi.issueHandover(values, '品控员')
    await reload()
    await openOps(result.handover)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '交班签发失败'
  } finally {
    busy.value = false
  }
}

async function openOps(handover: Handover) {
  busy.value = true
  errorMessage.value = ''
  try {
    const payload = await collabApi.handoverOps(handover.id)
    opsHandover.value = payload.handover
    opsSheet.value = payload.sheet
    opsPendingFiles.value = payload.pending_files
    const payloadExports = await collabApi.exports(handover.id)
    opsExports.value = payloadExports.exports
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '交班办理信息读取失败'
  } finally {
    busy.value = false
  }
}

function closeOps() {
  opsHandover.value = null
}

async function refreshOps() {
  if (opsHandover.value) await openOps(opsHandover.value)
  await reload()
}

async function acceptFile(file: RollingFile) {
  if (!opsHandover.value) return
  busy.value = true
  errorMessage.value = ''
  try {
    await collabApi.acceptReview(opsHandover.value.id, file.id)
    await refreshOps()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '受理核查失败'
  } finally {
    busy.value = false
  }
}

async function signReview() {
  if (!opsHandover.value) return
  busy.value = true
  errorMessage.value = ''
  try {
    await collabApi.signReview(opsHandover.value.id, reviewConclusion.value, reviewSigner.value)
    reviewConclusion.value = ''
    await refreshOps()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '复核签结失败'
  } finally {
    busy.value = false
  }
}

async function submitReaccept() {
  if (!opsHandover.value) return
  busy.value = true
  errorMessage.value = ''
  try {
    const result = await collabApi.reaccept(opsHandover.value.id, { ...reacceptValues }, '班长')
    reacceptValues.人工复称值 = ''
    reacceptValues.原因 = ''
    await reload()
    await openOps(result.handover)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '重新验收失败'
  } finally {
    busy.value = false
  }
}

async function exportHandover(handover: Handover) {
  busy.value = true
  errorMessage.value = ''
  try {
    await collabApi.exportHandover(handover.id, '班长')
    await openOps(handover)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '交班导出失败'
  } finally {
    busy.value = false
  }
}

async function downloadSheet(handover: Handover, version?: number) {
  busy.value = true
  errorMessage.value = ''
  try {
    const blob = await collabApi.downloadSheet(handover.id, version)
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `${handover.交班号}_交接单${version ? `_v${version}` : ''}.csv`
    document.body.appendChild(anchor)
    anchor.click()
    document.body.removeChild(anchor)
    URL.revokeObjectURL(url)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '交接单下载失败'
  } finally {
    busy.value = false
  }
}

onMounted(async () => {
  await reload()
  const handoverId = route.query.handover
  if (handoverId) {
    const target = handovers.value.find((h) => String(h.id) === String(handoverId))
    if (target) await openOps(target)
  }
})
</script>

<style scoped>
.collab-panel { background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 14px 16px; margin: 12px 0; }
.collab-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }
.collab-head h3 { margin: 0; font-size: 15px; }
.collab-desc { margin: 4px 0 0; color: var(--muted); font-size: 12px; }
.collab-sub { margin: 16px 0 8px; font-size: 13px; color: #334155; }
.collab-form { display: flex; flex-wrap: wrap; gap: 10px; align-items: flex-end; margin: 10px 0; }
.collab-field { display: flex; flex-direction: column; gap: 3px; font-size: 12px; color: var(--muted); }
.collab-field input { padding: 6px 8px; border: 1px solid var(--border); border-radius: 6px; font-size: 13px; }
.collab-wide input { min-width: 320px; }
.collab-check { flex-direction: row; align-items: center; gap: 6px; }
.collab-notice { background: #fff7e6; border: 1px solid #ffe0a3; color: #8a5a00; border-radius: 6px; padding: 8px 10px; font-size: 12px; }
.ops-panel { margin-top: 16px; border-top: 2px dashed var(--border); padding-top: 12px; }
</style>
