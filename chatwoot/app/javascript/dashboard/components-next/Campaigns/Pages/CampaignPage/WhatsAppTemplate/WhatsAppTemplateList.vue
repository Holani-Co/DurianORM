<script setup>
import { computed, ref } from 'vue';
import { useI18n } from 'vue-i18n';
import { useAlert } from 'dashboard/composables';
import { useMapGetter, useStore } from 'dashboard/composables/store';

import Button from 'dashboard/components-next/button/Button.vue';
import Select from 'dashboard/components-next/select/Select.vue';
import Spinner from 'dashboard/components-next/spinner/Spinner.vue';

const emit = defineEmits(['edit']);

const { t } = useI18n();
const store = useStore();
const templates = useMapGetter('whatsappTemplates/getTemplates');
const uiFlags = useMapGetter('whatsappTemplates/getUIFlags');
const inboxes = useMapGetter('inboxes/getWhatsAppInboxes');
const selectedInboxId = ref('');

const cloudInboxes = computed(() =>
  inboxes.value.filter(inbox => inbox.provider === 'whatsapp_cloud')
);
const inboxOptions = computed(() => [
  { value: '', label: t('CAMPAIGN.WHATSAPP.TEMPLATES.ALL_INBOXES') },
  ...cloudInboxes.value.map(inbox => ({ value: inbox.id, label: inbox.name })),
]);
const visibleTemplates = computed(() => {
  if (!selectedInboxId.value) return templates.value;
  return templates.value.filter(
    template => template.inbox_id === Number(selectedInboxId.value)
  );
});

const statusClass = status => {
  if (status === 'APPROVED') return 'bg-n-teal-3 text-n-teal-11';
  if (status === 'REJECTED') return 'bg-n-ruby-3 text-n-ruby-11';
  if (status === 'DRAFT') return 'bg-n-slate-3 text-n-slate-11';
  return 'bg-n-amber-3 text-n-amber-11';
};

const alertError = error => {
  useAlert(
    error?.response?.data?.error || t('CAMPAIGN.WHATSAPP.TEMPLATES.API.ERROR')
  );
};

const syncTemplates = async () => {
  const inboxIds = selectedInboxId.value
    ? [Number(selectedInboxId.value)]
    : cloudInboxes.value.map(inbox => inbox.id);
  try {
    await Promise.all(
      inboxIds.map(inboxId => store.dispatch('whatsappTemplates/sync', inboxId))
    );
    useAlert(t('CAMPAIGN.WHATSAPP.TEMPLATES.API.SYNCED'));
  } catch (error) {
    alertError(error);
  }
};

const submitTemplate = async id => {
  try {
    await store.dispatch('whatsappTemplates/submit', id);
    useAlert(t('CAMPAIGN.WHATSAPP.TEMPLATES.API.SUBMITTED'));
  } catch (error) {
    alertError(error);
  }
};

const deleteTemplate = async template => {
  // eslint-disable-next-line no-alert
  if (!window.confirm(t('CAMPAIGN.WHATSAPP.TEMPLATES.DELETE_CONFIRM'))) return;
  try {
    await store.dispatch('whatsappTemplates/delete', template.id);
    useAlert(t('CAMPAIGN.WHATSAPP.TEMPLATES.API.DELETED'));
  } catch (error) {
    alertError(error);
  }
};
</script>

<template>
  <div class="flex flex-col gap-4">
    <div class="flex items-center justify-between gap-3">
      <Select v-model="selectedInboxId" :options="inboxOptions" />
      <Button
        icon="i-lucide-refresh-cw"
        color="slate"
        variant="faded"
        size="sm"
        :is-loading="uiFlags.isSyncing"
        :disabled="cloudInboxes.length === 0"
        :label="t('CAMPAIGN.WHATSAPP.TEMPLATES.SYNC')"
        @click="syncTemplates"
      />
    </div>

    <div
      v-if="uiFlags.isFetching"
      class="flex justify-center py-12 text-n-slate-11"
    >
      <Spinner />
    </div>

    <div
      v-else-if="visibleTemplates.length === 0"
      class="rounded-xl border border-dashed border-n-weak px-6 py-14 text-center"
    >
      <p class="text-base font-medium text-n-slate-12">
        {{ t('CAMPAIGN.WHATSAPP.TEMPLATES.EMPTY_TITLE') }}
      </p>
      <p class="mt-1 text-sm text-n-slate-11">
        {{ t('CAMPAIGN.WHATSAPP.TEMPLATES.EMPTY_SUBTITLE') }}
      </p>
    </div>

    <div v-else class="overflow-hidden rounded-xl border border-n-weak">
      <div
        v-for="template in visibleTemplates"
        :key="template.id"
        class="flex items-center gap-4 border-b border-n-weak px-4 py-3 last:border-b-0"
      >
        <div class="min-w-0 flex-1">
          <div class="flex items-center gap-2">
            <span class="truncate text-sm font-medium text-n-slate-12">
              {{ template.name }}
            </span>
            <span
              class="rounded-md px-2 py-0.5 text-xs font-medium"
              :class="statusClass(template.status)"
            >
              {{ template.status }}
            </span>
          </div>
          <p class="mt-1 truncate text-xs text-n-slate-11">
            {{
              t('CAMPAIGN.WHATSAPP.TEMPLATES.META', {
                inbox: template.inbox.name,
                language: template.language,
                category: template.category,
              })
            }}
          </p>
          <p
            v-if="template.rejection_reason"
            class="mt-1 text-xs text-n-ruby-10"
          >
            {{ template.rejection_reason }}
          </p>
        </div>
        <Button
          v-if="['DRAFT', 'REJECTED', 'APPROVED'].includes(template.status)"
          icon="i-lucide-pencil"
          size="xs"
          color="slate"
          variant="ghost"
          @click="emit('edit', template)"
        />
        <Button
          v-if="['DRAFT', 'REJECTED'].includes(template.status)"
          size="xs"
          variant="faded"
          :is-loading="uiFlags.isSubmitting"
          :label="t('CAMPAIGN.WHATSAPP.TEMPLATES.SUBMIT')"
          @click="submitTemplate(template.id)"
        />
        <Button
          icon="i-lucide-trash-2"
          size="xs"
          color="ruby"
          variant="ghost"
          :disabled="uiFlags.isDeleting"
          @click="deleteTemplate(template)"
        />
      </div>
    </div>
  </div>
</template>
