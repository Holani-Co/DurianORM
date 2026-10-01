<script setup>
import { ref, computed, onMounted } from 'vue';
import { useMapGetter } from 'dashboard/composables/store';
import { useAlert } from 'dashboard/composables';
import { useI18n } from 'vue-i18n';
import { validEmailsByComma } from './helpers/emailHeadHelper';
import NextButton from 'dashboard/components-next/button/Button.vue';

const props = defineProps({
  show: { type: Boolean, default: false },
  currentChat: { type: Object, default: () => ({}) },
});
const emit = defineEmits(['cancel', 'update:show']);

const { t } = useI18n();
const accountId = useMapGetter('getCurrentAccountId');
const axios = window.axios;

const toEmails = ref('');
const ccEmails = ref('');
const note = ref('');
const presets = ref([]);
const isSubmitting = ref(false);

const localShow = computed({
  get: () => props.show,
  set: value => emit('update:show', value),
});

const isValid = computed(
  () =>
    toEmails.value.trim().length > 0 &&
    validEmailsByComma(toEmails.value) &&
    (!ccEmails.value.trim() || validEmailsByComma(ccEmails.value))
);

// Add a picked preset to the To line without clobbering what's already there.
const addPreset = email => {
  if (!email) return;
  const list = toEmails.value
    .split(',')
    .map(e => e.trim())
    .filter(Boolean);
  if (!list.includes(email)) list.push(email);
  toEmails.value = list.join(', ');
};

const onPresetChange = event => {
  addPreset(event.target.value);
  event.target.value = '';
};

const loadPresets = async () => {
  try {
    const { data } = await axios.post(
      `/api/v1/accounts/${accountId.value}/integrations/zoho_bridge/forward_recipients`,
      { conversation_id: props.currentChat.id }
    );
    presets.value = (data?.recipients || []).map(r => ({
      ...r,
      label: `${r.name} (${r.email})`,
    }));
  } catch {
    presets.value = [];
  }
};
onMounted(loadPresets);

const onCancel = () => emit('cancel');

const onSubmit = async () => {
  if (!isValid.value || isSubmitting.value) return;
  isSubmitting.value = true;
  try {
    await axios.post(
      `/api/v1/accounts/${accountId.value}/integrations/zoho_bridge/forward_email`,
      {
        conversation_id: props.currentChat.id,
        to_emails: toEmails.value.trim(),
        cc_emails: ccEmails.value.trim(),
        note: note.value.trim(),
      }
    );
    useAlert(t('FORWARD_EMAIL.SUCCESS'));
    onCancel();
  } catch (error) {
    useAlert(
      error?.response?.data?.detail ||
        error?.response?.data?.error ||
        t('FORWARD_EMAIL.ERROR')
    );
  } finally {
    isSubmitting.value = false;
  }
};
</script>

<template>
  <woot-modal v-model:show="localShow" :on-close="onCancel">
    <div class="flex flex-col h-auto overflow-auto">
      <woot-modal-header
        :header-title="$t('FORWARD_EMAIL.TITLE')"
        :header-content="$t('FORWARD_EMAIL.DESC')"
      />
      <form class="w-full" @submit.prevent="onSubmit">
        <div v-if="presets.length" class="w-full mt-2">
          <label class="block mb-1 text-sm font-medium text-n-slate-12">
            {{ $t('FORWARD_EMAIL.FORM.PRESET.LABEL') }}
          </label>
          <select class="w-full" @change="onPresetChange">
            <option value="">
              {{ $t('FORWARD_EMAIL.FORM.PRESET.PLACEHOLDER') }}
            </option>
            <option v-for="r in presets" :key="r.email" :value="r.email">
              {{ r.label }}
            </option>
          </select>
        </div>
        <div class="w-full mt-2">
          <label class="block mb-1 text-sm font-medium text-n-slate-12">
            {{ $t('FORWARD_EMAIL.FORM.TO.LABEL') }}
          </label>
          <input
            v-model="toEmails"
            type="text"
            :placeholder="$t('FORWARD_EMAIL.FORM.TO.PLACEHOLDER')"
          />
        </div>
        <div class="w-full mt-2">
          <label class="block mb-1 text-sm font-medium text-n-slate-12">
            {{ $t('FORWARD_EMAIL.FORM.CC.LABEL') }}
          </label>
          <input
            v-model="ccEmails"
            type="text"
            :placeholder="$t('FORWARD_EMAIL.FORM.CC.PLACEHOLDER')"
          />
        </div>
        <div class="w-full mt-2">
          <label class="block mb-1 text-sm font-medium text-n-slate-12">
            {{ $t('FORWARD_EMAIL.FORM.NOTE.LABEL') }}
          </label>
          <textarea
            v-model="note"
            rows="3"
            :placeholder="$t('FORWARD_EMAIL.FORM.NOTE.PLACEHOLDER')"
          />
        </div>
        <p class="mt-2 text-xs text-n-slate-11">
          {{ $t('FORWARD_EMAIL.FORM.TRAIL_HINT') }}
        </p>
        <div class="flex flex-row justify-end w-full gap-2 px-0 py-2">
          <NextButton
            faded
            slate
            type="reset"
            :label="$t('FORWARD_EMAIL.CANCEL')"
            @click.prevent="onCancel"
          />
          <NextButton
            type="submit"
            :label="
              isSubmitting
                ? $t('FORWARD_EMAIL.SENDING')
                : $t('FORWARD_EMAIL.SUBMIT')
            "
            :disabled="!isValid || isSubmitting"
          />
        </div>
      </form>
    </div>
  </woot-modal>
</template>
