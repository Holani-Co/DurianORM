<script setup>
import { computed, ref } from 'vue';
import { useStore, useMapGetter } from 'dashboard/composables/store';
import { useAlert } from 'dashboard/composables';
import axios from 'axios';

// Renders when zoho-bridge has PAUSED Zoho ticket creation because the
// contact already has one or more open tickets. Lets the agent decide
// whether to attach this conversation to an existing ticket (Zoho
// comment) or create a fresh one anyway. State is read from
// `custom_attributes.pending_zoho_ticket` (written by the bridge); on
// click we POST to the Rails proxy which forwards to the bridge.
//
// Shape of `pending` from main._pause_for_agent_decision:
//   {
//     sender_email, escalation_label, suggested_at,
//     candidates: [{ id, number, subject, status, url, created_at }, ...]
//   }
const props = defineProps({
  pending: {
    type: Object,
    default: () => ({}),
  },
  conversationId: {
    type: [Number, String],
    required: true,
  },
});

const emit = defineEmits(['resolved']);
const store = useStore();
const accountId = useMapGetter('getCurrentAccountId');

const submittingChoice = ref(null); // null | 'create_new' | <ticket_id>

const candidates = computed(() => props.pending?.candidates || []);
const escalationLabel = computed(() => props.pending?.escalation_label || '');
const senderEmail = computed(() => props.pending?.sender_email || '');

const formatLabel = label => {
  if (!label) return '';
  const map = {
    legal_or_compliance: 'Legal / compliance',
    hr_sensitive: 'HR-sensitive',
    financial_dispute: 'Financial dispute',
    brand_or_contract: 'Brand / contract',
  };
  return map[label] || label.replace(/_/g, ' ');
};

const formatStatus = status => (status === 'On Hold' ? 'On Hold' : 'Open');

async function resolve(choice, targetTicketId = null) {
  if (submittingChoice.value) return;
  submittingChoice.value = choice === 'use_existing' ? targetTicketId : 'create_new';
  try {
    await axios.post(
      `/api/v1/accounts/${accountId.value}/integrations/zoho_bridge/resolve_ticket_decision`,
      {
        conversation_id: Number(props.conversationId),
        choice,
        target_ticket_id: targetTicketId,
      }
    );
    // The bridge has already cleared pending_zoho_ticket server-side; mirror
    // that locally so Vuex updates and this panel disappears without waiting
    // for a websocket push. `updateCustomAttributes` is idempotent — a second
    // write of the same null is a no-op on the backend.
    await store.dispatch('updateCustomAttributes', {
      conversationId: Number(props.conversationId),
      customAttributes: { pending_zoho_ticket: null },
    });
    useAlert(
      choice === 'create_new'
        ? 'New Zoho ticket created.'
        : 'Conversation attached to existing ticket.'
    );
    emit('resolved', { choice, target_ticket_id: targetTicketId });
  } catch (e) {
    useAlert(
      e?.response?.data?.error ||
        e?.response?.data?.detail ||
        'Could not save ticket decision. Please try again.'
    );
  } finally {
    submittingChoice.value = null;
  }
}
</script>

<template>
  <div class="flex flex-col gap-2">
    <div class="px-1 text-xs text-n-slate-11">
      Bridge paused ticket creation —
      <span v-if="escalationLabel" class="text-n-slate-12">
        {{ formatLabel(escalationLabel) }}
      </span>
      <span v-if="senderEmail" class="text-n-slate-10">
        · {{ senderEmail }}
      </span>
    </div>

    <div
      v-for="ticket in candidates"
      :key="ticket.id"
      class="flex flex-col gap-1.5 p-3 rounded-md bg-n-alpha-1"
    >
      <!-- Header: ticket number + status -->
      <div class="flex items-center justify-between gap-2">
        <a
          v-if="ticket.url"
          :href="ticket.url"
          target="_blank"
          rel="noopener noreferrer"
          class="text-sm font-medium text-n-brand hover:underline truncate"
        >
          #{{ ticket.number || ticket.id }}
        </a>
        <span v-else class="text-sm font-medium text-n-slate-12 truncate">
          #{{ ticket.id }}
        </span>
        <span class="text-xs text-amber-600 dark:text-amber-400 shrink-0">
          {{ formatStatus(ticket.status) }}
        </span>
      </div>

      <!-- Subject -->
      <div v-if="ticket.subject" class="text-xs text-n-slate-11 line-clamp-2">
        {{ ticket.subject }}
      </div>

      <!-- Attach button -->
      <button
        type="button"
        class="self-start mt-1 text-xs font-medium text-n-brand hover:underline disabled:opacity-50"
        :disabled="submittingChoice !== null"
        @click="resolve('use_existing', ticket.id)"
      >
        <span class="i-ph-arrows-merge align-middle" />
        {{
          submittingChoice === ticket.id ? 'Attaching…' : 'Attach this conversation'
        }}
      </button>
    </div>

    <!-- Always-available fallback: create a new ticket anyway. -->
    <div class="flex items-center justify-between gap-2 px-1 pt-1">
      <span class="text-xs text-n-slate-10">
        Not a duplicate?
      </span>
      <button
        type="button"
        class="text-xs font-medium text-n-slate-11 hover:text-n-slate-12 disabled:opacity-50"
        :disabled="submittingChoice !== null"
        @click="resolve('create_new')"
      >
        {{ submittingChoice === 'create_new' ? 'Creating…' : 'Create new ticket' }}
      </button>
    </div>
  </div>
</template>
