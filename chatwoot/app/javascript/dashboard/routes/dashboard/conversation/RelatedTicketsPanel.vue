<script setup>
defineProps({
  tickets: {
    type: Array,
    default: () => [],
  },
});
</script>

<template>
  <div v-if="tickets && tickets.length" class="flex flex-col gap-2 p-3">
    <div
      v-for="ticket in tickets"
      :key="ticket.id || ticket.number"
      class="flex items-center justify-between gap-2 rounded-md bg-n-alpha-1 p-2"
    >
      <div class="flex items-center gap-2 min-w-0">
        <span class="i-ri-ticket-line text-base text-n-slate-11 shrink-0" />
        <span class="text-sm font-medium text-n-slate-12 truncate">
          {{ ticket.subject || ticket.title || `#${ticket.number || ticket.id}` }}
        </span>
      </div>
      <a
        v-if="ticket.url"
        :href="ticket.url"
        target="_blank"
        rel="noopener noreferrer"
        class="text-xs font-medium text-n-brand hover:underline shrink-0"
      >
        Open
        <span class="i-lucide-external-link align-middle" />
      </a>
    </div>
  </div>
  <div v-else class="px-3 pb-2 text-xs text-n-slate-10">
    No related tickets found.
  </div>
</template>
