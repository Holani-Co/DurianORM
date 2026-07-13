<script setup>
// Durian — "Escalate to team" for bad Google reviews. Shown in the sidebar
// only for low-star reviews (see ContactPanel gating). Opens a dialog where
// the agent enters recipient email(s), optionally includes the full
// conversation history, edits the message, and sends. The email is delivered
// by the bridge through the email inbox (the reviews inbox can't send mail);
// an audit note is posted back on the review.
import { computed, ref } from 'vue';
import { useMapGetter } from 'dashboard/composables/store';
import { useAlert } from 'dashboard/composables';
import { useI18n } from 'vue-i18n';
import Dialog from 'dashboard/components-next/dialog/Dialog.vue';

const props = defineProps({
  conversationId: { type: [Number, String], required: true },
  review: { type: Object, default: () => ({}) },
});

const axios = window.axios;
const { t } = useI18n();
const accountId = useMapGetter('getCurrentAccountId');

const dialog = ref(null);
const sending = ref(false);
const toEmails = ref('');
const ccEmails = ref('');
const includeHistory = ref(false);

const reviewer = computed(() => props.review?.reviewer || 'Customer');
const location = computed(() => props.review?.location || '');
const reviewText = computed(
  () => props.review?.review_comment || '(no text — rating only)'
);
const stars = computed(() => Number(props.review?.stars) || 0);

const subject = computed(
  () => `Negative Feedback Received on Google - ${reviewer.value}`
);

// Editable default body in the client's format. The agent adds their own
// signature; we don't force one.
const body = ref('');
const defaultBody = () =>
  [
    'Dear Team,',
    '',
    'We have received negative feedback on the Google website. Please contact the customer and resolve the problem. Below are the customer details and feedback for your reference.',
    '',
    `Customer Name - ${reviewer.value}`,
    `Customer Feedback - ${reviewText.value}`,
    location.value ? `Store - ${location.value}` : '',
    '',
    'Thanks & Regards,',
  ]
    .filter(line => line !== null)
    .join('\n');

const open = () => {
  body.value = defaultBody();
  toEmails.value = '';
  ccEmails.value = '';
  includeHistory.value = false;
  dialog.value?.open();
};

const send = async () => {
  if (sending.value) return;
  if (!toEmails.value.trim()) {
    useAlert(t('CONVERSATION.REVIEW_ESCALATION.NO_RECIPIENT'));
    return;
  }
  sending.value = true;
  try {
    await axios.post(
      `/api/v1/accounts/${accountId.value}/integrations/zoho_bridge/escalate_review`,
      {
        conversation_id: Number(props.conversationId),
        to_emails: toEmails.value.trim(),
        cc_emails: ccEmails.value.trim(),
        subject: subject.value,
        body: body.value,
        include_history: includeHistory.value,
      }
    );
    useAlert(t('CONVERSATION.REVIEW_ESCALATION.SENT'));
    dialog.value?.close();
  } catch (e) {
    useAlert(
      e?.response?.data?.detail ||
        e?.response?.data?.error ||
        t('CONVERSATION.REVIEW_ESCALATION.ERROR')
    );
  } finally {
    sending.value = false;
  }
};

const inputClass =
  'w-full px-2 py-1.5 text-sm rounded-md bg-n-background text-n-slate-12 border border-n-weak focus:outline-none focus:border-n-brand';
</script>

<template>
  <div class="flex flex-col gap-2 px-4 py-3 text-sm">
    <p class="text-xs text-n-slate-11">
      {{ t('CONVERSATION.REVIEW_ESCALATION.HINT', { stars }) }}
    </p>
    <button
      type="button"
      class="flex items-center justify-center gap-1.5 px-2.5 py-1.5 text-xs font-medium rounded-md text-white bg-n-brand hover:opacity-90"
      @click="open"
    >
      <span class="i-lucide-mail-warning align-middle" />
      {{ t('CONVERSATION.REVIEW_ESCALATION.BUTTON') }}
    </button>

    <Dialog
      ref="dialog"
      type="edit"
      width="2xl"
      :title="t('CONVERSATION.REVIEW_ESCALATION.TITLE')"
      :confirm-button-label="t('CONVERSATION.REVIEW_ESCALATION.SEND')"
      :is-loading="sending"
      @confirm="send"
    >
      <div class="flex flex-col gap-3">
        <label class="flex flex-col gap-1">
          <span class="text-xs font-medium text-n-slate-12">
            {{ t('CONVERSATION.REVIEW_ESCALATION.TO') }}
          </span>
          <input
            v-model="toEmails"
            type="text"
            :placeholder="t('CONVERSATION.REVIEW_ESCALATION.TO_PLACEHOLDER')"
            :class="inputClass"
          />
        </label>
        <label class="flex flex-col gap-1">
          <span class="text-xs font-medium text-n-slate-12">
            {{ t('CONVERSATION.REVIEW_ESCALATION.CC') }}
          </span>
          <input v-model="ccEmails" type="text" :class="inputClass" />
        </label>
        <label class="flex items-center gap-2 text-xs text-n-slate-12">
          <input v-model="includeHistory" type="checkbox" />
          {{ t('CONVERSATION.REVIEW_ESCALATION.INCLUDE_HISTORY') }}
        </label>
        <label class="flex flex-col gap-1">
          <span class="text-xs font-medium text-n-slate-12">
            {{ t('CONVERSATION.REVIEW_ESCALATION.MESSAGE') }}
            <span class="font-normal text-n-slate-10">· {{ subject }}</span>
          </span>
          <textarea
            v-model="body"
            rows="12"
            class="resize-y"
            :class="inputClass"
          />
        </label>
      </div>
    </Dialog>
  </div>
</template>
