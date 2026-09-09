<script setup>
/**
 * This component handles parsing and sending WhatsApp message templates.
 * It works as follows:
 * 1. Displays the template text with variable placeholders.
 * 2. Generates input fields for each variable in the template.
 * 3. Validates that all variables are filled before sending.
 * 4. Replaces placeholders with user-provided values.
 * 5. Emits events to send the processed message or reset the template.
 */
import { ref, computed, onMounted, watch } from 'vue';
import { useVuelidate } from '@vuelidate/core';
import { requiredIf } from '@vuelidate/validators';
import { useI18n } from 'vue-i18n';
import { uploadFile } from 'dashboard/helper/uploadHelper';

import Input from 'dashboard/components-next/input/Input.vue';
import {
  buildTemplateParameters,
  allKeysRequired,
  replaceTemplateVariables,
  DEFAULT_LANGUAGE,
  DEFAULT_CATEGORY,
  COMPONENT_TYPES,
  MEDIA_FORMATS,
  findComponentByType,
} from 'dashboard/helper/templateHelper';

const props = defineProps({
  template: {
    type: Object,
    default: () => ({}),
    validator: value => {
      if (!value || typeof value !== 'object') return false;
      if (!value.components || !Array.isArray(value.components)) return false;
      return true;
    },
  },
  allowMediaUpload: {
    type: Boolean,
    default: false,
  },
});

const emit = defineEmits(['sendMessage', 'resetTemplate', 'back']);

const { t } = useI18n();

const processedParams = ref({});
const mediaBlobId = ref(null);
const mediaFileName = ref('');
const mediaUploadError = ref('');
const isUploadingMedia = ref(false);

const MEDIA_FILE_TYPES = {
  IMAGE: ['image/jpeg', 'image/png'],
  VIDEO: ['video/mp4'],
  DOCUMENT: ['application/pdf'],
};
const MAX_MEDIA_FILE_SIZE = 16 * 1024 * 1024;

const languageLabel = computed(() => {
  return `${t('WHATSAPP_TEMPLATES.PARSER.LANGUAGE')}: ${props.template.language || DEFAULT_LANGUAGE}`;
});

const categoryLabel = computed(() => {
  return `${t('WHATSAPP_TEMPLATES.PARSER.CATEGORY')}: ${props.template.category || DEFAULT_CATEGORY}`;
});

const headerComponent = computed(() => {
  return findComponentByType(props.template, COMPONENT_TYPES.HEADER);
});

const bodyComponent = computed(() => {
  return findComponentByType(props.template, COMPONENT_TYPES.BODY);
});

const bodyText = computed(() => {
  return bodyComponent.value?.text || '';
});

const hasMediaHeader = computed(() =>
  MEDIA_FORMATS.includes(headerComponent.value?.format)
);

const formatType = computed(() => {
  const format = headerComponent.value?.format;
  return format ? format.charAt(0) + format.slice(1).toLowerCase() : '';
});

const isDocumentTemplate = computed(() => {
  return headerComponent.value?.format?.toLowerCase() === 'document';
});

const acceptedMediaTypes = computed(() =>
  (MEDIA_FILE_TYPES[headerComponent.value?.format] || []).join(',')
);

const hasVariables = computed(() => {
  return bodyText.value?.match(/{{([^}]+)}}/g) !== null;
});

const renderedTemplate = computed(() => {
  return replaceTemplateVariables(bodyText.value, processedParams.value);
});

const isFormInvalid = computed(() => {
  if (!hasVariables.value && !hasMediaHeader.value) return false;

  if (
    hasMediaHeader.value &&
    !processedParams.value.header?.media_url &&
    !mediaBlobId.value
  ) {
    return true;
  }

  if (isUploadingMedia.value || mediaUploadError.value) return true;

  if (hasVariables.value && processedParams.value.body) {
    const hasEmptyBodyVariable = Object.values(processedParams.value.body).some(
      value => !value
    );
    if (hasEmptyBodyVariable) return true;
  }

  if (processedParams.value.buttons) {
    const hasEmptyButtonParameter = processedParams.value.buttons.some(
      button => !button.parameter
    );
    if (hasEmptyButtonParameter) return true;
  }

  return false;
});

const v$ = useVuelidate(
  {
    processedParams: {
      requiredIfKeysPresent: requiredIf(hasVariables),
      allKeysRequired,
    },
  },
  { processedParams }
);

const initializeTemplateParameters = () => {
  processedParams.value = buildTemplateParameters(
    props.template,
    hasMediaHeader.value
  );
  mediaBlobId.value = null;
  mediaFileName.value = '';
  mediaUploadError.value = '';
};

const updateMediaUrl = value => {
  processedParams.value.header ??= {};
  processedParams.value.header.media_url = value;
  if (value) {
    mediaBlobId.value = null;
    mediaFileName.value = '';
    mediaUploadError.value = '';
  }
};

const updateMediaName = value => {
  processedParams.value.header ??= {};
  processedParams.value.header.media_name = value;
};

const uploadMedia = async event => {
  const file = event.target.files?.[0];
  if (!file) return;
  event.target.value = '';

  mediaBlobId.value = null;
  mediaFileName.value = '';
  mediaUploadError.value = '';
  if (!acceptedMediaTypes.value.split(',').includes(file.type)) {
    mediaUploadError.value = t(
      'WHATSAPP_TEMPLATES.PARSER.MEDIA_UPLOAD_TYPE_ERROR',
      { type: formatType.value }
    );
    return;
  }
  if (file.size > MAX_MEDIA_FILE_SIZE) {
    mediaUploadError.value = t(
      'WHATSAPP_TEMPLATES.PARSER.MEDIA_UPLOAD_SIZE_ERROR'
    );
    return;
  }

  isUploadingMedia.value = true;
  try {
    const { blobId } = await uploadFile(file);
    mediaBlobId.value = blobId;
    mediaFileName.value = file.name;
    processedParams.value.header.media_url = '';
    if (isDocumentTemplate.value && !processedParams.value.header.media_name) {
      processedParams.value.header.media_name = file.name;
    }
  } catch (error) {
    mediaUploadError.value =
      error?.response?.data?.error ||
      t('WHATSAPP_TEMPLATES.PARSER.MEDIA_UPLOAD_ERROR');
  } finally {
    isUploadingMedia.value = false;
  }
};

const sendMessage = () => {
  v$.value.$touch();
  if (v$.value.$invalid) return;

  const { name, category, language, namespace } = props.template;

  const payload = {
    message: renderedTemplate.value,
    templateParams: {
      name,
      category,
      language,
      namespace,
      processed_params: processedParams.value,
    },
  };
  emit('sendMessage', payload);
};

const resetTemplate = () => {
  emit('resetTemplate');
};

const goBack = () => {
  emit('back');
};

onMounted(initializeTemplateParameters);

watch(
  () => props.template,
  () => {
    initializeTemplateParameters();
    v$.value.$reset();
  },
  { deep: true }
);

defineExpose({
  processedParams,
  mediaBlobId,
  hasVariables,
  hasMediaHeader,
  isDocumentTemplate,
  headerComponent,
  renderedTemplate,
  isFormInvalid,
  isUploadingMedia,
  v$,
  updateMediaUrl,
  updateMediaName,
  sendMessage,
  resetTemplate,
  goBack,
});
</script>

<template>
  <div>
    <div class="flex flex-col gap-4 p-4 mb-4 rounded-lg bg-n-alpha-black2">
      <div class="flex justify-between items-center">
        <h3 class="text-sm font-medium text-n-slate-12">
          {{ template.name }}
        </h3>
        <span class="text-xs text-n-slate-11">
          {{ languageLabel }}
        </span>
      </div>

      <div class="flex flex-col gap-2">
        <div class="rounded-md">
          <div class="text-sm whitespace-pre-wrap text-n-slate-12">
            {{ renderedTemplate }}
          </div>
        </div>
      </div>

      <div class="text-xs text-n-slate-11">
        {{ categoryLabel }}
      </div>
    </div>

    <div v-if="hasVariables || hasMediaHeader">
      <div v-if="hasMediaHeader" class="mb-4">
        <p class="mb-2.5 text-sm font-semibold">
          {{
            $t('WHATSAPP_TEMPLATES.PARSER.MEDIA_HEADER_LABEL', {
              type: formatType,
            }) || `${formatType} Header`
          }}
        </p>
        <div v-if="allowMediaUpload" class="mb-3 flex flex-col gap-2">
          <label class="text-sm font-medium text-n-slate-12">
            {{ t('WHATSAPP_TEMPLATES.PARSER.MEDIA_UPLOAD_LABEL') }}
          </label>
          <input
            type="file"
            :accept="acceptedMediaTypes"
            class="text-sm text-n-slate-11 file:mr-3 file:rounded-md file:border-0 file:bg-n-alpha-2 file:px-3 file:py-1.5 file:text-n-slate-12"
            :disabled="isUploadingMedia"
            @change="uploadMedia"
          />
          <p v-if="isUploadingMedia" class="text-xs text-n-slate-11">
            {{ t('WHATSAPP_TEMPLATES.PARSER.MEDIA_UPLOADING') }}
          </p>
          <p v-else-if="mediaFileName" class="text-xs text-n-teal-11">
            {{
              t('WHATSAPP_TEMPLATES.PARSER.MEDIA_UPLOAD_SUCCESS', {
                filename: mediaFileName,
              })
            }}
          </p>
          <p v-if="mediaUploadError" class="text-xs text-n-ruby-11">
            {{ mediaUploadError }}
          </p>
          <p class="text-center text-xs text-n-slate-10">
            {{ t('WHATSAPP_TEMPLATES.PARSER.MEDIA_UPLOAD_OR_URL') }}
          </p>
        </div>
        <div class="mb-2.5 flex items-center">
          <Input
            :model-value="processedParams.header?.media_url || ''"
            type="url"
            class="flex-1"
            :placeholder="
              t('WHATSAPP_TEMPLATES.PARSER.MEDIA_URL_LABEL', {
                type: formatType,
              })
            "
            @update:model-value="updateMediaUrl"
          />
        </div>
        <p class="mb-2.5 text-xs text-n-slate-11">
          {{ t('WHATSAPP_TEMPLATES.PARSER.MEDIA_URL_HINT') }}
        </p>
        <div v-if="isDocumentTemplate" class="flex items-center mb-2.5">
          <Input
            :model-value="processedParams.header?.media_name || ''"
            type="text"
            class="flex-1"
            :placeholder="
              t('WHATSAPP_TEMPLATES.PARSER.DOCUMENT_NAME_PLACEHOLDER')
            "
            @update:model-value="updateMediaName"
          />
        </div>
      </div>

      <!-- Body Variables Section -->
      <div v-if="processedParams.body">
        <p class="mb-2.5 text-sm font-semibold">
          {{ $t('WHATSAPP_TEMPLATES.PARSER.VARIABLES_LABEL') }}
        </p>
        <div
          v-for="(variable, key) in processedParams.body"
          :key="`body-${key}`"
          class="flex items-center mb-2.5"
        >
          <Input
            v-model="processedParams.body[key]"
            type="text"
            class="flex-1"
            :placeholder="
              t('WHATSAPP_TEMPLATES.PARSER.VARIABLE_PLACEHOLDER', {
                variable: key,
              })
            "
          />
        </div>
      </div>

      <!-- Button Variables Section -->
      <div v-if="processedParams.buttons">
        <p class="mb-2.5 text-sm font-semibold">
          {{ t('WHATSAPP_TEMPLATES.PARSER.BUTTON_PARAMETERS') }}
        </p>
        <div
          v-for="(button, index) in processedParams.buttons"
          :key="`button-${index}`"
          class="flex items-center mb-2.5"
        >
          <Input
            v-model="processedParams.buttons[index].parameter"
            type="text"
            class="flex-1"
            :placeholder="t('WHATSAPP_TEMPLATES.PARSER.BUTTON_PARAMETER')"
          />
        </div>
      </div>
      <p
        v-if="v$.$dirty && v$.$invalid"
        class="p-2.5 text-center rounded-md bg-n-ruby-9/20 text-n-ruby-9"
      >
        {{ $t('WHATSAPP_TEMPLATES.PARSER.FORM_ERROR_MESSAGE') }}
      </p>
    </div>

    <slot
      name="actions"
      :send-message="sendMessage"
      :reset-template="resetTemplate"
      :go-back="goBack"
      :is-valid="!v$.$invalid"
      :disabled="isFormInvalid"
    />
  </div>
</template>
