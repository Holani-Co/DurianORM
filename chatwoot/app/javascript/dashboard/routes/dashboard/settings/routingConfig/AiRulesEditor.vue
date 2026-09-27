<script setup>
// AI decision rules the classifier follows, editable without a deploy:
//   • category_ai_rules          — how to choose between categories
//   • ai_examples_per_category   — how many example messages the AI reads
//   • bulk order Government vs Private rules + name keywords
//     (categories.project_bulk_order.sector_routing)
// The output format (confidence score, reason, alternatives) and the bulk
// confidence calibration stay locked in the bridge so an edit can't break
// routing. Per-category subcategory rules live on the Categories tab.
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

// Draft edits: { categoryRules, examples, bulkRules, govKeywords, privKeywords }
const edits = reactive({});
const busy = ref(false);
const errors = ref([]);
const kwInput = reactive({ government: '', private: '' });

const sector = computed(
  () =>
    (props.effective.categories || {}).project_bulk_order?.sector_routing || {}
);
const dirtyCount = computed(() => Object.keys(edits).length);

const categoryRules = computed(
  () => edits.categoryRules ?? props.effective.category_ai_rules ?? []
);
const examplesCount = computed(
  () => edits.examples ?? props.effective.ai_examples_per_category ?? 4
);
const bulkRules = computed(
  () => edits.bulkRules ?? sector.value.ai_rules ?? []
);
function sectorKeywords(sec) {
  const k = sec === 'government' ? 'govKeywords' : 'privKeywords';
  return edits[k] ?? sector.value[sec]?.keywords ?? [];
}

const lines = text => (text || '').split('\n');
const clean = arr => arr.map(x => String(x).trim()).filter(Boolean);

function addKeyword(sec) {
  const kw = kwInput[sec].trim();
  kwInput[sec] = '';
  const cur = sectorKeywords(sec);
  if (!kw || cur.includes(kw)) return;
  edits[sec === 'government' ? 'govKeywords' : 'privKeywords'] = [...cur, kw];
}
function removeKeyword(sec, idx) {
  const arr = [...sectorKeywords(sec)];
  arr.splice(idx, 1);
  edits[sec === 'government' ? 'govKeywords' : 'privKeywords'] = arr;
}
function setExamples(raw) {
  const n = parseInt(raw, 10);
  // Keep a non-number as-is so the bridge rejects it with a clear message.
  edits.examples = Number.isFinite(n) ? n : raw;
}

function discard() {
  Object.keys(edits).forEach(k => delete edits[k]);
  errors.value = [];
}

async function publish() {
  if (busy.value || !dirtyCount.value) return;
  errors.value = [];
  const doc = JSON.parse(JSON.stringify(props.override || {}));
  if ('categoryRules' in edits)
    doc.category_ai_rules = clean(edits.categoryRules);
  if ('examples' in edits) doc.ai_examples_per_category = edits.examples;
  const sr = {};
  if ('bulkRules' in edits) sr.ai_rules = clean(edits.bulkRules);
  if ('govKeywords' in edits)
    sr.government = { keywords: clean(edits.govKeywords) };
  if ('privKeywords' in edits)
    sr.private = { keywords: clean(edits.privKeywords) };
  if (Object.keys(sr).length) {
    // Merge into the saved override so other bulk settings aren't dropped.
    doc.categories = doc.categories || {};
    const bulk = doc.categories.project_bulk_order || {};
    const cur = bulk.sector_routing || {};
    doc.categories.project_bulk_order = {
      ...bulk,
      sector_routing: {
        ...cur,
        ...sr,
        ...(sr.government && {
          government: { ...(cur.government || {}), ...sr.government },
        }),
        ...(sr.private && {
          private: { ...(cur.private || {}), ...sr.private },
        }),
      },
    };
  }

  busy.value = true;
  const url = `/api/v1/accounts/${accountId.value}/integrations/routing_config`;
  try {
    const { data: v } = await axios.post(`${url}/validate`, { doc });
    if (!v.ok) {
      errors.value = v.errors || [
        t('ROUTING_CONFIG.AI_RULES.VALIDATION_FAILED'),
      ];
      return;
    }
    await axios.post(`${url}/publish`, {
      doc,
      note: 'AI rules updated from the UI',
    });
    useAlert(t('ROUTING_CONFIG.AI_RULES.PUBLISHED'));
    discard();
    emit('published');
  } catch (e) {
    useAlert(
      e?.response?.data?.error || t('ROUTING_CONFIG.AI_RULES.PUBLISH_FAILED')
    );
  } finally {
    busy.value = false;
  }
}

const inputClass =
  'w-full px-2.5 py-1.5 text-sm border rounded-lg outline-none resize-y border-n-weak bg-n-surface text-n-slate-12 focus:border-n-brand';
</script>

<template>
  <div class="flex flex-col gap-6 pb-24">
    <p class="text-sm text-n-slate-11">
      {{ t('ROUTING_CONFIG.AI_RULES.EDIT_HINT') }}
    </p>

    <section class="flex flex-col gap-2">
      <h3 class="text-sm font-medium text-n-slate-12">
        {{ t('ROUTING_CONFIG.AI_RULES.CATEGORY_RULES') }}
      </h3>
      <p class="text-xs text-n-slate-10">
        {{ t('ROUTING_CONFIG.AI_RULES.CATEGORY_RULES_HINT') }}
      </p>
      <textarea
        :value="categoryRules.join('\n')"
        rows="6"
        :class="inputClass"
        @input="edits.categoryRules = lines($event.target.value)"
      />
    </section>

    <section class="flex flex-col gap-2">
      <h3 class="text-sm font-medium text-n-slate-12">
        {{ t('ROUTING_CONFIG.AI_RULES.EXAMPLES') }}
      </h3>
      <p class="text-xs text-n-slate-10">
        {{ t('ROUTING_CONFIG.AI_RULES.EXAMPLES_HINT') }}
      </p>
      <input
        :value="examplesCount"
        type="number"
        min="1"
        max="20"
        class="w-24 px-2.5 py-1.5 text-sm border rounded-lg outline-none border-n-weak bg-n-surface text-n-slate-12 focus:border-n-brand"
        @input="setExamples($event.target.value)"
      />
    </section>

    <section class="flex flex-col gap-3">
      <h3 class="text-sm font-medium text-n-slate-12">
        {{ t('ROUTING_CONFIG.AI_RULES.BULK_TITLE') }}
      </h3>
      <p class="text-xs text-n-slate-10">
        {{ t('ROUTING_CONFIG.AI_RULES.BULK_HINT') }}
      </p>
      <textarea
        :value="bulkRules.join('\n')"
        rows="4"
        :class="inputClass"
        @input="edits.bulkRules = lines($event.target.value)"
      />
      <div
        v-for="sec in ['government', 'private']"
        :key="sec"
        class="flex flex-col gap-1"
      >
        <span class="text-xs font-medium text-n-slate-11">
          {{
            sec === 'government'
              ? t('ROUTING_CONFIG.AI_RULES.GOV_KEYWORDS')
              : t('ROUTING_CONFIG.AI_RULES.PRIVATE_KEYWORDS')
          }}
        </span>
        <div class="flex flex-wrap items-center gap-1.5">
          <span
            v-for="(kw, idx) in sectorKeywords(sec)"
            :key="idx"
            class="inline-flex items-center gap-1 px-2 py-0.5 text-xs rounded-full bg-n-alpha-2 text-n-slate-12"
          >
            {{ kw }}
            <button
              type="button"
              class="flex text-n-slate-10 hover:text-n-ruby-11"
              @click="removeKeyword(sec, idx)"
            >
              <span class="i-lucide-x text-[0.85rem]" aria-hidden="true" />
            </button>
          </span>
          <input
            v-model="kwInput[sec]"
            type="text"
            :placeholder="t('ROUTING_CONFIG.CATEGORIES.ADD_KEYWORD_PH')"
            class="min-w-[12rem] flex-1 px-2 py-1 text-xs border rounded-lg outline-none border-n-weak bg-n-surface text-n-slate-12 focus:border-n-brand"
            @keydown.enter.prevent="addKeyword(sec)"
          />
        </div>
      </div>
    </section>

    <p class="text-xs italic text-n-slate-10">
      {{ t('ROUTING_CONFIG.AI_RULES.LOCKED_NOTE') }}
    </p>

    <!-- validation errors -->
    <div
      v-if="errors.length"
      class="p-3 text-sm border rounded-lg border-n-weak bg-n-ruby-2 text-n-ruby-11"
    >
      <div class="mb-1 font-medium">
        {{ t('ROUTING_CONFIG.AI_RULES.VALIDATION_FAILED') }}
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
          {{ t('ROUTING_CONFIG.AI_RULES.DIRTY', { count: dirtyCount }) }}
        </span>
        <button
          type="button"
          class="px-3 py-1.5 text-sm rounded-lg text-n-slate-11 hover:text-n-slate-12"
          :disabled="busy"
          @click="discard"
        >
          {{ t('ROUTING_CONFIG.AI_RULES.DISCARD') }}
        </button>
        <button
          type="button"
          class="px-4 py-1.5 text-sm font-medium rounded-lg text-white bg-n-brand hover:opacity-90 disabled:opacity-60"
          :disabled="busy"
          @click="publish"
        >
          {{
            busy
              ? t('ROUTING_CONFIG.AI_RULES.PUBLISHING')
              : t('ROUTING_CONFIG.AI_RULES.PUBLISH')
          }}
        </button>
      </div>
    </div>
  </div>
</template>
