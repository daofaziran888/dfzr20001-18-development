<template>
  <section class="collab-panel" data-testid="fermentation-receiving-panel">
    <header class="collab-head">
      <div>
        <h3>发酵接料 · 成条交接凭据</h3>
        <p class="collab-desc">
          只受理符合要求的已签发交班凭据；同一茶盆保留一种实际入料身份。接料回执直接显示是否入料与实际使用的交班号。
        </p>
      </div>
    </header>

    <div class="collab-form" data-testid="receiving-draft-form">
      <label v-for="field in draftFields" :key="field.key" class="collab-field">
        <span>{{ field.label }}</span>
        <input
          v-model="draftValues[field.key]"
          :data-testid="`receive-${field.key}`"
          :placeholder="field.placeholder"
        />
      </label>
      <button class="btn primary" data-testid="submit-receiving-draft" type="button" :disabled="busy" @click="createDraft">
        建立接料草稿
      </button>
    </div>
    <p v-if="receiptMessage" class="collab-notice" data-testid="receipt-message">{{ receiptMessage }}</p>
    <p v-if="errorMessage" class="error-text" data-testid="receiving-error">{{ errorMessage }}</p>

    <table class="data-table">
      <thead>
        <tr>
          <th>回执号</th>
          <th>发酵记录</th>
          <th>实际茶盆</th>
          <th>关联交班号</th>
          <th>入料重量</th>
          <th>状态</th>
          <th>是否入料</th>
          <th>实际使用交班号</th>
          <th>说明</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="r in receipts" :key="r.id" :data-testid="`receipt-${r.id}`" :data-status="r.状态">
          <td>{{ r.id }}</td>
          <td>{{ r.发酵记录ID }}</td>
          <td>{{ r.实际茶盆 }}</td>
          <td>{{ r.交班号 }}</td>
          <td>{{ r.入料重量 }}</td>
          <td>{{ r.状态 }}</td>
          <td :data-testid="`receipt-fed-${r.id}`">{{ r.是否入料 ? '已入料' : '未入料' }}</td>
          <td :data-testid="`receipt-handover-${r.id}`">{{ r.实际交班号 || '—' }}</td>
          <td>{{ r.挂起原因 || r.替代说明 || '—' }}</td>
          <td class="row-actions">
            <button
              class="link"
              :data-testid="`submit-receipt-${r.id}`"
              type="button"
              :disabled="busy"
              @click="submit(r)"
            >
              提交接料
            </button>
            <button
              class="link"
              :data-testid="`receipt-to-handover-${r.id}`"
              type="button"
              :disabled="!r.实际交班号"
              @click="openHandover(r)"
            >
              打开对应交班
            </button>
          </td>
        </tr>
        <tr v-if="!receipts.length">
          <td colspan="10" class="empty-state">暂无接料回执，可先选择已签发交班建立接料草稿</td>
        </tr>
      </tbody>
    </table>

    <p v-if="submitResult" class="collab-notice" data-testid="submit-result">{{ submitResult }}</p>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'

import { collabApi } from '@/api/collab-client'
import type { Receipt } from '@/api/collab-client'

const router = useRouter()
const busy = ref(false)
const errorMessage = ref('')
const receiptMessage = ref('')
const submitResult = ref('')
const receipts = ref<Receipt[]>([])

const draftFields = [
  { key: '发酵记录ID', label: '发酵记录ID', placeholder: '如 3' },
  { key: '交班号', label: '交班号', placeholder: 'HO-BATCH-...' },
  { key: '实际茶盆', label: '实际茶盆', placeholder: '须与交班一致' },
  { key: '入料重量', label: '入料重量（kg）', placeholder: '50' },
]
const draftValues = reactive<Record<string, string>>(
  Object.fromEntries(draftFields.map((field) => [field.key, ''])),
)

async function reload() {
  try {
    const payload = await collabApi.receipts()
    receipts.value = payload.receipts
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '接料回执读取失败'
  }
}

async function createDraft() {
  busy.value = true
  errorMessage.value = ''
  receiptMessage.value = ''
  submitResult.value = ''
  try {
    const result = await collabApi.createReceivingDraft(
      Number(draftValues.发酵记录ID),
      { 交班号: draftValues.交班号, 实际茶盆: draftValues.实际茶盆, 入料重量: draftValues.入料重量 },
      '发酵工',
    )
    receiptMessage.value = result.message
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '接料草稿建立失败'
  } finally {
    busy.value = false
  }
}

async function submit(receipt: Receipt) {
  busy.value = true
  errorMessage.value = ''
  try {
    const result = await collabApi.submitReceiving(receipt.id)
    submitResult.value = (
      `回执 ${receipt.id}：${result.是否入料 ? '已入料' : '未入料'}，`
      + `实际使用交班 ${result.实际交班号 || '（无）'}；${result.message}`
    )
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '接料提交失败'
  } finally {
    busy.value = false
  }
}

function openHandover(receipt: Receipt) {
  void router.push({
    name: 'module',
    params: { moduleKey: 'rolling' },
    query: { handover: String(receipt.交班ID) },
  })
}

onMounted(reload)
</script>

<style scoped>
.collab-panel { background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 14px 16px; margin: 12px 0; }
.collab-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }
.collab-head h3 { margin: 0; font-size: 15px; }
.collab-desc { margin: 4px 0 0; color: var(--muted); font-size: 12px; }
.collab-form { display: flex; flex-wrap: wrap; gap: 10px; align-items: flex-end; margin: 10px 0; }
.collab-field { display: flex; flex-direction: column; gap: 3px; font-size: 12px; color: var(--muted); }
.collab-field input { padding: 6px 8px; border: 1px solid var(--border); border-radius: 6px; font-size: 13px; }
.collab-notice { background: #fff7e6; border: 1px solid #ffe0a3; color: #8a5a00; border-radius: 6px; padding: 8px 10px; font-size: 12px; }
</style>
