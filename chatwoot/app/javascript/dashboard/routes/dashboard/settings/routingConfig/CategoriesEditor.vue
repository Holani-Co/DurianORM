<script setup>
// Phase 3 — editable categories & keywords. Each category is a collapsible card;
// edit its display name, whether it stays in the inbox or forwards (and where),
// and its keywords. The description + example messages teach the AI classifier and
// live behind an "advanced" section with a caution. Edits are collected as a draft
// and published to the bridge in one go (validate -> publish -> live). Only the
// changed fields are sent, so untouched fields keep coming from the YAML defaults.
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

const catEdits = reactive({}); // { catKey: { changedField: value } }
const kwInput = reactive({}); // { catKey: 'draft keyword' }
const busy = ref(false);
const errors = ref([]);

const categoryKeys = computed(() =>
  Object.keys(props.effective.categories || {})
);
const dirtyCount = computed(() => Object.keys(catEdits).length);
const isEdited = key => key in catEdits;

function base(key) {
  return (props.effective.categories || {})[key] || {};
}
function field(key, name) {
  if (catEdits[key] && name in catEdits[key]) return catEdits[key][name];
  return base(key)[name];
}
function setField(key, name, value) {
  catEdits[key] = { ...(catEdits[key] || {}), [name]: value };
}

function keywords(key) {
  return field(key, 'keywords') || [];
}
function addKeyword(key) {
  const kw = (kwInput[key] || '').trim();
  if (!kw) return;
  if (keywords(key).includes(kw)) {
    kwInput[key] = '';
    return;
  }
  setField(key, 'keywords', [...keywords(key), kw]);
  kwInput[key] = '';
}
function removeKeyword(key, idx) {
  const arr = [...keywords(key)];
  arr.splice(idx, 1);
  setField(key, 'keywords', arr);
}

function examplesText(key) {
  return (field(key, 'examples') || []).join('\n');
}
function setExamples(key, text) {
  setField(key, 'examples', text.split('\n'));
}

function discard() {
  Object.keys(catEdits).forEach(k => delete catEdits[k]);
  errors.value = [];
}

async function publish() {
  if (busy.value || !dirtyCount.value) return;
  errors.value = [];
  const doc = JSON.parse(JSON.stringify(props.override || {}));
  doc.categories = doc.categories || {};
  Object.keys(catEdits).forEach(key => {
    const changed = { ...catEdits[key] };
    if (Array.isArray(changed.keywords)) {
      changed.keywords = changed.keywords.map(k => k.trim()).filter(Boolean);
    }
    if (Array.isArray(changed.examples)) {
      changed.examples = changed.examples.map(e => e.trim()).filter(Boolean);
    }
    doc.categories[key] = { ...(doc.categories[key] || {}), ...changed };
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
      note: 'Categories updated from the UI',
    });
    useAlert(t('ROUTING_CONFIG.CATEGORIES.PUBLISHED'));
    discard();
    emit('published');
  } catch (e) {
    useAlert(
      e?.response?.data?.error || t('ROUTING_CONFIG.CATEGORIES.PUBLISH_FAILED')
    );
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <div class="pb-24">
    <p class="mb-4 text-sm text-n-slate-11">
      {{ t('ROUTING_CONFIG.CATEGORIES.EDIT_HINT') }}
    </p>

    <details
      v-for="key in categoryKeys"
      :key="key"
      class="mb-2 border rounded-xl border-n-weak"
      :class="isEdited(key) ? 'border-n-brand' : ''"
    >
      <summary
        class="flex flex-wrap items-center gap-2 px-4 py-3 cursor-pointer"
      >
        <span class="font-medium text-n-slate-12">{{
          field(key, 'display_name') || key
        }}</span>
        <span
          class="px-2 py-0.5 text-xs font-medium rounded-full"
          :class="
            field(key, 'action') === 'forward'
              ? 'bg-n-amber-2 text-n-amber-11'
              : 'bg-n-teal-3 text-n-teal-11'
          "
        >
          {{
            field(key, 'action') === 'forward'
              ? t('ROUTING_CONFIG.CATEGORIES.ACTION_FORWARD')
              : t('ROUTING_CONFIG.CATEGORIES.ACTION_IN_CHANNEL')
          }}
        </span>
        <span
          v-if="field(key, 'action') === 'forward' && field(key, 'forward_to')"
          class="text-xs text-n-slate-10"
        >
          {{ field(key, 'forward_to') }}
        </span>
        <span
          v-if="isEdited(key)"
          class="w-2 h-2 ml-auto rounded-full bg-n-brand"
          aria-hidden="true"
        />
      </summary>

      <div class="flex flex-col gap-3 p-4 border-t border-n-weak">
        <label class="flex flex-col gap-1">
          <span class="text-xs font-medium text-n-slate-11">{{
            t('ROUTING_CONFIG.CATEGORIES.DISPLAY_NAME')
          }}</span>
          <input
            type="text"
            :value="field(key, 'display_name')"
            class="max-w-sm px-2.5 py-1.5 text-sm border rounded-lg outline-none border-n-weak bg-n-surface text-n-slate-12 focus:border-n-brand"
            @input="setField(key, 'display_name', $event.target.value)"
          />
        </label>

        <div class="flex flex-wrap items-end gap-3">
          <label class="flex flex-col gap-1">
            <span class="text-xs font-medium text-n-slate-11">{{
              t('ROUTING_CONFIG.CATEGORIES.COL_ACTION')
            }}</span>
            <select
              :value="field(key, 'action') || 'in_channel'"
              class="px-2.5 py-1.5 text-sm border rounded-lg outline-none border-n-weak bg-n-surface text-n-slate-12 focus:border-n-brand"
              @change="setField(key, 'action', $event.target.value)"
            >
              <option value="in_channel">
                {{ t('ROUTING_CONFIG.CATEGORIES.ACTION_IN_CHANNEL') }}
              </option>
              <option value="forward">
                {{ t('ROUTING_CONFIG.CATEGORIES.ACTION_FORWARD') }}
              </option>
            </select>
          </label>
          <label
            v-if="field(key, 'action') === 'forward'"
            class="flex flex-col flex-1 gap-1 min-w-[14rem]"
          >
            <span class="text-xs font-medium text-n-slate-11">{{
              t('ROUTING_CONFIG.CATEGORIES.COL_FORWARD')
            }}</span>
            <input
              type="email"
              :value="field(key, 'forward_to')"
              :placeholder="t('ROUTING_CONFIG.CATEGORIES.FORWARD_TO_PH')"
              class="px-2.5 py-1.5 text-sm border rounded-lg outline-none border-n-weak bg-n-surface text-n-slate-12 focus:border-n-brand"
              @input="setField(key, 'forward_to', $event.target.value)"
            />
          </label>
        </div>

        <div class="flex flex-col gap-1">
          <span class="text-xs font-medium text-n-slate-11">{{
            t('ROUTING_CONFIG.CATEGORIES.KEYWORDS_LABEL')
          }}</span>
          <div class="flex flex-wrap items-center gap-1.5">
            <span
              v-for="(kw, idx) in keywords(key)"
              :key="idx"
              class="inline-flex items-center gap-1 px-2 py-0.5 text-xs rounded-full bg-n-alpha-2 text-n-slate-12"
            >
              {{ kw }}
              <button
                type="button"
                :title="t('ROUTING_CONFIG.OWNERS.REMOVE')"
                class="flex text-n-slate-10 hover:text-n-ruby-11"
                @click="removeKeyword(key, idx)"
              >
                <span class="i-lucide-x text-[0.85rem]" aria-hidden="true" />
              </button>
            </span>
            <input
              v-model="kwInput[key]"
              type="text"
              :placeholder="t('ROUTING_CONFIG.CATEGORIES.ADD_KEYWORD_PH')"
              class="min-w-[12rem] flex-1 px-2 py-1 text-xs border rounded-lg outline-none border-n-weak bg-n-surface text-n-slate-12 focus:border-n-brand"
              @keydown.enter.prevent="addKeyword(key)"
            />
          </div>
        </div>

        <details class="mt-1">
          <summary class="text-xs font-medium cursor-pointer text-n-amber-11">
            {{ t('ROUTING_CONFIG.CATEGORIES.ADVANCED') }}
          </summary>
          <div class="flex flex-col gap-3 mt-3">
            <label class="flex flex-col gap-1">
              <span class="text-xs font-medium text-n-slate-11">{{
                t('ROUTING_CONFIG.CATEGORIES.DESCRIPTION_LABEL')
              }}</span>
              <span class="text-xs text-n-slate-10">{{
                t('ROUTING_CONFIG.CATEGORIES.DESCRIPTION_HINT')
              }}</span>
              <textarea
                :value="field(key, 'description')"
                rows="4"
                class="px-2.5 py-1.5 text-sm border rounded-lg outline-none resize-y border-n-weak bg-n-surface text-n-slate-12 focus:border-n-brand"
                @input="setField(key, 'description', $event.target.value)"
              />
            </label>
            <label class="flex flex-col gap-1">
              <span class="text-xs font-medium text-n-slate-11">{{
                t('ROUTING_CONFIG.CATEGORIES.EXAMPLES_LABEL')
              }}</span>
              <span class="text-xs text-n-slate-10">{{
                t('ROUTING_CONFIG.CATEGORIES.EXAMPLES_HINT')
              }}</span>
              <textarea
                :value="examplesText(key)"
                rows="4"
                class="px-2.5 py-1.5 text-sm border rounded-lg outline-none resize-y border-n-weak bg-n-surface text-n-slate-12 focus:border-n-brand"
                @input="setExamples(key, $event.target.value)"
              />
            </label>
          </div>
        </details>
      </div>
    </details>

    <!-- validation errors -->
    <div
      v-if="errors.length"
      class="p-3 mt-4 text-sm border rounded-lg border-n-weak bg-n-ruby-2 text-n-ruby-11"
    >
      <div class="mb-1 font-medium">
        {{ t('ROUTING_CONFIG.CATEGORIES.VALIDATION_FAILED') }}
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
          {{ t('ROUTING_CONFIG.CATEGORIES.DIRTY', { count: dirtyCount }) }}
        </span>
        <button
          type="button"
          class="px-3 py-1.5 text-sm rounded-lg text-n-slate-11 hover:text-n-slate-12"
          :disabled="busy"
          @click="discard"
        >
          {{ t('ROUTING_CONFIG.CATEGORIES.DISCARD') }}
        </button>
        <button
          type="button"
          class="px-4 py-1.5 text-sm font-medium rounded-lg text-white bg-n-brand hover:opacity-90 disabled:opacity-60"
          :disabled="busy"
          @click="publish"
        >
          {{
            busy
              ? t('ROUTING_CONFIG.CATEGORIES.PUBLISHING')
              : t('ROUTING_CONFIG.CATEGORIES.PUBLISH')
          }}
        </button>
      </div>
    </div>
  </div>
</template>
