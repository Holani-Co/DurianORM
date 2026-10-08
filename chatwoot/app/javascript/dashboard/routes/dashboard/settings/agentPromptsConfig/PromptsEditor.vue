<script setup>
// One guidance box per vertical; an empty box means no guidance for it.
// Saves validate then publish to the bridge's agent-prompts override.
import { ref, reactive, computed } from 'vue';
import { useI18n } from 'vue-i18n';
import { useMapGetter } from 'dashboard/composables/store';
import { useAlert } from 'dashboard/composables';

const props = defineProps({
  override: { type: Object, default: () => ({}) },
  verticals: { type: Array, default: () => [] },
  maxChars: { type: Number, default: 1500 },
  enabled: { type: Boolean, default: false },
});
const emit = defineEmits(['published']);

const { t } = useI18n();
const accountId = useMapGetter('getCurrentAccountId');
const axios = window.axios;

const LABELS = {
  furniture: t('AGENT_PROMPTS_CONFIG.VERTICALS.FURNITURE'),
  doors: t('AGENT_PROMPTS_CONFIG.VERTICALS.DOORS'),
  fhc: t('AGENT_PROMPTS_CONFIG.VERTICALS.FHC'),
};
const label = v => LABELS[v] || v;

// Working copy, one string per vertical, seeded from what's live.
const edits = reactive(
  Object.fromEntries(props.verticals.map(v => [v, props.override[v] || '']))
);

const busy = ref(false);
const errors = ref([]);

const remaining = v => props.maxChars - (edits[v] || '').length;
const overLimit = computed(() => props.verticals.some(v => remaining(v) < 0));

const buildDoc = () => {
  const doc = {};
  props.verticals.forEach(v => {
    const text = (edits[v] || '').trim();
    if (text) doc[v] = text;
  });
  return doc;
};

async function save() {
  if (busy.value || overLimit.value) return;
  busy.value = true;
  errors.value = [];
  const base = `/api/v1/accounts/${accountId.value}/integrations/agent_prompts_config`;
  const doc = buildDoc();
  try {
    const { data: v } = await axios.post(`${base}/validate`, { doc });
    if (!v.ok) {
      errors.value = v.errors || [t('AGENT_PROMPTS_CONFIG.SAVE_FAILED')];
      return;
    }
    await axios.post(`${base}/publish`, {
      doc,
      note: t('AGENT_PROMPTS_CONFIG.PUBLISH_NOTE'),
    });
    useAlert(t('AGENT_PROMPTS_CONFIG.SAVED'));
    emit('published');
  } catch (e) {
    useAlert(e?.response?.data?.error || t('AGENT_PROMPTS_CONFIG.SAVE_FAILED'));
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <div class="max-w-3xl">
    <div
      class="p-3 mb-4 text-xs border rounded-lg border-n-weak bg-n-alpha-1 text-n-slate-11"
    >
      {{ t('AGENT_PROMPTS_CONFIG.ADVISORY_HINT') }}
    </div>

    <div
      v-if="!enabled"
      class="p-3 mb-4 text-xs border rounded-lg border-n-weak bg-n-amber-2 text-n-amber-11"
    >
      {{ t('AGENT_PROMPTS_CONFIG.DISABLED_HINT') }}
    </div>

    <div
      v-if="errors.length"
      class="p-3 mb-3 text-sm border rounded-lg border-n-weak bg-n-ruby-2 text-n-ruby-11"
    >
      <ul class="list-disc list-inside">
        <li v-for="(e, i) in errors" :key="i">{{ e }}</li>
      </ul>
    </div>

    <div class="flex flex-col gap-5">
      <div v-for="v in verticals" :key="v">
        <div class="flex items-baseline justify-between mb-1">
          <label
            :for="`guidance-${v}`"
            class="text-sm font-medium text-n-slate-12"
          >
            {{ label(v) }}
          </label>
          <span
            class="text-xs"
            :class="remaining(v) < 0 ? 'text-n-ruby-11' : 'text-n-slate-10'"
          >
            {{ remaining(v) }}
          </span>
        </div>
        <textarea
          :id="`guidance-${v}`"
          v-model="edits[v]"
          rows="5"
          :placeholder="
            t('AGENT_PROMPTS_CONFIG.PLACEHOLDER', { vertical: label(v) })
          "
          class="w-full px-3 py-2 text-sm border rounded-lg border-n-weak bg-n-alpha-black-2 text-n-slate-12"
        />
      </div>
    </div>

    <div class="sticky bottom-0 flex justify-end py-3 mt-4 bg-n-background">
      <button
        type="button"
        class="px-4 py-2 text-sm font-medium text-white rounded-lg bg-n-brand hover:opacity-90 disabled:opacity-60"
        :disabled="busy || overLimit"
        @click="save"
      >
        {{
          busy
            ? t('AGENT_PROMPTS_CONFIG.SAVING')
            : t('AGENT_PROMPTS_CONFIG.SAVE')
        }}
      </button>
    </div>
  </div>
</template>
