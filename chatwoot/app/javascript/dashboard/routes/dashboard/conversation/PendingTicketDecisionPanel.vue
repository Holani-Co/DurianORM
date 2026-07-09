<script setup>
// Chatwoot attaches the configured axios instance (with devise-token-auth
// headers attached on login) to window.axios in entrypoints/dashboard.js.
// Importing the raw 'axios' package would skip those headers — every
// request would land with 401 Unauthorized.
import { computed, ref } from 'vue';
import { format } from 'date-fns';
import { useStore, useMapGetter } from 'dashboard/composables/store';
import { useAlert } from 'dashboard/composables';

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
//     candidates: [{ id, number, subject, status, url, created_at, match? }, ...]
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

const axios = window.axios;
const store = useStore();
const accountId = useMapGetter('getCurrentAccountId');

const submittingChoice = ref(null); // null | 'create_new' | 'reject' | <ticket_id>

const candidates = computed(() => props.pending?.candidates || []);
const hasCandidates = computed(() => candidates.value.length > 0);
const escalationLabel = computed(() => props.pending?.escalation_label || '');
const senderEmail = computed(() => props.pending?.sender_email || '');
// attach_only: the customer named an existing ticket, so creating a brand-new
// one is never right — hide "Create new ticket" and offer attach/reject only.
const attachOnly = computed(() => props.pending?.attach_only === true);

const headerText = computed(() => {
  if (attachOnly.value)
    return 'Refers to an existing ticket — attach or reject';
  if (hasCandidates.value)
    return 'Possible duplicate — approve, attach, or reject';
  return 'Zoho ticket needs your approval';
});

const ALERTS = {
  create_new: 'Zoho ticket created.',
  use_existing: 'Conversation attached to existing ticket.',
  reject: 'Ticket creation rejected — no ticket was created.',
};

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

// Zoho `createdTime` (ISO) → readable stamp; '' when missing/unparseable.
const formatCreatedAt = ticket => {
  if (!ticket?.created_at) return '';
  const date = new Date(ticket.created_at);
  if (Number.isNaN(date.getTime())) return '';
  return format(date, 'MMM d, yyyy · h:mm a');
};

async function resolve(choice, targetTicketId = null) {
  if (submittingChoice.value) return;
  submittingChoice.value = targetTicketId ?? choice;
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
    // ONLY in the local store so the panel disappears immediately. Never POST
    // this to the API: Chatwoot's custom_attributes endpoint REPLACES the
    // whole hash, so a single-key write from here wiped the conversation's
    // other attributes — including the zoho_tickets sidebar history.
    store.commit('UPDATE_CONVERSATION_CUSTOM_ATTRIBUTES', {
      conversationId: Number(props.conversationId),
      customAttributes: { pending_zoho_ticket: null },
    });
    useAlert(ALERTS[choice] || 'Done.');
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
      <span class="text-n-slate-12">
        {{ headerText }}
      </span>
      <span v-if="escalationLabel" class="text-n-slate-10">
        · {{ formatLabel(escalationLabel) }}
      </span>
      <span v-if="senderEmail" class="text-n-slate-10">
        · {{ senderEmail }}
      </span>
    </div>

    <!-- Primary actions up top: Approve/Create new + Reject. In attach-only
         mode the customer named an existing ticket, so "create new" is
         suppressed — only Reject sits up here, and attach is below. -->
    <div class="flex items-center gap-2 px-1">
      <button
        v-if="!attachOnly"
        type="button"
        class="flex-1 px-3 py-1.5 text-xs font-medium text-white rounded-md bg-n-brand hover:opacity-90 disabled:opacity-50"
        :disabled="submittingChoice !== null"
        @click="resolve('create_new')"
      >
        <span class="i-ph-check-circle align-middle" />
        {{
          submittingChoice === 'create_new'
            ? 'Creating…'
            : hasCandidates
              ? 'Create new ticket'
              : 'Approve & create ticket'
        }}
      </button>
      <button
        type="button"
        class="px-3 py-1.5 text-xs font-medium rounded-md bg-n-solid-3 text-n-slate-11 hover:text-n-ruby-11 disabled:opacity-50"
        :disabled="submittingChoice !== null"
        @click="resolve('reject')"
      >
        {{ submittingChoice === 'reject' ? 'Rejecting…' : 'Reject' }}
      </button>
    </div>

    <div v-if="hasCandidates" class="px-1 pt-1 text-xs text-n-slate-10">
      {{
        attachOnly
          ? 'Attach to the referenced ticket:'
          : '…or attach to an existing ticket:'
      }}
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

      <!-- Why the bridge surfaced this candidate: "same contact",
           "#N referenced in message", or "similar content" (may be a
           cross-contact match — same person on a second email address). -->
      <div v-if="ticket.match" class="text-xs italic text-n-slate-10">
        {{ ticket.match }}
      </div>

      <!-- Subject -->
      <div v-if="ticket.subject" class="text-xs text-n-slate-11 line-clamp-2">
        {{ ticket.subject }}
      </div>

      <!-- Created date -->
      <div v-if="formatCreatedAt(ticket)" class="text-xs text-n-slate-10">
        Created {{ formatCreatedAt(ticket) }}
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
          submittingChoice === ticket.id
            ? 'Attaching…'
            : 'Attach this conversation'
        }}
      </button>
    </div>
  </div>
</template>
