<script setup>
import { computed } from 'vue';

const props = defineProps({
  ticket: {
    type: Object,
    default: () => null,
  },
});

// Show "auto-routed (Legal)" or "manual handoff" prettier than the raw key.
const sourceLabel = computed(() => {
  const s = props.ticket?.source;
  if (s === 'manual_handoff') return 'Manual handoff from bot';
  if (s === 'auto_legal') return 'Auto-routed (Legal)';
  return s || '';
});

const ticketLabel = computed(() => {
  const t = props.ticket;
  if (!t) return '';
  if (t.number) return `#${t.number}`;
  if (t.id) return t.id;
  return '';
});
</script>

<template>
  <div v-if="ticket" class="flex flex-col gap-2 p-3 rounded-md bg-n-alpha-1">
    <div class="flex items-center justify-between gap-2">
      <div class="flex items-center gap-2 min-w-0">
        <span class="i-ri-ticket-line text-base text-n-slate-11 shrink-0" />
        <span class="text-sm font-medium text-n-slate-12 truncate">
          Zoho Desk
        </span>
      </div>
      <a
        v-if="ticket.url"
        :href="ticket.url"
        target="_blank"
        rel="noopener noreferrer"
        class="text-xs font-medium text-n-brand hover:underline shrink-0"
      >
        Open in Zoho
        <span class="i-lucide-external-link align-middle" />
      </a>
    </div>
    <div class="flex flex-col gap-1 text-xs text-n-slate-11">
      <div v-if="ticketLabel">
        <span class="text-n-slate-10">Ticket:</span>
        <span class="ml-1 font-medium text-n-slate-12">{{ ticketLabel }}</span>
      </div>
      <div v-if="sourceLabel">
        <span class="text-n-slate-10">Source:</span>
        <span class="ml-1 text-n-slate-12">{{ sourceLabel }}</span>
      </div>
    </div>
  </div>
  <div v-else class="px-3 pb-2 text-xs text-n-slate-10">
    No Zoho ticket associated with this conversation yet.
  </div>
</template>
