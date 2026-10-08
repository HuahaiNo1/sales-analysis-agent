<script setup lang="ts">
import { ref, useId } from 'vue'
import { FileText, LoaderCircle } from '@lucide/vue'
import type { ReportAnnotations } from '../types/reviews'

const props = defineProps<{ initialTitle: string; busy: boolean; error: string }>()
const emit = defineEmits<{ save: [annotations: ReportAnnotations]; cancel: [] }>()
const title = ref(props.initialTitle)
const comment = ref('')
const suggestions = ref('')
const invalid = ref('')
const headingId = useId()
function save() {
  if (props.busy) return
  if (!title.value.trim()) { invalid.value = '请输入报告标题'; return }
  invalid.value = ''
  emit('save', { title: title.value.trim(), comment: comment.value, suggestions: suggestions.value })
}
</script>
<template>
  <form class="panel report-save-form" :aria-labelledby="headingId" @submit.prevent="save">
    <div class="panel-heading"><div><h2 :id="headingId"><FileText :size="17" />保存一份独立报告</h2><p>数字与证据冻结留存，之后可以修改标题、备注和补充建议</p></div></div>
    <div class="report-fields"><label>报告标题<input v-model="title" maxlength="120" required :disabled="busy" /></label><label>我的备注（可选）<textarea v-model="comment" rows="3" maxlength="4000" :disabled="busy" placeholder="补充业务背景或需要核实的事项"></textarea></label><label>我的补充建议（可选）<textarea v-model="suggestions" rows="3" maxlength="4000" :disabled="busy" placeholder="与原始分析建议分开保存，不会改写数据事实"></textarea></label></div>
    <div v-if="error || invalid" class="alert alert-error" role="alert">{{ error || invalid }}</div>
    <div class="report-form-actions"><button class="button button-outline" type="button" :disabled="busy" @click="emit('cancel')">暂不保存</button><button class="button button-primary" type="submit" :disabled="busy" :aria-busy="busy"><LoaderCircle v-if="busy" class="spin" :size="15" />{{ busy ? '正在保存' : '确认保存报告' }}</button></div>
  </form>
</template>
