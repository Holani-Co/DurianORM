<script setup>
// Phase 4 — editable thresholds & misc. Three live-editable settings that all sit
// at the top level of routing_rules.yaml, so the override engine already carries
// them and their helpers read through the live accessor:
//   • confidence_threshold      — below this the classifier defers to an agent
//   • system_notification_subjects — subjects filed as General Information
//   • auto_file_senders         — internal addresses auto-filed, never classified
// The cache TTL is shown read-only (it's a server env var, not part of the config).
import { ref, reactive, computed } from 'vue';
import { useI18n } from 'vue-i18n';
import { useMapGetter } from 'dashboard/composables/store';
import { useAlert } from 'dashboard/composables';

const props = defineProps({
  effective: { type: Object, default: () => ({}) },
  override: { type: Object, default: () => ({}) },
});
const emit = defineEmits(['published']);

const { t } = useI18n();
const accountId = useMapGetter('getCurrentAccountId');
const axios = window.axios;

const edits = reactive({}); // { topLevelField: value }
const busy = ref(false);
const errors = ref([]);
const subjectInput = ref('');
const senderInput = ref('');

const dirtyCount = computed(() => Object.keys(edits).length);

function field(name) {
  return name in edits ? edits[name] : props.effective[name];
}
function setField(name, value) {
  edits[name] = value;
}

const threshold = computed(() => field('confidence_threshold') ?? '');
function setThreshold(raw) {
  const n = parseFloat(raw);
  // Keep the raw string when it isn't a number so the bridge rejects it clearly.
  setField('confidence_threshold', Number.isFinite(n) ? n : raw);
}

const subjects = computed(() => field('system_notification_subjects') || []);
const senders = computed(() => field('auto_file_senders') || []);

function addSubject() {
  const v = subjectInput.value.trim();
  if (!v || subjects.value.includes(v)) {
    subjectInput.value = '';
    return;
  }
  setField('system_notification_subjects', [...subjects.value, v]);
  subjectInput.value = '';
}
function removeSubject(idx) {
  const arr = [...subjects.value];
  arr.splice(idx, 1);
  setField('system_notification_subjects', arr);
}

const validSender = v =>
  /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(v) || /^@[^@\s]+\.[^@\s]+$/.test(v);

function addSender() {
  const v = senderInput.value.trim().toLowerCase();
  if (!v) return;
  if (!validSender(v)) {
    useAlert(t('ROUTING_CONFIG.SETTINGS.INVALID_SENDER'));
    return;
  }
  if (senders.value.includes(v)) {
    senderInput.value = '';
    return;
  }
  setField('auto_file_senders', [...senders.value, v]);
  senderInput.value = '';
}
function removeSender(idx) {
  const arr = [...senders.value];
  arr.splice(idx, 1);
  setField('auto_file_senders', arr);
}

function discard() {
  Object.keys(edits).forEach(k => delete edits[k]);
  errors.value = [];
}

async function publish() {
  if (busy.value || !dirtyCount.value) return;
  errors.value = [];
  const doc = JSON.parse(JSON.stringify(props.override || {}));
  Object.keys(edits).forEach(k => {
    const v = edits[k];
    doc[k] = Array.isArray(v)
      ? v.map(x => String(x).trim()).filter(Boolean)
      : v;
  });

  busy.value = true;
  const url = `/api/v1/accounts/${accountId.value}/integrations/routing_config`;
  try {
    const { data: v } = await axios.post(`${url}/validate`, { doc });
    if (!v.ok) {
      errors.value = v.errors || ['Validation failed.'];
      return;
    }
    await axios.post(`${url}/publish`, {
      doc,
      note: 'Thresholds updated from the UI',
    });
    useAlert(t('ROUTING_CONFIG.SETTINGS.PUBLISHED'));
    discard();
    emit('published');
  } catch (e) {
    useAlert(
      e?.response?.data?.error || t('ROUTING_CONFIG.SETTINGS.PUBLISH_FAILED')
    );
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <div class="max-w-2xl pb-24">
    <p class="mb-4 text-sm text-n-slate-11">
      {{ t('ROUTING_CONFIG.SETTINGS.EDIT_HINT') }}
    </p>

    <!-- confidence threshold -->
    <div class="p-4 mb-4 border rounded-xl border-n-weak">
      <div class="text-sm font-medium text-n-slate-12">
        {{ t('ROUTING_CONFIG.SETTINGS.CONFIDENCE') }}
      </div>
      <div class="mb-2 text-xs text-n-slate-10">
        {{ t('ROUTING_CONFIG.SETTINGS.CONFIDENCE_HINT') }}
      </div>
      <input
        type="number"
        min="0"
        max="1"
        step="0.05"
        :value="threshold"
        class="w-32 px-2.5 py-1.5 text-sm border rounded-lg outline-none border-n-weak bg-n-surface text-n-slate-12 focus:border-n-brand"
        @input="setThreshold($event.target.value)"
      />
    </div>

    <!-- system notification subjects -->
    <div class="p-4 mb-4 border rounded-xl border-n-weak">
      <div class="text-sm font-medium text-n-slate-12">
        {{ t('ROUTING_CONFIG.SETTINGS.SYSTEM_SUBJECTS') }}
      </div>
      <div class="mb-2 text-xs text-n-slate-10">
        {{ t('ROUTING_CONFIG.SETTINGS.SYSTEM_SUBJECTS_HINT') }}
      </div>
      <div class="flex flex-wrap items-center gap-1.5">
        <span
          v-for="(s, idx) in subjects"
          :key="idx"
          class="inline-flex items-center gap-1 px-2 py-0.5 text-xs rounded-full bg-n-alpha-2 text-n-slate-12"
        >
          {{ s }}
          <button
            type="button"
            :title="t('ROUTING_CONFIG.OWNERS.REMOVE')"
            class="flex text-n-slate-10 hover:text-n-ruby-11"
            @click="removeSubject(idx)"
          >
            <span class="i-lucide-x text-[0.85rem]" aria-hidden="true" />
          </button>
        </span>
        <input
          v-model="subjectInput"
          type="text"
          :placeholder="t('ROUTING_CONFIG.SETTINGS.ADD_SUBJECT_PH')"
          class="min-w-[14rem] flex-1 px-2 py-1 text-xs border rounded-lg outline-none border-n-weak bg-n-surface text-n-slate-12 focus:border-n-brand"
          @keydown.enter.prevent="addSubject"
        />
      </div>
    </div>

    <!-- auto-file senders -->
    <div class="p-4 mb-4 border rounded-xl border-n-weak">
      <div class="text-sm font-medium text-n-slate-12">
        {{ t('ROUTING_CONFIG.SETTINGS.AUTO_FILE_SENDERS') }}
      </div>
      <div class="mb-2 text-xs text-n-slate-10">
        {{ t('ROUTING_CONFIG.SETTINGS.AUTO_FILE_SENDERS_HINT') }}
      </div>
      <div class="flex flex-wrap items-center gap-1.5">
        <span
          v-for="(s, idx) in senders"
          :key="idx"
          class="inline-flex items-center gap-1 px-2 py-0.5 font-mono text-xs rounded-full bg-n-alpha-2 text-n-slate-12"
        >
          {{ s }}
          <button
            type="button"
            :title="t('ROUTING_CONFIG.OWNERS.REMOVE')"
            class="flex text-n-slate-10 hover:text-n-ruby-11"
            @click="removeSender(idx)"
          >
            <span class="i-lucide-x text-[0.85rem]" aria-hidden="true" />
          </button>
        </span>
        <input
          v-model="senderInput"
          type="text"
          :placeholder="t('ROUTING_CONFIG.SETTINGS.ADD_SENDER_PH')"
          class="min-w-[16rem] flex-1 px-2 py-1 font-mono text-xs border rounded-lg outline-none border-n-weak bg-n-surface text-n-slate-12 focus:border-n-brand"
          @keydown.enter.prevent="addSender"
        />
      </div>
    </div>


    <!-- validation errors -->
    <div
      v-if="errors.length"
      class="p-3 mt-4 text-sm border rounded-lg border-n-weak bg-n-ruby-2 text-n-ruby-11"
    >
      <div class="mb-1 font-medium">
        {{ t('ROUTING_CONFIG.SETTINGS.VALIDATION_FAILED') }}
      </div>
      <ul class="pl-4 list-disc">
        <li v-for="(er, i) in errors" :key="i">{{ er }}</li>
      </ul>
    </div>

    <!-- sticky action bar -->
    <div
      v-if="dirtyCount"
      class="fixed inset-x-0 bottom-0 z-10 border-t border-n-weak bg-n-surface/95 backdrop-blur"
    >
      <div
        class="flex items-center justify-end max-w-5xl gap-3 px-6 py-3 mx-auto"
      >
        <span class="mr-auto text-sm text-n-slate-11">
          {{ t('ROUTING_CONFIG.SETTINGS.DIRTY', { count: dirtyCount }) }}
        </span>
        <button
          type="button"
          class="px-3 py-1.5 text-sm rounded-lg text-n-slate-11 hover:text-n-slate-12"
          :disabled="busy"
          @click="discard"
        >
          {{ t('ROUTING_CONFIG.SETTINGS.DISCARD') }}
        </button>
        <button
          type="button"
          class="px-4 py-1.5 text-sm font-medium rounded-lg text-white bg-n-brand hover:opacity-90 disabled:opacity-60"
          :disabled="busy"
          @click="publish"
        >
          {{
            busy
              ? t('ROUTING_CONFIG.SETTINGS.PUBLISHING')
              : t('ROUTING_CONFIG.SETTINGS.PUBLISH')
          }}
        </button>
      </div>
    </div>
  </div>
</template>
