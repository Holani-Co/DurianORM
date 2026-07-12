<script setup>
// Durian — manual (agent-initiated) Zoho Desk ticket creation.
//
// Shown in the "Zoho Desk" sidebar section when a conversation has no ticket
// yet. Clicking "Create ticket" opens a dialog whose title/description/priority
// are pre-filled by the AI (draft_ticket), which the agent edits before saving.
// Creation goes through the SAME bridge pipeline as an AI-suggested ticket
// (dedup check included) — so if the contact already has related open tickets
// the bridge pauses and the Ticket-decision panel appears instead.
import { ref } from 'vue';
import { useMapGetter } from 'dashboard/composables/store';
import { useAlert } from 'dashboard/composables';
import { useI18n } from 'vue-i18n';
import Dialog from 'dashboard/components-next/dialog/Dialog.vue';

const props = defineProps({
  conversationId: { type: [Number, String], required: true },
});

const axios = window.axios;
const { t } = useI18n();
const accountId = useMapGetter('getCurrentAccountId');

const dialog = ref(null);
const loadingDraft = ref(false);
const creating = ref(false);
const subject = ref('');
const description = ref('');
const priority = ref('medium');

const PRIORITIES = ['low', 'medium', 'high', 'urgent'];

const proxy = action =>
  `/api/v1/accounts/${accountId.value}/integrations/zoho_bridge/${action}`;

const open = async () => {
  subject.value = '';
  description.value = '';
  priority.value = 'medium';
  dialog.value?.open();
  loadingDraft.value = true;
  try {
    const { data } = await axios.post(proxy('draft_ticket'), {
      conversation_id: Number(props.conversationId),
    });
    subject.value = data?.subject || '';
    description.value = data?.description || '';
    priority.value = data?.priority || 'medium';
  } catch (e) {
    useAlert(t('CONVERSATION.MANUAL_TICKET.DRAFT_ERROR'));
  } finally {
    loadingDraft.value = false;
  }
};

const create = async () => {
  if (creating.value || !subject.value.trim()) return;
  creating.value = true;
  try {
    const { data } = await axios.post(proxy('create_ticket'), {
      conversation_id: Number(props.conversationId),
      subject: subject.value.trim(),
      description: description.value.trim(),
      priority: priority.value,
    });
    useAlert(
      data?.paused
        ? t('CONVERSATION.MANUAL_TICKET.PAUSED')
        : t('CONVERSATION.MANUAL_TICKET.CREATED')
    );
    dialog.value?.close();
  } catch (e) {
    useAlert(
      e?.response?.data?.detail ||
        e?.response?.data?.error ||
        t('CONVERSATION.MANUAL_TICKET.CREATE_ERROR')
    );
  } finally {
    creating.value = false;
  }
};
</script>

<template>
  <div class="px-3 pb-2">
    <button
      type="button"
      class="inline-flex items-center gap-1.5 px-2.5 py-1 text-xs font-medium rounded-md bg-n-solid-3 text-n-slate-12 hover:bg-n-solid-2"
      @click="open"
    >
      <span class="i-ph-plus-circle align-middle" />
      {{ t('CONVERSATION.MANUAL_TICKET.BUTTON') }}
    </button>

    <Dialog
      ref="dialog"
      type="edit"
      :title="t('CONVERSATION.MANUAL_TICKET.TITLE')"
      :description="t('CONVERSATION.MANUAL_TICKET.SUBTITLE')"
      :confirm-button-label="t('CONVERSATION.MANUAL_TICKET.CREATE')"
      :is-loading="creating"
      :disable-confirm-button="loadingDraft || !subject.trim()"
      @confirm="create"
    >
      <div class="flex flex-col gap-3">
        <p v-if="loadingDraft" class="text-xs text-n-slate-11">
          <span class="i-ph-sparkle-fill align-middle text-n-iris-11" />
          {{ t('CONVERSATION.MANUAL_TICKET.DRAFTING') }}
        </p>

        <label class="flex flex-col gap-1">
          <span class="text-xs font-medium text-n-slate-11">
            {{ t('CONVERSATION.MANUAL_TICKET.SUBJECT') }}
          </span>
          <input
            v-model="subject"
            type="text"
            :disabled="loadingDraft"
            class="w-full px-2 py-1.5 text-sm border rounded-md bg-n-background text-n-slate-12 border-n-weak focus:outline-none focus:border-n-brand"
          />
        </label>

        <label class="flex flex-col gap-1">
          <span class="text-xs font-medium text-n-slate-11">
            {{ t('CONVERSATION.MANUAL_TICKET.DESCRIPTION') }}
          </span>
          <textarea
            v-model="description"
            rows="6"
            :disabled="loadingDraft"
            class="w-full px-2 py-1.5 text-sm border rounded-md resize-y bg-n-background text-n-slate-12 border-n-weak focus:outline-none focus:border-n-brand"
          />
        </label>

        <label class="flex flex-col gap-1">
          <span class="text-xs font-medium text-n-slate-11">
            {{ t('CONVERSATION.MANUAL_TICKET.PRIORITY') }}
          </span>
          <select
            v-model="priority"
            class="w-full px-2 py-1.5 text-sm border rounded-md bg-n-background text-n-slate-12 border-n-weak focus:outline-none focus:border-n-brand"
          >
            <option v-for="p in PRIORITIES" :key="p" :value="p">
              {{ t(`CONVERSATION.MANUAL_TICKET.PRIORITIES.${p}`) }}
            </option>
          </select>
        </label>
      </div>
    </Dialog>
  </div>
</template>
