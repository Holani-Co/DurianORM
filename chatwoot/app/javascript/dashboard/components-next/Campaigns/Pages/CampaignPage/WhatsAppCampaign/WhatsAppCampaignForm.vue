<script setup>
import { reactive, computed, watch, ref } from 'vue';
import { useI18n } from 'vue-i18n';
import { useVuelidate } from '@vuelidate/core';
import { required, minLength } from '@vuelidate/validators';
import { useAlert } from 'dashboard/composables';
import { useMapGetter, useStore } from 'dashboard/composables/store';

import Input from 'dashboard/components-next/input/Input.vue';
import Button from 'dashboard/components-next/button/Button.vue';
import ComboBox from 'dashboard/components-next/combobox/ComboBox.vue';
import TagMultiSelectComboBox from 'dashboard/components-next/combobox/TagMultiSelectComboBox.vue';
import WhatsAppTemplateParser from 'dashboard/components-next/whatsapp/WhatsAppTemplateParser.vue';

const emit = defineEmits(['submit', 'cancel']);

const { t } = useI18n();
const store = useStore();

const formState = {
  uiFlags: useMapGetter('campaigns/getUIFlags'),
  labels: useMapGetter('labels/getLabels'),
  inboxes: useMapGetter('inboxes/getWhatsAppInboxes'),
  whatsappTemplates: useMapGetter('whatsappTemplates/getTemplates'),
};

const initialState = {
  title: '',
  inboxId: null,
  templateId: null,
  scheduledAt: null,
  selectedAudience: [],
  testPhone: '',
};

const state = reactive({ ...initialState });
const templateParserRef = ref(null);
const audiencePreview = ref(null);
const isPreviewingAudience = ref(false);
const isSendingTest = ref(false);

const rules = {
  title: { required, minLength: minLength(1) },
  inboxId: { required },
  templateId: { required },
  scheduledAt: { required },
  selectedAudience: { required },
};

const v$ = useVuelidate(rules, state);

const isCreating = computed(() => formState.uiFlags.value.isCreating);

const currentDateTime = computed(() => {
  // Added to disable the scheduled at field from being set to the current time
  const now = new Date();
  const localTime = new Date(now.getTime() - now.getTimezoneOffset() * 60000);
  return localTime.toISOString().slice(0, 16);
});

const mapToOptions = (items, valueKey, labelKey) =>
  items?.map(item => ({
    value: item[valueKey],
    label: item[labelKey],
  })) ?? [];

const audienceList = computed(() =>
  mapToOptions(formState.labels.value, 'id', 'title')
);

const inboxOptions = computed(() =>
  mapToOptions(formState.inboxes.value, 'id', 'name')
);

const templateOptions = computed(() => {
  if (!state.inboxId) return [];
  const templates = formState.whatsappTemplates.value.filter(
    template =>
      template.inbox_id === Number(state.inboxId) &&
      template.status === 'APPROVED'
  );
  return templates.map(template => {
    // Create a more user-friendly label from template name
    const friendlyName = template.name
      .replace(/_/g, ' ')
      .replace(/\b\w/g, l => l.toUpperCase());

    return {
      value: template.id,
      label: `${friendlyName} (${template.language || 'en'})`,
      template: template,
    };
  });
});

const selectedTemplate = computed(() => {
  if (!state.templateId) return null;
  return templateOptions.value.find(option => option.value === state.templateId)
    ?.template;
});

const formErrors = computed(() => ({
  title: v$.value.title.$error
    ? t('CAMPAIGN.WHATSAPP.CREATE.FORM.TITLE.ERROR')
    : '',
  inbox: v$.value.inboxId.$error
    ? t('CAMPAIGN.WHATSAPP.CREATE.FORM.INBOX.ERROR')
    : '',
  template: v$.value.templateId.$error
    ? t('CAMPAIGN.WHATSAPP.CREATE.FORM.TEMPLATE.ERROR')
    : '',
  scheduledAt: v$.value.scheduledAt.$error
    ? t('CAMPAIGN.WHATSAPP.CREATE.FORM.SCHEDULED_AT.ERROR')
    : '',
  audience: v$.value.selectedAudience.$error
    ? t('CAMPAIGN.WHATSAPP.CREATE.FORM.AUDIENCE.ERROR')
    : '',
}));

const hasRequiredTemplateParams = computed(() => {
  return (
    templateParserRef.value?.v$?.$invalid === false &&
    templateParserRef.value?.isFormInvalid === false
  );
});

const isSubmitDisabled = computed(
  () => v$.value.$invalid || !hasRequiredTemplateParams.value
);
const canSendTest = computed(
  () =>
    /^\+[1-9]\d{1,14}$/.test(state.testPhone) &&
    selectedTemplate.value &&
    hasRequiredTemplateParams.value
);

const formatToUTCString = localDateTime =>
  localDateTime ? new Date(localDateTime).toISOString() : null;

const resetState = () => {
  Object.assign(state, initialState);
  v$.value.$reset();
};

const handleCancel = () => emit('cancel');

const previewAudience = async () => {
  if (!state.inboxId || state.selectedAudience.length === 0) return null;

  isPreviewingAudience.value = true;
  try {
    audiencePreview.value = await store.dispatch('campaigns/previewAudience', {
      inbox_id: state.inboxId,
      audience: state.selectedAudience.map(id => ({ id, type: 'Label' })),
    });
    return audiencePreview.value;
  } catch (error) {
    useAlert(
      error?.response?.data?.error ||
        t('CAMPAIGN.WHATSAPP.CREATE.FORM.AUDIENCE.PREVIEW_ERROR')
    );
    return null;
  } finally {
    isPreviewingAudience.value = false;
  }
};

const prepareTemplateParams = () => ({
  name: selectedTemplate.value?.name || '',
  namespace: selectedTemplate.value?.namespace || '',
  category: selectedTemplate.value?.category || 'UTILITY',
  language: selectedTemplate.value?.language || 'en_US',
  processed_params: templateParserRef.value?.processedParams || {},
});

const sendTestMessage = async () => {
  if (!canSendTest.value) return;

  isSendingTest.value = true;
  try {
    await store.dispatch('campaigns/sendTestMessage', {
      inbox_id: state.inboxId,
      template_id: state.templateId,
      phone_number: state.testPhone,
      template_params: prepareTemplateParams(),
    });
    useAlert(t('CAMPAIGN.WHATSAPP.CREATE.FORM.TEST.SUCCESS'));
  } catch (error) {
    useAlert(
      error?.response?.data?.error ||
        t('CAMPAIGN.WHATSAPP.CREATE.FORM.TEST.ERROR')
    );
  } finally {
    isSendingTest.value = false;
  }
};

const prepareCampaignDetails = () => {
  // Find the selected template to get its content
  const parserData = templateParserRef.value;

  // Extract template content - this should be the template message body
  const templateContent = parserData?.renderedTemplate || '';

  // Prepare template_params object with the same structure as used in contacts
  const templateParams = prepareTemplateParams();

  return {
    title: state.title,
    message: templateContent,
    template_params: templateParams,
    inbox_id: state.inboxId,
    scheduled_at: formatToUTCString(state.scheduledAt),
    audience: state.selectedAudience?.map(id => ({
      id,
      type: 'Label',
    })),
  };
};

const handleSubmit = async () => {
  const isFormValid = await v$.value.$validate();
  if (!isFormValid) return;
  const preview = await previewAudience();
  if (!preview?.eligible_count) return;

  emit('submit', prepareCampaignDetails());
  resetState();
  handleCancel();
};

// Reset template selection when inbox changes
watch(
  () => state.inboxId,
  () => {
    state.templateId = null;
    audiencePreview.value = null;
  }
);

watch(
  () => state.selectedAudience,
  () => {
    audiencePreview.value = null;
  },
  { deep: true }
);
</script>

<template>
  <form class="flex flex-col gap-4" @submit.prevent="handleSubmit">
    <Input
      v-model="state.title"
      :label="t('CAMPAIGN.WHATSAPP.CREATE.FORM.TITLE.LABEL')"
      :placeholder="t('CAMPAIGN.WHATSAPP.CREATE.FORM.TITLE.PLACEHOLDER')"
      :message="formErrors.title"
      :message-type="formErrors.title ? 'error' : 'info'"
    />

    <div class="flex flex-col gap-1">
      <label for="inbox" class="mb-0.5 text-sm font-medium text-n-slate-12">
        {{ t('CAMPAIGN.WHATSAPP.CREATE.FORM.INBOX.LABEL') }}
      </label>
      <ComboBox
        id="inbox"
        v-model="state.inboxId"
        :options="inboxOptions"
        :has-error="!!formErrors.inbox"
        :placeholder="t('CAMPAIGN.WHATSAPP.CREATE.FORM.INBOX.PLACEHOLDER')"
        :message="formErrors.inbox"
        class="[&>div>button]:bg-n-alpha-black2 [&>div>button:not(.focused)]:dark:outline-n-weak [&>div>button:not(.focused)]:hover:!outline-n-slate-6"
      />
    </div>

    <div class="flex flex-col gap-1">
      <label for="template" class="mb-0.5 text-sm font-medium text-n-slate-12">
        {{ t('CAMPAIGN.WHATSAPP.CREATE.FORM.TEMPLATE.LABEL') }}
      </label>
      <ComboBox
        id="template"
        v-model="state.templateId"
        :options="templateOptions"
        :has-error="!!formErrors.template"
        :placeholder="t('CAMPAIGN.WHATSAPP.CREATE.FORM.TEMPLATE.PLACEHOLDER')"
        :message="formErrors.template"
        class="[&>div>button]:bg-n-alpha-black2 [&>div>button:not(.focused)]:dark:outline-n-weak [&>div>button:not(.focused)]:hover:!outline-n-slate-6"
      />
      <p class="mt-1 text-xs text-n-slate-11">
        {{ t('CAMPAIGN.WHATSAPP.CREATE.FORM.TEMPLATE.INFO') }}
      </p>
    </div>

    <!-- Template Parser -->
    <WhatsAppTemplateParser
      v-if="selectedTemplate"
      ref="templateParserRef"
      :template="selectedTemplate"
    />

    <div v-if="selectedTemplate" class="rounded-lg border border-n-weak p-3">
      <p class="mb-2 text-sm font-medium text-n-slate-12">
        {{ t('CAMPAIGN.WHATSAPP.CREATE.FORM.TEST.TITLE') }}
      </p>
      <div class="flex items-end gap-2">
        <Input
          v-model="state.testPhone"
          class="min-w-0 flex-1"
          :label="t('CAMPAIGN.WHATSAPP.CREATE.FORM.TEST.PHONE')"
          :placeholder="t('CAMPAIGN.WHATSAPP.CREATE.FORM.TEST.PLACEHOLDER')"
        />
        <Button
          type="button"
          variant="faded"
          color="slate"
          :is-loading="isSendingTest"
          :disabled="!canSendTest || isSendingTest"
          :label="t('CAMPAIGN.WHATSAPP.CREATE.FORM.TEST.SEND')"
          @click="sendTestMessage"
        />
      </div>
      <p class="mt-2 text-xs text-n-slate-10">
        {{ t('CAMPAIGN.WHATSAPP.CREATE.FORM.TEST.HELP') }}
      </p>
    </div>

    <div class="flex flex-col gap-1">
      <label for="audience" class="mb-0.5 text-sm font-medium text-n-slate-12">
        {{ t('CAMPAIGN.WHATSAPP.CREATE.FORM.AUDIENCE.LABEL') }}
      </label>
      <TagMultiSelectComboBox
        v-model="state.selectedAudience"
        :options="audienceList"
        :label="t('CAMPAIGN.WHATSAPP.CREATE.FORM.AUDIENCE.LABEL')"
        :placeholder="t('CAMPAIGN.WHATSAPP.CREATE.FORM.AUDIENCE.PLACEHOLDER')"
        :has-error="!!formErrors.audience"
        :message="formErrors.audience"
        class="[&>div>button]:bg-n-alpha-black2"
      />
      <div class="mt-2 flex items-center justify-between gap-3">
        <Button
          type="button"
          size="xs"
          variant="faded"
          color="slate"
          :is-loading="isPreviewingAudience"
          :disabled="!state.inboxId || state.selectedAudience.length === 0"
          :label="t('CAMPAIGN.WHATSAPP.CREATE.FORM.AUDIENCE.PREVIEW')"
          @click="previewAudience"
        />
        <p v-if="audiencePreview" class="text-xs text-n-slate-11">
          {{
            t('CAMPAIGN.WHATSAPP.CREATE.FORM.AUDIENCE.PREVIEW_RESULT', {
              eligible: audiencePreview.eligible_count,
              total: audiencePreview.audience_count,
              skipped: audiencePreview.skipped_count,
            })
          }}
        </p>
      </div>
      <p
        v-if="audiencePreview?.limit_exceeded"
        class="mt-1 text-xs text-n-ruby-10"
      >
        {{
          t('CAMPAIGN.WHATSAPP.CREATE.FORM.AUDIENCE.LIMIT_EXCEEDED', {
            max: audiencePreview.max_audience_count,
          })
        }}
      </p>
      <p
        v-else-if="audiencePreview && audiencePreview.eligible_count === 0"
        class="mt-1 text-xs text-n-ruby-10"
      >
        {{ t('CAMPAIGN.WHATSAPP.CREATE.FORM.AUDIENCE.NO_ELIGIBLE') }}
      </p>
    </div>

    <Input
      v-model="state.scheduledAt"
      :label="t('CAMPAIGN.WHATSAPP.CREATE.FORM.SCHEDULED_AT.LABEL')"
      type="datetime-local"
      :min="currentDateTime"
      :placeholder="t('CAMPAIGN.WHATSAPP.CREATE.FORM.SCHEDULED_AT.PLACEHOLDER')"
      :message="formErrors.scheduledAt"
      :message-type="formErrors.scheduledAt ? 'error' : 'info'"
    />

    <div class="flex gap-3 justify-between items-center w-full">
      <Button
        variant="faded"
        color="slate"
        type="button"
        :label="t('CAMPAIGN.WHATSAPP.CREATE.FORM.BUTTONS.CANCEL')"
        class="w-full bg-n-alpha-2 text-n-blue-11 hover:bg-n-alpha-3"
        @click="handleCancel"
      />
      <Button
        :label="t('CAMPAIGN.WHATSAPP.CREATE.FORM.BUTTONS.CREATE')"
        class="w-full"
        type="submit"
        :is-loading="isCreating"
        :disabled="isCreating || isSubmitDisabled"
      />
    </div>
  </form>
</template>
