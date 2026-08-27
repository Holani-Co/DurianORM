<script setup>
import { computed, reactive } from 'vue';
import { useI18n } from 'vue-i18n';
import { useAlert } from 'dashboard/composables';
import { useMapGetter, useStore } from 'dashboard/composables/store';

import Input from 'dashboard/components-next/input/Input.vue';
import Button from 'dashboard/components-next/button/Button.vue';
import ComboBox from 'dashboard/components-next/combobox/ComboBox.vue';

const props = defineProps({
  template: {
    type: Object,
    default: null,
  },
});
const emit = defineEmits(['close']);
const { t } = useI18n();
const store = useStore();
const inboxes = useMapGetter('inboxes/getWhatsAppInboxes');
const uiFlags = useMapGetter('whatsappTemplates/getUIFlags');

const componentByType = type =>
  props.template?.components?.find(component => component.type === type);
const existingHeader = componentByType('HEADER');
const existingBody = componentByType('BODY');
const existingFooter = componentByType('FOOTER');
const existingButtons = componentByType('BUTTONS');

const state = reactive({
  name: props.template?.name || '',
  inboxId: props.template?.inbox_id || null,
  category: props.template?.category || 'MARKETING',
  language: props.template?.language || 'en_US',
  headerType: existingHeader?.format || 'NONE',
  header: existingHeader?.text || '',
  mediaFile: null,
  mediaHandle: existingHeader?.example?.header_handle?.[0] || null,
  body: existingBody?.text || '',
  footer: existingFooter?.text || '',
  buttons:
    existingButtons?.buttons?.map(button => ({
      type: button.type,
      text: button.text,
      value: button.url || button.phone_number || '',
    })) || [],
});

const isEdit = computed(() => Boolean(props.template));

const cloudInboxes = computed(() =>
  inboxes.value.filter(inbox => inbox.provider === 'whatsapp_cloud')
);
const inboxOptions = computed(() =>
  cloudInboxes.value.map(inbox => ({ value: inbox.id, label: inbox.name }))
);
const categoryOptions = [
  { value: 'MARKETING', label: 'Marketing' },
  { value: 'UTILITY', label: 'Utility' },
];
const languageOptions = [
  { value: 'en_US', label: 'English (US)' },
  { value: 'en', label: 'English' },
  { value: 'hi', label: 'Hindi' },
];
const headerOptions = computed(() => [
  { value: 'NONE', label: t('CAMPAIGN.WHATSAPP.TEMPLATES.FORM.HEADER_NONE') },
  { value: 'TEXT', label: t('CAMPAIGN.WHATSAPP.TEMPLATES.FORM.HEADER_TEXT') },
  { value: 'IMAGE', label: t('CAMPAIGN.WHATSAPP.TEMPLATES.FORM.HEADER_IMAGE') },
  { value: 'VIDEO', label: t('CAMPAIGN.WHATSAPP.TEMPLATES.FORM.HEADER_VIDEO') },
  {
    value: 'DOCUMENT',
    label: t('CAMPAIGN.WHATSAPP.TEMPLATES.FORM.HEADER_DOCUMENT'),
  },
]);
const buttonOptions = computed(() => [
  {
    value: 'QUICK_REPLY',
    label: t('CAMPAIGN.WHATSAPP.TEMPLATES.FORM.BUTTON_QUICK_REPLY'),
  },
  { value: 'URL', label: t('CAMPAIGN.WHATSAPP.TEMPLATES.FORM.BUTTON_URL') },
  {
    value: 'PHONE_NUMBER',
    label: t('CAMPAIGN.WHATSAPP.TEMPLATES.FORM.BUTTON_PHONE'),
  },
]);
const mediaAccept = computed(() => {
  if (state.headerType === 'IMAGE') return 'image/jpeg,image/png';
  if (state.headerType === 'VIDEO') return 'video/mp4';
  if (state.headerType === 'DOCUMENT') return 'application/pdf';
  return '';
});
const mediaHeader = computed(() =>
  ['IMAGE', 'VIDEO', 'DOCUMENT'].includes(state.headerType)
);
const hasReusableMedia = computed(
  () => existingHeader?.format === state.headerType && state.mediaHandle
);

const isBusy = computed(
  () =>
    uiFlags.value.isCreating ||
    uiFlags.value.isSubmitting ||
    uiFlags.value.isUploading
);
const normalizedName = computed(() =>
  state.name
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
);
const headerValid = computed(
  () =>
    state.headerType === 'NONE' ||
    (state.headerType === 'TEXT' && state.header.trim()) ||
    (mediaHeader.value && (state.mediaFile || hasReusableMedia.value))
);
const buttonsValid = computed(() =>
  state.buttons.every(
    button =>
      button.text.trim() &&
      (button.type === 'QUICK_REPLY' || button.value.trim())
  )
);
const canSubmit = computed(
  () =>
    normalizedName.value &&
    state.inboxId &&
    state.body.trim() &&
    headerValid.value &&
    buttonsValid.value &&
    !isBusy.value
);

const templateVariables = text => {
  const matches = [...text.matchAll(/\{\{(\d+)\}\}/g)].map(match =>
    Number(match[1])
  );
  return [...new Set(matches)].sort((first, second) => first - second);
};

const componentExample = (text, componentType) => {
  const variables = templateVariables(text);
  if (variables.length === 0) return {};

  const samples = variables.map(index => `Sample ${index}`);
  return componentType === 'HEADER'
    ? { example: { header_text: samples } }
    : { example: { body_text: [samples] } };
};

const buildHeader = mediaHandle => {
  if (state.headerType === 'NONE') return null;
  if (state.headerType === 'TEXT') {
    return {
      type: 'HEADER',
      format: 'TEXT',
      text: state.header.trim(),
      ...componentExample(state.header, 'HEADER'),
    };
  }
  return {
    type: 'HEADER',
    format: state.headerType,
    example: { header_handle: [mediaHandle] },
  };
};

const buildButtons = () => {
  const buttons = state.buttons.map(button => {
    const component = { type: button.type, text: button.text.trim() };
    if (button.type === 'URL') {
      component.url = button.value.trim();
      if (component.url.includes('{{1}}')) {
        component.example = ['sample'];
      }
    }
    if (button.type === 'PHONE_NUMBER') {
      component.phone_number = button.value.trim();
    }
    return component;
  });
  return buttons.length ? { type: 'BUTTONS', buttons } : null;
};

const buildComponents = mediaHandle => {
  const components = [];
  const header = buildHeader(mediaHandle);
  if (header) components.push(header);
  components.push({
    type: 'BODY',
    text: state.body.trim(),
    ...componentExample(state.body, 'BODY'),
  });
  if (state.footer.trim()) {
    components.push({ type: 'FOOTER', text: state.footer.trim() });
  }
  const buttons = buildButtons();
  if (buttons) components.push(buttons);
  return components;
};

const addButton = () => {
  if (state.buttons.length >= 3) return;
  state.buttons.push({ type: 'QUICK_REPLY', text: '', value: '' });
};

const removeButton = index => state.buttons.splice(index, 1);

const setMediaFile = event => {
  [state.mediaFile] = event.target.files;
};

const errorMessage = error =>
  error?.response?.data?.error ||
  error?.response?.data?.message ||
  t('CAMPAIGN.WHATSAPP.TEMPLATES.API.ERROR');

const submitTemplate = async () => {
  if (!canSubmit.value) return;

  try {
    let mediaHandle = null;
    if (mediaHeader.value && state.mediaFile) {
      mediaHandle = await store.dispatch('whatsappTemplates/uploadSample', {
        inboxId: state.inboxId,
        file: state.mediaFile,
      });
    } else if (hasReusableMedia.value) {
      mediaHandle = state.mediaHandle;
    }
    const templatePayload = {
      name: normalizedName.value,
      inbox_id: state.inboxId,
      category: state.category,
      language: state.language,
      components: buildComponents(mediaHandle),
    };
    const template = isEdit.value
      ? await store.dispatch('whatsappTemplates/update', {
          id: props.template.id,
          ...templatePayload,
        })
      : await store.dispatch('whatsappTemplates/create', templatePayload);
    if (!isEdit.value || props.template.status === 'DRAFT') {
      await store.dispatch('whatsappTemplates/submit', template.id);
    }
    useAlert(t('CAMPAIGN.WHATSAPP.TEMPLATES.API.SUBMITTED'));
    emit('close');
  } catch (error) {
    useAlert(errorMessage(error));
  }
};
</script>

<template>
  <div
    class="absolute top-10 z-50 flex max-h-[80vh] w-[28rem] flex-col overflow-y-auto rounded-xl border border-n-weak bg-n-alpha-3 shadow-md backdrop-blur-[100px] ltr:right-0 rtl:left-0"
  >
    <form class="flex flex-col gap-4 p-6" @submit.prevent="submitTemplate">
      <div>
        <h3 class="text-base font-medium text-n-slate-12">
          {{
            isEdit
              ? t('CAMPAIGN.WHATSAPP.TEMPLATES.EDIT_TITLE')
              : t('CAMPAIGN.WHATSAPP.TEMPLATES.CREATE_TITLE')
          }}
        </h3>
        <p class="mt-1 text-xs text-n-slate-11">
          {{ t('CAMPAIGN.WHATSAPP.TEMPLATES.CREATE_SUBTITLE') }}
        </p>
      </div>

      <Input
        v-model="state.name"
        :label="t('CAMPAIGN.WHATSAPP.TEMPLATES.FORM.NAME')"
        :placeholder="t('CAMPAIGN.WHATSAPP.TEMPLATES.FORM.NAME_PLACEHOLDER')"
        :message="
          normalizedName && normalizedName !== state.name ? normalizedName : ''
        "
      />

      <div class="flex flex-col gap-1">
        <label class="text-heading-3 text-n-slate-12">
          {{ t('CAMPAIGN.WHATSAPP.TEMPLATES.FORM.INBOX') }}
        </label>
        <ComboBox
          v-model="state.inboxId"
          :options="inboxOptions"
          :disabled="isEdit"
          :placeholder="t('CAMPAIGN.WHATSAPP.TEMPLATES.FORM.INBOX_PLACEHOLDER')"
        />
      </div>

      <div class="grid grid-cols-2 gap-3">
        <div class="flex flex-col gap-1">
          <label class="text-heading-3 text-n-slate-12">
            {{ t('CAMPAIGN.WHATSAPP.TEMPLATES.FORM.CATEGORY') }}
          </label>
          <ComboBox v-model="state.category" :options="categoryOptions" />
        </div>
        <div class="flex flex-col gap-1">
          <label class="text-heading-3 text-n-slate-12">
            {{ t('CAMPAIGN.WHATSAPP.TEMPLATES.FORM.LANGUAGE') }}
          </label>
          <ComboBox v-model="state.language" :options="languageOptions" />
        </div>
      </div>

      <div class="flex flex-col gap-1">
        <label class="text-heading-3 text-n-slate-12">
          {{ t('CAMPAIGN.WHATSAPP.TEMPLATES.FORM.HEADER_TYPE') }}
        </label>
        <ComboBox v-model="state.headerType" :options="headerOptions" />
      </div>

      <Input
        v-if="state.headerType === 'TEXT'"
        v-model="state.header"
        :label="t('CAMPAIGN.WHATSAPP.TEMPLATES.FORM.HEADER')"
        :placeholder="t('CAMPAIGN.WHATSAPP.TEMPLATES.FORM.HEADER_PLACEHOLDER')"
      />

      <div v-if="mediaHeader" class="flex flex-col gap-1">
        <label
          for="whatsapp-template-media"
          class="text-heading-3 text-n-slate-12"
        >
          {{ t('CAMPAIGN.WHATSAPP.TEMPLATES.FORM.MEDIA_SAMPLE') }}
        </label>
        <input
          id="whatsapp-template-media"
          type="file"
          :accept="mediaAccept"
          class="block w-full rounded-lg bg-n-alpha-black2 px-3 py-2 text-sm text-n-slate-11 outline outline-1 -outline-offset-1 outline-n-weak file:mr-3 file:rounded-md file:border-0 file:bg-n-alpha-2 file:px-3 file:py-1 file:text-n-slate-12"
          @change="setMediaFile"
        />
        <p class="mt-1 text-xs text-n-slate-11">
          {{ t('CAMPAIGN.WHATSAPP.TEMPLATES.FORM.MEDIA_HELP') }}
        </p>
      </div>

      <div class="flex flex-col gap-1">
        <label
          for="whatsapp-template-body"
          class="text-heading-3 text-n-slate-12"
        >
          {{ t('CAMPAIGN.WHATSAPP.TEMPLATES.FORM.BODY') }}
        </label>
        <textarea
          id="whatsapp-template-body"
          v-model="state.body"
          rows="6"
          :placeholder="t('CAMPAIGN.WHATSAPP.TEMPLATES.FORM.BODY_PLACEHOLDER')"
          class="reset-base w-full resize-y rounded-lg border-0 bg-n-alpha-black2 px-3 py-2.5 text-sm text-n-slate-12 outline outline-1 -outline-offset-1 outline-n-weak placeholder:text-n-slate-10 hover:outline-n-slate-6 focus:outline-n-brand"
        />
        <p class="mt-1 text-xs text-n-slate-11">
          {{ t('CAMPAIGN.WHATSAPP.TEMPLATES.FORM.BODY_HELP') }}
        </p>
      </div>

      <Input
        v-model="state.footer"
        :label="t('CAMPAIGN.WHATSAPP.TEMPLATES.FORM.FOOTER')"
        :placeholder="t('CAMPAIGN.WHATSAPP.TEMPLATES.FORM.FOOTER_PLACEHOLDER')"
      />

      <div class="flex flex-col gap-3">
        <div class="flex items-center justify-between">
          <label class="text-heading-3 text-n-slate-12">
            {{ t('CAMPAIGN.WHATSAPP.TEMPLATES.FORM.BUTTONS') }}
          </label>
          <Button
            v-if="state.buttons.length < 3"
            type="button"
            size="xs"
            variant="ghost"
            icon="i-lucide-plus"
            :label="t('CAMPAIGN.WHATSAPP.TEMPLATES.FORM.ADD_BUTTON')"
            @click="addButton"
          />
        </div>
        <div
          v-for="(button, index) in state.buttons"
          :key="index"
          class="flex flex-col gap-2 rounded-lg border border-n-weak p-3"
        >
          <div class="flex items-center gap-2">
            <ComboBox
              v-model="button.type"
              :options="buttonOptions"
              class="flex-1"
            />
            <Button
              type="button"
              size="xs"
              color="ruby"
              variant="ghost"
              icon="i-lucide-x"
              @click="removeButton(index)"
            />
          </div>
          <Input
            v-model="button.text"
            :placeholder="t('CAMPAIGN.WHATSAPP.TEMPLATES.FORM.BUTTON_TEXT')"
          />
          <Input
            v-if="button.type !== 'QUICK_REPLY'"
            v-model="button.value"
            :placeholder="
              button.type === 'URL'
                ? t('CAMPAIGN.WHATSAPP.TEMPLATES.FORM.BUTTON_URL_PLACEHOLDER')
                : t('CAMPAIGN.WHATSAPP.TEMPLATES.FORM.BUTTON_PHONE_PLACEHOLDER')
            "
          />
        </div>
      </div>

      <div class="flex items-center justify-end gap-3 pt-2">
        <Button
          type="button"
          color="slate"
          variant="faded"
          :label="t('CAMPAIGN.WHATSAPP.TEMPLATES.FORM.CANCEL')"
          @click="emit('close')"
        />
        <Button
          type="submit"
          :disabled="!canSubmit"
          :is-loading="isBusy"
          :label="
            isEdit
              ? t('CAMPAIGN.WHATSAPP.TEMPLATES.FORM.UPDATE')
              : t('CAMPAIGN.WHATSAPP.TEMPLATES.FORM.SUBMIT')
          "
        />
      </div>
    </form>
  </div>
</template>
