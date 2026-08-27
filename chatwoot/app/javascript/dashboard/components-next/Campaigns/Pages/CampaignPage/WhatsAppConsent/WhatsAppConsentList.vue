<script setup>
import { computed, ref } from 'vue';
import { useI18n } from 'vue-i18n';
import { useMapGetter } from 'dashboard/composables/store';

import Select from 'dashboard/components-next/select/Select.vue';
import Spinner from 'dashboard/components-next/spinner/Spinner.vue';

const { t } = useI18n();
const consents = useMapGetter('whatsappConsents/getConsents');
const uiFlags = useMapGetter('whatsappConsents/getUIFlags');
const inboxes = useMapGetter('inboxes/getWhatsAppInboxes');
const selectedInboxId = ref('');

const inboxOptions = computed(() => [
  { value: '', label: t('CAMPAIGN.WHATSAPP.CONSENTS.ALL_INBOXES') },
  ...inboxes.value
    .filter(inbox => inbox.provider === 'whatsapp_cloud')
    .map(inbox => ({ value: inbox.id, label: inbox.name })),
]);
const visibleConsents = computed(() => {
  if (!selectedInboxId.value) return consents.value;
  return consents.value.filter(
    consent => consent.inbox_id === Number(selectedInboxId.value)
  );
});

const statusLabel = status =>
  status === 'OPTED_IN'
    ? t('CAMPAIGN.WHATSAPP.CONSENTS.OPTED_IN')
    : t('CAMPAIGN.WHATSAPP.CONSENTS.OPTED_OUT');
</script>

<template>
  <div class="flex flex-col gap-4">
    <Select v-model="selectedInboxId" :options="inboxOptions" />
    <div
      v-if="uiFlags.isFetching"
      class="flex justify-center py-12 text-n-slate-11"
    >
      <Spinner />
    </div>
    <div
      v-else-if="visibleConsents.length === 0"
      class="rounded-xl border border-dashed border-n-weak px-6 py-14 text-center"
    >
      <p class="text-base font-medium text-n-slate-12">
        {{ t('CAMPAIGN.WHATSAPP.CONSENTS.EMPTY_TITLE') }}
      </p>
      <p class="mt-1 text-sm text-n-slate-11">
        {{ t('CAMPAIGN.WHATSAPP.CONSENTS.EMPTY_SUBTITLE') }}
      </p>
    </div>
    <div v-else class="overflow-hidden rounded-xl border border-n-weak">
      <div
        v-for="consent in visibleConsents"
        :key="consent.id"
        class="flex items-center gap-4 border-b border-n-weak px-4 py-3 last:border-b-0"
      >
        <div class="min-w-0 flex-1">
          <p class="truncate text-sm font-medium text-n-slate-12">
            {{ consent.contact.name || consent.contact.phone_number }}
          </p>
          <p class="mt-1 truncate text-xs text-n-slate-10">
            {{
              t('CAMPAIGN.WHATSAPP.CONSENTS.META', {
                phone: consent.contact.phone_number,
                inbox: consent.inbox.name,
                source: consent.source,
              })
            }}
          </p>
        </div>
        <span
          class="rounded-md px-2 py-0.5 text-xs font-medium"
          :class="
            consent.status === 'OPTED_IN'
              ? 'bg-n-teal-3 text-n-teal-11'
              : 'bg-n-ruby-3 text-n-ruby-11'
          "
        >
          {{ statusLabel(consent.status) }}
        </span>
      </div>
    </div>
  </div>
</template>
