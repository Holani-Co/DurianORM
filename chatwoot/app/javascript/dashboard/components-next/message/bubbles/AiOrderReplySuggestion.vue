<script setup>
// Durian — interactive order-lookup reply card.
//
// Rendered for private notes the zoho-bridge posts with
// content_attributes.type === 'ai_order_reply'. For an existing-order enquiry
// the bridge drafts the customer-facing reply (an ask-for-details template, or
// the fetched order details); this card lets the agent review / edit and send
// it to the customer in one click — nothing is sent automatically.
//
// - context     → read-only agent-only note (order snapshot / what was matched)
// - Edit        → inline textarea on the draft
// - Send        → posts the draft as an OUTGOING reply to the customer, then
//                 marks the card "sent by <agent>"
// - Cancel      → deletes this suggestion note
import { computed, ref } from 'vue';
import { useStore, useMapGetter } from 'dashboard/composables/store';
import { useAlert } from 'dashboard/composables';
import { useI18n } from 'vue-i18n';
import { useMessageContext } from '../provider.js';
import Dialog from 'dashboard/components-next/dialog/Dialog.vue';

const axios = window.axios;
const store = useStore();
const { t } = useI18n();
const accountId = useMapGetter('getCurrentAccountId');
const currentUser = useMapGetter('getCurrentUser');

const { id, content, contentAttributes, conversationId } = useMessageContext();

// Once sent, the card transforms in place into an "Approved & sent by <agent>"
// line — persisted on the note's content_attributes so it survives reload and
// shows for other agents too.
const localSent = ref(false);
const localSentBy = ref('');
const isSent = computed(
  () => localSent.value || contentAttributes.value?.sent || false
);
const sentByName = computed(
  () => contentAttributes.value?.sent_by || localSentBy.value || ''
);
const sentLine = computed(() =>
  sentByName.value
    ? t('CONVERSATION.AI_ORDER_REPLY.APPROVED_SENT_BY', { agent: sentByName.value })
    : t('CONVERSATION.AI_ORDER_REPLY.APPROVED_SENT')
);

// Agent-only context (order snapshot / match reason) — read-only, never sent.
const context = computed(() => contentAttributes.value?.context || '');
const initialText = computed(
  () => contentAttributes.value?.suggestion || content.value || ''
);

const draft = ref(initialText.value);
const isEditing = ref(false);
const isSending = ref(false);
const confirmSendDialog = ref(null);
const busy = computed(() => isSending.value);

const requestSend = () => {
  if (busy.value || !draft.value.trim()) return;
  confirmSendDialog.value?.open();
};

const send = async () => {
  if (busy.value || !draft.value.trim()) return;
  isSending.value = true;
  try {
    await store.dispatch('createPendingMessageAndSend', {
      conversationId: Number(conversationId.value),
      message: draft.value.trim(),
      private: false,
    });
    try {
      const { data } = await axios.post(
        `/api/v1/accounts/${accountId.value}/conversations/${Number(
          conversationId.value
        )}/messages/${id.value}/mark_suggestion_sent`
      );
      localSentBy.value =
        data?.content_attributes?.sent_by || currentUser.value?.name || '';
    } catch (e) {
      // Non-fatal: the reply was sent regardless; fall back to local attribution.
      localSentBy.value = currentUser.value?.name || '';
    }
    localSent.value = true;
    useAlert(t('CONVERSATION.AI_ORDER_REPLY.SENT'));
  } catch (e) {
    useAlert(t('CONVERSATION.AI_ORDER_REPLY.SEND_ERROR'));
  } finally {
    isSending.value = false;
    confirmSendDialog.value?.close();
  }
};

const cancel = async () => {
  if (busy.value) return;
  try {
    await store.dispatch('deleteMessage', {
      conversationId: Number(conversationId.value),
      messageId: id.value,
    });
  } catch (e) {
    useAlert(t('CONVERSATION.AI_ORDER_REPLY.CANCEL_ERROR'));
  }
};
</script>

<template>
  <!-- Sent → the card transforms into a compact "sent by <agent>" line. -->
  <div
    v-if="isSent"
    class="flex items-center gap-1.5 w-full max-w-2xl px-3 py-2 text-xs font-medium border rounded-xl bg-n-solid-3 border-n-weak text-n-slate-11"
  >
    <span class="i-ph-check-circle-fill text-n-teal-10" />
    {{ sentLine }}
  </div>

  <div
    v-else
    class="flex flex-col w-full max-w-2xl gap-2 p-3 border rounded-xl bg-n-solid-iris border-n-strong"
  >
    <div class="flex items-center gap-1.5 text-xs font-medium text-n-slate-12">
      <span class="i-ph-package-fill text-n-iris-11" />
      {{ t('CONVERSATION.AI_ORDER_REPLY.TITLE') }}
      <span class="font-normal text-n-slate-10">
        {{ t('CONVERSATION.AI_ORDER_REPLY.SUBTITLE') }}
      </span>
    </div>

    <!-- Agent-only context (order snapshot / match reason) — never sent. -->
    <div
      v-if="context"
      class="p-2 text-xs whitespace-pre-wrap border rounded-md text-n-slate-11 bg-n-solid-2 border-n-weak"
    >
      {{ context }}
    </div>

    <textarea
      v-if="isEditing"
      v-model="draft"
      rows="8"
      class="w-full p-2 text-sm border rounded-md resize-y bg-n-background text-n-slate-12 border-n-weak focus:outline-none focus:border-n-brand"
    />
    <div v-else class="text-sm whitespace-pre-wrap text-n-slate-12">
      {{ draft }}
    </div>

    <div class="flex flex-wrap items-center gap-2 pt-1">
      <button
        type="button"
        class="px-2 py-1 text-xs font-medium rounded-md bg-n-solid-3 text-n-slate-12 hover:bg-n-solid-2 disabled:opacity-50"
        :disabled="busy"
        @click="isEditing = !isEditing"
      >
        <span class="i-ph-pencil-simple align-middle" />
        {{
          isEditing
            ? t('CONVERSATION.AI_ORDER_REPLY.DONE_EDITING')
            : t('CONVERSATION.AI_ORDER_REPLY.EDIT')
        }}
      </button>
      <button
        type="button"
        class="px-3 py-1 text-xs font-medium text-white rounded-md bg-n-brand hover:opacity-90 disabled:opacity-50"
        :disabled="busy || !draft.trim()"
        @click="requestSend"
      >
        <span class="i-ph-paper-plane-tilt-fill align-middle" />
        {{
          isSending
            ? t('CONVERSATION.AI_ORDER_REPLY.SENDING')
            : t('CONVERSATION.AI_ORDER_REPLY.SEND')
        }}
      </button>
      <button
        type="button"
        class="px-2 py-1 text-xs font-medium rounded-md text-n-slate-11 hover:text-n-slate-12 disabled:opacity-50"
        :disabled="busy"
        @click="cancel"
      >
        {{ t('CONVERSATION.AI_ORDER_REPLY.CANCEL') }}
      </button>
    </div>

    <!-- Confirmation before the reply is sent to the customer. -->
    <Dialog
      ref="confirmSendDialog"
      type="alert"
      :title="t('CONVERSATION.AI_ORDER_REPLY.CONFIRM_TITLE')"
      :description="t('CONVERSATION.AI_ORDER_REPLY.CONFIRM_BODY')"
      :confirm-button-label="t('CONVERSATION.AI_ORDER_REPLY.CONFIRM_BUTTON')"
      @confirm="send"
    />
  </div>
</template>
