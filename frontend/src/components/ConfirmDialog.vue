<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, useId } from 'vue'

withDefaults(defineProps<{ title: string; confirmLabel: string; cancelLabel?: string; busy?: boolean }>(), { cancelLabel: '返回', busy: false })
const emit = defineEmits<{ confirm: []; cancel: [] }>()
const headingId = useId()
const dialog = ref<HTMLElement>()
const cancelButton = ref<HTMLButtonElement>()
const previous = document.activeElement as HTMLElement | null
function keydown(event: KeyboardEvent) {
  if (event.key === 'Escape') { event.preventDefault(); emit('cancel') }
  if (event.key !== 'Tab') return
  const buttons = Array.from(dialog.value?.querySelectorAll<HTMLButtonElement>('button:not(:disabled)') || [])
  const first = buttons[0]; const last = buttons.at(-1)
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus() }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus() }
}
onMounted(() => void nextTick(() => cancelButton.value?.focus()))
onBeforeUnmount(() => previous?.focus())
</script>
<template><Teleport to="body"><div class="modal-backdrop" @click.self="!busy && emit('cancel')"><section ref="dialog" class="confirm-modal" role="dialog" aria-modal="true" :aria-labelledby="headingId" @keydown="keydown"><h2 :id="headingId">{{ title }}</h2><div class="confirm-content"><slot /></div><div class="modal-actions"><button ref="cancelButton" class="button button-outline" :disabled="busy" @click="emit('cancel')">{{ cancelLabel }}</button><button class="button button-primary" :disabled="busy" @click="emit('confirm')">{{ confirmLabel }}</button></div></section></div></Teleport></template>
