<script setup>
import { computed } from 'vue';
import { format } from 'date-fns';

// Multi-ticket sidebar panel. Renders the FULL `custom_attributes.zoho_tickets`
// array — one card per ticket, newest first — written by the zoho-bridge
// sidecar. Each ticket entry has the shape:
//   { id, number, url, subject, source, created_at, status }
//
// Replaces the single-ticket ZohoTicketPanel that only ever rendered the
// head of the array. Now an agent can see ALL escalations a conversation
// has accumulated (AI-signal ticket from first message + priority bump from
// later, re-escalations on re-opens, etc.).
const props = defineProps({
  tickets: {
    type: Array,
    default: () => [],
  },
});

// Show a human-friendly label for ticket.source. The bridge writes these
// codes; the agent shouldn't have to decode them.
const sourceLabel = source => {
  const s = String(source || '');
  if (s === 'manual_handoff') return 'Manual handoff';
  if (s.startsWith('priority_')) {
    const level = s.slice('priority_'.length);
    return `Priority escalation (${level.toUpperCase()})`;
  }
  if (s === 'auto_team_legal') return 'Auto-routed (Legal)';
  if (s.startsWith('auto_high_priority')) return 'Auto high-priority';
  if (s.startsWith('auto_signal_')) {
    // Inside-the-parens detail (set by main._surface_ticket_in_chatwoot)
    // already reads as "(financial-dispute signal: ...)" — surface verbatim.
    const match = s.match(/^auto_signal_[^(]+(?:\((.*)\))?$/);
    if (match && match[1]) return `AI escalation (${match[1]})`;
    return 'AI escalation';
  }
  return s || 'Manual';
};

const ticketLabel = t => {
  if (!t) return '';
  if (t.number) return `#${t.number}`;
  if (t.id) return String(t.id);
  return '';
};

// Status dot color. Defaults to "open" colour when status is missing — the
// bridge writes `status: "Open"` for freshly-created tickets and doesn't
// (yet) refresh from Zoho, so the dot is best-read as "we created this; ask
// Zoho for the live state via the Open in Zoho link."
const statusColorClass = status => {
  const s = String(status || '').toLowerCase();
  if (s === 'closed' || s === 'resolved') return 'bg-n-slate-9';
  if (s === 'on hold' || s === 'on_hold' || s === 'waiting')
    return 'bg-amber-500';
  return 'bg-emerald-500'; // open / unknown
};

// Format the bridge-written ISO `created_at` (e.g. "2026-06-26T12:39:00+05:30")
// into a readable stamp. Returns '' for missing/unparseable values so the row
// just omits the date rather than showing "Invalid Date".
const createdAtLabel = ticket => {
  if (!ticket?.created_at) return '';
  const date = new Date(ticket.created_at);
  if (Number.isNaN(date.getTime())) return '';
  return format(date, 'MMM d, yyyy · h:mm a');
};

// Newest first. The bridge already prepends new tickets, but legacy/backfilled
// conversations may not be ordered — sort defensively by created_at desc.
// Entries without a valid date sink to the bottom.
const sortedTickets = computed(() => {
  const toMs = t => {
    const ms = t?.created_at ? new Date(t.created_at).getTime() : NaN;
    return Number.isNaN(ms) ? -Infinity : ms;
  };
  return [...(props.tickets || [])].sort((a, b) => toMs(b) - toMs(a));
});

const headerCount = computed(() => props.tickets?.length || 0);
</script>

<template>
  <div v-if="tickets && tickets.length" class="flex flex-col gap-2">
    <div class="px-1 text-xs font-medium text-n-slate-11">
      {{ headerCount }} ticket{{ headerCount === 1 ? '' : 's' }}
    </div>
    <div
      v-for="(ticket, idx) in sortedTickets"
      :key="ticket.id || ticket.number || idx"
      class="flex flex-col gap-2 p-3 rounded-md bg-n-alpha-1"
    >
      <div class="flex items-center justify-between gap-2">
        <div class="flex items-center gap-2 min-w-0">
          <span
            class="w-2 h-2 rounded-full shrink-0"
            :class="statusColorClass(ticket.status)"
            :title="ticket.status || 'open'"
          />
          <span class="text-sm font-medium text-n-slate-12 truncate">
            {{ ticketLabel(ticket) || 'Zoho Desk' }}
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
      <div
        v-if="ticket.subject"
        class="text-xs text-n-slate-11 line-clamp-2"
        :title="ticket.subject"
      >
        {{ ticket.subject }}
      </div>
      <div
        class="flex items-center justify-between gap-2 text-xs text-n-slate-10"
      >
        <span>{{ sourceLabel(ticket.source) }}</span>
        <span v-if="createdAtLabel(ticket)" class="shrink-0">
          {{ createdAtLabel(ticket) }}
        </span>
      </div>
    </div>
  </div>
  <div v-else class="px-3 pb-2 text-xs text-n-slate-10">
    No Zoho ticket associated with this conversation yet.
  </div>
</template>
