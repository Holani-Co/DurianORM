<script setup>
// Durian — interactive AI reply card for Google reviews.
//
// Rendered for private notes the zoho-bridge posts with
// content_attributes.type === 'ai_review_suggestion'. The bridge picks the
// best Durian reply template (canned response) and personalises it; this card
// lets the agent edit / regenerate / send / cancel without leaving the thread.
//
// - Edit       → inline textarea
// - Regenerate → POST to the Rails proxy → bridge re-drafts a fresh template
// - Send       → posts as an OUTGOING reply; the bridge webhook then publishes
//                it to Google, and we delete this suggestion note
// - Cancel     → deletes this suggestion note
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

// Once approved, the card transforms into an "Approved & sent by <agent>" line
// in place (persisted on the note's content_attributes) — no soft-delete, so
// no "message deleted" tombstone. Initialised from the persisted flag so the
// sent state survives reload.
// `localSent` flips this card to the sent state immediately for the acting
// agent; `contentAttributes.sent` is the persisted flag (survives reload and
// shows for other agents). Either makes the card render the "sent by" line.
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
    ? t('CONVERSATION.AI_REVIEW_SUGGESTION.APPROVED_SENT_BY', {
        agent: sentByName.value,
      })
    : t('CONVERSATION.AI_REVIEW_SUGGESTION.APPROVED_SENT')
);

// channel comes from the bridge (review / whatsapp / instagram / facebook).
// Default to "review" so older review cards posted before this field existed
// still render with the original "Send to Google" label.
const channel = computed(() => contentAttributes.value?.channel || 'review');

const initialText = computed(
  () => contentAttributes.value?.suggestion || content.value || ''
);

const draft = ref(initialText.value);
const isEditing = ref(false);
const isRegenerating = ref(false);
const isSending = ref(false);
const confirmSendDialog = ref(null);

const busy = computed(() => isRegenerating.value || isSending.value);

// Confirmation dialog text — channel-aware (Google review vs other channels).
const isReview = computed(() => channel.value === 'review');
const confirmTitle = computed(() =>
  isReview.value
    ? t('CONVERSATION.AI_REVIEW_SUGGESTION.CONFIRM_TITLE')
    : t('CONVERSATION.AI_REVIEW_SUGGESTION.CONFIRM_TITLE_GENERIC')
);
const confirmBody = computed(() =>
  isReview.value
    ? t('CONVERSATION.AI_REVIEW_SUGGESTION.CONFIRM_BODY')
    : t('CONVERSATION.AI_REVIEW_SUGGESTION.CONFIRM_BODY_GENERIC')
);
const confirmButton = computed(() =>
  isReview.value
    ? t('CONVERSATION.AI_REVIEW_SUGGESTION.CONFIRM_BUTTON')
    : t('CONVERSATION.AI_REVIEW_SUGGESTION.CONFIRM_BUTTON_GENERIC')
);

// Send button opens the confirmation dialog instead of sending directly.
const requestSend = () => {
  if (busy.value || !draft.value.trim()) return;
  confirmSendDialog.value?.open();
};

const sendLabel = computed(() =>
  channel.value === 'review'
    ? t('CONVERSATION.AI_REVIEW_SUGGESTION.SEND')
    : t('CONVERSATION.AI_REVIEW_SUGGESTION.SEND_REPLY')
);

const sentToast = computed(() =>
  channel.value === 'review'
    ? t('CONVERSATION.AI_REVIEW_SUGGESTION.SENT')
    : t('CONVERSATION.AI_REVIEW_SUGGESTION.SENT_GENERIC')
);

const regenerate = async () => {
  if (busy.value) return;
  isRegenerating.value = true;
  try {
    const { data } = await axios.post(
      `/api/v1/accounts/${accountId.value}/integrations/zoho_bridge/regenerate_review_reply`,
      {
        conversation_id: Number(conversationId.value),
        channel: channel.value,
      }
    );
    if (data?.suggestion) {
      draft.value = data.suggestion;
      isEditing.value = false;
    } else {
      useAlert(t('CONVERSATION.AI_REVIEW_SUGGESTION.NO_SUGGESTION'));
    }
  } catch (e) {
    useAlert(
      e?.response?.data?.error ||
        e?.response?.data?.detail ||
        t('CONVERSATION.AI_REVIEW_SUGGESTION.REGENERATE_ERROR')
    );
  } finally {
    isRegenerating.value = false;
  }
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
    // Transform the card in place into "Approved & sent by <agent>" instead of
    // soft-deleting it (which left a "message deleted" tombstone).
    try {
      const { data } = await axios.post(
        `/api/v1/accounts/${accountId.value}/conversations/${Number(
          conversationId.value
        )}/messages/${id.value}/mark_suggestion_sent`
      );
      localSentBy.value = data?.content_attributes?.sent_by || currentUser.value?.name || '';
    } catch (e) {
      // Non-fatal: the reply was sent regardless; fall back to local attribution.
      localSentBy.value = currentUser.value?.name || '';
    }
    localSent.value = true;
    useAlert(sentToast.value);
  } catch (e) {
    useAlert(t('CONVERSATION.AI_REVIEW_SUGGESTION.SEND_ERROR'));
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
    useAlert(t('CONVERSATION.AI_REVIEW_SUGGESTION.CANCEL_ERROR'));
  }
};
</script>

<template>
  <!-- Approved → the card transforms into a compact "sent by <agent>" line. -->
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
      <span class="i-ph-sparkle-fill text-n-iris-11" />
      {{ t('CONVERSATION.AI_REVIEW_SUGGESTION.TITLE') }}
      <span class="font-normal text-n-slate-10">
        {{ t('CONVERSATION.AI_REVIEW_SUGGESTION.SUBTITLE') }}
      </span>
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
            ? t('CONVERSATION.AI_REVIEW_SUGGESTION.DONE_EDITING')
            : t('CONVERSATION.AI_REVIEW_SUGGESTION.EDIT')
        }}
      </button>
      <button
        type="button"
        class="px-2 py-1 text-xs font-medium rounded-md bg-n-solid-3 text-n-slate-12 hover:bg-n-solid-2 disabled:opacity-50"
        :disabled="busy"
        @click="regenerate"
      >
        <span class="i-ph-arrows-clockwise align-middle" />
        {{
          isRegenerating
            ? t('CONVERSATION.AI_REVIEW_SUGGESTION.REGENERATING')
            : t('CONVERSATION.AI_REVIEW_SUGGESTION.REGENERATE')
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
          isSending ? t('CONVERSATION.AI_REVIEW_SUGGESTION.SENDING') : sendLabel
        }}
      </button>
      <button
        type="button"
        class="px-2 py-1 text-xs font-medium rounded-md text-n-slate-11 hover:text-n-slate-12 disabled:opacity-50"
        :disabled="busy"
        @click="cancel"
      >
        {{ t('CONVERSATION.AI_REVIEW_SUGGESTION.CANCEL') }}
      </button>
    </div>

    <!-- Confirmation before the reply is published (Google review / channel). -->
    <Dialog
      ref="confirmSendDialog"
      type="alert"
      :title="confirmTitle"
      :description="confirmBody"
      :confirm-button-label="confirmButton"
      @confirm="send"
    />
  </div>
</template>
