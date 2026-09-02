<script setup>
import { computed, onMounted, reactive, ref } from 'vue';
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
const labels = useMapGetter('labels/getLabels');
const uiFlags = useMapGetter('whatsappConsents/getUIFlags');

const mode = ref('single'); // 'single' | 'csv'
const state = reactive({ inboxId: null, phoneNumber: '', status: 'OPTED_IN' });
const csv = reactive({ inboxId: null, label: null, file: null });

onMounted(() => store.dispatch('labels/get'));

const inboxOptions = computed(() =>
  inboxes.value
    .filter(inbox => inbox.provider === 'whatsapp_cloud')
    .map(inbox => ({ value: inbox.id, label: inbox.name }))
);
const statusOptions = computed(() => [
  { value: 'OPTED_IN', label: t('CAMPAIGN.WHATSAPP.CONSENTS.OPTED_IN') },
  { value: 'OPTED_OUT', label: t('CAMPAIGN.WHATSAPP.CONSENTS.OPTED_OUT') },
]);
const labelOptions = computed(() =>
  labels.value.map(label => ({ value: label.title, label: label.title }))
);

const canSubmit = computed(
  () => state.inboxId && /^\+[1-9]\d{1,14}$/.test(state.phoneNumber)
);
const canImport = computed(() => csv.inboxId && csv.label && csv.file);

const handleFileChange = event => {
  csv.file = event.target.files?.[0] || null;
};

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

const submitImport = async () => {
  if (!canImport.value) return;
  try {
    await store.dispatch('whatsappConsents/import', {
      file: csv.file,
      inboxId: csv.inboxId,
      label: csv.label,
    });
    useAlert(t('CAMPAIGN.WHATSAPP.CONSENTS.IMPORT.QUEUED'));
    emit('close');
  } catch (error) {
    useAlert(
      error?.response?.data?.error ||
        error?.response?.data?.message ||
        t('CAMPAIGN.WHATSAPP.CONSENTS.API.ERROR')
    );
  }
};
</script>

<template>
  <div
    class="absolute top-10 z-50 w-[25rem] rounded-xl border border-n-weak bg-n-alpha-3 p-6 shadow-md backdrop-blur-[100px] ltr:right-0 rtl:left-0"
  >
    <div class="mb-4 flex gap-2">
      <Button
        type="button"
        size="sm"
        :color="mode === 'single' ? 'blue' : 'slate'"
        :variant="mode === 'single' ? 'solid' : 'faded'"
        :label="t('CAMPAIGN.WHATSAPP.CONSENTS.IMPORT.MODE_SINGLE')"
        @click="mode = 'single'"
      />
      <Button
        type="button"
        size="sm"
        :color="mode === 'csv' ? 'blue' : 'slate'"
        :variant="mode === 'csv' ? 'solid' : 'faded'"
        :label="t('CAMPAIGN.WHATSAPP.CONSENTS.IMPORT.MODE_CSV')"
        @click="mode = 'csv'"
      />
    </div>

    <form
      v-if="mode === 'single'"
      class="flex flex-col gap-4"
      @submit.prevent="submit"
    >
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

    <form v-else class="flex flex-col gap-4" @submit.prevent="submitImport">
      <div>
        <h3 class="text-base font-medium text-n-slate-12">
          {{ t('CAMPAIGN.WHATSAPP.CONSENTS.IMPORT.CSV_TITLE') }}
        </h3>
        <p class="mt-1 text-xs text-n-slate-11">
          {{ t('CAMPAIGN.WHATSAPP.CONSENTS.IMPORT.CSV_SUBTITLE') }}
        </p>
      </div>
      <div class="flex flex-col gap-1">
        <label class="text-heading-3 text-n-slate-12">
          {{ t('CAMPAIGN.WHATSAPP.CONSENTS.INBOX') }}
        </label>
        <ComboBox
          v-model="csv.inboxId"
          :options="inboxOptions"
          :placeholder="t('CAMPAIGN.WHATSAPP.CONSENTS.INBOX_PLACEHOLDER')"
        />
      </div>
      <div class="flex flex-col gap-1">
        <label class="text-heading-3 text-n-slate-12">
          {{ t('CAMPAIGN.WHATSAPP.CONSENTS.IMPORT.LABEL') }}
        </label>
        <ComboBox
          v-model="csv.label"
          :options="labelOptions"
          :placeholder="
            t('CAMPAIGN.WHATSAPP.CONSENTS.IMPORT.LABEL_PLACEHOLDER')
          "
        />
      </div>
      <div class="flex flex-col gap-1">
        <label class="text-heading-3 text-n-slate-12">
          {{ t('CAMPAIGN.WHATSAPP.CONSENTS.IMPORT.FILE') }}
        </label>
        <input
          type="file"
          accept="text/csv"
          class="text-sm text-n-slate-11 file:mr-3 file:rounded-md file:border-0 file:bg-n-alpha-2 file:px-3 file:py-1.5 file:text-n-slate-12"
          @change="handleFileChange"
        />
        <p class="mt-1 text-xs text-n-slate-11">
          {{ t('CAMPAIGN.WHATSAPP.CONSENTS.IMPORT.FILE_HINT') }}
        </p>
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
          :disabled="!canImport"
          :is-loading="uiFlags.isImporting"
          :label="t('CAMPAIGN.WHATSAPP.CONSENTS.IMPORT.SUBMIT')"
        />
      </div>
    </form>
  </div>
</template>
