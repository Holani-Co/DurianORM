<script setup>
import { computed, reactive } from 'vue';
import { useI18n } from 'vue-i18n';
import { useAlert } from 'dashboard/composables';
import { useMapGetter, useStore } from 'dashboard/composables/store';

import Input from 'dashboard/components-next/input/Input.vue';
import ComboBox from 'dashboard/components-next/combobox/ComboBox.vue';
import Button from 'dashboard/components-next/button/Button.vue';

const emit = defineEmits(['close']);
const { t } = useI18n();
const store = useStore();
const inboxes = useMapGetter('inboxes/getWhatsAppInboxes');
const uiFlags = useMapGetter('whatsappConsents/getUIFlags');
const state = reactive({ inboxId: null, phoneNumber: '', status: 'OPTED_IN' });

const inboxOptions = computed(() =>
  inboxes.value
    .filter(inbox => inbox.provider === 'whatsapp_cloud')
    .map(inbox => ({ value: inbox.id, label: inbox.name }))
);
const statusOptions = computed(() => [
  { value: 'OPTED_IN', label: t('CAMPAIGN.WHATSAPP.CONSENTS.OPTED_IN') },
  { value: 'OPTED_OUT', label: t('CAMPAIGN.WHATSAPP.CONSENTS.OPTED_OUT') },
]);
const canSubmit = computed(
  () => state.inboxId && /^\+[1-9]\d{1,14}$/.test(state.phoneNumber)
);

const submit = async () => {
  if (!canSubmit.value) return;
  try {
    await store.dispatch('whatsappConsents/create', {
      inbox_id: state.inboxId,
      phone_number: state.phoneNumber,
      status: state.status,
      details: {},
    });
    useAlert(t('CAMPAIGN.WHATSAPP.CONSENTS.API.CREATED'));
    emit('close');
  } catch (error) {
    useAlert(
      error?.response?.data?.message ||
        error?.response?.data?.error ||
        t('CAMPAIGN.WHATSAPP.CONSENTS.API.ERROR')
    );
  }
};
</script>

<template>
  <div
    class="absolute top-10 z-50 w-[25rem] rounded-xl border border-n-weak bg-n-alpha-3 p-6 shadow-md backdrop-blur-[100px] ltr:right-0 rtl:left-0"
  >
    <form class="flex flex-col gap-4" @submit.prevent="submit">
      <div>
        <h3 class="text-base font-medium text-n-slate-12">
          {{ t('CAMPAIGN.WHATSAPP.CONSENTS.RECORD_TITLE') }}
        </h3>
        <p class="mt-1 text-xs text-n-slate-11">
          {{ t('CAMPAIGN.WHATSAPP.CONSENTS.RECORD_SUBTITLE') }}
        </p>
      </div>
      <div class="flex flex-col gap-1">
        <label class="text-heading-3 text-n-slate-12">
          {{ t('CAMPAIGN.WHATSAPP.CONSENTS.INBOX') }}
        </label>
        <ComboBox
          v-model="state.inboxId"
          :options="inboxOptions"
          :placeholder="t('CAMPAIGN.WHATSAPP.CONSENTS.INBOX_PLACEHOLDER')"
        />
      </div>
      <Input
        v-model="state.phoneNumber"
        :label="t('CAMPAIGN.WHATSAPP.CONSENTS.PHONE')"
        :placeholder="t('CAMPAIGN.WHATSAPP.CONSENTS.PHONE_PLACEHOLDER')"
      />
      <div class="flex flex-col gap-1">
        <label class="text-heading-3 text-n-slate-12">
          {{ t('CAMPAIGN.WHATSAPP.CONSENTS.STATUS') }}
        </label>
        <ComboBox v-model="state.status" :options="statusOptions" />
      </div>
      <div class="flex justify-end gap-3 pt-2">
        <Button
          type="button"
          color="slate"
          variant="faded"
          :label="t('CAMPAIGN.WHATSAPP.CONSENTS.CANCEL')"
          @click="emit('close')"
        />
        <Button
          type="submit"
          :disabled="!canSubmit"
          :is-loading="uiFlags.isCreating"
          :label="t('CAMPAIGN.WHATSAPP.CONSENTS.SAVE')"
        />
      </div>
    </form>
  </div>
</template>
